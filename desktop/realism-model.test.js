"use strict";
const { test } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const http = require("node:http");
const crypto = require("node:crypto");
const model = require("./realism-model");
const body = Buffer.alloc(16384, 7);
const expected = { expectedBytes: body.length, expectedSha: crypto.createHash("sha256").update(body).digest("hex") };
function temp(t) { const root = fs.mkdtempSync(path.join(os.tmpdir(), "realism-test-")); t.after(() => fs.rmSync(root, { recursive: true, force: true })); return root; }
function dest(root) { return path.join(root, "models/realism/broncho-flow-int8.onnx"); }
async function serve(t, handler) {
  const server = http.createServer(handler);
  await new Promise(resolve => server.listen(0, "127.0.0.1", resolve));
  t.after(() => new Promise(resolve => { server.closeAllConnections(); server.close(resolve); }));
  return `http://127.0.0.1:${server.address().port}/`;
}
function options(dataRoot, baseUrl, extra = {}) { return { dataRoot, baseUrl, key: crypto.randomBytes(24).toString("hex"), ...expected, ...extra }; }
test("full download, verified cache invalidation, bad and absent status", async t => {
  const root = temp(t);
  assert.equal(model.status(root, expected).state, "absent");
  const base = await serve(t, (req, res) => { assert.ok(req.headers.authorization.startsWith("Bearer ")); res.end(body); });
  assert.equal((await model.download(options(root, base))).state, "verified");
  assert.equal(model.status(root, expected).state, "verified");
  fs.writeFileSync(dest(root), Buffer.alloc(body.length));
  const future = new Date(Date.now() + 10000); fs.utimesSync(dest(root), future, future);
  assert.equal(model.status(root, expected).state, "bad");
  model.remove(root); assert.equal(model.status(root, expected).state, "absent");
});
test("resume after server cuts a stream", async t => {
  const root = temp(t); let count = 0;
  const base = await serve(t, (req, res) => {
    if (++count === 1) { res.writeHead(200, { "Content-Length": body.length }); res.write(body.subarray(0, 500)); setTimeout(() => res.destroy(), 20); }
    else { assert.equal(req.headers.range, "bytes=500-"); res.writeHead(206, { "Content-Range": `bytes 500-${body.length - 1}/${body.length}` }); res.end(body.subarray(500)); }
  });
  await assert.rejects(model.download(options(root, base)), { code: "interrupted" });
  assert.deepEqual(model.status(root, expected), { state: "partial", bytes: 500, total: body.length });
  assert.equal((await model.download(options(root, base))).state, "verified");
});
test("401 and transport errors never expose credentials", async t => {
  const root = temp(t); const logs = [];
  const old = console.log; console.log = (...args) => logs.push(args.join(" "));
  t.after(() => { console.log = old; });
  const base = await serve(t, (_req, res) => { res.writeHead(401); res.end(); });
  const opts = options(root, base);
  await assert.rejects(model.download(opts), error => { assert.equal(error.code, "bad_key"); assert.ok(!String(error.stack).includes(opts.key)); assert.ok(!JSON.stringify(error).includes(opts.key)); return true; });
  assert.ok(!logs.join("").includes(opts.key));
});
test("checksum mismatch removes partial", async t => {
  const root = temp(t);
  const base = await serve(t, (_req, res) => res.end(Buffer.alloc(body.length)));
  await assert.rejects(model.download(options(root, base)), { code: "checksum" });
  assert.ok(!fs.existsSync(dest(root) + ".partial"));
});
test("cancel preserves partial and can resume", async t => {
  const root = temp(t); const abort = new AbortController();
  const base = await serve(t, (req, res) => {
    if (req.headers.range) {
      assert.equal(req.headers.range, "bytes=500-");
      res.writeHead(206, { "Content-Range": `bytes 500-${body.length - 1}/${body.length}` });
      res.end(body.subarray(500));
    } else { res.writeHead(200, { "Content-Length": body.length }); res.write(body.subarray(0, 500)); }
  });
  await assert.rejects(model.download(options(root, base, { signal: abort.signal, onProgress: ({ bytes }) => { if (bytes) abort.abort(); } })), { code: "cancelled" });
  assert.equal(model.status(root, expected).state, "partial");
  assert.equal(fs.statSync(dest(root) + ".partial").size, 500);
  assert.equal((await model.download(options(root, base))).state, "verified");
});
test("encrypted key storage and unavailable keychain", t => {
  const root = temp(t); let available = true;
  const secret = crypto.randomBytes(24).toString("hex");
  const safe = { isEncryptionAvailable: () => available,
    encryptString: value => Buffer.from(value).map(byte => byte ^ 0xaa),
    decryptString: value => Buffer.from(value.map(byte => byte ^ 0xaa)).toString() };
  const keys = model.keyStore(root, safe);
  assert.equal(keys.hasKey(), false); keys.setKey(secret);
  assert.equal(keys.hasKey(), true); assert.ok(keys.getKey() === secret);
  assert.ok(!fs.readFileSync(path.join(root, "realism-key.bin")).includes(Buffer.from(secret)));
  available = false;
  assert.throws(() => keys.setKey(secret), { code: "no_keychain" });
  assert.throws(() => keys.getKey(), { code: "no_keychain" });
  keys.clearKey(); assert.equal(keys.hasKey(), false);
});

test("redirects do not forward the bearer credential", async t => {
  const root = temp(t); let forwarded = false;
  const other = await serve(t, (_req, res) => { forwarded = true; res.end(body); });
  const base = await serve(t, (_req, res) => { res.writeHead(302, { Location: other }); res.end(); });
  await assert.rejects(model.download(options(root, base)), { code: "interrupted" });
  assert.equal(forwarded, false);
});
test("server ignoring Range restarts the partial", async t => {
  const root = temp(t);
  fs.mkdirSync(path.dirname(dest(root)), { recursive: true });
  fs.writeFileSync(dest(root) + ".partial", body.subarray(0, 500));
  const base = await serve(t, (req, res) => { assert.equal(req.headers.range, "bytes=500-"); res.end(body); });
  assert.equal((await model.download(options(root, base))).state, "verified");
});
