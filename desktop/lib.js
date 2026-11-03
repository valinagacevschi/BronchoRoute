// Changes: save-video names allow anatomical-orbit and topology-spin (#45).
// Changes: validate Record names and JPEG frames, resolve ffmpeg, and the Movies/BronchoRoute path.
// Changes: validate Save image names, PNG bytes, and the Pictures/BronchoRoute path.
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

function ensureSendUsageStatsFalse(configPath) {
  fs.mkdirSync(path.dirname(configPath), { recursive: true });
  if (!fs.existsSync(configPath)) {
    atomicWriteJson(configPath, { send_usage_stats: false });
    return;
  }
  let parsed;
  try {
    parsed = JSON.parse(fs.readFileSync(configPath, "utf8"));
  } catch (err) {
    return;
  }
  if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) return;
  if (parsed.send_usage_stats === false) return;
  parsed.send_usage_stats = false;
  atomicWriteJson(configPath, parsed);
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
  // This variable is nnUNet_results: Dataset folders live directly inside it.
  env.TOTALSEG_WEIGHTS_PATH = path.join(options.dataRoot, "models", "nnunet", "results");
  const config = path.join(env.TOTALSEG_HOME_DIR, "config.json");
  ensureSendUsageStatsFalse(config);
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

const PNG_SIGNATURE = Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]);
const SAVE_IMAGE_MAX_BYTES = 50 * 1024 * 1024;
const SAVE_IMAGE_NAME_RE = /^local-\d{3}_(anatomical|topology|endoluminal)_\d{8}-\d{4}\.png$/;

function isSaveImageName(name) {
  return typeof name === "string" && SAVE_IMAGE_NAME_RE.test(name);
}

function byteLength(bytes) {
  if (Buffer.isBuffer(bytes) || ArrayBuffer.isView(bytes)) return bytes.byteLength;
  if (bytes instanceof ArrayBuffer) return bytes.byteLength;
  return null;
}

function pngFitsLimit(length) {
  return Number.isInteger(length) && length >= 8 && length < SAVE_IMAGE_MAX_BYTES;
}

function isPngBytes(bytes) {
  const length = byteLength(bytes);
  if (!pngFitsLimit(length)) return false;
  const view = (Buffer.isBuffer(bytes) || ArrayBuffer.isView(bytes))
    ? bytes
    : new Uint8Array(bytes);
  for (let i = 0; i < 8; i += 1) {
    if (view[i] !== PNG_SIGNATURE[i]) return false;
  }
  return true;
}

function resolveSaveImageBaseDir(override, picturesPath) {
  if (typeof override === "string" && override.trim() !== "") {
    return path.resolve(override.trim());
  }
  if (typeof picturesPath !== "string" || picturesPath.trim() === "") {
    throw new Error("pictures directory must be set");
  }
  return picturesPath;
}

function saveImageDefaultPath(baseDir, defaultName) {
  if (typeof baseDir !== "string" || baseDir.trim() === "") {
    throw new Error("save image base directory must be set");
  }
  if (!isSaveImageName(defaultName)) {
    throw new Error("invalid save image name");
  }
  return path.join(baseDir, "BronchoRoute", defaultName);
}

const JPEG_SIGNATURE = Buffer.from([0xff, 0xd8, 0xff]);
const JPEG_MAX_BYTES = 5 * 1024 * 1024;
const SAVE_VIDEO_NAME_RE = /^local-\d{3}_(anatomical|anatomical-orbit|topology|topology-spin|endoluminal)_\d{8}-\d{4}\.mp4$/;

function isSaveVideoName(name) {
  return typeof name === "string" && SAVE_VIDEO_NAME_RE.test(name);
}

function isRecordSize(width, height) {
  return (width === 1920 && height === 1080) || (width === 1280 && height === 720);
}

function jpegFitsLimit(length) {
  return Number.isInteger(length) && length >= JPEG_SIGNATURE.length && length <= JPEG_MAX_BYTES;
}

function isJpegBytes(bytes) {
  const length = byteLength(bytes);
  if (!jpegFitsLimit(length)) return false;
  const view = (Buffer.isBuffer(bytes) || ArrayBuffer.isView(bytes))
    ? bytes
    : new Uint8Array(bytes);
  for (let i = 0; i < JPEG_SIGNATURE.length; i += 1) {
    if (view[i] !== JPEG_SIGNATURE[i]) return false;
  }
  return true;
}

function resolveSaveVideoBaseDir(override, videosPath) {
  if (typeof override === "string" && override.trim() !== "") {
    return path.resolve(override.trim());
  }
  if (typeof videosPath !== "string" || videosPath.trim() === "") {
    throw new Error("videos directory must be set");
  }
  return videosPath;
}

function saveVideoDefaultPath(baseDir, defaultName) {
  if (typeof baseDir !== "string" || baseDir.trim() === "") {
    throw new Error("save video base directory must be set");
  }
  if (!isSaveVideoName(defaultName)) {
    throw new Error("invalid save video name");
  }
  return path.join(baseDir, "BronchoRoute", defaultName);
}

function findOnPath(fileName, pathEnv, exists) {
  const entries = String(pathEnv || "").split(path.delimiter);
  for (const entry of entries) {
    if (!entry) continue;
    const candidate = path.join(entry, fileName);
    if (exists(candidate)) return candidate;
  }
  return null;
}

function resolveFfmpeg(options = {}) {
  const packaged = options.packaged === true;
  const env = options.env || process.env;
  const exists = typeof options.exists === "function" ? options.exists : fs.existsSync;
  if (packaged) {
    const resourcesPath = options.resourcesPath;
    if (typeof resourcesPath !== "string" || resourcesPath.trim() === "") {
      throw new Error("Video export needs ffmpeg");
    }
    const bundled = path.join(resourcesPath, "ffmpeg", "ffmpeg");
    if (!exists(bundled)) throw new Error("Video export needs ffmpeg");
    return bundled;
  }
  const raw = env && env.BRONCHOROUTE_FFMPEG;
  if (typeof raw === "string" && raw.trim() !== "") {
    const candidate = path.resolve(raw.trim());
    if (!exists(candidate)) throw new Error("Video export needs ffmpeg");
    return candidate;
  }
  const found = findOnPath("ffmpeg", env && env.PATH, exists);
  if (!found) throw new Error("Video export needs ffmpeg");
  return found;
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
  PNG_SIGNATURE,
  SAVE_IMAGE_MAX_BYTES,
  isSaveImageName,
  pngFitsLimit,
  isPngBytes,
  resolveSaveImageBaseDir,
  saveImageDefaultPath,
  JPEG_SIGNATURE,
  JPEG_MAX_BYTES,
  isSaveVideoName,
  isRecordSize,
  jpegFitsLimit,
  isJpegBytes,
  resolveSaveVideoBaseDir,
  saveVideoDefaultPath,
  resolveFfmpeg,
};
