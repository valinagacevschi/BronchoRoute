"""Pure axial contour builder for nodule masks.

Coordinates are continuous voxel coordinates in the CT array frame: voxel
``(i, j, k)`` has its centre at ``(i, j, k)``.  A polygon point ``[x, y]`` is
therefore a half-voxel boundary point such as ``(3.5, 2.0)``.  Add ``0.5`` per
axis to reach the viewer's continuous corner coordinates.  The slice index is
the integer ``k``.
"""

from __future__ import annotations

import numpy as np

_Point = tuple[float, float]
_Segment = tuple[_Point, _Point]

# Marching-squares corners, clockwise from the top-left of a cell.
_TL, _TR, _BR, _BL = 1, 2, 4, 8

# Each entry pairs edge names to draw a segment between their midpoints.
# Saddle cells (5 and 10) connect the diagonal foreground corners, matching an
# 8-connected mask outline.
_SEGMENTS = {
    1: (("L", "T"),),
    2: (("T", "R"),),
    3: (("L", "R"),),
    4: (("R", "B"),),
    5: (("T", "R"), ("B", "L")),
    6: (("T", "B"),),
    7: (("L", "B"),),
    8: (("B", "L"),),
    9: (("T", "B"),),
    10: (("L", "T"), ("R", "B")),
    11: (("R", "B"),),
    12: (("R", "L"),),
    13: (("T", "R"),),
    14: (("L", "T"),),
    15: (),
}


def resample_to_grid(
    mask: np.ndarray,
    mask_affine: np.ndarray,
    target_shape: tuple[int, int, int],
    target_affine: np.ndarray | None = None,
) -> np.ndarray:
    """Nearest-neighbour resample a nodule mask onto the CT array grid.

    ``target_affine`` defaults to ``mask_affine``, which is correct when the
    mask and the CT share a world origin and only the array shape differs.
    Returns a boolean volume with ``target_shape``.
    """
    source = np.asarray(mask)
    if source.ndim != 3:
        raise ValueError("mask must be 3-D")
    source_affine = np.asarray(mask_affine, dtype=float)
    if source_affine.shape != (4, 4):
        raise ValueError("mask_affine must be 4x4")
    target = source_affine if target_affine is None else np.asarray(target_affine, dtype=float)
    if target.shape != (4, 4):
        raise ValueError("target_affine must be 4x4")
    shape = tuple(int(size) for size in target_shape)
    if len(shape) != 3 or any(size <= 0 for size in shape):
        raise ValueError("target_shape must be three positive integers")

    # Map target array indices straight to source array indices.
    transform = np.linalg.inv(source_affine) @ target
    nx, ny, nz = shape
    xs, ys = np.meshgrid(
        np.arange(nx, dtype=float),
        np.arange(ny, dtype=float),
        indexing="ij",
    )
    output = np.zeros(shape, dtype=bool)
    for k in range(nz):
        corners = np.stack(
            [xs, ys, np.full_like(xs, float(k)), np.ones_like(xs)],
            axis=-1,
        ).reshape(-1, 4)
        source_ijk = np.floor((corners @ transform.T)[:, :3] + 0.5).astype(np.int64)
        inside = np.all(
            (source_ijk >= 0) & (source_ijk < np.asarray(source.shape)),
            axis=1,
        )
        flat = np.zeros(nx * ny, dtype=bool)
        if inside.any():
            picked = source_ijk[inside]
            flat[inside] = source[picked[:, 0], picked[:, 1], picked[:, 2]] > 0
        output[:, :, k] = flat.reshape(nx, ny)
    return output


def build_contours(
    nodules: list[dict],
    target_shape: tuple[int, int, int],
    target_affine: np.ndarray | None = None,
) -> dict:
    """Build the axial contour artifact for a list of nodule masks.

    Each input nodule is ``{"id": int, "mask": ndarray, "affine": 4x4}``.
    Every nodule appears in the output, ordered by id; slices are ascending and
    empty slices are omitted.  The result is JSON ready::

        {
          "shape": [nx, ny, nz],
          "nodules": [
            {"id": 1, "slices": [
              {"slice_index": 42, "polygons": [[[x, y], ...], ...]}
            ]}
          ]
        }

    Points are continuous voxel coordinates in the CT array frame (voxel centre
    at the integer index, boundary at the half-voxel offset).  The z axis is
    the axial slice axis, so ``index`` is the integer ``k`` of that slice.
    """
    shape = tuple(int(size) for size in target_shape)
    if len(shape) != 3 or any(size <= 0 for size in shape):
        raise ValueError("target_shape must be three positive integers")

    records: list[dict] = []
    for nodule in nodules:
        mask = resample_to_grid(
            nodule["mask"], nodule["affine"], shape, target_affine,
        )
        slices = [
            {
                "slice_index": entry["slice_index"],
                "polygons": [
                    [list(point) for point in polygon]
                    for polygon in entry["polygons"]
                ],
            }
            for entry in trace_mask_axial(mask)
        ]
        records.append({"id": nodule["id"], "slices": slices})

    records.sort(key=lambda record: record["id"])
    return {"shape": list(shape), "nodules": records}


def _cell_segments(x: int, y: int, case: int) -> list[_Segment]:
    edges = {
        "T": (x + 0.5, float(y)),
        "R": (float(x + 1), y + 0.5),
        "B": (x + 0.5, float(y + 1)),
        "L": (float(x), y + 0.5),
    }
    return [(edges[a], edges[b]) for a, b in _SEGMENTS[case]]


def _stitch(segments: list[_Segment]) -> list[list[_Point]]:
    neighbours: dict[_Point, list[_Point]] = {}
    for a, b in segments:
        neighbours.setdefault(a, []).append(b)
        neighbours.setdefault(b, []).append(a)
    for points in neighbours.values():
        points.sort()

    used: set[_Segment] = set()
    polygons: list[list[tuple[float, float]]] = []
    for start in sorted(neighbours):
        for first in neighbours[start]:
            edge = (start, first) if start <= first else (first, start)
            if edge in used:
                continue
            polygon = [start]
            current = first
            used.add(edge)
            while current != start:
                polygon.append(current)
                candidates = [
                    point
                    for point in neighbours[current]
                    if (
                        (current, point) if current <= point else (point, current)
                    ) not in used
                ]
                if not candidates:
                    raise RuntimeError("contour tracing produced an open polygon")
                nxt = candidates[0]
                used.add((current, nxt) if current <= nxt else (nxt, current))
                current = nxt
            if len(polygon) >= 4:
                polygons.append(polygon)
    return polygons


def trace_slice(slice_mask: np.ndarray) -> list[list[tuple[float, float]]]:
    """Return closed polygons on the half-voxel boundary of one axial slice."""
    grid = np.asarray(slice_mask).astype(bool)
    if grid.ndim != 2:
        raise ValueError("slice_mask must be 2-D")
    padded = np.pad(grid, 1, mode="constant", constant_values=False)
    height, width = grid.shape
    segments: list[_Segment] = []
    for row in range(height + 1):
        for column in range(width + 1):
            case = (
                (_TL if padded[row, column] else 0)
                | (_TR if padded[row, column + 1] else 0)
                | (_BR if padded[row + 1, column + 1] else 0)
                | (_BL if padded[row + 1, column] else 0)
            )
            if case:
                segments.extend(_cell_segments(column - 1, row - 1, case))
    return _stitch(segments)


def trace_mask_axial(mask: np.ndarray) -> list[dict]:
    """Trace every non-empty axial slice, in ascending slice order.

    Each entry is ``{"slice_index": k, "polygons": [[[x, y], ...], ...]}``.
    """
    volume = np.asarray(mask).astype(bool)
    if volume.ndim != 3:
        raise ValueError("mask must be 3-D")
    slices: list[dict] = []
    for index in range(volume.shape[2]):
        polygons = trace_slice(volume[:, :, index])
        if polygons:
            slices.append({
                "slice_index": index,
                "polygons": polygons,
            })
    return slices
