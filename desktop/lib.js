// Changes: FileVault status line used by the one-time first-run note.
// Changes: arm64 gate and one-time warning below 16 GB of memory.
// Changes: shell helpers including isolated packaged Python launch configuration.
// Changes: shell helpers; settings persistence belongs to the Python sidecar.
// Changes: pure helpers for the dev-mode Electron shell (port, env, origin).
"use strict";

const crypto = require("node:crypto");
const fs = require("node:fs");
const path = require("node:path");

const DISCLAIMER = "Research and education tool. Not a medical device. Not for diagnosis or treatment decisions.";
const FILEVAULT_NOTE = "FileVault is off. Turn it on so Cases stored on this Mac stay encrypted.";
const SPLASH = "Starting the AI stack… (first launch can take about a minute)";
// os.totalmem() is physical bytes. A 16 GB Mac is not below this line.
const MIN_RAM_BYTES = 16 * 1024 * 1024 * 1024;

function hardwareBlockReason(arch) {
  if (arch !== "arm64") {
    return "BronchoRoute requires an Apple Silicon Mac (arm64).";
  }
  return null;
}

function shouldWarnLowRam(totalBytes, ramWarningShown) {
  return typeof totalBytes === "number"
    && Number.isFinite(totalBytes)
    && totalBytes < MIN_RAM_BYTES
    && ramWarningShown !== true;
}

function parsePortLine(line) {
  const match = /^BRONCHOROUTE_PORT=([0-9]+)$/.exec(String(line).trim());
  if (!match) {
    return null;
  }
  const digits = match[1];
  if (digits.length > 1 && digits.startsWith("0")) {
    return null;
  }
  const port = Number(digits);
  if (!Number.isInteger(port) || port < 1 || port > 65535) {
    return null;
  }
  return port;
}

function splitCompleteLines(buffer) {
  const lines = [];
  let rest = String(buffer);
  let index = rest.indexOf("\n");
  while (index !== -1) {
    let line = rest.slice(0, index);
    rest = rest.slice(index + 1);
    if (line.endsWith("\r")) {
      line = line.slice(0, -1);
    }
    lines.push(line);
    index = rest.indexOf("\n");
  }
  return { lines, rest };
}

function buildSidecarEnv(baseEnv, options) {
  const token = options && options.token;
  if (typeof token !== "string" || token.trim() === "") {
    throw new Error("sidecar token must be non-blank");
  }
  for (const key of ["dataRoot", "tmpDir", "logDir"]) {
    const value = options[key];
    if (typeof value !== "string" || value.trim() === "") {
      throw new Error(`sidecar ${key} must be set`);
    }
  }
  const env = { ...baseEnv };
  delete env.PYTHONPATH;
  // A parent shell can pin these and skip --port 0 or the data root.
  delete env.LUNGNAV_PORT;
  delete env.LUNGNAV_HOST;
  // stdout is a pipe; without this, later log lines sit in Python's buffer.
  env.PYTHONUNBUFFERED = "1";
  env.BRONCHOROUTE_TOKEN = token;
  env.LUNGNAV_DATA_ROOT = options.dataRoot;
  env.LUNGNAV_TMP_DIR = options.tmpDir;
  env.LUNGNAV_LOG_DIR = options.logDir;
  return env;
}

function sidecarLaunch(packaged, resourcesPath, options, baseEnv) {
  const env = buildSidecarEnv(baseEnv, options);
  const cwd = packaged
    ? path.join(resourcesPath, "app", "pipeline")
    : path.resolve(__dirname, "..", "pipeline");
  const args = ["app_server.py", "--port", "0", "--exit-on-stdin-close"];
  if (!packaged) {
    return { command: "uv", args: ["run", "python", ...args], cwd, env };
  }
  env.PYTHONDONTWRITEBYTECODE = "1";
  env.PYTHONNOUSERSITE = "1";
  env.TOTALSEG_HOME_DIR = path.join(options.dataRoot, "models", "totalseg");
  env.TOTALSEG_WEIGHTS_PATH = path.join(options.dataRoot, "models");
  const config = path.join(env.TOTALSEG_HOME_DIR, "config.json");
  fs.mkdirSync(env.TOTALSEG_HOME_DIR, { recursive: true });
  if (!fs.existsSync(config)) {
    atomicWriteJson(config, { send_usage_stats: false });
  }
  return {
    command: path.join(resourcesPath, "python", "bin", "python3"),
    args: [path.join(cwd, args[0]), ...args.slice(1)],
    cwd,
    env,
  };
}

function sidecarOrigin(port) {
  const parsed = parsePortLine(`BRONCHOROUTE_PORT=${port}`);
  if (parsed == null) {
    throw new Error("invalid sidecar port");
  }
  return `http://127.0.0.1:${parsed}`;
}

function requestFilterUrls(port) {
  return [`${sidecarOrigin(port)}/*`];
}

function isAllowedAppUrl(url, port) {
  let parsed;
  try {
    parsed = new URL(url);
  } catch (err) {
    return false;
  }
  return parsed.origin === sidecarOrigin(port);
}

function firstLocalCaseId(cases) {
  if (!Array.isArray(cases)) {
    return null;
  }
  for (const row of cases) {
    if (!row || typeof row.id !== "string") {
      continue;
    }
    if (row.id.startsWith("local-")) {
      return row.id;
    }
  }
  return null;
}

function resolveDataRoot(env, userDataPath) {
  const raw = env && env.BRONCHOROUTE_DATA_ROOT;
  if (typeof raw === "string" && raw.trim() !== "") {
    return path.resolve(raw.trim());
  }
  return userDataPath;
}

function fileVaultIsOff(statusText) {
  const line = String(statusText || "")
    .split(/\r?\n/)
    .map((item) => item.trim())
    .find(Boolean);
  return /^FileVault is Off\.?$/.test(line || "");
}

function newLaunchToken() {
  return crypto.randomBytes(32).toString("hex");
}

function atomicWriteText(file, text) {
  const dir = path.dirname(file);
  fs.mkdirSync(dir, { recursive: true });
  const tmp = path.join(
    dir,
    `.${path.basename(file)}.${process.pid}.${crypto.randomBytes(4).toString("hex")}.tmp`,
  );
  let fd = null;
  try {
    fd = fs.openSync(tmp, "w");
    fs.writeSync(fd, text);
    fs.fsyncSync(fd);
    fs.closeSync(fd);
    fd = null;
    fs.renameSync(tmp, file);
  } catch (err) {
    if (fd != null) {
      fs.closeSync(fd);
    }
    fs.rmSync(tmp, { force: true });
    throw err;
  }
}

function atomicWriteJson(file, data) {
  atomicWriteText(file, `${JSON.stringify(data, null, 2)}\n`);
}

module.exports = {
  DISCLAIMER,
  FILEVAULT_NOTE,
  SPLASH,
  fileVaultIsOff,
  parsePortLine,
  splitCompleteLines,
  buildSidecarEnv,
  sidecarLaunch,
  sidecarOrigin,
  requestFilterUrls,
  isAllowedAppUrl,
  firstLocalCaseId,
  resolveDataRoot,
  newLaunchToken,
  atomicWriteText,
  atomicWriteJson,
  MIN_RAM_BYTES,
  hardwareBlockReason,
  shouldWarnLowRam,
};
