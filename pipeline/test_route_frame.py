# Changes: angle tests for the manual endoluminal clocks.
import math
from pathlib import Path

import numpy as np

from route_frame import (
    AP_DEGENERACY,
    apply_roll,
    branch_angle,
    clock_hour,
    clock_of,
    direction_along,
    fork_clock_line,
    forks,
    in_plane_fraction,
    patient_angle,
    patient_frame,
    pause_points,
    resample,
    roll_profile,
    rotate,
    rotate_hint,
    scope_angle,
    tangents,
    transport_frame,
    wrap_deg,
)


def test_straight_caudad_keeps_anterior_up():
    p, s, _ = resample([[0, 0, 0], [0, 0, -50]])
    t = tangents(p); up = transport_frame(t)
    assert np.allclose(up, [0, 1, 0], atol=1e-6)
    assert np.isfinite(up).all()


def test_fork_clock_and_roll_sign():
    t = np.array([0., 0., -1.]); up = np.array([0., 1., 0.])
    right = np.cross(t, up)
    d_right = np.array([1., 0., -1.]); d_left = np.array([-1., 0., -1.])
    c, a = clock_of(d_right, t, up); sibling, _ = clock_of(d_left, t, up)
    assert (c, sibling) == (3, 9)
    projected = d_right - np.dot(d_right, t)*t
    assert np.dot(rotate(up, t, np.radians(a)), projected/np.linalg.norm(projected)) > .95


def test_forks_reads_child_geometry_and_marks_route():
    edges = [
        {"from": 0, "to": 1, "centreline_mm": [[0, 0, 0], [0, 0, -50]], "radius_mm": 8, "generation": 0},
        {"from": 1, "to": 2, "centreline_mm": [[0, 0, -50], [5, 0, -55]], "radius_mm": 4, "generation": 1, "airway_name": "right"},
        {"from": 1, "to": 3, "centreline_mm": [[0, 0, -50], [-5, 0, -55]], "radius_mm": 4, "generation": 1, "airway_name": "left"},
    ]
    path = [[0, 0, -z] for z in range(0, 51)] + [[x, 0, -50-x] for x in range(1, 11)]
    p, s, _ = resample(path)
    t = tangents(p); up = transport_frame(t)
    result = forks(edges, [0, 1], p, s, t, up)
    assert len(result) == 1
    assert [(c["clock"], c["on_route"]) for c in result[0]["children"]] == [(3, True), (9, False)]


def test_transport_on_helix_is_orthonormal():
    x = np.linspace(0, 5*np.pi, 400)
    p = np.c_[20*np.cos(x), 20*np.sin(x), -2*x]
    p, _, _ = resample(p)
    t = tangents(p); up = transport_frame(t)
    assert np.allclose(np.linalg.norm(up, axis=1), 1, atol=1e-6)
    assert np.max(np.abs(np.sum(t*up, axis=1))) < 1e-6
    right = np.cross(t, up)
    twist = [abs(np.arctan2(np.dot(np.cross(up[i], up[i+1]), t[i]), np.dot(up[i], up[i+1]))) for i in range(len(t)-1)]
    assert np.median(twist) < .02


def test_roll_profile_wrap_ramp_and_lock():
    s = np.arange(0., 41.)
    f = [{"s": 20., "angle": 10.}, {"s": 30., "angle": 350.}]
    r = roll_profile(s, f, 10, 3)
    assert np.all(r[:11] == 0)
    assert np.all(np.diff(r[10:18]) >= -1e-8)
    assert np.isclose(r[17], np.radians(10), atol=.03)
    assert np.all(np.diff(r[20:28]) <= 1e-8)
    assert np.isclose(r[27], np.radians(-10), atol=.03)
    assert np.isclose(r[30], np.radians(-10), atol=.03)
    assert pause_points(f) == [15., 25.]


def test_overlapping_roll_ramps_start_at_previous_end():
    s = np.arange(0., 31.)
    profile = roll_profile(
        s,
        [{"s": 20., "angle": 90.}, {"s": 24., "angle": 180.}],
        roll_start_mm=10.0,
        roll_end_mm=3.0,
    )
    assert np.isclose(profile[17], np.radians(90))
    assert np.all(np.diff(profile[17:22]) >= -1e-12)
    assert np.isclose(profile[21], np.radians(180))
    assert np.max(np.abs(np.diff(profile[17:22]))) <= np.radians(90)


def test_parallel_daughter_is_straight_ahead():
    t = np.array([0., 0., -1.]); up = np.array([0., 1., 0.])
    assert clock_of(t, t, up) == (None, None)
    assert roll_profile(np.arange(20.), [{"s": 10., "angle": None}]).max() == 0


def test_patient_clock_anterior_is_12_and_right_is_3():
    tangent = np.array([0.0, 0.0, -1.0])
    frame = patient_frame(tangent)
    assert frame["degenerate"] is False
    assert np.allclose(frame["upA"], [0.0, 1.0, 0.0], atol=1e-9)
    assert np.allclose(frame["rightA"], [1.0, 0.0, 0.0], atol=1e-9)
    assert clock_hour(patient_angle(tangent, [0.0, 1.0, 0.0])) == 12
    assert clock_hour(patient_angle(tangent, [1.0, 0.0, 0.0])) == 3
    assert clock_hour(patient_angle(tangent, [-1.0, 0.0, 0.0])) == 9
    assert clock_hour(patient_angle(tangent, [0.0, -1.0, 0.0])) == 6
    assert clock_hour(math.radians(15)) == 1
    assert in_plane_fraction(tangent, [0.0, 1.0, 0.0]) > 0.15


def test_roll_plus_90_puts_needle_at_3_and_shifts_scope_branch_by_minus_90():
    tangent = np.array([0.0, 0.0, -1.0])
    frame = patient_frame(tangent)
    up = frame["upA"]
    right = frame["rightA"]
    direction = np.array([1.0, 0.0, 0.0])
    theta = patient_angle(tangent, direction)
    psi0 = scope_angle(tangent, up)
    assert clock_hour(theta) == 3
    assert np.isclose(psi0, 0.0, atol=1e-9)
    rolled = apply_roll(up, right, math.pi / 2)
    psi = scope_angle(tangent, rolled)
    assert clock_hour(psi) == 3
    assert np.isclose(math.degrees(psi), 90.0, atol=1e-6)
    scope0 = wrap_deg(math.degrees(theta - psi0))
    scope1 = wrap_deg(math.degrees(theta - psi))
    assert np.isclose(wrap_deg(scope1 - scope0), -90.0, atol=1e-6)
    assert fork_clock_line(1, 12, theta, psi) == (
        "Fork 1 · 12 mm — patient 3 o'clock · scope 12 o'clock"
    )


def test_rotate_hint_wraps_at_180_and_aligns_through_15():
    assert wrap_deg(180) == 180
    assert wrap_deg(-180) == 180
    assert wrap_deg(181) == -179
    assert wrap_deg(-181) == 179
    assert rotate_hint(15) == "aligned ✓"
    assert rotate_hint(-15) == "aligned ✓"
    assert rotate_hint(16) == "rotate 16° ↻"
    assert rotate_hint(-16) == "rotate 16° ↺"
    assert rotate_hint(180) == "rotate 180° ↻"
    assert rotate_hint(-180) == "rotate 180° ↻"
    assert rotate_hint(181) == "rotate 179° ↺"
    assert rotate_hint(-181) == "rotate 179° ↻"
    assert branch_angle([0.0, 0.0, -1.0], [0.0, 0.0, -1.0]) is None
    assert fork_clock_line(2, 4, None, 0.0, ahead=True) == (
        "Fork 2 · 4 mm — straight ahead"
    )


def test_ap_axis_degeneracy_is_strictly_under_a_tenth():
    assert patient_frame([0.0, 1.0, 0.0])["degenerate"] is True
    assert patient_angle([0.0, 1.0, 0.0], [1.0, 0.0, 0.0]) is None
    nearly = np.array([0.0, 1.0, 0.02])
    nearly = nearly / np.linalg.norm(nearly)
    assert patient_frame(nearly)["degenerate"] is True
    assert fork_clock_line(1, 12, None, None) == (
        "Fork 1 · 12 mm — degenerate AP axis"
    )
    lateral = AP_DEGENERACY
    axial = math.sqrt(1.0 - lateral * lateral)
    on_edge = patient_frame([0.0, axial, lateral])
    assert abs(on_edge["length"] - AP_DEGENERACY) < 1e-9
    assert on_edge["degenerate"] is False
    inside = 0.099
    assert patient_frame([0.0, math.sqrt(1.0 - inside * inside), inside])["degenerate"] is True


def test_local_001_carina_and_rul_takeoff_clocks():
    root = Path(__file__).resolve().parent
    graph_path = root / "out/local-001/local-001_graph.json"
    case_path = root / "data/extracted/local/local-001/case.json"
    if not graph_path.is_file() or not case_path.is_file():
        import pytest
        pytest.skip("local-001 graph is not in this checkout")
    import json

    from make_prototype_data import build_payload
    from termcolor import cprint

    graph = json.loads(graph_path.read_text())
    case_record = json.loads(case_path.read_text())
    payload = build_payload(graph, case_record, True)
    fly = payload["fly"]
    step = float(fly["step_mm"])

    def tangent_at(distance):
        sample = max(0.0, min(len(fly["s"]) - 1, float(distance) / step))
        index = int(math.floor(sample))
        tangent = np.asarray(fly["t"][index], dtype=float)
        return tangent / np.linalg.norm(tangent)

    def child_facts(fork):
        tangent = tangent_at(fork["s"])
        facts = []
        for child in fork["children"]:
            direction = direction_along(payload["edges"][child["edge"]]["c"])
            theta = patient_angle(tangent, direction)
            facts.append({
                "name": child["name"],
                "clock": clock_hour(branch_angle(tangent, direction)),
                "ahead": branch_angle(tangent, direction) is None
                and not patient_frame(tangent)["degenerate"],
                "in_plane": in_plane_fraction(tangent, direction),
                "x": float(direction[0]),
            })
        return facts

    carina = next(
        fork for fork in payload["forks"]
        if {child["name"] for child in fork["children"]} >= {
            "right main bronchus", "left main bronchus",
        }
    )
    carina_facts = {row["name"]: row for row in child_facts(carina)}
    right = carina_facts["right main bronchus"]
    left = carina_facts["left main bronchus"]
    assert right["x"] > 0
    assert left["x"] < 0
    assert right["clock"] == 3
    assert left["clock"] == 9
    rul = next(
        fork for fork in payload["forks"]
        if any("intermedius" in (child["name"] or "") for child in fork["children"])
        and any("upper" in (child["name"] or "") for child in fork["children"])
    )
    rul_facts = {row["name"]: row for row in child_facts(rul)}
    intermedius = next(row for name, row in rul_facts.items() if "intermedius" in name)
    upper = next(row for name, row in rul_facts.items() if "upper" in name)
    assert intermedius["ahead"] is True
    assert intermedius["in_plane"] < 0.15
    assert upper["in_plane"] > 0.5
    assert upper["clock"] in {2, 3, 4}
    cprint(
        f"local-001 fork {carina['fork']}: right main patient {right['clock']}, "
        f"left main patient {left['clock']}",
        "cyan",
    )
    cprint(
        f"local-001 RUL takeoff: bronchus intermedius straight ahead "
        f"(in-plane {intermedius['in_plane']:.3f}); "
        f"right upper lobe bronchus patient {upper['clock']} "
        f"(in-plane {upper['in_plane']:.3f})",
        "cyan",
    )
