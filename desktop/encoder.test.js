// Changes: require yuv420p and color_range tv from the videotoolbox encode.
// Changes: videotoolbox args, a 2s MP4 probe, and cancel deletes the temp file.
"use strict";

const assert = require("node:assert/strict");
const { execFileSync, spawnSync } = require("node:child_process");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const test = require("node:test");
const { resolveFfmpeg } = require("./lib");
const { ffmpegVideoArgs, startEncoder } = require("./encoder");

function fakeProcess() {
  const listeners = { exit: [], error: [] };
  const stdinListeners = { error: [], drain: [] };
  const proc = {
    exitCode: null,
    signalCode: null,
    killed: false,
    stdin: {
      destroyed: false,
      write() {
        return true;
      },
      end() {},
      destroy() {
        this.destroyed = true;
      },
      on(event, fn) {
        (stdinListeners[event] || (stdinListeners[event] = [])).push(fn);
        return this;
      },
      off(event, fn) {
        const list = stdinListeners[event] || [];
        const index = list.indexOf(fn);
        if (index >= 0) list.splice(index, 1);
        return this;
      },
      once(event, fn) {
        const wrap = (...args) => {
          this.off(event, wrap);
          fn(...args);
        };
        return this.on(event, wrap);
      },
    },
    stderr: { on() { return this; } },
    on(event, fn) {
      (listeners[event] || (listeners[event] = [])).push(fn);
      return this;
    },
    once(event, fn) {
      const wrap = (...args) => {
        const list = listeners[event] || [];
        const index = list.indexOf(wrap);
        if (index >= 0) list.splice(index, 1);
        fn(...args);
      };
      return this.on(event, wrap);
    },
    kill() {
      this.killed = true;
      this.signalCode = "SIGKILL";
      const exit = (listeners.exit || []).slice();
      for (const fn of exit) fn(null, "SIGKILL");
    },
  };
  return proc;
}

test("encoder args use h264_videotoolbox and strip metadata", async () => {
  const seen = [];
  function spawn(cmd, args) {
    seen.push({ cmd, args });
    return fakeProcess();
  }
  const outPath = path.join(os.tmpdir(), "bronchoroute-43-out.mp4");
  const hd = startEncoder({
    ffmpegPath: "/opt/ffmpeg/ffmpeg",
    width: 1920,
    height: 1080,
    fps: 30,
    outPath,
    spawn,
  });
  const sd = startEncoder({
    ffmpegPath: "/opt/ffmpeg/ffmpeg",
    width: 1280,
    height: 720,
    fps: 30,
    outPath,
    spawn,
  });
  try {
    assert.equal(seen.length, 2);
    for (const row of seen) {
      assert.equal(row.cmd, "/opt/ffmpeg/ffmpeg");
      assert.equal(row.args.some((arg) => String(arg).includes("libx264")), false);
      assert.equal(row.args.includes("h264_videotoolbox"), true);
      const meta = row.args.indexOf("-map_metadata");
      assert.equal(row.args[meta + 1], "-1");
      assert.notEqual(row.args.at(-1), outPath);
      assert.match(path.basename(row.args.at(-1)), /^\.bronchoroute-[0-9a-f]+\.mp4$/);
      assert.deepEqual(row.args, ffmpegVideoArgs({
        width: row.args.includes("8M") ? 1920 : 1280,
        height: row.args.includes("8M") ? 1080 : 720,
        fps: 30,
        outPath: row.args.at(-1),
      }));
    }
    assert.equal(seen[0].args[seen[0].args.indexOf("-b:v") + 1], "8M");
    assert.equal(seen[1].args[seen[1].args.indexOf("-b:v") + 1], "4M");
    assert.deepEqual(ffmpegVideoArgs({
      width: 1920,
      height: 1080,
      fps: 30,
      outPath: "OUT",
    }), [
      "-f", "image2pipe",
      "-c:v", "mjpeg",
      "-framerate", "30",
      "-i", "-",
      "-c:v", "h264_videotoolbox",
      "-b:v", "8M",
      "-vf", "scale=out_range=tv,format=yuv420p",
      "-color_range", "tv",
      "-r", "30",
      "-movflags", "+faststart",
      "-map_metadata", "-1",
      "-metadata", "creation_time=",
      "-an",
      "OUT",
    ]);
  } finally {
    await hd.cancel();
    await sd.cancel();
  }
});

function ffmpegOnPath(t) {
  try {
    return resolveFfmpeg({
      packaged: false,
      env: { PATH: process.env.PATH || "" },
    });
  } catch (err) {
    t.skip("ffmpeg not available");
    return null;
  }
}

function ratio(value) {
  const [num, den] = String(value).split("/").map(Number);
  if (!den) return num;
  return num / den;
}

test("encoder writes a 2s h264 mp4", { timeout: 60_000 }, async (t) => {
  const ffmpegPath = ffmpegOnPath(t);
  if (!ffmpegPath) return;
  const ffprobe = path.join(path.dirname(ffmpegPath), "ffprobe");
  if (!fs.existsSync(ffprobe)) {
    t.skip("ffprobe not available");
    return;
  }
  const jpeg = execFileSync(ffmpegPath, [
    "-hide_banner", "-loglevel", "error",
    "-f", "lavfi", "-i", "color=c=black:s=1280x720",
    "-frames:v", "1",
    "-f", "image2pipe", "-c:v", "mjpeg",
    "-",
  ], { maxBuffer: 8 * 1024 * 1024 });
  assert.equal(jpeg[0], 0xff);
  assert.equal(jpeg[1], 0xd8);
  assert.equal(jpeg[2], 0xff);
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "bronchoroute43-local-001-"));
  const outPath = path.join(dir, "clip.mp4");
  const enc = startEncoder({
    ffmpegPath,
    width: 1280,
    height: 720,
    fps: 30,
    outPath,
  });
  try {
    for (let i = 0; i < 60; i += 1) {
      await enc.writeFrame(jpeg);
    }
    await enc.finish();
    assert.equal(fs.existsSync(outPath), true);
    assert.deepEqual(fs.readdirSync(dir), ["clip.mp4"]);
    const probe = spawnSync(ffprobe, [
      "-v", "error",
      "-print_format", "json",
      "-show_format",
      "-show_streams",
      outPath,
    ], { encoding: "utf8" });
    assert.equal(probe.status, 0, probe.stderr);
    const info = JSON.parse(probe.stdout);
    const stream = info.streams.find((row) => row.codec_type === "video");
    assert.ok(stream);
    assert.equal(stream.codec_name, "h264");
    assert.equal(stream.width, 1280);
    assert.equal(stream.height, 720);
    assert.equal(stream.pix_fmt, "yuv420p");
    assert.equal(stream.color_range, "tv");
    assert.ok(Math.abs(ratio(stream.avg_frame_rate) - 30) < 0.01, stream.avg_frame_rate);
    assert.ok(Math.abs(ratio(stream.r_frame_rate) - 30) < 0.01, stream.r_frame_rate);
    assert.ok(Math.abs(Number(info.format.duration) - 2) <= 0.1, info.format.duration);
    assert.equal(info.streams.some((row) => row.codec_type === "audio"), false);
    const tags = { ...(info.format.tags || {}), ...(stream.tags || {}) };
    for (const [key, value] of Object.entries(tags)) {
      const name = key.toLowerCase();
      assert.equal(["title", "comment", "creation_time"].includes(name), false, key);
      const text = String(value);
      assert.equal(text.toLowerCase().includes("libx264"), false, text);
      assert.equal(text.includes("local-"), false, text);
      assert.equal(text.includes(dir), false, text);
    }
    const blob = fs.readFileSync(outPath);
    assert.equal(blob.includes(Buffer.from("libx264")), false);
    assert.equal(blob.includes(Buffer.from("local-001")), false);
    assert.equal(blob.includes(Buffer.from("local-007")), false);
    assert.equal(blob.includes(Buffer.from(dir)), false);
    assert.equal(blob.includes(Buffer.from(outPath)), false);
    assert.equal(JSON.stringify(info).includes("libx264"), false);
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
});

test("encoder cancel leaves no file", { timeout: 30_000 }, async (t) => {
  const ffmpegPath = ffmpegOnPath(t);
  if (!ffmpegPath) return;
  const jpeg = execFileSync(ffmpegPath, [
    "-hide_banner", "-loglevel", "error",
    "-f", "lavfi", "-i", "color=c=black:s=1280x720",
    "-frames:v", "1",
    "-f", "image2pipe", "-c:v", "mjpeg",
    "-",
  ], { maxBuffer: 8 * 1024 * 1024 });
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "bronchoroute43-cancel-"));
  const outPath = path.join(dir, "clip.mp4");
  const enc = startEncoder({
    ffmpegPath,
    width: 1280,
    height: 720,
    fps: 30,
    outPath,
  });
  try {
    await enc.writeFrame(jpeg);
    await enc.writeFrame(jpeg);
    await enc.cancel();
    assert.equal(fs.existsSync(outPath), false);
    assert.deepEqual(fs.readdirSync(dir), []);
  } finally {
    await enc.cancel();
    fs.rmSync(dir, { recursive: true, force: true });
  }
});
