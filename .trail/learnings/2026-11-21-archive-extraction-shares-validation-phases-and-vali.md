---
title: "Archive extraction shares validation phases, and upload scans must leave the async event loop"
author: Vali
date: 2026-11-21
branch: 74-viewer-demo-affordances
cost: { minutes: 16, tokens: 858300 }
id: l-a5ec8c07
scope: repo
status: active
src: agent
touches:
  - app/imports.py
  - app/server.py
  - pipeline/import_dicom.py
  - THIRD_PARTY_NOTICES.md
  - app/jobs.py
  - desktop/main.js
  - pipeline/pyproject.toml
  - pipeline/test_import_tracer.py
  - pipeline/uv.lock
  - prototype/import.js
---

## Decided
Keep one set of Archive rules for ZIP and RAR: member count/size caps, unsafe-name checks, junk filtering, DICOM pruning, and temp-root cleanup. The source-specific parts are only how bytes are materialized: ZIP streams each entry; RAR validates the member list, then runs one `bsdtar -T` extraction for the selected names only.

Archive kind is resolved once and passed down. The upload endpoint hands `scan_series` to `run_in_threadpool`; the path-based scan endpoint is already a sync handler and runs in the threadpool.

## Tried and ruled out
Extracting every RAR member and filtering afterwards: wastes writes on `.app`, html and executable members. `bsdtar -T` with `--null` and a NUL-separated name list extracts only candidates.

Per-member `rarfile.open()` extraction: one bsdtar process per entry, and the test archive has 5,920 entries.

Keeping a second path-jail helper in the import module: reuse `path_under_root` instead.

## Watch out
- `path_under_root` must resolve the root too. On macOS `/tmp` is a symlink to `/private/tmp`; resolving only the candidate makes every RAR output path look outside the jail.
- `MAX_ARCHIVE_BYTES` is still checked from declared RAR sizes and after extraction, not while bsdtar writes. A lying header can exceed the cap on disk before the check runs. ZIP enforces the cap per chunk.
- No automated success-path RAR test yet; manual scan used `CT-Scan.rar` (25 Series, 2,860 files, ~25 s).

In the code:
- app/imports.py `_validate_members`, `_extract_zip`, `_extract_rar`, `_load_source`
- app/server.py `/api/imports/upload` uses `run_in_threadpool`
- pipeline/import_dicom.py `path_under_root`
