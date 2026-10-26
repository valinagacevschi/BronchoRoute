"""Step 1 — DICOM series scan and import from the app (Phase 4a).

Scan uses import_dicom dry-run semantics (grouping + pick). Confirm writes the
NIfTI + case.json and appends series_confirmed. Paths must sit under $HOME.
Shares case-document builder and duplicate message with pipeline/import_dicom.py.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import numpy as np
from termcolor import cprint

import paths
from atomic_json import atomic_write_json
from import_dicom import (
    build_case_document,
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
        raise ValueError(f"invalid src_dir: {src_dir}") from exc
    if not path.is_dir():
        raise ValueError(f"src_dir is not an existing directory: {path}")
    try:
        path.relative_to(home)
    except ValueError as exc:
        raise ValueError(f"src_dir must be under home ({home})") from exc
    return path


def _public_row(row: dict, *, skip: str | None, proposed: bool) -> dict:
    return {
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
        "skip_reason": skip,
        "proposed": proposed,
    }


def scan_series(src_dir: str | Path) -> dict:
    """Group + pick with dry-run semantics; allowed facts only (no UIDs/paths)."""
    root = validate_src_dir(src_dir)
    cprint(f"import scan: {root}", "cyan")
    series, unreadable = read_series(root)
    try:
        chosen_uid, chosen, reasons = pick_series(series, None)
    except SystemExit as exc:
        raise ValueError(str(exc) or "no eligible CT series found") from exc

    table = []
    for uid, row in sorted(series.items(), key=lambda item: item[1]["number"]):
        skip = reasons.get(uid)
        table.append(_public_row(row, skip=skip, proposed=(uid == chosen_uid)))

    return {
        "src_dir": str(root),
        "unreadable_files": unreadable,
        "proposed": _public_row(chosen, skip=None, proposed=True),
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
    cprint(f"import confirm: {root} series={override}", "cyan")
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
            },
            clinician,
        )
    except Exception:
        if output_dir is not None and output_dir.is_dir():
            shutil.rmtree(output_dir, ignore_errors=True)
        raise

    cprint(f"imported {case_id}", "green")
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
