# Changes: snap_orifices_to_openings — local-max depth anchors + greedy angle snap (#60c).
"""Camera poses and orifice projections for realistic fork views."""

from __future__ import annotations

import math

import numpy as np
from scipy.ndimage import gaussian_filter, maximum_filter

from route_frame import fork_hold_s

from .brea_geometry import brea_geometry_image
from .depth import render_depth

ORIFICE_DISTANCE_MM = 8.0
ORIFICE_DIR_DISTANCE_MM = 20.0
_OPENING_GAUSS_SIGMA = 3.0
_OPENING_MAX_FILTER = 21
_OPENING_MIN_SEPARATION_PX = 12.0
_OPENING_DEPTH_RANGE_MIN_MM = 1.0
_SNAP_COST_MAX = 1.0
_SNAP_RADIUS_WEIGHT = 0.5


def _unit(vector):
    vector = np.asarray(vector, dtype=float)
    norm = float(np.linalg.norm(vector))
    if norm < 1e-12:
        raise ValueError("zero-length vector")
    return vector / norm


def _point_along(centreline, distance_mm=ORIFICE_DISTANCE_MM):
    """Sample a centreline point at arc length, clamped like direction_along."""
    points = np.asarray(centreline, dtype=float)
    if len(points) < 2:
        return points[0].copy() if len(points) else np.zeros(3)
    lengths = np.r_[0.0, np.cumsum(np.linalg.norm(np.diff(points, axis=0), axis=1))]
    if float(lengths[-1]) < 1e-9:
        return points[0].copy()
    distance = min(float(distance_mm), float(lengths[-1]) / 2.0)
    return np.array([
        np.interp(distance, lengths, points[:, axis])
        for axis in range(3)
    ])


def fork_poses(payload) -> list[dict]:
    """One zero-roll hold pose per route fork, camera at fork_hold_s."""
    fly = payload["fly"]
    s = np.asarray(fly["s"], dtype=float)
    positions = np.asarray(fly["p"], dtype=float)
    forwards = np.asarray(fly["t"], dtype=float)
    ups = np.asarray(fly["up"], dtype=float)
    origin = np.asarray(payload["origin_mm"], dtype=float)
    poses = []
    prev_fork_s = None
    for fork in payload["forks"]:
        hold_s = float(fork_hold_s(fork["s"], prev_fork_s))
        index = int(np.argmin(np.abs(s - hold_s)))
        poses.append({
            "fork": fork.get("fork"),
            "node": fork.get("node"),
            "fork_s": float(fork["s"]),
            "hold_s": hold_s,
            "position_ras_mm": (positions[index] + origin).tolist(),
            "forward": forwards[index].tolist(),
            "up": ups[index].tolist(),
        })
        prev_fork_s = fork["s"]
    return poses


def orifice_points(payload, fork, edges_full) -> list[dict]:
    """World-RAS points 8 mm down each child centreline (full_edges / relative)."""
    origin = np.asarray(payload["origin_mm"], dtype=float)
    rows = []
    for child in fork["children"]:
        edge = edges_full[child["edge"]]
        centreline = edge.get("centreline_mm", edge.get("c"))
        point = _point_along(centreline, ORIFICE_DISTANCE_MM)
        rows.append({
            "edge": child["edge"],
            "on_route": bool(child.get("on_route")),
            "point_ras_mm": (np.asarray(point, dtype=float) + origin).tolist(),
        })
    return rows


def _pixel_inside(u, v, size=256) -> bool:
    if u != u or v != v:
        return False
    xx = (u + 0.5) * 2.0 / size - 1.0
    yy = 1.0 - (v + 0.5) * 2.0 / size
    return bool(math.hypot(xx, yy) <= 1.0)


def _polar_about_centre(u, v, centre):
    return math.atan2(v - centre, u - centre), math.hypot(u - centre, v - centre)


def _opening_anchors(depth, valid, *, size=256) -> list[tuple[float, float]]:
    """Local-maxima openings in the depth map → (u, v) candidate pixels."""
    depth = np.asarray(depth, dtype=float)
    valid = np.asarray(valid, dtype=bool)
    if depth.shape != (size, size) or valid.shape != (size, size):
        raise ValueError(f"depth/valid must be ({size}, {size})")
    if not bool(valid.any()):
        return []
    dvals = depth[valid]
    if float(dvals.max() - dvals.min()) < _OPENING_DEPTH_RANGE_MIN_MM:
        return []
    q = float(np.percentile(dvals, 80))
    filled = np.where(valid, depth, 0.0)
    smooth = gaussian_filter(filled, sigma=_OPENING_GAUSS_SIGMA)
    peaks = (
        (smooth == maximum_filter(smooth, size=_OPENING_MAX_FILTER))
        & valid
        & (depth >= q)
    )
    ys, xs = np.where(peaks)
    ranked = [
        (float(smooth[int(y), int(x)]), float(x), float(y))
        for y, x in zip(ys, xs)
    ]
    ranked.sort(reverse=True)
    kept: list[tuple[float, float]] = []
    min_sep2 = _OPENING_MIN_SEPARATION_PX ** 2
    for _s, u, v in ranked:
        if any((u - ku) ** 2 + (v - kv) ** 2 < min_sep2 for ku, kv in kept):
            continue
        kept.append((u, v))
    return kept


def snap_orifices_to_openings(depth, valid, orifices, *, size=256) -> list[dict]:
    """Snap projected orifice markers onto local-max depth openings.

    Matching uses optional ``u_dir``/``v_dir`` (20 mm child direction); otherwise
    ``u``/``v``. Output keeps existing fields, replaces ``u``/``v``, recomputes
    ``inside``, and adds ``snapped``, ``u_proj``, ``v_proj``. Drops ``u_dir``/
    ``v_dir`` from the result.
    """
    anchors = _opening_anchors(depth, valid, size=size)
    centre = (size - 1) / 2.0
    half = size / 2.0

    def strip_dir(row: dict) -> dict:
        return {k: v for k, v in row.items() if k not in ("u_dir", "v_dir")}

    if not anchors:
        out = []
        for row in orifices:
            u_proj = float(row["u"])
            v_proj = float(row["v"])
            item = strip_dir(row)
            item["u"] = u_proj
            item["v"] = v_proj
            item["u_proj"] = u_proj
            item["v_proj"] = v_proj
            item["snapped"] = False
            item["inside"] = _pixel_inside(u_proj, v_proj, size)
            out.append(item)
        return out

    pairs: list[tuple[float, int, int]] = []
    for child_i, row in enumerate(orifices):
        u_proj = float(row["u"])
        v_proj = float(row["v"])
        if "u_dir" in row and "v_dir" in row:
            u_dir = float(row["u_dir"])
            v_dir = float(row["v_dir"])
        else:
            u_dir, v_dir = u_proj, v_proj
        if u_dir != u_dir or v_dir != v_dir:
            continue
        child_angle, child_radius = _polar_about_centre(u_dir, v_dir, centre)
        child_radius_norm = child_radius / half
        for anchor_i, (au, av) in enumerate(anchors):
            anchor_angle, anchor_radius = _polar_about_centre(au, av, centre)
            delta_angle = abs(child_angle - anchor_angle)
            delta_angle = min(delta_angle, 2.0 * math.pi - delta_angle)
            delta_radius = abs(child_radius_norm - anchor_radius / half)
            cost = delta_angle + _SNAP_RADIUS_WEIGHT * delta_radius
            pairs.append((cost, child_i, anchor_i))
    pairs.sort()

    assigned_child: set[int] = set()
    assigned_anchor: set[int] = set()
    snap_at: dict[int, tuple[float, float]] = {}
    for cost, child_i, anchor_i in pairs:
        if cost >= _SNAP_COST_MAX:
            break
        if child_i in assigned_child or anchor_i in assigned_anchor:
            continue
        assigned_child.add(child_i)
        assigned_anchor.add(anchor_i)
        snap_at[child_i] = anchors[anchor_i]

    out = []
    for child_i, row in enumerate(orifices):
        u_proj = float(row["u"])
        v_proj = float(row["v"])
        item = strip_dir(row)
        if child_i in snap_at:
            u, v = snap_at[child_i]
            item["u"] = float(u)
            item["v"] = float(v)
            item["snapped"] = True
        else:
            item["u"] = u_proj
            item["v"] = v_proj
            item["snapped"] = False
        item["u_proj"] = u_proj
        item["v_proj"] = v_proj
        item["inside"] = _pixel_inside(item["u"], item["v"], size)
        out.append(item)
    return out


def fisheye_project(point, position, forward, up, size=256, fov=120):
    """Inverse of `_render_depth` equidistant rays → pixel (u, v) and inside flag."""
    point = np.asarray(point, dtype=float)
    position = np.asarray(position, dtype=float)
    forward = _unit(forward)
    up = _unit(up)
    offset = point - position
    distance = float(np.linalg.norm(offset))
    if distance < 1e-12:
        centre = (size - 1) / 2.0
        return centre, centre, True
    direction = offset / distance
    ahead = float(np.dot(direction, forward))
    if ahead <= 0.0:
        return float("nan"), float("nan"), False
    theta = math.acos(float(np.clip(ahead, -1.0, 1.0)))
    half = math.radians(float(fov)) / 2.0
    radius = theta / half if half > 0 else float("inf")
    right = np.cross(forward, up)
    right = right / np.linalg.norm(right)
    # Match render: up is already the camera up used for yy; re-orthogonalise.
    up = up - np.dot(up, forward) * forward
    up = up / np.linalg.norm(up)
    right = np.cross(forward, up)
    right = right / np.linalg.norm(right)
    if theta < 1e-12:
        xx = 0.0
        yy = 0.0
    else:
        radial = direction - ahead * forward
        radial_norm = float(np.linalg.norm(radial))
        if radial_norm < 1e-12:
            xx = 0.0
            yy = 0.0
        else:
            radial_hat = radial / radial_norm
            xx = radius * float(np.dot(radial_hat, right))
            yy = radius * float(np.dot(radial_hat, up))
    # axis = (i + 0.5) / size * 2 - 1; yy, xx = meshgrid(-axis, axis)
    u = (xx + 1.0) * size / 2.0 - 0.5
    v = (1.0 - yy) * size / 2.0 - 0.5
    return float(u), float(v), bool(radius <= 1.0)


def build_fork_inputs(case_mask, affine, payload, edges_full) -> list[dict]:
    """Per-fork pose, depth, BREA encoding, and projected orifices."""
    results = []
    poses = fork_poses(payload)
    for fork, pose in zip(payload["forks"], poses):
        position = np.asarray(pose["position_ras_mm"], dtype=float)
        forward = np.asarray(pose["forward"], dtype=float)
        up = np.asarray(pose["up"], dtype=float)
        depth, valid = render_depth(case_mask, affine, position, forward, up)
        encoded = brea_geometry_image(depth, valid, size=256)
        orifices = []
        all_inside = True
        for row in orifice_points(payload, fork, edges_full):
            u, v, inside = fisheye_project(
                row["point_ras_mm"], position, forward, up
            )
            radius = None
            if u == u and v == v:  # not NaN
                xx = (u + 0.5) * 2.0 / 256.0 - 1.0
                yy = 1.0 - (v + 0.5) * 2.0 / 256.0
                radius = float(math.hypot(xx, yy))
            if not inside:
                all_inside = False
            orifices.append({
                **row,
                "u": u,
                "v": v,
                "inside": inside,
                "radius": radius,
            })
        results.append({
            "pose": pose,
            "depth": depth,
            "valid": valid,
            "encoded": encoded,
            "orifices": orifices,
            "all_children_inside": all_inside,
        })
    return results
