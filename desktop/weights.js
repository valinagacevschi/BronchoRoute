// Changes: verify, resume, and extract mirrored TotalSegmentator weights.
"use strict";

const crypto = require("node:crypto");
const fs = require("node:fs");
const http = require("node:http");
const https = require("node:https");
const path = require("node:path");
const zlib = require("node:zlib");

const CHECKSUM_ERROR = "Model download failed checksum — try again";
const UNSAFE_ENTRY = "Refused to extract unsafe zip entry";
const SIZE_CAP = 2 * 1024 * 1024 * 1024;
const RESULTS_PARTS = ["nnunet", "results"];

function checksumError() {
  const error = new Error(CHECKSUM_ERROR);
  error.code = "CHECKSUM";
  return error;
}

function cancelledError() {
  const error = new Error("Model download cancelled");
  error.code = "CANCELLED";
  return error;
}

function interruptedError() {
  const error = new Error("Model download interrupted");
  error.code = "INTERRUPTED";
  return error;
}

function unsafeEntry() {
  const error = new Error(UNSAFE_ENTRY);
  error.code = "UNSAFE_ZIP";
  return error;
}

function throwIfAborted(signal) {
  if (signal && signal.aborted) throw cancelledError();
}

function weightsRequired(isPackaged, env = process.env) {
  return Boolean(env && env.BRONCHOROUTE_FORCE_WEIGHTS === "1") || isPackaged === true;
}

function assertManifest(manifest) {
  if (!manifest || typeof manifest !== "object" || Array.isArray(manifest)) {
    throw new Error("invalid weights manifest");
  }
  if (typeof manifest.repo !== "string" || !/^[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+$/.test(manifest.repo)) {
    throw new Error("invalid weights manifest");
  }
  if (typeof manifest.tag !== "string" || !/^[A-Za-z0-9._-]+$/.test(manifest.tag)) {
    throw new Error("invalid weights manifest");
  }
  if (typeof manifest.totalsegmentator_version !== "string" || manifest.totalsegmentator_version.trim() === "") {
    throw new Error("invalid weights manifest");
  }
  if (!Array.isArray(manifest.files) || manifest.files.length === 0) {
    throw new Error("invalid weights manifest");
  }
  const seen = new Set();
  for (const file of manifest.files) {
    if (!file || typeof file !== "object") throw new Error("invalid weights manifest");
    if (typeof file.dataset !== "string" || !/^Dataset\d{3}_[A-Za-z0-9_]+$/.test(file.dataset)) {
      throw new Error("invalid weights manifest");
    }
    if (file.asset !== `${file.dataset}.zip`) throw new Error("invalid weights manifest");
    if (!Number.isInteger(file.bytes) || file.bytes < 1) throw new Error("invalid weights manifest");
    if (typeof file.sha256 !== "string" || !/^[0-9a-f]{64}$/.test(file.sha256)) {
      throw new Error("invalid weights manifest");
    }
    if (seen.has(file.dataset)) throw new Error("invalid weights manifest");
    seen.add(file.dataset);
  }
  return manifest;
}

function loadManifest(file) {
  return assertManifest(JSON.parse(fs.readFileSync(file, "utf8")));
}

function weightsBaseUrl(manifest, env = process.env) {
  assertManifest(manifest);
  const override = env && env.BRONCHOROUTE_WEIGHTS_BASE_URL;
  if (typeof override === "string" && override.trim() !== "") return override.trim();
  return `https://github.com/${manifest.repo}/releases/download/${manifest.tag}/`;
}

function withSlash(url) {
  return url.endsWith("/") ? url : `${url}/`;
}

function resultsDir(modelsDir) {
  return path.join(modelsDir, ...RESULTS_PARTS);
}

function datasetDir(modelsDir, dataset) {
  return path.join(resultsDir(modelsDir), dataset);
}

function datasetReady(modelsDir, file) {
  const dir = datasetDir(modelsDir, file.dataset);
  let info;
  try {
    info = fs.lstatSync(dir);
  } catch (err) {
    return false;
  }
  if (!info.isDirectory()) return false;
  let text;
  try {
    text = fs.readFileSync(path.join(dir, ".verified"), "utf8");
  } catch (err) {
    return false;
  }
  return text.trim() === file.sha256;
}

function weightsStatus(modelsDir, manifest) {
  assertManifest(manifest);
  const missing = [];
  for (const file of manifest.files) {
    if (!datasetReady(modelsDir, file)) {
      missing.push({
        dataset: file.dataset,
        asset: file.asset,
        bytes: file.bytes,
        sha256: file.sha256,
      });
    }
  }
  return { ready: missing.length === 0, missing };
}

function assertInside(root, target) {
  const base = path.resolve(root);
  const resolved = path.resolve(target);
  if (resolved !== base && !resolved.startsWith(`${base}${path.sep}`)) {
    throw new Error("Refused to write outside the model directory");
  }
}

function hashFile(hash, file) {
  const fd = fs.openSync(file, "r");
  try {
    const buf = Buffer.alloc(1024 * 1024);
    let pos = 0;
    for (;;) {
      const n = fs.readSync(fd, buf, 0, buf.length, pos);
      if (n <= 0) break;
      hash.update(buf.subarray(0, n));
      pos += n;
    }
  } finally {
    fs.closeSync(fd);
  }
}

function bindAbort(signal, onAbort) {
  if (!signal) return () => {};
  if (signal.aborted) {
    onAbort();
    return () => {};
  }
  signal.addEventListener("abort", onAbort);
  return () => signal.removeEventListener("abort", onAbort);
}

function requestOnce(target, headers, signal) {
  const url = new URL(target);
  if (url.protocol !== "http:" && url.protocol !== "https:") {
    return Promise.reject(new Error("Model download URL must be http or https"));
  }
  const lib = url.protocol === "https:" ? https : http;
  return new Promise((resolve, reject) => {
    let settled = false;
    let unbind = () => {};
    const req = lib.request(url, { method: "GET", headers }, (res) => {
      settle(() => resolve(res));
    });
    function settle(fn) {
      if (settled) return;
      settled = true;
      unbind();
      fn();
    }
    function fail(err) {
      settle(() => {
        req.destroy();
        reject(err);
      });
    }
    unbind = bindAbort(signal, () => fail(cancelledError()));
    req.on("error", (err) => {
      if (signal && signal.aborted) fail(cancelledError());
      else {
        const error = interruptedError();
        error.cause = err;
        fail(error);
      }
    });
    if (!settled) req.end();
  });
}

async function openDownload(startUrl, headers, signal) {
  let current = startUrl;
  for (let hop = 0; hop < 5; hop += 1) {
    const res = await requestOnce(current, headers, signal);
    const status = res.statusCode || 0;
    if (status >= 300 && status < 400) {
      const location = res.headers.location;
      res.resume();
      if (!location) throw new Error("Model download failed redirect");
      const next = new URL(location, current);
      if (next.protocol !== "http:" && next.protocol !== "https:") {
        throw new Error("Model download failed redirect");
      }
      current = next.href;
      continue;
    }
    return res;
  }
  throw new Error("Model download failed redirect");
}

function readBody(res, fd, hash, start, expectedBytes, signal, onReceived) {
  return new Promise((resolve, reject) => {
    let received = start;
    let settled = false;
    let unbind = () => {};
    function finish(err, value) {
      if (settled) return;
      settled = true;
      unbind();
      if (err) reject(err);
      else resolve(value);
    }
    unbind = bindAbort(signal, () => {
      finish(cancelledError());
      res.destroy();
    });
    res.on("data", (chunk) => {
      try {
        fs.writeSync(fd, chunk);
        hash.update(chunk);
        received += chunk.length;
        if (received > expectedBytes) {
          res.destroy();
          finish(checksumError());
          return;
        }
        onReceived(received);
      } catch (err) {
        res.destroy();
        finish(err);
      }
    });
    res.on("end", () => finish(null, received));
    res.on("aborted", () => finish(signal && signal.aborted ? cancelledError() : interruptedError()));
    res.on("error", () => finish(signal && signal.aborted ? cancelledError() : interruptedError()));
  });
}

function closeFd(fd) {
  if (fd == null) return;
  try {
    fs.fsyncSync(fd);
  } catch (err) {
    // The partial is still the bytes already written.
  }
  fs.closeSync(fd);
}

async function downloadOne({ url, dest, expectedBytes, expectedSha, signal, onReceived }) {
  throwIfAborted(signal);
  fs.mkdirSync(path.dirname(dest), { recursive: true });
  let existing = fs.existsSync(dest) ? fs.statSync(dest).size : 0;
  if (existing > expectedBytes) {
    fs.rmSync(dest, { force: true });
    existing = 0;
  }
  let hash = crypto.createHash("sha256");
  if (existing > 0) hashFile(hash, dest);
  if (existing === expectedBytes) {
    const digest = hash.digest("hex");
    if (digest !== expectedSha) {
      fs.rmSync(dest, { force: true });
      throw checksumError();
    }
    onReceived(existing);
    return;
  }

  const headers = {
    "User-Agent": "BronchoRoute",
    "Accept-Encoding": "identity",
  };
  if (existing > 0) headers.Range = `bytes=${existing}-`;
  const res = await openDownload(url, headers, signal);
  let fd = null;
  let received = existing;
  try {
    const status = res.statusCode || 0;
    if (status === 206) {
      const match = /^bytes (\d+)-/.exec(String(res.headers["content-range"] || ""));
      if (!match || Number(match[1]) !== existing) {
        res.resume();
        throw new Error("Model download failed resume");
      }
      fd = fs.openSync(dest, "a");
    } else if (status === 200) {
      if (existing > 0) {
        hash = crypto.createHash("sha256");
        received = 0;
      }
      fd = fs.openSync(dest, "w");
    } else {
      res.resume();
      throw interruptedError();
    }
    onReceived(received);
    received = await readBody(res, fd, hash, received, expectedBytes, signal, onReceived);
  } catch (err) {
    closeFd(fd);
    fd = null;
    if (err && err.code === "CHECKSUM") fs.rmSync(dest, { force: true });
    throw err;
  }
  closeFd(fd);
  if (received !== expectedBytes || hash.digest("hex") !== expectedSha) {
    fs.rmSync(dest, { force: true });
    throw checksumError();
  }
  onReceived(received);
}

function findEocd(buf) {
  const min = Math.max(0, buf.length - (22 + 65535));
  for (let i = buf.length - 22; i >= min; i -= 1) {
    if (buf.readUInt32LE(i) !== 0x06054b50) continue;
    const commentLen = buf.readUInt16LE(i + 20);
    if (i + 22 + commentLen === buf.length) return i;
  }
  throw new Error("zip central directory not found");
}

function readCentralDirectory(fd) {
  const stat = fs.fstatSync(fd);
  const tailLen = Math.min(stat.size, 22 + 65535);
  const tail = Buffer.alloc(tailLen);
  fs.readSync(fd, tail, 0, tailLen, stat.size - tailLen);
  const eocd = findEocd(tail);
  if (tail.readUInt16LE(eocd + 4) !== 0 || tail.readUInt16LE(eocd + 6) !== 0) {
    throw new Error("zip64 archives are not supported");
  }
  const entries = tail.readUInt16LE(eocd + 10);
  const cdSize = tail.readUInt32LE(eocd + 12);
  const cdOffset = tail.readUInt32LE(eocd + 16);
  if (entries === 0xffff || cdSize === 0xffffffff || cdOffset === 0xffffffff) {
    throw new Error("zip64 archives are not supported");
  }
  const cd = Buffer.alloc(cdSize);
  fs.readSync(fd, cd, 0, cdSize, cdOffset);
  const list = [];
  let pos = 0;
  for (let n = 0; n < entries; n += 1) {
    if (pos + 46 > cd.length || cd.readUInt32LE(pos) !== 0x02014b50) {
      throw new Error("bad zip central directory");
    }
    const madeBy = cd.readUInt16LE(pos + 4);
    const flags = cd.readUInt16LE(pos + 8);
    const method = cd.readUInt16LE(pos + 10);
    const crc = cd.readUInt32LE(pos + 16);
    const compressed = cd.readUInt32LE(pos + 20);
    const uncompressed = cd.readUInt32LE(pos + 24);
    const nameLen = cd.readUInt16LE(pos + 28);
    const extraLen = cd.readUInt16LE(pos + 30);
    const commentLen = cd.readUInt16LE(pos + 32);
    const external = cd.readUInt32LE(pos + 38);
    const localOff = cd.readUInt32LE(pos + 42);
    const name = cd.subarray(pos + 46, pos + 46 + nameLen).toString("utf8");
    list.push({
      madeBy, flags, method, crc, compressed, uncompressed, external, localOff, name,
    });
    pos += 46 + nameLen + extraLen + commentLen;
  }
  return list;
}

function isSymlink(entry) {
  if ((entry.madeBy >> 8) !== 3) return false;
  const mode = (entry.external >>> 16) & 0xffff;
  return (mode & 0o170000) === 0o120000;
}

function lexicalDestination(root, name) {
  if (typeof name !== "string" || name.length === 0 || name.includes("\0") || name.includes("\\")) {
    throw unsafeEntry();
  }
  if (name.startsWith("/") || name.startsWith("//") || /^[A-Za-z]:/.test(name)) throw unsafeEntry();
  const directory = name.endsWith("/");
  const parts = name.split("/");
  if (directory) parts.pop();
  if (parts.length === 0 || parts.some((part) => part === "" || part === "." || part === "..")) {
    throw unsafeEntry();
  }
  const target = path.resolve(root, ...parts);
  const base = path.resolve(root);
  if (target !== base && !target.startsWith(`${base}${path.sep}`)) throw unsafeEntry();
  return target;
}

function localDataOffset(fd, entry) {
  const header = Buffer.alloc(30);
  const n = fs.readSync(fd, header, 0, 30, entry.localOff);
  if (n !== 30 || header.readUInt32LE(0) !== 0x04034b50) throw new Error("bad zip local header");
  const nameLen = header.readUInt16LE(26);
  const extraLen = header.readUInt16LE(28);
  return entry.localOff + 30 + nameLen + extraLen;
}

function readExact(fd, buf, offset) {
  let pos = 0;
  while (pos < buf.length) {
    const n = fs.readSync(fd, buf, pos, buf.length - pos, offset + pos);
    if (n <= 0) throw new Error("truncated zip entry");
    pos += n;
  }
}

function copyStored(fd, offset, length, outFd, expectedCrc) {
  let crc = 0;
  let left = length;
  let pos = offset;
  const buf = Buffer.alloc(Math.min(1024 * 1024, Math.max(length, 1)));
  while (left > 0) {
    const n = fs.readSync(fd, buf, 0, Math.min(buf.length, left), pos);
    if (n <= 0) throw new Error("truncated zip entry");
    fs.writeSync(outFd, buf, 0, n);
    crc = zlib.crc32(buf.subarray(0, n), crc);
    pos += n;
    left -= n;
  }
  if ((crc >>> 0) !== (expectedCrc >>> 0)) throw new Error("zip crc mismatch");
}

function assertRealParent(root, target) {
  const parent = path.dirname(target);
  fs.mkdirSync(parent, { recursive: true });
  let current = fs.realpathSync(root);
  const base = current;
  const rest = path.relative(root, parent);
  if (rest === "") return;
  for (const part of rest.split(path.sep)) {
    if (part === "" || part === ".") continue;
    current = path.join(current, part);
    const info = fs.lstatSync(current);
    if (info.isSymbolicLink()) throw unsafeEntry();
    if (!info.isDirectory()) throw unsafeEntry();
    current = fs.realpathSync(current);
    if (current !== base && !current.startsWith(`${base}${path.sep}`)) throw unsafeEntry();
  }
}

function extractEntry(zipFd, entry, destDir) {
  const target = lexicalDestination(destDir, entry.name);
  if (entry.name.endsWith("/")) {
    fs.mkdirSync(target, { recursive: true });
    assertRealParent(destDir, target);
    return 0;
  }
  assertRealParent(destDir, target);
  try {
    if (fs.lstatSync(target).isSymbolicLink()) throw unsafeEntry();
  } catch (err) {
    if (err.code !== "ENOENT") throw err;
  }
  const out = fs.openSync(
    target,
    fs.constants.O_WRONLY | fs.constants.O_CREAT | fs.constants.O_TRUNC | fs.constants.O_NOFOLLOW,
    0o644,
  );
  try {
    const dataOff = localDataOffset(zipFd, entry);
    if (entry.method === 0) {
      if (entry.compressed !== entry.uncompressed) throw new Error("stored zip size mismatch");
      copyStored(zipFd, dataOff, entry.compressed, out, entry.crc);
      return entry.uncompressed;
    }
    if (entry.method !== 8) throw new Error("unsupported zip compression");
    const compressed = Buffer.alloc(entry.compressed);
    readExact(zipFd, compressed, dataOff);
    const raw = zlib.inflateRawSync(compressed);
    if (raw.length !== entry.uncompressed) throw new Error("zip size mismatch");
    if ((zlib.crc32(raw) >>> 0) !== (entry.crc >>> 0)) throw new Error("zip crc mismatch");
    fs.writeSync(out, raw);
    return raw.length;
  } finally {
    fs.closeSync(out);
  }
}

function extractZip(zipPath, destDir) {
  const zipFd = fs.openSync(zipPath, "r");
  try {
    const entries = readCentralDirectory(zipFd);
    let declared = 0;
    for (const entry of entries) {
      if (entry.flags & 1) throw unsafeEntry();
      if (entry.method !== 0 && entry.method !== 8) throw new Error("unsupported zip compression");
      if (isSymlink(entry)) throw unsafeEntry();
      if (entry.uncompressed > SIZE_CAP) throw unsafeEntry();
      declared += entry.uncompressed;
      if (declared > SIZE_CAP) throw unsafeEntry();
      lexicalDestination(destDir, entry.name);
    }
    fs.mkdirSync(destDir, { recursive: true });
    let written = 0;
    for (const entry of entries) {
      written += extractEntry(zipFd, entry, destDir);
      if (written > SIZE_CAP) throw unsafeEntry();
    }
  } finally {
    fs.closeSync(zipFd);
  }
}

async function downloadWeights(options) {
  const manifest = assertManifest(options.manifest);
  const modelsDir = options.modelsDir;
  if (typeof modelsDir !== "string" || modelsDir.trim() === "") {
    throw new Error("models directory must be set");
  }
  const base = withSlash(options.baseUrl || weightsBaseUrl(manifest, options.env || process.env));
  const signal = options.signal;
  const onProgress = typeof options.onProgress === "function" ? options.onProgress : () => {};
  const missing = weightsStatus(modelsDir, manifest).missing;
  const total = missing.reduce((sum, file) => sum + file.bytes, 0);
  let completed = 0;
  let reported = 0;
  const report = (received) => {
    if (received < reported) return;
    reported = received;
    onProgress({ received, total });
  };
  report(0);
  for (const file of missing) {
    throwIfAborted(signal);
    const partial = path.join(modelsDir, ".partial", file.asset);
    const dest = datasetDir(modelsDir, file.dataset);
    assertInside(modelsDir, partial);
    assertInside(modelsDir, dest);
    const url = new URL(file.asset, base).href;
    await downloadOne({
      url,
      dest: partial,
      expectedBytes: file.bytes,
      expectedSha: file.sha256,
      signal,
      onReceived: (n) => report(completed + n),
    });
    fs.rmSync(dest, { recursive: true, force: true });
    try {
      extractZip(partial, dest);
    } catch (err) {
      fs.rmSync(dest, { recursive: true, force: true });
      throw err;
    }
    fs.writeFileSync(path.join(dest, ".verified"), `${file.sha256}\n`);
    fs.rmSync(partial, { force: true });
    completed += file.bytes;
    report(completed);
  }
  return weightsStatus(modelsDir, manifest);
}

module.exports = {
  CHECKSUM_ERROR,
  UNSAFE_ENTRY,
  assertManifest,
  loadManifest,
  weightsRequired,
  weightsBaseUrl,
  weightsStatus,
  downloadWeights,
};
