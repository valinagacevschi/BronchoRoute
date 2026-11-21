# Changes: save the clinician's Series override reason, not the classifier note.
# Changes: scan marks a Series already in the Case Library.
# Changes: bounded Archive extraction, opaque sessions and identifier-free import results.
# Changes: expose Series eligibility, kernel class and coverage in scan responses.
# Changes: log import lifecycle without Archive paths or Series selections.
# Changes: accept RAR Archives alongside ZIP, matched by file signature.
"""Safe Archive sessions and confirmed Series import for the desktop app."""

from __future__ import annotations

import logging
import shutil
import secrets
import stat
import subprocess
import tempfile
import threading
import zipfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import PurePosixPath, PureWindowsPath
from pathlib import Path
from typing import Literal

import numpy as np
import rarfile

import paths
from atomic_json import atomic_write_json
from import_dicom import (
    build_case_document,
    classify_series,
    duplicate_message,
    next_case_id,
    path_under_root,
    pick_series,
    read_series,
    write_ct_nifti,
)

from app.cases import append_record
from app.workflow import WorkflowStep


class ImportDuplicate(Exception):
    """Series already imported; message matches CLI dup-guard wording."""

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


MAX_ARCHIVE_BYTES = 4 * 1024 ** 3
MAX_ARCHIVE_FILES = 20_000
MAX_OVERRIDE_REASON = 200
_sessions: dict[str, tuple[Path, bool]] = {}
_sessions_lock = threading.Lock()
_confirm_lock = threading.Lock()


def validate_src_dir(src_dir: str | Path) -> Path:
    path = Path(src_dir).expanduser().resolve()
    if not path.is_dir():
        raise ValueError("Archive folder does not exist")
    return path


ArchiveKind = Literal["zip", "rar"]


def _archive_kind(archive: Path) -> ArchiveKind | None:
    """Identify an Archive by its first bytes, or fall back to the suffix."""
    try:
        with archive.open("rb") as source:
            head = source.read(8)
    except OSError:
        return None
    if head.startswith(b"Rar!\x1a\x07"):
        return "rar"
    if head.startswith((b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08")):
        return "zip"
    return {".rar": "rar", ".zip": "zip"}.get(archive.suffix.lower())


def _safe_member(name: str, *, is_link: bool) -> bool:
    name = name.replace("\\", "/")
    return (PurePosixPath(name).is_absolute() or PureWindowsPath(name).drive
            or ".." in PurePosixPath(name).parts or is_link)


_JUNK_NAMES = {"autorun.inf", ".ds_store"}
_JUNK_SUFFIXES = {".exe", ".dll", ".bat", ".cmd"}


def _skip_entry(name: PurePosixPath) -> bool:
    return ("__MACOSX" in name.parts or name.name.lower() in _JUNK_NAMES
            or name.suffix.lower() in _JUNK_SUFFIXES)


def _keep_dicom(target: Path) -> None:
    import pydicom

    try:
        ds = pydicom.dcmread(target, stop_before_pixels=True)
        if not getattr(ds, "SeriesInstanceUID", None):
            target.unlink()
    except Exception:
        target.unlink(missing_ok=True)


def _validate_members(members: list[tuple[str, int, bool]]) -> None:
    if len(members) > MAX_ARCHIVE_FILES:
        raise ValueError("Archive file-count limit exceeded")
    if sum(size for _name, size, _is_link in members) > MAX_ARCHIVE_BYTES:
        raise ValueError("Archive size limit exceeded")
    for name, _size, is_link in members:
        if _safe_member(name, is_link=is_link):
            raise ValueError("unsafe Archive entry")


@contextmanager
def _extraction_root() -> Iterator[Path]:
    paths.TMP_DIR.mkdir(parents=True, exist_ok=True)
    root = Path(tempfile.mkdtemp(prefix="import-", dir=paths.TMP_DIR))
    try:
        yield root
    except Exception as exc:
        shutil.rmtree(root, ignore_errors=True)
        if isinstance(exc, ValueError):
            raise
        raise ValueError("Archive could not be read") from None


def _extract_zip(archive: Path) -> Path:
    """Validate every member before writing; never retain Archive member names."""
    with _extraction_root() as root:
        with zipfile.ZipFile(archive) as source:
            members = source.infolist()
            _validate_members([
                (member.filename, member.file_size,
                 stat.S_ISLNK(member.external_attr >> 16))
                for member in members
            ])
            total = 0
            for index, member in enumerate(members):
                name = PurePosixPath(member.filename.replace("\\", "/"))
                if member.is_dir() or _skip_entry(name):
                    continue
                target = root / f"{index:05d}.dcm"
                with source.open(member) as src, target.open("wb") as dst:
                    while chunk := src.read(1024 * 1024):
                        total += len(chunk)
                        if total > MAX_ARCHIVE_BYTES:
                            raise ValueError("Archive size limit exceeded")
                        dst.write(chunk)
                _keep_dicom(target)
        return root


def _extract_rar(archive: Path) -> Path:
    """Validate RAR members, then extract the wanted entries once with bsdtar."""
    tool = shutil.which("bsdtar")
    if tool is None:
        raise ValueError("RAR support is unavailable")
    with _extraction_root() as root:
        with rarfile.RarFile(archive) as source:
            members = source.infolist()
            _validate_members([
                (member.filename, int(member.file_size or 0), member.is_symlink())
                for member in members
            ])
            selected = [
                member.filename for member in members
                if not member.is_dir()
                and not _skip_entry(PurePosixPath(member.filename.replace("\\", "/")))
            ]
        raw = root / "raw"
        raw.mkdir()
        listing = root / "members"
        listing.write_bytes(b"".join(
            name.encode("utf-8", "surrogateescape") + b"\0" for name in selected
        ))
        result = subprocess.run(
            [tool, "-x", "--no-same-owner", "--null", "-f", str(archive),
             "-C", str(raw), "-T", str(listing)],
            capture_output=True, text=True,
        )
        listing.unlink(missing_ok=True)
        if result.returncode != 0:
            raise ValueError("Archive could not be read")
        total = 0
        for index, path in enumerate(sorted(p for p in raw.rglob("*") if p.is_file())):
            if path.is_symlink() or not path_under_root(path, raw):
                raise ValueError("unsafe Archive entry")
            total += path.stat().st_size
            if total > MAX_ARCHIVE_BYTES:
                raise ValueError("Archive size limit exceeded")
            target = root / f"{index:05d}.dcm"
            path.rename(target)
            _keep_dicom(target)
        shutil.rmtree(raw, ignore_errors=True)
        return root


def _extract_kind(archive: Path, kind: ArchiveKind) -> Path:
    if kind == "rar":
        return _extract_rar(archive)
    return _extract_zip(archive)


def extract_archive(archive: Path) -> Path:
    kind = _archive_kind(archive)
    if kind is None:
        raise ValueError("Archive could not be read")
    return _extract_kind(archive, kind)


def _load_source(source: Path) -> tuple[Path, bool]:
    """Return the DICOM root and whether a temporary Archive was extracted."""
    if not source.is_file():
        return validate_src_dir(source), False
    kind = _archive_kind(source)
    if kind is not None:
        return _extract_kind(source, kind), True
    folder = source.parent if source.name.upper() == "DICOMDIR" else source
    return validate_src_dir(folder), False


def release_import(import_id: str) -> None:
    with _sessions_lock:
        session = _sessions.pop(import_id, None)
    if session and session[1]:
        shutil.rmtree(session[0], ignore_errors=True)


def _clean_override_reason(value: str) -> str:
    text = value.strip()
    if len(text) > MAX_OVERRIDE_REASON:
        raise ValueError("override reason must be at most 200 characters")
    return text


def import_and_process(
    import_id: str, series_key: str, clinician: str, override_reason: str = "",
) -> dict:
    from app.jobs import start_segment_job

    reason = _clean_override_reason(override_reason)
    with _confirm_lock:
        with _sessions_lock:
            session = _sessions.get(import_id)
        if session is None:
            raise ValueError("Archive session expired; open the Archive again")
        case_id = None
        try:
            result = confirm_import(
                session[0], series_key, clinician=clinician, override_reason=reason,
            )
            case_id = result["case_id"]
            case_path = paths.LOCAL_CASES / case_id / "case.json"
            import json
            document = json.loads(case_path.read_text())
            document["import_processing"] = True
            atomic_write_json(case_path, document)
            result["job"] = start_segment_job(
                case_id, clinician, import_id=import_id,
            )
            return result
        except Exception:
            release_import(import_id)
            if case_id:
                shutil.rmtree(paths.LOCAL_CASES / case_id, ignore_errors=True)
            raise


def _public_row(row: dict, *, skip: str | None, proposed: bool) -> dict:
    return {
        "series_key": row["series_uid_sha256"],
        "series_number": row["number"],
        "description": row["description"],
        "kernel": row["kernel"],
        "modality": row["modality"],
        "slice_thickness_mm": round(float(row["slice_thickness"]), 2),
        "spacing_mm": round(float(row["spacing"]), 2),
        "axial": bool(row["axial"]),
        "size": list(row["size"]),
        "count": int(row["count"]),
        "status": row["status"],
        "reason": row["reason"],
        "kernel_class": row["kernel_class"],
        "contrast": row["contrast"],
        "z_span_mm": row["z_span_mm"],
        "skip_reason": skip,
        "proposed": proposed,
    }


def scan_series(path: str | Path) -> dict:
    """Return approved Series facts, grouped by opaque Study keys."""
    source = Path(path).expanduser().resolve()
    root, extracted = _load_source(source)
    try:
        series, unreadable = read_series(root)
        if not series:
            raise ValueError("no CT Series found")
        rows = classify_series(list(series.values()))
        from app.library import cases_by_series_key
        known = cases_by_series_key()
        table = [_public_row(row,
                             skip=row["reason"] if row["status"] == "not_usable" else None,
                             proposed=row["status"] == "recommended")
                 for row in sorted(rows, key=lambda row: row["number"])]
        for public in table:
            public["in_library"] = known.get(public["series_key"])
        studies = {}
        for row, public in zip(sorted(rows, key=lambda row: row["number"]), table):
            key = row.get("study_key", "unknown")
            study = studies.setdefault(key, {
                "study_key": key, "date": row.get("study_date", ""),
                "description": row.get("study_description", ""), "series": [],
            })
            study["series"].append(public)
        import_id = secrets.token_urlsafe(24)
        with _sessions_lock:
            _sessions[import_id] = (root, extracted)
        return {
            "import_id": import_id, "unreadable_files": unreadable,
            "proposed": next((row for row in table if row["proposed"]), None),
            "series": table, "studies": list(studies.values()),
        }
    except Exception:
        if extracted:
            shutil.rmtree(root, ignore_errors=True)
        raise


def find_duplicate(series_uid_sha256: str, chosen: dict) -> str | None:
    """Return the CLI-identical duplicate message, or None."""
    local_root = paths.LOCAL_CASES
    if not local_root.is_dir():
        return None
    for case_path in sorted(local_root.glob("*/case.json")):
        try:
            import json
            existing = json.loads(case_path.read_text())
            existing_hash = existing.get("source", {}).get("series_uid_sha256")
        except (OSError, json.JSONDecodeError):
            continue
        if existing_hash == series_uid_sha256:
            return duplicate_message(
                existing.get("id", case_path.parent.name), chosen
            )
    return None


def confirm_import(
    src_dir: str | Path,
    series: str | int,
    *,
    clinician: str,
    confirm: bool = True,
    force: bool = False,
    override_reason: str = "",
) -> dict:
    """Import the chosen series and append series_confirmed."""
    if not confirm:
        raise ValueError("confirm must be true to import")
    root = validate_src_dir(src_dir)
    override = str(series)
    logging.getLogger(__name__).info("import confirmation started")
    series_map, _unreadable = read_series(root)
    try:
        _uid, chosen, reasons = pick_series(series_map, override)
    except SystemExit as exc:
        raise ValueError(str(exc) or "series not found") from exc

    dup = None if force else find_duplicate(chosen["series_uid_sha256"], chosen)
    if dup:
        raise ImportDuplicate(dup)

    # Reason rules run before next_case_id so a refusal leaves no Case directory.
    text = _clean_override_reason(override_reason)
    recommended = next(
        (row for row in classify_series(list(series_map.values()))
         if row["status"] == "recommended"),
        None,
    )
    if chosen["status"] == "recommended":
        overridden = False
        stored_reason = ""
    elif recommended is not None:
        overridden = True
        if not text:
            raise ValueError("Give a reason for choosing this Series")
        stored_reason = text
    else:
        overridden = False
        stored_reason = text
    status_reason = chosen.get("reason") or ""

    output_dir: Path | None = None
    try:
        case_id = next_case_id(paths.LOCAL_CASES)
        output_dir = paths.LOCAL_CASES / case_id
        target = output_dir / f"{case_id}_CT.nii.gz"
        volume, affine = write_ct_nifti(chosen["files"], target)
        spacing = [float(np.linalg.norm(affine[:3, axis])) for axis in range(3)]
        document = build_case_document(
            case_id,
            chosen,
            series_map,
            reasons,
            spacing_mm=spacing,
            size=list(volume.shape),
        )
        # Folder labels may contain identifiers; the Series hash is the cache key.
        document["source"].pop("folder", None)
        for skipped in document["skipped_series"]:
            skipped.pop("folder", None)
        document["source"]["series_choice"] = {
            "status": chosen["status"],
            "status_reason": status_reason,
            "overridden": bool(overridden),
            "override_reason": stored_reason,
        }
        case_path = output_dir / "case.json"
        atomic_write_json(case_path, document)

        record = append_record(
            case_id,
            int(WorkflowStep.IMPORT),
            "series_confirmed",
            {
                "series_number": chosen["number"],
                "description": chosen["description"],
                "kernel": chosen["kernel"],
                "overridden": bool(overridden),
                "override_reason": stored_reason,
                "status_reason": status_reason,
                "status": chosen["status"],
            },
            clinician,
        )
    except Exception:
        if output_dir is not None and output_dir.is_dir():
            shutil.rmtree(output_dir, ignore_errors=True)
        raise

    logging.getLogger(__name__).info("import completed")
    return {
        "case_id": case_id,
        "source": document["source"],
        "record": record,
        "duplicate": None,
    }


__all__ = [
    "ImportDuplicate",
    "confirm_import",
    "find_duplicate",
    "scan_series",
    "validate_src_dir",
]
