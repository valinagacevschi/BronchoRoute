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
         "--roi_subset", *segmentation.ROIS, "--device", "mps", *suffix],
    ]
