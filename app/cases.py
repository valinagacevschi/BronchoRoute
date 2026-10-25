"""Case inventory and locked record writes for the local app server.

Phase 3 review: STEP_MARK_LESION, crop bounds [-0.5, n-0.5), shared aeropath record helper.
"""

from __future__ import annotations

import json
import math
from datetime import datetime, timezone
import numpy as np
import paths
from atomic_json import atomic_write_json, json_file_lock
from make_prototype_data import ReviewBlocked, build_payload, review_gate, source_label

from app.caseref import CaseRef, UnknownCase
from app.ct_crop import build_ct_crop

# Workflow step 2 — Mark the Lesion (docs/Workflow and Build Plan.md).
STEP_MARK_LESION = 2

RECORD_KINDS = frozenset({
    "lesion_confirmed",
    "lesion_cleared",
    "route_target_chosen",
    "bifurcation_confirmed",
    "correction",
    "bridge_decision",
    "note",
    "signoff",
})
FORBIDDEN_KEY_SUBSTR = ("patient", "name", "birth", "mrn", "accession", "uid")
MAX_DATA_BYTES = 8192
MAX_STRING_CHARS = 500


def load_case_record(ref: CaseRef) -> dict:
    path = ref.case_json
    if path.is_file():
        return json.loads(path.read_text())
    return {"id": ref.case_id, "source": ref.source_label, "records": [], "versions": []}


def list_cases() -> list[dict]:
    items: list[dict] = []
    seen: set[str] = set()

    if paths.LOCAL_CASES.is_dir():
        for case_path in sorted(paths.LOCAL_CASES.glob("*/case.json")):
            case_id = case_path.parent.name
            try:
                ref = CaseRef.parse(case_id)
            except UnknownCase:
                continue
            seen.add(case_id)
            items.append(_case_summary(ref, json.loads(case_path.read_text())))

    if paths.AEROPATH.is_dir():
        for case_dir in sorted(paths.AEROPATH.iterdir()):
            if not case_dir.is_dir():
                continue
            try:
                ref = CaseRef.parse(case_dir.name)
            except UnknownCase:
                continue
            if ref.case_id in seen:
                continue
            if ref.graph_path() is None:
                continue
            items.append(_case_summary(ref, load_case_record(ref)))

    return items


def _case_summary(ref: CaseRef, record: dict) -> dict:
    graph_path = ref.graph_path()
    unresolved = 0
    if graph_path is not None:
        graph = json.loads(graph_path.read_text())
        _, unresolved_entries, _ = review_gate(
            graph.get("review", []), record.get("review_signoff", [])
        )
        unresolved = len(unresolved_entries)
    versions = record.get("versions") or []
    return {
        "id": ref.case_id,
        "source": source_label(ref.case_id),
        "has_graph": graph_path is not None,
        "review": {"unresolved": unresolved},
        "version": versions[-1]["n"] if versions else None,
    }


def case_payload(case_id: str, allow_review: bool = False) -> dict:
    ref = CaseRef.parse(case_id)
    graph_path = ref.graph_path()
    if graph_path is None:
        raise FileNotFoundError(f"no graph for case {case_id}")
    graph = json.loads(graph_path.read_text())
    return build_payload(graph, load_case_record(ref), allow_review)


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _ras_inside_crop(ras_mm: list[float], meta: dict) -> bool:
    """True when continuous voxel coords lie in [-0.5, n-0.5) per axis."""
    affine = np.asarray(meta["affine"], dtype=float)
    inv = np.linalg.inv(affine)
    ijk = inv @ np.array([ras_mm[0], ras_mm[1], ras_mm[2], 1.0], dtype=float)
    shape = meta["shape"]
    for axis in range(3):
        v = float(ijk[axis])
        if v < -0.5 or v >= shape[axis] - 0.5:
            return False
    return True


def validate_lesion_ras(case_id: str, ras_mm: object) -> list[float]:
    if not isinstance(ras_mm, (list, tuple)) or len(ras_mm) != 3:
        raise ValueError("ras_mm must be a length-3 list")
    try:
        coords = [float(ras_mm[0]), float(ras_mm[1]), float(ras_mm[2])]
    except (TypeError, ValueError) as exc:
        raise ValueError("ras_mm values must be numbers") from exc
    if not all(math.isfinite(v) for v in coords):
        raise ValueError("ras_mm values must be finite")
    meta = build_ct_crop(case_id)
    if not _ras_inside_crop(coords, meta):
        raise ValueError("lesion ras_mm is outside the CT crop bounds")
    return coords


def default_aeropath_record(ref: CaseRef) -> dict:
    """Empty case.json body for an AeroPath case that has no file yet."""
    return {
        "id": ref.case_id,
        "source": ref.source_label,
        "records": [],
        "versions": [],
    }


def _load_or_init_record(ref: CaseRef, path: Path) -> dict:
    if path.is_file():
        return json.loads(path.read_text())
    if ref.kind == "aeropath":
        return default_aeropath_record(ref)
    raise FileNotFoundError(f"case.json not found for {ref.case_id}")


def set_lesion(case_id: str, ras_mm: list[float], clinician: str) -> dict:
    """Store lesion and append lesion_confirmed under one case lock."""
    coords = validate_lesion_ras(case_id, ras_mm)
    ref = CaseRef.parse(case_id)
    path = ref.case_json
    at = _utc_now()
    with json_file_lock(path):
        record = _load_or_init_record(ref, path)
        lesion = {
            "ras_mm": coords,
            "confirmed_by": clinician,
            "at": at,
        }
        record["lesion"] = lesion
        entry = {
            "step": STEP_MARK_LESION,
            "kind": "lesion_confirmed",
            "data": {"ras_mm": coords},
            "clinician": clinician,
            "at": at,
        }
        record.setdefault("records", []).append(entry)
        atomic_write_json(path, record)
        return {"lesion": lesion, "record": entry}


def clear_lesion(case_id: str, clinician: str) -> dict:
    """Clear lesion and append lesion_cleared under one case lock."""
    ref = CaseRef.parse(case_id)
    path = ref.case_json
    at = _utc_now()
    with json_file_lock(path):
        record = _load_or_init_record(ref, path)
        record.pop("lesion", None)
        entry = {
            "step": STEP_MARK_LESION,
            "kind": "lesion_cleared",
            "data": {},
            "clinician": clinician,
            "at": at,
        }
        record.setdefault("records", []).append(entry)
        atomic_write_json(path, record)
        return {"lesion": None, "record": entry}


def validate_record_data(data: object) -> None:
    try:
        serialised = json.dumps(data, separators=(",", ":"))
    except (TypeError, ValueError) as exc:
        raise ValueError("data must be JSON-serialisable") from exc
    if len(serialised.encode("utf-8")) > MAX_DATA_BYTES:
        raise ValueError(f"data exceeds {MAX_DATA_BYTES} bytes serialised")
    _walk_record_value(data)


def _walk_record_value(value: object) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            if not isinstance(key, str):
                raise ValueError("data keys must be strings")
            lowered = key.lower()
            if any(token in lowered for token in FORBIDDEN_KEY_SUBSTR):
                raise ValueError(f"data key not allowed: {key!r}")
            _walk_record_value(child)
        return
    if isinstance(value, list):
        for child in value:
            _walk_record_value(child)
        return
    if isinstance(value, str):
        if len(value) > MAX_STRING_CHARS:
            raise ValueError(f"string values must be ≤ {MAX_STRING_CHARS} characters")
        return
    if isinstance(value, bool) or value is None:
        return
    if isinstance(value, int) and not isinstance(value, bool):
        return
    if isinstance(value, float):
        return
    raise ValueError("data may only contain JSON scalars, lists, and dicts")


def append_record(
    case_id: str, step: int, kind: str, data: dict, clinician: str
) -> dict:
    if not isinstance(step, int) or isinstance(step, bool) or not 1 <= step <= 8:
        raise ValueError("step must be an integer from 1 to 8")
    if kind not in RECORD_KINDS:
        raise ValueError(f"kind must be one of {sorted(RECORD_KINDS)}")
    validate_record_data(data)

    ref = CaseRef.parse(case_id)
    path = ref.case_json
    with json_file_lock(path):
        record = _load_or_init_record(ref, path)
        entries = record.setdefault("records", [])
        entry = {
            "step": step,
            "kind": kind,
            "data": data,
            "clinician": clinician,
            "at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
        entries.append(entry)
        atomic_write_json(path, record)
        return entry


def load_versions(case_id: str) -> list[dict]:
    ref = CaseRef.parse(case_id)
    return list(load_case_record(ref).get("versions") or [])


def read_case_json(case_id: str) -> dict:
    """Load case.json for a known case (shared by server and CLI)."""
    return load_case_record(CaseRef.parse(case_id))


def write_case_json(case_id: str, record: dict) -> Path:
    """Atomically write case.json under the case lock."""
    ref = CaseRef.parse(case_id)
    path = ref.case_json
    with json_file_lock(path):
        atomic_write_json(path, record)
    return path


__all__ = [
    "ReviewBlocked",
    "RECORD_KINDS",
    "STEP_MARK_LESION",
    "UnknownCase",
    "append_record",
    "atomic_write_json",
    "case_payload",
    "clear_lesion",
    "default_aeropath_record",
    "list_cases",
    "load_case_record",
    "load_versions",
    "read_case_json",
    "set_lesion",
    "validate_lesion_ras",
    "validate_record_data",
    "write_case_json",
]
