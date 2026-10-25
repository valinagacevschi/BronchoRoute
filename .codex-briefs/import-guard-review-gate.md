# Brief: duplicate-import guard + review gate on export

Repo /Users/val/dev/dicom, work only in `pipeline/`. `env -u PYTHONPATH uv run ...` from `pipeline/`.
Do NOT git add/commit. Same PATIENT DATA RULE as `.codex-briefs/import-and-review.md` (never print
or write identifiers or raw UIDs). Another agent recently edited `make_prototype_data.py`,
`route_frame.py`, `prototype_data.js`; keep their changes intact and touch `make_prototype_data.py`
only as described in Task 2.

## Task 1 — duplicate guard in import_dicom.py
- `source.series_uid_sha256` = hex SHA-256 of the SeriesInstanceUID string (utf-8). Store it in
  case.json; never store or print the UID itself.
- Before importing, scan `data/extracted/local/*/case.json`; if any has the same hash, print
  `already imported as <id> (folder <folder> / SeriesNumber <n>)` and exit 0 without writing.
  Same check in `--dry-run` (print what would happen). `--force` bypasses it.
- Backfill local-001: add `series_uid_sha256` for folder SERIES4 (SeriesNumber 5) by reading only
  that series' first DICOM header from `~/Downloads/dicom/SERIES4`. Do it with a small
  `--backfill-hash <case-id> <src_dir> <series>` option (or a one-off function) — reuse the
  importer's series grouping so the hash is computed identically.
- Files that are DICOM but not CT Image Storage (secondary capture, SR) are currently lumped into
  "unreadable/non-DICOM". Report them per series as `not a CT image (<SOP class name>)`; keep the
  unreadable count for truly unreadable files only.

## Task 2 — review gate
`build_graph.py` exports `"review": [...]`. In `make_prototype_data.py` (the learner-facing
export), refuse a graph whose `review` list is non-empty: exit non-zero with the review entries
printed, unless `--allow-review` is passed (then print a loud warning and embed
`"review": [...]` in the output so the renderer could show it). Graphs without a `review` key
(older files) are treated as unreviewed-but-clean: print a one-line note, proceed.

## Verify
- py_compile; `pytest test_bridge.py test_route_frame.py` pass (with `--with pytest`).
- `import_dicom.py ~/Downloads/dicom --dry-run` → must say already imported as local-001. Paste.
- `make_prototype_data.py out/local-001/local-001_graph.json ...` (check its CLI) → refused.
  Same on `out/fallback/3_graph.json` → succeeds (write the output to a scratch path under
  `out/`, do not overwrite `prototype_data.js`). Paste both.
Short summary.
