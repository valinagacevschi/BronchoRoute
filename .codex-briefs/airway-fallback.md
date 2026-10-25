# Brief: fix the TotalSegmentator airway fallback

Repo: /Users/val/dev/dicom, work only inside `pipeline/`. Python env: `cd pipeline && uv run ...`
(if the shell has PYTHONPATH set, use `env -u PYTHONPATH uv run ...`). Do NOT git add/commit.
Do NOT touch docs/, prototype/, data/, or existing files in out/ or ts_out/ other than new
subfolders described below.

## Background (verified facts)
- `pipeline/run_totalsegmentator.py` runs `--task lung_vessels` and expects
  `lung_trachea_bronchia.nii.gz`. In installed totalsegmentator 2.18 that class only exists in
  task `lung_vessels_LEGACY`. The current `lung_vessels` writes `lung_airways.nii.gz`,
  `lung_airways_wall.nii.gz`, `lung_arteries.nii.gz`, `lung_veins.nii.gz`. So the fallback is broken.
- Measured on AeroPath case 1 (`ts_out/airways_1/lung_airways.nii.gz`, already computed):
  Dice 0.858 vs expert; 135 connected components (largest = 99.1% of voxels); the upper trachea is
  missing (only 51% of the expert's top 30 mm covered).
- The `total` task has a `trachea` class; the script already runs `total` with `--roi_subset` for
  the five lobes.
- AeroPath cases live in `data/extracted/AeroPath/<n>/` (n=1..6): `<n>_CT_HR.nii.gz`,
  `<n>_CT_HR_label_airways.nii.gz` (expert). Existing lobe outputs: `ts_out/lobes_<n>/` and merged
  `ts_out/lobes_<n>.nii.gz` for some cases. `run_holdback.sh` shows the existing per-case loop and
  the 8-name completeness check (WANT list). `build_graph.py` accepts `--airways` and `--lobes`.
  `compare_segmentation.py truth pred` prints Dice + tree stats.
- Device on this Mac: `--device mps`.

## Decisions already made (implement exactly)
1. Airway fallback = `lung_vessels` → `lung_airways` (lumen). Keep `lung_airways_wall` on disk but
   unused.
2. Add `trachea` to the existing `total --roi_subset` call (alongside the five lobes). Keep the full
   trachea length the model gives (no cropping).
3. Combined airway mask = union(lung_airways, trachea), then keep only the largest 26-connected
   component. Write it as `<out>/airways.nii.gz` (uint8, affine/header of the lung_airways image;
   resample is not needed if shapes match — assert they do and fail loudly otherwise). Print voxel
   counts before/after and the number of components dropped.
4. `--legacy` flag on run_totalsegmentator.py: use `--task lung_vessels_LEGACY` and its
   `lung_trachea_bronchia.nii.gz` instead of `lung_airways` (same union + largest-component step).
5. Add `--device` (default `mps`) passed to every TotalSegmentator call, keep `--fast`.
   Skip a TotalSegmentator call when its expected output files already exist (cache), so reruns are
   cheap. Keep the `lobes.nii.gz` merge as is (must ignore the trachea file).
6. New `pipeline/eval_fallback.sh` (or .py, your call; match repo style): for cases 1..6,
   run run_totalsegmentator.py into `ts_out/fallback_<n>/`, then
   `build_graph.py data/extracted/AeroPath/<n> --airways ts_out/fallback_<n>/airways.nii.gz
   --lobes ts_out/fallback_<n>/lobes.nii.gz -o out/fallback`, plus compare_segmentation against the
   expert mask. Then print the table + per-structure naming grid like run_holdback.sh, reading
   `out/fallback/<n>_graph.json`.
   Pass rule per case: all 8 WANT names found. For each failing case, rerun with `--legacy` into
   `ts_out/fallback_legacy_<n>/` → `out/fallback_legacy/`, report it; if it still fails, print
   `MANUAL REVIEW: case <n>`. No region-growing fallback.
   Note: `build_graph.py` writes `<case>_graph.json` where case = case_dir name, so separate out dirs
   are required.
7. Update the module docstring of run_totalsegmentator.py to describe the new outputs.

## Verification you must do
- `python -m py_compile` on changed .py files.
- Run `run_totalsegmentator.py data/extracted/AeroPath/1/1_CT_HR.nii.gz -o ts_out/fallback_1`
  end to end and paste the output. You may reuse `ts_out/airways_1` only by copying, not by moving.
- Do NOT run the full 6-case eval (slow); I will. Tell me the exact command.

Finish with a short summary: files changed, commands run, outputs.
