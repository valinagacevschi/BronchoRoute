# Changes: pure nodule post-processing: split, fuse, measure, deterministic ids.
"""Synthetic-array tests for the nodule candidate core (no I/O, no GPU)."""
from __future__ import annotations

import math

import numpy as np
import pytest

import nodule_candidates as nc


def affine(spacing: float = 1.0) -> np.ndarray:
    matrix = np.eye(4)
    matrix[0, 0] = matrix[1, 1] = matrix[2, 2] = spacing
    return matrix


def box(shape, lo, hi) -> np.ndarray:
    mask = np.zeros(shape, dtype=bool)
    mask[lo[0]:hi[0], lo[1]:hi[1], lo[2]:hi[2]] = True
    return mask


SHAPE = (32, 32, 32)


def test_split_keeps_two_components_separate_and_orders_by_volume():
    mask = box(SHAPE, (2, 2, 2), (5, 5, 5))       # 27 voxels
    mask |= box(SHAPE, (20, 20, 20), (25, 25, 25))  # 125 voxels

    components = nc.split_components(mask, affine())

    assert [c["id"] for c in components] == [1, 2]
    assert [c["volume_mm3"] for c in components] == [125.0, 27.0]
    assert np.allclose(components[0]["center_ras_mm"], [22.0, 22.0, 22.0])
    assert np.allclose(components[1]["center_ras_mm"], [3.0, 3.0, 3.0])


def test_equal_volumes_order_by_centroid_ras_ascending():
    mask = box(SHAPE, (20, 10, 10), (23, 13, 13))
    mask |= box(SHAPE, (2, 10, 10), (5, 13, 13))

    components = nc.split_components(mask, affine())

    assert [c["id"] for c in components] == [1, 2]
    assert [c["center_ras_mm"][0] for c in components] == [3.0, 21.0]


def test_component_below_volume_floor_is_dropped():
    mask = box(SHAPE, (2, 2, 2), (3, 3, 3))       # 1 voxel * 8 mm3 = 8 mm3
    mask |= box(SHAPE, (20, 20, 20), (24, 24, 24))  # 64 voxels * 8 mm3

    components = nc.split_components(mask, affine(spacing=2.0))

    assert len(components) == 1
    assert components[0]["volume_mm3"] == pytest.approx(512.0)


def test_repeated_calls_produce_identical_ids_and_order():
    mask = box(SHAPE, (2, 2, 2), (6, 6, 6))
    mask |= box(SHAPE, (18, 18, 18), (20, 20, 20))
    mask |= box(SHAPE, (10, 24, 4), (12, 28, 8))

    first = nc.split_components(mask, affine())
    second = nc.split_components(mask, affine())

    assert [(c["id"], tuple(c["center_ras_mm"]), c["volume_mm3"]) for c in first] == \
           [(c["id"], tuple(c["center_ras_mm"]), c["volume_mm3"]) for c in second]


def test_fusion_covers_mask_and_box_mask_only_and_box_only():
    mask = box(SHAPE, (10, 10, 10), (14, 14, 14))
    mask |= box(SHAPE, (22, 22, 22), (25, 25, 25))
    components = nc.split_components(mask, affine())
    proposals = [
        {"ras_mm": [12.0, 12.0, 12.0], "size_mm": [8, 8, 6], "score": 0.87},
        {"ras_mm": [30.0, 30.0, 30.0], "size_mm": [4, 4, 4], "score": 0.20},
    ]

    fused = nc.fuse_candidates(components, proposals)

    states = {candidate["match"] for candidate in fused}
    assert states == {nc.MASK_AND_BOX, nc.MASK_ONLY, nc.BOX_ONLY}
    by_match = {candidate["match"]: candidate for candidate in fused}
    assert by_match[nc.MASK_AND_BOX]["score"] == pytest.approx(0.87)
    assert by_match[nc.MASK_AND_BOX]["center_ras_mm"] == pytest.approx([11.5, 11.5, 11.5])
    assert by_match[nc.MASK_ONLY]["score"] is None
    assert by_match[nc.BOX_ONLY]["volume_mm3"] is None
    assert by_match[nc.BOX_ONLY]["voxels"] is None
    assert [candidate["id"] for candidate in fused] == sorted(
        candidate["id"] for candidate in fused
    )


def test_repeated_fusion_produces_identical_ids_and_order():
    mask = box(SHAPE, (10, 10, 10), (14, 14, 14))
    mask |= box(SHAPE, (22, 22, 22), (25, 25, 25))
    components = nc.split_components(mask, affine())
    proposals = [
        {"ras_mm": [11.5, 11.5, 11.5], "size_mm": [8, 8, 6], "score": 0.4},
        {"ras_mm": [30.0, 30.0, 30.0], "size_mm": [4, 4, 4], "score": 0.9},
    ]

    first = nc.fuse_candidates(components, proposals)
    second = nc.fuse_candidates(components, proposals)

    assert [(c["id"], c["match"], tuple(c["center_ras_mm"])) for c in first] == \
           [(c["id"], c["match"], tuple(c["center_ras_mm"])) for c in second]


def test_match_threshold_uses_the_largest_box_side():
    mask = box(SHAPE, (10, 10, 10), (13, 13, 13))
    components = nc.split_components(mask, affine())

    near_large_box = nc.fuse_candidates(
        components, [{"ras_mm": [15.0, 11.0, 11.0], "size_mm": [12, 4, 4], "score": 0.9}]
    )
    far_small_box = nc.fuse_candidates(
        components, [{"ras_mm": [15.0, 11.0, 11.0], "size_mm": [4, 4, 4], "score": 0.9}]
    )

    assert near_large_box[0]["match"] == nc.MASK_AND_BOX
    assert {candidate["match"] for candidate in far_small_box} == {
        nc.MASK_ONLY, nc.BOX_ONLY
    }


def test_two_components_closer_than_match_distance_stay_two_instances():
    mask = box(SHAPE, (10, 10, 10), (12, 12, 20))
    mask |= box(SHAPE, (14, 10, 10), (16, 12, 20))
    components = nc.split_components(mask, affine())
    assert len(components) == 2

    fused = nc.fuse_candidates(
        components, [{"ras_mm": [10.5, 10.5, 14.5], "size_mm": [8, 8, 8], "score": 0.9}]
    )

    assert len(fused) == 2
    assert [candidate["match"] for candidate in fused] == [nc.MASK_AND_BOX, nc.MASK_ONLY]


def test_measurements_come_from_the_mask():
    mask = box(SHAPE, (0, 0, 0), (2, 2, 2))
    components = nc.split_components(mask, affine(), min_volume_mm3=0.0)
    hu = np.full(SHAPE, 100.0)
    hu[0:2, 0:2, 0:2] = np.arange(8, dtype=float).reshape(2, 2, 2)

    measured = nc.measure_component(components[0], hu, affine())

    assert measured["volume_mm3"] == pytest.approx(8.0)
    expected = 2.0 * (3.0 * 8.0 / (4.0 * math.pi)) ** (1.0 / 3.0)
    assert measured["diameter_mm"] == pytest.approx(expected)
    assert measured["axial_diameter_mm"] == pytest.approx(math.sqrt(8.0))
    assert measured["hu_mean"] == pytest.approx(3.5)
    assert measured["hu_min"] == pytest.approx(0.0)
    assert measured["hu_max"] == pytest.approx(7.0)


def test_lobe_containment_uses_the_component_majority():
    mask = box(SHAPE, (10, 10, 10), (14, 14, 14))
    components = nc.split_components(mask, affine())
    lobes = np.zeros(SHAPE, dtype=np.uint8)
    lobes[10:14, 10:14, 10:14] = 2
    lobes[13, 13, 13] = 3

    lobe = nc.lobe_for_component(components[0], lobes)

    assert lobe == "lung_lower_lobe_left"


def test_distance_to_nearest_airway_point():
    points = np.array([[0.0, 0.0, 0.0], [10.0, 0.0, 0.0], [0.0, 3.0, 4.0]])

    assert nc.distance_to_airway([9.0, 0.0, 0.0], points) == pytest.approx(1.0)
    assert nc.distance_to_airway([0.0, 3.0, 4.0], points) == pytest.approx(0.0)
    assert nc.distance_to_airway([0.0, 0.0, 0.0], np.empty((0, 3))) is None


def test_build_candidates_empty_mask_is_empty_not_an_error():
    assert nc.build_candidates(np.zeros(SHAPE, dtype=bool), affine()) == []


def test_build_candidates_attaches_measurements_lobe_and_airway_distance():
    mask = box(SHAPE, (10, 10, 10), (14, 14, 14))
    hu = np.full(SHAPE, -100.0)
    hu[10:14, 10:14, 10:14] = -20.0
    lobes = np.zeros(SHAPE, dtype=np.uint8)
    lobes[10:14, 10:14, 10:14] = 4

    candidates = nc.build_candidates(
        mask,
        affine(),
        hu=hu,
        proposals=[{"ras_mm": [20.0, 20.0, 20.0], "size_mm": [4, 4, 4], "score": 0.5}],
        lobe_labels=lobes,
        airway_points_ras_mm=np.array([[10.0, 10.0, 10.0]]),
    )

    mask_candidate = next(c for c in candidates if c["match"] != nc.BOX_ONLY)
    assert mask_candidate["volume_mm3"] == pytest.approx(64.0)
    assert mask_candidate["hu_mean"] == pytest.approx(-20.0)
    assert mask_candidate["lobe"] == "lung_middle_lobe_right"
    assert mask_candidate["to_airway_mm"] == pytest.approx(
        np.linalg.norm(np.array([11.5, 11.5, 11.5]) - np.array([10.0, 10.0, 10.0]))
    )
    box_candidate = next(c for c in candidates if c["match"] == nc.BOX_ONLY)
    assert box_candidate["volume_mm3"] is None
    assert box_candidate["hu_mean"] is None
    assert box_candidate["lobe"] is None
    assert box_candidate["to_airway_mm"] is None
