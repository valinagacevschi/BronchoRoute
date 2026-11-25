# Changes: payload carries estimated airway beside edges, never merged into them.
# Changes: payload lesion includes diameter_mm (10 when the record omits it).
# Changes: Phase 4c review — build_payload routes from start_node; truncates at
# last original ≤ sample arc then appends exact end_ras_mm; optional stored gap.
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


def estimated_mapping(graph: dict, rel) -> list[dict]:
    """Decimate graph['estimated'] into payload rows. Never folded into edges."""
    rows = []
    for segment in graph.get("estimated") or []:
        points = segment.get("points_mm") or []
        distances = segment.get("distance_mm") or []
        if len(points) < 2 or len(distances) != len(points):
            continue
        indices = decimated_indices(len(points), KEEP_EVERY)
        rows.append({
            "id": segment.get("id"),
            "from_edge": segment.get("from_edge"),
            "prov": "estimated",
            "c": [rel(points[index]) for index in indices],
            "d": [round(float(distances[index]), 3) for index in indices],
        })
    return rows


def interpolated_segments(points: list, indices: list[int], spans: list[list[float]]) -> list[int]:
    if not spans or len(indices) < 2:
        return []
    cumulative = np.r_[0.0, np.cumsum(np.linalg.norm(np.diff(points, axis=0), axis=1))]
    return [segment for segment, (first, last) in enumerate(zip(indices[:-1], indices[1:]))
            if any(cumulative[first] < end and cumulative[last] > start
                   for start, end in spans)]


def lesion_diameter_mm(lesion_rec: dict) -> float:
    """Payload diameter. Missing or unusable values fall back to 10 mm."""
    raw = lesion_rec.get("diameter_mm", 10.0) if isinstance(lesion_rec, dict) else 10.0
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return 10.0
    if value != value or value in (float("inf"), float("-inf")):
        return 10.0
    return value


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
                          end_sample: int, rel, end_ras_mm=None):
    """Join route centrelines; stop at last original ≤ end_sample, then exact sample.

    When `end_ras_mm` is set, `end_sample` is the last original vertex at or before
    the sample arc; the exact sample point is appended (RouteTarget fly end).
    """
    points = []
    for ei in route:
        line = list(edges[ei]["centreline_mm"])
        if ei == end_edge:
            line = line[: end_sample + 1]
            if end_ras_mm is not None:
                sample = [float(v) for v in end_ras_mm]
                if not line or any(
                    abs(float(line[-1][i]) - sample[i]) > 1e-9 for i in range(3)
                ):
                    line = line + [sample]
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
    """Return approved matches and remaining review flags.

    Malformed sign-off rows are skipped (never raise).
    """
    approved = {}
    for s in signoffs:
        if not isinstance(s, dict):
            continue
        if s.get("decision") != "approved":
            continue
        kind, lobe = s.get("kind"), s.get("lobe")
        if not kind or not lobe:
            continue
        approved[(kind, lobe)] = s
    resolved, unresolved = [], []
    for entry in review:
        signoff = approved.get((entry["kind"], entry["lobe"]))
        if signoff:
            day = signoff.get("date")
            if not day and isinstance(signoff.get("at"), str):
                day = signoff["at"][:10]
            resolved.append({
                "kind": signoff["kind"],
                "lobe": signoff["lobe"],
                "finding": signoff.get("finding", ""),
                "reviewer": signoff.get("reviewer", ""),
                "date": day or "",
            })
        else:
            unresolved.append(entry)
    return resolved, unresolved, bool(unresolved) and not allow_review


def build_payload(graph: dict, case_record: dict, allow_review: bool,
                  target: list[float] | None = None,
                  *, end_edge: int | None = None,
                  end_sample: int | None = None,
                  start_node: int | None = None,
                  end_ras_mm: list[float] | None = None,
                  stored_gap_mm: float | None = None,
                  stored_inside_airway: bool | None = None,
                  poi_ras_mm: list[float] | None = None,
                  poi_kind: str | None = None) -> dict:
    """Build the renderer `window.AIRWAY` payload; raise ReviewBlocked if gated.

    CLI `--target` (interim): nearest centreline sample to `target` is the route
    end; `target` is also the POI for the gap line.
    Explicit `end_edge`/`end_sample` (Phase 4c): truncate at that sample; POI /
    gap use case lesion when present, else `target`. A route to a Lymph node
    passes that node in `poi_ras_mm`; `poi_kind` labels it. Route starts at
    confirmed RouteStart (`start_node`) when given, else graph root.
    When `end_ras_mm` is set, fly ends at that exact point (appended after the
    last original vertex at or before `end_sample`). Stored gap fields win over
    recomputation when provided.
    """
    review = graph.get("review", [])
    signoffs, unresolved, refused = review_gate(
        review, case_record.get("review_signoff", []), allow_review
    )
    if refused:
        raise ReviewBlocked(unresolved)

    nodes = {n["id"]: n for n in graph["nodes"]}
    edges = graph["edges"]
    case_id = str(graph["case"])
    route_root = int(start_node) if start_node is not None else int(graph["root_node_id"])

    # Centre on the trachea so the prototype can ignore patient-space offsets.
    root = nodes[graph["root_node_id"]]["position_mm"]

    def rel(p):
        return [round(float(p[i]) - float(root[i]), 1) for i in range(3)]

    def rel_exact(p):
        return [float(p[i]) - float(root[i]) for i in range(3)]

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

    # Deepest branch reachable from the route start: a plausible navigation target.
    children: dict[int, list[int]] = {}
    for i, e in enumerate(out_edges):
        children.setdefault(e["f"], []).append(i)

    parent_edge: dict[int, int] = {}
    seen = {route_root}
    order = []
    q = deque([route_root])
    while q:
        n = q.popleft()
        order.append(n)
        for ei in children.get(n, []):
            nxt = out_edges[ei]["t"]
            if nxt not in seen:
                seen.add(nxt)
                parent_edge[nxt] = ei
                q.append(nxt)

    lesion_rec = case_record.get("lesion")
    lesion_ras = None
    if isinstance(lesion_rec, dict) and lesion_rec.get("ras_mm") is not None:
        lesion_ras = [float(v) for v in lesion_rec["ras_mm"]]
    poi_override = (
        [float(v) for v in poi_ras_mm]
        if isinstance(poi_ras_mm, (list, tuple)) and len(poi_ras_mm) == 3
        else None
    )

    truncated = False
    endpoint = target_gap = target_inside = None
    poi_ras = None
    exact_end = end_ras_mm is not None
    if end_edge is not None and end_sample is not None:
        end_edge = int(end_edge)
        end_sample = int(end_sample)
        if end_edge < 0 or end_edge >= len(edges):
            raise ValueError(f"end_edge {end_edge} out of range")
        line = edges[end_edge]["centreline_mm"]
        if end_sample < 0 or end_sample >= len(line):
            raise ValueError(f"end_sample {end_sample} out of range")
        if exact_end:
            endpoint = np.asarray(end_ras_mm, dtype=float).copy()
        else:
            endpoint = np.asarray(line[end_sample], dtype=float).copy()
        route = edge_route(edges, route_root, end_edge)
        target_node = edges[end_edge]["to"]
        poi_ras = (
            poi_override if poi_override is not None
            else (lesion_ras if lesion_ras is not None else target)
        )
        if stored_gap_mm is not None:
            target_gap = float(stored_gap_mm)
            target_inside = (
                bool(stored_inside_airway)
                if stored_inside_airway is not None
                else (
                    target_gap <= edges[end_edge]["radius_mm"]
                    if poi_ras is not None else None
                )
            )
        elif poi_ras is not None:
            target_gap = float(np.linalg.norm(np.asarray(poi_ras, float) - endpoint))
            target_inside = target_gap <= edges[end_edge]["radius_mm"]
        truncated = True
    elif target is not None:
        end_edge, end_sample, endpoint, target_gap = nearest_centreline_sample(
            edges, target
        )
        route = edge_route(edges, route_root, end_edge)
        target_node = edges[end_edge]["to"]
        target_inside = target_gap <= edges[end_edge]["radius_mm"]
        poi_ras = poi_override if poi_override is not None else target
        truncated = True
    else:
        target_node = order[-1] if order else graph["root_node_id"]
        route, cur = [], target_node
        while cur in parent_edge:
            ei = parent_edge[cur]
            route.append(ei)
            cur = out_edges[ei]["f"]
        route.reverse()

    route_points, route_labels = [], []
    if truncated:
        route_points = truncate_route_points(
            edges, route, end_edge, end_sample, rel_exact if exact_end else rel,
            end_ras_mm=end_ras_mm if exact_end else None,
        )
        for route_pos, ei in enumerate(route):
            count = len(edges[ei]["centreline_mm"])
            if ei == end_edge:
                count = end_sample + 1 + (1 if exact_end else 0)
            route_labels.extend([route_pos] * max(0, count - int(route_pos > 0)))
        # Labels must match route_points length (exact append may skip dup point).
        if len(route_labels) != len(route_points):
            route_labels = (
                [route_labels[i] if i < len(route_labels) else route_labels[-1]
                 for i in range(len(route_points))]
                if route_labels else [0] * len(route_points)
            )
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
    if truncated:
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
        if truncated and ei == end_edge:
            if exact_end and end_ras_mm is not None:
                keep = points[: end_sample + 1]
                sample = np.asarray(end_ras_mm, dtype=float)
                if len(keep) == 0 or float(np.linalg.norm(keep[-1] - sample)) > 1e-9:
                    points = np.vstack([keep, sample]) if len(keep) else sample.reshape(1, 3)
                else:
                    points = keep
            else:
                points = points[: end_sample + 1]
        route_lengths.append(
            float(np.linalg.norm(np.diff(points, axis=0), axis=1).sum())
            if len(points) >= 2 else 0.0
        )
    route_offsets = np.r_[0.0, np.cumsum(route_lengths[:-1])] if route_lengths else np.array([0.0])
    fly_provenance = [
        edge_provenance_at(
            edges[route[int(route_pos)]],
            float(distance - route_offsets[int(route_pos)]),
        )
        for distance, route_pos in zip(fs, fe)
    ]

    candidates_out = []
    block = case_record.get("candidates")
    if isinstance(block, dict) and isinstance(block.get("items"), list):
        for item in block["items"]:
            if not isinstance(item, dict) or item.get("ras_mm") is None:
                continue
            candidates_out.append({
                "ras_mm": [round(float(v), 3) for v in item["ras_mm"]],
                "diameter_mm": lesion_diameter_mm(item),
                "score": item.get("score"),
                "id": item.get("id"),
                "volume_mm3": item.get("volume_mm3"),
                "lobe": item.get("lobe"),
                "to_airway_mm": item.get("to_airway_mm"),
                "size_mm": item.get("size_mm"),
                "review": bool(item.get("review", False)),
            })

    lesion_out = None
    if lesion_ras is not None:
        lesion_out = {
            "id": lesion_rec.get("id") if isinstance(lesion_rec, dict) else None,
            "candidate_id": (
                lesion_rec.get("candidate_id")
                if isinstance(lesion_rec, dict) else None
            ),
            "ras_mm": lesion_ras,
            "diameter_mm": lesion_diameter_mm(lesion_rec if isinstance(lesion_rec, dict) else {}),
            "confirmed_by": lesion_rec.get("confirmed_by") if isinstance(lesion_rec, dict) else None,
            "at": lesion_rec.get("at") if isinstance(lesion_rec, dict) else None,
            "source": lesion_rec.get("source") if isinstance(lesion_rec, dict) else None,
        }

    confirmed = case_record.get("lesions")
    if not isinstance(confirmed, list):
        confirmed = [lesion_rec] if isinstance(lesion_rec, dict) else []
    lesions_out = []
    for item in confirmed:
        if not isinstance(item, dict) or item.get("ras_mm") is None:
            continue
        lesions_out.append({
            "id": item.get("id"),
            "candidate_id": item.get("candidate_id"),
            "ras_mm": [float(v) for v in item["ras_mm"]],
            "diameter_mm": lesion_diameter_mm(item),
            "confirmed_by": item.get("confirmed_by"),
            "at": item.get("at"),
            "source": item.get("source"),
        })

    # Exact RouteTarget end: keep full precision on endpoint + last fly sample.
    if truncated and exact_end and endpoint is not None and len(fp):
        end_rel = rel_exact(endpoint)
        fp = np.asarray(fp, dtype=float).copy()
        fp[-1] = end_rel
        endpoint_out = end_rel
        fly_p = np.round(fp, 1).tolist()
        fly_p[-1] = end_rel
    else:
        endpoint_out = rel(endpoint) if truncated and endpoint is not None else None
        fly_p = np.round(fp, 1).tolist()

    gap_out = None
    if truncated and target_gap is not None:
        gap_out = (
            round(float(target_gap), 2)
            if stored_gap_mm is not None
            else round(float(target_gap), 1)
        )

    node_marks_out = []
    for mark in case_record.get("node_marks") or []:
        if not isinstance(mark, dict) or mark.get("ras_mm") is None:
            continue
        try:
            ras = [float(v) for v in mark["ras_mm"]]
        except (TypeError, ValueError):
            continue
        if len(ras) != 3:
            continue
        node_marks_out.append({
            "id": mark.get("id"),
            "p": rel(ras),
            "ras_mm": ras,
            "diameter_mm": lesion_diameter_mm(mark),
            "label": mark.get("label"),
        })

    return {
        "case": graph["case"],
        "source": source_label(case_id),
        "root": graph["root_node_id"],
        "origin_mm": [round(float(root[i]), 3) for i in range(3)],
        "lesion": lesion_out,
        "lesions": lesions_out,
        "candidates": candidates_out,
        "nodes": {str(k): {"p": rel(v["position_mm"]), "r": v["radius_mm"]}
                  for k, v in nodes.items()},
        "edges": out_edges,
        "estimated": estimated_mapping(graph, rel),
        "route": route,
        "target": target_node,
        "poi": rel(poi_ras) if poi_ras is not None else None,
        "poi_kind": poi_kind,
        "node_marks": node_marks_out,
        "endpoint": endpoint_out,
        "target_gap_mm": gap_out,
        "target_inside_airway": target_inside if truncated else None,
        "route_interpolated": any(out_edges[ei]["prov"] == "interpolated" for ei in route),
        "provenance_recorded": provenance_recorded,
        "axes": "RAS",
        "fly": {"step_mm": STEP_MM, "s": np.round(fs, 1).tolist(), "p": fly_p,
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
