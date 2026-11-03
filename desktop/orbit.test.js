// Changes: assert orbit/spin reuse Record IPC; no dedicated orbit/spin channels (#45).
"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const { isSaveVideoName } = require("./lib");

const ROOT = path.resolve(__dirname, "..");
const TEMPLATE = path.join(ROOT, "prototype", "renderer.template.html");
const MAIN = path.join(__dirname, "main.js");
const PRELOAD = path.join(__dirname, "preload.js");

test("orbit and spin use the Record IPC path", () => {
  const template = fs.readFileSync(TEMPLATE, "utf8");
  assert.match(template, /async function renderOrbit\b/);
  assert.match(template, /async function renderSpin\b/);
  assert.match(template, /async function startOrbit\b/);
  assert.match(template, /async function startSpin\b/);
  assert.match(template, /id="orbit-btn"/);
  assert.match(template, /id="spin-btn"/);
  assert.match(template, /anatomical-orbit_/);
  assert.match(template, /topology-spin_/);
  assert.match(template, /ORBIT_SPIN_FRAMES/);
  assert.equal(template.includes("bronchoroute:orbit"), false);
  assert.equal(template.includes("bronchoroute:spin"), false);
  assert.equal(/ffmpeg|h264_videotoolbox|spawn\b/.test(template.slice(
    template.indexOf("async function startOrbit"),
    template.indexOf("async function startSpin") + 400,
  )), false);

  for (const name of ["startOrbit", "startSpin", "startOfflineVideo"]) {
    const start = template.indexOf(`async function ${name}`);
    assert.ok(start >= 0, name);
  }
  const bodyStart = template.indexOf("async function startOfflineVideo");
  const bodyEnd = template.indexOf("\ndocument.getElementById(\"flythrough-btn\")", bodyStart);
  assert.ok(bodyStart >= 0 && bodyEnd > bodyStart, "startOfflineVideo body");
  const body = template.slice(bodyStart, bodyEnd);
  assert.match(body, /api\.recordStart\(\{/);
  assert.match(body, /api\.recordFrame\(\{/);
  assert.match(body, /api\.recordStop\(\{/);
  assert.match(body, /api\.recordCancel\(\{/);

  const main = fs.readFileSync(MAIN, "utf8");
  const handles = [...main.matchAll(/ipcMain\.handle\(\s*"([^"]+)"/g)].map((m) => m[1]);
  for (const ch of [
    "bronchoroute:record-start",
    "bronchoroute:record-frame",
    "bronchoroute:record-stop",
    "bronchoroute:record-cancel",
  ]) {
    assert.ok(handles.includes(ch), ch);
  }
  assert.equal(handles.some((ch) => /fly|orbit|spin/i.test(ch)), false);
  assert.equal(main.includes("orbit"), false);
  assert.equal(main.includes("spin"), false);

  const preload = fs.readFileSync(PRELOAD, "utf8");
  assert.equal(
    [...preload.matchAll(/"(bronchoroute:[^"]+)"/g)].some((m) => /fly|orbit|spin/i.test(m[1])),
    false,
  );

  assert.equal(isSaveVideoName("local-001_anatomical-orbit_20261103-0140.mp4"), true);
  assert.equal(isSaveVideoName("local-001_topology-spin_20261103-0140.mp4"), true);
});
