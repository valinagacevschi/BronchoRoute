# Changes: estimate CT terminals along the paired artery, cap the arc at 15 mm,
# and keep the polylines out of edges and nodes.
"""Estimated airway: extend each CT-seen terminal along its paired artery.

The walk follows research/artery_follow.py (pair within 8 mm, alongside, within
35°, straightest forward step, stop on a sharp turn, leaving the lung, a lobe
change, or the cap). Artery skeletons are kimimaro space; positions are moved
into patient RAS with the airway affine before pairing, then shifted by
tip − artery start so the polyline begins at the CT tip. Arc length is cut at
exactly 15 mm. Nothing here is added to the navigable graph.
"""

from __future__ import annotations

from pathlib import Path

import kimimaro
import networkx as nx
import nibabel as nib
import numpy as np
from scipy.spatial import cKDTree

from build_graph import TEASAR, to_patient_mm

PAIR_MAX_MM = 8.0
ALONG_MAX_MM = 6.0
PAIR_MAX_DEG = 35.0
TURN_MAX_DEG = 45.0
MAX_MM = 15.0
MIN_MM = 1.0
SAMPLE_MM = 0.5
_HEADING_MM = 3.0
_TIP_MM = 5.0


def load_artery_mask(path: Path | str | None) -> np.ndarray | None:
    """Return the artery mask, or None when the path was not given or is absent."""
    if path is None:
        return None
    path = Path(path)
    if not path.is_file():
        return None
    return np.asanyarray(nib.load(str(path)).dataobj) > 0


def estimate_airway(
    edges: list[dict],
    artery_mask: np.ndarray | None,
    lobes: np.ndarray | None,
    spacing,
    affine: np.ndarray,
) -> tuple[list[dict], dict]:
    """Return estimated segments and the summary. Does not mutate edges."""
    terminals = _eligible_terminals(edges)
    summary = _summary(len(terminals), [])
    if not terminals or artery_mask is None or lobes is None:
        return [], summary
    if tuple(artery_mask.shape) != tuple(np.shape(lobes)):
        return [], summary
    graph = _artery_graph(artery_mask, spacing, affine)
    if graph.number_of_nodes() == 0:
        return [], summary

    ids = list(graph.nodes)
    positions = np.array([graph.nodes[node]["pos"] for node in ids], dtype=float)
    tree = cKDTree(positions)
    inverse = np.linalg.inv(np.asarray(affine, dtype=float))
    directions: dict[int, np.ndarray] = {}
    segments: list[dict] = []
    for index in terminals:
        edge = edges[index]
        centreline = np.asarray(edge["centreline_mm"], dtype=float)
        tip = centreline[-1]
        direction = _tip_direction(centreline)
        if float(np.linalg.norm(direction)) == 0.0:
            continue
        start = _pair(graph, ids, positions, tree, tip, direction, directions)
        if start is None:
            continue
        path, _length, stop = _walk(
            graph, start, direction, lobes, int(edge["lobe"]), inverse,
        )
        segment = _shift_and_cut(path, tip, stop, index)
        if segment is not None:
            segments.append(segment)
    return segments, _summary(len(terminals), segments)


def _eligible_terminals(edges: list[dict]) -> list[int]:
    starts = {edge.get("from") for edge in edges}
    chosen: list[int] = []
    for index, edge in enumerate(edges):
        if edge.get("to") in starts:
            continue
        if _lobe_id(edge.get("lobe")) is None:
            continue
        centreline = edge.get("centreline_mm") or []
        if len(centreline) < 2 or not _tip_is_ct(edge):
            continue
        chosen.append(index)
    return chosen


def _lobe_id(value) -> int | None:
    if isinstance(value, bool) or not isinstance(value, (int, np.integer)):
        return None
    lobe = int(value)
    return lobe if lobe > 0 else None


def _tip_is_ct(edge: dict) -> bool:
    """Same rule as edge_provenance_at at the distal end of the edge."""
    provenance = edge.get("provenance")
    if provenance != "interpolated":
        return provenance == "ct"
    if "interpolated_spans_mm" not in edge:
        return False
    length = float(edge.get("length_mm") or 0.0)
    spans = edge.get("interpolated_spans_mm") or []
    covered = any(float(start) <= length <= float(end) for start, end in spans)
    return not covered


def _artery_graph(mask: np.ndarray, spacing, affine: np.ndarray) -> nx.Graph:
    """Every artery component, in patient RAS millimetres. Dust matches the probe."""
    if not np.any(mask):
        return nx.Graph()
    skeletons = kimimaro.skeletonize(
        mask.astype(np.uint8),
        teasar_params=TEASAR,
        anisotropy=tuple(float(value) for value in spacing),
        dust_threshold=50,
        progress=False,
        parallel=1,
    )
    graph = nx.Graph()
    offset = 0
    for skeleton in skeletons.values():
        count = len(skeleton.vertices)
        if count == 0:
            continue
        patient = to_patient_mm(skeleton.vertices, spacing, affine)
        for index, point in enumerate(patient):
            graph.add_node(offset + index, pos=np.asarray(point, dtype=float))
        for left, right in skeleton.edges:
            graph.add_edge(offset + int(left), offset + int(right))
        offset += count
    return graph


def _unit(vector: np.ndarray) -> np.ndarray:
    vector = np.asarray(vector, dtype=float)
    norm = float(np.linalg.norm(vector))
    return vector / norm if norm > 0.0 else vector


def _tip_direction(centreline: np.ndarray, back_mm: float = _TIP_MM) -> np.ndarray:
    steps = np.linalg.norm(np.diff(centreline, axis=0), axis=1)
    travelled = 0.0
    index = len(centreline) - 1
    while index > 0 and travelled < back_mm:
        travelled += float(steps[index - 1])
        index -= 1
    return _unit(centreline[-1] - centreline[index])


def _local_dir(graph: nx.Graph, node: int, hops: int = 3) -> np.ndarray:
    near = list(nx.single_source_shortest_path_length(graph, node, cutoff=hops))
    points = np.array([graph.nodes[other]["pos"] for other in near], dtype=float)
    if len(points) < 2:
        return np.zeros(3)
    _u, _s, vt = np.linalg.svd(points - points.mean(0), full_matrices=False)
    return vt[0]


def _pair(graph, ids, positions, tree, tip, direction, cache) -> int | None:
    nearest = None
    for index in tree.query_ball_point(np.asarray(tip, dtype=float), PAIR_MAX_MM):
        offset = positions[index] - tip
        if abs(float(np.dot(offset, direction))) > ALONG_MAX_MM:
            continue
        node = ids[index]
        local = cache.get(node)
        if local is None:
            local = _local_dir(graph, node)
            cache[node] = local
        cosine = min(1.0, abs(float(np.dot(local, direction))))
        angle = float(np.degrees(np.arccos(cosine)))
        if angle > PAIR_MAX_DEG:
            continue
        distance = float(np.linalg.norm(offset))
        if nearest is None or distance < nearest[0]:
            nearest = (distance, node)
    return None if nearest is None else nearest[1]


def _voxel(point: np.ndarray, inverse: np.ndarray, shape) -> tuple[int, int, int]:
    homogeneous = np.array([point[0], point[1], point[2], 1.0], dtype=float)
    index = np.rint((inverse @ homogeneous)[:3]).astype(int)
    index = np.clip(index, 0, np.asarray(shape) - 1)
    return int(index[0]), int(index[1]), int(index[2])


def _walk(graph, start: int, direction: np.ndarray, lobes, lobe: int, inverse):
    def pos(node: int) -> np.ndarray:
        return graph.nodes[node]["pos"]

    path = [start]
    heading = np.array(direction, dtype=float, copy=True)
    visited = {start}
    length = 0.0
    while True:
        current = path[-1]
        forward = [
            nxt for nxt in graph.neighbors(current)
            if nxt not in visited and float(np.dot(_unit(pos(nxt) - pos(current)), heading)) > 0.0
        ]
        if not forward:
            return _path_points(path, pos), length, "artery_end"
        nxt = max(
            forward,
            key=lambda node: float(np.dot(_unit(pos(node) - pos(current)), heading)),
        )
        step = pos(nxt) - pos(current)
        back = path[-1]
        travelled = 0.0
        for index in range(len(path) - 1, 0, -1):
            travelled += float(np.linalg.norm(pos(path[index]) - pos(path[index - 1])))
            back = path[index - 1]
            if travelled >= _HEADING_MM:
                break
        new_heading = _unit(pos(nxt) - pos(back))
        turn = float(np.degrees(np.arccos(
            float(np.clip(np.dot(new_heading, heading), -1.0, 1.0))
        )))
        if turn > TURN_MAX_DEG:
            return _path_points(path, pos), length, "turn"
        label = int(lobes[_voxel(pos(nxt), inverse, lobes.shape)])
        if label == 0:
            return _path_points(path, pos), length, "left_lung"
        if lobe and label != lobe:
            return _path_points(path, pos), length, "lobe_change"
        path.append(nxt)
        visited.add(nxt)
        length += float(np.linalg.norm(step))
        heading = _unit(0.7 * heading + 0.3 * new_heading)
        if length >= MAX_MM:
            return _path_points(path, pos), length, "max_len"


def _path_points(path: list[int], pos) -> list[np.ndarray]:
    return [np.array(pos(node), dtype=float, copy=True) for node in path]


def _sample_targets(limit: float) -> list[float]:
    count = int(np.floor((limit + 1e-9) / SAMPLE_MM))
    targets = [index * SAMPLE_MM for index in range(count + 1)]
    if not targets:
        targets = [0.0]
    if limit - targets[-1] > 1e-6:
        targets.append(float(limit))
    else:
        targets[-1] = float(limit)
    return targets


def _interpolate(points: np.ndarray, cumulative: np.ndarray, targets: list[float]) -> np.ndarray:
    sampled = np.empty((len(targets), 3), dtype=float)
    last = len(points) - 2
    for index, target in enumerate(targets):
        cursor = int(np.searchsorted(cumulative, target, side="right") - 1)
        cursor = min(max(cursor, 0), last)
        span = float(cumulative[cursor + 1] - cumulative[cursor])
        if span <= 1e-12:
            sampled[index] = points[cursor]
        else:
            mix = (float(target) - float(cumulative[cursor])) / span
            sampled[index] = (1.0 - mix) * points[cursor] + mix * points[cursor + 1]
    return sampled


def _shift_and_cut(path: list, tip: np.ndarray, stop: str, edge_index: int) -> dict | None:
    if len(path) < 2:
        return None
    raw = np.array([point for point in path], dtype=float)
    shifted = raw + (np.asarray(tip, dtype=float) - raw[0])
    steps = np.linalg.norm(np.diff(shifted, axis=0), axis=1)
    cumulative = np.r_[0.0, np.cumsum(steps)]
    arc = float(cumulative[-1])
    if arc < MIN_MM:
        return None
    capped = arc >= MAX_MM - 1e-6
    limit = MAX_MM if capped else arc
    reason = "max_len" if capped and stop in {"max_len", "artery_end"} else stop
    targets = _sample_targets(limit)
    sampled = _interpolate(shifted, cumulative, targets)
    coords = [[round(float(value), 3) for value in point] for point in sampled]
    coords[0] = [float(value) for value in tip]
    distances = [round(float(target), 3) for target in targets]
    distances[0] = 0.0
    length = float(MAX_MM) if capped else round(float(limit), 3)
    distances[-1] = length
    return {
        "id": f"est-{edge_index}",
        "from_edge": int(edge_index),
        "provenance": "estimated",
        "points_mm": coords,
        "distance_mm": distances,
        "length_mm": length,
        "stop_reason": reason,
    }


def _summary(terminals: int, segments: list[dict]) -> dict:
    lengths = [float(segment["length_mm"]) for segment in segments]
    return {
        "terminals": int(terminals),
        "estimated": len(segments),
        "total_mm": round(float(sum(lengths)), 1) if lengths else 0.0,
        "max_mm": round(float(max(lengths)), 1) if lengths else 0.0,
    }
