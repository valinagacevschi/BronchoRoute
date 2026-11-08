# Changes: roll easing finishes at the fly-through fork look-ahead (holdS), not at fork.s.
# Changes: align_angle is the camera-frame video target; angle stays the mean-frame clock.
# Changes: patient and scope clocks for manual endoluminal roll.
"""Pure geometry for a transported airway navigation frame."""

import math

import numpy as np

STEP_MM = 1.0
LOOK_AHEAD_MM = 6.0
# Video fork hold sits FLYTHROUGH_FORK_LOOK_MM before fork.s (renderer). Roll must
# reach align_angle by that station and stay locked through the junction.
FLYTHROUGH_FORK_LOOK_MM = 12.0
ROLL_END_MM = FLYTHROUGH_FORK_LOOK_MM
ROLL_START_MM = FLYTHROUGH_FORK_LOOK_MM + 10.0  # ~10 mm ease into the hold pose
PAUSE_BEFORE_MM = 5.0
# Below this, a clock hour is the direction of a small miss, not a takeoff.
AHEAD_FRACTION = 0.15
# |anterior projected perpendicular to t| under this means the AP axis is unusable.
AP_DEGENERACY = 0.10
ALIGN_DEG = 15.0


def _unit(vector):
    length = np.linalg.norm(vector)
    if length <= 1e-12:
        return np.zeros(3)
    return vector / length


def resample(polyline_mm, step_mm=STEP_MM, edge_index=None):
    """Resample joined route geometry and retain its route-edge labels."""
    points = np.asarray(polyline_mm, dtype=float)
    if len(points) == 0:
        return np.empty((0, 3)), np.empty(0), np.empty(0, dtype=int)

    keep = np.r_[True, np.linalg.norm(np.diff(points, axis=0), axis=1) >= 0.01]
    points = points[keep]
    if edge_index is None:
        labels = np.zeros(len(points), dtype=int)
    else:
        labels = np.asarray(edge_index)[keep]

    if len(points) == 1:
        return points, np.array([0.0]), labels.astype(int)

    segment_lengths = np.linalg.norm(np.diff(points, axis=0), axis=1)
    cumulative = np.r_[0.0, np.cumsum(segment_lengths)]
    distances = np.arange(0.0, cumulative[-1], step_mm)
    if len(distances) == 0 or cumulative[-1] - distances[-1] > 1e-9:
        distances = np.r_[distances, cumulative[-1]]
    else:
        distances[-1] = cumulative[-1]

    samples = np.column_stack([
        np.interp(distances, cumulative, points[:, axis])
        for axis in range(3)
    ])
    edge_at = np.minimum(
        np.searchsorted(cumulative[1:], distances, side="left"), len(labels) - 1
    )
    return samples, distances, labels[edge_at].astype(int)


def tangents(points, step_mm=STEP_MM, look_ahead_mm=LOOK_AHEAD_MM):
    """Estimate unit tangents from centered finite differences."""
    points = np.asarray(points, dtype=float)
    half_window = max(1, round(look_ahead_mm / step_mm / 2))
    result = []
    for index in range(len(points)):
        before = max(0, index - half_window)
        after = min(len(points) - 1, index + half_window)
        tangent = _unit(points[after] - points[before])
        if np.linalg.norm(tangent) < 1e-12:
            tangent = result[-1].copy() if result else np.array([0.0, 0.0, 1.0])
        result.append(tangent)
    return np.asarray(result)


def transport_frame(tangents, initial_up=(0, 1, 0)):
    """Parallel-transport anterior-up, avoiding arbitrary camera roll."""
    tangents = np.asarray(tangents, dtype=float)
    if not len(tangents):
        return np.empty((0, 3))

    # Project patient anterior into the first tangent plane for clock 12.
    up = np.asarray(initial_up, dtype=float)
    up = up - np.dot(up, tangents[0]) * tangents[0]
    if np.linalg.norm(up) < 1e-3:
        up = np.array([0.0, 0.0, 1.0]) - tangents[0] * tangents[0, 2]
    ups = [_unit(up)]

    for index in range(1, len(tangents)):
        previous = tangents[index - 1]
        current = tangents[index]
        axis = np.cross(previous, current)
        sine = np.linalg.norm(axis)
        cosine = np.clip(np.dot(previous, current), -1.0, 1.0)
        up = ups[-1].copy()
        if sine > 1e-12:
            axis /= sine
            angle = np.arctan2(sine, cosine)
            up = (up * np.cos(angle) + np.cross(axis, up) * np.sin(angle)
                  + axis * np.dot(axis, up) * (1.0 - np.cos(angle)))

        up -= np.dot(up, current) * current
        if np.linalg.norm(up) < 1e-12:
            up = ups[-1] - np.dot(ups[-1], current) * current
        ups.append(_unit(up))
    return np.asarray(ups)


def clock_of(direction, tangent, up):
    """Return the clock position and angle in the transported frame."""
    direction = np.asarray(direction, dtype=float)
    tangent = _unit(np.asarray(tangent, dtype=float))
    projected = direction - np.dot(direction, tangent) * tangent
    if np.linalg.norm(projected) < AHEAD_FRACTION * np.linalg.norm(direction):
        return None, None

    right = _unit(np.cross(tangent, up))
    angle = np.degrees(
        np.arctan2(np.dot(projected, right), np.dot(projected, up))
    ) % 360
    clock = int(round(angle / 30)) % 12 or 12
    return clock, angle


def rotate(up, tangent, angle):
    """Positive roll turns up toward right, matching the fork-angle sign."""
    tangent = _unit(np.asarray(tangent, dtype=float))
    up = np.asarray(up, dtype=float)
    return (up * np.cos(angle) + np.cross(tangent, up) * np.sin(angle)
            + tangent * np.dot(tangent, up) * (1.0 - np.cos(angle)))


def patient_frame(tangent):
    """Anterior (0, 1, 0) projected perpendicular to the parent direction t.

    Degenerate when that projection is shorter than AP_DEGENERACY: t is then
    nearly the supine AP axis and 12 o'clock is undefined.
    """
    tangent = _unit(np.asarray(tangent, dtype=float))
    anterior = np.array([0.0, 1.0, 0.0])
    projected = anterior - tangent * float(np.dot(anterior, tangent))
    length = float(np.linalg.norm(projected))
    if length < AP_DEGENERACY:
        return {"degenerate": True, "upA": None, "rightA": None, "length": length}
    up_a = projected / length
    right_a = np.cross(tangent, up_a)
    return {"degenerate": False, "upA": up_a, "rightA": right_a, "length": length}


def patient_angle(tangent, direction):
    """θ_pat = atan2(d·rightA, d·upA). None when the AP axis is degenerate."""
    frame = patient_frame(tangent)
    if frame["degenerate"]:
        return None
    direction = np.asarray(direction, dtype=float)
    return float(np.arctan2(
        np.dot(direction, frame["rightA"]),
        np.dot(direction, frame["upA"]),
    ))


def scope_angle(tangent, up_screen):
    """ψ: signed angle from upA to the screen-up, about t."""
    return patient_angle(tangent, up_screen)


def in_plane_fraction(tangent, direction):
    """Share of d perpendicular to t. Near 0, the branch is straight ahead."""
    direction = np.asarray(direction, dtype=float)
    magnitude = float(np.linalg.norm(direction))
    if magnitude < 1e-12:
        return 0.0
    tangent = _unit(np.asarray(tangent, dtype=float))
    projected = direction - tangent * float(np.dot(direction, tangent))
    return float(np.linalg.norm(projected) / magnitude)


def branch_angle(tangent, direction):
    """Patient angle, or None if AP-degenerate or the branch is straight ahead."""
    if patient_frame(tangent)["degenerate"]:
        return None
    if in_plane_fraction(tangent, direction) < AHEAD_FRACTION:
        return None
    return patient_angle(tangent, direction)


def direction_along(centreline, distance_mm=5.0):
    """Offset from the first point to a sample min(distance, half the length)."""
    points = np.asarray(centreline, dtype=float)
    if len(points) < 2:
        return None
    lengths = np.r_[0.0, np.cumsum(np.linalg.norm(np.diff(points, axis=0), axis=1))]
    if float(lengths[-1]) < 1e-9:
        return None
    distance = min(float(distance_mm), float(lengths[-1]) / 2.0)
    point = np.array([
        np.interp(distance, lengths, points[:, axis])
        for axis in range(3)
    ])
    return point - points[0]


def apply_roll(up, right, phi):
    """User roll about the tangent: up' = up·cosφ + right·sinφ."""
    up = np.asarray(up, dtype=float)
    right = np.asarray(right, dtype=float)
    return up * math.cos(phi) + right * math.sin(phi)


def _js_round(value):
    """Math.round: halves toward +infinity."""
    return int(math.floor(float(value) + 0.5))


def wrap_deg(degrees):
    """Wrap degrees into (-180, 180]."""
    wrapped = ((float(degrees) + 180.0) % 360.0 + 360.0) % 360.0 - 180.0
    if wrapped == -180.0:
        return 180.0
    return float(wrapped)


def js_fixed0(value):
    """Number.toFixed(0): halves away from zero."""
    number = float(value)
    sign = -1 if number < 0 else 1
    rounded = int(math.floor(abs(number) + 0.5))
    return str(sign * rounded)


def clock_hour(angle_rad):
    """Nearest hour on a clock whose 12 is 0 radians. None stays None."""
    if angle_rad is None:
        return None
    hours = _js_round(float(angle_rad) * 6.0 / math.pi) % 12
    return hours or 12


def rotate_hint(delta_deg, ahead=False):
    """Shortest roll hint. Aligned within ±ALIGN_DEG; ahead has no hour to chase."""
    if ahead:
        return "straight ahead"
    if delta_deg is None:
        return "degenerate AP axis"
    wrapped = wrap_deg(delta_deg)
    if abs(wrapped) <= ALIGN_DEG:
        return "aligned ✓"
    arrow = "↻" if wrapped > 0 else "↺"
    return f"rotate {js_fixed0(abs(wrapped))}° {arrow}"


def fork_clock_line(fork_n, distance_mm, theta, psi, ahead=False):
    """HUD fork line. A degenerate AP axis replaces the patient-clock hour."""
    distance = js_fixed0(max(0.0, float(distance_mm)))
    if ahead:
        return f"Fork {fork_n} · {distance} mm — straight ahead"
    if theta is None or psi is None:
        return f"Fork {fork_n} · {distance} mm — degenerate AP axis"
    patient = clock_hour(theta)
    scope = clock_hour(theta - psi)
    return (
        f"Fork {fork_n} · {distance} mm — "
        f"patient {patient} o'clock · scope {scope} o'clock"
    )


def _camera_roll_angle(edge, tangent, up):
    """Roll, in degrees, that puts this child at scope 12 in the camera frame.

    The camera at ``fork.s`` rolls ``ups[sample]`` about ``tangents[sample]``.
    A 5 mm mean tangent is a different plane once the centreline bends, so the
    video profile has to ease to this sample-frame angle. ``None`` means the
    child is straight ahead in that frame (no hour to chase). The second value
    is False when the child has no usable direction.
    """
    centreline = None if edge is None else edge.get("centreline_mm", edge.get("c"))
    direction = direction_along(centreline) if centreline is not None else None
    if direction is None:
        return None, False
    tangent = _unit(np.asarray(tangent, dtype=float))
    screen_up = np.asarray(up, dtype=float)
    screen_up = screen_up - tangent * float(np.dot(screen_up, tangent))
    if float(np.linalg.norm(screen_up)) < 1e-8:
        return None, False
    _, angle = clock_of(direction, tangent, _unit(screen_up))
    return (None if angle is None else float(angle)), True


def forks(graph_edges, route, points, s, tangents, ups):
    """Describe route forks using full-resolution source edge centrelines."""
    points, s, tangents, ups = map(np.asarray, (points, s, tangents, ups))
    route = list(route)
    children = {}
    for index, edge in enumerate(graph_edges):
        parent = edge.get("from", edge.get("f"))
        children.setdefault(parent, []).append((index, edge))

    result = []
    for route_position, edge_index in enumerate(route[:-1]):
        edge = graph_edges[edge_index]
        node = edge.get("to", edge.get("t"))
        node_children = children.get(node, [])
        if len(node_children) < 2:
            continue

        first_child = node_children[0][1]
        child_points = first_child.get("centreline_mm", first_child.get("c"))
        junction = np.asarray(child_points[0], dtype=float)
        sample_index = int(np.argmin(np.linalg.norm(points - junction, axis=1)))
        fork_s = float(s[sample_index])
        window_start = int(np.searchsorted(s, max(0.0, fork_s - 5.0)))
        parent_direction = _unit(np.mean(tangents[window_start:sample_index + 1], axis=0))
        up = ups[sample_index] - np.dot(ups[sample_index], parent_direction) * parent_direction
        up = _unit(up)

        fork_children = []
        for child_index, child in node_children:
            centreline = np.asarray(
                child.get("centreline_mm", child.get("c")), dtype=float
            )
            lengths = np.r_[
                0.0, np.cumsum(np.linalg.norm(np.diff(centreline, axis=0), axis=1))
            ]
            distance = min(5.0, lengths[-1] / 2)
            point = np.array([
                np.interp(distance, lengths, centreline[:, axis])
                for axis in range(3)
            ])
            clock, angle = clock_of(point - centreline[0], parent_direction, up)
            fork_children.append({
                "edge": child_index,
                "clock": clock,
                "angle": None if angle is None else float(angle),
                "r": child.get("radius_mm", child.get("r")),
                "generation": child.get("generation", child.get("g")),
                "on_route": child_index == route[route_position + 1],
                "name": child.get("airway_name") or child.get("lobe_name"),
            })

        chosen = next((child for child in fork_children if child["on_route"]), None)
        # Mean-frame angle stays the interactive/clock field. Video auto-align
        # needs the sample-frame angle at fork.s (see align_angle / roll_profile).
        mean_angle = chosen["angle"] if chosen else None
        align_angle = mean_angle
        if chosen is not None:
            camera_angle, have_direction = _camera_roll_angle(
                graph_edges[chosen["edge"]], tangents[sample_index], ups[sample_index]
            )
            if have_direction:
                align_angle = camera_angle
        result.append({
            "node": node,
            "s": fork_s,
            "chosen": route[route_position + 1],
            "clock": chosen["clock"] if chosen else None,
            "angle": mean_angle,
            "align_angle": align_angle,
            "children": fork_children,
        })
    return result


def fork_hold_s(fork_s, prev_fork_s=None, look_mm=FLYTHROUGH_FORK_LOOK_MM):
    """Station for the video fork hold: look_mm before the junction, not on it."""
    hold = float(fork_s) - float(look_mm)
    if prev_fork_s is not None and np.isfinite(prev_fork_s):
        hold = max(hold, float(prev_fork_s))
    return max(0.0, hold)


def roll_profile(s, forks, roll_start_mm=ROLL_START_MM, roll_end_mm=ROLL_END_MM):
    """Ease between fork headings without overlapping ramps jumping the roll.

    Each ramp ends at ``fork.s - roll_end_mm`` (the fly-through hold station when
    ``roll_end_mm`` is ``FLYTHROUGH_FORK_LOOK_MM``), then stays locked through the
    junction. The next ramp starts only after that end (and not before
    ``fork.s - roll_start_mm``).
    """
    s = np.asarray(s, dtype=float)
    result = np.zeros(len(s))
    locked = 0.0
    previous_end = 0.0

    for fork in forks:
        # Video uses camera-frame align_angle when present; angle is the fallback
        # for unit fixtures that only set the mean-frame clock.
        target = fork.get("align_angle", fork.get("angle"))
        if target is None:
            continue

        start = max(0.0, fork["s"] - roll_start_mm, previous_end)
        end = max(start, fork["s"] - roll_end_mm)
        delta = (np.radians(target) - locked + np.pi) % (2 * np.pi) - np.pi
        if delta <= -np.pi:
            delta += 2 * np.pi
        between_ramps = (s >= previous_end) & (s < start)
        result[between_ramps] = locked

        if start == end:
            result[s >= end] = locked + delta
        else:
            ramp = (s >= start) & (s < end)
            progress = (s[ramp] - start) / (end - start)
            eased = progress * progress * (3.0 - 2.0 * progress)
            result[ramp] = locked + delta * eased
            result[s >= end] = locked + delta

        locked += delta
        # Hold the align through the junction; the next ramp may only start
        # after this fork is passed (not at holdS, which is still upstream).
        previous_end = max(end, float(fork["s"]))

    result[s >= previous_end] = locked
    return result


def pause_points(forks, pause_before_mm=PAUSE_BEFORE_MM):
    """Return one forward-motion stop point for each route fork."""
    return [max(0.0, fork["s"] - pause_before_mm) for fork in forks]
