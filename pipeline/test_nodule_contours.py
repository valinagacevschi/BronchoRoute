# Changes: axial nodule contours traced on the half-voxel boundary, deterministic artifact order.
"""Synthetic-mask tests for the axial nodule contour builder (no GPU, no real data)."""
from __future__ import annotations

import json

import numpy as np

from nodule_contours import (
    build_contours,
    resample_to_grid,
    trace_mask_axial,
    trace_slice,
)


def _affine(scale=(1.0, 1.0, 1.0), origin=(0.0, 0.0, 0.0)) -> np.ndarray:
    affine = np.eye(4)
    affine[0, 0], affine[1, 1], affine[2, 2] = scale
    affine[:3, 3] = origin
    return affine


def test_single_voxel_is_a_half_voxel_diamond():
    slice_mask = np.zeros((5, 5), dtype=bool)
    slice_mask[2, 3] = True  # row (y) 2, column (x) 3

    polygons = trace_slice(slice_mask)

    assert len(polygons) == 1
    assert set(polygons[0]) == {(3.0, 1.5), (2.5, 2.0), (3.0, 2.5), (3.5, 2.0)}


def test_empty_slice_has_no_polygons():
    assert trace_slice(np.zeros((4, 6), dtype=bool)) == []


def test_disconnected_components_trace_separate_polygons_in_order():
    slice_mask = np.zeros((6, 8), dtype=bool)
    slice_mask[1, 1] = True
    slice_mask[4, 6] = True

    polygons = trace_slice(slice_mask)

    assert len(polygons) == 2
    # Deterministic order: the component with the lowest y then x comes first.
    assert min(point[1] for point in polygons[0]) < min(point[1] for point in polygons[1])
    assert len(polygons[0]) == len(polygons[1]) == 4


def test_rectangle_keeps_half_voxel_corners_without_smoothing():
    slice_mask = np.zeros((6, 6), dtype=bool)
    slice_mask[2:4, 2:4] = True  # a 2x2 square

    polygons = trace_slice(slice_mask)

    assert len(polygons) == 1
    assert set(polygons[0]) == {
        (2.0, 1.5), (3.0, 1.5), (3.5, 2.0), (3.5, 3.0),
        (3.0, 3.5), (2.0, 3.5), (1.5, 3.0), (1.5, 2.0),
    }


def test_polygon_points_stay_on_the_mask_boundary_inside_its_bounding_box():
    slice_mask = np.zeros((10, 12), dtype=bool)
    slice_mask[3:7, 4:9] = True

    polygons = trace_slice(slice_mask)

    ys, xs = np.nonzero(slice_mask)
    box = (xs.min() - 0.5, xs.max() + 0.5, ys.min() - 0.5, ys.max() + 0.5)
    assert polygons
    for polygon in polygons:
        for x, y in polygon:
            assert box[0] <= x <= box[1]
            assert box[2] <= y <= box[3]
            # A half-voxel boundary point sits midway between a mask voxel and
            # a background voxel (or on the padded edge of a border voxel).
            assert x * 2 == int(x * 2)
            assert y * 2 == int(y * 2)


def test_every_point_sits_between_a_mask_voxel_and_a_background_voxel():
    slice_mask = np.zeros((10, 12), dtype=bool)
    slice_mask[3:7, 4:9] = True
    height, width = slice_mask.shape

    def value(x: int, y: int) -> bool:
        return 0 <= x < width and 0 <= y < height and bool(slice_mask[y, x])

    for polygon in trace_slice(slice_mask):
        for x, y in polygon:
            if x != int(x):
                x0 = int(x - 0.5)
                assert value(x0, int(y)) != value(x0 + 1, int(y))
            elif y != int(y):
                y0 = int(y - 0.5)
                assert value(int(x), y0) != value(int(x), y0 + 1)
            else:
                raise AssertionError(f"point {x},{y} is not on a half-voxel edge")


def test_a_hole_produces_an_inner_polygon():
    slice_mask = np.ones((5, 5), dtype=bool)
    slice_mask[2, 2] = False

    polygons = trace_slice(slice_mask)

    assert len(polygons) == 2
    spans = [
        max(point[0] for point in polygon) - min(point[0] for point in polygon)
        for polygon in polygons
    ]
    assert max(spans) > min(spans)


def test_tracing_is_deterministic():
    slice_mask = np.zeros((9, 9), dtype=bool)
    slice_mask[2:5, 3:6] = True
    slice_mask[7, 1] = True

    assert trace_slice(slice_mask) == trace_slice(slice_mask)


def test_mask_tracing_skips_empty_slices_and_keeps_slice_indices():
    mask = np.zeros((5, 5, 7), dtype=bool)
    mask[2, 2, 1] = True
    mask[1:4, 1:4, 5] = True

    slices = trace_mask_axial(mask)

    assert [entry["slice_index"] for entry in slices] == [1, 5]
    assert len(slices[0]["polygons"]) == 1
    assert len(slices[1]["polygons"]) == 1
    # Points are in-plane voxel coordinates; the slice is the integer k.
    assert set(slices[0]["polygons"][0]) == {
        (2.0, 1.5), (1.5, 2.0), (2.0, 2.5), (2.5, 2.0),
    }


def test_resample_default_target_affine_keeps_same_index_positions():
    mask = np.zeros((5, 6, 7), dtype=bool)
    mask[1:4, 2:5, 3:6] = True
    affine = _affine(scale=(0.8, 0.8, 1.0), origin=(4.0, -2.0, 7.0))

    out = resample_to_grid(mask, affine, (6, 6, 7))

    assert np.array_equal(out[0:5], mask)
    assert not out[5].any()


def test_resample_on_the_same_grid_is_exact_and_boolean():
    mask = np.zeros((8, 9, 7), dtype=np.uint8)
    mask[2:5, 3:6, 1:4] = 1
    affine = _affine(scale=(0.7, 0.7, 1.25), origin=(-10.0, 20.0, -5.0))

    out = resample_to_grid(mask, affine, mask.shape, affine)

    assert out.dtype == bool
    assert np.array_equal(out, mask.astype(bool))


def test_resample_translation_keeps_the_voxel_count():
    mask = np.zeros((8, 9, 7), dtype=bool)
    mask[2:5, 3:6, 1:4] = True
    target_affine = _affine(origin=(2.0, 0.0, 0.0))

    out = resample_to_grid(mask, _affine(), (10, 9, 7), target_affine)

    assert int(out.sum()) == int(mask.sum())
    assert np.array_equal(out[0:3, 3:6, 1:4], mask[2:5, 3:6, 1:4])


def test_resample_to_finer_grid_scales_the_count_by_the_volume_ratio():
    mask = np.zeros((6, 6, 6), dtype=bool)
    mask[2:4, 2:4, 2:4] = True
    source_affine = _affine(scale=(2.0, 2.0, 2.0))

    out = resample_to_grid(mask, source_affine, (12, 12, 12), _affine())

    assert int(out.sum()) == int(mask.sum()) * 8


def test_resample_subvoxel_shift_stays_within_nearest_neighbour_error():
    mask = np.zeros((12, 12, 12), dtype=bool)
    mask[4:8, 4:8, 4:8] = True
    target_affine = _affine(origin=(0.4, 0.0, 0.0))

    out = resample_to_grid(mask, _affine(), mask.shape, target_affine)

    assert abs(int(out.sum()) - int(mask.sum())) <= int(mask.sum()) * 0.1


def test_artifact_is_json_ready_sorted_by_nodule_id_then_slice():
    affine = _affine()
    first = np.zeros((8, 8, 8), dtype=bool)
    first[2, 2, 6] = True
    first[3, 3, 1] = True
    second = np.zeros((8, 8, 8), dtype=bool)
    second[5, 4, 3] = True

    artifact = build_contours(
        [
            {"id": 2, "mask": second, "affine": affine},
            {"id": 1, "mask": first, "affine": affine},
        ],
        (8, 8, 8),
        affine,
    )

    assert artifact["shape"] == [8, 8, 8]
    assert [nodule["id"] for nodule in artifact["nodules"]] == [1, 2]
    assert [entry["slice_index"] for entry in artifact["nodules"][0]["slices"]] == [1, 6]
    assert artifact["nodules"][1]["slices"][0]["slice_index"] == 3
    assert artifact["nodules"][0]["slices"][0]["polygons"] == [
        [[2.5, 3.0], [3.0, 2.5], [3.5, 3.0], [3.0, 3.5]],
    ]
    assert json.loads(json.dumps(artifact)) == artifact


def test_artifact_keeps_an_empty_nodule_with_no_slices():
    affine = _affine()
    empty = np.zeros((6, 6, 6), dtype=bool)

    artifact = build_contours(
        [{"id": 7, "mask": empty, "affine": affine}],
        (6, 6, 6),
        affine,
    )

    assert artifact["nodules"] == [{"id": 7, "slices": []}]


def test_artifact_rejects_a_mask_that_is_not_three_dimensional():
    with np.testing.assert_raises(ValueError):
        build_contours(
            [{"id": 1, "mask": np.ones((4, 4), dtype=bool), "affine": _affine()}],
            (4, 4, 4),
            _affine(),
        )
