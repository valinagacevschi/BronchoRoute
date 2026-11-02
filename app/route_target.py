# Changes: RouteTarget needs a Lesion only; RouteStart defaults to the trachea.
# Changes: Phase 4d review fixes — note_change on RouteTarget; clear pending_return.
# Phase 4c review fixes — RouteStart-rooted route helper, reachability
# 422, fly truncate+exact sample, stale target, lock-held PUT, stored gap.
"""Phase 4c — Choose RouteTarget (workflow step 6).

Candidates are densified (≤ 0.5 mm) CT-seen samples on selectable edges of the
effective graph. Ranking: target_gap_mm ↑, generation ↑, route length from
RouteStart ↑. Top 3, distinct edges, ≥ 5 mm apart.
"""

from __future__ import annotations

from typing import Any

import numpy as np
from atomic_json import atomic_write_json, json_file_lock
from make_prototype_data import edge_provenance_at, edge_route
from snomed import LOBES
from termcolor import cprint

from app.cases import _load_or_init_record, _utc_now, load_case_record, route_start_info
from app.caseref import CaseRef
from app.review import densify_centreline, effective_for_case
from app.workflow import WorkflowBlocked, WorkflowStep

CANDIDATE_STEP_MM = 0.5
DEDUP_MM = 5.0
SNAP_MM = 2.0
TOP_N = 3
NOT_ON_CT = "not on CT-seen airway"
NOT_REACHABLE = "not reachable from RouteStart"
LESION_FIRST = "place a Lesion first"


def _edge_oi(edge: dict, idx: int) -> int:
    oi = edge.get("_original_index")
    return int(oi) if oi is not None else int(idx)


def _lobe_label(lobe: Any) -> Any:
    if isinstance(lobe, int) and lobe in LOBES:
        return LOBES[lobe]["name"]
    return lobe


def _route_start_node(graph: dict, case: dict) -> int:
    """Trachea (graph root), unless a legacy confirmation stored another node."""
    info = route_start_info(graph, case)
    if info.get("confirmed") and info.get("node") is not None:
        return int(info["node"])
    return int(graph["root_node_id"])


def route_edges_from_start(
    edges: list[dict], start_node: int, end_edge: int
) -> list[int] | None:
    """Edge indices from start_node down to end_edge, or None if unreachable.

    Shared by candidates and payload so card route_length_mm matches the drawn route.
    """
    if end_edge < 0 or end_edge >= len(edges):
        return None
    start = int(start_node)
    end_edge = int(end_edge)
    route = edge_route(edges, start, end_edge)
    if not route or route[-1] != end_edge:
        return None
    if int(edges[route[0]]["from"]) != start:
        return None
    return route


def _sample_allowed(edge: dict, arc_mm: float) -> bool:
    """True when sample is on a CT span and within selectable_until_mm."""
    if not edge.get("selectable", True):
        return False
    until = edge.get("selectable_until_mm")
    if until is not None and float(arc_mm) > float(until) + 1e-9:
        return False
    return edge_provenance_at(edge, float(arc_mm)) == "ct"


def _arc_length_to_index(points: np.ndarray, sample_index: int) -> float:
    if len(points) < 2 or sample_index <= 0:
        return 0.0
    si = min(int(sample_index), len(points) - 1)
    return float(np.linalg.norm(np.diff(points[: si + 1], axis=0), axis=1).sum())


def _centreline_arcs(points: np.ndarray) -> np.ndarray:
    if len(points) == 0:
        return np.zeros(0, dtype=float)
    if len(points) == 1:
        return np.array([0.0], dtype=float)
    return np.r_[0.0, np.cumsum(np.linalg.norm(np.diff(points, axis=0), axis=1))]


def _route_length_mm(
    edges: list[dict], route: list[int], end_edge: int, end_arc_mm: float
) -> float:
    total = 0.0
    for ei in route:
        pts = np.asarray(edges[ei].get("centreline_mm") or [], dtype=float)
        if len(pts) < 2:
            continue
        full = float(np.linalg.norm(np.diff(pts, axis=0), axis=1).sum())
        if ei == end_edge:
            total += min(full, max(0.0, float(end_arc_mm)))
            break
        total += full
    return total


def _interpolated_mm_on_route(
    edges: list[dict], route: list[int], end_edge: int, end_arc_mm: float
) -> float:
    """Sum interpolated span length on the route (confirmed bridges only)."""
    total = 0.0
    for ei in route:
        edge = edges[ei]
        spans = edge.get("interpolated_spans_mm") or []
        limit = float(end_arc_mm) if ei == end_edge else float("inf")
        for span in spans:
            if not isinstance(span, (list, tuple)) or len(span) < 2:
                continue
            start, end = float(span[0]), float(span[1])
            if start >= limit:
                continue
            total += max(0.0, min(end, limit) - start)
        if ei == end_edge:
            break
    return round(total, 2)


def last_original_index_at_or_before_arc(edge: dict, arc_mm: float) -> int:
    """Index of last original centreline vertex with arc length ≤ arc_mm."""
    pts = np.asarray(edge.get("centreline_mm") or [], dtype=float)
    if len(pts) == 0:
        return 0
    arcs = _centreline_arcs(pts)
    idx = int(np.searchsorted(arcs, float(arc_mm), side="right") - 1)
    return max(0, min(idx, len(pts) - 1))


def truncate_route_absolute(
    edges: list[dict],
    route: list[int],
    end_edge: int,
    end_arc_mm: float,
    end_ras_mm: list[float],
) -> list[list[float]]:
    """Absolute RAS polyline: full edges, last original ≤ arc on end edge, then sample."""
    out: list[list[float]] = []
    sample = [float(v) for v in end_ras_mm]
    for ei in route:
        line = np.asarray(edges[ei].get("centreline_mm") or [], dtype=float)
        if ei == end_edge:
            if len(line) == 0:
                out.append(sample)
                break
            keep = last_original_index_at_or_before_arc(edges[ei], end_arc_mm)
            chunk = line[: keep + 1]
            if out and len(chunk) > 0:
                chunk = chunk[1:]
            for p in chunk:
                out.append([float(v) for v in p])
            if not out or float(np.linalg.norm(np.asarray(out[-1]) - sample)) > 1e-9:
                out.append(sample)
            break
        if len(line) == 0:
            continue
        chunk = line
        if out:
            chunk = chunk[1:]
        for p in chunk:
            out.append([float(v) for v in p])
    return out


def _edge_candidates_for_lesion(
    edges: list[dict],
    lesion: np.ndarray,
    start_node: int,
) -> list[dict]:
    """One nearest-CT-selectable densified sample per selectable edge."""
    out: list[dict] = []
    for ei, edge in enumerate(edges):
        if not edge.get("selectable", True):
            continue
        pts = np.asarray(edge.get("centreline_mm") or [], dtype=float)
        if pts.size == 0:
            continue
        samples, arcs = densify_centreline(pts, CANDIDATE_STEP_MM)
        if len(samples) == 0:
            continue
        best_si = None
        best_gap = None
        for si, (pt, arc) in enumerate(zip(samples, arcs)):
            if not _sample_allowed(edge, float(arc)):
                continue
            gap = float(np.linalg.norm(pt - lesion))
            if best_gap is None or gap < best_gap:
                best_gap = gap
                best_si = si
        if best_si is None or best_gap is None:
            continue
        route = route_edges_from_start(edges, start_node, ei)
        if route is None:
            continue
        end_arc = float(arcs[best_si])
        radius = float(edge.get("radius_mm") or 0.0)
        oi = _edge_oi(edge, ei)
        out.append({
            "edge_id": oi,
            "edge_index": ei,
            "sample_index": int(best_si),
            "ras_mm": [round(float(v), 3) for v in samples[best_si]],
            "target_gap_mm": round(best_gap, 2),
            "target_inside_airway": bool(best_gap <= radius),
            "generation": int(edge.get("generation") or 0),
            "route_length_mm": round(
                _route_length_mm(edges, route, ei, end_arc), 2
            ),
            "interpolated_mm_on_route": _interpolated_mm_on_route(
                edges, route, ei, end_arc
            ),
            "lobe": _lobe_label(edge.get("lobe")),
            "airway_name": edge.get("airway_name"),
            "route_edge_ids": [_edge_oi(edges[r], r) for r in route],
            "_sort": (
                best_gap,
                int(edge.get("generation") or 0),
                _route_length_mm(edges, route, ei, end_arc),
                oi,
            ),
        })
    return out


def rank_route_target_candidates(
    graph: dict,
    case: dict,
    lesion_ras: list[float],
    *,
    top_n: int = TOP_N,
    dedup_mm: float = DEDUP_MM,
) -> list[dict]:
    """Return top-N RouteTarget candidates for a lesion RAS point."""
    edges = list(graph.get("edges") or [])
    lesion = np.asarray(lesion_ras, dtype=float)
    start = _route_start_node(graph, case)
    per_edge = _edge_candidates_for_lesion(edges, lesion, start)
    per_edge.sort(key=lambda row: row["_sort"])
    chosen: list[dict] = []
    for row in per_edge:
        if any(c["edge_id"] == row["edge_id"] for c in chosen):
            continue
        pt = np.asarray(row["ras_mm"], dtype=float)
        if any(
            float(np.linalg.norm(pt - np.asarray(c["ras_mm"], dtype=float))) < dedup_mm
            for c in chosen
        ):
            continue
        clean = {k: v for k, v in row.items() if not k.startswith("_") and k != "edge_index"}
        chosen.append(clean)
        if len(chosen) >= top_n:
            break
    return chosen


def list_selectable_ct_samples(graph: dict) -> list[tuple[int, int, np.ndarray, float, dict]]:
    """(edge_index, sample_index, ras, arc_mm, edge) for every selectable CT sample."""
    rows: list[tuple[int, int, np.ndarray, float, dict]] = []
    for ei, edge in enumerate(graph.get("edges") or []):
        if not edge.get("selectable", True):
            continue
        pts = np.asarray(edge.get("centreline_mm") or [], dtype=float)
        if pts.size == 0:
            continue
        samples, arcs = densify_centreline(pts, CANDIDATE_STEP_MM)
        for si, (pt, arc) in enumerate(zip(samples, arcs)):
            if not _sample_allowed(edge, float(arc)):
                continue
            rows.append((ei, int(si), pt, float(arc), edge))
    return rows


def snap_to_selectable_ct(
    graph: dict, ras_mm: list[float], *, max_mm: float = SNAP_MM
) -> dict | None:
    """Nearest selectable CT densified sample within max_mm, or None."""
    target = np.asarray(ras_mm, dtype=float)
    best = None
    for ei, si, pt, arc, edge in list_selectable_ct_samples(graph):
        gap = float(np.linalg.norm(pt - target))
        if gap > max_mm:
            continue
        if best is None or gap < best[0]:
            best = (gap, ei, si, pt, arc, edge)
    if best is None:
        return None
    _, ei, si, pt, arc, edge = best
    return {
        "edge_id": _edge_oi(edge, ei),
        "edge_index": ei,
        "sample_index": si,
        "ras_mm": [round(float(v), 3) for v in pt],
        "arc_mm": arc,
        "edge": edge,
    }


def resolve_edge_sample(
    graph: dict, edge_id: int, sample_index: int
) -> dict:
    """Validate edge_id + densified sample_index on a selectable CT sample."""
    edges = list(graph.get("edges") or [])
    found = None
    found_i = None
    for i, edge in enumerate(edges):
        if _edge_oi(edge, i) == int(edge_id):
            found = edge
            found_i = i
            break
    if found is None or found_i is None:
        raise ValueError(f"unknown edge_id {edge_id}")
    if not found.get("selectable", True):
        raise ValueError(NOT_ON_CT)
    pts = np.asarray(found.get("centreline_mm") or [], dtype=float)
    samples, arcs = densify_centreline(pts, CANDIDATE_STEP_MM)
    if sample_index < 0 or sample_index >= len(samples):
        raise ValueError(f"sample_index {sample_index} out of range")
    arc = float(arcs[sample_index])
    if not _sample_allowed(found, arc):
        raise ValueError(NOT_ON_CT)
    return {
        "edge_id": int(edge_id),
        "edge_index": found_i,
        "sample_index": int(sample_index),
        "ras_mm": [round(float(v), 3) for v in samples[sample_index]],
        "arc_mm": arc,
        "edge": found,
    }


def centreline_index_for_sample(edge: dict, densified_index: int) -> int:
    """Map densified sample index → last original centreline index at or before its arc."""
    pts = np.asarray(edge.get("centreline_mm") or [], dtype=float)
    if len(pts) == 0:
        return 0
    samples, arcs = densify_centreline(pts, CANDIDATE_STEP_MM)
    if densified_index < 0 or densified_index >= len(samples):
        densified_index = min(max(0, densified_index), max(0, len(samples) - 1))
    return last_original_index_at_or_before_arc(edge, float(arcs[densified_index]))


def candidate_metrics_for_sample(
    graph: dict, case: dict, lesion_ras: list[float], resolved: dict
) -> dict:
    """Fill gap / generation / route fields for a resolved sample.

    Raises ValueError with NOT_REACHABLE when the sample's edge is outside
    RouteStart's downstream set.
    """
    edges = list(graph.get("edges") or [])
    ei = int(resolved["edge_index"])
    edge = resolved["edge"]
    start = _route_start_node(graph, case)
    route = route_edges_from_start(edges, start, ei)
    if route is None:
        raise ValueError(NOT_REACHABLE)
    lesion = np.asarray(lesion_ras, dtype=float)
    ras = np.asarray(resolved["ras_mm"], dtype=float)
    gap = float(np.linalg.norm(ras - lesion))
    radius = float(edge.get("radius_mm") or 0.0)
    arc = float(resolved["arc_mm"])
    return {
        "edge_id": int(resolved["edge_id"]),
        "sample_index": int(resolved["sample_index"]),
        "ras_mm": [float(v) for v in resolved["ras_mm"]],
        "target_gap_mm": round(gap, 2),
        "target_inside_airway": bool(gap <= radius),
        "generation": int(edge.get("generation") or 0),
        "route_length_mm": round(_route_length_mm(edges, route, ei, arc), 2),
        "interpolated_mm_on_route": _interpolated_mm_on_route(edges, route, ei, arc),
        "lobe": _lobe_label(edge.get("lobe")),
        "airway_name": edge.get("airway_name"),
        "route_edge_ids": [_edge_oi(edges[r], r) for r in route],
        "node": int(edge["to"]),
        "centreline_index": centreline_index_for_sample(
            edge, int(resolved["sample_index"])
        ),
        "arc_mm": arc,
        "edge_index": ei,
        "route": route,
    }


def stale_reason_for_route_target(
    graph: dict, case: dict, route_target: dict | None
) -> str | None:
    """Return a stale reason string, or None when the stored target is still valid."""
    if not isinstance(route_target, dict) or route_target.get("edge_id") is None:
        return None
    try:
        resolved = resolve_edge_sample(
            graph,
            int(route_target["edge_id"]),
            int(route_target.get("sample_index") or 0),
        )
    except (ValueError, TypeError) as exc:
        return str(exc) or "route target no longer valid"
    start = _route_start_node(graph, case)
    route = route_edges_from_start(
        list(graph.get("edges") or []), start, int(resolved["edge_index"])
    )
    if route is None:
        return NOT_REACHABLE
    return None


def plan_route_target_for_payload(
    effective_graph: dict, case: dict, route_target: dict | None
) -> dict:
    """Payload plan: stale marker, or truncation inputs + stored gap fields."""
    if not isinstance(route_target, dict) or route_target.get("ras_mm") is None:
        return {"active": False}
    reason = stale_reason_for_route_target(effective_graph, case, route_target)
    if reason is not None:
        return {"active": False, "stale": True, "reason": reason}
    edges = list(effective_graph.get("edges") or [])
    try:
        resolved = resolve_edge_sample(
            effective_graph,
            int(route_target["edge_id"]),
            int(route_target.get("sample_index") or 0),
        )
    except ValueError as exc:
        return {"active": False, "stale": True, "reason": str(exc)}
    start = _route_start_node(effective_graph, case)
    route = route_edges_from_start(edges, start, int(resolved["edge_index"]))
    if route is None:
        return {"active": False, "stale": True, "reason": NOT_REACHABLE}
    ras = [float(v) for v in route_target["ras_mm"]]
    return {
        "active": True,
        "stale": False,
        "start_node": start,
        "end_edge": int(resolved["edge_index"]),
        "end_arc_mm": float(resolved["arc_mm"]),
        "end_ras_mm": ras,
        "route": route,
        "target_gap_mm": route_target.get("target_gap_mm"),
        "target_inside_airway": route_target.get("target_inside_airway"),
        "route_target": {
            "edge_id": route_target.get("edge_id"),
            "sample_index": route_target.get("sample_index"),
            "ras_mm": ras,
            "rank": route_target.get("rank"),
            "target_gap_mm": route_target.get("target_gap_mm"),
            "target_inside_airway": route_target.get("target_inside_airway"),
            "confirmed": True,
            "stale": False,
        },
    }


def route_target_candidates(case_id: str) -> list[dict]:
    """Compute top candidates for the case lesion on the effective graph."""
    ref = CaseRef.parse(case_id)
    case = load_case_record(ref)
    lesion = case.get("lesion")
    if not isinstance(lesion, dict) or lesion.get("ras_mm") is None:
        raise WorkflowBlocked(LESION_FIRST)
    eff = effective_for_case(case_id, case)
    cprint(f"route-target candidates for {case_id}", "cyan")
    return rank_route_target_candidates(
        eff["graph"], case, [float(v) for v in lesion["ras_mm"]]
    )


def set_route_target(
    case_id: str,
    clinician: str,
    *,
    edge_id: int | None = None,
    sample_index: int | None = None,
    ras_mm: list[float] | None = None,
) -> dict:
    """Confirm RouteTarget from a candidate or a snapped custom point.

    A Lesion is the only requirement. Metrics and the write share one lock.
    """
    ref = CaseRef.parse(case_id)
    path = ref.case_json
    at = _utc_now()
    with json_file_lock(path):
        record = _load_or_init_record(ref, path)
        lesion = record.get("lesion")
        if not isinstance(lesion, dict) or lesion.get("ras_mm") is None:
            raise WorkflowBlocked(LESION_FIRST)
        lesion_ras = [float(v) for v in lesion["ras_mm"]]
        eff = effective_for_case(case_id, record)
        graph = eff["graph"]

        rank: int | str
        if edge_id is not None and sample_index is not None:
            resolved = resolve_edge_sample(graph, int(edge_id), int(sample_index))
            metrics = candidate_metrics_for_sample(graph, record, lesion_ras, resolved)
            cands = rank_route_target_candidates(graph, record, lesion_ras)
            rank = "custom"
            for i, c in enumerate(cands, 1):
                if (
                    c["edge_id"] == metrics["edge_id"]
                    and c["sample_index"] == metrics["sample_index"]
                ):
                    rank = i
                    break
        elif ras_mm is not None:
            snapped = snap_to_selectable_ct(graph, [float(v) for v in ras_mm])
            if snapped is None:
                raise ValueError(NOT_ON_CT)
            metrics = candidate_metrics_for_sample(graph, record, lesion_ras, snapped)
            rank = "custom"
        else:
            raise ValueError("provide {edge_id, sample_index} or {ras_mm}")

        data = {
            "edge_id": metrics["edge_id"],
            "sample_index": metrics["sample_index"],
            "centreline_index": metrics["centreline_index"],
            "ras_mm": metrics["ras_mm"],
            "node": metrics["node"],
            "rank": rank,
            "target_gap_mm": metrics["target_gap_mm"],
            "target_inside_airway": metrics["target_inside_airway"],
            "generation": metrics["generation"],
            "route_length_mm": metrics["route_length_mm"],
            "interpolated_mm_on_route": metrics["interpolated_mm_on_route"],
            "lobe": metrics["lobe"],
            "airway_name": metrics["airway_name"],
        }
        entry = {
            "step": int(WorkflowStep.ROUTE_TARGET),
            "kind": "route_target_chosen",
            "data": data,
            "clinician": clinician,
            "at": at,
        }
        from app.versions import note_change
        from app.walkthrough import clear_pending_return

        note_change(record, "route_target")
        record.setdefault("records", []).append(entry)
        record["route_target"] = {
            **data,
            "confirmed_by": clinician,
            "at": at,
        }
        clear_pending_return(record)
        atomic_write_json(path, record)
        cprint(
            f"route-target set edge={metrics['edge_id']} "
            f"sample={metrics['sample_index']} rank={rank}",
            "green",
        )
        return {"route_target": record["route_target"], "record": entry}


def resolve_route_end_for_payload(
    effective_graph: dict, route_target: dict
) -> tuple[int, int] | None:
    """Map stored route_target → (effective edge index, last original ≤ sample arc)."""
    if not isinstance(route_target, dict):
        return None
    edge_id = route_target.get("edge_id")
    if edge_id is None:
        return None
    edges = list(effective_graph.get("edges") or [])
    for i, edge in enumerate(edges):
        if _edge_oi(edge, i) != int(edge_id):
            continue
        try:
            resolved = resolve_edge_sample(
                effective_graph, int(edge_id), int(route_target.get("sample_index") or 0)
            )
        except ValueError:
            ci = route_target.get("centreline_index")
            if ci is None:
                return None
            n = len(edge.get("centreline_mm") or [])
            if n == 0:
                return None
            return i, max(0, min(int(ci), n - 1))
        return i, last_original_index_at_or_before_arc(edge, float(resolved["arc_mm"]))
    return None


__all__ = [
    "CANDIDATE_STEP_MM",
    "DEDUP_MM",
    "NOT_ON_CT",
    "NOT_REACHABLE",
    "SNAP_MM",
    "TOP_N",
    "candidate_metrics_for_sample",
    "centreline_index_for_sample",
    "last_original_index_at_or_before_arc",
    "list_selectable_ct_samples",
    "plan_route_target_for_payload",
    "rank_route_target_candidates",
    "resolve_edge_sample",
    "resolve_route_end_for_payload",
    "route_edges_from_start",
    "route_target_candidates",
    "set_route_target",
    "snap_to_selectable_ct",
    "stale_reason_for_route_target",
    "truncate_route_absolute",
]
