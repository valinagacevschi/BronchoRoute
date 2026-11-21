# Changes: lung nodule stage writes the merged mask, instances, and axial contours.
"""Run TotalSegmentator lung_nodules after the lobe step and write nodule artifacts.

The stage is non-fatal: a skip or failure prints one line and exits 0, so the
segmentation job keeps its airway and lobe output. It skips when the five lobe
masks are missing or the stored Series description names a non-chest region.

Inputs come from the same TotalSegmentator output tree as the lobe step::

    TS_OUT/<case>/lobes/<lobe>.nii.gz      crop masks for the nodule task
    TS_OUT/<case>/lobes.nii.gz             labelled lobes for containment
    OUT/<case>/<case>_graph.json           airway centreline for distance

Artifacts are written next to the other TotalSegmentator masks::

    TS_OUT/<case>/nodules_source/lung_nodules.nii.gz   raw task output
    TS_OUT/<case>/nodule.nii.gz                       merged binary mask, CT grid
    TS_OUT/<case>/nodule_instances.json               {"nodules": [instance...]}
    TS_OUT/<case>/nodule_contours.json                {"shape": [...], "nodules": [...]}

An instance has these fields: ``id, center_ras_mm, volume_mm3, diameter_mm,
axial_diameter_mm, hu_mean, hu_min, hu_max, lobe, to_airway_mm, score, match,
box_size_mm``. Contours are ``build_contours`` output: ``shape`` and
``nodules[].slices[].{slice_index, polygons}`` in CT voxel coordinates.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path

import nibabel as nib
import numpy as np

from atomic_json import atomic_write_json
from nodule_candidates import build_candidates
from nodule_contours import build_contours, resample_to_grid
from paths import TS_OUT
from run_totalsegmentator import require_task
from snomed import LOBES as LOBE_TABLE

TASK = "lung_nodules"
RAW_DIR = "nodules_source"
RAW_MASK = "lung_nodules.nii.gz"
MERGED_MASK = "nodule.nii.gz"
INSTANCES = "nodule_instances.json"
CONTOURS = "nodule_contours.json"
ARTIFACTS = (MERGED_MASK, INSTANCES, CONTOURS)
LOBE_MASKS = tuple(LOBE_TABLE[label]["key"] for label in sorted(LOBE_TABLE))

_CHEST = re.compile(r"\b(?:CHEST|THORAX|THORACIC|LUNG|PULMON\w*)\b", re.I)
_NON_CHEST = re.compile(
    r"\b(?:ABDOMEN|ABDOMINAL|PELVI\w*|HEAD|BRAIN|NECK|KNEE|SHOULDER|HIP|"
    r"ANKLE|WRIST|PROSTATE|LIVER|RENAL|KIDNEY|KUB|SPINE)\b",
    re.I,
)


def nodule_command(
    ct: Path,
    out_dir: Path,
    lobes_dir: Path,
    device: str = "mps",
) -> list[str]:
    """TotalSegmentator command for the lobe-cropped lung nodule task."""
    require_task(TASK)
    return [
        sys.executable, "-m", "totalsegmentator.bin.TotalSegmentator",
        "-i", str(ct), "-o", str(out_dir),
        "--task", TASK, "--crop_path", str(lobes_dir),
        "--device", device,
    ]


def series_description(case_json: Path | None) -> str:
    """Stored Series description, or empty when the Case record is unreadable."""
    if case_json is None:
        return ""
    try:
        document = json.loads(Path(case_json).read_text())
    except (OSError, ValueError):
        return ""
    source = document.get("source") if isinstance(document, dict) else None
    return str((source or {}).get("series_description") or "")


def is_chest_series(description: str) -> bool:
    """A named non-chest region skips; chest keywords and unknown text proceed."""
    text = description or ""
    if _CHEST.search(text):
        return True
    return not _NON_CHEST.search(text)


def skip_reason(lobes_dir: Path, description: str = "") -> str | None:
    """Why the stage cannot run, or None when it can."""
    if not is_chest_series(description):
        return "not a chest CT"
    missing = [
        name for name in LOBE_MASKS
        if not (Path(lobes_dir) / f"{name}.nii.gz").is_file()
    ]
    if missing:
        return f"lobe masks missing: {', '.join(missing)}"
    return None


def proposals_from_case(case_json: Path | None) -> list[dict]:
    """Detector candidates stored on the Case, shaped for mask fusion."""
    if case_json is None:
        return []
    try:
        document = json.loads(Path(case_json).read_text())
    except (OSError, ValueError):
        return []
    block = document.get("candidates") if isinstance(document, dict) else None
    items = block.get("items") if isinstance(block, dict) else None
    if not isinstance(items, list):
        return []
    proposals = []
    for item in items:
        if not isinstance(item, dict):
            continue
        ras = item.get("ras_mm")
        if not (isinstance(ras, (list, tuple)) and len(ras) == 3):
            continue
        size = item.get("size_mm")
        if not (isinstance(size, (list, tuple)) and len(size) == 3):
            diameter = item.get("diameter_mm")
            if diameter is None:
                continue
            size = [diameter, diameter, diameter]
        proposals.append({
            "ras_mm": [float(v) for v in ras],
            "size_mm": [float(v) for v in size],
            "score": item.get("score"),
        })
    return proposals


def _graph_points(graph_path: Path | None) -> np.ndarray | None:
    """Every exported centreline point in RAS mm, or None when unreadable."""
    if graph_path is None:
        return None
    try:
        graph = json.loads(Path(graph_path).read_text())
    except (OSError, ValueError):
        return None
    if not isinstance(graph, dict):
        return None
    points: list[list[float]] = []
    for node in graph.get("nodes") or []:
        position = node.get("position_mm")
        if isinstance(position, (list, tuple)) and len(position) == 3:
            points.append([float(v) for v in position])
    for edge in graph.get("edges") or []:
        for position in edge.get("centreline_mm") or []:
            if isinstance(position, (list, tuple)) and len(position) == 3:
                points.append([float(v) for v in position])
    return np.asarray(points, dtype=float) if points else None


def _float_or_none(value: object) -> float | None:
    return None if value is None else float(value)


def _instance_record(candidate: Mapping) -> dict:
    return {
        "id": int(candidate["id"]),
        "center_ras_mm": [float(v) for v in candidate["center_ras_mm"]],
        "volume_mm3": _float_or_none(candidate.get("volume_mm3")),
        "diameter_mm": _float_or_none(candidate.get("diameter_mm")),
        "axial_diameter_mm": _float_or_none(candidate.get("axial_diameter_mm")),
        "hu_mean": _float_or_none(candidate.get("hu_mean")),
        "hu_min": _float_or_none(candidate.get("hu_min")),
        "hu_max": _float_or_none(candidate.get("hu_max")),
        "lobe": candidate.get("lobe"),
        "to_airway_mm": _float_or_none(candidate.get("to_airway_mm")),
        "score": _float_or_none(candidate.get("score")),
        "match": candidate["match"],
        "box_size_mm": (
            None if candidate.get("box_size_mm") is None
            else [float(v) for v in candidate["box_size_mm"]]
        ),
    }


def _remove_artifacts(out_dir: Path) -> None:
    for name in ARTIFACTS:
        Path(out_dir, name).unlink(missing_ok=True)


def write_artifacts(
    ct_path: Path,
    mask_path: Path,
    out_dir: Path,
    *,
    lobes_path: Path | None = None,
    graph_path: Path | None = None,
    proposals: Iterable[Mapping] = (),
) -> dict | None:
    """Build and write the three nodule artifacts; None when the mask is empty."""
    out = Path(out_dir)
    if not Path(mask_path).is_file():
        _remove_artifacts(out)
        return None

    ct_img = nib.load(ct_path)
    ct_data = np.asanyarray(ct_img.dataobj)
    mask_img = nib.load(mask_path)
    mask = resample_to_grid(
        np.asanyarray(mask_img.dataobj),
        mask_img.affine,
        ct_data.shape,
        ct_img.affine,
    )
    if not np.any(mask):
        _remove_artifacts(out)
        return None

    lobe_labels = None
    if lobes_path is not None and Path(lobes_path).is_file():
        labels = np.asanyarray(nib.load(lobes_path).dataobj)
        if tuple(labels.shape) == tuple(ct_data.shape):
            lobe_labels = labels

    candidates = build_candidates(
        mask,
        ct_img.affine,
        hu=ct_data,
        proposals=proposals,
        lobe_labels=lobe_labels,
        airway_points_ras_mm=_graph_points(graph_path),
    )

    contour_inputs = []
    for candidate in candidates:
        voxels = candidate.get("voxels")
        if voxels is None:
            continue
        component = np.zeros(ct_data.shape, dtype=bool)
        component[tuple(np.asarray(voxels, dtype=np.intp).T)] = True
        contour_inputs.append({
            "id": candidate["id"],
            "mask": component,
            "affine": ct_img.affine,
        })

    header = ct_img.header.copy()
    header.set_data_dtype(np.uint8)
    nib.save(
        nib.Nifti1Image(mask.astype(np.uint8), ct_img.affine, header),
        out / MERGED_MASK,
    )
    atomic_write_json(out / INSTANCES, {
        "nodules": [_instance_record(candidate) for candidate in candidates],
    })
    atomic_write_json(out / CONTOURS, build_contours(
        contour_inputs, ct_data.shape, ct_img.affine,
    ))
    return {"nodules": len(candidates)}


def run_task(cmd: Sequence[str]) -> None:
    """Run one TotalSegmentator pass; keep its output off the one-line failure path."""
    result = subprocess.run(list(cmd), capture_output=True, text=True)
    if result.returncode != 0:
        lines = (result.stderr or result.stdout or "").strip().splitlines()
        detail = lines[-1] if lines else f"exit {result.returncode}"
        raise RuntimeError(f"TotalSegmentator {TASK} failed: {detail}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("ct", type=Path)
    parser.add_argument("-o", "--out", type=Path, default=TS_OUT)
    parser.add_argument("--device", default="mps")
    parser.add_argument("--case-json", type=Path)
    parser.add_argument("--graph", type=Path)
    args = parser.parse_args(argv)
    out = Path(args.out)

    try:
        reason = skip_reason(out / "lobes", series_description(args.case_json))
        if reason:
            print(f"nodules skipped: {reason}", flush=True)
            return 0
        run_task(nodule_command(args.ct, out / RAW_DIR, out / "lobes", args.device))
        result = write_artifacts(
            args.ct,
            out / RAW_DIR / RAW_MASK,
            out,
            lobes_path=out / "lobes.nii.gz",
            graph_path=args.graph,
            proposals=proposals_from_case(args.case_json),
        )
    except Exception as exc:  # noqa: BLE001
        _remove_artifacts(out)
        print(f"nodules skipped: {exc}", flush=True)
        return 0

    if result is None:
        print("nodules: no nodule mask, no artifacts", flush=True)
    else:
        print(
            f"nodules: {result['nodules']} candidates -> {out / MERGED_MASK}",
            flush=True,
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
