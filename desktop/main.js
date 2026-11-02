// Changes: native Save image dialog under Pictures/BronchoRoute; never log the path.
// Changes: one-time FileVault note when fdesetup reports FileVault is Off.
// Changes: refuse non-arm64 launch and warn once when memory is below 16 GB.
// Changes: combine native Archive IPC with API-owned disclaimer acknowledgement.
// Changes: launch bundled standalone Python in packaged mode; preserve dev launch.
// Changes: Python owns disclaimer acknowledgement through the token-guarded settings API.
// Changes: dev-mode Electron shell — sidecar lifecycle, token, disclaimer.
// Changes: native Archive IPC and File menu alongside sidecar lifecycle.
"use strict";

const { app, BrowserWindow, dialog, ipcMain, Menu, session } = require("electron");
const { execFile, spawn } = require("node:child_process");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const {
  DISCLAIMER,
  FILEVAULT_NOTE,
  SPLASH,
  fileVaultIsOff,
  atomicWriteText,
  sidecarLaunch,
  firstLocalCaseId,
  hardwareBlockReason,
  isAllowedAppUrl,
  newLaunchToken,
  parsePortLine,
  requestFilterUrls,
  resolveDataRoot,
  shouldWarnLowRam,
  sidecarOrigin,
  splitCompleteLines,
  isSaveImageName,
  isPngBytes,
  resolveSaveImageBaseDir,
  saveImageDefaultPath,
} = require("./lib");

app.setName("BronchoRoute");
// Chromium's profile is userData. Point it at the override before ready so a
// dev root does not also write into Application Support.
if (typeof process.env.BRONCHOROUTE_DATA_ROOT === "string" && process.env.BRONCHOROUTE_DATA_ROOT.trim()) {
  app.setPath("userData", path.resolve(process.env.BRONCHOROUTE_DATA_ROOT.trim()));
}

const PORT_TIMEOUT_MS = 120000;
const HEALTH_TIMEOUT_MS = 30000;
const SIDECAR_QUIT_MS = 5000;

let sidecar = null;
let mainWindow = null;
let allowedPort = null;
let shutdownStarted = false;
let saveImageBaseDir = null;

function setSaveImageBaseDir(dir) {
  saveImageBaseDir = typeof dir === "string" && dir.trim() !== "" ? dir : null;
}

function delay(ms) {
  return new Promise((resolve) => {
    setTimeout(resolve, ms);
  });
}

function escapeHtml(text) {
  return String(text)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function inlinePage(body) {
  return `<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>BronchoRoute</title>
<style>
  html, body { margin: 0; height: 100%; background: #EDF1F3; color: #17242B;
    font: 15px/1.5 -apple-system, BlinkMacSystemFont, sans-serif; }
  main { min-height: 100%; display: flex; align-items: center; justify-content: center; }
  p { max-width: 32rem; margin: 0; padding: 24px; }
</style>
</head>
<body>
<main>${body}</main>
</body>
</html>`;
}

function splashHtml() {
  return inlinePage(`<p>${escapeHtml(SPLASH)}</p>`);
}

function errorHtml(message) {
  return inlinePage(`<p>${escapeHtml(message)}</p>`);
}

function loadInline(win, html) {
  const url = `data:text/html;charset=utf-8,${encodeURIComponent(html)}`;
  return win.loadURL(url);
}

function createWindow() {
  const win = new BrowserWindow({
    width: 1440,
    height: 900,
    show: false,
    title: "BronchoRoute",
    backgroundColor: "#EDF1F3",
    webPreferences: {
      contextIsolation: true,
      sandbox: true,
      nodeIntegration: false,
      preload: path.join(__dirname, "preload.js"),
    },
  });
  // The viewer page has its own <title>; the window stays BronchoRoute.
  win.on("page-title-updated", (event) => {
    event.preventDefault();
  });
  return win;
}

function installNavigationGuards(win) {
  const block = (event, url) => {
    if (allowedPort == null || !isAllowedAppUrl(url, allowedPort)) {
      event.preventDefault();
    }
  };
  win.webContents.on("will-navigate", block);
  win.webContents.on("will-redirect", block);
  win.webContents.setWindowOpenHandler(() => {
    return { action: "deny" };
  });
}

function installTokenHeader(port, token) {
  const filter = { urls: requestFilterUrls(port) };
  session.defaultSession.webRequest.onBeforeSendHeaders(filter, (details, callback) => {
    const requestHeaders = details.requestHeaders;
    requestHeaders["X-BronchoRoute-Token"] = token;
    callback({ requestHeaders });
  });
}

function spawnSidecar(options) {
  const launch = sidecarLaunch(app.isPackaged, process.resourcesPath, options, process.env);
  const child = spawn(launch.command, launch.args, {
    cwd: launch.cwd,
    env: launch.env,
    stdio: ["pipe", "pipe", "pipe"],
  });
  child.stderr.on("data", (chunk) => {
    process.stderr.write(chunk);
  });
  return child;
}

function waitForPort(child, timeoutMs) {
  return new Promise((resolve, reject) => {
    let buffer = "";
    let settled = false;
    const timer = setTimeout(() => {
      fail(new Error("Timed out after 120s waiting for the sidecar port"));
    }, timeoutMs);

    function fail(err) {
      if (settled) {
        return;
      }
      settled = true;
      clearTimeout(timer);
      reject(err);
    }

    function succeed(port) {
      if (settled) {
        return;
      }
      settled = true;
      clearTimeout(timer);
      resolve(port);
    }

    child.once("error", () => {
      fail(new Error("Could not start the sidecar"));
    });
    child.once("exit", (code, signal) => {
      if (!settled) {
        const how = signal || String(code);
        fail(new Error(`Sidecar exited before announcing a port (${how})`));
      }
    });
    child.stdout.on("data", (chunk) => {
      if (settled) {
        process.stdout.write(chunk);
        return;
      }
      buffer += chunk.toString("utf8");
      if (buffer.length > 1000000) {
        fail(new Error("Sidecar stdout exceeded 1MB before the port line"));
        return;
      }
      const split = splitCompleteLines(buffer);
      buffer = split.rest;
      const pending = [];
      let found = null;
      for (const line of split.lines) {
        if (found == null) {
          const port = parsePortLine(line);
          if (port != null) {
            found = port;
          } else {
            pending.push(line);
          }
        } else {
          pending.push(line);
        }
      }
      for (const line of pending) {
        process.stdout.write(`${line}\n`);
      }
      if (found != null) {
        if (buffer.length > 0) {
          process.stdout.write(buffer);
          buffer = "";
        }
        succeed(found);
      }
    });
  });
}

async function waitForHealth(port, token, timeoutMs) {
  const url = `${sidecarOrigin(port)}/healthz`;
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    try {
      const response = await fetch(url, {
        headers: { "X-BronchoRoute-Token": token },
      });
      if (response.ok) {
        return;
      }
    } catch (err) {
      // The port is printed before uvicorn accepts connections.
    }
    await delay(100);
  }
  throw new Error("Sidecar did not answer /healthz");
}

async function chooseStartUrl(port, token) {
  const origin = sidecarOrigin(port);
  const response = await fetch(`${origin}/api/cases`, {
    headers: { "X-BronchoRoute-Token": token },
  });
  if (!response.ok) {
    throw new Error("Could not list Cases");
  }
  const cases = await response.json();
  const caseId = firstLocalCaseId(cases);
  if (!caseId) {
    return `${origin}/`;
  }
  return `${origin}/?case=${encodeURIComponent(caseId)}`;
}

function disclaimerInstallScript() {
  const sentence = JSON.stringify(DISCLAIMER);
  return `(() => {
    if (document.getElementById("disclaimer-overlay")) {
      return false;
    }
    const overlay = document.createElement("div");
    overlay.id = "disclaimer-overlay";
    overlay.setAttribute("role", "dialog");
    overlay.setAttribute("aria-modal", "true");
    overlay.style.cssText = "position:fixed;inset:0;z-index:10000;background:rgba(12,18,22,.45);display:flex;align-items:center;justify-content:center;";
    const panel = document.createElement("div");
    panel.style.cssText = "max-width:28rem;margin:16px;padding:20px 22px;background:#fff;color:#17242B;border-radius:10px;box-shadow:0 8px 24px rgba(23,36,43,.18);font:15px/1.5 -apple-system,BlinkMacSystemFont,sans-serif;";
    const text = document.createElement("p");
    text.id = "disclaimer-text";
    text.textContent = ${sentence};
    text.style.margin = "0 0 16px";
    const button = document.createElement("button");
    button.id = "disclaimer-ack";
    button.type = "button";
    button.textContent = "Acknowledge";
    button.style.cssText = "font:600 14px/1 -apple-system,BlinkMacSystemFont,sans-serif;padding:8px 14px;border-radius:6px;border:0;background:#2E6E88;color:#fff;cursor:pointer;";
    button.addEventListener("click", async () => {
      button.disabled = true;
      try {
        const response = await fetch("/api/settings", {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ disclaimer_ack: true }),
        });
        if (!response.ok) throw new Error("settings");
        overlay.remove();
      } catch (error) {
        button.textContent = "Could not save. Try again";
        button.disabled = false;
      }
    });
    panel.appendChild(text);
    panel.appendChild(button);
    overlay.appendChild(panel);
    overlay.setAttribute("aria-labelledby", "disclaimer-text");
    document.body.appendChild(overlay);
    button.focus();
    return true;
  })()`;
}

function readFileVaultStatus() {
  return new Promise((resolve) => {
    execFile("fdesetup", ["status"], { timeout: 5000 }, (err, stdout) => {
      if (err) {
        resolve("");
        return;
      }
      resolve(String(stdout || ""));
    });
  });
}

function fileVaultInstallScript() {
  const sentence = JSON.stringify(FILEVAULT_NOTE);
  return `(() => {
    const show = () => {
      if (document.getElementById("filevault-overlay")) return;
      const overlay = document.createElement("div");
      overlay.id = "filevault-overlay";
      overlay.setAttribute("role", "dialog");
      overlay.setAttribute("aria-modal", "true");
      overlay.style.cssText = "position:fixed;inset:0;z-index:10001;background:rgba(12,18,22,.45);display:flex;align-items:center;justify-content:center;";
      const panel = document.createElement("div");
      panel.style.cssText = "max-width:28rem;margin:16px;padding:20px 22px;background:#fff;color:#17242B;border-radius:10px;box-shadow:0 8px 24px rgba(23,36,43,.18);font:15px/1.5 -apple-system,BlinkMacSystemFont,sans-serif;";
      const text = document.createElement("p");
      text.id = "filevault-text";
      text.textContent = ${sentence};
      text.style.margin = "0 0 16px";
      const button = document.createElement("button");
      button.id = "filevault-ack";
      button.type = "button";
      button.textContent = "OK";
      button.style.cssText = "font:600 14px/1 -apple-system,BlinkMacSystemFont,sans-serif;padding:8px 14px;border-radius:6px;border:0;background:#2E6E88;color:#fff;cursor:pointer;";
      button.addEventListener("click", async () => {
        button.disabled = true;
        try {
          const response = await fetch("/api/settings", {
            method: "PUT",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ filevault_note_shown: true }),
          });
          if (!response.ok) throw new Error("settings");
          overlay.remove();
        } catch (error) {
          button.textContent = "Could not save. Try again";
          button.disabled = false;
        }
      });
      panel.appendChild(text);
      panel.appendChild(button);
      overlay.appendChild(panel);
      overlay.setAttribute("aria-labelledby", "filevault-text");
      document.body.appendChild(overlay);
      button.focus();
    };
    // Wait until the disclaimer click is done so this note does not steal it.
    if (document.getElementById("disclaimer-overlay")) {
      const observer = new MutationObserver(() => {
        if (!document.getElementById("disclaimer-overlay")) {
          observer.disconnect();
          show();
        }
      });
      observer.observe(document.body, { childList: true, subtree: true });
      return;
    }
    show();
  })()`;
}

async function showFileVaultNote(win, port, token) {
  const status = await readFileVaultStatus();
  if (!fileVaultIsOff(status)) return;
  const response = await fetch(`${sidecarOrigin(port)}/api/settings`, {
    headers: { "X-BronchoRoute-Token": token },
  });
  if (!response.ok) throw new Error("Could not load Settings");
  const settings = await response.json();
  if (settings.filevault_note_shown) return;
  await win.webContents.executeJavaScript(fileVaultInstallScript());
}

async function maybeWarnLowRam(port, token) {
  const response = await fetch(`${sidecarOrigin(port)}/api/settings`, {
    headers: { "X-BronchoRoute-Token": token },
  });
  if (!response.ok) throw new Error("Could not load Settings");
  const settings = await response.json();
  if (!shouldWarnLowRam(os.totalmem(), settings.ram_warning_shown)) {
    return;
  }
  const message = "This Mac has less than 16 GB of memory.";
  const detail = "BronchoRoute is built for Apple Silicon Macs with at least 16 GB of memory. Segmentation can be slow or run out of memory on this Mac.";
  if (process.env.BRONCHOROUTE_SMOKE === "1") {
    process.stderr.write(`${message} ${detail}\n`);
  } else {
    await dialog.showMessageBox(mainWindow, {
      type: "warning",
      buttons: ["OK"],
      defaultId: 0,
      title: "BronchoRoute",
      message,
      detail,
    });
  }
  const saved = await fetch(`${sidecarOrigin(port)}/api/settings`, {
    method: "PUT",
    headers: {
      "Content-Type": "application/json",
      "X-BronchoRoute-Token": token,
    },
    body: JSON.stringify({ ram_warning_shown: true }),
  });
  if (!saved.ok) throw new Error("Could not save the memory warning");
}

async function showDisclaimer(win, port, token) {
  const response = await fetch(`${sidecarOrigin(port)}/api/settings`, {
    headers: { "X-BronchoRoute-Token": token },
  });
  if (!response.ok) throw new Error("Could not load Settings");
  const settings = await response.json();
  if (!settings.disclaimer_ack) {
    await win.webContents.executeJavaScript(disclaimerInstallScript());
  }
}

async function showFatal(message) {
  const text = message || "BronchoRoute failed to start";
  process.stderr.write(`${text}\n`);
  if (process.env.BRONCHOROUTE_SMOKE === "1") {
    process.stdout.write(`BRONCHOROUTE_SMOKE_FAIL ${text}\n`);
  }
  if (sidecar && sidecar.exitCode === null && sidecar.signalCode === null) {
    if (sidecar.stdin && !sidecar.stdin.destroyed) {
      sidecar.stdin.end();
    }
  }
  if (mainWindow && !mainWindow.isDestroyed()) {
    await loadInline(mainWindow, errorHtml(text)).catch(() => {});
    if (!mainWindow.isVisible()) {
      mainWindow.show();
    }
  }
  if (process.env.BRONCHOROUTE_SMOKE !== "1") {
    dialog.showErrorBox("BronchoRoute", text);
  }
  app.exit(1);
}

async function openArchive(event, selectedPath) {
  if (event.sender !== mainWindow.webContents) {
    throw new Error("Archive request from an unknown window");
  }
  let archivePath = selectedPath;
  if (!archivePath) {
    const result = await dialog.showOpenDialog(mainWindow, {
      title: "Open Archive",
      properties: ["openFile", "openDirectory"],
      filters: [{ name: "Archives", extensions: ["zip", "dcm", ""] }],
    });
    if (result.canceled) {
      return;
    }
    archivePath = result.filePaths[0];
  }
  if (typeof archivePath !== "string") {
    throw new Error("Invalid Archive selection");
  }
  mainWindow.webContents.send("bronchoroute:archive", archivePath);
}

async function saveImage(event, payload) {
  if (!mainWindow || event.sender !== mainWindow.webContents) {
    throw new Error("Save image request from an unknown window");
  }
  const defaultName = payload && payload.defaultName;
  const bytes = payload && payload.bytes;
  if (!isSaveImageName(defaultName) || !isPngBytes(bytes)) {
    throw new Error("Invalid save image");
  }
  let defaultPath;
  try {
    const base = resolveSaveImageBaseDir(saveImageBaseDir, app.getPath("pictures"));
    defaultPath = saveImageDefaultPath(base, defaultName);
    fs.mkdirSync(path.dirname(defaultPath), { recursive: true });
  } catch (err) {
    throw new Error("Could not prepare the save folder");
  }
  const result = await dialog.showSaveDialog(mainWindow, {
    title: "Save image",
    defaultPath,
    filters: [{ name: "PNG", extensions: ["png"] }],
  });
  if (result.canceled || typeof result.filePath !== "string" || result.filePath === "") {
    return { saved: false };
  }
  try {
    fs.mkdirSync(path.dirname(result.filePath), { recursive: true });
    const body = Buffer.isBuffer(bytes) || ArrayBuffer.isView(bytes)
      ? bytes
      : Buffer.from(bytes);
    fs.writeFileSync(result.filePath, body);
  } catch (err) {
    throw new Error("Could not save image");
  }
  return { saved: true };
}

function installSaveImage() {
  ipcMain.handle("bronchoroute:save-image", (event, payload) => saveImage(event, payload));
}

function installArchiveMenu() {
  ipcMain.handle("bronchoroute:open-archive", event => openArchive(event));
  Menu.setApplicationMenu(Menu.buildFromTemplate([
    { role: "appMenu" },
    { label: "File", submenu: [
      { label: "Open Archive…", accelerator: "CmdOrCtrl+O", click: () => {
        openArchive({ sender: mainWindow.webContents }).catch(() => {});
      } },
      { role: "close" },
    ] },
    { role: "editMenu" },
  ]));
}

async function launch() {
  await app.whenReady();
  const dataRoot = resolveDataRoot(process.env, app.getPath("userData"));
  const tmpDir = path.join(dataRoot, "tmp");
  const logDir = path.join(dataRoot, "logs");
  fs.mkdirSync(tmpDir, { recursive: true });
  fs.mkdirSync(logDir, { recursive: true });
  const token = newLaunchToken();
  mainWindow = createWindow();
  installArchiveMenu();
  installSaveImage();
  installNavigationGuards(mainWindow);
  await loadInline(mainWindow, splashHtml());
  mainWindow.show();
  sidecar = spawnSidecar({
    token,
    dataRoot,
    tmpDir,
    logDir,
  });
  atomicWriteText(path.join(dataRoot, "sidecar.pid"), `${sidecar.pid}\n`);
  const port = await waitForPort(sidecar, PORT_TIMEOUT_MS);
  allowedPort = port;
  installTokenHeader(port, token);
  await waitForHealth(port, token, HEALTH_TIMEOUT_MS);
  await maybeWarnLowRam(port, token);
  const url = await chooseStartUrl(port, token);
  await mainWindow.loadURL(url);
  await showDisclaimer(mainWindow, port, token);
  await showFileVaultNote(mainWindow, port, token);
  return {
    window: mainWindow,
    port,
    sidecarPid: sidecar.pid,
  };
}

async function start() {
  const blocked = hardwareBlockReason(process.arch);
  if (blocked) {
    process.stderr.write(`${blocked}\n`);
    dialog.showErrorBox("BronchoRoute", blocked);
    app.exit(1);
    return null;
  }
  try {
    return await launch();
  } catch (err) {
    const message = err && err.message ? err.message : "BronchoRoute failed to start";
    await showFatal(message);
    return null;
  }
}

app.on("window-all-closed", () => {
  app.quit();
});

app.on("before-quit", (event) => {
  if (shutdownStarted) {
    return;
  }
  if (!sidecar || sidecar.exitCode !== null || sidecar.signalCode !== null) {
    return;
  }
  event.preventDefault();
  shutdownStarted = true;
  const child = sidecar;
  const timer = setTimeout(() => {
    if (child.exitCode === null && child.signalCode === null) {
      child.kill("SIGTERM");
    }
  }, SIDECAR_QUIT_MS);
  child.once("exit", () => {
    clearTimeout(timer);
    app.quit();
  });
  if (child.stdin && !child.stdin.destroyed) {
    child.stdin.end();
  } else {
    child.kill("SIGTERM");
  }
});

// `electron .` passes the app directory in argv, so this file is not require.main.
function isDirectLaunch() {
  if (require.main === module) {
    return true;
  }
  const arg = process.argv[1];
  if (!arg) {
    return false;
  }
  const resolved = path.resolve(arg);
  return resolved === __dirname || resolved === __filename;
}

if (isDirectLaunch()) {
  start().catch((err) => {
    const message = err && err.message ? err.message : "BronchoRoute failed to start";
    process.stderr.write(`${message}\n`);
    app.exit(1);
  });
}

module.exports = { start, openArchive, saveImage, setSaveImageBaseDir };
