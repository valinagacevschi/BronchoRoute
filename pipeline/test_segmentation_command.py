# Changes: licensed TotalSegmentator tasks are refused before a command runs.
# Changes: pin both segmentation passes to the current Python without PATH scripts.
"""Capture segmentation commands with synthetic input and no model execution."""

import subprocess
import sys

import pytest

import run_totalsegmentator as segmentation


@pytest.mark.parametrize("legacy", [False, True])
@pytest.mark.parametrize("extra_flags", [[], ["--fast", "--higher-order"]])
def test_segmentation_passes_use_current_python(tmp_path, monkeypatch, legacy, extra_flags):
    ct = tmp_path / "synthetic.nii.gz"
    ct.write_bytes(b"synthetic input; image processing is stubbed")
    out = tmp_path / "segmentation"
    commands = []

    def capture(cmd):
        commands.append(cmd)
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(segmentation.subprocess, "run", capture)
    monkeypatch.setattr(segmentation, "merge_lobes", lambda *args: None)
    monkeypatch.setattr(segmentation, "make_airway", lambda *args: None)
    flags = ["--legacy"] if legacy else []
    monkeypatch.setattr(sys, "argv", [
        "run_totalsegmentator.py", str(ct), "-o", str(out), "--device", "mps",
        *flags, *extra_flags,
    ])

    segmentation.main()

    prefix = [sys.executable, "-m", "totalsegmentator.bin.TotalSegmentator"]
    suffix = ["--fast", "--higher_order_resampling"] if extra_flags else []
    task = "lung_vessels_LEGACY" if legacy else "lung_vessels"
    assert commands == [
        [*prefix, "-i", str(ct), "-o", str(out / "airways_source"),
         "--task", task, "--device", "mps", *suffix],
        [*prefix, "-i", str(ct), "-o", str(out / "lobes"),
         "--task", "total_v2", "--roi_subset", *segmentation.ROIS,
         "--device", "mps", *suffix],
    ]


def test_licensed_tasks_are_refused():
    for task in ("heartchambers_highres", "tissue_types"):
        with pytest.raises(SystemExit, match=task):
            segmentation.require_task(task)
    for task in ("lung_vessels", "lung_vessels_LEGACY", "total", "total_v2"):
        assert segmentation.require_task(task) == task
    with pytest.raises(SystemExit, match="license_number"):
        segmentation.guard_command([
            sys.executable, "-m", "totalsegmentator.bin.TotalSegmentator",
            "--task", "total", "--license_number", "secret",
        ])
