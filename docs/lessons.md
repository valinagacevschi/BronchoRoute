<!-- Changes: PIL save to a .tmp path needs format="PNG" (#57). -->
<!-- Changes: realistic frame roll uses canvas rotate(-φ) to match applyRoll (#60). -->
<!-- Changes: desktop extraResources must list estimated_airway.py and realism/* (#63). -->
<!-- Changes: headless toBlob is rAF-throttled; video JPEG uses toDataURL (#45). -->
<!-- Changes: missing updates settings defaults on; unreadable settings skip the update check. -->
<!-- Changes: LGPL ffmpeg needs protocol fd as well as pipe for stdin `-`. -->
<!-- Changes: fly-through fork hold is look-ahead of the junction; roll easing ends at holdS. -->
<!-- Changes: fly-through fork/end frames need orifice face nudge and tip extrapolation; video HUD paints after letterbox at a 24 px inset. -->
<!-- Changes: fly-through roll uses the camera frame at the fork, not the 5 mm mean tangent. -->
<!-- Changes: public snapshot keeps renderer.html for the splice tests; scanner waives registry UIDs, the .999 test branch, path-redaction lines, the pygltflib URL, and "Patient Protocol". -->
<!-- Changes: TotalSegmentator weights path is nnUNet results; roi_subset still prefetches unused parts. -->
<!-- Changes: Record at a fixed 30 Hz and encode with h264_videotoolbox, not libx264. -->
<!-- Changes: topology estimated stubs sit on the parent ray, inside the node pick radius. -->
<!-- Changes: PNG stills redraw at 2× CSS size; strip text chunks; do not log the save path. -->
<!-- Changes: estimated airway is shifted into patient RAS and stored off the graph. -->
<!-- Changes: verify default viewer routes as well as selectable RouteTarget routes. -->
<!-- Changes: endoluminal Lesion label must clear the arrow and the visible edge. -->
<!-- Changes: endoluminal Lesion radius is the on-axis silhouette. -->
<!-- Changes: failure copy is one sentence; the override reason is the clinician's. -->
<!-- Changes: lesion slice distance follows the RAS crop slice increment. -->
<!-- Changes: outdated-Case prompt must not cover the footer hit targets. -->
<!-- Changes: retain Settings persistence and subprocess isolation lessons after rebase. -->
# Lessons Learned

- Realism PNG atomic write (#57): `Image.save(path)` picks the encoder from the
  suffix. A temp name ending in `.tmp` raises `unknown file extension: .tmp`.
  Pass `format="PNG"` (or use a `.png` temp suffix) before `os.replace`.
- Realistic fork roll (#60): zero-roll fisheye frames are drawn with
  `ctx.rotate(-φ)` (and the same matrix for orifice overlays). Positive `fly.phi`
  matches `applyRoll`; `+φ` alone puts the red-dot clock a quarter-turn off the
  HUD scope clock. Chromium still logs fetch 404 as a console "error" even when
  JS only checks `res.ok` — treat that network line as benign in Playwright.
- Bundle filters (#63): `extraResources` for `../pipeline` is an explicit file list — new pipeline modules (e.g. `estimated_airway.py`) are omitted until listed. `*.py` under `../app` is top-level only, so packages like `app/realism/` need their own `realism/*.py` / `realism/*.json` patterns.
- Offline video JPEG (#45): headless Chromium throttles `canvas.toBlob` to the background rAF rate (~1 fps), so Playwright offline exports took ~1 s/frame. `canvas.toDataURL("image/jpeg")` is synchronous and keeps Record / fly-through / orbit / spin at tens of ms/frame. `yieldEvery` lets tests skip most `yieldToEventLoop` ticks during bulk encode.
- Auto-update settings (#50): missing `app_state/settings.json` (or a
  missing `updates_enabled` key) means checks stay on. Unreadable or corrupt
  JSON means checks stay off for that launch — do not treat parse failure as
  the public default.
- Fly-through auto-align (#44): the scope rolls the sample frame at `fork.s`, but
  `fork.angle` is the 5 mm mean-tangent clock used by interactive guidance. Those
  diverge on a bend, so video roll uses a separate `align_angle` (camera-frame
  clock at the junction sample). `roll_profile` reads `align_angle`; interactive
  `fly.phi` never applies that profile. Video passes `{s, phi}` into
  `drawEndoluminal` and yields each frame. View switches stay enabled during
  export: frames are offscreen and do not write `fly.s` or `fly.phi`.   Spec §8 auto-aligns before each fork: the video hold is at
  `fork.s - FLYTHROUGH_FORK_LOOK_MM` (12 mm) so both child orifices stay in
  view; `roll_profile` finishes easing by that holdS and stays locked through
  the junction. A sibling behind the camera is omitted (never synthesised from
  the patient clock). Orifice centres pack from the rolled view-space XY.
  Snapshot orifices on the frame source before the JPEG await. At path end
  extend along the tip tangent. Video clocks paint after letterboxing at a
  24 px left inset.
- Public snapshot (#49): the server serves `renderer.template.html`, but
  `desktop/lib.test.js` and `test_template_splice` read `prototype/renderer.html`
  (that template plus the anonymized AeroPath graph, no name, UID, or date), so
  the snapshot keeps the file. The scanner has to waive DICOM root
  `1.2.840.10008`, the synthetic `.10.999` Series branch, `/Users/` only on
  lines that reject or redact absolute paths, the pygltflib `gitlab.com` URL,
  and the header-inventory description "Patient Protocol". A private host in
  code aborts; the only host comment is stripped from `pipeline/paths.py`.
- Weights mirror (#47): `TOTALSEG_WEIGHTS_PATH` is nnUNet's results directory
  (`models/nnunet/results`), not the models parent. TotalSegmentator 2.18.0
  `lung_vessels` loads Dataset117 and, because `robust_crop` is set, the 3 mm
  crop Dataset297. `total` with the five lobes plus trachea loads only organs
  part Dataset291; that roi crop uses the 6 mm Dataset298. `python_api.py`
  still downloads parts 292–295 before `nnunet.py` filters them, so those
  folders are fetched from upstream if they are absent.
- Bundled LGPL ffmpeg (#52): with `--disable-everything`, stdin `-` is the `fd`
  protocol, not `pipe`. Enable `--enable-protocol=fd` (and `file`/`pipe`) or
  `image2pipe` input fails with "Protocol not found".
- Live Record (#43): draw the still's CSS-sized view into a 1920×1080 or 1280×720
  frame and letterbox it, so the burn-in strings stay the same. Tick at 30 Hz on
  the wall clock and repeat the last JPEG when a draw is late; do not emit extra
  frames for one tick. Encode only with `h264_videotoolbox` (8M at 1080p, 4M at
  720p). An LGPL ffmpeg has no `libx264`. Dev resolves `BRONCHOROUTE_FFMPEG`, then
  `ffmpeg` on `PATH`; the packaged path is `Resources/ffmpeg/ffmpeg`.
- Estimated airway in topology and CT (#41): the stub continues along the parent
  node's screen ray, so part of it sits inside that node's 12 px pick radius.
  A click there selects nothing only when the stub is strictly closer than the
  node. Branch, generation, and centreline totals stay on payload edges. CT uses
  the same ±1 voxel slab as the centreline and adds no pick target.
- PNG stills (#42): redraw the current view into an offscreen canvas at 2× the
  CSS size with the same draw functions, then burn in. The anatomical scale bar
  uses look-at mm-per-pixel in that CSS space. Canvas PNGs can carry text
  chunks; keep only IHDR, IDAT, IEND, and optional sRGB/pHYs/gAMA. Never log
  the save path.

- Estimated airway (phase 5): the artery skeleton is kimimaro space. Apply the
  airway affine before pairing, shift the polyline by tip − artery start, and
  cut arc length at 15 mm — the walk can overshoot the cap by one skeleton
  step. Keep those polylines under `estimated`. Putting them in `edges` or
  `nodes` makes routing follow them.
- Bridge selectability does not protect the default viewer route: it can still
  follow a blocked branch before a RouteTarget exists. Stop that payload route at
  the first bridge boundary and test actual route geometry for every decision state.

- Endoluminal Lesion disc radius is the on-axis silhouette `F·R/sqrt(z²−R²)`; the glow
  stops at 1.25× that radius. Fit the arrow and label inside the canvas box clipped to
  the window, clear of the arrowhead, rail, clocks, and limitations panel. Clamping a
  label into `clientWidth` parks it on the arrowhead and on the last pixel.
- Processing failure is one sentence: `<step> step failed: <reason>. Try another Series`.
  The screen adds only the final period. All-zero lobe masks are "no lungs". A Series
  override stores the clinician's text; the ranking note stays in `status_reason`.
- Lesion off-slice distance is to the voxel-centre plane along the crop's slice
  increment (RAS: +axial superior, +coronal anterior, +sagittal patient right).
  The drawn radius is sqrt(r² − d²); a faded circle is the wrong glyph.
- Series selection: ranking by slice step before checking coverage can prefer a partial
  gated reconstruction. Classify eligibility first, use the first convolution-kernel
  value, and rank survivors independently of file discovery order. Keep the header
  inventory regression test to pin the recommendation and rejection reasons.
- Airway skeleton split (GitLab #14): kimimaro's soma handling (thresholds 0) treated the wide
  trachea as a cell body and cut the skeleton; keeping the largest component then dropped the
  left lung (44 % of vertices on the Bf37 test Case). Disable soma handling (thresholds `inf`),
  test graph connectivity and not only mask connectivity, and join any remaining skeleton hole
  only when every sample (≤ 0.25 mm apart) lies inside the airway mask; a real mask gap stays
  the bridge detector's job with `interpolated` provenance.
- Twig root (GitLab #14): the widest terminal can be a 0.8 mm twig when the tracheal cut face
  fans out. Root at the most superior vertex (world RAS z, full affine) with radius ≥ ½ max,
  and check root z above carina z on real data.
- Empty tree (GitLab #14): lung pruning can leave no branches. Stop before export with
  `graph: no airway tree in lung`; the job error names the graph step.
- Real-fixture graph tests (`test_graph_fixes.py`) skip unless `G14_FIXTURE_ROOT` points at a
  writable copy under `$TMPDIR` of the derived series-kernel fixtures (never the originals).
- Topology rotation: drawing and picking can drift if they transform coordinates
  separately. Rotate positions in their shared screen projection; draw glyphs
  without rotating the canvas. Use synthetic graphs and dynamic server ports in
  browser tests so local case availability and other worktrees cannot affect them.
- Feature flags must cover writes from shared edit hooks as well as HTTP routes.
  Hiding legacy review endpoints alone still lets Lesion and route edits create
  versions. Guard the shared version hooks and test both empty and frozen state
  with the flag off.
- Third-party notices (GitLab #46): PyPI `License` metadata is often `UNKNOWN`,
  a bare `BSD`, or the full licence text. The original 4-clause BSD is not
  GPL-compatible. Prefer `License-Expression`, then a known classifier, then the
  installed licence file, and leave a bare `BSD` with no clause count in the
  review section.
- Sidecar recovery must resolve saved job paths even if Case metadata is gone.
  Validate the saved Case key without requiring the Case to exist. Read the stdin
  watchdog with `os.read` so a blocked daemon cannot hold a buffered-reader lock
  during interpreter shutdown. Cover both with a real subprocess lifecycle test.
- Electron dev entry (GitLab #26): `electron .` puts the app directory in `argv`,
  so `require.main === module` is false and the shell never starts. Treat that
  directory as the entry, and launch with `electron .` as well as the smoke script.
- Endoluminal clock hours: a branch within about 9° of the view axis (in-plane
  fraction under 0.15, the same cut as `clock_of`) has no stable hour. That
  residual is the direction of a small miss, so call it straight ahead. The
  bronchus intermedius at the right-upper takeoff is that case; the carina is not.
- Shared settings: merging only the new API writes is insufficient when legacy
  clinician and CT preference writers replace the same file. Keep every writer
  under the same lock, preserve unknown keys, and test cross-API writes.
- Subprocesses do not inherit monkeypatched `paths.*`: pass the effective state root in the launcher environment, including for fake segmentation, or a passing request can overwrite real artifacts.
- Standalone Python packaging (#27): the frozen runtime includes source-only
  packages such as acvl-utils. Allow uv to build those locked releases rather
  than requiring wheels for every dependency; macOS markers still exclude CUDA.
- Packaged Python jobs (#27): imports and wrapper --help do not exercise child
  commands. Invoke Python CLIs with sys.executable and -m; console-script shebangs
  retain build paths. Smoke-check the child module with only system tools on PATH.
- Case Library (GitLab #31): the outdated-Case prompt cannot be a modal backdrop.
  Other viewer tests open fixtures with no pipeline_version, and a top-layer
  backdrop makes the footer About and Settings links fail hit-testing. Keep the
  prompt in the page, clear of the left rail and the switcher.
