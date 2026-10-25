import numpy as np
from scipy import ndimage

from run_totalsegmentator import bridge_airway_components


def make_components(components):
    shape = (48, 32, 24)
    combined = np.zeros(shape, dtype=bool)
    for target, center, half_width in components:
        slices = tuple(slice(c - half_width, c + half_width + 1) for c in center)
        combined[slices] = True
    return combined


def test_bridge_uses_a_millimeter_tube_and_drops_ineligible_pieces():
    components = [
        ("airway", (8, 12, 12), 3),
        ("trachea", (18, 12, 12), 3),
        ("airway", (8, 27, 12), 3),
        ("airway", (35, 12, 12), 1),
    ]
    combined = make_components(components)
    result, bridged, dropped = bridge_airway_components(combined, (0.7, 0.7, 0.5))
    labels, count = ndimage.label(combined, structure=np.ones((3, 3, 3)))
    main_labels = [labels[center] for _, center, _ in components[:2]]
    assert main_labels[0] != main_labels[1]
    assert count == 4

    added = result & ~combined
    first = labels == main_labels[0]
    second = labels == main_labels[1]
    distances, nearest = ndimage.distance_transform_edt(
        ~first, sampling=(0.7, 0.7, 0.5), return_indices=True
    )
    second_points = np.argwhere(second)
    closest = second_points[np.argmin(distances[tuple(second_points.T)])]
    start = nearest[(slice(None), *closest)]
    end = closest
    direction = (end - start) * np.array((0.7, 0.7, 0.5))
    length_squared = np.dot(direction, direction)
    added_points = np.argwhere(added)
    relative = (added_points - start) * np.array((0.7, 0.7, 0.5))
    t = np.clip(relative @ direction / length_squared, 0, 1)
    distance = np.linalg.norm(relative - t[:, None] * direction, axis=1)
    assert len(added_points) > 0
    assert np.all(distance <= 1.0 + 1e-6)
    assert np.all(result[first])
    assert np.all(result[second])
    assert not result[labels == labels[components[2][1]]].any()
    assert not result[labels == labels[components[3][1]]].any()
    _, result_count = ndimage.label(result, structure=np.ones((3, 3, 3)))
    assert result_count == 1
    assert len(bridged) == 1
    assert dropped == 2
