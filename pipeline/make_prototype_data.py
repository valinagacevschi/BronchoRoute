"""Slim graph.json down to something embeddable in a single-file prototype.

Throwaway: exists only to feed the renderer prototype for ticket 06.
Decimates centrelines, rounds coordinates, and precomputes one route from the
trachea to a distal target so the first-person variant has a path to follow.
"""

import json
import argparse
from collections import deque
from pathlib import Path
import numpy as np
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


def decimate(points: list, step: int) -> list:
    if len(points) <= 2:
        return points
    kept = points[::step]
    if kept[-1] != points[-1]:
        kept.append(points[-1])
    return kept


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("src", nargs="?", default="cases/1_graph.json")
    parser.add_argument("dst", nargs="?", default="prototype_data.js")
    parser.add_argument("--html", type=Path)
    parser.add_argument("--allow-review", action="store_true")
    args = parser.parse_args()
    html_path = args.html
    src = Path(args.src)
    dst = Path(args.dst)
    g = json.loads(src.read_text())
    if "review" not in g:
        print("Note: graph has no review field; treating it as clean")
    review = g.get("review", [])
    if review:
        if not args.allow_review:
            print("Refusing graph with review entries:")
            print(json.dumps(review, indent=2))
            raise SystemExit(1)
        print("WARNING: exporting a graph with review entries:")
        print(json.dumps(review, indent=2))

    nodes = {n["id"]: n for n in g["nodes"]}
    edges = g["edges"]

    # Centre on the trachea so the prototype can ignore patient-space offsets.
    root = nodes[g["root_node_id"]]["position_mm"]

    def rel(p):
        return [round(p[i] - root[i], 1) for i in range(3)]

    out_edges = []
    for e in edges:
        out_edges.append({
            "f": e["from"],
            "t": e["to"],
            "g": e["generation"],
            "r": e["radius_mm"],
            "l": e["length_mm"],
            "lobe": e["lobe"],
            "c": [rel(p) for p in decimate(e["centreline_mm"], KEEP_EVERY)],
        })

    # Deepest branch reachable from the root: a plausible navigation target.
    children: dict[int, list[int]] = {}
    for i, e in enumerate(out_edges):
        children.setdefault(e["f"], []).append(i)

    parent_edge: dict[int, int] = {}
    seen = {g["root_node_id"]}
    order = []
    q = deque([g["root_node_id"]])
    while q:
        n = q.popleft()
        order.append(n)
        for ei in children.get(n, []):
            nxt = out_edges[ei]["t"]
            if nxt not in seen:
                seen.add(nxt)
                parent_edge[nxt] = ei
                q.append(nxt)

    target = order[-1]
    route, cur = [], target
    while cur in parent_edge:
        ei = parent_edge[cur]
        route.append(ei)
        cur = out_edges[ei]["f"]
    route.reverse()

    route_points, route_labels = [], []
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

    payload = {
        "case": g["case"],
        "root": g["root_node_id"],
        "nodes": {str(k): {"p": rel(v["position_mm"]), "r": v["radius_mm"]}
                  for k, v in nodes.items()},
        "edges": out_edges,
        "route": route,
        "target": target,
        "axes": "RAS",
        "fly": {"step_mm": STEP_MM, "s": np.round(fs, 1).tolist(), "p": np.round(fp, 1).tolist(),
                "t": np.round(ft, 3).tolist(), "up": np.round(fu, 3).tolist(),
                "roll": np.round(roll, 4).tolist(), "edge": fe.tolist(),
                "r": [round(edges[route[int(i)]]["radius_mm"], 2) for i in fe],
                "g": [int(edges[route[int(i)]]["generation"]) for i in fe]},
        "forks": ff,
        "review": review,
    }

    js = "window.AIRWAY = " + json.dumps(payload, separators=(",", ":")) + ";"
    dst.write_text(js)
    print(f"{dst}  {dst.stat().st_size/1024:.0f} KB  "
          f"{len(out_edges)} edges, route of {len(route)} branches "
          f"to node {target}")
    for f in ff:
        label = f'{f["clock"]} o\'clock' if f["clock"] is not None else "straight ahead"
        siblings = [f'{c["clock"]} o\'clock' if c["clock"] is not None else "straight ahead"
                    for c in f["children"] if not c["on_route"]]
        print(f"fork {f['fork']} @ {f['s']:.0f} mm: take {label} ({f['name'] or 'unnamed'}); siblings {', '.join(siblings)}")
    print(f"target node {target}, route length {fs[-1]:.0f} mm")
    if html_path:
        template = Path(__file__).resolve().parents[1] / "prototype/renderer.template.html"
        html_path.write_text(template.read_text().replace("/*DATA*/", js))


if __name__ == "__main__":
    main()
