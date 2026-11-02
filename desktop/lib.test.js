// Changes: usage stats stay off, including a config that omitted the key.
// Changes: default Pictures and Movies paths are built at runtime, with no home-directory literal.
// Changes: Record name, JPEG signature, ffmpeg resolution, and injected Movies directory.
// Changes: Save image name, PNG signature, and injected Pictures directory.
// Changes: FileVault Off is the only status that asks for the one-time note.
// Changes: cover the arm64 gate, the 16 GB warning, and sign scripts.
// Changes: shell helper coverage excludes Python-owned settings persistence.
"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const test = require("node:test");
const {
  DISCLAIMER,
  buildSidecarEnv,
  fileVaultIsOff,
  firstLocalCaseId,
  hardwareBlockReason,
  isAllowedAppUrl,
  MIN_RAM_BYTES,
  shouldWarnLowRam,
  newLaunchToken,
  parsePortLine,
  requestFilterUrls,
  resolveDataRoot,
  splitCompleteLines,
  atomicWriteJson,
  PNG_SIGNATURE,
  SAVE_IMAGE_MAX_BYTES,
  isSaveImageName,
  pngFitsLimit,
  isPngBytes,
  resolveSaveImageBaseDir,
  saveImageDefaultPath,
  JPEG_MAX_BYTES,
  isSaveVideoName,
  isRecordSize,
  jpegFitsLimit,
  isJpegBytes,
  resolveSaveVideoBaseDir,
  saveVideoDefaultPath,
  resolveFfmpeg,
} = require("./lib");

test("parsePortLine accepts a single sidecar announcement", () => {
  assert.equal(parsePortLine("BRONCHOROUTE_PORT=8765"), 8765);
  assert.equal(parsePortLine("  BRONCHOROUTE_PORT=1\r"), 1);
  assert.equal(parsePortLine("BRONCHOROUTE_PORT=65535"), 65535);
  assert.equal(parsePortLine("BRONCHOROUTE_PORT=0"), null);
  assert.equal(parsePortLine("BRONCHOROUTE_PORT=65536"), null);
  assert.equal(parsePortLine("BRONCHOROUTE_PORT=080"), null);
  assert.equal(parsePortLine("BRONCHOROUTE_PORT="), null);
  assert.equal(parsePortLine("BRONCHOROUTE_PORT=12abc"), null);
  assert.equal(parsePortLine("noise"), null);
});

test("splitCompleteLines keeps a partial trailing line", () => {
  const split = splitCompleteLines("noise\r\nBRONCHOROUTE_PORT=9\npartial");
  assert.deepEqual(split.lines, ["noise", "BRONCHOROUTE_PORT=9"]);
  assert.equal(split.rest, "partial");
  assert.equal(parsePortLine(split.lines[1]), 9);
});

test("buildSidecarEnv strips PYTHONPATH and sets the sidecar vars", () => {
  const base = {
    PYTHONPATH: "/tmp/hidden",
    PATH: "/usr/bin",
    LUNGNAV_PORT: "8765",
    LUNGNAV_HOST: "0.0.0.0",
    LUNGNAV_DATA_ROOT: "/real",
  };
  const env = buildSidecarEnv(base, {
    token: "abc",
    dataRoot: "/data",
    tmpDir: "/data/tmp",
    logDir: "/data/logs",
  });
  assert.equal(base.PYTHONPATH, "/tmp/hidden");
  assert.equal(Object.hasOwn(env, "PYTHONPATH"), false);
  assert.equal(env.PATH, "/usr/bin");
  assert.equal(Object.hasOwn(env, "LUNGNAV_PORT"), false);
  assert.equal(Object.hasOwn(env, "LUNGNAV_HOST"), false);
  assert.equal(env.BRONCHOROUTE_TOKEN, "abc");
  assert.equal(env.LUNGNAV_DATA_ROOT, "/data");
  assert.equal(env.LUNGNAV_TMP_DIR, "/data/tmp");
  assert.equal(env.LUNGNAV_LOG_DIR, "/data/logs");
  assert.equal(env.PYTHONUNBUFFERED, "1");
  assert.throws(
    () => buildSidecarEnv({}, {
      token: "  ",
      dataRoot: "/data",
      tmpDir: "/data/tmp",
      logDir: "/data/logs",
    }),
    /non-blank/,
  );
});

test("origin filter allows only the sidecar origin", () => {
  assert.deepEqual(requestFilterUrls(9), ["http://127.0.0.1:9/*"]);
  assert.equal(isAllowedAppUrl("http://127.0.0.1:9/", 9), true);
  assert.equal(isAllowedAppUrl("http://127.0.0.1:9/api/cases?case=1", 9), true);
  assert.equal(isAllowedAppUrl("https://127.0.0.1:9/", 9), false);
  assert.equal(isAllowedAppUrl("http://localhost:9/", 9), false);
  assert.equal(isAllowedAppUrl("http://127.0.0.1:10/", 9), false);
  assert.equal(isAllowedAppUrl("http://evil.example/", 9), false);
  assert.equal(isAllowedAppUrl("http://127.0.0.1:9@evil.example/", 9), false);
  assert.equal(isAllowedAppUrl("file:///tmp/x", 9), false);
  assert.equal(isAllowedAppUrl("not a url", 9), false);
  assert.throws(() => requestFilterUrls(0), /invalid sidecar port/);
});

test("first local Case follows list order", () => {
  assert.equal(firstLocalCaseId([
    { id: "3" },
    { id: "local-002" },
    { id: "local-001" },
  ]), "local-002");
  assert.equal(firstLocalCaseId([]), null);
  assert.equal(firstLocalCaseId([{ id: 1 }]), null);
  assert.equal(firstLocalCaseId(null), null);
});

test("data root honors BRONCHOROUTE_DATA_ROOT", () => {
  const override = path.join(os.tmpdir(), "bronchoroute-override");
  assert.equal(
    resolveDataRoot({ BRONCHOROUTE_DATA_ROOT: `  ${override}  ` }, "/user/data"),
    override,
  );
  assert.equal(resolveDataRoot({}, "/user/data"), "/user/data");
  assert.equal(
    resolveDataRoot({ BRONCHOROUTE_DATA_ROOT: "   " }, "/user/data"),
    "/user/data",
  );
});

test("launch token is 32 bytes of hex", () => {
  const token = newLaunchToken();
  assert.match(token, /^[0-9a-f]{64}$/);
});

test("fileVaultIsOff matches only the Off status line", () => {
  assert.equal(fileVaultIsOff("FileVault is Off."), true);
  assert.equal(fileVaultIsOff("FileVault is Off.\n"), true);
  assert.equal(fileVaultIsOff("FileVault is On."), false);
  assert.equal(fileVaultIsOff("FileVault is On.\nSomething else"), false);
  assert.equal(fileVaultIsOff(""), false);
  assert.equal(fileVaultIsOff("error: fdesetup"), false);
});

test("viewer template vendors fonts and the research footer", () => {
  const root = path.join(__dirname, "..", "prototype");
  const template = fs.readFileSync(path.join(root, "renderer.template.html"), "utf8");
  const rendered = fs.readFileSync(path.join(root, "renderer.html"), "utf8");
  const sentinel = "/*__AIRWAY_EMBED__*/";
  assert.equal(template.split(sentinel).length, 2);
  assert.equal(template.includes(DISCLAIMER), true);
  assert.equal(template.includes('id="library-open"'), true);
  assert.equal(template.includes("fonts.googleapis.com"), false);
  assert.equal(template.includes("fonts/IBMPlexSans-Regular.woff2"), true);
  const parts = template.split(sentinel);
  const data = rendered.slice(parts[0].length, rendered.length - parts[1].length);
  assert.equal(rendered, template.replace(sentinel, data));
});

test("packaged sidecar uses the resource interpreter and isolated model state", () => {
  const { sidecarLaunch } = require("./lib");
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "bronchoroute-package-"));
  try {
    const options = { token: "test-token", dataRoot: root, tmpDir: root, logDir: root };
    const launch = sidecarLaunch(true, "/bundle/Resources", options, { PYTHONPATH: "/outside" });
    assert.equal(launch.command, "/bundle/Resources/python/bin/python3");
    assert.equal(launch.cwd, "/bundle/Resources/app/pipeline");
    assert.equal(launch.args[0], "/bundle/Resources/app/pipeline/app_server.py");
    assert.equal(launch.env.PYTHONPATH, undefined);
    assert.equal(launch.env.PYTHONDONTWRITEBYTECODE, "1");
    assert.equal(launch.env.PYTHONNOUSERSITE, "1");
    assert.equal(
      launch.env.TOTALSEG_WEIGHTS_PATH,
      path.join(root, "models", "nnunet", "results"),
    );
    const config = path.join(root, "models", "totalseg", "config.json");
    assert.deepEqual(JSON.parse(fs.readFileSync(config, "utf8")), { send_usage_stats: false });
    atomicWriteJson(config, { send_usage_stats: false, retained: 42 });
    sidecarLaunch(true, "/bundle/Resources", options, {});
    assert.deepEqual(JSON.parse(fs.readFileSync(config, "utf8")), { send_usage_stats: false, retained: 42 });
    atomicWriteJson(config, { totalseg_id: "keep-me" });
    sidecarLaunch(true, "/bundle/Resources", options, {});
    assert.deepEqual(JSON.parse(fs.readFileSync(config, "utf8")), {
      totalseg_id: "keep-me",
      send_usage_stats: false,
    });
    atomicWriteJson(config, { send_usage_stats: true, totalseg_id: "keep-me" });
    sidecarLaunch(true, "/bundle/Resources", options, {});
    assert.deepEqual(JSON.parse(fs.readFileSync(config, "utf8")), {
      send_usage_stats: false,
      totalseg_id: "keep-me",
    });
    const dev = sidecarLaunch(false, "/unused", options, {});
    assert.equal(dev.command, "uv");
    assert.deepEqual(dev.args, ["run", "python", "app_server.py", "--port", "0", "--exit-on-stdin-close"]);
    assert.equal(dev.env.PYTHONNOUSERSITE, undefined);
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
  }
});

test("hardware gate is arm64-only and warns once below 16 GB", () => {
  assert.equal(hardwareBlockReason("arm64"), null);
  assert.match(hardwareBlockReason("x64"), /Apple Silicon/);
  assert.match(hardwareBlockReason("ia32"), /arm64/);
  assert.equal(shouldWarnLowRam(MIN_RAM_BYTES, false), false);
  assert.equal(shouldWarnLowRam(MIN_RAM_BYTES - 1, false), true);
  assert.equal(shouldWarnLowRam(8 * 1024 * 1024 * 1024, false), true);
  assert.equal(shouldWarnLowRam(8 * 1024 * 1024 * 1024, true), false);
  assert.equal(shouldWarnLowRam(Number.NaN, false), false);
});

test("entitlements stay empty for Python and allow-jit only for the app", () => {
  const python = fs.readFileSync(path.join(__dirname, "build-resources", "python.entitlements.plist"), "utf8");
  const appEntitlements = fs.readFileSync(path.join(__dirname, "build-resources", "app.entitlements.plist"), "utf8");
  assert.equal(python.includes("com.apple.security"), false);
  assert.equal(appEntitlements.includes("com.apple.security.cs.allow-jit"), true);
  assert.equal(appEntitlements.includes("disable-library-validation"), false);
  assert.equal(appEntitlements.includes("get-task-allow"), false);
});

test("builder includes only runtime resources and targets unsigned arm64 macOS", () => {
  const { build, devDependencies, scripts } = require("./package.json");
  assert.equal(scripts.sign, "bash scripts/sign-and-notarize.sh");
  assert.equal(scripts["sign:adhoc"], "IDENTITY=- bash scripts/sign-and-notarize.sh");
  assert.equal(build.mac.identity, null);
  assert.equal(build.mac.minimumSystemVersion, "14.0");
  assert.deepEqual(build.mac.target, [{ target: "dir", arch: ["arm64"] }]);
  assert.ok(build.files.includes("weights.js"));
  assert.ok(build.files.includes("weights-manifest.json"));
  assert.match(devDependencies["electron-builder"], /^\d+\.\d+\.\d+$/);
  const pipeline = build.extraResources.find((row) => row.to === "app/pipeline");
  assert.ok(pipeline.filter.includes("repo_root.py"));
  assert.ok(pipeline.filter.includes("pyproject.toml"));
  assert.ok(pipeline.filter.every((name) => !name.startsWith("test_") && !name.includes("*")));
  const root = build.extraResources.find((row) => row.to === "app");
  assert.deepEqual(root.filter, ["LICENSE", "THIRD_PARTY_NOTICES.md", "sponsors.json"]);
});

test("save image names accept only local-NNN view stills", () => {
  const ok = [
    "local-007_anatomical_20261101-2350.png",
    "local-001_topology_20261102-0000.png",
    "local-999_endoluminal_19990101-0000.png",
    "local-001_anatomical_00000000-0000.png",
  ];
  for (const name of ok) assert.equal(isSaveImageName(name), true, name);
  const bad = [
    "local-07_anatomical_20261101-2350.png",
    "local-0001_anatomical_20261101-2350.png",
    "local-001_ct-panes_20261101-2350.png",
    "local-001_walkthrough_20261101-2350.png",
    "local-001_Anatomical_20261101-2350.png",
    "local-001_anatomical_2026111-2350.png",
    "local-001_anatomical_20261101-235.png",
    "local-001_anatomical_20261101-23500.png",
    "local-001_anatomical_20261101-2350.PNG",
    "local-001_anatomical_20261101-2350.png.exe",
    "../local-001_anatomical_20261101-2350.png",
    "local-001/anatomical_20261101-2350.png",
    " local-001_anatomical_20261101-2350.png",
    "local-001_anatomical_20261101-2350.png ",
    "",
    null,
    12,
  ];
  for (const name of bad) assert.equal(isSaveImageName(name), false, String(name));
});

test("png bytes require the signature and stay under 50 MB", () => {
  const sig = Buffer.from(PNG_SIGNATURE);
  assert.equal(isPngBytes(sig), true);
  assert.equal(isPngBytes(Buffer.concat([sig, Buffer.from([0, 1, 2])])), true);
  assert.equal(isPngBytes(new Uint8Array(sig)), true);
  assert.equal(isPngBytes(Uint8Array.from(sig).buffer), true);
  const padded = Buffer.concat([Buffer.from([9, 9]), sig, Buffer.from([3])]);
  const view = new Uint8Array(padded.buffer, padded.byteOffset + 2, 8);
  assert.equal(isPngBytes(view), true);
  assert.equal(isPngBytes(sig.subarray(0, 7)), false);
  assert.equal(isPngBytes(Buffer.concat([Buffer.from([0]), sig])), false);
  assert.equal(isPngBytes(Buffer.from("not a png")), false);
  assert.equal(isPngBytes("\x89PNG\r\n\x1a\n"), false);
  assert.equal(isPngBytes(null), false);
  assert.equal(SAVE_IMAGE_MAX_BYTES, 50 * 1024 * 1024);
  assert.equal(pngFitsLimit(8), true);
  assert.equal(pngFitsLimit(7), false);
  assert.equal(pngFitsLimit(SAVE_IMAGE_MAX_BYTES - 1), true);
  assert.equal(pngFitsLimit(SAVE_IMAGE_MAX_BYTES), false);
  assert.equal(pngFitsLimit(1.5), false);
});

test("save image default path uses an injected pictures directory", () => {
  const name = "local-007_anatomical_20261101-2350.png";
  const injected = path.join(os.tmpdir(), "bronchoroute-42-pics");
  const pictures = path.join(os.tmpdir(), "bronchoroute-42-pictures-base", "Pictures");
  const base = resolveSaveImageBaseDir(`  ${injected}  `, "/unused/Pictures");
  assert.equal(base, path.resolve(injected));
  assert.equal(
    saveImageDefaultPath(base, name),
    path.join(path.resolve(injected), "BronchoRoute", name),
  );
  assert.equal(
    resolveSaveImageBaseDir("   ", pictures),
    pictures,
  );
  assert.equal(
    saveImageDefaultPath(pictures, "local-001_endoluminal_20261102-0001.png"),
    path.join(pictures, "BronchoRoute", "local-001_endoluminal_20261102-0001.png"),
  );
  assert.throws(() => saveImageDefaultPath(base, "local-001_ct_20261101-2350.png"), /invalid save image name/);
  assert.throws(() => saveImageDefaultPath("  ", name), /base directory/);
  assert.throws(() => resolveSaveImageBaseDir("", "  "), /pictures directory/);
});

test("save video names accept only local-NNN view mp4s", () => {
  const ok = [
    "local-007_anatomical_20261101-2350.mp4",
    "local-001_topology_20261102-0000.mp4",
    "local-999_endoluminal_19990101-0000.mp4",
    "local-001_anatomical_00000000-0000.mp4",
  ];
  for (const name of ok) assert.equal(isSaveVideoName(name), true, name);
  const bad = [
    "local-007_anatomical_20261101-2350.png",
    "local-07_anatomical_20261101-2350.mp4",
    "local-0001_anatomical_20261101-2350.mp4",
    "local-001_ct-panes_20261101-2350.mp4",
    "local-001_walkthrough_20261101-2350.mp4",
    "local-001_Anatomical_20261101-2350.mp4",
    "local-001_anatomical_2026111-2350.mp4",
    "local-001_anatomical_20261101-235.mp4",
    "local-001_anatomical_20261101-23500.mp4",
    "local-001_anatomical_20261101-2350.MP4",
    "local-001_anatomical_20261101-2350.mp4.exe",
    "../local-001_anatomical_20261101-2350.mp4",
    "local-001/anatomical_20261101-2350.mp4",
    " local-001_anatomical_20261101-2350.mp4",
    "local-001_anatomical_20261101-2350.mp4 ",
    "",
    null,
    12,
  ];
  for (const name of bad) assert.equal(isSaveVideoName(name), false, String(name));
  assert.equal(isRecordSize(1920, 1080), true);
  assert.equal(isRecordSize(1280, 720), true);
  assert.equal(isRecordSize(1920, 720), false);
  assert.equal(isRecordSize("1920", 1080), false);
  assert.equal(isRecordSize(1280.5, 720), false);
});

test("jpeg bytes require the signature and stay at or under 5 MB", () => {
  const sig = Buffer.from([0xff, 0xd8, 0xff, 0xe0]);
  assert.equal(isJpegBytes(sig), true);
  assert.equal(isJpegBytes(Buffer.concat([sig, Buffer.from([0, 1])])), true);
  assert.equal(isJpegBytes(new Uint8Array(sig)), true);
  assert.equal(isJpegBytes(Uint8Array.from(sig).buffer), true);
  const padded = Buffer.concat([Buffer.from([1, 2]), sig]);
  const view = new Uint8Array(padded.buffer, padded.byteOffset + 2, 4);
  assert.equal(isJpegBytes(view), true);
  assert.equal(isJpegBytes(Buffer.from([0xff, 0xd8])), false);
  assert.equal(isJpegBytes(Buffer.from([0xff, 0xd8, 0x00, 0x00])), false);
  assert.equal(isJpegBytes(Buffer.from("not a jpeg")), false);
  assert.equal(isJpegBytes(null), false);
  assert.equal(JPEG_MAX_BYTES, 5 * 1024 * 1024);
  assert.equal(jpegFitsLimit(3), true);
  assert.equal(jpegFitsLimit(2), false);
  assert.equal(jpegFitsLimit(JPEG_MAX_BYTES), true);
  assert.equal(jpegFitsLimit(JPEG_MAX_BYTES + 1), false);
  const max = Buffer.alloc(JPEG_MAX_BYTES);
  max[0] = 0xff;
  max[1] = 0xd8;
  max[2] = 0xff;
  assert.equal(isJpegBytes(max), true);
  const over = Buffer.alloc(JPEG_MAX_BYTES + 1);
  over[0] = 0xff;
  over[1] = 0xd8;
  over[2] = 0xff;
  assert.equal(isJpegBytes(over), false);
});

test("resolveFfmpeg prefers env, then PATH, then the packaged binary", () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "bronchoroute-43-ff-"));
  try {
    const envBin = path.join(root, "from-env");
    fs.writeFileSync(envBin, "");
    const binDir = path.join(root, "bin");
    fs.mkdirSync(binDir);
    const pathBin = path.join(binDir, "ffmpeg");
    fs.writeFileSync(pathBin, "");
    const bundled = path.join(root, "ffmpeg", "ffmpeg");
    fs.mkdirSync(path.dirname(bundled), { recursive: true });
    fs.writeFileSync(bundled, "");
    assert.equal(
      resolveFfmpeg({
        packaged: false,
        env: { BRONCHOROUTE_FFMPEG: `  ${envBin}  `, PATH: binDir },
      }),
      path.resolve(envBin),
    );
    assert.equal(
      resolveFfmpeg({
        packaged: false,
        env: { BRONCHOROUTE_FFMPEG: "   ", PATH: binDir },
      }),
      pathBin,
    );
    assert.equal(
      resolveFfmpeg({
        packaged: true,
        resourcesPath: root,
        env: { BRONCHOROUTE_FFMPEG: envBin, PATH: binDir },
      }),
      bundled,
    );
    assert.throws(
      () => resolveFfmpeg({
        packaged: false,
        env: { BRONCHOROUTE_FFMPEG: path.join(root, "missing"), PATH: binDir },
      }),
      (err) => err instanceof Error && err.message === "Video export needs ffmpeg",
    );
    assert.throws(
      () => resolveFfmpeg({
        packaged: true,
        resourcesPath: path.join(root, "empty-resources"),
        env: { BRONCHOROUTE_FFMPEG: envBin, PATH: binDir },
      }),
      (err) => err instanceof Error && err.message === "Video export needs ffmpeg",
    );
    assert.throws(
      () => resolveFfmpeg({
        packaged: false,
        env: { PATH: path.join(root, "no-such-dir") },
      }),
      (err) => err instanceof Error && err.message === "Video export needs ffmpeg",
    );
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
  }
});

test("save video default path uses an injected movies directory", () => {
  const name = "local-007_anatomical_20261101-2350.mp4";
  const injected = path.join(os.tmpdir(), "bronchoroute-43-movies");
  const movies = path.join(os.tmpdir(), "bronchoroute-43-pictures-base", "Movies");
  const base = resolveSaveVideoBaseDir(`  ${injected}  `, "/unused/Movies");
  assert.equal(base, path.resolve(injected));
  assert.equal(
    saveVideoDefaultPath(base, name),
    path.join(path.resolve(injected), "BronchoRoute", name),
  );
  assert.equal(
    resolveSaveVideoBaseDir("   ", movies),
    movies,
  );
  assert.equal(
    saveVideoDefaultPath(movies, "local-001_endoluminal_20261102-0001.mp4"),
    path.join(movies, "BronchoRoute", "local-001_endoluminal_20261102-0001.mp4"),
  );
  assert.throws(() => saveVideoDefaultPath(base, "local-001_anatomical_20261101-2350.png"), /invalid save video name/);
  assert.throws(() => saveVideoDefaultPath("  ", name), /base directory/);
  assert.throws(() => resolveSaveVideoBaseDir("", "  "), /videos directory/);
});
