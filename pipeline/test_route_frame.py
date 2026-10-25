import numpy as np

from route_frame import (clock_of, forks, pause_points, resample, roll_profile, rotate,
                         tangents, transport_frame)


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
