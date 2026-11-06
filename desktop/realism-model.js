"use strict";
const crypto = require("node:crypto");
const fs = require("node:fs");
const http = require("node:http");
const https = require("node:https");
const path = require("node:path");
const MODEL = "broncho-flow-int8.onnx";
const DEFAULTS = { expectedBytes: 580180539, expectedSha: "419adbd774b0c0dad2eae9746c923b4f9265d5f1ab3a908fef9b3b2a784b58df" };
function failure(code) { const e = new Error(code); e.code = code; return e; }
function checksumError() { return failure("checksum"); }
function cancelledError() { return failure("cancelled"); }
function interruptedError() { return failure("interrupted"); }
function throwIfAborted(signal) { if (signal?.aborted) throw cancelledError(); }
function modelPath(dataRoot) { return path.join(dataRoot, "models", "realism", MODEL); }
function status(dataRoot, options = {}) {
  const { expectedBytes, expectedSha } = { ...DEFAULTS, ...options };
  const dest = modelPath(dataRoot);
  let state = "absent", bytes = 0;
  if (fs.existsSync(dest)) {
    const stat = fs.statSync(dest);
    bytes = stat.size;
    const stamp = JSON.stringify([bytes, stat.mtimeMs, expectedSha]);
    let cached;
    try { cached = fs.readFileSync(dest + ".verified", "utf8"); } catch {}
    state = "bad";
    if (bytes === expectedBytes) {
      const hash = crypto.createHash("sha256");
      if (cached !== stamp) hashFile(hash, dest);
      if (cached === stamp || hash.digest("hex") === expectedSha) {
        state = "verified";
        fs.writeFileSync(dest + ".verified", stamp);
      }
    }
  } else if (fs.existsSync(dest + ".partial")) {
    state = "partial"; bytes = fs.statSync(dest + ".partial").size;
  }
  return { state, bytes, total: expectedBytes };
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
    req.on("error", () => {
      if (signal && signal.aborted) fail(cancelledError());
      else {
        const error = interruptedError();
        // Do not attach transport errors that might contain request credentials.
        fail(error);
      }
    });
    if (!settled) req.end();
  });
}

async function openDownload(startUrl, headers, signal) {
  return requestOnce(startUrl, headers, signal);
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

async function downloadOne({ url, dest, expectedBytes, expectedSha, key, signal, onReceived }) {
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
    Authorization: `Bearer ${key}`,
    "Accept-Encoding": "identity",
  };
  if (existing > 0) headers.Range = `bytes=${existing}-`;
  const res = await openDownload(url, headers, signal);
  let fd = null;
  let received = existing;
  try {
    const status = res.statusCode || 0;
    if (status === 401) { res.resume(); throw failure("bad_key"); }
    if (status === 206) {
      const match = /^bytes (\d+)-(\d+)\/(\d+)$/.exec(String(res.headers["content-range"] || ""));
      if (!match || Number(match[1]) !== existing || Number(match[2]) !== expectedBytes - 1 || Number(match[3]) !== expectedBytes) {
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
    if (err && err.code === "checksum") fs.rmSync(dest, { force: true });
    throw err;
  }
  closeFd(fd);
  if (received !== expectedBytes || hash.digest("hex") !== expectedSha) {
    fs.rmSync(dest, { force: true });
    throw checksumError();
  }
  onReceived(received);
}


async function download(options) {
  const { dataRoot, key, signal, onProgress = () => {}, expectedBytes, expectedSha } = { ...DEFAULTS, ...options };
  const dest = modelPath(dataRoot);
  const base = options.baseUrl || process.env.BRONCHOROUTE_MODELS_BASE_URL || "https://lung-nav.appcenter.ro/models/";
  try {
    await downloadOne({ url: new URL(MODEL, base.endsWith("/") ? base : base + "/").href,
      dest: dest + ".partial", expectedBytes, expectedSha, key, signal,
      onReceived: bytes => onProgress({ bytes, total: expectedBytes }) });
    throwIfAborted(signal);
    fs.renameSync(dest + ".partial", dest);
    return status(dataRoot, { expectedBytes, expectedSha });
  } catch (error) {
    throw failure(["bad_key", "checksum", "cancelled"].includes(error.code) ? error.code : "interrupted");
  }
}
function remove(dataRoot) {
  const dest = modelPath(dataRoot);
  for (const suffix of ["", ".partial", ".verified"]) fs.rmSync(dest + suffix, { force: true });
}
function keyStore(userData, safeStorage) {
  const file = path.join(userData, "realism-key.bin");
  function available() { if (!safeStorage.isEncryptionAvailable()) throw failure("no_keychain"); }
  return {
    hasKey: () => fs.existsSync(file),
    setKey(key) {
      available();
      if (typeof key !== "string" || !key.trim() || /[\r\n]/.test(key)) throw failure("bad_key");
      try {
        fs.mkdirSync(userData, { recursive: true });
        fs.writeFileSync(file, safeStorage.encryptString(key), { mode: 0o600 });
      } catch { throw failure("key_storage"); }
    },
    getKey() { available(); try { return safeStorage.decryptString(fs.readFileSync(file)); } catch { throw failure("key_storage"); } },
    clearKey() { fs.rmSync(file, { force: true }); },
  };
}
module.exports = { status, download, remove, keyStore };
