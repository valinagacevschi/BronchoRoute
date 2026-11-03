# Changes: no-lungs after an airway-model log line still names segmentation.
# Changes: all-zero and missing lobe masks fail as segmentation: no lungs found.
"""Empty lung masks must stop segmentation before a Case is built."""
from __future__ import annotations

import nibabel as nib
import numpy as np
import pytest

import run_totalsegmentator as segmentation


def _write_lobes(folder, fill):
    folder.mkdir(parents=True, exist_ok=True)
    for name in segmentation.LOBES:
        data = np.zeros((2, 2, 2), dtype=np.uint8)
        fill(name, data)
        nib.save(nib.Nifti1Image(data, np.eye(4)), folder / f"{name}.nii.gz")


def test_merge_lobes_all_zero_is_no_lungs(tmp_path):
    seg = tmp_path / "lobes"
    _write_lobes(seg, lambda _name, _data: None)
    with pytest.raises(SystemExit, match="segmentation: no lungs found"):
        segmentation.merge_lobes(seg, tmp_path / "lobes.nii.gz")
    assert not (tmp_path / "lobes.nii.gz").exists()


def test_merge_lobes_missing_masks_are_no_lungs(tmp_path):
    with pytest.raises(SystemExit, match="segmentation: no lungs found"):
        segmentation.merge_lobes(tmp_path / "absent", tmp_path / "lobes.nii.gz")


def test_no_lungs_after_airway_model_names_segmentation():
    import repo_root
    repo_root.ensure()
    from app.jobs import JobState, _failure_message

    job = JobState("local-001", "segment")
    job.log_lines = [
        "step: reading",
        "step: segmentation",
        "step: airway model",
        "segmentation: no lungs found",
    ]
    assert _failure_message(job) == (
        "segmentation step failed: no lungs found. Try another Series"
    )


def test_merge_lobes_keeps_one_lung_voxel(tmp_path):
    seg = tmp_path / "lobes"
    names = list(segmentation.LOBES)
    _write_lobes(seg, lambda name, data: data.__setitem__((0, 0, 0), 1) if name == names[0] else None)
    out = tmp_path / "lobes.nii.gz"
    segmentation.merge_lobes(seg, out)
    merged = np.asanyarray(nib.load(out).dataobj)
    assert int(merged[0, 0, 0]) == segmentation.LOBES[names[0]]
