// Changes: cover update gating, prompt-before-download, quiet errors, settings, 2 GiB guard.
"use strict";

const assert = require("node:assert/strict");
const { EventEmitter } = require("node:events");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const test = require("node:test");
const {
  GITHUB_PUBLISH,
  MAX_RELEASE_ARTIFACT_BYTES,
  artifactExceedsLimit,
  assertReleaseArtifactsUnderLimit,
  configureUpdater,
  guardReleaseArtifactSizes,
  maybeCheckForUpdates,
  readUpdatesEnabled,
  settingsFilePath,
  trimReleaseNotes,
} = require("./updater");

function tempDir() {
  return fs.mkdtempSync(path.join(os.tmpdir(), "bronchoroute-updater-"));
}

function writeSettings(dir, data) {
  const file = settingsFilePath(dir);
  fs.mkdirSync(path.dirname(file), { recursive: true });
  fs.writeFileSync(file, `${JSON.stringify(data, null, 2)}\n`);
  return file;
}

class FakeUpdater extends EventEmitter {
  constructor(behavior = {}) {
    super();
    this.behavior = behavior;
    this.autoDownload = true;
    this.autoInstallOnAppQuit = true;
    this.allowPrerelease = true;
    this.checkCalls = 0;
    this.downloadCalls = 0;
    this.quitCalls = 0;
    this.feed = null;
  }

  setFeedURL(feed) {
    this.feed = feed;
  }

  checkForUpdates() {
    this.checkCalls += 1;
    if (this.behavior.rejectCheck) {
      return Promise.reject(this.behavior.rejectCheck);
    }
    queueMicrotask(() => {
      if (this.behavior.errorEvent) {
        this.emit("error", this.behavior.errorEvent);
        return;
      }
      if (this.behavior.notAvailable) {
        this.emit("update-not-available", { version: "0.1.0" });
        return;
      }
      this.emit("update-available", this.behavior.info || {
        version: "0.2.0",
        releaseNotes: "Fixes and improvements.",
      });
    });
    return Promise.resolve({ isUpdateAvailable: !this.behavior.notAvailable });
  }

  downloadUpdate() {
    this.downloadCalls += 1;
    if (this.behavior.rejectDownload) {
      return Promise.reject(this.behavior.rejectDownload);
    }
    queueMicrotask(() => {
      this.emit("download-progress", { percent: 40, transferred: 4, total: 10, bytesPerSecond: 1 });
      this.emit("update-downloaded", this.behavior.info || { version: "0.2.0" });
    });
    return Promise.resolve([]);
  }

  quitAndInstall() {
    this.quitCalls += 1;
  }
}

function fakeDialog(sequence) {
  const calls = [];
  let index = 0;
  return {
    calls,
    async showMessageBox(winOrOpts, maybeOpts) {
      const opts = maybeOpts || winOrOpts;
      calls.push(opts);
      const response = sequence[index] == null ? 1 : sequence[index];
      index += 1;
      return { response };
    },
  };
}

test("configureUpdater disables auto-download and pins the GitHub feed", () => {
  const updater = new FakeUpdater();
  configureUpdater(updater);
  assert.equal(updater.autoDownload, false);
  assert.equal(updater.autoInstallOnAppQuit, false);
  assert.equal(updater.allowPrerelease, false);
  assert.deepEqual(updater.feed, { ...GITHUB_PUBLISH });
});

test("package.json publish and mac targets match the release feed", () => {
  const pkg = require("./package.json");
  assert.ok(pkg.dependencies["electron-updater"]);
  assert.match(pkg.dependencies["electron-updater"], /^\d+\.\d+\.\d+$/);
  assert.deepEqual(pkg.build.publish, [{
    provider: "github",
    owner: "valinagacevschi",
    repo: "BronchoRoute",
    releaseType: "release",
  }]);
  assert.ok(pkg.build.files.includes("updater.js"));
  assert.equal(pkg.build.artifactName.includes(" "), false);
  const targets = pkg.build.mac.target.map((row) => row.target);
  assert.ok(targets.includes("dir"));
  assert.ok(targets.includes("dmg"));
  assert.ok(targets.includes("zip"));
  assert.ok(pkg.build.mac.target.every((row) => row.arch.length === 1 && row.arch[0] === "arm64"));
  assert.match(pkg.scripts.pack, /--publish never/);
});

test("readUpdatesEnabled: missing file defaults true; false key; corrupt JSON false", () => {
  const root = tempDir();
  try {
    assert.equal(readUpdatesEnabled(settingsFilePath(root)), true);
    writeSettings(root, { updates_enabled: false });
    assert.equal(readUpdatesEnabled(settingsFilePath(root)), false);
    writeSettings(root, { updates_enabled: true });
    assert.equal(readUpdatesEnabled(settingsFilePath(root)), true);
    writeSettings(root, { initials: "AB" });
    assert.equal(readUpdatesEnabled(settingsFilePath(root)), true);
    const file = settingsFilePath(root);
    fs.writeFileSync(file, "{not-json");
    assert.equal(readUpdatesEnabled(file), false);
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
  }
});

test("disabled switch never calls checkForUpdates", async () => {
  const updater = new FakeUpdater();
  const dialog = fakeDialog([0]);
  const result = await maybeCheckForUpdates({
    packaged: true,
    updatesEnabled: false,
    updater,
    dialog,
  });
  assert.deepEqual(result, { checked: false });
  assert.equal(updater.checkCalls, 0);
  assert.equal(dialog.calls.length, 0);
});

test("unpackaged never calls checkForUpdates", async () => {
  const updater = new FakeUpdater();
  const dialog = fakeDialog([0]);
  const result = await maybeCheckForUpdates({
    packaged: false,
    updatesEnabled: true,
    updater,
    dialog,
  });
  assert.deepEqual(result, { checked: false });
  assert.equal(updater.checkCalls, 0);
});

test("update-available shows dialog and downloads only when Download is chosen", async () => {
  const updater = new FakeUpdater();
  const dialog = fakeDialog([0, 0]);
  const result = await maybeCheckForUpdates({
    packaged: true,
    updatesEnabled: true,
    updater,
    dialog,
  });
  assert.equal(updater.checkCalls, 1);
  assert.equal(dialog.calls.length, 2);
  assert.match(dialog.calls[0].message, /BronchoRoute 0\.2\.0 is available/);
  assert.deepEqual(dialog.calls[0].buttons, ["Download", "Later"]);
  assert.equal(updater.downloadCalls, 1);
  assert.deepEqual(dialog.calls[1].buttons, ["Restart", "Later"]);
  assert.equal(updater.quitCalls, 1);
  assert.equal(result.installed, true);
});

test("Later skips download", async () => {
  const updater = new FakeUpdater();
  const dialog = fakeDialog([1]);
  const result = await maybeCheckForUpdates({
    packaged: true,
    updatesEnabled: true,
    updater,
    dialog,
  });
  assert.equal(updater.downloadCalls, 0);
  assert.equal(updater.quitCalls, 0);
  assert.equal(dialog.calls.length, 1);
  assert.equal(result.downloaded, false);
});

test("download then Later skips quitAndInstall", async () => {
  const updater = new FakeUpdater();
  const dialog = fakeDialog([0, 1]);
  const result = await maybeCheckForUpdates({
    packaged: true,
    updatesEnabled: true,
    updater,
    dialog,
  });
  assert.equal(updater.downloadCalls, 1);
  assert.equal(updater.quitCalls, 0);
  assert.equal(result.installed, false);
});

test("error or 404 is logged once and never shows a dialog", async () => {
  const lines = [];
  const updater = new FakeUpdater({ errorEvent: new Error("HttpError: 404 Not Found") });
  const dialog = fakeDialog([0]);
  const result = await maybeCheckForUpdates({
    packaged: true,
    updatesEnabled: true,
    updater,
    dialog,
    logger: {
      error(message) {
        lines.push(message);
      },
      info() {},
      warn() {},
      debug() {},
    },
  });
  assert.equal(result.error, true);
  assert.equal(dialog.calls.length, 0);
  assert.equal(updater.downloadCalls, 0);
  assert.equal(lines.length, 1);
  assert.match(lines[0], /404/);
});

test("rejected checkForUpdates is swallowed the same way", async () => {
  const lines = [];
  const updater = new FakeUpdater({ rejectCheck: new Error("ENOTFOUND api.github.com") });
  const dialog = fakeDialog([0]);
  const result = await maybeCheckForUpdates({
    packaged: true,
    updatesEnabled: true,
    updater,
    dialog,
    logger: {
      error(message) {
        lines.push(message);
      },
      info() {},
      warn() {},
      debug() {},
    },
  });
  assert.equal(result.error, true);
  assert.equal(dialog.calls.length, 0);
  assert.equal(lines.length, 1);
});

test("trimReleaseNotes caps long notes", () => {
  const long = "x".repeat(2000);
  assert.equal(trimReleaseNotes(long).endsWith("…"), true);
  assert.ok(trimReleaseNotes(long).length <= 800);
});

test("artifact size guard fails at 2 GiB", () => {
  assert.equal(artifactExceedsLimit(MAX_RELEASE_ARTIFACT_BYTES - 1), false);
  assert.equal(artifactExceedsLimit(MAX_RELEASE_ARTIFACT_BYTES), true);
  const dist = path.join(__dirname, "dist");
  if (fs.existsSync(dist)) {
    assertReleaseArtifactsUnderLimit(dist);
  } else {
    const result = guardReleaseArtifactSizes("/no/such/dist", {
      existsSync: () => false,
      readdirSync: () => [],
      statSync: () => ({ size: 0 }),
    });
    assert.equal(result.ok, true);
    assert.equal(result.checked, false);
    assert.throws(
      () => assertReleaseArtifactsUnderLimit("/tmp", {
        existsSync: () => true,
        readdirSync: () => ["BronchoRoute-9.9.9-arm64.dmg"],
        statSync: () => ({ size: MAX_RELEASE_ARTIFACT_BYTES }),
      }),
      /2 GiB/,
    );
  }
});
