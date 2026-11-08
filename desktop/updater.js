// Changes: wrap electron-updater; ask before download/install; read updates_enabled; 2 GiB guard.
"use strict";

const fs = require("node:fs");
const path = require("node:path");

const MAX_RELEASE_ARTIFACT_BYTES = 2 * 1024 * 1024 * 1024;
const GITHUB_PUBLISH = Object.freeze({
  provider: "github",
  owner: "valinagacevschi",
  repo: "BronchoRoute",
});
const RELEASE_NOTES_MAX = 800;

function stripPaths(text) {
  return String(text).replace(/(?:\/|\b[A-Za-z]:\\)[^\s"'`]+/g, "[path]");
}

function createQuietLogger(write) {
  const out = typeof write === "function" ? write : (level, message) => {
    const line = `${level}: ${message}\n`;
    if (level === "error" || level === "warn") {
      process.stderr.write(line);
    }
  };
  return {
    info(message) {
      out("info", stripPaths(message));
    },
    warn(message) {
      out("warn", stripPaths(message));
    },
    error(message) {
      out("error", stripPaths(message));
    },
    debug() {},
  };
}

function settingsFilePath(dataRoot) {
  return path.join(dataRoot, "app_state", "settings.json");
}

function readUpdatesEnabled(settingsPath) {
  let raw;
  try {
    if (!fs.existsSync(settingsPath)) {
      return true;
    }
    raw = fs.readFileSync(settingsPath, "utf8");
  } catch (err) {
    return false;
  }
  let data;
  try {
    data = JSON.parse(raw);
  } catch (err) {
    return false;
  }
  if (!data || typeof data !== "object" || Array.isArray(data)) {
    return false;
  }
  if (!Object.prototype.hasOwnProperty.call(data, "updates_enabled")) {
    return true;
  }
  return data.updates_enabled === true;
}

function configureUpdater(updater, options = {}) {
  if (!updater || typeof updater !== "object") {
    throw new Error("updater is required");
  }
  updater.autoDownload = false;
  updater.autoInstallOnAppQuit = false;
  updater.allowPrerelease = false;
  if (typeof updater.setFeedURL === "function") {
    updater.setFeedURL({ ...GITHUB_PUBLISH });
  }
  updater.logger = options.logger || createQuietLogger(options.write);
  return updater;
}

function trimReleaseNotes(notes) {
  let text = "";
  if (typeof notes === "string") {
    text = notes;
  } else if (Array.isArray(notes)) {
    text = notes
      .map((row) => {
        if (typeof row === "string") return row;
        if (row && typeof row.note === "string") return row.note;
        return "";
      })
      .filter(Boolean)
      .join("\n");
  }
  text = text.trim();
  if (text.length <= RELEASE_NOTES_MAX) {
    return text;
  }
  return `${text.slice(0, RELEASE_NOTES_MAX - 1)}…`;
}

function releaseNotesDetail(info) {
  return trimReleaseNotes(info && info.releaseNotes);
}

function artifactExceedsLimit(bytes, limit = MAX_RELEASE_ARTIFACT_BYTES) {
  return typeof bytes === "number" && Number.isFinite(bytes) && bytes >= limit;
}

function listReleaseArtifacts(distDir, io = {}) {
  const exists = io.existsSync || fs.existsSync;
  const readdir = io.readdirSync || fs.readdirSync;
  const stat = io.statSync || fs.statSync;
  if (!exists(distDir)) {
    return [];
  }
  const names = readdir(distDir);
  const rows = [];
  for (const name of names) {
    if (!/\.(dmg|zip|pkg|AppImage|exe|msi|deb|rpm)$/i.test(name)) {
      continue;
    }
    const full = path.join(distDir, name);
    let size;
    try {
      size = stat(full).size;
    } catch (err) {
      continue;
    }
    rows.push({ name, bytes: size });
  }
  return rows;
}

function guardReleaseArtifactSizes(distDir, io = {}) {
  const limit = io.limit == null ? MAX_RELEASE_ARTIFACT_BYTES : io.limit;
  const artifacts = listReleaseArtifacts(distDir, io);
  const offenders = artifacts.filter((row) => artifactExceedsLimit(row.bytes, limit));
  return {
    checked: artifacts.length > 0 || (io.existsSync || fs.existsSync)(distDir),
    ok: offenders.length === 0,
    offenders,
    limit,
  };
}

function assertReleaseArtifactsUnderLimit(distDir, io = {}) {
  const result = guardReleaseArtifactSizes(distDir, io);
  if (!result.ok) {
    const names = result.offenders.map((row) => row.name).join(", ");
    throw new Error(`release artifact exceeds 2 GiB: ${names}`);
  }
  return result;
}

function showBox(dialog, browserWindow, options) {
  if (browserWindow) {
    return dialog.showMessageBox(browserWindow, options);
  }
  return dialog.showMessageBox(options);
}

function setDockProgress(browserWindow, percent) {
  if (!browserWindow || browserWindow.isDestroyed?.() || typeof browserWindow.setProgressBar !== "function") {
    return;
  }
  if (percent == null || percent < 0) {
    browserWindow.setProgressBar(-1);
    return;
  }
  browserWindow.setProgressBar(Math.min(1, Math.max(0, percent / 100)));
}

async function maybeCheckForUpdates(options) {
  const packaged = options && options.packaged === true;
  const updatesEnabled = options && options.updatesEnabled === true;
  const updater = options && options.updater;
  const dialog = options && options.dialog;
  const browserWindow = options && options.browserWindow;
  const logger = (options && options.logger) || createQuietLogger(options && options.write);

  if (!packaged || !updatesEnabled) {
    return { checked: false };
  }
  if (!updater || typeof updater.checkForUpdates !== "function") {
    throw new Error("updater.checkForUpdates is required");
  }
  if (!dialog || typeof dialog.showMessageBox !== "function") {
    throw new Error("dialog.showMessageBox is required");
  }

  let errorLogged = false;
  const logErrorOnce = (err) => {
    if (errorLogged) {
      return;
    }
    errorLogged = true;
    const message = err && err.message ? err.message : String(err);
    logger.error(`update check failed: ${stripPaths(message)}`);
  };

  return new Promise((resolve) => {
    let settled = false;
    let downloading = false;

    const finish = (result) => {
      if (settled) {
        return;
      }
      settled = true;
      updater.off?.("error", onError);
      updater.off?.("update-available", onAvailable);
      updater.off?.("update-not-available", onNotAvailable);
      updater.off?.("update-downloaded", onDownloaded);
      updater.off?.("download-progress", onProgress);
      setDockProgress(browserWindow, -1);
      resolve(result);
    };

    function onError(err) {
      logErrorOnce(err);
      finish({ checked: true, error: true });
    }

    function onNotAvailable() {
      finish({ checked: true, available: false });
    }

    function onProgress(progress) {
      const percent = progress && typeof progress.percent === "number" ? progress.percent : 0;
      setDockProgress(browserWindow, percent);
    }

    async function onAvailable(info) {
      if (settled || downloading) {
        return;
      }
      const version = info && info.version ? String(info.version) : "";
      const detail = releaseNotesDetail(info);
      let response;
      try {
        const box = await showBox(dialog, browserWindow, {
          type: "info",
          buttons: ["Download", "Later"],
          defaultId: 0,
          cancelId: 1,
          title: "BronchoRoute",
          message: `BronchoRoute ${version} is available. Download now?`,
          detail: detail || undefined,
        });
        response = box && box.response;
      } catch (err) {
        logErrorOnce(err);
        finish({ checked: true, error: true });
        return;
      }
      if (response !== 0) {
        finish({ checked: true, available: true, downloaded: false });
        return;
      }
      downloading = true;
      updater.on?.("download-progress", onProgress);
      try {
        await updater.downloadUpdate();
      } catch (err) {
        logErrorOnce(err);
        finish({ checked: true, error: true });
      }
    }

    async function onDownloaded() {
      if (settled) {
        return;
      }
      setDockProgress(browserWindow, -1);
      let response;
      try {
        const box = await showBox(dialog, browserWindow, {
          type: "info",
          buttons: ["Restart", "Later"],
          defaultId: 0,
          cancelId: 1,
          title: "BronchoRoute",
          message: "Restart to install?",
        });
        response = box && box.response;
      } catch (err) {
        logErrorOnce(err);
        finish({ checked: true, error: true });
        return;
      }
      if (response === 0 && typeof updater.quitAndInstall === "function") {
        updater.quitAndInstall();
        finish({ checked: true, available: true, downloaded: true, installed: true });
        return;
      }
      finish({ checked: true, available: true, downloaded: true, installed: false });
    }

    updater.on("error", onError);
    updater.on("update-available", onAvailable);
    updater.on("update-not-available", onNotAvailable);
    updater.on("update-downloaded", onDownloaded);

    Promise.resolve()
      .then(() => updater.checkForUpdates())
      .catch(onError);
  });
}

module.exports = {
  MAX_RELEASE_ARTIFACT_BYTES,
  GITHUB_PUBLISH,
  stripPaths,
  createQuietLogger,
  settingsFilePath,
  readUpdatesEnabled,
  configureUpdater,
  trimReleaseNotes,
  artifactExceedsLimit,
  listReleaseArtifacts,
  guardReleaseArtifactSizes,
  assertReleaseArtifactsUnderLimit,
  maybeCheckForUpdates,
};
