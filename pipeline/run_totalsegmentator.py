"""Run TotalSegmentator for airway and lobe masks.

The airway fallback combines the lung_vessels task's lung_airways lumen with
the total task's trachea, then keeps the largest 26-connected component. Pass
--legacy to use lung_vessels_LEGACY's lung_trachea_bronchia instead. The total
task also produces the five lobe masks, which are merged into lobes.nii.gz;
the trachea is deliberately excluded from that merge.
"""

import argparse
import subprocess
import sys
from pathlib import Path

import nibabel as nib
import numpy as np
from scipy import ndimage

LOBES = {
    "lung_upper_lobe_left": 1,
    "lung_lower_lobe_left": 2,
    "lung_upper_lobe_right": 3,
    "lung_middle_lobe_right": 4,
    "lung_lower_lobe_right": 5,
}
ROIS = [*LOBES, "trachea"]
BRIDGE_MAX_GAP_MM = 4.0
BRIDGE_MIN_VOLUME_MM3 = 20.0
BRIDGE_RADIUS_MM = 1.0


def run(cmd: list[str]) -> None:
    print("  $", " ".join(cmd), flush=True)
    r = subprocess.run(cmd)
    if r.returncode != 0:
        sys.exit(f"failed: {' '.join(cmd)}")


def run_cached(cmd: list[str], expected: list[Path]) -> None:
    missing = [path for path in expected if not path.exists()]
    if not missing:
        print(f"  cached: {cmd[cmd.index('-o') + 1]}")
        return
    run(cmd)


def merge_lobes(seg_dir: Path, out: Path) -> None:
    """Five binary masks -> one labelled volume, 1..5 as in LOBES."""
    ref = None
    merged = None
    for name, value in LOBES.items():
        path = seg_dir / f"{name}.nii.gz"
        if not path.exists():
            print(f"  missing {name}")
            continue
        img = nib.load(path)
        data = np.asanyarray(img.dataobj) > 0
        if merged is None:
            ref = img
            merged = np.zeros(data.shape, dtype=np.uint8)
        merged[data] = value
    if merged is None:
        sys.exit("no lobe masks produced")
    nib.save(nib.Nifti1Image(merged, ref.affine, ref.header), out)
    counts = {n: int((merged == v).sum()) for n, v in LOBES.items()}
    print(f"  merged -> {out}")
    for n, c in counts.items():
        print(f"    {n:<24} {c:>12,} voxels")


def bridge_airway_components(
    combined: np.ndarray, spacing: tuple[float, float, float]
) -> tuple[np.ndarray, list[tuple[float, float]], int]:
    """Keep the largest airway component and bridge nearby, sizable pieces."""
    spacing = np.asarray(spacing, dtype=float)
    labels, component_count = ndimage.label(
        combined, structure=np.ones((3, 3, 3), dtype=np.uint8)
    )
    counts = np.bincount(labels.ravel())
    voxel_volume = float(np.prod(spacing))
    bridged: list[tuple[float, float]] = []
    if component_count:
        largest = int(counts[1:].argmax()) + 1
        result = labels == largest
        remaining = set(range(1, component_count + 1)) - {largest}
        while True:
            candidates = [label for label in remaining
                          if counts[label] * voxel_volume >= BRIDGE_MIN_VOLUME_MM3]
            if not candidates:
                break
            lo = np.maximum(np.array(np.where(result)).min(axis=1) -
                            np.ceil(BRIDGE_MAX_GAP_MM / spacing).astype(int), 0)
            hi = np.minimum(np.array(np.where(result)).max(axis=1) +
                            np.ceil(BRIDGE_MAX_GAP_MM / spacing).astype(int) + 1,
                            result.shape)
            slices = tuple(slice(int(a), int(b)) for a, b in zip(lo, hi))
            local_tree = result[slices]
            distances, nearest = ndimage.distance_transform_edt(
                ~local_tree, sampling=spacing, return_indices=True
            )
            best = None
            for label in candidates:
                coords = np.argwhere(labels[slices] == label)
                if not len(coords):
                    continue
                gaps = distances[tuple(coords.T)]
                idx = int(gaps.argmin())
                gap = float(gaps[idx])
                if best is None or gap < best[0]:
                    best = (gap, label, coords[idx], nearest[(slice(None), *coords[idx])])
            if best is None or best[0] > BRIDGE_MAX_GAP_MM:
                break
            gap, label, piece_local, tree_local = best
            piece = piece_local + lo
            tree = tree_local + lo
            delta = (piece - tree) * spacing
            length = float(np.linalg.norm(delta))
            steps = max(1, int(np.ceil(length / (min(spacing) * 0.5))))
            points = tree + np.linspace(0, 1, steps + 1)[:, None] * (piece - tree)
            radius_vox = np.ceil(BRIDGE_RADIUS_MM / spacing).astype(int)
            start = np.maximum(np.floor(points.min(axis=0)).astype(int) - radius_vox, 0)
            stop = np.minimum(np.ceil(points.max(axis=0)).astype(int) + radius_vox + 1,
                              result.shape)
            bridge_slice = tuple(slice(int(a), int(b)) for a, b in zip(start, stop))
            grid = np.ogrid[bridge_slice]
            segment_mm = (piece - tree) * spacing
            segment_length_squared = float(np.dot(segment_mm, segment_mm))
            projection = np.zeros(tuple(stop - start), dtype=np.float32)
            point_distance_squared = np.zeros_like(projection)
            for axis, g in enumerate(grid):
                relative_mm = (g - tree[axis]) * spacing[axis]
                projection += relative_mm * segment_mm[axis]
                point_distance_squared += relative_mm**2
            t = np.clip(projection / segment_length_squared, 0.0, 1.0)
            distance_squared = np.maximum(
                point_distance_squared - 2 * t * projection
                + t**2 * segment_length_squared, 0.0
            )
            result[bridge_slice] |= distance_squared <= BRIDGE_RADIUS_MM**2
            voxel_steps = max(1, int(np.max(np.abs(piece - tree)) * 4))
            centerline = np.rint(
                tree + np.linspace(0, 1, voxel_steps + 1)[:, None] * (piece - tree)
            ).astype(int)
            result[tuple(centerline.T)] = True
            result |= labels == label
            remaining.remove(label)
            bridged.append((counts[label] * voxel_volume, gap))
        dropped = len(remaining)
    else:
        result = combined
        dropped = 0

    final_labels, final_count = ndimage.label(
        result, structure=np.ones((3, 3, 3), dtype=np.uint8)
    )
    if final_count != 1:
        sizes = np.bincount(final_labels.ravel())[1:].tolist()
        raise RuntimeError(
            f"airway result has {final_count} components with sizes {sizes} voxels"
        )
    return result, bridged, dropped


def make_airway(airway_path: Path, trachea_path: Path, out: Path) -> None:
    airway_img = nib.load(airway_path)
    trachea_img = nib.load(trachea_path)
    airway = np.asanyarray(airway_img.dataobj) > 0
    trachea = np.asanyarray(trachea_img.dataobj) > 0
    if airway.shape != trachea.shape:
        raise ValueError(
            f"airway and trachea shapes differ: {airway.shape} vs {trachea.shape}"
        )

    combined = airway | trachea
    result, bridged, dropped = bridge_airway_components(
        combined, tuple(nib.affines.voxel_sizes(airway_img.affine))
    )

    header = airway_img.header.copy()
    header.set_data_dtype(np.uint8)
    nib.save(nib.Nifti1Image(result.astype(np.uint8), airway_img.affine, header), out)
    print(f"  combined airway voxels: {int(combined.sum()):,}")
    print(f"  bridged {len(bridged)} pieces: " + ", ".join(
        f"{volume:.1f} mm3 @ {gap:.1f} mm" for volume, gap in bridged
    ))
    print(f"  dropped {dropped} pieces")
    print(f"  retained voxels: {int(result.sum()):,}")
    print(f"  airway mask: {out}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("ct", type=Path)
    ap.add_argument("-o", "--out", type=Path, default=Path("ts_out"))
    ap.add_argument("--fast", action="store_true")
    ap.add_argument("--device", default="mps")
    ap.add_argument("--legacy", action="store_true")
    args = ap.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)

    airway_dir = args.out / "airways_source"
    if args.legacy:
        task = "lung_vessels_LEGACY"
        airway_filename = "lung_trachea_bronchia.nii.gz"
        airway_outputs = [airway_filename, "lung_vessels.nii.gz"]
    else:
        task = "lung_vessels"
        airway_filename = "lung_airways.nii.gz"
        airway_outputs = [airway_filename, "lung_airways_wall.nii.gz",
                          "lung_arteries.nii.gz", "lung_veins.nii.gz"]
    print(f"airways ({task} task)")
    cmd = ["TotalSegmentator", "-i", str(args.ct), "-o", str(airway_dir),
           "--task", task, "--device", args.device]
    if args.fast:
        cmd.append("--fast")
    run_cached(cmd, [airway_dir / name for name in airway_outputs])

    lobe_dir = args.out / "lobes"
    print("lobes and trachea (total task, six structures only)")
    cmd = ["TotalSegmentator", "-i", str(args.ct), "-o", str(lobe_dir),
           "--roi_subset", *ROIS, "--device", args.device]
    if args.fast:
        cmd.append("--fast")
    run_cached(cmd, [lobe_dir / f"{name}.nii.gz" for name in ROIS])

    merge_lobes(lobe_dir, args.out / "lobes.nii.gz")
    make_airway(airway_dir / airway_filename, lobe_dir / "trachea.nii.gz",
                args.out / "airways.nii.gz")


if __name__ == "__main__":
    main()
