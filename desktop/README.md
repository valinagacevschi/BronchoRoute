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

Live Record writes MP4 H.264 with ffmpeg's `h264_videotoolbox` encoder. An LGPL ffmpeg build has no `libx264`, so the encoder name stays `h264_videotoolbox`. In development, `BRONCHOROUTE_FFMPEG` selects the binary when it is set to an existing path; otherwise the shell uses `ffmpeg` on `PATH`. A packaged app uses `process.resourcesPath/ffmpeg/ffmpeg` (`Contents/Resources/ffmpeg/ffmpeg`). Bundling that LGPL binary is a later packaging step. `encoder.js` has to be added to the electron-builder file list at the same time.

## Signing and notarization

`npm run pack` leaves the app unsigned (`identity` is null). Sign that build on this Mac.

Install the Developer ID Application certificate for team `488B4W32U2`. An Apple Development certificate is not the signing identity. In Xcode: Settings → Accounts → Manage Certificates, and add Developer ID Application. Or create it at developer.apple.com, download it, and open it so it is in the login keychain.

Create a notarytool keychain profile yourself. The command prompts for an app-specific password; that password stays in the keychain.

```sh
xcrun notarytool store-credentials <profile> --apple-id <email> --team-id 488B4W32U2
```

From this directory, after `npm run build:python` and `npm run pack`:

```sh
NOTARY_PROFILE=<profile> npm run sign
```

If `NOTARY_PROFILE` is unset, the script signs and verifies the app and DMG, then stops before notarizing. `npm run sign:adhoc` sets `IDENTITY=-`, signs ad-hoc, and does not build a DMG or notarize.

The packaged app is arm64-only and quits on any other architecture. `LSMinimumSystemVersion` is 14.0. The first launch on a Mac with less than 16 GB of memory shows a one-time warning.
