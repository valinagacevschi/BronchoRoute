# Changes: nodule components get one layer and one unsmoothed mesh each.
# Changes: context layers (lungs, vessels, heart) are built when masks exist and skipped when absent.
"""Synthetic masks for the multi-layer scene build."""
from __future__ import annotations

import json
from pathlib import Path

import nibabel as nib
import numpy as np
import pytest

import build_endoluminal_web as bw
import paths

SHAPE = (48, 40, 40)
AFFINE = np.eye(4)
CASE = "synthetic-001"


def _save(path: Path, mask: np.ndarray) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    nib.save(nib.Nifti1Image(mask.astype(np.uint8), AFFINE), str(path))


def _box(lo, hi):
    mask = np.zeros(SHAPE, dtype=np.uint8)
    mask[lo[0]:hi[0], lo[1]:hi[1], lo[2]:hi[2]] = 1
    return mask


def _case_tree(tmp_path: Path, heart: bool = True) -> tuple[Path, Path]:
    cases, ts = tmp_path / "cases", tmp_path / "ts_out"
    case_dir = cases / CASE
    case_dir.mkdir(parents=True)
    (case_dir / "case.json").write_text("{}")
    _save(ts / CASE / "airways.nii.gz", _box((4, 18, 18), (44, 22, 22)))
    lobes = ts / CASE / "lobes"
    for name in bw.LOBES:
        _save(lobes / f"{name}.nii.gz", _box((6, 8, 6), (42, 32, 34)))
    if heart:
        _save(lobes / "heart.nii.gz", _box((18, 12, 12), (30, 28, 28)))
    source = ts / CASE / "airways_source"
    _save(source / "lung_arteries.nii.gz", _box((10, 14, 14), (38, 16, 16)))
    _save(source / "lung_veins.nii.gz", _box((10, 24, 24), (38, 26, 26)))
    return cases, ts


def test_context_layers_built_from_masks(tmp_path, monkeypatch):
    cases, ts = _case_tree(tmp_path)
    monkeypatch.setattr(paths, "LOCAL_CASES", cases)
    monkeypatch.setattr(paths, "TS_OUT", ts)
    out = bw.build_case(CASE)
    scene = json.loads((out / "scene.json").read_text())
    names = [layer["name"] for layer in scene["layers"]]
    assert names == ["lungs", "heart", "arteries", "veins", "airways"]
    for layer in scene["layers"]:
        data = (out / layer["file"]).read_bytes()
        expected = layer["vertices"] * 12 + layer["triangles"] * 12
        assert len(data) == expected
        assert layer["triangles"] > 0
    assert (out / "graph.json").is_file()


def test_missing_structure_is_skipped(tmp_path, monkeypatch):
    cases, ts = _case_tree(tmp_path, heart=False)
    monkeypatch.setattr(paths, "LOCAL_CASES", cases)
    monkeypatch.setattr(paths, "TS_OUT", ts)
    out = bw.build_case(CASE)
    names = [layer["name"] for layer in json.loads((out / "scene.json").read_text())["layers"]]
    assert "heart" not in names
    assert not (out / "heart.bin").exists()
    assert "airways" in names


def test_all_zero_structure_is_skipped(tmp_path, monkeypatch):
    cases, ts = _case_tree(tmp_path)
    _save(ts / CASE / "lobes" / "heart.nii.gz", np.zeros(SHAPE, np.uint8))
    monkeypatch.setattr(paths, "LOCAL_CASES", cases)
    monkeypatch.setattr(paths, "TS_OUT", ts)
    out = bw.build_case(CASE)
    names = [layer["name"] for layer in json.loads((out / "scene.json").read_text())["layers"]]
    assert "heart" not in names
    assert "airways" in names


def test_missing_airway_stops_the_build(tmp_path, monkeypatch):
    cases, ts = _case_tree(tmp_path)
    (ts / CASE / "airways.nii.gz").unlink()
    monkeypatch.setattr(paths, "LOCAL_CASES", cases)
    monkeypatch.setattr(paths, "TS_OUT", ts)
    with pytest.raises(SystemExit):
        bw.build_case(CASE)


def _nodule_mask(tmp_path: Path, boxes) -> tuple[Path, Path]:
    cases, ts = _case_tree(tmp_path)
    mask = np.zeros(SHAPE, dtype=np.uint8)
    for lo, hi in boxes:
        mask[lo[0]:hi[0], lo[1]:hi[1], lo[2]:hi[2]] = 1
    _save(ts / CASE / "nodule.nii.gz", mask)
    return cases, ts


def _instance_records() -> list[dict]:
    return [
        {"id": 1, "center_ras_mm": [6.0, 6.0, 6.0], "volume_mm3": 125.0,
         "diameter_mm": 6.2, "axial_diameter_mm": 5.0,
         "lobe": "lung_upper_lobe_left", "to_airway_mm": 4.5,
         "score": 0.87, "match": "mask_and_box"},
        {"id": 2, "center_ras_mm": [32.0, 26.0, 26.0], "volume_mm3": 125.0,
         "diameter_mm": 6.4, "axial_diameter_mm": 5.2,
         "lobe": "lung_lower_lobe_right", "to_airway_mm": 7.5,
         "score": None, "match": "mask_only"},
    ]


def _mesh_span(path: Path, layer: dict) -> np.ndarray:
    raw = np.frombuffer(path.read_bytes(), dtype=np.float32)
    vertices = raw[:layer["vertices"] * 3].reshape(-1, 3)
    return vertices.max(axis=0) - vertices.min(axis=0)


def test_two_nodules_get_one_layer_and_mesh_each(tmp_path, monkeypatch):
    cases, ts = _nodule_mask(tmp_path, [((4, 4, 4), (9, 9, 9)),
                                        ((30, 24, 24), (35, 29, 29))])
    (ts / CASE / "nodule_instances.json").write_text(
        json.dumps({"nodules": _instance_records()}))
    monkeypatch.setattr(paths, "LOCAL_CASES", cases)
    monkeypatch.setattr(paths, "TS_OUT", ts)

    out = bw.build_case(CASE)
    scene = json.loads((out / "scene.json").read_text())

    names = [layer["name"] for layer in scene["layers"]]
    assert names == ["lungs", "heart", "arteries", "veins", "airways",
                     "nodule", "nodule"]
    nodules = [layer for layer in scene["layers"] if layer["name"] == "nodule"]
    records = _instance_records()
    assert [layer["id"] for layer in nodules] == [1, 2]

    for layer, record in zip(nodules, records):
        assert layer["file"] == f"nodule-{layer['id']}.bin"
        assert layer["color"] == "#f2b632"
        assert layer["opacity"] == 1.0
        assert layer["volume_mm3"] == record["volume_mm3"]
        assert layer["diameter_mm"] == record["diameter_mm"]
        assert layer["score"] == record["score"]
        assert layer["lobe"] == record["lobe"]
        assert layer["to_airway_mm"] == record["to_airway_mm"]
        assert layer["match"] == record["match"]
        assert len(layer["center_mm"]) == 3
        assert layer["vertices"] > 0 and layer["triangles"] > 0
        data = (out / layer["file"]).read_bytes()
        assert len(data) == layer["vertices"] * 12 + layer["triangles"] * 12
        # Each mesh is built from its own component crop, not the merged mask.
        assert _mesh_span(out / layer["file"], layer).max() < 20.0


def test_nodule_measurements_fall_back_to_the_mask(tmp_path, monkeypatch):
    cases, ts = _nodule_mask(tmp_path, [((4, 4, 4), (9, 9, 9))])
    monkeypatch.setattr(paths, "LOCAL_CASES", cases)
    monkeypatch.setattr(paths, "TS_OUT", ts)

    out = bw.build_case(CASE)
    nodules = [layer for layer in json.loads((out / "scene.json").read_text())["layers"]
               if layer["name"] == "nodule"]

    assert len(nodules) == 1
    assert nodules[0]["id"] == 1
    assert nodules[0]["volume_mm3"] == 125.0
    assert nodules[0]["diameter_mm"] > 0.0
    assert nodules[0]["score"] is None
    assert nodules[0]["match"] is None


def test_no_nodule_mask_adds_no_scene_entry(tmp_path, monkeypatch):
    cases, ts = _case_tree(tmp_path)
    monkeypatch.setattr(paths, "LOCAL_CASES", cases)
    monkeypatch.setattr(paths, "TS_OUT", ts)

    out = bw.build_case(CASE)
    scene = json.loads((out / "scene.json").read_text())

    assert [layer["name"] for layer in scene["layers"]] == [
        "lungs", "heart", "arteries", "veins", "airways"]
    assert not list(out.glob("nodule-*.bin"))
