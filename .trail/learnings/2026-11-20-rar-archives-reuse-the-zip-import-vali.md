---
title: RAR archives reuse the ZIP import path by sniffing file signatures and extracting once with bsdtar
author: Vali
date: 2026-11-20
branch: 74-viewer-demo-affordances
cost: { minutes: 4, tokens: 285144 }
id: l-56c438ea
scope: repo
status: active
src: agent
touches:
  - app/imports.py
  - desktop/main.js
  - prototype/import.js
  - prototype/renderer.template.html
  - prototype/renderer.html
  - pipeline/test_import_tracer.py
  - app/jobs.py
  - app/server.py
  - pipeline/pyproject.toml
---

## Decided
Support RAR in the same Archive import path as ZIP. Python `rarfile` parses headers only; extraction runs `/usr/bin/bsdtar` once per archive, then the temporary tree is filtered to files pydicom accepts as DICOM.

## Tried and ruled out
Per-member `RarFile.open()` extraction: rarfile spawns one bsdtar subprocess per entry. The test CT archive had 5,920 entries, so this would rescan the 585 MB archive thousands of times. One `bsdtar -x -f ARCHIVE -C DIR` call is fast enough (24 s end to end).

Relying on the upload temp suffix: browser uploads land in a `.zip` temp file even for RAR content. Dispatch must use magic bytes first, suffix only as fallback.

## Watch out
- RAR extraction needs an external tool. macOS ships `/usr/bin/bsdtar`; do not assume `unrar` exists.
- libarchive/bsdtar cannot read solid RAR archives. This test archive was not solid. A solid archive needs another backend.
- Unsafe path, absolute path, drive prefix, and symlink entries must be rejected before extraction, matching the ZIP guard.

In the code:
- app/imports.py `_archive_kind`, `_extract_rar`, `_safe_member`
- app/server.py `/api/imports/upload` keeps a `.zip` temp suffix
- pipeline/pyproject.toml `rarfile`
