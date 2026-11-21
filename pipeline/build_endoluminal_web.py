"""Build the endoluminal 3D assets (airway mesh, context layers, and graph) for one case.

Writes scene.json, graph.json and one .bin per mesh into
<case dir>/web, served by /case-web/<case>/.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np

import paths

# Ported from examples/airway_nav/prepare.py so the product viewer can show the
# same surface render without depending on the examples folder at runtime.
import networkx as nx
import nibabel as nib
import numpy as np
from scipy import ndimage
from scipy.spatial import cKDTree
from skimage.measure import marching_cubes
from skimage.morphology import skeletonize

# RAS (nibabel world) -> three.js (x right on screen in an anterior view, y up, z toward viewer)
RAS_TO_THREE = np.array([[-1, 0, 0], [0, 0, 1], [0, 1, 0]], float)

LOBES = ["lung_upper_lobe_left", "lung_lower_lobe_left", "lung_upper_lobe_right",
         "lung_middle_lobe_right", "lung_lower_lobe_right"]
# name: (source masks, colour, opacity, grid mm, smoothing sigma in voxels)
LAYERS = {
    "lungs": (LOBES, "#9ec5fe", 0.10, 2.5, 1.5),
    "heart": (["heart"], "#d6336c", 0.85, 1.5, 1.0),
    "aorta": (["aorta"], "#e03131", 0.85, 1.5, 1.0),
    "pulmonary_vein": (["pulmonary_vein"], "#e03131", 0.85, 1.5, 1.0),
    "arteries": (["lung_arteries"], "#3b5bdb", 0.9, 0.9, 0.6),
    "veins": (["lung_veins"], "#e03131", 0.9, 0.9, 0.6),
    "airways": (["lung_airways", "trachea"], "#ece6e8", 1.0, 0.7, 0.7),
    "nodule": (["nodule"], "#f2b632", 1.0, 0.7, 1.0),
}
# Mask directory under TS_OUT/<case> for each context layer. The merged airway
# mask sits at the root; vessels come from the lung_vessels run and the other
# structures from the total_v2 run.
LAYER_DIRS = {
    "lungs": "lobes",
    "heart": "lobes",
    "aorta": "lobes",
    "pulmonary_vein": "lobes",
    "arteries": "airways_source",
    "veins": "airways_source",
    "nodule": ".",
}
MASK_NAMES = {"airways": ["airways"]}


class IsoGrid:
    """A crop of a mask resampled to (near) isotropic voxels, with a map back to world mm."""
    def __init__(self, mask, affine, iso, pad=4):
        idx = np.argwhere(mask)
        lo = np.maximum(idx.min(0) - pad, 0)
        hi = np.minimum(idx.max(0) + pad + 1, mask.shape)
        sub = mask[tuple(slice(a, b) for a, b in zip(lo, hi))].astype(np.float32)
        spacing = np.sqrt((affine[:3, :3] ** 2).sum(0))
        self.vol = ndimage.zoom(sub, spacing / iso, order=1)
        # ndimage.zoom aligns the corner voxels, so this is the exact old/new index ratio
        self.scale = (np.array(sub.shape) - 1) / np.maximum(np.array(self.vol.shape) - 1, 1)
        self.spacing = spacing * self.scale
        self.source_spacing = spacing
        self.lo, self.affine = lo, affine

    def to_world(self, ijk):
        old = self.lo + np.asarray(ijk, float) * self.scale
        return old @ self.affine[:3, :3].T + self.affine[:3, 3]


def to_three(world, center):
    return world @ RAS_TO_THREE.T - center


def load_mask(seg_dir, names, extra):
    out = None
    for n in names:
        path = extra.get(n) or seg_dir / f"{n}.nii.gz"
        if not Path(path).exists():
            continue
        img = nib.load(path)
        m = np.asarray(img.dataobj) > 0
        out = (m, img.affine) if out is None else (out[0] | m, out[1])
    return out


def smoothed(grid, sigma):
    # smooth thick-slice axes more, or the slice steps (e.g. 2.5 mm) show on the surface
    return ndimage.gaussian_filter(grid.vol, np.maximum(sigma, 0.6 * grid.source_spacing / grid.spacing))


def mesh(grid, sigma, center, flip):
    vol = smoothed(grid, sigma)
    if vol.max() < 0.5:
        return None
    verts, faces, _, _ = marching_cubes(vol, 0.5, allow_degenerate=False)
    if flip:
        faces = faces[:, ::-1]
    return to_three(grid.to_world(verts), center).astype(np.float32), faces.astype(np.uint32)


def skeleton_graph(binary, spacing, max_gap_mm=8.0):
    """26-connected skeleton voxels as a weighted tree, from the largest piece plus nearby pieces."""
    skel = skeletonize(binary)
    pts = np.argwhere(skel)
    index = -np.ones(skel.shape, np.int64)
    index[tuple(pts.T)] = np.arange(len(pts))
    g = nx.Graph()
    g.add_nodes_from(range(len(pts)))
    offsets = [o for o in np.ndindex(3, 3, 3) if o > (1, 1, 1)]  # 13 forward neighbours
    for o in offsets:
        o = np.array(o) - 1
        nb = pts + o
        ok = np.all((nb >= 0) & (nb < skel.shape), axis=1)
        j = index[tuple(nb[ok].T)]
        i = np.nonzero(ok)[0][j >= 0]
        g.add_weighted_edges_from(zip(i, j[j >= 0], [float(np.linalg.norm(o * spacing))] * len(i)))
    g = bridge_pieces(g, pts * spacing, max_gap_mm)
    # 26-connectivity adds diagonal shortcuts (voxel triangles) that look like junctions;
    # the airway is a tree, so the minimum spanning tree keeps only the real links.
    return nx.minimum_spanning_tree(g), pts


def bridge_pieces(g, xyz, max_gap_mm):
    """The model often leaves small gaps in thin airways, which cuts whole branches off the tree.
    Join each piece to the main tree at its closest point if the gap is at most max_gap_mm."""
    pieces = sorted(nx.connected_components(g), key=len, reverse=True)
    main, rest = set(pieces[0]), [list(p) for p in pieces[1:] if len(p) >= 5]
    added = True
    while added and rest:
        added = False
        main_ids = np.fromiter(main, int)
        tree = cKDTree(xyz[main_ids])
        for piece in list(rest):
            d, j = tree.query(xyz[piece])
            k = int(np.argmin(d))
            if d[k] <= max_gap_mm:
                g.add_edge(piece[k], int(main_ids[j[k]]), weight=float(d[k]))
                main.update(piece)
                rest.remove(piece)
                added = True
    return g.subgraph(main).copy()


def prune_spurs(g, min_len, rounds=3):
    """Drop end branches shorter than min_len mm (skeleton noise), so junctions are real branches."""
    for _ in range(rounds):
        cut = []
        for end in [n for n in g if g.degree(n) == 1]:
            path, length, prev, cur = [end], 0.0, None, end
            while True:
                nxt = [n for n in g[cur] if n != prev]
                if len(nxt) != 1:
                    break
                length += g[cur][nxt[0]]["weight"]
                prev, cur = cur, nxt[0]
                if g.degree(cur) >= 3:
                    if length < min_len:
                        cut += path
                    break
                path.append(cur)
        if not cut:
            break
        g.remove_nodes_from(cut)
    return g


def build_graph(grid, center, min_spur_mm, sigma=0.0):
    # same smoothing as the airway mesh, so the route never leaves the drawn airway
    binary = ndimage.binary_closing(smoothed(grid, sigma) > 0.5, iterations=1)
    g, pts = skeleton_graph(binary, grid.spacing)
    g = prune_spurs(g, min_spur_mm)
    radius = ndimage.distance_transform_edt(binary, sampling=grid.spacing)
    nodes = sorted(g)
    remap = {n: i for i, n in enumerate(nodes)}
    pos = to_three(grid.to_world(pts[nodes]), center)
    r = radius[tuple(pts[nodes].T)]
    edges = [(remap[a], remap[b]) for a, b in g.edges]
    return {
        "pos": np.round(pos, 2).ravel().tolist(),
        "radius": np.round(r, 2).tolist(),
        "edges": np.array(edges, int).ravel().tolist(),
        "start": int(np.argmax(pos[:, 1])),  # most superior node = top of the trachea
    }




def build_case(case_id: str) -> Path:
    case_dir = paths.LOCAL_CASES / case_id
    if not (case_dir / "case.json").is_file():
        raise SystemExit(f"unknown case: {case_id}")
    masks = paths.TS_OUT / case_id / "airways.nii.gz"
    if not masks.is_file():
        raise SystemExit(f"no airway mask at {masks}")
    out = case_dir / "web"
    out.mkdir(parents=True, exist_ok=True)
    airway = load_mask(masks.parent, ["airways"], {})
    if airway is None:
        raise SystemExit(f"no airway mask at {masks}")
    air_grid = IsoGrid(airway[0], airway[1], LAYERS["airways"][3])
    lo, hi = (air_grid.to_world(c) @ RAS_TO_THREE.T
              for c in (np.zeros(3), np.array(air_grid.vol.shape) - 1))
    center = (lo + hi) / 2
    layers = []
    for name, (names, color, opacity, iso, sigma) in LAYERS.items():
        mask = load_mask(masks.parent / LAYER_DIRS.get(name, "."),
                         MASK_NAMES.get(name, names), {})
        if mask is None:
            if name == "airways":
                raise SystemExit("airway mesh empty")
            print(f"{case_id}: no {name} mask, layer skipped")
            continue
        grid = air_grid if name == "airways" else IsoGrid(mask[0], mask[1], iso)
        m = mesh(grid, sigma, center, flip=np.linalg.det(mask[1][:3, :3]) < 0)
        if m is None:
            print(f"{case_id}: empty {name} mesh, layer skipped")
            continue
        verts, faces = m
        (out / f"{name}.bin").write_bytes(verts.tobytes() + faces.tobytes())
        layers.append({"name": name, "file": f"{name}.bin", "color": color,
                       "opacity": opacity, "vertices": len(verts),
                       "triangles": len(faces)})
    graph = build_graph(air_grid, center, 4.0, sigma=LAYERS["airways"][4])
    (out / "graph.json").write_text(json.dumps(graph, separators=(",", ":")))
    (out / "scene.json").write_text(json.dumps(
        {"layers": layers, "center_mm": center.tolist()}, indent=1))
    total = sum(layer["triangles"] for layer in layers)
    print(f"{case_id}: {len(layers)} layers, {total:,d} triangles -> {out}")
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("case_id")
    args = parser.parse_args()
    build_case(args.case_id)


if __name__ == "__main__":
    main()
