// Changes: pure helpers for the dev-mode Electron shell (port, env, origin).
"use strict";

const crypto = require("node:crypto");
const fs = require("node:fs");
const path = require("node:path");

const DISCLAIMER = "Research and education tool. Not a medical device. Not for diagnosis or treatment decisions.";
const SPLASH = "Starting the AI stack… (first launch can take about a minute)";

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

function readSettings(file) {
  let text;
  try {
    text = fs.readFileSync(file, "utf8");
  } catch (err) {
    if (err && err.code === "ENOENT") {
      return {};
    }
    return null;
  }
  try {
    const data = JSON.parse(text);
    if (!data || typeof data !== "object" || Array.isArray(data)) {
      return null;
    }
    return data;
  } catch (err) {
    return null;
  }
}

function disclaimerAcknowledged(settings) {
  return Boolean(settings && settings.disclaimer_ack);
}

function settingsWithDisclaimerAck(settings) {
  return { ...settings, disclaimer_ack: true };
}

module.exports = {
  DISCLAIMER,
  SPLASH,
  parsePortLine,
  splitCompleteLines,
  buildSidecarEnv,
  sidecarOrigin,
  requestFilterUrls,
  isAllowedAppUrl,
  firstLocalCaseId,
  resolveDataRoot,
  newLaunchToken,
  atomicWriteText,
  atomicWriteJson,
  readSettings,
  disclaimerAcknowledged,
  settingsWithDisclaimerAck,
};
