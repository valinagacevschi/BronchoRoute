// Changes: packaged smoke no longer clicks a disclaimer Acknowledge button.
// Changes: encode a short packaged-ffmpeg clip (h264 / 30 fps) before the viewer smoke.
// Changes: check packaged viewer lifecycle and TotalSegmentator module without PATH scripts.
"use strict";

const assert = require("node:assert/strict");
const { spawn, spawnSync } = require("node:child_process");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { resolveFfmpeg } = require("../lib");
const { startEncoder } = require("../encoder");

const desktop = path.resolve(__dirname, "..");
const pipeline = path.resolve(desktop, "..", "pipeline");
const bundle = path.join(desktop, "dist", "mac-arm64", "BronchoRoute.app", "Contents");
const delay = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

// Valid 64×64 JPEG (no lavfi in the LGPL build). scale/format in encoder.js sizes it.
const SYNTHETIC_JPEG = Buffer.from(
  "/9j/4AAQSkZJRgABAgAAAQABAAD//gAPTGF2YzYzLjEuMTAxAP/bAEMACAoKCwoLDQ0N"
  + "DQ0NEA8QEBAQEBAQEBAQEBISEhUVFRISEhAQEhIUFBUVFxcXFRUVFRcXGRkZHh4cHCMj"
  + "JCsrM//EAEsAAQEAAAAAAAAAAAAAAAAAAAAIAQEAAAAAAAAAAAAAAAAAAAAAEAEAAAAA"
  + "AAAAAAAAAAAAAAAAEQEAAAAAAAAAAAAAAAAAAAAA/8AAEQgAQABAAwEiAAIRAAMRAP/a"
  + "AAwDAQACEQMRAD8An8AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAH//2Q==",
  "base64",
);

async function encodePackagedClip(resourcesPath) {
  const ffmpegPath = resolveFfmpeg({ packaged: true, resourcesPath });
  assert.equal(SYNTHETIC_JPEG[0], 0xff);
  assert.equal(SYNTHETIC_JPEG[1], 0xd8);
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "bronchoroute-52-ffmpeg-"));
  const outPath = path.join(dir, "clip.mp4");
  try {
    const enc = startEncoder({
      ffmpegPath,
      outPath,
      width: 1280,
      height: 720,
      fps: 30,
    });
    for (let i = 0; i < 30; i += 1) {
      await enc.writeFrame(SYNTHETIC_JPEG);
    }
    await enc.finish();
    const probe = spawnSync(ffmpegPath, ["-hide_banner", "-i", outPath], {
      encoding: "utf8",
      stdio: ["ignore", "ignore", "pipe"],
    });
    const info = String(probe.stderr || "");
    assert.match(info, /Video:\s*h264/i, "packaged encode is h264");
    assert.match(info, /30\s*fps|30\s*tbr/i, "packaged encode is 30 fps");
    process.stdout.write("PASS: packaged ffmpeg encoded 30 JPEG frames to h264@30fps\n");
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
}

function snapshot(root) {
  const result = new Map();
  function walk(dir) {
    if (!fs.existsSync(dir)) {
      return;
    }
    for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
      const file = path.join(dir, entry.name);
      if (entry.isDirectory()) {
        walk(file);
      } else if (entry.isFile()) {
        const stat = fs.statSync(file, { bigint: true });
        result.set(file, String(stat.mtimeNs) + ":" + String(stat.size));
      }
    }
  }
  walk(root);
  return result;
}

function linkInputs(source, dest) {
  fs.mkdirSync(dest, { recursive: true });
  if (!fs.existsSync(source)) {
    return;
  }
  for (const entry of fs.readdirSync(source, { withFileTypes: true })) {
    const src = path.join(source, entry.name);
    const dst = path.join(dest, entry.name);
    if (entry.isDirectory()) {
      linkInputs(src, dst);
    } else if (entry.isFile() && entry.name !== "job.json" && !entry.name.endsWith(".lock")) {
      fs.symlinkSync(src, dst);
    }
  }
}

function alive(pid) {
  try {
    process.kill(pid, 0);
    return true;
  } catch {
    return false;
  }
}

async function until(check, timeout = 180000) {
  const deadline = Date.now() + timeout;
  while (Date.now() < deadline) {
    const result = await check();
    if (result) {
      return result;
    }
    await delay(200);
  }
  throw new Error("Packaged smoke timed out");
}

async function main() {
  const standIn = process.env.BRONCHOROUTE_SMOKE_RESOURCES;
  if (typeof standIn === "string" && standIn.trim() !== "") {
    await encodePackagedClip(path.resolve(standIn.trim()));
    return;
  }
  await encodePackagedClip(path.join(bundle, "Resources"));
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "bronchoroute-27-data-"));
  const trees = ["data", "ts_out", "out", "app_state"];
  const before = trees.map((name) => snapshot(path.join(pipeline, name)));
  for (const name of trees.slice(0, 3)) {
    linkInputs(path.join(pipeline, name), path.join(root, name));
  }
  const env = { ...process.env, PATH: "/usr/bin:/bin", BRONCHOROUTE_DATA_ROOT: root, BRONCHOROUTE_SMOKE: "1" };
  delete env.PYTHONPATH;
  delete env.ELECTRON_RUN_AS_NODE;
  const help = spawnSync(
    path.join(bundle, "Resources", "python", "bin", "python3"),
    ["-m", "totalsegmentator.bin.TotalSegmentator", "--help"],
    {
      cwd: path.join(bundle, "Resources", "app", "pipeline"),
      env: {
        PATH: "/usr/bin:/bin",
        HOME: process.env.HOME,
        PYTHONDONTWRITEBYTECODE: "1",
        PYTHONNOUSERSITE: "1",
        TOTALSEG_HOME_DIR: path.join(root, "models", "totalseg"),
        TOTALSEG_WEIGHTS_PATH: path.join(root, "models"),
      },
      timeout: 120000,
      stdio: "ignore",
    },
  );
  if (help.status !== 0) {
    fs.rmSync(root, { recursive: true, force: true });
    throw new Error("Bundled TotalSegmentator module help failed");
  }
  const child = spawn(path.join(bundle, "MacOS", "BronchoRoute"), ["--remote-debugging-port=0"], {
    env,
    stdio: ["ignore", "ignore", "ignore"],
  });
  let sidecarPid;
  let socket;
  try {
    child.on("error", () => {});
    const active = path.join(root, "DevToolsActivePort");
    const port = await until(() => fs.existsSync(active) && Number(fs.readFileSync(active, "utf8").split("\n")[0]));
    const page = await until(async () => {
      const pages = await (await fetch("http://127.0.0.1:" + port + "/json")).json();
      return pages.find((row) => row.type === "page" && row.url.startsWith("http://127.0.0.1:"));
    });
    socket = new WebSocket(page.webSocketDebuggerUrl);
    await new Promise((resolve, reject) => {
      socket.addEventListener("open", resolve, { once: true });
      socket.addEventListener("error", reject, { once: true });
    });
    let nextId = 0;
    const pending = new Map();
    socket.addEventListener("message", (event) => {
      const message = JSON.parse(event.data);
      const callback = pending.get(message.id);
      if (callback) {
        pending.delete(message.id);
        callback(message);
      }
    });
    function cdp(method, params = {}) {
      return new Promise((resolve, reject) => {
        const id = ++nextId;
        pending.set(id, (message) => {
          if (message.error) {
            reject(new Error("Browser command failed"));
          } else {
            resolve(message.result);
          }
        });
        socket.send(JSON.stringify({ id, method, params }));
      });
    }
    await until(async () => {
      const result = await cdp("Runtime.evaluate", { expression: `(() => {
        if (document.getElementById("disclaimer-overlay")) return false;
        const footer = document.querySelector(".research-footer");
        if (!footer || !footer.textContent.includes("Not a medical device")) return false;
        return Boolean(new URL(location.href).searchParams.get("case"))
          && document.querySelectorAll("#switcher button").length > 0
          && Array.from(document.querySelectorAll("canvas")).some((canvas) => {
            if (canvas.width < 8 || canvas.height < 8 || !canvas.checkVisibility()) return false;
            const ctx = canvas.getContext("2d");
            if (!ctx) return false;
            const pixels = ctx.getImageData(0, 0, canvas.width, canvas.height).data;
            return pixels.some((value, index) => index % 4 !== 3 && value > 24);
          });
      })()`, returnByValue: true });
      return result.result && result.result.value === true;
    });
    sidecarPid = Number(fs.readFileSync(path.join(root, "sidecar.pid"), "utf8"));
    assert.ok(Number.isInteger(sidecarPid) && sidecarPid > 0, "valid sidecar PID");
    const ps = spawnSync("/bin/ps", ["-p", String(sidecarPid), "-o", "command="], { encoding: "utf8" });
    assert.equal(ps.status, 0);
    assert.ok(ps.stdout.trim().startsWith(path.join(bundle, "Resources", "python", "bin", "python3") + " "), "sidecar runs bundled Python");
    const screenshot = await cdp("Page.captureScreenshot", { format: "png" });
    const shot = path.join(os.tmpdir(), "bronchoroute-27", "packaged.png");
    fs.mkdirSync(path.dirname(shot), { recursive: true });
    fs.writeFileSync(shot, Buffer.from(screenshot.data, "base64"));
    socket.close();
    child.kill("SIGKILL");
    await until(() => !alive(sidecarPid), 15000);
    trees.forEach((name, index) => {
      assert.deepEqual(snapshot(path.join(pipeline, name)), before[index], "source tree unchanged");
    });
    process.stdout.write("PASS: bundled Python, TotalSegmentator module help, rendered Case, SIGKILL cleanup, source guard\n");
    process.stdout.write(shot + "\n");
  } finally {
    if (socket) {
      socket.close();
    }
    child.kill("SIGKILL");
    if (sidecarPid && alive(sidecarPid)) {
      process.kill(sidecarPid, "SIGTERM");
    }
    fs.rmSync(root, { recursive: true, force: true });
  }
}

main().catch(() => {
  // Do not expose browser/server errors that could contain case metadata.
  process.stderr.write("FAIL: packaged smoke assertion or timeout\n");
  process.exitCode = 1;
});
