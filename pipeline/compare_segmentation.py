# Changes: pass mask and RAS geometry to graph repair and tracheal rooting.
"""Score a predicted airway mask against AeroPath's expert mask.

Voxel Dice is the usual number but it is not the one that matters here: a
navigable tree is judged by how many branches and generations survive, because
that is what a learner flies through. A prediction can score well on Dice by
nailing the trachea and main bronchi while losing every distal branch.

Reports both, plus the graph-level comparison via build_graph.
"""

import argparse
from pathlib import Path

import nibabel as nib
import numpy as np

import build_graph as bg


def load(path: Path) -> np.ndarray:
    return (np.asanyarray(nib.load(path).dataobj) > 0).astype(np.uint8)


def voxel_scores(truth: np.ndarray, pred: np.ndarray) -> dict:
    t, p = truth.astype(bool), pred.astype(bool)
    inter = np.count_nonzero(t & p)
    return {
        "dice": 2 * inter / max(1, np.count_nonzero(t) + np.count_nonzero(p)),
        "sensitivity": inter / max(1, np.count_nonzero(t)),
        "precision": inter / max(1, np.count_nonzero(p)),
        "truth_voxels": int(np.count_nonzero(t)),
        "pred_voxels": int(np.count_nonzero(p)),
    }


def tree_stats(mask_path: Path, label: str) -> dict:
    """Run the real pipeline over a mask and report what a learner would get."""
    mask, affine, spacing = bg.load_mask(mask_path)
    skel = bg.skeletonise(mask, spacing)
    g = bg.to_graph(skel, mask, spacing)
    root = bg.find_root(g, spacing, affine)
    branches = bg.prune(bg.trace_branches(g, root), bg.MIN_BRANCH_MM)
    return {
        "label": label,
        "branches": len(branches),
        "generations": max(b["generation"] for b in branches),
        "length_mm": sum(b["length_mm"] for b in branches),
        "root_radius_mm": g.nodes[root]["radius"],
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("truth", type=Path, help="AeroPath expert airway mask")
    ap.add_argument("pred", type=Path, help="predicted airway mask")
    args = ap.parse_args()

    print("voxel agreement")
    v = voxel_scores(load(args.truth), load(args.pred))
    print(f"  Dice          {v['dice']:.3f}")
    print(f"  sensitivity   {v['sensitivity']:.3f}   (expert voxels recovered)")
    print(f"  precision     {v['precision']:.3f}   (predicted voxels that are real)")
    print(f"  voxels        expert {v['truth_voxels']:,} vs predicted {v['pred_voxels']:,}")

    print("\nnavigable tree")
    rows = [tree_stats(args.truth, "expert"), tree_stats(args.pred, "predicted")]
    print(f"  {'':<11}{'branches':>10}{'gens':>7}{'centreline':>13}{'root r':>9}")
    for r in rows:
        print(f"  {r['label']:<11}{r['branches']:>10}{r['generations']:>7}"
              f"{r['length_mm']/10:>11.0f} cm{r['root_radius_mm']:>8.1f}")

    e, p = rows
    print(f"\n  branches kept   {p['branches']/max(1,e['branches']):.0%}")
    print(f"  tree length     {p['length_mm']/max(1,e['length_mm']):.0%}")
    print(f"  depth           {p['generations']} vs {e['generations']} generations")


if __name__ == "__main__":
    main()
