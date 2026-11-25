# Changes: list, size, delete, and Series-key lookup for the Case Library.
"""Local Case Library: three directories per Case, no source DICOM, no patient fields."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import paths
from app.caseref import CASE_ID_RE, CaseRef, UnknownCase
from app.jobs import JobConflict, job_in_progress

# Spec warns above 10 GB. Strictly greater, so tests can patch this constant.
LIBRARY_WARN_BYTES = 10 * 1024 ** 3


def _directory_bytes(path: Path) -> int:
    """Byte size of files under path. Symlinks are not followed."""
    if path.is_symlink() or not path.is_dir():
        return 0
    total = 0
    for item in path.rglob("*"):
        if item.is_symlink() or not item.is_file():
            continue
        try:
            total += item.stat().st_size
        except OSError:
            continue
    return total


def _local_records() -> list[tuple[str, dict]]:
    """Finished local Cases. Unfinished imports stay out of the library."""
    root = paths.LOCAL_CASES
    if not root.is_dir():
        return []
    rows: list[tuple[str, dict]] = []
    for case_path in sorted(root.glob("*/case.json")):
        case_id = case_path.parent.name
        if not case_id.startswith("local-") or not CASE_ID_RE.fullmatch(case_id):
            continue
        try:
            record = json.loads(case_path.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(record, dict) or record.get("import_processing"):
            continue
        rows.append((case_id, record))
    return rows


def _case_dirs(case_id: str) -> tuple[tuple[Path, Path], ...]:
    return (
        (paths.LOCAL_CASES, paths.LOCAL_CASES / case_id),
        (paths.TS_OUT, paths.TS_OUT / case_id),
        (paths.OUT, paths.OUT / case_id),
    )


def _remove_case_tree(case_id: str) -> None:
    for root, path in _case_dirs(case_id):
        if path.is_symlink():
            path.unlink()
            continue
        if not path.exists():
            continue
        if not root.exists():
            continue
        resolved = path.resolve()
        root_resolved = root.resolve()
        inside = resolved == root_resolved or root_resolved in resolved.parents
        if not inside:
            raise ValueError("refusing to delete outside the case directory")
        shutil.rmtree(path)


def _source(record: dict) -> dict:
    source = record.get("source")
    if isinstance(source, dict):
        return source
    return {}


def list_library() -> dict:
    cases = []
    total = 0
    for case_id, record in _local_records():
        source = _source(record)
        size = sum(_directory_bytes(path) for _root, path in _case_dirs(case_id))
        total += size
        processed = record.get("processed_at")
        cases.append({
            "id": case_id,
            "study_date": str(source.get("study_date") or ""),
            "series_description": str(source.get("series_description") or ""),
            "processed_at": processed if isinstance(processed, str) and processed else None,
            "size_bytes": size,
            "outdated": record.get("pipeline_version") != paths.PIPELINE_VERSION,
        })
    return {
        "cases": cases,
        "total_bytes": total,
        "warn": total > LIBRARY_WARN_BYTES,
    }


def cases_by_series_key() -> dict[str, str]:
    """Map Series hash to the first library Case id. Unfinished imports are absent."""
    found: dict[str, str] = {}
    for case_id, record in _local_records():
        key = _source(record).get("series_uid_sha256")
        if isinstance(key, str) and key and key not in found:
            found[key] = case_id
    return found


def delete_case(case_id: str) -> dict:
    if job_in_progress():
        raise JobConflict(case_id)
    ref = CaseRef.parse(case_id)
    if ref.kind != "local":
        raise UnknownCase(f"unknown case: {case_id}")
    _remove_case_tree(case_id)
    return {"deleted": [case_id]}


def delete_library() -> dict:
    if job_in_progress():
        raise JobConflict("library")
    ids = [case_id for case_id, _record in _local_records()]
    for case_id in ids:
        _remove_case_tree(case_id)
    return {"deleted": ids}
