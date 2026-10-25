"""Slim graph.json down to something embeddable in a single-file prototype.

Throwaway: exists only to feed the renderer prototype for ticket 06.
Decimates centrelines, rounds coordinates, and precomputes one route from the
trachea to a distal target so the first-person variant has a path to follow.
Phase 3: payload includes origin_mm (absolute RAS of the root) and lesion.
"""

import json
import argparse
from collections import deque
from pathlib import Path
import numpy as np
import repo_root
from route_frame import (
    PAUSE_BEFORE_MM,
    STEP_MM,
    forks as route_forks,
    pause_points,
    resample,
    roll_profile,
    tangents,
    transport_frame,
)

KEEP_EVERY = 3  # centreline samples; endpoints always kept
AIRWAY_EMBED_SENTINEL = "/*__AIRWAY_EMBED__*/"

repo_root.ensure()


def source_label(case_id: str | int) -> str:
    text = str(case_id)
    return f"AeroPath {text}" if text.isdigit() else text


def load_case_record(case_id: str) -> dict:
    """Load case.json via CaseRef (local or OUT/cases/<n>/ for AeroPath)."""
    from app.caseref import CaseRef, UnknownCase

    try:
        ref = CaseRef.parse(str(case_id))
    except UnknownCase:
        return {}
    path = ref.case_json
    if not path.is_file():
        return {}
    return json.loads(path.read_text())


class ReviewBlocked(Exception):
    """Raised when unresolved review flags block payload export."""

    def __init__(self, unresolved: list):
        self.unresolved = unresolved
        super().__init__("graph has unresolved review entries")


def provenance_mapping(graph_edges: list[dict]) -> tuple[list[dict], bool]:
    """Return prototype edge provenance without inventing facts for old graphs."""
    recorded = bool(graph_edges) and all("provenance" in edge for edge in graph_edges)
    return ([{"prov": edge.get("provenance", "unrecorded"),
              "down_i": bool(edge.get("downstream_of_interpolated", False))}
             for edge in graph_edges], recorded)


def decimated_indices(point_count: int, step: int) -> list[int]:
    indices = list(range(0, point_count, step))
    if point_count and indices[-1] != point_count - 1:
        indices.append(point_count - 1)
    return indices


def interpolated_segments(points: list, indices: list[int], spans: list[list[float]]) -> list[int]:
    if not spans or len(indices) < 2:
        return []
    cumulative = np.r_[0.0, np.cumsum(np.linalg.norm(np.diff(points, axis=0), axis=1))]
    return [segment for segment, (first, last) in enumerate(zip(indices[:-1], indices[1:]))
            if any(cumulative[first] < end and cumulative[last] > start
                   for start, end in spans)]


def edge_provenance_at(edge: dict, arc_mm: float) -> str:
    provenance = edge.get("provenance", "unrecorded")
    if provenance != "interpolated" or "interpolated_spans_mm" not in edge:
        return provenance
    return ("interpolated" if any(start <= arc_mm <= end
                                   for start, end in edge["interpolated_spans_mm"])
            else "ct")


def nearest_centreline_sample(edges: list[dict], poi: list[float]):
    """Return (edge index, sample index, point, gap) for the nearest full-res sample."""
    target = np.asarray(poi, dtype=float)
    best = None
    for ei, edge in enumerate(edges):
        points = np.asarray(edge["centreline_mm"], dtype=float)
        distances = np.linalg.norm(points - target, axis=1)
        si = int(np.argmin(distances))
        candidate = (float(distances[si]), ei, si, points[si].copy())
        if best is None or candidate[0] < best[0]:
            best = candidate
    if best is None:
        raise ValueError("graph has no centreline samples")
    gap, ei, si, point = best
    return ei, si, point, gap


def edge_route(edges: list[dict], root: int, final_edge: int) -> list[int]:
    """Build root-to-edge route using the graph's tree topology."""
    children: dict[int, list[int]] = {}
    for i, edge in enumerate(edges):
        children.setdefault(edge["from"], []).append(i)
    parents: dict[int, int] = {}
    queue = deque([root])
    seen = {root}
    while queue:
        node = queue.popleft()
        for ei in children.get(node, []):
            child = edges[ei]["to"]
            if child not in seen:
                seen.add(child)
                parents[child] = ei
                queue.append(child)
    final_node = edges[final_edge]["to"]
    route, cur = [], final_node
    while cur in parents:
        ei = parents[cur]
        route.append(ei)
        cur = edges[ei]["from"]
    route.reverse()
    if not route or route[-1] != final_edge:
        route.append(final_edge)
    return route


def truncate_route_points(edges: list[dict], route: list[int], end_edge: int,
                          end_sample: int, rel):
    """Join full-resolution route centrelines and stop exactly at selected sample."""
    points = []
    for ei in route:
        line = edges[ei]["centreline_mm"]
        if ei == end_edge:
            line = line[:end_sample + 1]
        if points:
            line = line[1:]
        points.extend(rel(p) for p in line)
        if ei == end_edge:
            break
    return points


def parse_target(value: str) -> list[float]:
    point = [float(part) for part in value.split(",")]
    if len(point) != 3:
        raise argparse.ArgumentTypeError("target must be X,Y,Z")
    return point


def review_gate(review: list[dict], signoffs: list[dict], allow_review: bool = False):
    """Return approved matches and remaining review flags."""
    approved = {(s["kind"], s["lobe"]): s for s in signoffs
                if s["decision"] == "approved"}
    resolved, unresolved = [], []
    for entry in review:
        signoff = approved.get((entry["kind"], entry["lobe"]))
        if signoff:
            resolved.append({key: signoff[key] for key in
                             ("kind", "lobe", "finding", "reviewer", "date")})
        else:
            unresolved.append(entry)
    return resolved, unresolved, bool(unresolved) and not allow_review


def build_payload(graph: dict, case_record: dict, allow_review: bool,
                  target: list[float] | None = None) -> dict:
    """Build the renderer `window.AIRWAY` payload; raise ReviewBlocked if gated."""
    review = graph.get("review", [])
    signoffs, unresolved, refused = review_gate(
        review, case_record.get("review_signoff", []), allow_review
    )
    if refused:
        raise ReviewBlocked(unresolved)

    nodes = {n["id"]: n for n in graph["nodes"]}
    edges = graph["edges"]
    case_id = str(graph["case"])

    # Centre on the trachea so the prototype can ignore patient-space offsets.
    root = nodes[graph["root_node_id"]]["position_mm"]

    def rel(p):
        return [round(p[i] - root[i], 1) for i in range(3)]

    prov_map, provenance_recorded = provenance_mapping(edges)
    out_edges = []
    for e, prov in zip(edges, prov_map):
        indices = decimated_indices(len(e["centreline_mm"]), KEEP_EVERY)
        out_edges.append({
            "f": e["from"],
            "t": e["to"],
            "g": e["generation"],
            "r": e["radius_mm"],
            "l": e["length_mm"],
            "lobe": e["lobe"],
            **prov,
            "ci": interpolated_segments(
                e["centreline_mm"], indices, e.get("interpolated_spans_mm", [])
            ),
            "c": [rel(e["centreline_mm"][i]) for i in indices],
        })

    # Deepest branch reachable from the root: a plausible navigation target.
    children: dict[int, list[int]] = {}
    for i, e in enumerate(out_edges):
        children.setdefault(e["f"], []).append(i)

    parent_edge: dict[int, int] = {}
    seen = {graph["root_node_id"]}
    order = []
    q = deque([graph["root_node_id"]])
    while q:
        n = q.popleft()
        order.append(n)
        for ei in children.get(n, []):
            nxt = out_edges[ei]["t"]
            if nxt not in seen:
                seen.add(nxt)
                parent_edge[nxt] = ei
                q.append(nxt)

    end_edge = end_sample = endpoint = target_gap = target_inside = None
    if target is not None:
        end_edge, end_sample, endpoint, target_gap = nearest_centreline_sample(edges, target)
        route = edge_route(edges, graph["root_node_id"], end_edge)
        target_node = edges[end_edge]["to"]
        target_inside = target_gap <= edges[end_edge]["radius_mm"]
    else:
        target_node = order[-1]
        route, cur = [], target_node
        while cur in parent_edge:
            ei = parent_edge[cur]
            route.append(ei)
            cur = out_edges[ei]["f"]
        route.reverse()

    route_points, route_labels = [], []
    if target is not None:
        route_points = truncate_route_points(edges, route, end_edge, end_sample, rel)
        for route_pos, ei in enumerate(route):
            count = len(edges[ei]["centreline_mm"])
            if ei == end_edge:
                count = end_sample + 1
            route_labels.extend([route_pos] * max(0, count - int(route_pos > 0)))
    else:
        for route_pos, ei in enumerate(route):
            line = [rel(p) for p in edges[ei]["centreline_mm"]]
            if route_points:
                line = line[1:]
            route_points.extend(line)
            route_labels.extend([route_pos] * len(line))
    full_edges = [{**e, "centreline_mm": [rel(p) for p in e["centreline_mm"]]} for e in edges]
    fp, fs, fe = resample(route_points, STEP_MM, route_labels)
    ft = tangents(fp, STEP_MM)
    fu = transport_frame(ft)
    ff = route_forks(full_edges, route, fp, fs, ft, fu)
    for fork in ff:
        fork["prov"] = out_edges[fork["chosen"]]["prov"]
        for child in fork["children"]:
            child_edge = out_edges[child["edge"]]
            child["prov"] = child_edge["prov"]
            child["down_i"] = child_edge["down_i"]
    if target is not None:
        ff = [fork for fork in ff if fork["s"] <= fs[-1]]
    pauses = pause_points(ff, PAUSE_BEFORE_MM)
    for i, f in enumerate(ff, 1):
        f["fork"] = i
        f["s"] = round(f["s"], 1)
        f["pause_s"] = round(pauses[i - 1], 1)
        chosen = next(c for c in f["children"] if c["on_route"])
        f["name"] = chosen["name"]
        f["angle"] = round(f["angle"], 1) if f["angle"] is not None else None
        for child in f["children"]:
            child["angle"] = round(child["angle"], 1) if child["angle"] is not None else None
            child["r"] = round(child["r"], 2) if child["r"] is not None else None
    roll = roll_profile(fs, ff)

    route_lengths = []
    for ei in route:
        points = np.asarray(edges[ei]["centreline_mm"], dtype=float)
        if target is not None and ei == end_edge:
            points = points[:end_sample + 1]
        route_lengths.append(float(np.linalg.norm(np.diff(points, axis=0), axis=1).sum()))
    route_offsets = np.r_[0.0, np.cumsum(route_lengths[:-1])]
    fly_provenance = [
        edge_provenance_at(
            edges[route[int(route_pos)]],
            float(distance - route_offsets[int(route_pos)]),
        )
        for distance, route_pos in zip(fs, fe)
    ]

    lesion_rec = case_record.get("lesion")
    lesion_out = None
    if isinstance(lesion_rec, dict) and lesion_rec.get("ras_mm") is not None:
        lesion_out = {
            "ras_mm": [float(v) for v in lesion_rec["ras_mm"]],
            "confirmed_by": lesion_rec.get("confirmed_by"),
            "at": lesion_rec.get("at"),
        }

    return {
        "case": graph["case"],
        "source": source_label(case_id),
        "root": graph["root_node_id"],
        "origin_mm": [round(float(root[i]), 3) for i in range(3)],
        "lesion": lesion_out,
        "nodes": {str(k): {"p": rel(v["position_mm"]), "r": v["radius_mm"]}
                  for k, v in nodes.items()},
        "edges": out_edges,
        "route": route,
        "target": target_node,
        "poi": rel(target) if target is not None else None,
        "endpoint": rel(endpoint) if target is not None else None,
        "target_gap_mm": round(target_gap, 1) if target is not None else None,
        "target_inside_airway": target_inside if target is not None else None,
        "route_interpolated": any(out_edges[ei]["prov"] == "interpolated" for ei in route),
        "provenance_recorded": provenance_recorded,
        "axes": "RAS",
        "fly": {"step_mm": STEP_MM, "s": np.round(fs, 1).tolist(), "p": np.round(fp, 1).tolist(),
                "t": np.round(ft, 3).tolist(), "up": np.round(fu, 3).tolist(),
                "roll": np.round(roll, 4).tolist(), "edge": fe.tolist(),
                "r": [round(edges[route[int(i)]]["radius_mm"], 2) for i in fe],
                "g": [int(edges[route[int(i)]]["generation"]) for i in fe],
                "prov": fly_provenance},
        "forks": ff,
        "review": unresolved,
        "signoff": signoffs,
    }


def embed_payload_in_template(template_text: str, js: str) -> str:
    count = template_text.count(AIRWAY_EMBED_SENTINEL)
    if count != 1:
        raise ValueError(
            f"template must contain airway embed sentinel exactly once, found {count}"
        )
    return template_text.replace(AIRWAY_EMBED_SENTINEL, js, 1)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("src", nargs="?", default="cases/1_graph.json")
    parser.add_argument("dst", nargs="?", default="prototype_data.js")
    parser.add_argument("--html", type=Path)
    parser.add_argument("--allow-review", action="store_true")
    parser.add_argument("--target", type=parse_target, help="patient-space RAS point X,Y,Z")
    args = parser.parse_args()
    html_path = args.html
    src = Path(args.src)
    dst = Path(args.dst)
    g = json.loads(src.read_text())
    if "review" not in g:
        print("Note: graph has no review field; treating it as clean")
    case_id = str(g["case"])
    case_record = load_case_record(case_id)

    try:
        payload = build_payload(g, case_record, args.allow_review, args.target)
    except ReviewBlocked as exc:
        print("Refusing graph with review entries:")
        print(json.dumps(exc.unresolved, indent=2))
        raise SystemExit(1) from exc

    for entry in payload["signoff"]:
        print(f"reviewed: {entry['kind']} {entry['lobe']} — approved by "
              f"{entry['reviewer']} on {entry['date']}")
    if payload["review"]:
        print("WARNING: exporting a graph with review entries:")
        print(json.dumps(payload["review"], indent=2))

    out_edges = payload["edges"]
    route = payload["route"]
    target = payload["target"]
    ff = payload["forks"]
    provenance_recorded = payload["provenance_recorded"]
    target_gap = payload["target_gap_mm"]
    target_inside = payload["target_inside_airway"]
    fs = payload["fly"]["s"]
    edges = g["edges"]

    js = "window.AIRWAY = " + json.dumps(payload, separators=(",", ":")) + ";"
    dst.write_text(js)
    print(f"{dst}  {dst.stat().st_size/1024:.0f} KB  "
          f"{len(out_edges)} edges, route of {len(route)} branches "
          f"to node {target}")
    for f in ff:
        label = f'{f["clock"]} o\'clock' if f["clock"] is not None else "straight ahead"
        siblings = [f'{c["clock"]} o\'clock' if c["clock"] is not None else "straight ahead"
                    for c in f["children"] if not c["on_route"]]
        mark = " [interpolated]" if f["prov"] == "interpolated" else ""
        print(f"fork {f['fork']} @ {f['s']:.0f} mm: take {label} ({f['name'] or 'unnamed'}){mark}; siblings {', '.join(siblings)}")
    for ei in route:
        if out_edges[ei]["prov"] == "interpolated":
            print(f"interpolated edge {ei}: {edges[ei].get('interpolated_mm', 0):.1f} mm")
    if not provenance_recorded:
        print("provenance not recorded in this graph")
    if args.target is not None:
        if target_inside:
            print(f"target inside airway ({target_gap:.1f} mm from centreline)")
        else:
            print(f"route ends at last airway sample; target {target_gap:.1f} mm beyond (no airway shown)")
    interpolated_route = [ei for ei in route if out_edges[ei]["prov"] == "interpolated"]
    if interpolated_route:
        amount = sum(edges[ei].get("interpolated_mm", 0) for ei in interpolated_route)
        print(f"WARNING: route crosses {len(interpolated_route)} interpolated segment(s), {amount:.1f} mm")
    print(f"target node {target}, route length {fs[-1]:.0f} mm")
    if html_path:
        template = Path(__file__).resolve().parents[1] / "prototype/renderer.template.html"
        html_path.write_text(embed_payload_in_template(template.read_text(), js))


if __name__ == "__main__":
    main()
