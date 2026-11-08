"""Shared atomic bin+meta publish and size-checked read for CT/mask crop caches.

Phase 3 review: one helper for ct_crop and masks (header sha from meta; corrupt → rebuild).
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Callable


def publish_binary_pair(
    bin_path: Path,
    meta_path: Path,
    raw: bytes,
    meta: dict,
    *,
    bin_prefix: str,
    meta_prefix: str,
) -> dict:
    """Write bin then meta (meta carries sha256); publish each via replace."""
    bin_path.parent.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256(raw).hexdigest()
    tmp_bin_name: str | None = None
    tmp_meta_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=bin_path.parent,
            prefix=bin_prefix,
            suffix=".tmp",
            delete=False,
        ) as tmp_bin:
            tmp_bin.write(raw)
            tmp_bin.flush()
            os.fsync(tmp_bin.fileno())
            tmp_bin_name = tmp_bin.name
        os.replace(tmp_bin_name, bin_path)
        tmp_bin_name = None
        published = dict(meta)
        published["sha256"] = digest
        published["bin_size"] = len(raw)
        published["bin_mtime_ns"] = bin_path.stat().st_mtime_ns
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=meta_path.parent,
            prefix=meta_prefix,
            suffix=".tmp",
            delete=False,
        ) as tmp_meta:
            json.dump(published, tmp_meta, indent=2)
            tmp_meta.write("\n")
            tmp_meta.flush()
            os.fsync(tmp_meta.fileno())
            tmp_meta_name = tmp_meta.name
        os.replace(tmp_meta_name, meta_path)
        tmp_meta_name = None
        return published
    finally:
        for name in (tmp_bin_name, tmp_meta_name):
            if name is not None:
                try:
                    os.unlink(name)
                except OSError:
                    pass


def read_meta_json(meta_path: Path) -> dict | None:
    """Load meta JSON; None if missing or corrupt (caller rebuilds)."""
    if not meta_path.is_file():
        return None
    try:
        return json.loads(meta_path.read_text())
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError, TypeError):
        return None


def read_cached_binary_pair(
    case_id: str,
    bin_path: Path,
    meta_path: Path,
    *,
    ensure_meta: Callable[[str], dict],
    force_rebuild: Callable[[str], dict],
    expected_nbytes: Callable[[dict], int],
) -> tuple[bytes, dict]:
    """Ensure meta (under its own lock), then read bytes; rebuild on size/corrupt meta."""
    meta = ensure_meta(case_id)
    if read_meta_json(meta_path) is None:
        meta = force_rebuild(case_id)
    raw = bin_path.read_bytes()
    expected = meta.get("bin_size")
    want = expected if expected is not None else expected_nbytes(meta)
    if len(raw) != int(want):
        meta = force_rebuild(case_id)
        raw = bin_path.read_bytes()
    return raw, meta
