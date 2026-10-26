# Changes: Phase 4d review fixes — note_change on review mutate/signoff;
# clear pending_return on re-completion; remove ack_walk_return.
# Phase 4b review fixes — provenance bridge mapping, arc-length
# selectability, nearest-sample manual layer, root-only rematch, app sign-off
# with server `at` only, shared mutator helper.
"""Phase 4b — review & correct: bridges, removal corrections, effective graph.

Bridge↔graph mapping uses densified samples inside labelled bridge tubes
(airway_bridges_labels.nii.gz), not geometry guesses. Selectability is per
arc-length (selectable_until_mm). Manual layer = nearest-centreline-sample
labelling of airway voxels for removed edges only (KD-tree; no tube fallback).
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import uuid
from collections import defaultdict, deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import nibabel as nib
import numpy as np
from atomic_json import atomic_write_json, json_file_lock
from make_prototype_data import review_gate
from review_signoff import (
    KNOWN_KINDS,
    SIGNOFF_DECISIONS,
    iter_valid_signoffs,
    validate_finding,
    valid_reviewer,
)
from scipy.spatial import cKDTree
from termcolor import cprint

from app.caseref import CaseRef
from app.cases import _load_or_init_record, load_case_record
from app.ct_crop import _load_canonical, assert_affine_match
from app.versions import note_change, register_artifact
from app.workflow import WorkflowStep, load_graph, require_prior_steps

NOTE_MAX_CHARS = 300
REMATCH_TOL_MM = 2.0
SAMPLE_STEP_MM = 0.25
DECISIONS = frozenset({"confirmed", "rejected"})
UNMATCHED_BRIDGE_REASON = (
    "bridge {id} not found on the graph — re-run segmentation or review manually"
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def validate_ct_evidence_note(note: object) -> str:
    if not isinstance(note, str):
        raise ValueError("note must be a string (CT evidence)")
    text = note.strip()
    if not text:
        raise ValueError("note is required (CT evidence)")
    if "\n" in note or "\r" in note:
        raise ValueError("note must be a single line")
    if len(text) > NOTE_MAX_CHARS:
        raise ValueError(f"note must be ≤ {NOTE_MAX_CHARS} characters")
    return text


def bridge_stable_id(index: int, bridge: dict) -> str:
    """Stable id = index + sha1 of rounded tree/piece endpoints."""
    tree = [round(float(v), 3) for v in bridge["tree_point_mm"]]
    piece = [round(float(v), 3) for v in bridge["piece_point_mm"]]
    raw = (
        f"{tree[0]:.3f},{tree[1]:.3f},{tree[2]:.3f}|"
        f"{piece[0]:.3f},{piece[1]:.3f},{piece[2]:.3f}"
    )
    digest = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]
    return f"{index}-{digest}"


def load_bridges_annotated(ref: CaseRef) -> list[dict]:
    """Bridges from airway_bridges.json with stable ids and midpoints."""
    path = ref.ts_out / "airway_bridges.json"
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return []
    raw = data.get("bridges") if isinstance(data, dict) else None
    if not isinstance(raw, list):
        return []
    radius = 1.0
    if isinstance(data, dict) and data.get("radius_mm") is not None:
        radius = float(data["radius_mm"])
    out: list[dict] = []
    for i, bridge in enumerate(raw):
        if not isinstance(bridge, dict):
            continue
        if "tree_point_mm" not in bridge or "piece_point_mm" not in bridge:
            continue
        tree = [float(v) for v in bridge["tree_point_mm"]]
        piece = [float(v) for v in bridge["piece_point_mm"]]
        mid = [(tree[j] + piece[j]) / 2.0 for j in range(3)]
        out.append({
            "id": bridge_stable_id(i, bridge),
            "index": i,
            "label": i + 1,
            "gap_mm": bridge.get("gap_mm"),
            "piece_volume_mm3": bridge.get("piece_volume_mm3"),
            "tree_point_mm": tree,
            "piece_point_mm": piece,
            "midpoint_mm": mid,
            "radius_mm": float(bridge.get("radius_mm") or radius),
        })
    return out


def densify_centreline(
    points: np.ndarray, step_mm: float = SAMPLE_STEP_MM
) -> tuple[np.ndarray, np.ndarray]:
    """Densify a polyline to ≤ step_mm; return (samples Nx3, arc_lengths N)."""
    pts = np.asarray(points, dtype=float)
    if pts.size == 0:
        return np.zeros((0, 3), dtype=float), np.zeros(0, dtype=float)
    if len(pts) == 1:
        return pts.copy(), np.zeros(1, dtype=float)
    samples: list[np.ndarray] = []
    arcs: list[float] = []
    offset = 0.0
    for a, b in zip(pts[:-1], pts[1:]):
        delta = b - a
        length = float(np.linalg.norm(delta))
        if length <= 1e-12:
            continue
        n = max(1, int(np.ceil(length / step_mm)))
        for i in range(n):
            t = i / n
            samples.append(a + delta * t)
            arcs.append(offset + t * length)
        offset += length
    samples.append(pts[-1].copy())
    arcs.append(offset)
    return np.asarray(samples, dtype=float), np.asarray(arcs, dtype=float)


def _edge_endpoints(edge: dict) -> tuple[list[float], list[float]]:
    line = edge.get("centreline_mm") or []
    if not line:
        return [0.0, 0.0, 0.0], [0.0, 0.0, 0.0]
    start = [float(v) for v in line[0]]
    end = [float(v) for v in line[-1]]
    return start, end


def rematch_edge_by_endpoints(
    graph: dict,
    start_mm: list[float],
    end_mm: list[float],
    tol_mm: float = REMATCH_TOL_MM,
) -> int | None:
    """Edge whose own endpoints are within tol of both stored endpoints."""
    a = np.asarray(start_mm, dtype=float)
    b = np.asarray(end_mm, dtype=float)
    best_i: int | None = None
    best_score = float("inf")
    for i, edge in enumerate(graph.get("edges") or []):
        e0, e1 = _edge_endpoints(edge)
        p0 = np.asarray(e0, dtype=float)
        p1 = np.asarray(e1, dtype=float)
        forward = float(np.linalg.norm(a - p0)) <= tol_mm and float(
            np.linalg.norm(b - p1)
        ) <= tol_mm
        reverse = float(np.linalg.norm(a - p1)) <= tol_mm and float(
            np.linalg.norm(b - p0)
        ) <= tol_mm
        if not (forward or reverse):
            continue
        score = min(
            float(np.linalg.norm(a - p0) + np.linalg.norm(b - p1)),
            float(np.linalg.norm(a - p1) + np.linalg.norm(b - p0)),
        )
        if score < best_score:
            best_score = score
            best_i = i
    return best_i


def _children_map(edges: list[dict]) -> dict[int, list[int]]:
    kids: dict[int, list[int]] = {}
    for i, edge in enumerate(edges):
        kids.setdefault(int(edge["from"]), []).append(i)
    return kids


def subtree_edge_ids(edges: list[dict], start_edge: int) -> list[int]:
    """Edge index plus every edge reachable from its `to` node."""
    kids = _children_map(edges)
    out = [start_edge]
    seen_nodes = {int(edges[start_edge]["to"])}
    q = deque([int(edges[start_edge]["to"])])
    while q:
        node = q.popleft()
        for ei in kids.get(node, []):
            out.append(ei)
            nxt = int(edges[ei]["to"])
            if nxt not in seen_nodes:
                seen_nodes.add(nxt)
                q.append(nxt)
    return out


def _paint_tube(
    volume: np.ndarray,
    affine: np.ndarray,
    start: np.ndarray,
    end: np.ndarray,
    radius_mm: float,
    label: int,
) -> None:
    """Paint a labelled tube between two points into volume (in place)."""
    inv = np.linalg.inv(affine)
    pts, _ = densify_centreline(np.vstack([start, end]), step_mm=min(SAMPLE_STEP_MM, radius_mm / 2))
    spacing = np.abs(np.diag(affine)[:3])
    radius_vox = max(1, int(np.ceil(radius_mm / float(np.min(spacing)) + 1)))
    shape = np.asarray(volume.shape)
    for point in pts:
        vox = (inv @ np.array([point[0], point[1], point[2], 1.0], dtype=float))[:3]
        centre = np.rint(vox).astype(int)
        for di in range(-radius_vox, radius_vox + 1):
            for dj in range(-radius_vox, radius_vox + 1):
                for dk in range(-radius_vox, radius_vox + 1):
                    ijk = centre + np.array([di, dj, dk])
                    if np.any(ijk < 0) or np.any(ijk >= shape):
                        continue
                    world = affine @ np.array(
                        [ijk[0], ijk[1], ijk[2], 1.0], dtype=float
                    )
                    if float(np.linalg.norm(world[:3] - point)) <= radius_mm + 0.5:
                        volume[tuple(ijk)] = label


def build_bridge_labels_volume(
    shape: tuple[int, ...],
    affine: np.ndarray,
    bridges: list[dict],
) -> np.ndarray:
    """Labelled companion from bridge JSON endpoints + radius (review layer)."""
    labels = np.zeros(shape, dtype=np.uint16)
    for bridge in bridges:
        start = np.asarray(bridge["tree_point_mm"], dtype=float)
        end = np.asarray(bridge["piece_point_mm"], dtype=float)
        radius = max(0.5, float(bridge.get("radius_mm") or 1.0))
        _paint_tube(labels, affine, start, end, radius, int(bridge["label"]))
    return labels


def atomic_write_nifti(path: Path, data: np.ndarray, affine: np.ndarray, header) -> None:
    """Write NIfTI via temp file + os.replace."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    hdr = header.copy()
    hdr.set_data_dtype(data.dtype)
    tmp_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp.nii.gz",
            delete=False,
        ) as tmp:
            tmp_name = tmp.name
        nib.save(nib.Nifti1Image(data, affine, hdr), tmp_name)
        os.replace(tmp_name, path)
        tmp_name = None
    finally:
        if tmp_name is not None:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass


def ensure_bridge_labels(ref: CaseRef, bridges: list[dict]) -> Path:
    """Write airway_bridges_labels.nii.gz from bridge JSON (does not alter pipeline files)."""
    cprint(f"writing bridge labels for {ref.case_id}", "cyan")
    airways_img = _load_canonical(ref.airways)
    affine = np.asarray(airways_img.affine, dtype=float)
    labels = build_bridge_labels_volume(airways_img.shape, affine, bridges)
    out = ref.ts_out / "airway_bridges_labels.nii.gz"
    atomic_write_nifti(out, labels, affine, airways_img.header)
    assert_affine_match(affine, np.asarray(nib.load(out).affine, dtype=float), "bridge_labels")
    return out


def match_bridge_spans(
    graph: dict,
    bridge: dict,
    labels: np.ndarray,
    affine: np.ndarray,
) -> list[dict[str, Any]]:
    """Shared geometry helper: densified samples inside this bridge's label voxels.

    Returns list of {edge_index, start_mm, end_mm} arc-length spans.
    """
    label = int(bridge["label"])
    inv = np.linalg.inv(affine)
    shape = np.asarray(labels.shape)
    per_edge: dict[int, list[float]] = defaultdict(list)
    for ei, edge in enumerate(graph.get("edges") or []):
        pts = np.asarray(edge.get("centreline_mm") or [], dtype=float)
        if pts.size == 0:
            continue
        samples, arcs = densify_centreline(pts)
        for sample, arc in zip(samples, arcs):
            vox = np.rint((inv @ np.r_[sample, 1.0])[:3]).astype(int)
            if np.any(vox < 0) or np.any(vox >= shape):
                continue
            if int(labels[tuple(vox)]) == label:
                per_edge[ei].append(float(arc))
    spans: list[dict[str, Any]] = []
    for ei, arcs in per_edge.items():
        if not arcs:
            continue
        spans.append({
            "edge_index": ei,
            "start_mm": round(min(arcs), 3),
            "end_mm": round(max(arcs), 3),
        })
    spans.sort(key=lambda s: (s["start_mm"], s["edge_index"]))
    return spans


def map_all_bridges(
    graph: dict,
    bridges: list[dict],
    ref: CaseRef,
) -> dict[str, list[dict[str, Any]]]:
    """Map each bridge id → arc-length spans on the graph (writes labels companion)."""
    if not bridges:
        return {}
    ensure_bridge_labels(ref, bridges)
    airways_img = _load_canonical(ref.airways)
    affine = np.asarray(airways_img.affine, dtype=float)
    labels_path = ref.ts_out / "airway_bridges_labels.nii.gz"
    labels = np.asanyarray(nib.load(labels_path).dataobj)
    return {
        bridge["id"]: match_bridge_spans(graph, bridge, labels, affine)
        for bridge in bridges
    }


def unmatched_bridge_ids(
    graph: dict, bridges: list[dict], ref: CaseRef,
    bridge_map: dict[str, list[dict]] | None = None,
) -> list[str]:
    mapping = bridge_map if bridge_map is not None else map_all_bridges(graph, bridges, ref)
    return [b["id"] for b in bridges if not mapping.get(b["id"])]


def unmatched_bridge_reason(bridge_id: str) -> str:
    return UNMATCHED_BRIDGE_REASON.format(id=bridge_id)


def _active_corrections(case: dict, graph: dict) -> tuple[list[dict], set[int], dict[int, str]]:
    """Root-only rematch; subtree re-derived. Returns corrections, removed idxs, oi→cid."""
    corrections = case.get("corrections") or []
    if not isinstance(corrections, list):
        return [], set(), {}
    removed: set[int] = set()
    oi_to_cid: dict[int, str] = {}
    updated: list[dict] = []
    edges = graph.get("edges") or []
    for corr in corrections:
        if not isinstance(corr, dict) or corr.get("undone"):
            updated.append(corr if isinstance(corr, dict) else {})
            continue
        root = corr.get("root_edge")
        if not isinstance(root, dict):
            # Legacy: first removed_edges entry is the root.
            specs = corr.get("removed_edges") or []
            root = specs[0] if specs else None
        entry = dict(corr)
        if not isinstance(root, dict) or not root.get("start_mm") or not root.get("end_mm"):
            entry["status"] = "stale"
            updated.append(entry)
            continue
        ei = rematch_edge_by_endpoints(graph, root["start_mm"], root["end_mm"])
        if ei is None or ei < 0 or ei >= len(edges):
            entry["status"] = "stale"
            updated.append(entry)
            continue
        entry["status"] = "active"
        entry["matched_edge_id"] = ei
        sub = subtree_edge_ids(edges, ei)
        cid = str(entry.get("id") or "")
        for sub_i in sub:
            removed.add(sub_i)
            if cid:
                oi_to_cid[sub_i] = cid
        # Refresh removed_edges from the new graph subtree.
        entry["removed_edges"] = [
            {
                "edge_id": sub_i,
                "start_mm": _edge_endpoints(edges[sub_i])[0],
                "end_mm": _edge_endpoints(edges[sub_i])[1],
            }
            for sub_i in sub
        ]
        updated.append(entry)
    return updated, removed, oi_to_cid


def has_stale_correction(
    case: dict, graph: dict | None = None, *, ref: CaseRef | None = None
) -> bool:
    if graph is None:
        if ref is None:
            raise ValueError("has_stale_correction needs graph or ref")
        try:
            graph = load_graph(ref=ref)
        except ValueError:
            return False
    corrections, _, _ = _active_corrections(case, graph)
    return any(c.get("status") == "stale" and not c.get("undone") for c in corrections)


def compute_effective_graph(
    graph: dict,
    case: dict,
    bridges: list[dict],
    *,
    ref: CaseRef | None = None,
    bridge_map: dict[str, list[dict]] | None = None,
) -> dict[str, Any]:
    """Derive effective edges, removed (ghosted), selectable + selectable_until_mm.

    `bridges` is required (no `_bridges` hook). Pass `bridge_map` to avoid I/O
    in unit tests; otherwise `ref` is required when bridges is non-empty.
    """
    edges = list(graph.get("edges") or [])
    decisions = case.get("bridge_decisions") or {}
    if not isinstance(decisions, dict):
        decisions = {}

    if bridge_map is None:
        if bridges and ref is None:
            raise ValueError("compute_effective_graph requires ref or bridge_map")
        bridge_map = map_all_bridges(graph, bridges, ref) if bridges else {}

    corrections, corr_removed, oi_to_cid = _active_corrections(case, graph)
    removed: set[int] = set(corr_removed)

    # selectable_until_mm[ei] = arc length; None means unset (whole edge if selectable)
    selectable_until: dict[int, float | None] = {}
    not_selectable: set[int] = set()
    rejected_bridge_edges: set[int] = set()
    unconfirmed_bridge_edges: set[int] = set()
    unmatched: list[str] = []

    for bridge in bridges:
        bid = bridge["id"]
        spans = bridge_map.get(bid) or []
        if not spans:
            unmatched.append(bid)
            continue
        decision = decisions.get(bid)
        state = decision.get("decision") if isinstance(decision, dict) else None
        primary = min(spans, key=lambda s: s["start_mm"])
        primary_ei = int(primary["edge_index"])
        span_start = float(primary["start_mm"])

        if state == "rejected":
            rejected_bridge_edges.add(primary_ei)
            if span_start <= 1e-6:
                for sub in subtree_edge_ids(edges, primary_ei):
                    removed.add(sub)
            else:
                selectable_until[primary_ei] = span_start
                # Downstream subtree (children of `to`) removed; prefix stays.
                kids = _children_map(edges)
                for ei in kids.get(int(edges[primary_ei]["to"]), []):
                    for sub in subtree_edge_ids(edges, ei):
                        removed.add(sub)
        elif state != "confirmed":
            unconfirmed_bridge_edges.add(primary_ei)
            selectable_until[primary_ei] = span_start
            for sub in subtree_edge_ids(edges, primary_ei):
                if sub != primary_ei:
                    not_selectable.add(sub)

    effective_idxs = [i for i in range(len(edges)) if i not in removed]

    # Reachability for selectability: cannot cross past unconfirmed bridge span.
    kids = _children_map(edges)
    root = int(graph["root_node_id"])
    selectable_nodes = {root}
    q = deque([root])
    while q:
        node = q.popleft()
        for ei in kids.get(node, []):
            if ei in removed:
                continue
            if ei in unconfirmed_bridge_edges:
                # Edge itself may be partially selectable; do not enter `to`.
                continue
            if ei in not_selectable:
                continue
            nxt = int(edges[ei]["to"])
            if nxt not in selectable_nodes:
                selectable_nodes.add(nxt)
                q.append(nxt)

    removed_edges = []
    for i in sorted(removed):
        edge = dict(edges[i])
        edge["_original_index"] = i
        edge["selectable"] = False
        edge["selectable_until_mm"] = None
        if i in oi_to_cid:
            edge["correction_id"] = oi_to_cid[i]
        removed_edges.append(edge)

    effective_edges = []
    index_map: dict[int, int] = {}
    for new_i, old_i in enumerate(effective_idxs):
        index_map[old_i] = new_i
        edge = dict(edges[old_i])
        edge["_original_index"] = old_i
        until = selectable_until.get(old_i)
        if old_i in not_selectable or (
            old_i in unconfirmed_bridge_edges and (until is None or until <= 1e-6)
        ):
            edge["selectable"] = False
            edge["selectable_until_mm"] = until
        elif old_i in unconfirmed_bridge_edges or until is not None:
            edge["selectable"] = True
            edge["selectable_until_mm"] = until
        else:
            edge["selectable"] = int(edges[old_i]["from"]) in selectable_nodes
            edge["selectable_until_mm"] = None
        effective_edges.append(edge)

    effective_graph = dict(graph)
    effective_graph["edges"] = effective_edges
    used_nodes = {int(effective_graph["root_node_id"])}
    for edge in effective_edges:
        used_nodes.add(int(edge["from"]))
        used_nodes.add(int(edge["to"]))
    effective_graph["nodes"] = [
        n for n in graph.get("nodes") or [] if int(n["id"]) in used_nodes
    ]

    return {
        "graph": effective_graph,
        "removed": removed_edges,
        "corrections": corrections,
        "bridge_map": bridge_map,
        "index_map": index_map,
        "unconfirmed_bridge_edges": sorted(unconfirmed_bridge_edges),
        "rejected_bridge_edges": sorted(rejected_bridge_edges),
        "unmatched_bridges": unmatched,
        "oi_to_correction": oi_to_cid,
    }


def effective_for_case(case_id: str, case: dict | None = None) -> dict[str, Any]:
    ref = CaseRef.parse(case_id)
    if case is None:
        case = load_case_record(ref)
    graph = load_graph(ref=ref)
    bridges = load_bridges_annotated(ref)
    return compute_effective_graph(graph, case, bridges, ref=ref)


def review_state(case_id: str) -> dict[str, Any]:
    """GET /api/cases/{id}/review payload."""
    ref = CaseRef.parse(case_id)
    case = load_case_record(ref)
    graph = load_graph(ref=ref)
    bridges = load_bridges_annotated(ref)
    decisions = case.get("bridge_decisions") or {}
    if not isinstance(decisions, dict):
        decisions = {}
    bridge_map = map_all_bridges(graph, bridges, ref) if bridges else {}
    bridge_rows = []
    for bridge in bridges:
        decision = decisions.get(bridge["id"])
        spans = bridge_map.get(bridge["id"]) or []
        bridge_rows.append({
            **bridge,
            "decision": decision if isinstance(decision, dict) else None,
            "matched": bool(spans),
            "spans": spans,
        })

    review_flags = graph.get("review") or []
    signoffs = iter_valid_signoffs(case.get("review_signoff") or [])
    approved = {
        (s["kind"], s["lobe"]): s
        for s in signoffs
        if s.get("decision") == "approved"
    }
    all_signoffs = {(s["kind"], s["lobe"]): s for s in signoffs}
    flag_rows = []
    for entry in review_flags:
        key = (entry.get("kind"), entry.get("lobe"))
        so = all_signoffs.get(key)
        flag_rows.append({
            **entry,
            "signoff": so,
            "resolved": key in approved,
        })

    eff = compute_effective_graph(graph, case, bridges, ref=ref, bridge_map=bridge_map)
    return {
        "case": case_id,
        "bridges": bridge_rows,
        "review_flags": flag_rows,
        "corrections": [
            c for c in eff["corrections"] if isinstance(c, dict) and not c.get("undone")
        ],
        "unmatched_bridges": eff["unmatched_bridges"],
    }


def _steps_invalidated_by_review(case: dict, ref: CaseRef) -> list[int]:
    """Steps ≥5 that have completion records (review change invalidates them)."""
    del ref  # case id already scoped by caller
    found: set[int] = set()
    for rec in case.get("records") or []:
        if not isinstance(rec, dict):
            continue
        step = rec.get("step")
        if isinstance(step, int) and step >= int(WorkflowStep.ROUTE_START):
            found.add(step)
    if case.get("versions") and any(
        isinstance(v, dict) and v.get("frozen") for v in case["versions"]
    ):
        found.add(int(WorkflowStep.SIGNOFF))
    return sorted(found)


def compute_manual_labels(
    airways: np.ndarray,
    affine: np.ndarray,
    graph_edges: list[dict],
    removed_ois: set[int],
) -> tuple[np.ndarray, int]:
    """Nearest-sample labelling: airway voxels whose nearest centreline sample
    belongs to a removed edge. Vectorised via KD-tree. Returns (mask, n_vox)."""
    manual = np.zeros(airways.shape, dtype=np.uint8)
    if not removed_ois or not np.any(airways):
        return manual, 0

    sample_pts: list[np.ndarray] = []
    sample_oi: list[int] = []
    for oi, edge in enumerate(graph_edges):
        pts = np.asarray(edge.get("centreline_mm") or [], dtype=float)
        if pts.size == 0:
            continue
        dens, _ = densify_centreline(pts)
        for p in dens:
            sample_pts.append(p)
            sample_oi.append(oi)
    if not sample_pts:
        return manual, 0

    samples = np.asarray(sample_pts, dtype=float)
    oi_arr = np.asarray(sample_oi, dtype=int)
    tree = cKDTree(samples)

    air_idx = np.argwhere(airways)
    # Voxel centres in world mm.
    ones = np.ones((len(air_idx), 1), dtype=float)
    ijk_h = np.hstack([air_idx.astype(float), ones])
    world = (affine @ ijk_h.T).T[:, :3]
    _, nn = tree.query(world, k=1)
    nn = np.asarray(nn, dtype=int)
    hit = np.isin(oi_arr[nn], list(removed_ois))
    if not np.any(hit):
        return manual, 0
    hit_ijk = air_idx[hit]
    manual[hit_ijk[:, 0], hit_ijk[:, 1], hit_ijk[:, 2]] = 1
    return manual, int(hit_ijk.shape[0])


def write_manual_mask(
    ref: CaseRef,
    graph: dict,
    removed_edges: list[dict],
) -> tuple[Path, int]:
    """Rasterise removed-branch voxels into airway_manual.nii.gz (never touch airways).

    Nearest graph centreline sample among all edges; no tube fallback.
    Returns (path, voxel_count). Must be called OUTSIDE the case.json lock.
    """
    cprint(f"writing manual mask for {ref.case_id}", "cyan")
    airways_img = _load_canonical(ref.airways)
    affine = np.asarray(airways_img.affine, dtype=float)
    airways = np.asanyarray(airways_img.dataobj) > 0
    removed_ois = {
        int(e["_original_index"])
        for e in removed_edges
        if e.get("_original_index") is not None
    }
    manual, n_vox = compute_manual_labels(
        airways, affine, list(graph.get("edges") or []), removed_ois
    )
    out = ref.ts_out / "airway_manual.nii.gz"
    atomic_write_nifti(out, manual, affine, airways_img.header)
    assert_affine_match(affine, np.asarray(nib.load(out).affine, dtype=float), "manual")
    return out, n_vox


def _register_paths(ref: CaseRef, case: dict, eff_path: Path, manual_path: Path) -> None:
    register_artifact(case, "effective_graph", eff_path)
    register_artifact(case, "manual_mask", manual_path)


def _commit_review_state(
    ref: CaseRef,
    record: dict,
    graph: dict,
    bridges: list[dict],
    *,
    bridge_map: dict[str, list[dict]] | None = None,
) -> dict[str, Any]:
    """Compute effective graph, write JSON under lock; caller writes manual outside."""
    effective = compute_effective_graph(
        graph, record, bridges, ref=ref, bridge_map=bridge_map
    )
    record["corrections"] = [
        c for c in effective["corrections"] if isinstance(c, dict)
    ]
    eff_path = ref.ts_out / "effective_graph.json"
    ref.ts_out.mkdir(parents=True, exist_ok=True)
    atomic_write_json(eff_path, effective["graph"])
    effective["eff_path"] = eff_path
    return effective


def apply_review_mutation(
    case_id: str,
    mutator: Callable[[dict, dict, list[dict], dict[str, Any]], dict],
) -> dict:
    """Shared load→mutate→compute→write helper for all review mutators.

    Manual mask KD-tree compute runs OUTSIDE the case.json lock.
    """
    require_prior_steps(case_id, int(WorkflowStep.REVIEW))
    ref = CaseRef.parse(case_id)
    path = ref.case_json
    graph = load_graph(ref=ref)
    bridges = load_bridges_annotated(ref)
    bridge_map = map_all_bridges(graph, bridges, ref) if bridges else {}

    with json_file_lock(path):
        record = _load_or_init_record(ref, path)
        from app.walkthrough import clear_pending_return, pending_return_step

        note_change(record, "review")
        if pending_return_step(record) == int(WorkflowStep.REVIEW):
            clear_pending_return(record)
        ctx = {
            "at": _utc_now(),
            "bridge_map": bridge_map,
            "invalidates": _steps_invalidated_by_review(record, ref),
        }
        result = mutator(record, graph, bridges, ctx)
        effective = _commit_review_state(
            ref, record, graph, bridges, bridge_map=bridge_map
        )
        removed_snapshot = list(effective["removed"])
        atomic_write_json(path, record)

    # Heavy mask compute OUTSIDE the lock.
    manual_path, n_vox = write_manual_mask(ref, graph, removed_snapshot)

    with json_file_lock(path):
        record = _load_or_init_record(ref, path)
        # Attach zero-voxel warnings to active corrections that contributed.
        if n_vox == 0 and removed_snapshot:
            for corr in record.get("corrections") or []:
                if not isinstance(corr, dict) or corr.get("undone"):
                    continue
                if corr.get("status") == "stale":
                    continue
                corr.setdefault(
                    "warnings",
                    [],
                )
                warn = "manual layer painted zero voxels (no tube fallback)"
                if warn not in corr["warnings"]:
                    corr["warnings"].append(warn)
                    cprint(f"correction {corr.get('id')}: {warn}", "yellow")
        _register_paths(ref, record, effective["eff_path"], manual_path)
        atomic_write_json(path, record)

    result["manual_voxels"] = n_vox
    return result


def set_bridge_decision(
    case_id: str, bridge_id: str, decision: str, note: str, clinician: str
) -> dict:
    if decision not in DECISIONS:
        raise ValueError(f"decision must be one of {sorted(DECISIONS)}")
    note_text = validate_ct_evidence_note(note)

    def mutate(record, graph, bridges, ctx):
        by_id = {b["id"]: b for b in bridges}
        if bridge_id not in by_id:
            raise ValueError(f"unknown bridge id {bridge_id!r}")
        spans = (ctx.get("bridge_map") or {}).get(bridge_id) or []
        if not spans:
            raise ValueError(unmatched_bridge_reason(bridge_id))
        decisions = record.setdefault("bridge_decisions", {})
        entry_data = {
            "bridge_id": bridge_id,
            "decision": decision,
            "note": note_text,
            "invalidates": list(ctx.get("invalidates") or []),
        }
        decisions[bridge_id] = {
            **entry_data,
            "clinician": clinician,
            "at": ctx["at"],
        }
        rec = {
            "step": int(WorkflowStep.REVIEW),
            "kind": "bridge_decision",
            "data": entry_data,
            "clinician": clinician,
            "at": ctx["at"],
        }
        record.setdefault("records", []).append(rec)
        return {"decision": decisions[bridge_id], "record": rec}

    return apply_review_mutation(case_id, mutate)


def add_correction(
    case_id: str, edge_id: int, note: str, clinician: str
) -> dict:
    """POST remove_branch correction — edge + downstream subtree."""
    note_text = validate_ct_evidence_note(note)
    if not isinstance(edge_id, int) or isinstance(edge_id, bool):
        raise ValueError("edge_id must be an integer")

    def mutate(record, graph, bridges, ctx):
        edges = graph.get("edges") or []
        if edge_id < 0 or edge_id >= len(edges):
            raise ValueError(f"unknown edge_id {edge_id}")
        eff = compute_effective_graph(
            graph, record, bridges, ref=None, bridge_map=ctx.get("bridge_map") or {}
        )
        effective_ois = {e["_original_index"] for e in eff["graph"]["edges"]}
        if edge_id not in effective_ois:
            raise ValueError(f"edge_id {edge_id} is already removed or not in graph")
        # Removal of not-selectable branches is allowed.
        sub = subtree_edge_ids(edges, edge_id)
        start, end = _edge_endpoints(edges[edge_id])
        root_edge = {"edge_id": edge_id, "start_mm": start, "end_mm": end}
        removed_specs = []
        for ei in sub:
            s, e = _edge_endpoints(edges[ei])
            removed_specs.append({"edge_id": ei, "start_mm": s, "end_mm": e})
        cid = str(uuid.uuid4())
        correction = {
            "id": cid,
            "kind": "remove_branch",
            "edge_id": edge_id,
            "root_edge": root_edge,
            "note": note_text,
            "removed_edges": removed_specs,
            "status": "active",
            "clinician": clinician,
            "at": ctx["at"],
            "undone": False,
            "invalidates": list(ctx.get("invalidates") or []),
            "warnings": [],
        }
        record.setdefault("corrections", []).append(correction)
        rec = {
            "step": int(WorkflowStep.REVIEW),
            "kind": "correction",
            "data": {
                "action": "remove_branch",
                "correction_id": cid,
                "edge_id": edge_id,
                "removed_edge_ids": [s["edge_id"] for s in removed_specs],
                "note": note_text,
                "invalidates": list(ctx.get("invalidates") or []),
            },
            "clinician": clinician,
            "at": ctx["at"],
        }
        record.setdefault("records", []).append(rec)
        return {"correction": correction, "record": rec}

    return apply_review_mutation(case_id, mutate)


def undo_correction(case_id: str, correction_id: str, clinician: str) -> dict:
    def mutate(record, graph, bridges, ctx):
        corrections = record.setdefault("corrections", [])
        found = None
        for corr in corrections:
            if isinstance(corr, dict) and corr.get("id") == correction_id:
                found = corr
                break
        if found is None:
            raise ValueError(f"unknown correction {correction_id!r}")
        if found.get("undone"):
            raise ValueError(f"correction {correction_id} already undone")
        found["undone"] = True
        found["undone_at"] = ctx["at"]
        found["undone_by"] = clinician
        found["status"] = "undone"
        found["invalidates"] = list(ctx.get("invalidates") or [])
        rec = {
            "step": int(WorkflowStep.REVIEW),
            "kind": "correction",
            "data": {
                "action": "undo",
                "correction_id": correction_id,
                "invalidates": list(ctx.get("invalidates") or []),
            },
            "clinician": clinician,
            "at": ctx["at"],
        }
        record.setdefault("records", []).append(rec)
        return {"correction": found, "record": rec}

    return apply_review_mutation(case_id, mutate)


def apply_review_signoff(
    case_id: str,
    *,
    kind: str,
    lobe: str,
    decision: str,
    finding: str,
    clinician: str,
) -> dict:
    """App sign-off: server stamps `at`; no client date; stored only in review_signoff."""
    require_prior_steps(case_id, int(WorkflowStep.REVIEW))
    if kind not in KNOWN_KINDS:
        raise ValueError(f"kind must be one of {sorted(KNOWN_KINDS)}")
    if decision not in SIGNOFF_DECISIONS:
        raise ValueError(f"decision must be one of {sorted(SIGNOFF_DECISIONS)}")
    if not isinstance(lobe, str) or not lobe.strip():
        raise ValueError("lobe is required")
    finding_text = validate_finding(finding)
    if not valid_reviewer(clinician):
        raise ValueError("clinician must be role + initials")

    ref = CaseRef.parse(case_id)
    graph = load_graph(ref=ref)
    flags = {
        (e.get("kind"), e.get("lobe"))
        for e in (graph.get("review") or [])
        if isinstance(e, dict)
    }
    if (kind, lobe) not in flags:
        raise ValueError(f"no review flag {kind!r} / {lobe!r} on graph")

    path = ref.case_json
    at = _utc_now()
    with json_file_lock(path):
        record = _load_or_init_record(ref, path)
        from app.walkthrough import clear_pending_return, pending_return_step

        note_change(record, "review_signoff")
        if pending_return_step(record) == int(WorkflowStep.REVIEW):
            clear_pending_return(record)
        entries = record.setdefault("review_signoff", [])
        entry = {
            "kind": kind,
            "lobe": lobe,
            "decision": decision,
            "finding": finding_text,
            "reviewer": clinician,
            "at": at,
        }
        entries[:] = [
            old for old in entries
            if not (
                isinstance(old, dict)
                and (old.get("kind"), old.get("lobe")) == (kind, lobe)
            )
        ]
        entries.append(entry)
        atomic_write_json(path, record)
        return {"signoff": entry}


def bridges_complete(case: dict, ref: CaseRef) -> bool:
    bridges = load_bridges_annotated(ref)
    if not bridges:
        return True
    try:
        graph = load_graph(ref=ref)
    except ValueError:
        return False
    mapping = map_all_bridges(graph, bridges, ref)
    if any(not mapping.get(b["id"]) for b in bridges):
        return False
    decisions = case.get("bridge_decisions") or {}
    if not isinstance(decisions, dict):
        return False
    for bridge in bridges:
        entry = decisions.get(bridge["id"])
        if not isinstance(entry, dict) or entry.get("decision") not in DECISIONS:
            return False
    return True


def review_flags_approved(case: dict, ref: CaseRef) -> bool:
    try:
        graph = load_graph(ref=ref)
    except ValueError:
        return False
    review = graph.get("review") or []
    if not review:
        return True
    signoffs = iter_valid_signoffs(case.get("review_signoff") or [])
    _, unresolved, _ = review_gate(review, signoffs)
    return len(unresolved) == 0


def review_block_reason(case: dict, ref: CaseRef) -> str | None:
    """Reason string when step 4 cannot complete (unmatched bridge, etc.)."""
    bridges = load_bridges_annotated(ref)
    if not bridges:
        return None
    try:
        graph = load_graph(ref=ref)
    except ValueError:
        return None
    for bid in unmatched_bridge_ids(graph, bridges, ref):
        return unmatched_bridge_reason(bid)
    return None


__all__ = [
    "NOTE_MAX_CHARS",
    "add_correction",
    "apply_review_mutation",
    "apply_review_signoff",
    "bridge_stable_id",
    "bridges_complete",
    "compute_effective_graph",
    "compute_manual_labels",
    "densify_centreline",
    "effective_for_case",
    "ensure_bridge_labels",
    "has_stale_correction",
    "load_bridges_annotated",
    "map_all_bridges",
    "match_bridge_spans",
    "rematch_edge_by_endpoints",
    "review_block_reason",
    "review_flags_approved",
    "review_state",
    "set_bridge_decision",
    "subtree_edge_ids",
    "undo_correction",
    "unmatched_bridge_reason",
    "validate_ct_evidence_note",
    "write_manual_mask",
]
