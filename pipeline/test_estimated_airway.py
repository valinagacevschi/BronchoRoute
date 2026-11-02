# Changes: estimated airway is capped at 15 mm, stays out of the routable graph,
# and the graph step receives the artery mask.
"""Synthetic masks, graph CLI, and routing for estimated airway."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import nibabel as nib
import numpy as np
import pytest
from termcolor import cprint

import build_graph as bg
from estimated_airway import estimate_airway

PIPE = Path(__file__).resolve().parent
TIP = [48.0, 16.0, 16.0]
SHAPE = (110, 44, 36)
SPACING = (1.0, 1.0, 1.0)
AFFINE = np.eye(4)
_ESTIMATED_LINE = re.compile(
    r"estimated (\d+) of (\d+) terminals, ([0-9.]+) mm, "
    r"max ([0-9.]+) mm \(([0-9.]+) s\)"
)


def _line(start, end, step=1.0) -> list[list[float]]:
    start = np.asarray(start, dtype=float)
    end = np.asarray(end, dtype=float)
    count = max(1, int(round(float(np.linalg.norm(end - start) / step))))
    points = []
    for t in np.linspace(0.0, 1.0, count + 1):
        points.append(((1.0 - t) * start + t * end).round(3).tolist())
    return points


def _edge(start, end, frm, to, generation, lobe, provenance="ct", spans=None) -> dict:
    points = _line(start, end)
    length = float(np.linalg.norm(np.diff(np.asarray(points, dtype=float), axis=0), axis=1).sum())
    edge = {
        "from": frm,
        "to": to,
        "generation": generation,
        "length_mm": round(length, 2),
        "radius_mm": 1.5,
        "lobe": lobe,
        "provenance": provenance,
        "centreline_mm": points,
    }
    if spans is not None:
        edge["interpolated_spans_mm"] = spans
    return edge


def _edges(provenance="ct", spans=None) -> list[dict]:
    return [
        _edge([8.0, 16.0, 16.0], [30.0, 16.0, 16.0], 0, 1, 0, 5),
        _edge([30.0, 16.0, 16.0], TIP, 1, 2, 1, 5, provenance, spans),
    ]


def _tube(shape, start, end, radius) -> np.ndarray:
    shape = tuple(int(value) for value in shape)
    start = np.asarray(start, dtype=float)
    end = np.asarray(end, dtype=float)
    lo = np.floor(np.minimum(start, end) - radius - 1).astype(int)
    hi = np.ceil(np.maximum(start, end) + radius + 1).astype(int)
    lo = np.clip(lo, 0, np.asarray(shape) - 1)
    hi = np.clip(hi, 0, np.asarray(shape) - 1)
    xs = np.arange(lo[0], hi[0] + 1)
    ys = np.arange(lo[1], hi[1] + 1)
    zs = np.arange(lo[2], hi[2] + 1)
    grid = np.stack(np.meshgrid(xs, ys, zs, indexing="ij"), axis=-1).reshape(-1, 3)
    span = end - start
    denom = float(np.dot(span, span))
    if denom == 0.0:
        distance = np.linalg.norm(grid - start, axis=1)
    else:
        t = np.clip(((grid - start) @ span) / denom, 0.0, 1.0)
        distance = np.linalg.norm(grid - (start + t[:, None] * span), axis=1)
    mask = np.zeros(shape, dtype=np.uint8)
    chosen = grid[distance <= radius].astype(int)
    if len(chosen):
        mask[chosen[:, 0], chosen[:, 1], chosen[:, 2]] = 1
    return mask


def _estimate(artery, lobes, provenance="ct", spans=None):
    edges = _edges(provenance, spans)
    before = json.dumps(edges)
    segments, summary = estimate_airway(edges, artery > 0, lobes, SPACING, AFFINE)
    assert json.dumps(edges) == before
    assert all(edge.get("provenance") != "estimated" for edge in edges)
    return edges, segments, summary


def _parallel(y=19.0, x1=90.0, radius=1.7):
    return _tube(SHAPE, (18.0, y, 16.0), (x1, y, 16.0), radius)


def test_parallel_artery_is_capped_at_15mm():
    lobes = np.full(SHAPE, 5, dtype=np.int16)
    edges, segments, summary = _estimate(_parallel(), lobes)
    assert len(segments) == 1, summary
    segment = segments[0]
    tip = edges[1]["centreline_mm"][-1]
    points = np.asarray(segment["points_mm"], dtype=float)
    distances = segment["distance_mm"]
    direction = points[-1] - points[0]
    direction = direction / np.linalg.norm(direction)
    assert segment["from_edge"] == 1
    assert segment["provenance"] == "estimated"
    assert segment["stop_reason"] == "max_len"
    assert segment["points_mm"][0] == tip
    assert abs(segment["length_mm"] - 15.0) <= 0.1
    assert distances[0] == 0.0
    assert abs(distances[-1] - 15.0) <= 0.1
    assert all(later >= earlier - 1e-9 for earlier, later in zip(distances, distances[1:]))
    assert max(distances) <= 15.0 + 1e-9
    assert direction[0] > 0.98
    assert np.max(np.abs(points[:, 1] - tip[1])) < 1.25
    assert np.max(np.abs(points[:, 2] - tip[2])) < 1.25
    assert abs(float(points[:, 1].mean()) - 19.0) > 2.0
    steps = np.linalg.norm(np.diff(points, axis=0), axis=1)
    assert steps.max() <= 0.51
    assert summary["estimated"] == 1
    assert summary["max_mm"] == pytest.approx(15.0, abs=0.1)


def test_distant_artery_is_not_estimated():
    lobes = np.full(SHAPE, 5, dtype=np.int16)
    _edges, segments, summary = _estimate(_parallel(y=28.0), lobes)
    assert segments == []
    assert summary["estimated"] == 0
    assert summary["terminals"] == 1


def test_antiparallel_artery_is_not_estimated():
    lobes = np.full(SHAPE, 5, dtype=np.int16)
    # Beside the tip and running back toward the hilum. The capsule must end
    # before the tip, or its end-cap skeleton is a short forward spur.
    artery = _tube(SHAPE, (12.0, 19.0, 16.0), (46.0, 19.0, 16.0), 1.3)
    _edges, segments, summary = _estimate(artery, lobes)
    assert segments == [], segments
    assert summary["estimated"] == 0


def test_turn_stops_before_the_cap():
    shape = (80, 70, 36)
    lobes = np.full(shape, 5, dtype=np.int16)
    artery = np.maximum(
        _tube(shape, (20.0, 19.0, 16.0), (53.0, 19.0, 16.0), 1.4),
        _tube(shape, (53.0, 19.0, 16.0), (53.0, 55.0, 16.0), 1.4),
    )
    _edges, segments, _summary = _estimate(artery, lobes)
    assert len(segments) == 1, segments
    segment = segments[0]
    assert segment["stop_reason"] == "turn", segment
    assert 1.0 <= segment["length_mm"] < 15.0


@pytest.mark.parametrize("label, reason", [(0, "left_lung"), (4, "lobe_change")])
def test_leaving_the_lobe_stops(label, reason):
    lobes = np.full(SHAPE, 5, dtype=np.int16)
    lobes[56:] = label
    _edges, segments, _summary = _estimate(_parallel(), lobes)
    assert len(segments) == 1, segments
    segment = segments[0]
    assert segment["stop_reason"] == reason, segment
    assert 1.0 <= segment["length_mm"] < 15.0


def test_interpolated_tip_is_not_estimated():
    lobes = np.full(SHAPE, 5, dtype=np.int16)
    length = _edges()[1]["length_mm"]
    _kept, segments, summary = _estimate(
        _parallel(), lobes, provenance="interpolated", spans=[[0.0, length]],
    )
    assert segments == []
    assert summary["terminals"] == 0


def _save(path: Path, data) -> None:
    nib.save(nib.Nifti1Image(np.asanyarray(data), np.eye(4)), path)


def _run_graph(tmp: Path, airways: Path, lobes: Path, out: Path, arteries: Path | None):
    cmd = [
        sys.executable, str(Path(bg.__file__)), str(tmp / "synthetic"),
        "--airways", str(airways),
        "--lobes", str(lobes),
        "-o", str(out),
    ]
    if arteries is not None:
        cmd.extend(["--arteries", str(arteries)])
    env = {**os.environ, "LUNGNAV_DATA_ROOT": str(tmp)}
    env.pop("PYTHONPATH", None)
    return subprocess.run(cmd, capture_output=True, text=True, env=env, timeout=180)


def _fingerprint(graph: dict) -> list:
    return [
        (edge["from"], edge["to"], edge["centreline_mm"], edge.get("provenance"))
        for edge in graph["edges"]
    ]


def test_build_graph_cli_estimated_does_not_change_edges(tmp_path):
    shape = (40, 40, 150)
    airway = _tube(shape, (20, 20, 140), (20, 20, 42), 2.4)
    airway = np.maximum(airway, _tube(shape, (20, 20, 145), (20, 20, 105), 4.2))
    artery = _tube(shape, (23, 20, 70), (23, 20, 4), 1.8)
    lobes = np.full(shape, 5, dtype=np.int16)
    airways = tmp_path / "airways.nii.gz"
    lobe_path = tmp_path / "lobes.nii.gz"
    artery_path = tmp_path / "arteries.nii.gz"
    _save(airways, airway)
    _save(lobe_path, lobes)
    _save(artery_path, artery)
    missing = tmp_path / "missing-arteries.nii.gz"

    plain = _run_graph(tmp_path, airways, lobe_path, tmp_path / "plain", None)
    guided = _run_graph(tmp_path, airways, lobe_path, tmp_path / "guided", artery_path)
    absent = _run_graph(tmp_path, airways, lobe_path, tmp_path / "absent", missing)
    for result in (plain, guided, absent):
        assert result.returncode == 0, result.stdout + result.stderr
        assert "Traceback" not in result.stderr
        assert _ESTIMATED_LINE.search(result.stdout)

    plain_graph = json.loads((tmp_path / "plain" / "synthetic_graph.json").read_text())
    guided_graph = json.loads((tmp_path / "guided" / "synthetic_graph.json").read_text())
    absent_graph = json.loads((tmp_path / "absent" / "synthetic_graph.json").read_text())
    assert plain_graph["estimated"] == []
    assert absent_graph["estimated"] == []
    assert guided_graph["estimated"], guided.stdout
    assert _fingerprint(plain_graph) == _fingerprint(guided_graph)
    assert _fingerprint(plain_graph) == _fingerprint(absent_graph)
    assert all(edge.get("provenance") != "estimated" for edge in guided_graph["edges"])
    for segment in guided_graph["estimated"]:
        assert segment["provenance"] == "estimated"
        assert 1.0 <= segment["length_mm"] <= 15.1
    assert guided_graph["estimated_summary"]["max_mm"] <= 15.1


def test_estimated_segments_are_not_routable():
    import repo_root
    repo_root.ensure()
    from app.route_target import rank_route_target_candidates, snap_to_selectable_ct
    from make_prototype_data import build_payload

    lobes = np.full(SHAPE, 5, dtype=np.int16)
    edges, segments, _summary = _estimate(_parallel(), lobes)
    assert len(segments) == 1
    tip = np.asarray(edges[1]["centreline_mm"][-1], dtype=float)
    far = segments[0]["points_mm"][-1]
    assert float(np.linalg.norm(np.asarray(far, dtype=float) - tip)) > 10.0
    positions = {}
    for edge in edges:
        positions[edge["from"]] = edge["centreline_mm"][0]
        positions[edge["to"]] = edge["centreline_mm"][-1]
    graph = {
        "case": "synthetic",
        "root_node_id": 0,
        "nodes": [
            {"id": node, "position_mm": positions[node], "radius_mm": 1.5}
            for node in sorted(positions)
        ],
        "edges": edges,
        "estimated": segments,
        "review": [],
    }
    candidates = rank_route_target_candidates(graph, {}, far)
    assert candidates
    assert min(candidate["target_gap_mm"] for candidate in candidates) > 10.0
    for candidate in candidates:
        assert float(np.linalg.norm(np.asarray(candidate["ras_mm"], dtype=float) - far)) > 8.0
    assert snap_to_selectable_ct(graph, far) is None
    click = [tip[0] + 1.0, tip[1], tip[2]]
    snapped = snap_to_selectable_ct(graph, click)
    assert snapped is not None
    snapped_ras = np.asarray(snapped["ras_mm"], dtype=float)
    assert snapped["edge_index"] == 1
    assert float(np.linalg.norm(snapped_ras - tip)) < 0.75
    assert float(np.linalg.norm(snapped_ras - click)) > 0.4

    payload = build_payload(
        graph,
        {"lesion": {"ras_mm": [float(value) for value in far], "diameter_mm": 10}},
        True,
        target=[float(value) for value in far],
    )
    assert len(payload["edges"]) == len(edges)
    assert all(edge.get("prov") != "estimated" for edge in payload["edges"])
    assert payload["estimated"]
    assert all(row["prov"] == "estimated" for row in payload["estimated"])
    assert payload["target_gap_mm"] > 10.0
    origin = np.asarray(payload["origin_mm"], dtype=float)
    endpoint = np.asarray(payload["endpoint"], dtype=float) + origin
    assert float(np.linalg.norm(endpoint - tip)) < 0.75
    assert float(np.linalg.norm(endpoint - far)) > 10.0
    fly = np.asarray(payload["fly"]["p"], dtype=float)
    assert "estimated" not in payload["fly"]["prov"]
    estimated_far = [
        np.asarray(point, dtype=float)
        for row in payload["estimated"]
        for point, distance in zip(row["c"], row["d"])
        if distance > 1.0
    ]
    assert estimated_far
    for point in estimated_far:
        assert float(np.linalg.norm(fly - point, axis=1).min()) > 0.6


def test_segment_command_passes_arteries(tmp_path, monkeypatch):
    import repo_root
    repo_root.ensure()
    import paths
    from app.caseref import CaseRef
    from app.jobs import _segment_commands
    from test_app_server import _write_case_tree

    assert paths.PIPELINE_VERSION == "2"
    _write_case_tree(tmp_path)
    monkeypatch.setattr(paths, "PIPELINE", tmp_path)
    monkeypatch.setattr(paths, "LOCAL_CASES", tmp_path / "data/extracted/local")
    monkeypatch.setattr(paths, "AEROPATH", tmp_path / "data/extracted/AeroPath")
    monkeypatch.setattr(paths, "TS_OUT", tmp_path / "ts_out")
    monkeypatch.setattr(paths, "OUT", tmp_path / "out")
    monkeypatch.delenv("BRONCHOROUTE_FAKE_SEGMENT", raising=False)
    commands = _segment_commands(CaseRef.parse("local-001"))
    graph = commands[1]
    arteries = tmp_path / "ts_out" / "local-001" / "airways_source" / "lung_arteries.nii.gz"
    assert graph[graph.index("--arteries") + 1] == str(arteries)


def test_local_001_estimated_airway(tmp_path):
    from conftest import symlink_input_file

    real = {
        "airways": PIPE / "ts_out/local-001/airways.nii.gz",
        "lobes": PIPE / "ts_out/local-001/lobes.nii.gz",
        "bridges": PIPE / "ts_out/local-001/airway_bridges.nii.gz",
        "arteries": PIPE / "ts_out/local-001/airways_source/lung_arteries.nii.gz",
    }
    missing = [str(path) for path in real.values() if not path.is_file()]
    if missing:
        pytest.skip("local-001 masks are not in this worktree")
    case_dir = tmp_path / "data/extracted/local/local-001"
    case_dir.mkdir(parents=True)
    ts = tmp_path / "ts_out/local-001"
    linked = {name: ts / path.name for name, path in real.items()}
    linked["arteries"] = ts / "airways_source/lung_arteries.nii.gz"
    for name, path in real.items():
        symlink_input_file(path, linked[name])

    def run(out: Path, arteries: Path | None):
        cmd = [
            sys.executable, str(Path(bg.__file__)), str(case_dir),
            "--airways", str(linked["airways"]),
            "--lobes", str(linked["lobes"]),
            "--bridges", str(linked["bridges"]),
            "-o", str(out),
        ]
        if arteries is not None:
            cmd.extend(["--arteries", str(arteries)])
        env = {
            **os.environ,
            "LUNGNAV_DATA_ROOT": str(tmp_path),
            "LUNGNAV_PIPELINE_ROOT": str(tmp_path),
        }
        env.pop("PYTHONPATH", None)
        cprint(f"build_graph local-001 arteries={arteries is not None}", "cyan")
        return subprocess.run(cmd, capture_output=True, text=True, env=env, timeout=600)

    guided = run(tmp_path / "with", linked["arteries"])
    plain = run(tmp_path / "without", None)
    assert guided.returncode == 0, guided.stdout + guided.stderr
    assert plain.returncode == 0, plain.stdout + plain.stderr
    match = _ESTIMATED_LINE.search(guided.stdout)
    assert match, guided.stdout
    elapsed = float(match.group(5))
    guided_graph = json.loads((tmp_path / "with/local-001_graph.json").read_text())
    plain_graph = json.loads((tmp_path / "without/local-001_graph.json").read_text())
    summary = guided_graph["estimated_summary"]
    cprint(
        f"local-001 estimated_summary {summary} estimation {elapsed:.2f} s",
        "cyan",
    )
    assert summary["estimated"] > 0
    assert summary["max_mm"] <= 15.1
    assert _fingerprint(guided_graph) == _fingerprint(plain_graph)
    assert plain_graph["estimated"] == []
    for segment in guided_graph["estimated"]:
        assert segment["provenance"] == "estimated"
        assert 1.0 <= segment["length_mm"] <= 15.1
        assert max(segment["distance_mm"]) <= 15.0 + 1e-6
    assert all(edge.get("provenance") != "estimated" for edge in guided_graph["edges"])
