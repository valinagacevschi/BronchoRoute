# Changes: expose Series eligibility, kernel class and coverage in scan responses.
# Changes: log import lifecycle without Archive paths or Series selections.
"""Step 1 — DICOM series scan and import from the app (Phase 4a).

Scan uses import_dicom dry-run semantics (grouping + pick). Confirm writes the
NIfTI + case.json and appends series_confirmed. Paths must sit under $HOME.
Shares case-document builder and duplicate message with pipeline/import_dicom.py.
"""

from __future__ import annotations

import logging
import shutil
from pathlib import Path

import numpy as np

import paths
from atomic_json import atomic_write_json
from import_dicom import (
    build_case_document,
    classify_series,
    duplicate_message,
    next_case_id,
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


def validate_src_dir(src_dir: str | Path) -> Path:
    """Require an existing directory under the user's home."""
    home = Path.home().resolve()
    try:
        path = Path(src_dir).expanduser().resolve()
    except OSError as exc:
        raise ValueError("invalid source directory") from exc
    if not path.is_dir():
        raise ValueError("src_dir is not an existing directory")
    try:
        path.relative_to(home)
    except ValueError as exc:
        raise ValueError(f"src_dir must be under home ({home})") from exc
    return path


def _public_row(row: dict, *, skip: str | None, proposed: bool) -> dict:
    return {
        "series_key": row["series_uid_sha256"],
        "folder": row["folder"],
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


def scan_series(src_dir: str | Path) -> dict:
    """Group + pick with dry-run semantics; allowed facts only (no UIDs/paths)."""
    root = validate_src_dir(src_dir)
    logging.getLogger(__name__).info("import scan started")
    series, unreadable = read_series(root)
    if not series:
        raise ValueError("no CT Series found")
    rows = classify_series(list(series.values()))
    table = [_public_row(row,
                         skip=row["reason"] if row["status"] == "not_usable" else None,
                         proposed=row["status"] == "recommended")
             for row in sorted(rows, key=lambda row: row["number"])]
    return {
        "src_dir": str(root),
        "unreadable_files": unreadable,
        "proposed": next((row for row in table if row["proposed"]), None),
        "series": table,
    }


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

    # Detect override vs auto-pick.
    try:
        _auto_uid, auto_row, _ = pick_series(series_map, None)
        overridden = (
            auto_row["folder"] != chosen["folder"]
            or auto_row["number"] != chosen["number"]
        )
    except SystemExit:
        overridden = True

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
        case_path = output_dir / "case.json"
        atomic_write_json(case_path, document)

        record = append_record(
            case_id,
            int(WorkflowStep.IMPORT),
            "series_confirmed",
            {
                "folder": chosen["folder"],
                "series_number": chosen["number"],
                "description": chosen["description"],
                "kernel": chosen["kernel"],
                "overridden": bool(overridden),
                "override_reason": chosen["reason"] if overridden else "",
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
