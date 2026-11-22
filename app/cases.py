# Changes: stop the default viewer route at the first unconfirmed bridge boundary.
# Changes: lesion record stores diameter_mm (default 10 mm, allowed 2–60).
# Changes: keep unfinished imported Cases out of the library.
# Changes: recheck saved header facts before confirming an imported Series.
# Changes: hide legacy review behind the default-off feature flag.
# Changes: Phase 4d review fixes — note_change on lesion/route_start/records;
# clinician-text checks on note records.
"""Case inventory and locked record writes for the local app server.

Phase 4a: WorkflowStep, guarded load_graph, series/confirm.
Phase 4b: case_payload uses effective graph + removed + selectable.
Phase 4c: RouteTarget truncation, stored gap, stale marker.
Phase 4d: note_change opens N+1 after freeze.
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
    "case_reset",
    "note",
    "signoff",
})
FORBIDDEN_KEY_SUBSTR = ("patient", "name", "birth", "mrn", "accession", "uid")
MAX_DATA_BYTES = 8192
MAX_STRING_CHARS = 500
ROUTE_START_RADIUS_FRAC = 0.8
MANUAL_LESION_SOURCE = "manual"
NODULE_LESION_SOURCE = "nodule"
NODULE_MATCH_MM = 0.5


def lesion_source(record: dict) -> str:
    """Where the stored Lesion came from: a person or a detected Nodule.

    Records written before the source field existed are matched against the
    stored candidates by position. A match means a detected Nodule.
    """
    lesion = record.get("lesion")
    if not isinstance(lesion, dict):
        return MANUAL_LESION_SOURCE
    stored = lesion.get("source")
    if stored in {MANUAL_LESION_SOURCE, NODULE_LESION_SOURCE}:
        return str(stored)
    block = record.get("candidates")
    items = block.get("items") if isinstance(block, dict) else None
    ras = lesion.get("ras_mm")
    if isinstance(ras, list) and len(ras) == 3 and isinstance(items, list):
        for item in items:
            other = item.get("ras_mm") if isinstance(item, dict) else None
            if not isinstance(other, list) or len(other) != 3:
                continue
            if all(
                abs(float(ras[axis]) - float(other[axis])) <= NODULE_MATCH_MM
                for axis in range(3)
            ):
                return NODULE_LESION_SOURCE
    return MANUAL_LESION_SOURCE


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
            record = json.loads(case_path.read_text())
            if record.get("import_processing"):
                continue
            seen.add(case_id)
            items.append(_case_summary(ref, record))

    if paths.legacy_review() and paths.AEROPATH.is_dir():
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
    from app.route_target import (
        last_original_index_at_or_before_arc,
        plan_route_target_for_payload,
    )
    from app.workflow import load_graph

    ref = CaseRef.parse(case_id)
    record = load_case_record(ref)
    # Gate on the original graph's review flags (sign-off still required).
    raw_graph = load_graph(record, ref=ref)
    eff = effective_for_case(case_id, record)
    plan = plan_route_target_for_payload(
        eff["graph"], record, record.get("route_target")
    )
    if plan.get("active"):
        end_edge = int(plan["end_edge"])
        end_sample = last_original_index_at_or_before_arc(
            eff["graph"]["edges"][end_edge], float(plan["end_arc_mm"])
        )
        payload = build_payload(
            eff["graph"], record, allow_review,
            end_edge=end_edge,
            end_sample=end_sample,
            start_node=int(plan["start_node"]),
            end_ras_mm=list(plan["end_ras_mm"]),
            stored_gap_mm=plan.get("target_gap_mm"),
            stored_inside_airway=plan.get("target_inside_airway"),
        )
        # Stored gap is the single source of truth.
        if plan.get("target_gap_mm") is not None:
            payload["target_gap_mm"] = plan["target_gap_mm"]
        if plan.get("target_inside_airway") is not None:
            payload["target_inside_airway"] = plan["target_inside_airway"]
    else:
        # Fall back to RouteStart-rooted deepest route when no active target.
        start_info = route_start_info(eff["graph"], record)
        start = (
            int(start_info["node"])
            if start_info.get("confirmed")
            else int(eff["graph"]["root_node_id"])
        )
        payload = build_payload(
            eff["graph"], record, allow_review, start_node=start
        )
        for ei in payload["route"]:
            edge = eff["graph"]["edges"][ei]
            until = edge.get("selectable_until_mm")
            if edge.get("selectable", True) and until is None:
                continue
            arc = float(until or 0.0)
            points = np.asarray(edge["centreline_mm"], dtype=float)
            arcs = np.r_[0.0, np.cumsum(np.linalg.norm(np.diff(points, axis=0), axis=1))]
            point = [float(np.interp(arc, arcs, points[:, axis])) for axis in range(3)]
            payload = build_payload(
                eff["graph"], record, allow_review, start_node=start,
                end_edge=ei,
                end_sample=last_original_index_at_or_before_arc(edge, arc),
                end_ras_mm=point,
            )
            break
        # Stale or absent target: never draw a disagreeing RouteTarget marker.
        payload["endpoint"] = None
        payload["target_gap_mm"] = None
        payload["target_inside_airway"] = None
    # RouteStart still uses the raw root (trachea) even if branches were pruned.
    payload["route_start"] = route_start_info(raw_graph, record)
    if plan.get("stale"):
        payload["route_target"] = {
            "stale": True,
            "reason": plan.get("reason") or "route target no longer valid",
        }
    elif plan.get("active"):
        payload["route_target"] = plan["route_target"]
    else:
        payload["route_target"] = None
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
    if isinstance(payload.get("lesion"), dict):
        payload["lesion"]["source"] = lesion_source(record)
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
        from app.versions import note_change

        note_change(record, "route_start")
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
        from series_rule import reject_reason

        facts = source.get("series_facts")
        reason = reject_reason(facts) if facts else None
        if reason or source.get("status") == "not_usable":
            raise ValueError(f"Series not usable: {reason or source.get('reason')}")
        data = {
            "folder": source.get("folder"),
            "series_number": source.get("series_number"),
            "description": source.get("series_description", ""),
            "kernel": source.get("convolution_kernel", ""),
            "overridden": bool(overridden),
            "status": source.get("status"),
            "override_reason": source.get("reason", "") if overridden else "",
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


def validate_lesion_diameter(diameter_mm: object) -> float:
    """Default 10 mm. Allowed range is 2–60 inclusive."""
    if diameter_mm is None:
        return 10.0
    if isinstance(diameter_mm, bool):
        raise ValueError("diameter_mm must be a number")
    try:
        value = float(diameter_mm)
    except (TypeError, ValueError) as exc:
        raise ValueError("diameter_mm must be a number") from exc
    if not math.isfinite(value) or value < 2 or value > 60:
        raise ValueError("diameter_mm must be between 2 and 60")
    return value


def _candidate_diameter_for_lesion(diameter_mm: object) -> float:
    """Clamp a measured candidate to the Lesion contract.

    A mask can measure a candidate above 60 mm. The stored candidate keeps its
    measured value; only the confirmed Lesion takes the nearest allowed size.
    """
    if isinstance(diameter_mm, bool):
        return validate_lesion_diameter(diameter_mm)
    try:
        measured = float(diameter_mm)
    except (TypeError, ValueError):
        return validate_lesion_diameter(diameter_mm)
    if not math.isfinite(measured):
        return validate_lesion_diameter(diameter_mm)
    return validate_lesion_diameter(min(60.0, max(2.0, measured)))


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


def _update_record(case_id: str, event: str, mutate):
    """Run one read-modify-write under the case lock.

    A None result means the mutation was a no-op and nothing is written.
    """
    ref = CaseRef.parse(case_id)
    path = ref.case_json
    with json_file_lock(path):
        record = _load_or_init_record(ref, path)
        result = mutate(record)
        if result is None:
            return None
        from app.versions import note_change

        note_change(record, event)
        atomic_write_json(path, record)
        return result


MAX_CANDIDATES = 20
CANDIDATE_SCORE_MIN = 0.0
CANDIDATE_SCORE_MAX = 1.0


def _clean_nodule_id(value: object) -> int | None:
    """Stable nodule id from the instance record, or None for a box-only candidate."""
    if value is None:
        return None
    if isinstance(value, bool):
        raise ValueError("candidate id must be an integer")
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("candidate id must be an integer") from exc
    if isinstance(value, float) and not value.is_integer():
        raise ValueError("candidate id must be an integer")
    if number < 1:
        raise ValueError("candidate id must be a positive integer")
    return number


def _clean_volume(value: object) -> float | None:
    """Mask-derived volume in mm3, or None when the candidate has no mask."""
    if value is None:
        return None
    if isinstance(value, bool):
        raise ValueError("volume_mm3 must be a number")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("volume_mm3 must be a number") from exc
    if not math.isfinite(number) or number < 0:
        raise ValueError("volume_mm3 must be a non-negative number")
    return number


def _clean_box_size(value: object) -> list[float] | None:
    """Detector box size in mm, or None when the proposal did not carry one."""
    if value is None:
        return None
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        raise ValueError("size_mm must be a length-3 list")
    try:
        size = [float(v) for v in value]
    except (TypeError, ValueError) as exc:
        raise ValueError("size_mm values must be numbers") from exc
    if not all(math.isfinite(v) and v > 0 for v in size):
        raise ValueError("size_mm values must be positive finite numbers")
    return size


def _clean_to_airway(value: object) -> float | None:
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("to_airway_mm must be a number") from exc
    if not math.isfinite(number) or number < 0:
        raise ValueError("to_airway_mm must be a non-negative number")
    return number


def _clean_candidate(case_id: str, item: object) -> dict:
    """Validate one detection candidate against the CT crop."""
    if not isinstance(item, dict):
        raise ValueError("candidate must be an object")
    ras = validate_lesion_ras(case_id, item.get("ras_mm"))
    diameter = validate_lesion_diameter(item.get("diameter_mm"))
    score = item.get("score")
    if score is not None:
        try:
            score = float(score)
        except (TypeError, ValueError) as exc:
            raise ValueError("candidate score must be a number") from exc
        if not math.isfinite(score) or not (
            CANDIDATE_SCORE_MIN <= score <= CANDIDATE_SCORE_MAX
        ):
            raise ValueError(
                f"candidate score must be between {CANDIDATE_SCORE_MIN:g} and "
                f"{CANDIDATE_SCORE_MAX:g}"
            )
    lobe = item.get("lobe")
    if lobe is not None and not isinstance(lobe, str):
        raise ValueError("candidate lobe must be a string")
    return {
        "ras_mm": ras,
        "diameter_mm": diameter,
        "score": score,
        "id": _clean_nodule_id(item.get("id")),
        "volume_mm3": _clean_volume(item.get("volume_mm3")),
        "lobe": lobe,
        "to_airway_mm": _clean_to_airway(item.get("to_airway_mm")),
        "size_mm": _clean_box_size(item.get("size_mm")),
        "review": bool(item.get("review", False)),
    }


def set_candidates(
    case_id: str,
    items: object,
    source: str,
    clinician: str,
) -> dict:
    """Store detector candidates as suggestions; they never become the lesion."""
    if not isinstance(items, list) or not items:
        raise ValueError("candidates must be a non-empty list")
    if len(items) > MAX_CANDIDATES:
        raise ValueError(f"at most {MAX_CANDIDATES} candidates")
    cleaned = [_clean_candidate(case_id, item) for item in items]

    def apply(record: dict) -> dict:
        record["candidates"] = {
            "source": str(source or "detector"),
            "at": _utc_now(),
            "by": clinician,
            "items": cleaned,
        }
        return {"candidates": record["candidates"]}

    return _update_record(case_id, "candidates", apply)


def clear_candidates(case_id: str, clinician: str) -> dict:
    def apply(record: dict) -> dict | None:
        if "candidates" not in record:
            return None
        record.pop("candidates", None)
        return {"candidates": None}

    result = _update_record(case_id, "candidates_cleared", apply)
    return result if result is not None else {"candidates": None}


def confirm_candidate(case_id: str, index: object, clinician: str) -> dict:
    """Promote one candidate to the confirmed lesion and drop the rest, atomically."""
    from app.workflow import WorkflowStep

    try:
        position = int(index)
    except (TypeError, ValueError) as exc:
        raise ValueError("candidate index must be an integer") from exc

    def apply(record: dict) -> dict:
        block = record.get("candidates")
        items = block.get("items") if isinstance(block, dict) else None
        if not isinstance(items, list) or not items:
            raise ValueError("no candidates stored for this case")
        if position < 0 or position >= len(items):
            raise ValueError("candidate index out of range")
        item = items[position]
        coords = validate_lesion_ras(case_id, item["ras_mm"])
        # Keep the candidate's measured value; the Lesion takes the nearest size
        # inside its 2–60 mm contract.
        diameter = _candidate_diameter_for_lesion(item.get("diameter_mm"))
        at = _utc_now()
        lesion = {
            "ras_mm": coords,
            "diameter_mm": diameter,
            "confirmed_by": clinician,
            "at": at,
            "source": NODULE_LESION_SOURCE,
        }
        entry = {
            "step": int(WorkflowStep.LESION),
            "kind": "lesion_confirmed",
            "data": {"ras_mm": coords, "diameter_mm": diameter},
            "clinician": clinician,
            "at": at,
        }
        record["lesion"] = lesion
        record.setdefault("records", []).append(entry)
        record.pop("candidates", None)
        return {"lesion": lesion, "record": entry}

    return _update_record(case_id, "lesion", apply)


def set_lesion(
    case_id: str,
    ras_mm: list[float],
    clinician: str,
    diameter_mm: float | None = None,
) -> dict:
    """Store one lesion (replace) and append lesion_confirmed under one case lock."""
    from app.workflow import WorkflowStep

    coords = validate_lesion_ras(case_id, ras_mm)
    diameter = validate_lesion_diameter(diameter_mm)

    def apply(record: dict) -> dict:
        at = _utc_now()
        lesion = {
            "ras_mm": coords,
            "diameter_mm": diameter,
            "confirmed_by": clinician,
            "at": at,
            "source": MANUAL_LESION_SOURCE,
        }
        entry = {
            "step": int(WorkflowStep.LESION),
            "kind": "lesion_confirmed",
            "data": {"ras_mm": coords, "diameter_mm": diameter},
            "clinician": clinician,
            "at": at,
        }
        record["lesion"] = lesion
        record.setdefault("records", []).append(entry)
        return {"lesion": lesion, "record": entry}

    return _update_record(case_id, "lesion", apply)


RESET_KEEP_RECORD_KINDS = frozenset({"series_confirmed", "segmentation_done"})
RESET_DROP_FIELDS = (
    "lesion",
    "route_target",
    "route_start",
    "corrections",
    "pending_return",
    "review_signoff",
    "versions",
)


def reset_case(case_id: str, clinician: str) -> dict:
    """Return the Case to its post-processing state, keeping detected nodules.

    Removes human decisions: Lesion, RouteTarget, RouteStart, corrections,
    review sign-off, and bridge decisions by a person. Detector candidates,
    the auto bridge decisions, and the pipeline records stay.
    """
    def apply(record: dict) -> dict:
        for key in RESET_DROP_FIELDS:
            record.pop(key, None)
        decisions = record.get("bridge_decisions")
        if isinstance(decisions, dict):
            kept = {
                key: value for key, value in decisions.items()
                if isinstance(value, dict) and value.get("source") == "auto"
            }
            if kept:
                record["bridge_decisions"] = kept
            else:
                record.pop("bridge_decisions", None)
        records = []
        for rec in record.get("records") or []:
            if not isinstance(rec, dict):
                continue
            kind = rec.get("kind")
            data = rec.get("data") if isinstance(rec.get("data"), dict) else {}
            if kind in RESET_KEEP_RECORD_KINDS or (
                kind == "bridge_decision" and data.get("source") == "auto"
            ):
                records.append(rec)
        entry = {
            "step": 1,
            "kind": "case_reset",
            "data": {},
            "clinician": clinician,
            "at": _utc_now(),
        }
        records.append(entry)
        record["records"] = records
        return {"reset": True, "record": entry}

    return _update_record(case_id, "case_reset", apply)


def clear_lesion(case_id: str, clinician: str) -> dict:
    """Clear a manually placed Lesion and append lesion_cleared.

    A Lesion confirmed from a detected Nodule is never deleted. It can only be
    replaced by a new Lesion, which rewrites the record.
    """
    from app.workflow import WorkflowStep

    def apply(record: dict) -> dict:
        if lesion_source(record) == NODULE_LESION_SOURCE:
            raise ValueError(
                "This Lesion came from a detected nodule and cannot be cleared. "
                "Place a new Lesion to replace it."
            )
        record.pop("lesion", None)
        entry = {
            "step": int(WorkflowStep.LESION),
            "kind": "lesion_cleared",
            "data": {},
            "clinician": clinician,
            "at": _utc_now(),
        }
        record.setdefault("records", []).append(entry)
        return {"lesion": None, "record": entry}

    return _update_record(case_id, "lesion_cleared", apply)


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
        from app.versions import note_change, validate_clinician_text

        note_change(record, f"record:{kind}")
        if kind == "note":
            text = (data or {}).get("text") or (data or {}).get("note")
            if isinstance(text, str) and text.strip():
                validate_clinician_text(text, field="note")
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
    "CANDIDATE_SCORE_MAX",
    "CANDIDATE_SCORE_MIN",
    "MAX_CANDIDATES",
    "clear_candidates",
    "clear_lesion",
    "confirm_candidate",
    "confirm_existing_series",
    "default_aeropath_record",
    "list_cases",
    "load_case_record",
    "load_versions",
    "qualifying_route_start_terminals",
    "read_case_json",
    "reset_case",
    "route_start_info",
    "set_candidates",
    "set_lesion",
    "validate_lesion_diameter",
    "set_route_start",
    "validate_lesion_ras",
    "validate_record_data",
    "write_case_json",
]
