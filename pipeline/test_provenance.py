import numpy as np

from build_graph import add_edge_provenance, export
from make_prototype_data import (edge_provenance_at, interpolated_segments,
                                 nearest_centreline_sample, provenance_mapping,
                                 truncate_route_points)
from route_frame import forks, resample, tangents, transport_frame


def edge(a, b, points, **extra):
    return {"from": a, "to": b, "centreline_mm": points,
            "radius_mm": 5.0, "generation": b - 1, **extra}


def test_bridge_samples_tag_edge_and_only_descendant_as_downstream():
    edges = [
        edge(0, 1, [[0, 0, 0], [1, 0, 0], [2, 0, 0], [3, 0, 0], [4, 0, 0]]),
        edge(1, 2, [[4, 0, 0], [5, 0, 0]]),
        edge(0, 3, [[0, 0, 0], [0, 1, 0]]),
    ]
    mask = np.zeros((5, 2, 1), dtype=np.uint8)
    mask[1:4, 0, 0] = 1
    source, count, length, downstream = add_edge_provenance(edges, mask, np.eye(4))
    assert source == "bridge mask"
    assert (count, length, downstream) == (1, 3.0, 1)
    assert edges[0]["provenance"] == "interpolated"
    assert edges[1]["provenance"] == edges[2]["provenance"] == "ct"
    assert edges[1]["downstream_of_interpolated"] is True
    assert edges[2]["downstream_of_interpolated"] is False


def test_sparse_samples_straddling_one_bridge_voxel_are_detected():
    edges = [edge(0, 1, [[0, 1, 1], [2, 1, 1]])]
    mask = np.zeros((3, 3, 3), dtype=np.uint8)
    mask[1, 1, 1] = 1
    add_edge_provenance(edges, mask, np.eye(4))
    assert edges[0]["provenance"] == "interpolated"
    assert edges[0]["interpolated_mm"] == 1.0
    assert edges[0]["interpolated_spans_mm"] == [[0.5, 1.5]]


def test_spans_map_to_decimated_segments_and_fly_arc_positions():
    points = [[float(x), 0, 0] for x in range(7)]
    spans = [[2.2, 2.6], [4.0, 4.2]]
    assert interpolated_segments(points, [0, 3, 6], spans) == [0, 1]
    edge_data = {"provenance": "interpolated", "interpolated_spans_mm": [spans[0]]}
    assert edge_provenance_at(edge_data, 2.0) == "ct"
    assert edge_provenance_at(edge_data, 2.4) == "interpolated"
    assert edge_provenance_at(edge_data, 2.8) == "ct"
    assert edge_provenance_at({"provenance": "ct"}, 2.4) == "ct"
    assert edge_provenance_at({}, 2.4) == "unrecorded"


def test_no_bridge_mask_marks_edges_ct_and_graph_source(tmp_path):
    edges = [edge(0, 1, [[0, 0, 0], [1, 0, 0]])]
    result = add_edge_provenance(edges, None, np.eye(4))
    assert result == ("no bridge mask", 0, 0.0, 0)
    assert edges[0]["provenance"] == "ct"
    assert edges[0]["downstream_of_interpolated"] is False
    branch = {"from": 0, "to": 1, "generation": 0, "length_mm": 1.0,
              "centreline": np.array([[0., 0., 0.], [1., 0., 0.]]),
              "radii": np.array([1., 1.]), "lobe": None,
              "lobe_confidence": None, "lobe_source": None,
              "airway_name": None}
    graph = export([branch], "synthetic", (1, 1, 1), np.eye(4), tmp_path / "graph.json")
    assert graph["provenance_source"] == "no bridge mask"


def test_nearest_sample_gap_truncates_fly_geometry_and_inside_radius():
    edges = [
        edge(0, 1, [[0, 0, 0], [5, 0, 0]]),
        edge(1, 2, [[5, 0, 0], [10, 0, 0], [15, 0, 0]]),
        edge(1, 3, [[5, 0, 0], [5, 5, 0]]),
        edge(2, 4, [[15, 0, 0], [20, 0, 0]]),
        edge(2, 5, [[15, 0, 0], [10, 0, 0]]),
    ]
    ei, si, point, gap = nearest_centreline_sample(edges, [15, 12, 0])
    assert (ei, si, gap) == (1, 2, 12.0)
    route = [0, 1]
    points = truncate_route_points(edges, route, ei, si, lambda p: p)
    assert np.allclose(points[-1], point)
    p, s, _ = resample(points)
    assert np.allclose(p[-1], point) and s[-1] == 15
    # Forks after this endpoint are absent because no later route edge is passed.
    t = tangents(p); up = transport_frame(t)
    assert [fork["node"] for fork in forks(edges, route, p, s, t, up)] == [1]
    _, _, _, inside_gap = nearest_centreline_sample(edges, [10, 2, 0])
    target_inside_airway = inside_gap <= edges[1]["radius_mm"]
    assert inside_gap == 2.0 and target_inside_airway


def test_old_graph_provenance_is_unrecorded():
    mapped, recorded = provenance_mapping([edge(0, 1, [[0, 0, 0], [1, 0, 0]])])
    assert not recorded
    assert mapped == [{"prov": "unrecorded", "down_i": False}]
