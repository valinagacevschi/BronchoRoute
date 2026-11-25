<!-- Changes: document GitHub Releases auto-update (ask before download; Settings switch). -->
<!-- Changes: document npm run build:ffmpeg and the packaged LGPL binary path. -->
# BronchoRoute desktop shell

Dev run, from this directory:

```sh
npm install
npm start
```

The shell starts `pipeline/app_server.py` with `uv` (must be on `PATH`) and opens the viewer.
Set `BRONCHOROUTE_DATA_ROOT` to use a data root other than
`~/Library/Application Support/BronchoRoute` (a pipeline tree, for local dev).

## Video

Live Record writes MP4 H.264 with ffmpeg's `h264_videotoolbox` encoder. An LGPL ffmpeg build has no `libx264`, so the encoder name stays `h264_videotoolbox`. In development, `BRONCHOROUTE_FFMPEG` selects the binary when it is set to an existing path; otherwise the shell uses `ffmpeg` on `PATH`. A packaged app uses `process.resourcesPath/ffmpeg/ffmpeg` (`Contents/Resources/ffmpeg/ffmpeg`).

Build the bundled binary on an Apple Silicon Mac (no Homebrew libs linked into it):

```sh
npm run build:ffmpeg
```

That writes `build/ffmpeg/ffmpeg` and `build/ffmpeg/BUILD_INFO.txt` (pinned source SHA-256 and configure line). `npm run pack` copies `build/ffmpeg` into `Contents/Resources/ffmpeg`. Sign/notarize covers that binary with hardened runtime and no entitlements.

## Model weights

Dev mode uses the TotalSegmentator weights already on this Mac and does not download them.
A packaged app downloads the manifest in `weights-manifest.json` into
`<data root>/models/nnunet/results` before an Archive can be processed. Cases already stored
still open. Set `BRONCHOROUTE_FORCE_WEIGHTS=1` to show that download in dev, and
`BRONCHOROUTE_WEIGHTS_BASE_URL` to point it at a mirror other than the GitHub Release.

`scripts/mirror-weights.py --pack --dry-run` lists the datasets. `--pack` writes stored zips
and updates the manifest. `--upload` prints `gh release` commands and does not run them.

## Realism model

The realism model (`broncho-flow-int8.onnx`, 580 MB) downloads into
`<data root>/models/realism` from the Settings "Realistic fork views" row.
The download needs a per-user bearer key: "Model access key" in the same
Settings row, stored encrypted in the keychain-backed key store. Set
`BRONCHOROUTE_MODELS_BASE_URL` to download from a mirror other than the
GitHub Release. Dev/`npm start` can use a local pipeline tree's model file
directly; the download verifies size and SHA-256 before the file is used.

## Updates

A packaged app checks GitHub Releases for `valinagacevschi/BronchoRoute` once after the
viewer loads, only when Settings → "Check for updates at launch" is on (default).
Changing that switch applies on the next launch. Dev/`npm start` never checks.
When an update is found, a native dialog asks before downloading; after download,
another dialog asks before restarting to install. Offline or missing-release errors
are logged once and do not block the app.

`npm run pack` still builds the unpacked `dir` target with `--publish never`. Release
artifacts are arm64 `dmg` and `zip` (plus `dir` for smoke tests), named without spaces,
and must stay under 2 GiB.

## Signing and notarization

`npm run pack` leaves the app unsigned (`identity` is null). Sign that build on this Mac.

Install the Developer ID Application certificate for team `488B4W32U2`. An Apple Development certificate is not the signing identity. In Xcode: Settings → Accounts → Manage Certificates, and add Developer ID Application. Or create it at developer.apple.com, download it, and open it so it is in the login keychain.

Create a notarytool keychain profile yourself. The command prompts for an app-specific password; that password stays in the keychain.

```sh
xcrun notarytool store-credentials <profile> --apple-id <email> --team-id 488B4W32U2
```

From this directory, after `npm run build:python`, `npm run build:ffmpeg`, and `npm run pack`:

```sh
NOTARY_PROFILE=<profile> npm run sign
```

If `NOTARY_PROFILE` is unset, the script signs and verifies the app and DMG, then stops before notarizing. `npm run sign:adhoc` sets `IDENTITY=-`, signs ad-hoc, and does not build a DMG or notarize.

The packaged app is arm64-only and quits on any other architecture. `LSMinimumSystemVersion` is 14.0. The first launch on a Mac with less than 16 GB of memory shows a one-time warning.
