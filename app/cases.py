# Changes: Phase 4b fixes — stamp selectable_until_mm and correction_id on payload.
"""Case inventory and locked record writes for the local app server.

Phase 4a: WorkflowStep, guarded load_graph, series/confirm.
Phase 4b: case_payload uses effective graph + removed + selectable.
"""

from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import paths
from atomic_json import atomic_write_json, json_file_lock
from make_prototype_data import ReviewBlocked, build_payload, review_gate, source_label

from app.caseref import CaseRef, UnknownCase
from app.ct_crop import build_ct_crop

RECORD_KINDS = frozenset({
    "series_confirmed",
    "lesion_confirmed",
    "lesion_cleared",
    "segmentation_done",
    "route_start_confirmed",
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
ROUTE_START_RADIUS_FRAC = 0.8


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
    from app.review import effective_for_case
    from app.workflow import load_graph

    ref = CaseRef.parse(case_id)
    record = load_case_record(ref)
    # Gate on the original graph's review flags (sign-off still required).
    raw_graph = load_graph(record, ref=ref)
    eff = effective_for_case(case_id, record)
    payload = build_payload(eff["graph"], record, allow_review)
    # RouteStart still uses the raw root (trachea) even if branches were pruned.
    payload["route_start"] = route_start_info(raw_graph, record)
    payload["removed"] = _payload_removed_edges(
        eff["removed"], raw_graph, payload.get("origin_mm") or [0, 0, 0]
    )
    # Stamp selectable onto payload edges (aligned by original index order in effective).
    for edge, src in zip(payload["edges"], eff["graph"]["edges"]):
        edge["selectable"] = bool(src.get("selectable", True))
        edge["selectable_until_mm"] = src.get("selectable_until_mm")
        edge["oi"] = src.get("_original_index")
    for rem in payload["removed"]:
        rem["selectable"] = False
        rem["selectable_until_mm"] = None
    return payload


def _payload_removed_edges(
    removed: list[dict], raw_graph: dict, origin_mm: list[float]
) -> list[dict]:
    """Ghosted removed edges in the same relative coords as payload edges."""
    from make_prototype_data import KEEP_EVERY, decimated_indices, provenance_mapping

    if not removed:
        return []
    # provenance_mapping expects full edge list shape; map one-by-one.
    out = []
    root = origin_mm

    def rel(p):
        return [round(float(p[i]) - float(root[i]), 1) for i in range(3)]

    for edge in removed:
        prov_map, _ = provenance_mapping([edge])
        prov = prov_map[0]
        indices = decimated_indices(len(edge.get("centreline_mm") or []), KEEP_EVERY)
        line = edge.get("centreline_mm") or []
        row = {
            "f": edge["from"],
            "t": edge["to"],
            "g": edge.get("generation", 0),
            "r": edge.get("radius_mm", 1.0),
            "l": edge.get("length_mm", 0.0),
            "lobe": edge.get("lobe"),
            **prov,
            "c": [rel(line[i]) for i in indices if i < len(line)],
            "oi": edge.get("_original_index"),
            "ghost": True,
            "selectable": False,
            "selectable_until_mm": None,
        }
        if edge.get("correction_id"):
            row["correction_id"] = edge["correction_id"]
        out.append(row)
    return out


def _node_degree(graph: dict) -> dict[int, int]:
    deg: dict[int, int] = {}
    for edge in graph.get("edges") or []:
        a, b = int(edge["from"]), int(edge["to"])
        deg[a] = deg.get(a, 0) + 1
        deg[b] = deg.get(b, 0) + 1
    return deg


def _nodes_by_id(graph: dict) -> dict[int, dict]:
    return {int(n["id"]): n for n in graph.get("nodes") or []}


def route_start_info(graph: dict, case_record: dict | None = None) -> dict:
    """Default RouteStart = graph root (widest terminal) plus confirmation state."""
    nodes = _nodes_by_id(graph)
    root_id = int(graph["root_node_id"])
    root = nodes[root_id]
    ras = [float(v) for v in root["position_mm"]]
    confirmed = None
    if case_record:
        for entry in reversed(case_record.get("records") or []):
            if entry.get("kind") == "route_start_confirmed":
                confirmed = entry.get("data")
                break
    node = int(confirmed["node"]) if confirmed and confirmed.get("node") is not None else root_id
    if node in nodes:
        ras = [float(v) for v in nodes[node]["position_mm"]]
    return {
        "node": node,
        "ras_mm": ras,
        "default_node": root_id,
        "confirmed": confirmed is not None,
    }


def _node_radius_mm(node: dict, node_id: int) -> float:
    if "radius_mm" not in node:
        raise ValueError(f"node {node_id} is missing radius_mm")
    try:
        return float(node["radius_mm"])
    except (TypeError, ValueError) as exc:
        raise ValueError(f"node {node_id} has invalid radius_mm") from exc


def qualifying_route_start_terminals(graph: dict) -> list[dict]:
    """Terminals whose radius ≥ 0.8 × root radius (includes the root)."""
    nodes = _nodes_by_id(graph)
    deg = _node_degree(graph)
    root_id = int(graph["root_node_id"])
    if root_id not in nodes:
        raise ValueError(f"root node {root_id} missing from graph")
    root_r = _node_radius_mm(nodes[root_id], root_id)
    min_r = ROUTE_START_RADIUS_FRAC * root_r
    out = []
    for nid, node in nodes.items():
        if deg.get(nid, 0) != 1:
            continue
        radius = _node_radius_mm(node, nid)
        if radius < min_r:
            continue
        out.append({
            "node": nid,
            "ras_mm": [float(v) for v in node["position_mm"]],
            "radius_mm": radius,
            "is_default": nid == root_id,
        })
    out.sort(key=lambda row: (-row["radius_mm"], row["node"]))
    return out


def set_route_start(case_id: str, node: int, clinician: str) -> dict:
    """Confirm RouteStart; node must be a qualifying terminal."""
    from app.workflow import WorkflowStep, load_graph

    ref = CaseRef.parse(case_id)
    graph = load_graph(ref=ref)
    nodes = _nodes_by_id(graph)
    if int(node) not in nodes:
        raise ValueError(f"unknown node {node}")
    qual = {row["node"]: row for row in qualifying_route_start_terminals(graph)}
    if int(node) not in qual:
        root_id = int(graph["root_node_id"])
        root_r = _node_radius_mm(nodes[root_id], root_id)
        got = _node_radius_mm(nodes[int(node)], int(node))
        raise ValueError(
            f"node {node} is not a qualifying RouteStart terminal "
            f"(radius {got:.2f} mm < {ROUTE_START_RADIUS_FRAC:.0%} of root "
            f"{root_r:.2f} mm, or not a terminal)"
        )
    chosen = qual[int(node)]
    default_node = int(graph["root_node_id"])
    moved = int(node) != default_node
    path = ref.case_json
    at = _utc_now()
    with json_file_lock(path):
        record = _load_or_init_record(ref, path)
        data = {
            "node": int(node),
            "ras_mm": chosen["ras_mm"],
            "moved": moved,
        }
        entry = {
            "step": int(WorkflowStep.ROUTE_START),
            "kind": "route_start_confirmed",
            "data": data,
            "clinician": clinician,
            "at": at,
        }
        record.setdefault("records", []).append(entry)
        record["route_start"] = {
            "node": int(node),
            "ras_mm": chosen["ras_mm"],
            "confirmed_by": clinician,
            "at": at,
        }
        atomic_write_json(path, record)
        return {"route_start": record["route_start"], "record": entry}


def confirm_existing_series(case_id: str, clinician: str, *, overridden: bool = False) -> dict:
    """Stamp series_confirmed from case.json source (already-imported local cases)."""
    from app.workflow import WorkflowStep

    ref = CaseRef.parse(case_id)
    if ref.kind != "local":
        raise ValueError("series confirm is only for local cases")
    path = ref.case_json
    at = _utc_now()
    with json_file_lock(path):
        record = _load_or_init_record(ref, path)
        source = record.get("source") or {}
        if not isinstance(source, dict) or not source.get("folder"):
            raise ValueError("case has no source series to confirm")
        data = {
            "folder": source.get("folder"),
            "series_number": source.get("series_number"),
            "description": source.get("series_description", ""),
            "kernel": source.get("convolution_kernel", ""),
            "overridden": bool(overridden),
        }
        entry = {
            "step": int(WorkflowStep.IMPORT),
            "kind": "series_confirmed",
            "data": data,
            "clinician": clinician,
            "at": at,
        }
        record.setdefault("records", []).append(entry)
        atomic_write_json(path, record)
        return {"record": entry, "source": source}


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
    from app.workflow import WorkflowStep

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
            "step": int(WorkflowStep.LESION),
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
    from app.workflow import WorkflowStep

    ref = CaseRef.parse(case_id)
    path = ref.case_json
    at = _utc_now()
    with json_file_lock(path):
        record = _load_or_init_record(ref, path)
        record.pop("lesion", None)
        entry = {
            "step": int(WorkflowStep.LESION),
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
    "ROUTE_START_RADIUS_FRAC",
    "UnknownCase",
    "append_record",
    "atomic_write_json",
    "case_payload",
    "clear_lesion",
    "confirm_existing_series",
    "default_aeropath_record",
    "list_cases",
    "load_case_record",
    "load_versions",
    "qualifying_route_start_terminals",
    "read_case_json",
    "route_start_info",
    "set_lesion",
    "set_route_start",
    "validate_lesion_ras",
    "validate_record_data",
    "write_case_json",
]
