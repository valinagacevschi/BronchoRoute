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

function sleepSync(ms) {
  Atomics.wait(new Int32Array(new SharedArrayBuffer(4)), 0, 0, ms);
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

function linkInputTrees(dataRoot, pipeline) {
  const env = { ...process.env };
  delete env.PYTHONPATH;
  const code = [
    "import sys",
    "from pathlib import Path",
    "from conftest import symlink_input_tree",
    "dst = Path(sys.argv[1])",
    "src = Path(sys.argv[2])",
    "for name in ('data', 'ts_out', 'out'):",
    "    symlink_input_tree(src / name, dst / name)",
  ].join("\n");
  const result = spawnSync(
    "uv",
    ["run", "python", "-c", code, dataRoot, pipeline],
    { cwd: pipeline, env, encoding: "utf8" },
  );
  if (result.status !== 0) {
    throw new Error("symlink_input_tree failed");
  }
}

function ackSaved(file) {
  try {
    const data = JSON.parse(fs.readFileSync(file, "utf8"));
    return Boolean(data && data.disclaimer_ack === true);
  } catch (err) {
    return false;
  }
}

async function childMain() {
  const { start } = require("../main");
  const launched = await start();
  if (!launched || !launched.window) {
    return;
  }
  const wc = launched.window.webContents;
  const settingsPath = path.join(process.env.BRONCHOROUTE_DATA_ROOT, "settings.json");
  const deadline = Date.now() + 180000;
  let ready = false;
  let readyAt = 0;
  while (Date.now() < deadline) {
    let state = null;
    try {
      state = await wc.executeJavaScript(`(() => {
        const button = document.getElementById("disclaimer-ack");
        if (button) {
          button.click();
        }
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
        };
      })()`);
    } catch (err) {
      await delay(200);
      continue;
    }
    if (state.buttons > 0 && !state.overlay && ackSaved(settingsPath)) {
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
  await wc.executeJavaScript(
    "new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(() => resolve(true))))",
  );
  const png = (await wc.capturePage()).toPNG();
  if (png.length < 1000) {
    process.stdout.write("BRONCHOROUTE_SMOKE_FAIL screenshot was empty\n");
    require("electron").app.exit(1);
    return;
  }
  const shot = path.join(os.tmpdir(), "bronchoroute-26", "viewer.png");
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

function parentMain() {
  const electronBinary = require("electron");
  const repo = path.resolve(__dirname, "..", "..");
  const pipeline = path.join(repo, "pipeline");
  const dataRoot = fs.mkdtempSync(path.join(os.tmpdir(), "bronchoroute-26-data-"));
  const trees = ["data", "ts_out", "out"].map((name) => path.join(pipeline, name));
  const before = new Map();
  for (const tree of trees) {
    for (const [key, value] of snapshotDir(tree)) {
      before.set(key, value);
    }
  }
  linkInputTrees(dataRoot, pipeline);
  const child = spawn(electronBinary, [path.join(__dirname, "smoke.js")], {
    cwd: path.resolve(__dirname, ".."),
    env: {
      ...process.env,
      BRONCHOROUTE_DATA_ROOT: dataRoot,
      BRONCHOROUTE_SMOKE: "1",
    },
    stdio: ["ignore", "pipe", "pipe"],
  });
  let buffer = "";
  let stderr = "";
  let shot = null;
  let failed = null;
  const done = new Promise((resolve) => {
    child.stdout.on("data", (chunk) => {
      buffer += chunk.toString("utf8");
      const ready = buffer.match(/BRONCHOROUTE_SMOKE_READY (.+)\n/);
      if (ready) {
        shot = ready[1].trim();
        resolve();
      }
      const fail = buffer.match(/BRONCHOROUTE_SMOKE_FAIL (.+)\n/);
      if (fail && !shot) {
        failed = fail[1].trim();
        resolve();
      }
    });
    child.stderr.on("data", (chunk) => {
      stderr += chunk.toString("utf8");
      if (stderr.length > 8000) {
        stderr = stderr.slice(-8000);
      }
    });
    child.once("exit", () => {
      resolve();
    });
  });
  const timer = setTimeout(() => {
    killPid(child.pid);
  }, 240000);
  done.then(() => {
    clearTimeout(timer);
    if (!shot) {
      process.stderr.write(`${failed || "smoke failed"}\n`);
      if (stderr.trim()) {
        process.stderr.write(`${stderr.slice(-2000)}\n`);
      }
      killPid(child.pid);
      process.exit(1);
    }
    const pidText = fs.readFileSync(path.join(dataRoot, "sidecar.pid"), "utf8").trim();
    const sidecarPid = Number(pidText);
    if (!Number.isInteger(sidecarPid) || sidecarPid <= 0) {
      process.stderr.write("sidecar pid file is invalid\n");
      killPid(child.pid);
      process.exit(1);
    }
    killPid(child.pid);
    const deadline = Date.now() + 10000;
    let gone = false;
    while (Date.now() < deadline) {
      if (!alive(sidecarPid)) {
        gone = true;
        break;
      }
      sleepSync(200);
    }
    if (!gone) {
      process.stderr.write("sidecar still running after SIGKILL\n");
      process.exit(1);
    }
    const after = new Map();
    for (const tree of trees) {
      for (const [key, value] of snapshotDir(tree)) {
        after.set(key, value);
      }
    }
    if (!sameSnapshot(before, after)) {
      process.stderr.write("real pipeline tree changed during smoke\n");
      process.exit(1);
    }
    process.stdout.write(`${shot}\n`);
  }).catch((err) => {
    killPid(child.pid);
    process.stderr.write(`${err && err.message ? err.message : "smoke failed"}\n`);
    process.exit(1);
  });
}

if (inElectron) {
  childMain().catch((err) => {
    const message = err && err.message ? err.message : "smoke failed";
    process.stdout.write(`BRONCHOROUTE_SMOKE_FAIL ${message}\n`);
    require("electron").app.exit(1);
  });
} else {
  try {
    parentMain();
  } catch (err) {
    process.stderr.write(`${err && err.message ? err.message : "smoke failed"}\n`);
    process.exit(1);
  }
}
