# Changes: lung nodule stage writes mask, instances, and axial contours; skips cleanly.
"""Synthetic-mask tests for the lung nodule pipeline stage (no GPU, no download)."""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import nibabel as nib
import numpy as np

import run_nodules as nodules
import run_totalsegmentator as segmentation

SHAPE = (24, 24, 24)


def _save(path: Path, data: np.ndarray) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    nib.save(nib.Nifti1Image(np.asarray(data), np.eye(4)), path)


def _write_ct(tmp_path: Path, name: str = "ct.nii.gz") -> Path:
    path = tmp_path / name
    _save(path, np.full(SHAPE, -800.0, dtype=np.float32))
    return path


def _write_lobes(out_dir: Path) -> Path:
    lobes_dir = out_dir / "lobes"
    for name in segmentation.LOBES:
        _save(lobes_dir / f"{name}.nii.gz", np.zeros(SHAPE, dtype=np.uint8))
    merged = np.zeros(SHAPE, dtype=np.uint8)
    merged[8:16, 8:16, 8:16] = 1
    _save(out_dir / "lobes.nii.gz", merged)
    return out_dir / "lobes.nii.gz"


def _write_mask(tmp_path: Path, data: np.ndarray | None = None) -> Path:
    mask = np.zeros(SHAPE, dtype=bool) if data is None else data
    if data is None:
        mask[10:13, 10:13, 10:13] = True   # 27 voxels, id 1
        mask[16:19, 16:19, 16:19] = True   # 27 voxels, id 2
    path = tmp_path / "lung_nodules.nii.gz"
    _save(path, mask.astype(np.uint8))
    return path


def _write_case(tmp_path: Path, description: str = "CT CHEST",
                 candidates: list[dict] | None = None) -> Path:
    path = tmp_path / "case.json"
    document = {"source": {"series_description": description}}
    if candidates is not None:
        document["candidates"] = {"source": "detector", "items": candidates}
    path.write_text(json.dumps(document))
    return path


def test_command_runs_lung_nodules_with_the_lobe_crop(tmp_path):
    ct = tmp_path / "ct.nii.gz"
    out = tmp_path / "nodules_source"
    lobes = tmp_path / "lobes"
    cmd = nodules.nodule_command(ct, out, lobes)

    assert cmd == [
        sys.executable, "-m", "totalsegmentator.bin.TotalSegmentator",
        "-i", str(ct), "-o", str(out),
        "--task", "lung_nodules", "--crop_path", str(lobes),
        "--device", "mps",
    ]
    segmentation.guard_command(cmd)


def test_synthetic_mask_writes_all_three_artifacts(tmp_path):
    ct = _write_ct(tmp_path)
    out = tmp_path / "ts"
    lobes = _write_lobes(out)
    mask = _write_mask(tmp_path)
    graph = tmp_path / "case_graph.json"
    graph.write_text(json.dumps({
        "nodes": [{"id": 0, "position_mm": [0.0, 0.0, 0.0]}],
        "edges": [],
    }))

    result = nodules.write_artifacts(
        ct, mask, out, lobes_path=lobes, graph_path=graph,
    )

    assert result == {"nodules": 2}
    merged = nib.load(out / "nodule.nii.gz")
    assert merged.shape == SHAPE
    assert int(np.asanyarray(merged.dataobj).sum()) == 54

    instances = json.loads((out / "nodule_instances.json").read_text())
    assert [nodule["id"] for nodule in instances["nodules"]] == [1, 2]
    first = instances["nodules"][0]
    assert set(first) == {
        "id", "center_ras_mm", "volume_mm3", "diameter_mm",
        "axial_diameter_mm", "hu_mean", "hu_min", "hu_max", "lobe",
        "to_airway_mm", "score", "match", "box_size_mm",
    }
    assert first["volume_mm3"] == 27.0
    assert first["match"] == "mask_only"
    assert first["score"] is None
    assert first["lobe"] == "lung_upper_lobe_left"
    assert first["to_airway_mm"] is not None

    contours = json.loads((out / "nodule_contours.json").read_text())
    assert contours["shape"] == list(SHAPE)
    assert [nodule["id"] for nodule in contours["nodules"]] == [1, 2]
    first_slices = contours["nodules"][0]["slices"]
    assert [entry["slice_index"] for entry in first_slices] == [10, 11, 12]
    assert first_slices[0]["polygons"]
    for polygon in first_slices[0]["polygons"]:
        for row, column in polygon:
            assert 9.0 <= row <= 14.0
            assert 9.0 <= column <= 14.0


def test_detector_candidates_are_fused_into_the_instances(tmp_path):
    ct = _write_ct(tmp_path)
    out = tmp_path / "ts"
    lobes = _write_lobes(out)
    mask = _write_mask(tmp_path, data=np.zeros(SHAPE, dtype=bool))
    data = np.zeros(SHAPE, dtype=bool)
    data[10:13, 10:13, 10:13] = True
    _save(mask, data.astype(np.uint8))
    case = _write_case(tmp_path, candidates=[
        {"ras_mm": [11.0, 11.0, 11.0], "diameter_mm": 4.0, "score": 0.9},
    ])
    proposals = nodules.proposals_from_case(case)

    nodules.write_artifacts(
        ct, mask, out, lobes_path=lobes, proposals=proposals,
    )

    instances = json.loads((out / "nodule_instances.json").read_text())
    first = instances["nodules"][0]
    assert first["match"] == "mask_and_box"
    assert first["score"] == 0.9
    assert first["box_size_mm"] == [4.0, 4.0, 4.0]


def test_stage_publishes_fused_candidates_on_the_case(tmp_path):
    """Fused instances join the Case candidate list as suggestions."""
    ct = _write_ct(tmp_path)
    out = tmp_path / "ts"
    lobes = _write_lobes(out)
    data = np.zeros(SHAPE, dtype=bool)
    data[10:13, 10:13, 10:13] = True   # matched by the proposal below
    data[16:19, 16:19, 16:19] = True   # mask only
    mask = _write_mask(tmp_path, data=data)
    # The box is wide enough to reach the component centre (3.57 mm away);
    # falling back to the 4 mm diameter would miss it.
    case = _write_case(tmp_path, candidates=[
        {
            "ras_mm": [15.0, 11.0, 11.0], "diameter_mm": 4.0,
            "size_mm": [12.0, 4.0, 4.0], "score": 0.9,
        },
    ])

    nodules.write_artifacts(
        ct, mask, out, lobes_path=lobes,
        graph_path=None, proposals=nodules.proposals_from_case(case),
        case_json=case,
    )

    record = json.loads(case.read_text())
    assert "lesion" not in record
    block = record["candidates"]
    assert block["source"] == "lung_nodules"
    items = {item["id"]: item for item in block["items"]}
    assert sorted(items) == [1, 2]
    matched = items[1]
    assert matched["score"] == 0.9
    assert matched["size_mm"] == [12.0, 4.0, 4.0]
    assert matched["volume_mm3"] == 27.0
    assert matched["diameter_mm"] > 0.0
    assert matched["lobe"] == "lung_upper_lobe_left"
    assert matched["to_airway_mm"] is None
    assert "review" not in matched
    mask_only = items[2]
    assert mask_only["id"] == 2
    assert mask_only["score"] is None
    assert mask_only["review"] is True
    assert mask_only["volume_mm3"] == 27.0
    assert mask_only["diameter_mm"] > 0.0


def test_empty_mask_leaves_the_case_candidates_unchanged(tmp_path):
    ct = _write_ct(tmp_path)
    out = tmp_path / "ts"
    lobes = _write_lobes(out)
    case = _write_case(tmp_path, candidates=[
        {"ras_mm": [11.0, 11.0, 11.0], "diameter_mm": 4.0, "score": 0.9},
    ])
    before = json.loads(case.read_text())

    empty = _write_mask(tmp_path, data=np.zeros(SHAPE, dtype=bool))
    assert nodules.write_artifacts(
        ct, empty, out, lobes_path=lobes, case_json=case,
    ) is None

    assert json.loads(case.read_text()) == before


def test_candidate_box_size_survives_the_case_roundtrip(tmp_path):
    """A stored proposal keeps its detector box for the fusion radius."""
    case = tmp_path / "case.json"
    case.write_text(json.dumps({
        "candidates": {
            "items": [{
                "ras_mm": [15.0, 11.0, 11.0], "diameter_mm": 4.0,
                "size_mm": [12.0, 4.0, 4.0], "score": 0.9,
            }],
        },
    }))

    proposals = nodules.proposals_from_case(case)

    assert proposals[0]["size_mm"] == [12.0, 4.0, 4.0]


def test_empty_or_missing_mask_writes_no_artifacts(tmp_path):
    ct = _write_ct(tmp_path)
    out = tmp_path / "ts"
    lobes = _write_lobes(out)
    for name in (nodules.MERGED_MASK, nodules.INSTANCES, nodules.CONTOURS):
        (out / name).write_text("stale")

    empty = _write_mask(tmp_path, data=np.zeros(SHAPE, dtype=bool))
    assert nodules.write_artifacts(ct, empty, out, lobes_path=lobes) is None
    for name in (nodules.MERGED_MASK, nodules.INSTANCES, nodules.CONTOURS):
        assert not (out / name).exists()

    assert nodules.write_artifacts(
        ct, tmp_path / "absent.nii.gz", out, lobes_path=lobes,
    ) is None
    for name in (nodules.MERGED_MASK, nodules.INSTANCES, nodules.CONTOURS):
        assert not (out / name).exists()


def test_skip_reason_covers_missing_lobes_and_non_chest_series(tmp_path):
    out = tmp_path / "ts"
    assert "lobe masks" in nodules.skip_reason(out / "lobes", "CT CHEST")
    _write_lobes(out)
    assert nodules.skip_reason(out / "lobes", "CT CHEST") is None
    assert nodules.skip_reason(out / "lobes", "CT ABDOMEN") == "not a chest CT"


def test_main_skips_without_running_when_lobes_are_missing(tmp_path, monkeypatch, capsys):
    calls = []
    monkeypatch.setattr(nodules, "run_task", lambda cmd: calls.append(cmd))
    out = tmp_path / "ts"
    out.mkdir()

    code = nodules.main([str(_write_ct(tmp_path)), "-o", str(out)])

    assert code == 0
    assert calls == []
    assert "nodules skipped" in capsys.readouterr().out


def test_main_task_failure_is_one_log_line_and_not_fatal(tmp_path, monkeypatch, capsys):
    out = tmp_path / "ts"
    _write_lobes(out)

    def boom(_cmd):
        raise RuntimeError("task failed")

    monkeypatch.setattr(nodules, "run_task", boom)

    code = nodules.main([str(_write_ct(tmp_path)), "-o", str(out)])

    assert code == 0
    lines = [line for line in capsys.readouterr().out.splitlines() if line.strip()]
    assert lines == ["nodules skipped: task failed"]


def test_main_runs_the_task_then_writes_artifacts(tmp_path, monkeypatch):
    ct = _write_ct(tmp_path)
    out = tmp_path / "ts"
    lobes = _write_lobes(out)
    mask = _write_mask(tmp_path)
    graph = out / "case_graph.json"
    graph.write_text(json.dumps({"nodes": [{"id": 0, "position_mm": [0.0, 0.0, 0.0]}],
                                 "edges": []}))
    case = _write_case(tmp_path, candidates=[
        {"ras_mm": [11.0, 11.0, 11.0], "diameter_mm": 4.0, "score": 0.9},
    ])

    def fake_run(_cmd):
        destination = out / nodules.RAW_DIR / "lung_nodules.nii.gz"
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(mask, destination)

    monkeypatch.setattr(nodules, "run_task", fake_run)

    code = nodules.main([
        str(ct), "-o", str(out), "--case-json", str(case), "--graph", str(graph),
    ])

    assert code == 0
    assert (out / nodules.MERGED_MASK).is_file()
    instances = json.loads((out / nodules.INSTANCES).read_text())
    assert instances["nodules"][0]["match"] == "mask_and_box"
    assert (out / nodules.CONTOURS).is_file()
    assert lobes.is_file()
