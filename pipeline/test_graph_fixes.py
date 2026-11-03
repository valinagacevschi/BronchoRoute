# Changes: regressions for mask-contained skeleton joins, RAS root and empty trees.
from pathlib import Path
from types import SimpleNamespace
import os
import subprocess
import sys

import networkx as nx
import nibabel as nib
import numpy as np
import pytest

import build_graph as bg


def pieces():
    return SimpleNamespace(
        vertices=np.array([[1, 2, 2], [2, 2, 2], [5, 2, 2], [6, 2, 2]], dtype=float),
        radii=np.ones(4),
        edges=np.array([[0, 1], [2, 3]]),
    )


def test_join_inside_tube_is_ct(tmp_path):
    mask = np.zeros((8, 5, 5), dtype=np.uint8)
    mask[1:7, 1:4, 1:4] = 1
    graph = bg.to_graph(pieces(), mask, (1, 1, 1))
    assert nx.is_connected(graph)
    assert len(graph) == 4
    assert graph.edges[1, 2]["provenance"] == "ct"
    branches = bg.trace_branches(graph, 0)
    bg.label_lobes(branches, None, (1, 1, 1))
    exported = bg.export(branches, "synthetic", (1, 1, 1), np.eye(4), tmp_path / "graph.json")
    assert {edge["provenance"] for edge in exported["edges"]} == {"ct"}


def test_join_refuses_non_mask_gap():
    mask = np.ones((8, 5, 5), dtype=np.uint8)
    mask[3] = 0
    graph = bg.to_graph(pieces(), mask, (1, 1, 1))
    assert len(graph) == 2
    assert not graph.has_edge(1, 2)


@pytest.mark.parametrize("affine, expected", [
    (np.eye(4), 0),
    (np.diag([1, 1, -1, 1]), 1),
    (np.array([[0, 0, 1, 0], [0, 1, 0, 0], [1, 0, 0, 0], [0, 0, 0, 1]]), 1),
])
def test_root_is_wide_and_superior_in_ras(affine, expected):
    graph = nx.Graph()
    for node, pos, radius in [(0, [1, 0, 10], 5), (1, [4, 0, 0], 6),
                              (2, [0, 0, 20], 0.5), (3, [0, 1, -5], 1)]:
        graph.add_node(node, pos=np.array(pos), radius=radius)
    graph.add_edges_from([(0, 1), (1, 2), (1, 3)])
    assert bg.find_root(graph, (1, 1, 1), affine) == expected


@pytest.mark.parametrize("empty_mask", [False, True])
def test_empty_tree_cli_is_clean(tmp_path, empty_mask):
    mask = np.zeros((16, 16, 32), dtype=np.uint8)
    if not empty_mask:
        mask[5:11, 5:11, 2:30] = 1
    airways = tmp_path / "airways.nii.gz"
    lobes = tmp_path / "lobes.nii.gz"
    nib.save(nib.Nifti1Image(mask, np.eye(4)), airways)
    nib.save(nib.Nifti1Image(np.zeros_like(mask), np.eye(4)), lobes)
    result = subprocess.run(
        [sys.executable, str(Path(bg.__file__)), str(tmp_path / "synthetic"),
         "--airways", str(airways), "--lobes", str(lobes), "-o", str(tmp_path / "out")],
        env={**os.environ, "LUNGNAV_DATA_ROOT": str(tmp_path)},
        capture_output=True, text=True,
    )
    assert result.returncode != 0
    assert "no airway tree in lung" in result.stderr
    assert "Traceback" not in result.stderr
    assert not (tmp_path / "out").exists()


def test_job_failure_names_graph_step(tmp_path, monkeypatch):
    import io
    import repo_root
    repo_root.ensure()
    from app import jobs

    job = jobs.JobState("local-001", "segment")
    job._json_path = tmp_path / "job.json"
    monkeypatch.setattr(jobs, "_pipeline_cwd", lambda: Path(bg.__file__).parent)
    monkeypatch.setattr(jobs, "_kill_process_group", lambda *args: None)
    proc = SimpleNamespace(pid=424242, stdout=io.StringIO("graph: no airway tree in lung\n"),
                           wait=lambda timeout: 1)
    monkeypatch.setattr(jobs, "_launcher", lambda cmd, cwd: proc)
    jobs._run_job(job, [["python", "build_graph.py"]], "AB")
    assert job.status == "failed"
    assert "graph step" in job.error
    assert "no airway tree in lung" in job.error
    assert "Try another Series" in job.error


@pytest.mark.parametrize("case, branches, generation", [
    ("s2-ser3-bf37", 435, 14),
    ("s2-ser4-br60tap", 372, 14),
    ("s2-ser5-br60", 394, 13),
    ("s1-ser2-br38gated", 0, None),
])
def test_real_fixture_graph(tmp_path, case, branches, generation):
    """Exercise the job runner's graph command using an explicit temporary fixture copy."""
    import json
    import tempfile

    fixture_root = os.environ.get("G14_FIXTURE_ROOT")
    if not fixture_root:
        pytest.skip("set G14_FIXTURE_ROOT to a temporary fixture copy")
    root = Path(fixture_root).resolve()
    assert root.is_relative_to(Path(tempfile.gettempdir()).resolve())
    ts = root / "ts_out" / case
    out = tmp_path / "out"
    result = subprocess.run(
        [sys.executable, str(Path(bg.__file__)), str(root / "data/extracted/local" / case),
         "--airways", str(ts / "airways.nii.gz"), "--lobes", str(ts / "lobes.nii.gz"),
         "--bridges", str(ts / "airway_bridges.nii.gz"), "-o", str(out)],
        env={**os.environ, "LUNGNAV_DATA_ROOT": str(root)},
        capture_output=True, text=True,
    )
    if not branches:
        assert result.returncode != 0
        assert "no airway tree in lung" in result.stderr
        assert "Traceback" not in result.stderr
        assert not out.exists()
        return
    assert result.returncode == 0, result.stderr
    graph = json.loads((out / f"{case}_graph.json").read_text())
    edges = graph["edges"]
    assert len(edges) == pytest.approx(branches, rel=0.05)
    assert max(edge["generation"] for edge in edges) == generation
    assert {edge["lobe"] for edge in edges if edge["lobe"]} == {1, 2, 3, 4, 5}
    assert not any(flag["kind"] == "lobe_without_airways" for flag in graph["review"])
    nodes = {node["id"]: node for node in graph["nodes"]}
    trachea = next(edge for edge in edges if edge["airway_name"] == "trachea")
    root_node = nodes[graph["root_node_id"]]
    assert root_node["position_mm"][2] > nodes[trachea["to"]]["position_mm"][2]
    assert root_node["radius_mm"] >= 4
