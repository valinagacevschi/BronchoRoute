# Changes: fork hold poses, orifice samples, fisheye projection, build_fork_inputs (#56).
"""Camera poses and orifice projections for realistic fork views."""

from __future__ import annotations

import math

import numpy as np

from route_frame import fork_hold_s

from .brea_geometry import brea_geometry_image
from .depth import render_depth

ORIFICE_DISTANCE_MM = 8.0


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
