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
