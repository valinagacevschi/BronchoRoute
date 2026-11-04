# Changes: exact-match tests for depth/BREA port and fork poses (#56).
"""Port parity for realistic fork depth + BREA encoder + hold poses."""

from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path

import numpy as np
import pytest
from termcolor import cprint

import repo_root

repo_root.ensure()

from app.realism.brea_geometry import (  # noqa: E402
    DEFAULT_PROFILE,
    brea_geometry_image,
    load_profile,
)
from app.realism.depth import render_depth  # noqa: E402
from app.realism.poses import (  # noqa: E402
    build_fork_inputs,
    fisheye_project,
    fork_poses,
)

pytestmark = pytest.mark.server

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "realism"
PROFILE_SHA256 = "436620e6a63e0f5b162add85c9f5f897537aa9bf993ae60e1dea806285940284"


def _ref_roots():
    ref = os.environ.get("REALISM_REF_ROOT")
    case = os.environ.get("REALISM_CASE_ROOT")
    if not ref or not case:
        return None
    return Path(ref).expanduser(), Path(case).expanduser()


def test_profile_sha256_matches_module():
    cprint("profile sha256", "cyan")
    # PROFILE_SHA256 is the BronchoNavigation module's brea-depth-profile.json.
    bundled = hashlib.sha256(DEFAULT_PROFILE.read_bytes()).hexdigest()
    assert bundled == PROFILE_SHA256


def test_synthetic_depth_and_encoder_match_reference():
    cprint("synthetic_y reference match", "cyan")
    mask_npz = np.load(FIXTURES / "synthetic_mask.npz")
    ref = np.load(FIXTURES / "synthetic_y.npz")
    mask = mask_npz["mask"]
    affine = mask_npz["affine"]
    depth, valid = render_depth(
        mask,
        affine,
        ref["position"],
        ref["forward"],
        ref["up"],
    )
    encoded = brea_geometry_image(depth, valid, size=256, profile=load_profile())
    assert np.allclose(depth, ref["depth"], atol=1e-4)
    assert np.array_equal(valid, ref["valid"])
    assert np.array_equal(encoded, ref["encoded"])


def test_fisheye_project_is_inverse_of_render_rays():
    cprint("fisheye_project round-trip", "cyan")
    size, fov = 256, 120
    forward = np.array([0.14, -0.22, -0.96], dtype=float)
    forward /= np.linalg.norm(forward)
    up = np.array([-0.04, 0.97, -0.24], dtype=float)
    up = up - np.dot(up, forward) * forward
    up /= np.linalg.norm(up)
    position = np.array([3.0, -1.5, 12.0], dtype=float)

    axis = (np.arange(size) + 0.5) / size * 2 - 1
    yy, xx = np.meshgrid(-axis, axis, indexing="ij")
    radius = np.hypot(xx, yy)
    right = np.cross(forward, up)
    right /= np.linalg.norm(right)
    theta = radius * np.deg2rad(fov) / 2
    radial = (xx[..., None] * right + yy[..., None] * up) / np.maximum(
        radius[..., None], 1e-12
    )
    directions = np.cos(theta)[..., None] * forward + np.sin(theta)[..., None] * radial
    directions /= np.linalg.norm(directions, axis=-1, keepdims=True)

    rng = np.random.default_rng(7)
    checked = 0
    while checked < 20:
        row = int(rng.integers(0, size))
        col = int(rng.integers(0, size))
        if radius[row, col] > 1:
            continue
        point = position + 35.0 * directions[row, col]
        u, v, inside = fisheye_project(
            point, position, forward, up, size=size, fov=fov
        )
        assert inside
        assert math.hypot(u - col, v - row) <= 0.5
        checked += 1

    _, _, behind_inside = fisheye_project(
        position - 10.0 * forward, position, forward, up, size=size, fov=fov
    )
    assert behind_inside is False


@pytest.mark.skipif(
    _ref_roots() is None,
    reason="set REALISM_REF_ROOT and REALISM_CASE_ROOT for local-001 refs",
)
def test_local_001_fork_refs_and_poses():
    ref_root, case_root = _ref_roots()
    cprint(f"local-001 refs under {ref_root}", "cyan")
    import nibabel as nib
    from make_prototype_data import build_payload

    refs = json.loads((ref_root / "refs.json").read_text())
    airways_path = case_root / "ts_out" / "local-001" / "airways.nii.gz"
    graph_path = case_root / "out" / "local-001" / "local-001_graph.json"
    case_path = case_root / "data" / "extracted" / "local" / "local-001" / "case.json"
    assert airways_path.is_file(), airways_path
    assert hashlib.sha256(airways_path.read_bytes()).hexdigest() == refs["airways_sha256"]

    img = nib.load(str(airways_path))
    mask = (np.asanyarray(img.dataobj) > 0).astype(np.uint8)
    affine = img.affine
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

    profile = load_profile()
    table = []
    for pose, row in zip(poses, fork_rows):
        ref = np.load(ref_root / f"{row['name']}.npz")
        assert pose["hold_s"] == pytest.approx(row["hold_s"])
        assert pose["fork_s"] == pytest.approx(row["fork_s"])
        assert np.allclose(
            pose["position_ras_mm"], ref["position"], atol=0.05
        ), (pose["position_ras_mm"], ref["position"])

        depth, valid = render_depth(
            mask, affine, ref["position"], ref["forward"], ref["up"]
        )
        encoded = brea_geometry_image(depth, valid, size=256, profile=profile)
        assert np.allclose(depth, ref["depth"], atol=1e-4)
        assert np.array_equal(valid, ref["valid"])
        assert np.array_equal(encoded, ref["encoded"])

    inputs = build_fork_inputs(mask, affine, payload, edges_full)
    print("\nlocal-001 all_children_inside:")
    for fork, item in zip(payload["forks"], inputs):
        misses = [
            {
                "edge": o["edge"],
                "on_route": o["on_route"],
                "radius": o["radius"],
                "u": o["u"],
                "v": o["v"],
            }
            for o in item["orifices"]
            if not o["inside"]
        ]
        line = (
            f"  fork {fork['fork']} s={fork['s']} hold={item['pose']['hold_s']} "
            f"all_children_inside={item['all_children_inside']}"
        )
        if misses:
            line += f" misses={misses}"
        print(line)
        table.append((fork["fork"], item["all_children_inside"], misses))
    cprint(
        "all_children_inside summary: "
        + ", ".join(
            f"{n}:{'Y' if ok else 'N'}" for n, ok, _ in table
        ),
        "yellow",
    )
