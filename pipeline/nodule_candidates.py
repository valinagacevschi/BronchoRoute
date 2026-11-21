# Changes: pure nodule post-processing: split, fuse, measure, deterministic ids.
"""Turn a nodule mask and detector boxes into candidate records.

Pure functions only: no file I/O and no TotalSegmentator call. The caller supplies
the binary mask, the CT values for HU statistics, the labelled lobe volume and the
airway centreline points in RAS mm.

Conventions
-----------
* RAS mm is nibabel world space: ``world = affine @ [i, j, k, 1]``.
* A component is one 26-connected piece of the binary mask. Pieces below
  ``VOLUME_FLOOR_MM3`` are dropped.
* Components are numbered 1..n by volume descending, then centroid RAS
  ascending, so ids and list order are deterministic.
* Fusion matches a detector proposal to a component when their centre distance
  is at most ``max(MATCH_MIN_MM, half the largest box side)``. Each component
  keeps at most one proposal (the highest score), and each proposal is used once.
  Proposals left over become box-only candidates.
* Measurements come from the mask, never from the detector box. ``diameter_mm``
  is the equivalent-sphere diameter from the volume. ``axial_diameter_mm`` is the
  longest caliper distance in the axial plane between voxel corners.
"""
from __future__ import annotations

import math
from collections.abc import Iterable, Mapping, Sequence

import numpy as np
from scipy import ndimage
from scipy.spatial import ConvexHull

from snomed import LOBES as SNOMED_LOBES

VOLUME_FLOOR_MM3 = 20.0
MATCH_MIN_MM = 3.0

MASK_AND_BOX = "mask_and_box"
MASK_ONLY = "mask_only"
BOX_ONLY = "box_only"

_COMPONENT_STRUCTURE = np.ones((3, 3, 3), dtype=np.uint8)
_LOBE_NAMES = {label: info["key"] for label, info in SNOMED_LOBES.items()}


def _spacing(affine: np.ndarray) -> np.ndarray:
    return np.sqrt((np.asarray(affine, dtype=float)[:3, :3] ** 2).sum(axis=0))


def _sort_key(volume_mm3: float, center_ras_mm: Sequence[float]) -> tuple:
    return (-float(volume_mm3), *(float(v) for v in center_ras_mm))


def split_components(
    mask: np.ndarray,
    affine: np.ndarray,
    min_volume_mm3: float = VOLUME_FLOOR_MM3,
) -> list[dict]:
    """Split a binary mask into deterministic, volume-filtered components."""
    binary = np.asarray(mask) != 0
    affine = np.asarray(affine, dtype=float)
    labels, count = ndimage.label(binary, structure=_COMPONENT_STRUCTURE)
    voxel_volume = abs(float(np.linalg.det(affine[:3, :3])))
    components: list[dict] = []
    for label in range(1, int(count) + 1):
        voxels = np.argwhere(labels == label)
        volume_mm3 = len(voxels) * voxel_volume
        if volume_mm3 < min_volume_mm3:
            continue
        center = affine[:3, :3] @ voxels.mean(axis=0) + affine[:3, 3]
        components.append({
            "id": 0,
            "voxels": voxels.astype(np.intp),
            "center_ras_mm": tuple(float(v) for v in center),
            "volume_mm3": float(volume_mm3),
        })
    components.sort(key=lambda item: _sort_key(item["volume_mm3"], item["center_ras_mm"]))
    for identifier, component in enumerate(components, start=1):
        component["id"] = identifier
    return components


def _longest_axial_caliper(voxels: np.ndarray, spacing: np.ndarray) -> float:
    if len(voxels) == 0:
        return 0.0
    centers = voxels[:, :2].astype(float) * spacing[:2]
    corners = np.vstack([
        centers + np.array([dx * spacing[0] / 2.0, dy * spacing[1] / 2.0])
        for dx in (-1.0, 1.0)
        for dy in (-1.0, 1.0)
    ])
    try:
        hull_points = corners[np.asarray(ConvexHull(corners).vertices)]
    except Exception:
        hull_points = corners
    if len(hull_points) < 2:
        return 0.0
    diff = hull_points[:, None, :] - hull_points[None, :, :]
    return float(np.sqrt((diff ** 2).sum(axis=-1)).max())


def measure_component(
    component: Mapping,
    hu: np.ndarray | None,
    affine: np.ndarray,
) -> dict:
    """Add mask-derived volume, diameters and HU statistics to a component."""
    result = dict(component)
    voxels = np.asarray(component["voxels"], dtype=np.intp)
    volume_mm3 = float(component["volume_mm3"])
    result["diameter_mm"] = (
        2.0 * (3.0 * volume_mm3 / (4.0 * math.pi)) ** (1.0 / 3.0)
        if volume_mm3 > 0.0 else 0.0
    )
    result["axial_diameter_mm"] = _longest_axial_caliper(voxels, _spacing(affine))
    if hu is None:
        result["hu_mean"] = result["hu_min"] = result["hu_max"] = None
    else:
        values = np.asarray(hu, dtype=float)[tuple(voxels.T)]
        result["hu_mean"] = float(values.mean())
        result["hu_min"] = float(values.min())
        result["hu_max"] = float(values.max())
    return result


def lobe_for_component(
    component: Mapping,
    lobe_labels: np.ndarray | None,
    lobe_names: Mapping[int, str] | None = None,
) -> str | None:
    """Name the lobe that holds most of the component's voxels."""
    if lobe_labels is None:
        return None
    voxels = np.asarray(component["voxels"], dtype=np.intp)
    if len(voxels) == 0:
        return None
    labels = np.asarray(lobe_labels)[tuple(voxels.T)].astype(np.int64)
    labels = labels[labels > 0]
    if len(labels) == 0:
        return None
    values, counts = np.unique(labels, return_counts=True)
    chosen = int(values[int(np.argmax(counts))])
    names = _LOBE_NAMES if lobe_names is None else lobe_names
    return names.get(chosen)


def distance_to_airway(
    center_ras_mm: Sequence[float],
    airway_points_ras_mm: np.ndarray | None,
) -> float | None:
    """Euclidean distance from a point to the nearest centreline point, in mm."""
    if airway_points_ras_mm is None:
        return None
    points = np.asarray(airway_points_ras_mm, dtype=float)
    if points.size == 0:
        return None
    center = np.asarray(center_ras_mm, dtype=float)
    return float(np.min(np.linalg.norm(points - center, axis=1)))


def _proposal_key(proposal: Mapping) -> tuple:
    score = proposal.get("score")
    center = tuple(float(v) for v in proposal["ras_mm"])
    return (score is None, -(float(score) if score is not None else 0.0), *center)


def _match_distance(proposal: Mapping) -> float:
    largest_side = max(float(v) for v in proposal["size_mm"])
    return max(MATCH_MIN_MM, largest_side / 2.0)


def fuse_candidates(
    components: Sequence[Mapping],
    proposals: Iterable[Mapping],
) -> list[dict]:
    """Match detector proposals to mask components and label every candidate.

    Each proposal is tried highest score first. It takes the nearest unused
    component inside the match distance; otherwise it becomes a box-only
    candidate. Components without a proposal become mask-only candidates.
    """
    ordered = sorted(enumerate(proposals), key=lambda item: _proposal_key(item[1]))
    used: set[int] = set()
    matched: dict[int, Mapping] = {}
    box_only: list[Mapping] = []
    for _, proposal in ordered:
        proposal_center = np.asarray(proposal["ras_mm"], dtype=float)
        threshold = _match_distance(proposal)
        best: tuple[float, int] | None = None
        for index, component in enumerate(components):
            if index in used:
                continue
            distance = float(np.linalg.norm(
                np.asarray(component["center_ras_mm"], dtype=float) - proposal_center
            ))
            if distance <= threshold + 1e-9 and (best is None or distance < best[0]):
                best = (distance, index)
        if best is None:
            box_only.append(proposal)
        else:
            used.add(best[1])
            matched[best[1]] = proposal

    fused: list[dict] = []
    for index, component in enumerate(components):
        entry = dict(component)
        proposal = matched.get(index)
        if proposal is None:
            entry["match"] = MASK_ONLY
            entry["score"] = None
            entry["box_size_mm"] = None
        else:
            score = proposal.get("score")
            entry["match"] = MASK_AND_BOX
            entry["score"] = float(score) if score is not None else None
            entry["box_size_mm"] = tuple(float(v) for v in proposal["size_mm"])
        fused.append(entry)

    next_id = len(fused) + 1
    for proposal in box_only:
        score = proposal.get("score")
        fused.append({
            "id": next_id,
            "match": BOX_ONLY,
            "center_ras_mm": tuple(float(v) for v in proposal["ras_mm"]),
            "volume_mm3": None,
            "voxels": None,
            "score": float(score) if score is not None else None,
            "box_size_mm": tuple(float(v) for v in proposal["size_mm"]),
        })
        next_id += 1
    return fused


def build_candidates(
    mask: np.ndarray,
    affine: np.ndarray,
    hu: np.ndarray | None = None,
    proposals: Iterable[Mapping] = (),
    lobe_labels: np.ndarray | None = None,
    lobe_names: Mapping[int, str] | None = None,
    airway_points_ras_mm: np.ndarray | None = None,
    min_volume_mm3: float = VOLUME_FLOOR_MM3,
) -> list[dict]:
    """Full post-processing: split, fuse, then measure every mask-backed candidate."""
    components = split_components(mask, affine, min_volume_mm3)
    fused = fuse_candidates(components, proposals)
    for candidate in fused:
        if candidate["voxels"] is None:
            candidate.update({
                "diameter_mm": None,
                "axial_diameter_mm": None,
                "hu_mean": None,
                "hu_min": None,
                "hu_max": None,
                "lobe": None,
                "to_airway_mm": None,
            })
            continue
        measured = measure_component(candidate, hu, affine)
        for key in ("diameter_mm", "axial_diameter_mm", "hu_mean", "hu_min", "hu_max"):
            candidate[key] = measured[key]
        candidate["lobe"] = lobe_for_component(candidate, lobe_labels, lobe_names)
        candidate["to_airway_mm"] = distance_to_airway(
            candidate["center_ras_mm"], airway_points_ras_mm
        )
    return fused
