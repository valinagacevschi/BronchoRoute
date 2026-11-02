// Changes: shell helper coverage excludes Python-owned settings persistence.
"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const test = require("node:test");
const {
  DISCLAIMER,
  buildSidecarEnv,
  firstLocalCaseId,
  isAllowedAppUrl,
  newLaunchToken,
  parsePortLine,
  requestFilterUrls,
  resolveDataRoot,
  splitCompleteLines,
} = require("./lib");

test("parsePortLine accepts a single sidecar announcement", () => {
  assert.equal(parsePortLine("BRONCHOROUTE_PORT=8765"), 8765);
  assert.equal(parsePortLine("  BRONCHOROUTE_PORT=1\r"), 1);
  assert.equal(parsePortLine("BRONCHOROUTE_PORT=65535"), 65535);
  assert.equal(parsePortLine("BRONCHOROUTE_PORT=0"), null);
  assert.equal(parsePortLine("BRONCHOROUTE_PORT=65536"), null);
  assert.equal(parsePortLine("BRONCHOROUTE_PORT=080"), null);
  assert.equal(parsePortLine("BRONCHOROUTE_PORT="), null);
  assert.equal(parsePortLine("BRONCHOROUTE_PORT=12abc"), null);
  assert.equal(parsePortLine("noise"), null);
});

test("splitCompleteLines keeps a partial trailing line", () => {
  const split = splitCompleteLines("noise\r\nBRONCHOROUTE_PORT=9\npartial");
  assert.deepEqual(split.lines, ["noise", "BRONCHOROUTE_PORT=9"]);
  assert.equal(split.rest, "partial");
  assert.equal(parsePortLine(split.lines[1]), 9);
});

test("buildSidecarEnv strips PYTHONPATH and sets the sidecar vars", () => {
  const base = {
    PYTHONPATH: "/tmp/hidden",
    PATH: "/usr/bin",
    LUNGNAV_PORT: "8765",
    LUNGNAV_HOST: "0.0.0.0",
    LUNGNAV_DATA_ROOT: "/real",
  };
  const env = buildSidecarEnv(base, {
    token: "abc",
    dataRoot: "/data",
    tmpDir: "/data/tmp",
    logDir: "/data/logs",
  });
  assert.equal(base.PYTHONPATH, "/tmp/hidden");
  assert.equal(Object.hasOwn(env, "PYTHONPATH"), false);
  assert.equal(env.PATH, "/usr/bin");
  assert.equal(Object.hasOwn(env, "LUNGNAV_PORT"), false);
  assert.equal(Object.hasOwn(env, "LUNGNAV_HOST"), false);
  assert.equal(env.BRONCHOROUTE_TOKEN, "abc");
  assert.equal(env.LUNGNAV_DATA_ROOT, "/data");
  assert.equal(env.LUNGNAV_TMP_DIR, "/data/tmp");
  assert.equal(env.LUNGNAV_LOG_DIR, "/data/logs");
  assert.equal(env.PYTHONUNBUFFERED, "1");
  assert.throws(
    () => buildSidecarEnv({}, {
      token: "  ",
      dataRoot: "/data",
      tmpDir: "/data/tmp",
      logDir: "/data/logs",
    }),
    /non-blank/,
  );
});

test("origin filter allows only the sidecar origin", () => {
  assert.deepEqual(requestFilterUrls(9), ["http://127.0.0.1:9/*"]);
  assert.equal(isAllowedAppUrl("http://127.0.0.1:9/", 9), true);
  assert.equal(isAllowedAppUrl("http://127.0.0.1:9/api/cases?case=1", 9), true);
  assert.equal(isAllowedAppUrl("https://127.0.0.1:9/", 9), false);
  assert.equal(isAllowedAppUrl("http://localhost:9/", 9), false);
  assert.equal(isAllowedAppUrl("http://127.0.0.1:10/", 9), false);
  assert.equal(isAllowedAppUrl("http://evil.example/", 9), false);
  assert.equal(isAllowedAppUrl("http://127.0.0.1:9@evil.example/", 9), false);
  assert.equal(isAllowedAppUrl("file:///tmp/x", 9), false);
  assert.equal(isAllowedAppUrl("not a url", 9), false);
  assert.throws(() => requestFilterUrls(0), /invalid sidecar port/);
});

test("first local Case follows list order", () => {
  assert.equal(firstLocalCaseId([
    { id: "3" },
    { id: "local-002" },
    { id: "local-001" },
  ]), "local-002");
  assert.equal(firstLocalCaseId([]), null);
  assert.equal(firstLocalCaseId([{ id: 1 }]), null);
  assert.equal(firstLocalCaseId(null), null);
});

test("data root honors BRONCHOROUTE_DATA_ROOT", () => {
  const override = path.join(os.tmpdir(), "bronchoroute-override");
  assert.equal(
    resolveDataRoot({ BRONCHOROUTE_DATA_ROOT: `  ${override}  ` }, "/user/data"),
    override,
  );
  assert.equal(resolveDataRoot({}, "/user/data"), "/user/data");
  assert.equal(
    resolveDataRoot({ BRONCHOROUTE_DATA_ROOT: "   " }, "/user/data"),
    "/user/data",
  );
});

test("launch token is 32 bytes of hex", () => {
  const token = newLaunchToken();
  assert.match(token, /^[0-9a-f]{64}$/);
});

test("viewer template vendors fonts and the research footer", () => {
  const root = path.join(__dirname, "..", "prototype");
  const template = fs.readFileSync(path.join(root, "renderer.template.html"), "utf8");
  const rendered = fs.readFileSync(path.join(root, "renderer.html"), "utf8");
  const sentinel = "/*__AIRWAY_EMBED__*/";
  assert.equal(template.split(sentinel).length, 2);
  assert.equal(template.includes(DISCLAIMER), true);
  assert.equal(template.includes("fonts.googleapis.com"), false);
  assert.equal(template.includes("fonts/IBMPlexSans-Regular.woff2"), true);
  const parts = template.split(sentinel);
  const data = rendered.slice(parts[0].length, rendered.length - parts[1].length);
  assert.equal(rendered, template.replace(sentinel, data));
});
