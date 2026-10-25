# Brief: reconnect near airway pieces + overlay review images

Repo /Users/val/dev/dicom, work only in `pipeline/`. Run Python with `env -u PYTHONPATH uv run ...`
from `pipeline/`. Do NOT git add/commit. Do not touch docs/, prototype/, data/.

## Context (verified)
`run_totalsegmentator.py::make_airway` unions `lung_airways` + `trachea` and keeps the largest
26-connected component. On AeroPath case 6 that drops the whole right-middle-lobe airway: a 1,184-voxel
piece whose nearest voxel is 3.3 mm from the main tree. Measured detached pieces (voxels, gap mm):
case 6: (1930, 3.6), (1184, 3.3), (502, 2.0), (496, 1.6), (328, 2.6), (272, 6.3), (160, 13.4);
case 1: (218, 8.0), (168, 1.5), (112, 28.4); case 3: (288, 1.7), (80, 2.7), (24, 20.1).
Spacing is anisotropic (e.g. 0.684 x 0.684 x 0.5 mm) — measure in mm via the affine zooms.

## Task 1 — bridging in make_airway (decision approved)
After labelling, instead of discarding every non-largest component:
- Constants at module top: `BRIDGE_MAX_GAP_MM = 4.0`, `BRIDGE_MIN_VOLUME_MM3 = 20.0`.
- Greedy loop: tree = largest component. Repeatedly find the eligible detached component
  (volume >= min) with the smallest mm gap to the current tree (EDT of ~tree with `sampling=spacing`);
  if gap <= max, attach it: find the closest voxel pair (piece voxel, tree voxel), rasterise a
  straight cylinder between them with radius 1.0 mm (in mm, respect anisotropy) into the mask, add
  the piece. Recompute and continue, so pieces can chain via newly attached pieces. Stop when none
  qualifies. Everything not attached is dropped as before.
- Keep it reasonably fast on 512x512x~800 volumes (crop to a bounding box around candidates /
  compute EDT once per attach is fine; there are < 100 components).
- Print: bridged N pieces (list volume mm3 + gap mm each), dropped M, retained voxels.
- Final mask must be a single 26-connected component — assert it.

## Task 2 — eval reruns cheaply
Re-running `eval_fallback.py` must reuse cached TotalSegmentator outputs (it already does) and just
rebuild airways.nii.gz / graphs. No change needed unless something breaks; verify.

## Task 3 — overlay review images (new `pipeline/render_overlays.py`)
For a case n: load CT `data/extracted/AeroPath/<n>/<n>_CT_HR.nii.gz`, expert mask
`..._label_airways.nii.gz`, fallback mask `ts_out/fallback_<n>/airways.nii.gz`.
Write PNGs to `out/overlays/<n>/`: 6 coronal slices evenly spaced through the expert-mask
extent + 3 axial slices through the lungs; plus one coronal maximum-intensity projection of both masks.
Window CT at lung window (W 1500 / L -600), grey. Colours: both = yellow, fallback-only = red,
expert-only = cyan, semi-transparent. Correct physical aspect ratio (use voxel spacing), patient
superior at top. Small text label with case, plane, slice index, and legend. Use PIL + numpy only
(PIL is already importable; check `uv run python -c "import PIL"`); no matplotlib unless it is
already installed.
CLI: `render_overlays.py 3 4 6`.

## Verify
- py_compile changed files.
- `uv run python run_totalsegmentator.py data/extracted/AeroPath/6/6_CT_HR.nii.gz -o ts_out/fallback_6`
  and paste the bridging output.
- `uv run python render_overlays.py 3 4 6` and list the files.
- Do NOT run the full eval; I will.
Short summary at the end.
