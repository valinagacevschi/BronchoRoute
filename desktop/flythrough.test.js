// Changes: assert Record IPC reuse without exact channel-list lockstep (allows #47 weights).
"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const { ffmpegVideoArgs } = require("./encoder");

const ROOT = path.resolve(__dirname, "..");
const TEMPLATE = path.join(ROOT, "prototype", "renderer.template.html");
const MAIN = path.join(__dirname, "main.js");
const PRELOAD = path.join(__dirname, "preload.js");

test("fly-through uses the Record IPC path", () => {
  const template = fs.readFileSync(TEMPLATE, "utf8");
  assert.match(template, /async function renderFlythrough\b/);
  assert.match(template, /async function startFlythrough\b/);
  const start = template.indexOf("async function startFlythrough");
  const end = template.indexOf("\nlet saveImageBusy", start);
  assert.ok(start >= 0 && end > start, "startFlythrough body");
  const body = template.slice(start, end);
  assert.match(body, /recordStart\(\s*\{/);
  assert.match(body, /recordFrame\(\s*\{/);
  assert.match(body, /recordStop\(\s*\{/);
  assert.match(body, /recordCancel\(\s*\{/);
  assert.match(body, /api\.recordStart\(\{/);
  assert.match(body, /defaultName:\s*name/);
  assert.match(body, /width:\s*size\.width/);
  assert.match(body, /height:\s*size\.height/);
  assert.equal(body.includes("api.recordStart({"), true);
  assert.equal(/ffmpeg|h264_videotoolbox|spawn\b/.test(body), false);
  assert.equal(template.includes("bronchoroute:fly"), false);

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
  assert.equal(handles.some((ch) => /fly/i.test(ch)), false);
  assert.match(main, /fps:\s*30/);
  assert.equal(main.includes("flythrough"), false);

  const preload = fs.readFileSync(PRELOAD, "utf8");
  const invokes = [...preload.matchAll(/ipcRenderer\.invoke\(\s*"([^"]+)"/g)].map((m) => m[1]);
  for (const ch of [
    "bronchoroute:record-start",
    "bronchoroute:record-frame",
    "bronchoroute:record-stop",
    "bronchoroute:record-cancel",
  ]) {
    assert.ok(invokes.includes(ch), ch);
  }
  assert.equal(
    [...preload.matchAll(/"(bronchoroute:[^"]+)"/g)].some((m) => /fly/i.test(m[1])),
    false,
  );

  assert.deepEqual(ffmpegVideoArgs({
    width: 1280,
    height: 720,
    fps: 30,
    outPath: "OUT",
  }), [
    "-f", "image2pipe",
    "-c:v", "mjpeg",
    "-framerate", "30",
    "-i", "-",
    "-c:v", "h264_videotoolbox",
    "-b:v", "4M",
    "-vf", "scale=out_range=tv,format=yuv420p",
    "-color_range", "tv",
    "-r", "30",
    "-movflags", "+faststart",
    "-map_metadata", "-1",
    "-metadata", "creation_time=",
    "-an",
    "OUT",
  ]);
});
