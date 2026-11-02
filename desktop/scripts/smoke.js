// Changes: first-launch acknowledgement and IPC Archive import persist across relaunch.
// Changes: synthetic Case smoke verifies click acknowledgement and persistence after relaunch.
// Changes: synthetic Archive via native IPC → picker → processing → Case; verify shutdown isolation.
// Changes: Electron smoke — render a Case, screenshot, SIGKILL leaves no sidecar.
"use strict";

const { spawn, spawnSync } = require("node:child_process");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");

const inElectron = Boolean(process.versions && process.versions.electron);

function delay(ms) {
  return new Promise((resolve) => {
    setTimeout(resolve, ms);
  });
}

function alive(pid) {
  try {
    process.kill(pid, 0);
    return true;
  } catch (err) {
    return err.code === "EPERM";
  }
}

function snapshotDir(dir) {
  const out = new Map();
  function walk(current) {
    let entries;
    try {
      entries = fs.readdirSync(current, { withFileTypes: true });
    } catch (err) {
      return;
    }
    for (const ent of entries) {
      const full = path.join(current, ent.name);
      if (ent.isSymbolicLink()) {
        continue;
      }
      if (ent.isDirectory()) {
        walk(full);
      } else if (ent.isFile()) {
        const st = fs.statSync(full);
        out.set(full, `${st.mtimeMs}:${st.size}`);
      }
    }
  }
  if (fs.existsSync(dir)) {
    walk(dir);
  }
  return out;
}

function sameSnapshot(before, after) {
  if (before.size !== after.size) {
    return false;
  }
  for (const [key, value] of before) {
    if (after.get(key) !== value) {
      return false;
    }
  }
  return true;
}

function createArchive(dataRoot, pipeline) {
  const env = { ...process.env };
  delete env.PYTHONPATH;
  const result = spawnSync(
    "uv", ["run", "python", "tracer_fixture.py", path.join(dataRoot, "input.zip")],
    { cwd: pipeline, env, encoding: "utf8" },
  );
  if (result.status !== 0) {
    throw new Error("synthetic Archive creation failed");
  }
}

async function clickElement(wc, selector) {
  const point = await wc.executeJavaScript(`(() => {
    const element = document.querySelector(${JSON.stringify(selector)});
    if (!element || element.disabled) return null;
    const rect = element.getBoundingClientRect();
    if (!rect.width || !rect.height) return null;
    return { x: Math.round(rect.x + rect.width / 2), y: Math.round(rect.y + rect.height / 2) };
  })()`);
  if (!point) {
    throw new Error("smoke control is not available");
  }
  wc.sendInputEvent({ type: "mouseDown", button: "left", clickCount: 1, ...point });
  wc.sendInputEvent({ type: "mouseUp", button: "left", clickCount: 1, ...point });
}

async function childMain() {
  const { start, openArchive } = require("../main");
  const launched = await start();
  if (!launched || !launched.window) {
    return;
  }
  const wc = launched.window.webContents;
  const relaunch = process.env.BRONCHOROUTE_SMOKE_RELAUNCH === "1";
  const ack = await wc.executeJavaScript(`(() => {
    const button = document.getElementById("disclaimer-ack");
    if (!button) return null;
    const rect = button.getBoundingClientRect();
    return { x: Math.round(rect.x + rect.width / 2), y: Math.round(rect.y + rect.height / 2) };
  })()`);
  if (Boolean(ack) === relaunch) {
    throw new Error(relaunch ? "disclaimer reappeared after relaunch" : "fresh launch has no disclaimer");
  }
  if (ack) {
    wc.sendInputEvent({ type: "mouseDown", button: "left", clickCount: 1, ...ack });
    wc.sendInputEvent({ type: "mouseUp", button: "left", clickCount: 1, ...ack });
  }
  const deadline = Date.now() + 180000;
  let ready = false;
  let archiveOpened = false;
  let pickerConfirmed = false;
  let processingSeen = false;
  let readyAt = 0;
  while (Date.now() < deadline) {
    let state = null;
    try {
      if (!relaunch && !archiveOpened) {
        const acknowledged = await wc.executeJavaScript("!document.getElementById('disclaimer-overlay')");
        if (acknowledged) {
          await openArchive({ sender: wc }, path.join(process.env.BRONCHOROUTE_DATA_ROOT, "input.zip"));
          archiveOpened = true;
        }
      }
      if (!relaunch && !pickerConfirmed) {
        const pickerReady = await wc.executeJavaScript(`(() => {
          const picker = document.getElementById("series-picker");
          return Boolean(picker && !picker.hidden);
        })()`);
        if (pickerReady) {
          await clickElement(wc, "#clinician-initials");
          await wc.insertText("AB");
          await clickElement(wc, "#series-confirm");
          pickerConfirmed = true;
        }
      }
      state = await wc.executeJavaScript(`(async () => {
        const canvases = Array.from(document.querySelectorAll("canvas"));
        let painted = false;
        for (const canvas of canvases) {
          let shown = true;
          for (let node = canvas; node && node.nodeType === 1; node = node.parentElement) {
            const style = getComputedStyle(node);
            if (style.display === "none" || style.visibility === "hidden") {
              shown = false;
              break;
            }
          }
          if (!shown || canvas.width < 8 || canvas.height < 8) {
            continue;
          }
          const ctx = canvas.getContext("2d");
          if (!ctx) {
            continue;
          }
          let data;
          try {
            data = ctx.getImageData(0, 0, Math.min(canvas.width, 48), Math.min(canvas.height, 48)).data;
          } catch (err) {
            continue;
          }
          for (let i = 0; i < data.length; i += 16) {
            if (data[i] + data[i + 1] + data[i + 2] > 24) {
              painted = true;
              break;
            }
          }
          if (painted) {
            break;
          }
        }
        return {
          buttons: document.querySelectorAll("#switcher button").length,
          overlay: Boolean(document.getElementById("disclaimer-overlay")),
          painted,
          imported: new URLSearchParams(location.search).get("case") === "local-001",
          processing: Boolean(document.getElementById("processing") && !document.getElementById("processing").hidden),
          caseListed: (await fetch("/api/cases").then(response => response.json())).some(item => item.id === "local-001" && item.has_graph),
          ackSaved: (await fetch("/api/settings").then(response => response.json())).disclaimer_ack === true,
        };
      })()`);
    } catch (err) {
      await delay(200);
      continue;
    }
    processingSeen = processingSeen || state.processing;
    if (state.imported && state.caseListed && state.buttons > 0 && !state.overlay && state.ackSaved) {
      if (!readyAt) {
        readyAt = Date.now();
      }
      // CT slices arrive after the switcher. Don't snapshot an empty pane.
      if (state.painted || Date.now() - readyAt > 20000) {
        ready = true;
        break;
      }
    }
    await delay(200);
  }
  if (!ready) {
    process.stdout.write("BRONCHOROUTE_SMOKE_FAIL viewer did not render\n");
    require("electron").app.exit(1);
    return;
  }
  if (!relaunch && (!pickerConfirmed || !processingSeen)) {
    throw new Error("Archive import did not show picker and processing");
  }
  await wc.executeJavaScript(
    "new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(() => resolve(true))))",
  );
  const png = (await wc.capturePage()).toPNG();
  if (png.length < 1000) {
    process.stdout.write("BRONCHOROUTE_SMOKE_FAIL screenshot was empty\n");
    require("electron").app.exit(1);
    return;
  }
  const shot = path.join(os.tmpdir(), "bronchoroute-28", relaunch ? "relaunch.png" : "viewer.png");
  fs.mkdirSync(path.dirname(shot), { recursive: true });
  fs.writeFileSync(shot, png);
  process.stdout.write(`BRONCHOROUTE_SMOKE_READY ${shot}\n`);
  await new Promise(() => {});
}

function killPid(pid) {
  try {
    process.kill(pid, "SIGKILL");
  } catch (err) {
    // already gone
  }
}

async function parentMain() {
  const electronBinary = require("electron");
  const repo = path.resolve(__dirname, "..", "..");
  const pipeline = path.join(repo, "pipeline");
  const dataRoot = fs.mkdtempSync(path.join(os.tmpdir(), "bronchoroute-28-data-"));
  const trees = ["data", "ts_out", "out", "app_state"].map((name) => path.join(pipeline, name));
  const before = new Map();
  for (const tree of trees) {
    for (const [key, value] of snapshotDir(tree)) {
      before.set(key, value);
    }
  }
  createArchive(dataRoot, pipeline);
  for (const relaunch of [false, true]) {
    const child = spawn(electronBinary, [path.join(__dirname, "smoke.js")], {
      cwd: path.resolve(__dirname, ".."),
      env: {
        ...process.env,
        BRONCHOROUTE_DATA_ROOT: dataRoot,
        BRONCHOROUTE_SMOKE: "1",
        BRONCHOROUTE_FAKE_SEGMENT: "1",
        BRONCHOROUTE_SMOKE_RELAUNCH: relaunch ? "1" : "0",
      },
      stdio: ["ignore", "pipe", "pipe"],
    });
    let buffer = "";
    let shot = null;
    let failed = null;
    const done = new Promise((resolve) => {
      child.stdout.on("data", (chunk) => {
        buffer += chunk.toString("utf8");
        const ready = buffer.match(/BRONCHOROUTE_SMOKE_READY (.+)\n/);
        const fail = buffer.match(/BRONCHOROUTE_SMOKE_FAIL (.+)\n/);
        if (ready) {
          shot = ready[1].trim();
          resolve();
        } else if (fail) {
          failed = fail[1].trim();
          resolve();
        }
      });
      // Sidecar logs are intentionally not echoed by this test.
      child.stderr.resume();
      child.once("exit", resolve);
      child.once("error", resolve);
    });
    const timer = setTimeout(() => killPid(child.pid), 240000);
    try {
      await done;
      if (!shot) {
        throw new Error(failed || "smoke failed");
      }
      const sidecarPid = Number(fs.readFileSync(path.join(dataRoot, "sidecar.pid"), "utf8").trim());
      if (!Number.isInteger(sidecarPid) || sidecarPid <= 0) {
        throw new Error("sidecar pid file is invalid");
      }
      killPid(child.pid);
      const deadline = Date.now() + 10000;
      while (alive(sidecarPid) && Date.now() < deadline) {
        await delay(200);
      }
      if (alive(sidecarPid)) {
        throw new Error("sidecar still running after SIGKILL");
      }
      process.stdout.write(`${relaunch ? "relaunch" : "fresh launch"}: passed\n`);
      process.stdout.write(`${shot}\n`);
    } finally {
      clearTimeout(timer);
      killPid(child.pid);
    }
  }
  const after = new Map();
  for (const tree of trees) {
    for (const [key, value] of snapshotDir(tree)) {
      after.set(key, value);
    }
  }
  if (!sameSnapshot(before, after)) {
    throw new Error("real pipeline tree changed during smoke");
  }
}

if (inElectron) {
  childMain().catch((err) => {
    const message = err && err.message ? err.message : "smoke failed";
    process.stdout.write(`BRONCHOROUTE_SMOKE_FAIL ${message}\n`);
    require("electron").app.exit(1);
  });
} else {
  parentMain().catch((err) => {
    process.stderr.write(`${err && err.message ? err.message : "smoke failed"}\n`);
    process.exit(1);
  });
}
