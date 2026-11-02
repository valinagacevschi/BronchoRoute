// Changes: convert MJPEG full range to tv yuv420p before h264_videotoolbox.
// Changes: encode Record JPEGs to MP4 with h264_videotoolbox and rename the temp file on finish.
"use strict";

const crypto = require("node:crypto");
const fs = require("node:fs");
const path = require("node:path");
const { spawn } = require("node:child_process");

function videoBitrate(width, height) {
  if (width === 1920 && height === 1080) return "8M";
  if (width === 1280 && height === 720) return "4M";
  throw new Error("Could not encode video");
}

function ffmpegVideoArgs({ width, height, fps, outPath }) {
  const rate = String(fps);
  return [
    "-f", "image2pipe",
    "-c:v", "mjpeg",
    "-framerate", rate,
    "-i", "-",
    "-c:v", "h264_videotoolbox",
    "-b:v", videoBitrate(width, height),
    "-vf", "scale=out_range=tv,format=yuv420p",
    "-color_range", "tv",
    "-r", rate,
    "-movflags", "+faststart",
    "-map_metadata", "-1",
    "-metadata", "creation_time=",
    "-an",
    outPath,
  ];
}

function toBuffer(bytes) {
  if (Buffer.isBuffer(bytes)) return bytes;
  if (bytes instanceof ArrayBuffer) return Buffer.from(bytes);
  if (ArrayBuffer.isView(bytes)) {
    return Buffer.from(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  }
  throw new Error("Could not encode video");
}

function startEncoder(options) {
  const ffmpegPath = options && options.ffmpegPath;
  const outPath = options && options.outPath;
  const width = options && options.width;
  const height = options && options.height;
  const fps = options && options.fps;
  if (typeof ffmpegPath !== "string" || ffmpegPath.trim() === "") {
    throw new Error("Video export needs ffmpeg");
  }
  if (typeof outPath !== "string" || outPath.trim() === "") {
    throw new Error("Could not encode video");
  }
  if (!(typeof fps === "number" && Number.isFinite(fps) && fps > 0)) {
    throw new Error("Could not encode video");
  }
  const tmpPath = path.join(
    path.dirname(outPath),
    `.bronchoroute-${crypto.randomBytes(8).toString("hex")}.mp4`,
  );
  const args = ffmpegVideoArgs({ width, height, fps, outPath: tmpPath });
  const spawnFn = options.spawn || spawn;
  const child = spawnFn(ffmpegPath, args, {
    stdio: ["pipe", "ignore", "pipe"],
    windowsHide: true,
  });
  if (child.stderr && typeof child.stderr.on === "function") {
    child.stderr.on("data", () => {});
  }
  if (child.stdin && typeof child.stdin.on === "function") {
    child.stdin.on("error", () => {});
  }

  let state = "open";
  let writeChain = Promise.resolve();
  let closedResult = null;
  const closedWaiters = [];

  function removeTmp() {
    try {
      fs.rmSync(tmpPath, { force: true });
    } catch (err) {
      // The temp file is already gone.
    }
  }

  function noteClosed(code) {
    if (closedResult) return;
    closedResult = { code: code == null ? 1 : code };
    const waiters = closedWaiters.splice(0);
    for (const waiter of waiters) waiter(closedResult.code);
  }

  function whenClosed() {
    if (child.exitCode != null || child.signalCode != null) {
      noteClosed(child.exitCode == null ? 1 : child.exitCode);
    }
    if (closedResult) return Promise.resolve(closedResult.code);
    return new Promise((resolve) => {
      closedWaiters.push(resolve);
    });
  }

  if (typeof child.on === "function") {
    child.on("error", () => {
      if (state === "open") state = "failed";
      removeTmp();
      noteClosed(1);
    });
    child.on("exit", (code) => noteClosed(code));
  }

  function writeBuffer(buf) {
    if (state !== "open") return Promise.reject(new Error("Could not encode video"));
    const stdin = child.stdin;
    if (!stdin || stdin.destroyed) return Promise.reject(new Error("Could not encode video"));
    return new Promise((resolve, reject) => {
      let settled = false;
      const done = (err) => {
        if (settled) return;
        settled = true;
        if (typeof stdin.off === "function") {
          stdin.off("error", onError);
          stdin.off("drain", onDrain);
        }
        if (err) reject(new Error("Could not encode video"));
        else resolve();
      };
      const onError = () => done(new Error("write failed"));
      const onDrain = () => done();
      if (typeof stdin.on === "function") stdin.on("error", onError);
      let queued = true;
      try {
        queued = stdin.write(buf);
      } catch (err) {
        done(err);
        return;
      }
      if (queued !== false) done();
      else if (typeof stdin.once === "function") stdin.once("drain", onDrain);
      else done(new Error("write stalled"));
    });
  }

  function writeFrame(bytes) {
    const buf = toBuffer(bytes);
    const pending = writeChain.then(() => writeBuffer(buf));
    writeChain = pending.catch(() => {});
    return pending;
  }

  function finish() {
    if (state !== "open") return Promise.reject(new Error("Could not encode video"));
    state = "finishing";
    const waited = writeChain.then(() => new Promise((resolve, reject) => {
      const fail = () => {
        if (state !== "cancelled") state = "failed";
        removeTmp();
        reject(new Error("Could not encode video"));
      };
      whenClosed().then((code) => {
        if (state === "cancelled") {
          removeTmp();
          reject(new Error("Could not encode video"));
          return;
        }
        if (code !== 0) {
          fail();
          return;
        }
        try {
          fs.renameSync(tmpPath, outPath);
          state = "finished";
          resolve();
        } catch (err) {
          fail();
        }
      });
      try {
        child.stdin.end();
      } catch (err) {
        try { child.kill("SIGKILL"); } catch (killErr) { /* already gone */ }
        fail();
      }
    }));
    writeChain = waited.catch(() => {});
    return waited;
  }

  let cancelPromise = null;

  function cancel() {
    if (state === "finished") return Promise.resolve();
    if (cancelPromise) return cancelPromise;
    state = "cancelled";
    cancelPromise = whenClosed().then(() => {
      removeTmp();
    });
    try {
      if (child.stdin && typeof child.stdin.destroy === "function") child.stdin.destroy();
    } catch (err) { /* closing */ }
    try {
      child.kill("SIGKILL");
    } catch (err) { /* already exited */ }
    writeChain = cancelPromise.catch(() => {});
    return cancelPromise;
  }

  return { writeFrame, finish, cancel };
}

module.exports = {
  ffmpegVideoArgs,
  startEncoder,
  videoBitrate,
};
