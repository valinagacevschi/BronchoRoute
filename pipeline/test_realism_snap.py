# Changes: snap_orifices_to_openings unit + local-001 fork printout (#60c).
"""Orifice markers snap onto local-maxima openings in the depth map."""

from __future__ import annotations

import json
import os
from pathlib import Path

import numpy as np
import pytest
from termcolor import cprint

import repo_root

repo_root.ensure()

from app.realism.poses import (  # noqa: E402
    ORIFICE_DIR_DISTANCE_MM,
    _point_along,
    fisheye_project,
    fork_poses,
    orifice_points,
    snap_orifices_to_openings,
)

pytestmark = pytest.mark.server

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "realism"


def _ref_roots():
    ref = os.environ.get("REALISM_REF_ROOT")
    case = os.environ.get("REALISM_CASE_ROOT")
    if not ref or not case:
        return None
    return Path(ref).expanduser(), Path(case).expanduser()


def _orifice_row(name, point8, point20, position, forward, up, **extra):
    u, v, inside = fisheye_project(point8, position, forward, up)
    u_dir, v_dir, dir_inside = fisheye_project(point20, position, forward, up)
    if not dir_inside or u_dir != u_dir or v_dir != v_dir:
        u_dir, v_dir = u, v
    return {
        "name": name,
        "u": u,
        "v": v,
        "inside": inside,
        "u_dir": u_dir,
        "v_dir": v_dir,
        **extra,
    }


@pytest.mark.skipif(
    not (FIXTURES / "synthetic_y.npz").is_file(),
    reason="reference arrays are not in the public snapshot",
)
def test_synthetic_y_both_children_snap_left_right():
    cprint("synthetic Y snap left/right openings", "cyan")
    ref = np.load(FIXTURES / "synthetic_y.npz")
    depth = ref["depth"]
    valid = ref["valid"].astype(bool)
    position = ref["position"]
    forward = ref["forward"]
    up = ref["up"]
    # Trunk along −z from the hold; branches toward ±x (image right = +x).
    left = _orifice_row(
        "left",
        [14.0, 20.0, 47.0],
        [10.0, 20.0, 40.0],
        position,
        forward,
        up,
        edge=0,
        on_route=False,
    )
    right = _orifice_row(
        "right",
        [26.0, 20.0, 47.0],
        [30.0, 20.0, 40.0],
        position,
        forward,
        up,
        edge=1,
        on_route=True,
    )
    snapped = snap_orifices_to_openings(depth, valid, [left, right])
    assert all(row["snapped"] for row in snapped), snapped
    assert snapped[0]["u"] < 128 < snapped[1]["u"], snapped
    assert snapped[0]["name"] == "left" and snapped[1]["name"] == "right"
    q80 = float(np.percentile(depth[valid], 80))
    assert snapped[0]["u_proj"] == pytest.approx(left["u"])
    assert snapped[0]["v_proj"] == pytest.approx(left["v"])
    assert snapped[1]["u_proj"] == pytest.approx(right["u"])
    assert snapped[1]["v_proj"] == pytest.approx(right["v"])
    for row in snapped:
        u = int(round(row["u"]))
        v = int(round(row["v"]))
        assert depth[v, u] >= q80, (row, depth[v, u], q80)
        assert "u_dir" not in row and "v_dir" not in row


def test_one_to_one_three_children_two_candidates():
    cprint("one-to-one: 3 children, 2 candidates", "cyan")
    size = 256
    yy, xx = np.mgrid[:size, :size]
    depth = np.full((size, size), 5.0, dtype=np.float64)
    for vc, uc, amp in [(100, 80, 40.0), (100, 180, 40.0)]:
        depth += amp * np.exp(-((yy - vc) ** 2 + (xx - uc) ** 2) / (2 * 12.0 ** 2))
    valid = np.ones((size, size), dtype=bool)

    orifices = [
        {"name": "a", "u": 60.0, "v": 100.0, "inside": True, "u_dir": 60.0, "v_dir": 100.0},
        {"name": "b", "u": 200.0, "v": 100.0, "inside": True, "u_dir": 200.0, "v_dir": 100.0},
        {"name": "c", "u": 128.0, "v": 210.0, "inside": True, "u_dir": 128.0, "v_dir": 210.0},
    ]
    snapped = snap_orifices_to_openings(depth, valid, orifices)
    assert sum(1 for row in snapped if row["snapped"]) == 2, snapped
    assert sum(1 for row in snapped if not row["snapped"]) == 1, snapped


def test_flat_depth_no_candidates_unchanged():
    cprint("flat depth → no snap", "cyan")
    size = 256
    depth = np.full((size, size), 10.0, dtype=np.float64)
    valid = np.ones((size, size), dtype=bool)
    orifices = [
        {"name": "a", "u": 90.0, "v": 110.0, "inside": True, "u_dir": 90.0, "v_dir": 110.0},
        {"name": "b", "u": 160.0, "v": 140.0, "inside": True, "u_dir": 160.0, "v_dir": 140.0},
    ]
    snapped = snap_orifices_to_openings(depth, valid, orifices)
    assert all(not row["snapped"] for row in snapped)
    for src, row in zip(orifices, snapped):
        assert row["u"] == src["u"] and row["v"] == src["v"]
        assert row["u_proj"] == src["u"] and row["v_proj"] == src["v"]
        assert "u_dir" not in row and "v_dir" not in row


@pytest.mark.skipif(
    _ref_roots() is None,
    reason="set REALISM_REF_ROOT and REALISM_CASE_ROOT for local-001 refs",
)
def test_local_001_fork_snaps_printout():
    ref_root, case_root = _ref_roots()
    cprint(f"local-001 orifice snap under {ref_root}", "cyan")
    import nibabel as nib
    from make_prototype_data import build_payload

    refs = json.loads((ref_root / "refs.json").read_text())
    airways_path = case_root / "ts_out" / "local-001" / "airways.nii.gz"
    graph_path = case_root / "out" / "local-001" / "local-001_graph.json"
    case_path = case_root / "data" / "extracted" / "local" / "local-001" / "case.json"
    graph = json.loads(graph_path.read_text())
    case_record = json.loads(case_path.read_text())
    payload = build_payload(graph, case_record, True, None)
    origin = np.asarray(payload["origin_mm"], dtype=float)

    def rel(point):
        return [float(point[i]) - float(origin[i]) for i in range(3)]

    edges_full = [
        {**edge, "centreline_mm": [rel(p) for p in edge["centreline_mm"]]}
        for edge in graph["edges"]
    ]
    poses = fork_poses(payload)
    fork_rows = [row for row in refs["rows"] if row["name"].startswith("local-001_fork")]
    assert len(poses) == len(fork_rows)

    print("\nlocal-001 snapped vs proj orifices:")
    fork1_snapped = None
    for pose, row, fork in zip(poses, fork_rows, payload["forks"]):
        ref = np.load(ref_root / f"{row['name']}.npz")
        depth = ref["depth"]
        valid = ref["valid"].astype(bool)
        position = np.asarray(pose["position_ras_mm"], dtype=float)
        forward = np.asarray(pose["forward"], dtype=float)
        up = np.asarray(pose["up"], dtype=float)
        orifices = []
        for child, sample in zip(
            fork["children"],
            orifice_points(payload, fork, edges_full),
            strict=True,
        ):
            u, v, inside = fisheye_project(
                sample["point_ras_mm"], position, forward, up
            )
            edge = edges_full[child["edge"]]
            centreline = edge.get("centreline_mm", edge.get("c"))
            point_dir = _point_along(centreline, ORIFICE_DIR_DISTANCE_MM)
            point_dir_ras = (np.asarray(point_dir, dtype=float) + origin).tolist()
            u_dir, v_dir, dir_inside = fisheye_project(
                point_dir_ras, position, forward, up
            )
            if not dir_inside or u_dir != u_dir or v_dir != v_dir:
                u_dir, v_dir = u, v
            orifices.append({
                "fork": fork["fork"],
                "edge": sample["edge"],
                "on_route": bool(sample["on_route"]),
                "name": child.get("name"),
                "u": u,
                "v": v,
                "inside": inside,
                "u_dir": u_dir,
                "v_dir": v_dir,
            })
        snapped = snap_orifices_to_openings(depth, valid, orifices)
        if fork["fork"] == 1:
            fork1_snapped = snapped
        print(f"  fork {fork['fork']} hold_s={pose['hold_s']:.2f}")
        for item in snapped:
            print(
                f"    edge={item['edge']} on_route={item['on_route']} "
                f"name={item.get('name')} snapped={item['snapped']} "
                f"u={item['u']:.1f} v={item['v']:.1f} "
                f"u_proj={item['u_proj']:.1f} v_proj={item['v_proj']:.1f}"
            )

    assert fork1_snapped is not None
    on_route = next(o for o in fork1_snapped if o["on_route"])
    off_route = next(o for o in fork1_snapped if not o["on_route"])
    assert on_route["name"] == "right main bronchus"
    assert off_route["name"] == "left main bronchus"
    assert on_route["snapped"] and off_route["snapped"]
    assert on_route["u"] > 128, on_route
    assert off_route["u"] < 128, off_route
    # airways present (sanity that case root is the real tree)
    assert airways_path.is_file()
    _ = nib.load(str(airways_path))
