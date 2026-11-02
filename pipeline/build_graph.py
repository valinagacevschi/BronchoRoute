# Changes: optional artery mask adds estimated airway beside the graph, never into it.
# Changes: disable soma cuts, repair only mask-contained skeleton holes, root in RAS,
# and reject empty lung trees before exporting.
"""AeroPath airway mask -> navigable airway graph.

    mask.nii.gz -> kimimaro skeleton -> networkx graph -> graph.json

Kimimaro gives a loop-free skeleton with a radius at every vertex, which is why
it replaces skeletonize + skan here. Everything downstream is plain graph work:
root at the trachea, number the generations, collapse the degree-2 chains into
branches, then write the browser asset.

Coordinates stay in patient physical millimetres throughout. Converting to any
game space is the renderer's job, not this pipeline's.
"""

import argparse
import json
import time
from collections import deque
from pathlib import Path

import kimimaro
import networkx as nx
import nibabel as nib
import numpy as np
from scipy.spatial import cKDTree
from paths import OUT
from snomed import LOBES, SNOMED_SOURCE, TRACHEA

# Kimimaro's TEASAR parameters. `scale`/`const` set the invalidation-ball size
# around each accepted path: too small and the skeleton grows hairs, too large
# and real distal branches vanish. These suit airways at ~0.5-1mm spacing.
TEASAR = {
    "scale": 1.5,
    "const": 2.0,          # mm
    "pdrf_scale": 100_000,
    "pdrf_exponent": 4,
    # Soma handling cuts paths around a neuronal cell body; the trachea is a tube.
    "soma_detection_threshold": float("inf"),
    "soma_acceptance_threshold": float("inf"),
}

# Branches shorter than this are thinning artefacts rather than anatomy.
MIN_BRANCH_MM = 2.0

LOBE_NAMES = {value: info["name"] for value, info in LOBES.items()}


def trachea_snomed(name: str | None) -> dict | None:
    return TRACHEA.copy() if name == "trachea" else None


def exported_lobes() -> list[dict]:
    return [
        {"value": value, "name": info["name"], "snomed": info["snomed"].copy()}
        for value, info in sorted(LOBES.items())
    ]


def load_mask(path: Path) -> tuple[np.ndarray, np.ndarray, tuple[float, float, float]]:
    img = nib.load(path)
    mask = np.asanyarray(img.dataobj).astype(np.uint8)
    spacing = tuple(float(z) for z in img.header.get_zooms()[:3])
    return mask, img.affine, spacing


def skeletonise(mask: np.ndarray, spacing: tuple[float, float, float]):
    """Voxel mask -> single kimimaro Skeleton in physical millimetres."""
    skels = kimimaro.skeletonize(
        (mask > 0).astype(np.uint8),
        teasar_params=TEASAR,
        anisotropy=spacing,
        dust_threshold=100,
        progress=False,
        parallel=1,
    )
    if not skels:
        raise SystemExit("graph: no airway tree in lung")

    # A clean airway mask is one connected component; more means the mask
    # fragmented, so keep the largest and say so.
    if len(skels) > 1:
        sizes = {k: len(v.vertices) for k, v in skels.items()}
        print(f"  warning: {len(skels)} components, keeping largest {sizes}")
    return max(skels.values(), key=lambda s: len(s.vertices))


def to_patient_mm(pos: np.ndarray, spacing, affine: np.ndarray) -> np.ndarray:
    """Kimimaro coordinates -> true patient physical millimetres.

    Kimimaro returns `voxel_index * anisotropy`, which is a scaled voxel frame:
    it has no origin offset and no direction cosines, so it is NOT patient
    space. Dividing out the spacing recovers the index, and the affine then
    places it in the DICOM frame of reference — which is what a POI must be
    stored in if it is to reproduce in a CT viewer.
    """
    vox = np.asarray(pos) / np.asarray(spacing)
    homogeneous = np.c_[vox, np.ones(len(vox))]
    return (affine @ homogeneous.T).T[:, :3]


def to_voxel(pos: np.ndarray, spacing, shape) -> np.ndarray:
    """Kimimaro coordinates -> integer voxel indices, clamped in bounds."""
    vox = np.rint(np.asarray(pos) / np.asarray(spacing)).astype(int)
    return np.clip(vox, 0, np.asarray(shape) - 1)


def segment_inside_mask(start, end, mask: np.ndarray, spacing) -> bool:
    """Only repair skeleton holes supported throughout by the airway mask."""
    steps = max(1, int(np.ceil(np.linalg.norm(end - start) / 0.25)))
    samples = np.linspace(start, end, steps + 1)
    voxels = np.rint(samples / np.asarray(spacing)).astype(int)
    if np.any(voxels < 0) or np.any(voxels >= np.asarray(mask.shape)):
        return False
    return bool(np.all(mask[tuple(voxels.T)] > 0))


def to_graph(skel, mask: np.ndarray, spacing) -> nx.Graph:
    """Recover skeleton holes without inventing airways across mask gaps."""
    g = nx.Graph()
    for i, (pos, radius) in enumerate(zip(skel.vertices, skel.radii)):
        g.add_node(i, pos=pos.astype(float), radius=float(radius))
    for a, b in skel.edges:
        pa, pb = skel.vertices[a], skel.vertices[b]
        g.add_edge(int(a), int(b), length=float(np.linalg.norm(pa - pb)), provenance="ct")

    if not g:
        raise SystemExit("graph: no airway tree in lung")
    while True:
        parts = sorted(nx.connected_components(g), key=len, reverse=True)
        if len(parts) == 1:
            break
        main = np.array(sorted(parts[0]))
        tree = cKDTree(skel.vertices[main])
        candidates = []
        for part in parts[1:]:
            nodes = np.array(sorted(part))
            distances, nearest = tree.query(skel.vertices[nodes])
            index = int(distances.argmin())
            a = int(nodes[index])
            b = int(main[nearest[index]])
            if segment_inside_mask(skel.vertices[a], skel.vertices[b], mask, spacing):
                candidates.append((float(distances[index]), a, b))
        if not candidates:
            break
        distance, a, b = min(candidates)
        g.add_edge(a, b, length=distance, provenance="ct")
        print(f"  repaired skeleton hole: {distance:.2f} mm inside airway mask")

    parts = sorted(nx.connected_components(g), key=len, reverse=True)
    if len(parts) > 1:
        dropped = sum(len(p) for p in parts[1:])
        share = dropped / g.number_of_nodes()
        print(f"  warning: {len(parts)} graph components, dropped {dropped} "
              f"vertices ({share:.0%} of the skeleton); no mask-contained join")
        if share > 0.05:
            print(f"  ** {share:.0%} of the skeleton is disconnected from the "
                  f"main tree — review before use")
    return g.subgraph(parts[0]).copy()


def find_root(g: nx.Graph, spacing, affine: np.ndarray) -> int:
    """Wide superior vertices identify the trachea even when its cut end fans out."""
    if not g:
        raise SystemExit("graph: no airway tree in lung")
    maximum = max(data["radius"] for _, data in g.nodes(data=True))
    wide = [n for n in g if g.nodes[n]["radius"] >= 0.5 * maximum]
    positions = np.array([g.nodes[n]["pos"] for n in wide])
    world = to_patient_mm(positions, spacing, affine)
    return wide[int(world[:, 2].argmax())]


def branch_points(g: nx.Graph, root: int) -> set[int]:
    """Vertices that start or end a branch: the root, bifurcations, and tips."""
    return {n for n in g if g.degree(n) != 2} | {root}


def trace_branches(g: nx.Graph, root: int) -> list[dict]:
    """Collapse degree-2 chains into branches, walking outward from the root.

    Each branch carries its full centreline so the renderer can follow the real
    curve rather than a straight line between bifurcations.
    """
    anchors = branch_points(g, root)
    branches: list[dict] = []
    seen_edges: set[frozenset] = set()
    queue = deque([(root, 0)])
    visited_anchors = {root}

    while queue:
        start, generation = queue.popleft()
        for first in g.neighbors(start):
            if frozenset((start, first)) in seen_edges:
                continue

            path = [start, first]
            seen_edges.add(frozenset((start, first)))
            # Walk the chain until the next anchor.
            while path[-1] not in anchors:
                nxt = [m for m in g.neighbors(path[-1]) if m != path[-2]]
                if not nxt:
                    break
                seen_edges.add(frozenset((path[-1], nxt[0])))
                path.append(nxt[0])

            centreline = np.array([g.nodes[n]["pos"] for n in path])
            radii = np.array([g.nodes[n]["radius"] for n in path])
            length = float(np.linalg.norm(np.diff(centreline, axis=0), axis=1).sum())

            branches.append({
                "from": start,
                "to": path[-1],
                "generation": generation,
                "length_mm": length,
                "centreline": centreline,
                "radii": radii,
            })

            end = path[-1]
            if end in anchors and end not in visited_anchors:
                visited_anchors.add(end)
                queue.append((end, generation + 1))

    return branches


def prune(branches: list[dict], min_mm: float) -> list[dict]:
    """Drop short terminal stubs. Interior branches are kept regardless —
    removing one would disconnect everything distal to it."""
    ends = [b["to"] for b in branches]
    interior = {n for n in ends if ends.count(n) > 1} | {b["from"] for b in branches}
    return [b for b in branches
            if b["length_mm"] >= min_mm or b["to"] in interior]


def prune_blind_alleys(branches: list[dict]) -> list[dict]:
    """Drop branches whose subtree never reaches lung tissue.

    The airway mask ends in a flat disc where the trachea was cut off by the
    scan's field of view. Kimimaro skeletonises that disc into a fan of stubs
    roughly one tracheal diameter long (~17mm on case 1) with large radii,
    because radius is distance-to-background and the tube is wide there. They
    look exactly like main bronchi by length and calibre, and the first version
    of this pipeline mistook them for the carina.

    Topology separates them cleanly: a real airway leads to lung. Anything whose
    entire subtree carries no lobe label leads nowhere a learner can navigate to,
    so it is either a cut-face artefact or a mediastinal dead end. Either way it
    is not part of the navigable tree.
    """
    kids: dict[int, list[int]] = {}
    for i, b in enumerate(branches):
        kids.setdefault(b["from"], []).append(i)

    memo: dict[int, bool] = {}

    def reaches_lung(i: int) -> bool:
        if i in memo:
            return memo[i]
        memo[i] = bool(branches[i]["lobe"])          # provisional, breaks cycles
        found = bool(branches[i]["lobe"]) or any(
            reaches_lung(j) for j in kids.get(branches[i]["to"], []))
        memo[i] = found
        return found

    keep = [b for i, b in enumerate(branches) if reaches_lung(i)]
    dropped = len(branches) - len(keep)
    if dropped:
        print(f"  pruned   {dropped} branches whose subtree never reaches lung")
    return keep


def label_lobes(branches: list[dict], lungs: np.ndarray | None, spacing) -> None:
    """Assign each branch the lobe its centreline sits in, by majority vote.

    Records a confidence so branches straddling a boundary can be flagged rather
    than trusted: fissures are incomplete in most people, so the lobe edge is
    often inferred rather than anatomical, and RUL/RML is the usual offender.

    A branch outside the lung mask altogether gets `None`, which is the correct
    answer for the trachea and main bronchi — they are mediastinal, not
    intrapulmonary, so no lobe applies.
    """
    if lungs is None:
        for b in branches:
            b["lobe"], b["lobe_confidence"] = None, None
        return

    for b in branches:
        vox = to_voxel(b["centreline"], spacing, lungs.shape)
        votes = lungs[vox[:, 0], vox[:, 1], vox[:, 2]]
        votes = votes[votes > 0]

        if votes.size == 0:
            b["lobe"], b["lobe_confidence"] = None, 0.0
            continue

        values, counts = np.unique(votes, return_counts=True)
        b["lobe"] = int(values[counts.argmax()])
        b["lobe_confidence"] = round(float(counts.max() / votes.size), 3)


def recollapse(branches: list[dict], root_node: int) -> list[dict]:
    """Merge chains left behind after pruning, and renumber generations.

    Removing the cut-face stubs turns their parent bifurcations into plain
    continuations, but the branches either side stay split. Without this the
    trachea reads as three branches and the carina lands at generation 3.
    """
    kids: dict[int, list[int]] = {}
    for i, b in enumerate(branches):
        kids.setdefault(b["from"], []).append(i)

    merged: list[dict] = []
    queue = deque([(i, 0) for i in kids.get(root_node, [])])
    while queue:
        i, gen = queue.popleft()
        b = dict(branches[i])
        # Absorb single-child continuations into this branch.
        while True:
            nxt = kids.get(b["to"], [])
            if len(nxt) != 1:
                break
            c = branches[nxt[0]]
            b["centreline"] = np.vstack([b["centreline"], c["centreline"][1:]])
            b["radii"] = np.concatenate([b["radii"], c["radii"][1:]])
            b["length_mm"] += c["length_mm"]
            b["to"] = c["to"]
            if not b.get("lobe") and c.get("lobe"):
                b["lobe"] = c["lobe"]
                b["lobe_confidence"] = c["lobe_confidence"]
        b["generation"] = gen
        merged.append(b)
        for j in kids.get(b["to"], []):
            queue.append((j, gen + 1))
    return merged


def name_proximal(branches: list[dict]) -> None:
    """Name the proximal airways from topology and where each subtree drains.

    The lobe mask covers parenchyma, so it cannot label the trachea, main
    bronchi, bronchus intermedius or the lobar bronchi themselves — those run
    through the hilum, outside lung tissue. On case 1 nothing gets a lobe until
    generation 7, which is precisely the named anatomy a learner needs.

    Topology recovers it exactly, with no model and no atlas: a branch whose
    entire subtree drains one lobe *is* that lobe's bronchus, and the classic
    named airways fall out of which sets of lobes lie below them.
    """
    kids: dict[int, list[int]] = {}
    for i, b in enumerate(branches):
        kids.setdefault(b["from"], []).append(i)

    drains: dict[int, set] = {}

    def collect(i: int) -> set:
        if i in drains:
            return drains[i]
        below = set()
        if branches[i]["lobe"]:
            below.add(branches[i]["lobe"])
        for j in kids.get(branches[i]["to"], []):
            below |= collect(j)
        drains[i] = below
        return below

    roots = [i for i, b in enumerate(branches) if b["generation"] == 0]
    for i in roots:
        collect(i)
    for i in range(len(branches)):
        collect(i)

    LEFT, RIGHT = {1, 2}, {3, 4, 5}
    INTERMEDIUS = {4, 5}

    # A named structure is the *most proximal* branch draining a given set;
    # deeper branches with the same set are continuations of it.
    claimed: dict[frozenset, int] = {}
    for i in sorted(range(len(branches)), key=lambda k: branches[k]["generation"]):
        key = frozenset(drains[i])
        if key and key not in claimed:
            claimed[key] = i

    for i, b in enumerate(branches):
        below = drains[i]
        name = None
        if b["generation"] == 0:
            name = "trachea"
        elif claimed.get(frozenset(below)) == i:
            if below == LEFT:
                name = "left main bronchus"
            elif below == RIGHT:
                name = "right main bronchus"
            elif below == INTERMEDIUS:
                name = "bronchus intermedius"
            elif len(below) == 1:
                name = f"{LOBE_NAMES[next(iter(below))]} bronchus"
        b["airway_name"] = name

        # Fill the lobe in for unlabelled branches whose subtree is unanimous.
        if not b["lobe"] and len(below) == 1:
            b["lobe"] = next(iter(below))
            b["lobe_source"] = "inferred from subtree"
        elif b["lobe"]:
            b["lobe_source"] = "lobe mask"
        else:
            b["lobe_source"] = None


def _interpolated_spans(points: np.ndarray, bridge_mask: np.ndarray,
                        affine: np.ndarray) -> tuple[list[list[float]], float]:
    inverse = np.linalg.inv(affine)
    offset = 0.0
    spans = []
    inside_length = 0.0
    for start, end in zip(points[:-1], points[1:]):
        delta = end - start
        length = float(np.linalg.norm(delta))
        if length <= 1e-12:
            continue
        steps = max(1, int(np.ceil(length / 0.25)))
        sub_length = length / steps
        for index in range(steps):
            midpoint = start + delta * ((index + 0.5) / steps)
            voxel = np.rint((inverse @ np.r_[midpoint, 1.0])[:3]).astype(int)
            if np.all(voxel >= 0) and np.all(voxel < np.asarray(bridge_mask.shape)):
                if bridge_mask[tuple(voxel)] > 0:
                    inside_length += sub_length
                    a = offset + index * sub_length
                    b = a + sub_length
                    if spans and a <= spans[-1][1] + 1e-9:
                        spans[-1][1] = b
                    else:
                        spans.append([a, b])
        offset += length

    rounded = []
    for start, end in spans:
        a, b = round(start, 1), round(end, 1)
        if b <= a:
            b = round(a + 0.1, 1)
        if rounded and a <= rounded[-1][1]:
            rounded[-1][1] = max(rounded[-1][1], b)
        else:
            rounded.append([a, b])
    return rounded, inside_length


def add_edge_provenance(edges: list[dict], bridge_mask: np.ndarray | None,
                       affine: np.ndarray) -> tuple[str, int, float, int]:
    """Tag edges by bridge-mask overlap and propagate reachability downstream."""
    if bridge_mask is None:
        for edge in edges:
            edge.update(interpolated_mm=0.0, interpolated_spans_mm=[], provenance="ct",
                        downstream_of_interpolated=False)
        return "no bridge mask", 0, 0.0, 0

    kids: dict[int, list[dict]] = {}
    for edge in edges:
        edge["downstream_of_interpolated"] = False
        points = np.asarray(edge["centreline_mm"], dtype=float)
        spans, raw_amount = _interpolated_spans(points, bridge_mask, affine)
        amount = round(raw_amount, 1)
        if spans and amount == 0:
            amount = 0.1
        edge["interpolated_spans_mm"] = spans
        edge["interpolated_mm"] = amount
        edge["provenance"] = "interpolated" if amount > 0 else "ct"
        kids.setdefault(edge["from"], []).append(edge)

    downstream = 0
    queue = [(edge["to"], edge["provenance"] == "interpolated")
             for edge in kids.get(0, [])]
    while queue:
        node, inherited = queue.pop(0)
        for edge in kids.get(node, []):
            edge["downstream_of_interpolated"] = inherited
            downstream += int(inherited)
            queue.append((edge["to"], inherited or edge["provenance"] == "interpolated"))
    count = sum(edge["provenance"] == "interpolated" for edge in edges)
    total = round(sum(edge["interpolated_mm"] for edge in edges), 1)
    return "bridge mask", count, total, downstream


def export(branches: list[dict], case: str, spacing, affine: np.ndarray,
           out: Path, review: list[dict] | None = None,
           bridge_mask: np.ndarray | None = None,
           bridge_source: str = "no bridge mask",
           source: str = "AeroPath (MIT), expert airway mask",
           lobes: np.ndarray | None = None,
           arteries: Path | None = None) -> dict:
    node_ids: dict[int, int] = {}

    for b in branches:
        b["centreline"] = to_patient_mm(b["centreline"], spacing, affine)

    def node_id(raw: int) -> int:
        return node_ids.setdefault(raw, len(node_ids))

    nodes: dict[int, dict] = {}
    edges = []
    for b in branches:
        a, z = node_id(b["from"]), node_id(b["to"])
        for nid, pos, rad in ((a, b["centreline"][0], b["radii"][0]),
                              (z, b["centreline"][-1], b["radii"][-1])):
            nodes.setdefault(nid, {
                "id": nid,
                "position_mm": [round(float(v), 3) for v in pos],
                "radius_mm": round(float(rad), 3),
            })
        edges.append({
            "from": a,
            "to": z,
            "generation": b["generation"],
            "length_mm": round(b["length_mm"], 2),
            "radius_mm": round(float(np.median(b["radii"])), 3),
            "lobe": b["lobe"],
            "lobe_name": LOBE_NAMES.get(b["lobe"]),
            "lobe_source": b.get("lobe_source"),
            "airway_name": b.get("airway_name"),
            "snomed": trachea_snomed(b.get("airway_name")),
            "lobe_confidence": b["lobe_confidence"],
            "centreline_mm": [[round(float(v), 2) for v in p]
                              for p in b["centreline"]],
        })

    _, interp_count, interp_total, downstream_count = add_edge_provenance(
        edges, bridge_mask, affine
    )
    started = time.perf_counter()
    from estimated_airway import estimate_airway, load_artery_mask
    artery_mask = load_artery_mask(arteries)
    if (artery_mask is not None and lobes is not None
            and tuple(artery_mask.shape) != tuple(np.shape(lobes))):
        print("  warning: artery mask grid does not match the lobe mask; no estimates")
        artery_mask = None
    estimated, estimated_summary = estimate_airway(
        edges, artery_mask, lobes, spacing, affine
    )
    elapsed = time.perf_counter() - started
    graph = {
        "case": case,
        "coordinate_system": "patient_physical_mm",
        "source": source,
        "snomed_source": SNOMED_SOURCE,
        "lobes": exported_lobes(),
        "voxel_spacing_mm": [round(s, 4) for s in spacing],
        "root_node_id": 0,
        "provenance_source": (f"bridge mask {bridge_source}" if bridge_mask is not None
                              else "no bridge mask"),
        "review": review or [],
        "nodes": sorted(nodes.values(), key=lambda n: n["id"]),
        "edges": edges,
        "estimated": estimated,
        "estimated_summary": estimated_summary,
    }
    out.write_text(json.dumps(graph, separators=(",", ":")))
    print(f"  provenance {interp_count} interpolated edges, {interp_total:.1f} mm, "
          f"{downstream_count} downstream edges")
    print(f"  estimated {estimated_summary['estimated']} of "
          f"{estimated_summary['terminals']} terminals, "
          f"{estimated_summary['total_mm']:.1f} mm, "
          f"max {estimated_summary['max_mm']:.1f} mm ({elapsed:.2f} s)")
    return graph


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("case_dir", type=Path)
    ap.add_argument("-o", "--out", type=Path, default=OUT)
    ap.add_argument("--lobes", type=Path,
                    help="5-label lobe mask from TotalSegmentator. Without it "
                         "the fallback is AeroPath's lungs file, which is "
                         "binary and cannot support lobar naming.")
    ap.add_argument("--airways", type=Path,
                    help="override the airway mask, e.g. a predicted one")
    ap.add_argument("--bridges", type=Path, help="mask of interpolated bridge voxels")
    ap.add_argument("--arteries", type=Path,
                    help="pulmonary artery mask from the same TotalSegmentator run; "
                         "a missing file leaves estimated airway empty")
    args = ap.parse_args()

    airway_path = args.airways or next(args.case_dir.glob("*label_airways.nii.gz"))
    lung_paths = ([args.lobes] if args.lobes
                  else list(args.case_dir.glob("*label_lungs.nii.gz")))
    case = args.case_dir.name

    print(f"case {case}")
    mask, affine, spacing = load_mask(airway_path)
    bridge_path = args.bridges or airway_path.with_name("airway_bridges.nii.gz")
    bridge_mask = (np.asanyarray(nib.load(bridge_path).dataobj).astype(np.uint8)
                   if bridge_path.is_file() else None)
    print(f"  mask     {mask.shape} at {tuple(round(s, 3) for s in spacing)} mm, "
          f"{int((mask > 0).sum()):,} voxels")

    skel = skeletonise(mask, spacing)
    print(f"  skeleton {len(skel.vertices):,} vertices")

    g = to_graph(skel, mask, spacing)
    root = find_root(g, spacing, affine)
    print(f"  root     vertex {root}, radius {g.nodes[root]['radius']:.2f} mm")

    branches = trace_branches(g, root)
    kept = prune(branches, MIN_BRANCH_MM)
    print(f"  branches {len(branches)} traced, {len(kept)} kept "
          f"(pruned < {MIN_BRANCH_MM} mm)")

    lungs = None
    if lung_paths:
        lungs = np.asanyarray(nib.load(lung_paths[0]).dataobj).astype(np.int16)
        print(f"  lungs    labels {sorted(int(v) for v in np.unique(lungs))}")
    label_lobes(kept, lungs, spacing)
    kept = prune_blind_alleys(kept)
    kept = recollapse(kept, root)
    if not kept:
        raise SystemExit("graph: no airway tree in lung")
    name_proximal(kept)
    named = sum(1 for b in kept if b.get("airway_name"))
    with_lobe = sum(1 for b in kept if b["lobe"])
    print(f"  labels   {with_lobe} branches in a lobe, {named} named airways")

    # A lobe with no airways means the mask is missing anatomy, not that the
    # patient lacks a lobe. Case 5's right lower lobe is absent entirely.
    present = {b["lobe"] for b in kept if b["lobe"]}
    empty = [LOBE_NAMES[v] for v in sorted(LOBE_NAMES) if v not in present]
    if empty:
        print(f"  ** no airways reach: {', '.join(empty)} — "
              f"missing from the mask, review before use")

    review = [{"kind": "lobe_without_airways", "lobe": name} for name in empty]
    if args.lobes:
        voxel_ml = float(np.prod(nib.load(args.lobes).header.get_zooms()[:3])) / 1000
        volumes = {value: int((lungs == value).sum()) * voxel_ml
                   for value in LOBE_NAMES if lungs is not None}
        present_volumes = [volume for volume in volumes.values() if volume > 0]
        if present_volumes:
            median_ml = float(np.median(present_volumes))
            for value, name in LOBE_NAMES.items():
                volume_ml = volumes.get(value, 0.0)
                if volume_ml < 0.25 * median_ml:
                    print(f"  ** small lobe: {name} {volume_ml:.1f} ml vs median "
                          f"{median_ml:.1f} ml — clinical review before use")
                    review.append({"kind": "small_lobe", "lobe": name,
                                   "ml": round(volume_ml, 1),
                                   "median_ml": round(median_ml, 1)})

    args.out.mkdir(parents=True, exist_ok=True)
    path = args.out / f"{case}_graph.json"
    graph_source = (f"airway mask {airway_path.name}" if args.airways
                   else "AeroPath (MIT), expert airway mask")
    graph = export(kept, case, spacing, affine, path, review, bridge_mask,
                   bridge_path.name if bridge_mask is not None else "no bridge mask",
                   graph_source, lobes=lungs, arteries=args.arteries)

    depth = max(e["generation"] for e in graph["edges"])
    size_kb = path.stat().st_size / 1024
    print(f"  wrote    {path} — {len(graph['nodes'])} nodes, "
          f"{len(graph['edges'])} edges, {depth} generations, {size_kb:.0f} KB")


if __name__ == "__main__":
    main()
