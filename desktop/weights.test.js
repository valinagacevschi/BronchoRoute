// Changes: manifest, status, resume, checksum, and zip safety for model weights.
"use strict";

const assert = require("node:assert/strict");
const { spawnSync } = require("node:child_process");
const crypto = require("node:crypto");
const fs = require("node:fs");
const http = require("node:http");
const os = require("node:os");
const path = require("node:path");
const zlib = require("node:zlib");
const test = require("node:test");
const {
  CHECKSUM_ERROR,
  UNSAFE_ENTRY,
  assertManifest,
  downloadWeights,
  loadManifest,
  weightsBaseUrl,
  weightsRequired,
  weightsStatus,
} = require("./weights");

function buildZip(entries) {
  const locals = [];
  const centrals = [];
  let offset = 0;
  for (const entry of entries) {
    const name = Buffer.from(entry.name, "utf8");
    const data = Buffer.isBuffer(entry.data) ? entry.data : Buffer.from(entry.data);
    const crc = zlib.crc32(data) >>> 0;
    const local = Buffer.alloc(30 + name.length);
    local.writeUInt32LE(0x04034b50, 0);
    local.writeUInt16LE(20, 4);
    local.writeUInt16LE(0, 8);
    local.writeUInt16LE(0, 10);
    local.writeUInt16LE(0x21, 12);
    local.writeUInt32LE(crc, 14);
    local.writeUInt32LE(data.length, 18);
    local.writeUInt32LE(data.length, 22);
    local.writeUInt16LE(name.length, 26);
    name.copy(local, 30);
    locals.push(local, data);
    const central = Buffer.alloc(46 + name.length);
    const mode = entry.mode == null ? 0o100644 : entry.mode;
    central.writeUInt32LE(0x02014b50, 0);
    central.writeUInt16LE((3 << 8) | 20, 4);
    central.writeUInt16LE(20, 6);
    central.writeUInt16LE(0, 10);
    central.writeUInt16LE(0, 12);
    central.writeUInt16LE(0x21, 14);
    central.writeUInt32LE(crc, 16);
    central.writeUInt32LE(data.length, 20);
    central.writeUInt32LE(data.length, 24);
    central.writeUInt16LE(name.length, 28);
    central.writeUInt32LE((mode << 16) >>> 0, 38);
    central.writeUInt32LE(offset, 42);
    name.copy(central, 46);
    centrals.push(central);
    offset += local.length + data.length;
  }
  const cd = Buffer.concat(centrals);
  const eocd = Buffer.alloc(22);
  eocd.writeUInt32LE(0x06054b50, 0);
  eocd.writeUInt16LE(entries.length, 8);
  eocd.writeUInt16LE(entries.length, 10);
  eocd.writeUInt32LE(cd.length, 12);
  eocd.writeUInt32LE(offset, 16);
  return Buffer.concat([...locals, cd, eocd]);
}

function manifestFor(zip, dataset = "Dataset001_demo") {
  return {
    repo: "valinagacevschi/BronchoRoute",
    tag: "weights-v1",
    totalsegmentator_version: "2.18.0",
    files: [{
      dataset,
      asset: `${dataset}.zip`,
      bytes: zip.length,
      sha256: crypto.createHash("sha256").update(zip).digest("hex"),
    }],
  };
}

function serve(handler) {
  const server = http.createServer(handler);
  return new Promise((resolve) => {
    server.listen(0, "127.0.0.1", () => {
      const { port } = server.address();
      resolve({
        baseUrl: `http://127.0.0.1:${port}/`,
        close() {
          return new Promise((done) => {
            server.close(done);
            if (typeof server.closeAllConnections === "function") server.closeAllConnections();
          });
        },
      });
    });
  });
}

function sendBytes(res, body, status, extraHeaders) {
  res.writeHead(status, {
    "Content-Type": "application/zip",
    "Accept-Ranges": "bytes",
    "Content-Length": String(body.length),
    ...extraHeaders,
  });
  res.end(body);
}

test("weights manifest lists the datasets the app runs", () => {
  const manifest = loadManifest(path.join(__dirname, "weights-manifest.json"));
  assert.equal(manifest.repo, "valinagacevschi/BronchoRoute");
  assert.equal(manifest.tag, "weights-v1");
  assert.equal(manifest.totalsegmentator_version, "2.18.0");
  assert.deepEqual(manifest.files.map((file) => file.dataset), [
    "Dataset117_lung_airways_arteries_veins_282subj",
    "Dataset291_TotalSegmentator_part1_organs_1559subj",
    "Dataset297_TotalSegmentator_total_3mm_1559subj",
    "Dataset298_TotalSegmentator_total_6mm_1559subj",
  ]);
  for (const file of manifest.files) {
    assert.equal(file.asset, `${file.dataset}.zip`);
    assert.equal(Number.isInteger(file.bytes) && file.bytes > 0, true);
    assert.match(file.sha256, /^[0-9a-f]{64}$/);
  }
  assert.throws(() => assertManifest({ ...manifest, files: [] }), /manifest/);
  assert.throws(() => assertManifest({ ...manifest, repo: "not a repo" }), /manifest/);
});

test("weights gate and base URL follow packaging and the environment", () => {
  const manifest = {
    repo: "valinagacevschi/BronchoRoute",
    tag: "weights-v1",
    totalsegmentator_version: "2.18.0",
    files: [{
      dataset: "Dataset001_demo",
      asset: "Dataset001_demo.zip",
      bytes: 1,
      sha256: "a".repeat(64),
    }],
  };
  assert.equal(weightsRequired(false, {}), false);
  assert.equal(weightsRequired(false, { BRONCHOROUTE_FORCE_WEIGHTS: "1" }), true);
  assert.equal(weightsRequired(true, {}), true);
  assert.equal(
    weightsBaseUrl(manifest, {}),
    "https://github.com/valinagacevschi/BronchoRoute/releases/download/weights-v1/",
  );
  assert.equal(
    weightsBaseUrl(manifest, { BRONCHOROUTE_WEIGHTS_BASE_URL: "  http://127.0.0.1:9/w/  " }),
    "http://127.0.0.1:9/w/",
  );
});

test("weightsStatus reports ready only when the marker matches", () => {
  const zip = buildZip([{ name: "hello.txt", data: "hello" }]);
  const manifest = manifestFor(zip);
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "bronchoroute-status-"));
  try {
    assert.deepEqual(weightsStatus(root, manifest).missing.map((file) => file.dataset), [
      "Dataset001_demo",
    ]);
    const dir = path.join(root, "nnunet", "results", "Dataset001_demo");
    fs.mkdirSync(dir, { recursive: true });
    assert.equal(weightsStatus(root, manifest).ready, false);
    fs.writeFileSync(path.join(dir, ".verified"), `${"b".repeat(64)}\n`);
    assert.equal(weightsStatus(root, manifest).ready, false);
    fs.writeFileSync(path.join(dir, ".verified"), `${manifest.files[0].sha256}\n`);
    const ready = weightsStatus(root, manifest);
    assert.equal(ready.ready, true);
    assert.deepEqual(ready.missing, []);
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
  }
});

test("weights download verifies, extracts, and reports monotone progress", { timeout: 10000 }, async () => {
  const zip = buildZip([{ name: "hello.txt", data: "hello from weights" }]);
  const manifest = manifestFor(zip);
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "bronchoroute-dl-"));
  const halfway = Math.max(1, Math.floor(zip.length / 2));
  const server = await serve(async (req, res) => {
    res.writeHead(200, {
      "Content-Type": "application/zip",
      "Content-Length": String(zip.length),
      "Accept-Ranges": "bytes",
    });
    res.write(zip.subarray(0, halfway));
    await new Promise((resolve) => setTimeout(resolve, 40));
    res.end(zip.subarray(halfway));
  });
  const received = [];
  try {
    const status = await downloadWeights({
      modelsDir: root,
      manifest,
      baseUrl: server.baseUrl,
      onProgress(event) {
        received.push(event.received);
        assert.equal(event.total, zip.length);
      },
    });
    assert.equal(status.ready, true);
    assert.ok(received.length >= 2);
    for (let i = 1; i < received.length; i += 1) {
      assert.ok(received[i] >= received[i - 1]);
    }
    assert.equal(received.at(-1), zip.length);
    assert.ok(received.some((value) => value > 0 && value < zip.length));
    const dataset = path.join(root, "nnunet", "results", "Dataset001_demo");
    assert.equal(fs.readFileSync(path.join(dataset, "hello.txt"), "utf8"), "hello from weights");
    assert.equal(fs.readFileSync(path.join(dataset, ".verified"), "utf8").trim(), manifest.files[0].sha256);
    assert.equal(fs.existsSync(path.join(root, ".partial", manifest.files[0].asset)), false);
  } finally {
    await server.close();
    fs.rmSync(root, { recursive: true, force: true });
  }
});

test("weights download deletes a partial when the checksum does not match", { timeout: 10000 }, async () => {
  const zip = buildZip([{ name: "hello.txt", data: "wrong" }]);
  const manifest = manifestFor(zip);
  manifest.files[0].sha256 = "a".repeat(64);
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "bronchoroute-badhash-"));
  const server = await serve((req, res) => sendBytes(res, zip, 200));
  try {
    await assert.rejects(
      () => downloadWeights({ modelsDir: root, manifest, baseUrl: server.baseUrl }),
      (err) => err instanceof Error && err.message === CHECKSUM_ERROR,
    );
    assert.equal(fs.existsSync(path.join(root, ".partial", manifest.files[0].asset)), false);
    assert.equal(fs.existsSync(path.join(root, "nnunet", "results", "Dataset001_demo")), false);
  } finally {
    await server.close();
    fs.rmSync(root, { recursive: true, force: true });
  }
});

test("interrupted weights download resumes with Range", { timeout: 10000 }, async () => {
  const zip = buildZip([{ name: "hello.txt", data: "resume-this-model-payload" }]);
  const manifest = manifestFor(zip);
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "bronchoroute-resume-"));
  const ranges = [];
  let hits = 0;
  const server = await serve((req, res) => {
    hits += 1;
    ranges.push(req.headers.range || "");
    if (hits === 1) {
      res.writeHead(200, {
        "Content-Type": "application/zip",
        "Content-Length": String(zip.length),
        "Accept-Ranges": "bytes",
      });
      res.write(zip.subarray(0, 48));
      return;
    }
    const match = /^bytes=(\d+)-$/.exec(req.headers.range || "");
    assert.ok(match);
    const start = Number(match[1]);
    const slice = zip.subarray(start);
    res.writeHead(206, {
      "Content-Type": "application/zip",
      "Content-Length": String(slice.length),
      "Content-Range": `bytes ${start}-${zip.length - 1}/${zip.length}`,
      "Accept-Ranges": "bytes",
    });
    res.end(slice);
  });
  const controller = new AbortController();
  try {
    const first = downloadWeights({
      modelsDir: root,
      manifest,
      baseUrl: server.baseUrl,
      signal: controller.signal,
      onProgress(event) {
        if (event.received >= 48) controller.abort();
      },
    });
    await assert.rejects(first, /cancelled/);
    const partial = path.join(root, ".partial", manifest.files[0].asset);
    assert.ok(fs.existsSync(partial));
    const partialSize = fs.statSync(partial).size;
    assert.ok(partialSize >= 48);
    assert.ok(partialSize < zip.length);
    const status = await downloadWeights({
      modelsDir: root,
      manifest,
      baseUrl: server.baseUrl,
    });
    assert.equal(status.ready, true);
    assert.match(ranges[1], /^bytes=\d+-$/);
    assert.equal(Number(ranges[1].slice("bytes=".length, -1)), partialSize);
    const dataset = path.join(root, "nnunet", "results", "Dataset001_demo");
    assert.equal(fs.readFileSync(path.join(dataset, "hello.txt"), "utf8"), "resume-this-model-payload");
    assert.equal(fs.existsSync(partial), false);
  } finally {
    await server.close();
    fs.rmSync(root, { recursive: true, force: true });
  }
});

test("weights zip with a parent-directory entry is refused", { timeout: 10000 }, async () => {
  const zip = buildZip([
    { name: "ok.txt", data: "kept" },
    { name: "../evil", data: "pwned" },
  ]);
  const manifest = manifestFor(zip);
  const parent = fs.mkdtempSync(path.join(os.tmpdir(), "bronchoroute-slip-"));
  const root = path.join(parent, "models");
  const server = await serve((req, res) => sendBytes(res, zip, 200));
  try {
    await assert.rejects(
      () => downloadWeights({ modelsDir: root, manifest, baseUrl: server.baseUrl }),
      (err) => err instanceof Error && err.message === UNSAFE_ENTRY,
    );
    assert.deepEqual(fs.readdirSync(parent), ["models"]);
    assert.equal(fs.existsSync(path.join(parent, "evil")), false);
    assert.equal(fs.existsSync(path.join(root, "nnunet", "results", "evil")), false);
    assert.equal(fs.existsSync(path.join(root, "nnunet", "results", "Dataset001_demo", "ok.txt")), false);
  } finally {
    await server.close();
    fs.rmSync(parent, { recursive: true, force: true });
  }
});

test("weights zip with a symlink entry is refused", { timeout: 10000 }, async () => {
  const zip = buildZip([{ name: "link", data: "/tmp/outside", mode: 0o120777 }]);
  const manifest = manifestFor(zip);
  const parent = fs.mkdtempSync(path.join(os.tmpdir(), "bronchoroute-link-"));
  const root = path.join(parent, "models");
  const server = await serve((req, res) => sendBytes(res, zip, 200));
  try {
    await assert.rejects(
      () => downloadWeights({ modelsDir: root, manifest, baseUrl: server.baseUrl }),
      (err) => err instanceof Error && err.message === UNSAFE_ENTRY,
    );
    assert.deepEqual(fs.readdirSync(parent), ["models"]);
    const dataset = path.join(root, "nnunet", "results", "Dataset001_demo");
    assert.equal(fs.existsSync(path.join(dataset, "link")), false);
    if (fs.existsSync(dataset)) {
      for (const name of fs.readdirSync(dataset)) {
        assert.equal(fs.lstatSync(path.join(dataset, name)).isSymbolicLink(), false);
      }
    }
  } finally {
    await server.close();
    fs.rmSync(parent, { recursive: true, force: true });
  }
});

test("weights download extracts a Python stored zip", { timeout: 10000 }, async () => {
  const zipPath = path.join(os.tmpdir(), `bronchoroute-pyzip-${process.pid}-${Date.now()}.zip`);
  const script = `
import sys, zipfile
info = zipfile.ZipInfo("hello.txt", (1980, 1, 1, 0, 0, 0))
info.compress_type = zipfile.ZIP_STORED
info.create_system = 3
info.external_attr = (0o100644) << 16
info.flag_bits = 0
with zipfile.ZipFile(sys.argv[1], "w", compression=zipfile.ZIP_STORED, allowZip64=False) as archive:
    archive.writestr(info, b"from-python")
`;
  const created = spawnSync("python3", ["-c", script, zipPath], { encoding: "utf8" });
  assert.equal(created.status, 0, created.stderr);
  const zip = fs.readFileSync(zipPath);
  fs.rmSync(zipPath, { force: true });
  const manifest = manifestFor(zip, "Dataset002_python");
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "bronchoroute-py-"));
  const server = await serve((req, res) => sendBytes(res, zip, 200));
  try {
    await downloadWeights({ modelsDir: root, manifest, baseUrl: server.baseUrl });
    const file = path.join(root, "nnunet", "results", "Dataset002_python", "hello.txt");
    assert.equal(fs.readFileSync(file, "utf8"), "from-python");
  } finally {
    await server.close();
    fs.rmSync(root, { recursive: true, force: true });
  }
});
