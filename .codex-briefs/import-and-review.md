# Brief: DICOM import script, small-lobe warning, fallback-only overlays

Repo /Users/val/dev/dicom, work only in `pipeline/`. Run Python with `env -u PYTHONPATH uv run ...`
from `pipeline/` (pydicom 3 and SimpleITK are already in the venv via totalsegmentator; check, and
add them to pyproject.toml dependencies explicitly with `uv add` if you import them directly).
Do NOT git add/commit. Do not touch docs/, prototype/, data/extracted/AeroPath/.

## PATIENT DATA RULE (hard)
Source DICOM contains real patient identifiers. Never print, log, write or embed PatientName,
PatientID, PatientBirthDate, InstitutionName, AccessionNumber, ReferringPhysicianName, StudyDate,
or any UID into any file, stdout, or test fixture. Allowed series facts: SeriesNumber,
SeriesDescription, Modality, SOP class name, ConvolutionKernel, SliceThickness, spacing, size,
ImageOrientation-derived plane, file count. Output images: NIfTI only (no patient tags).

## Task 1 — `import_dicom.py <src_dir> [--id local-NNN] [--series N] [--dry-run]`
- Walk every folder under src_dir recursively; group files by SeriesInstanceUID (unreadable /
  non-DICOM files counted and skipped). Print one row per series with the allowed facts.
- Candidate = Modality CT, CT Image Storage (not secondary capture/SR), axial orientation
  (normal ~ ±z), >= 100 slices, uniform slice spacing (tolerance 0.05 mm), not a scout/localizer.
- Pick: thinnest slice spacing; tie-break prefer kernel STANDARD/SOFT/B30-ish over LUNG/BONE
  (lung-kernel series failed naming on our test patient), then non-contrast description
  (Nativ/native/non-contrast) over arterial/venous, then most slices. `--series N` overrides.
  Print the pick and, for each other series, a one-line reason it was skipped.
- Case id: `--id` or next free `local-NNN` under `data/extracted/local/`.
- Write `data/extracted/local/<id>/<id>_CT.nii.gz` (SimpleITK ImageSeriesReader) and
  `<id>/case.json` with: id, source series number + description + kernel + spacing + size,
  skipped series list with reasons, import tool version/date of import (today's date is fine; NOT
  the study date). No identifiers.
- After writing, assert the NIfTI header descrip/aux_file/intent_name are empty.
- Migrate the existing manual import without re-reading DICOM: `data/extracted/local/local-001/`
  holds `local-001_std.nii.gz` (SERIES4 "TAP Nativ 1.25mm", STANDARD, 1.25 mm, 512x512x485) — the
  chosen primary — and `local-001_lung.nii.gz` (SERIES2 "Lung", LUNG, 512x512x291) kept for
  comparison only. Rename std -> `local-001_CT.nii.gz`, move lung to
  `local-001/alt/local-001_lung.nii.gz`, write case.json by hand-coded values above (note
  "migrated from manual import"). Also rename `ts_out/local-001_std` -> `ts_out/local-001` and
  `out/local-001_std` -> `out/local-001` if present.
- Then verify by running `import_dicom.py ~/Downloads/dicom --dry-run`: it must pick SERIES4
  (series number 4). Paste the output (it contains no identifiers by construction — double check).

## Task 2 — small-lobe warning in `build_graph.py`
When `--lobes` is given, compute each lobe's volume in ml (voxel volume from the lobe image zooms).
If any lobe < 25% of the median lobe volume, print
`  ** small lobe: <name> <ml> ml vs median <ml> ml — clinical review before use`
and add `"review": [{"kind": "small_lobe", "lobe": name, "ml": x, "median_ml": y}]` to the
exported graph JSON (key present always, empty list when clean). Do not change naming or
anything else. Also add the existing "no airways reach <lobe>" condition to the same `review`
list as `{"kind": "lobe_without_airways", "lobe": name}`.

## Task 3 — `render_overlays.py` without an expert mask
Accept case ids that are not AeroPath numbers: `render_overlays.py 3 4 local-001`. Resolve
CT/expert/fallback paths: AeroPath n -> as now; `local-*` -> CT `data/extracted/local/<id>/<id>_CT.nii.gz`,
fallback `ts_out/<id>/airways.nii.gz`, no expert. With no expert, draw the fallback alone in red
and say "fallback only (no expert mask)" in the legend; slice placement uses the fallback mask
extent. Output `out/overlays/<id>/`.

## Verify
- py_compile; existing `test_bridge.py` still passes.
- Task 1 dry-run output pasted; migration done (ls the tree).
- `build_graph.py data/extracted/local/local-001 --airways ts_out/local-001/airways.nii.gz
  --lobes ts_out/local-001/lobes.nii.gz -o out/local-001` → paste output; must print the small-lobe
  warning for lung_middle_lobe_right. Also run build_graph for AeroPath case 3 with
  `--airways ts_out/fallback_3/airways.nii.gz --lobes ts_out/fallback_3/lobes.nii.gz -o out/fallback`
  and confirm no small-lobe warning (and 8 named airways unchanged).
  Note build_graph globs `*label_airways` only when --airways is absent; the local case has none.
- `render_overlays.py local-001` and list files.
Short summary at the end.
