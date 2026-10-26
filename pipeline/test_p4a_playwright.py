# Changes: uses build_tmp_pipeline (per-file ts_out symlinks, never dir links).
# Phase 4a playwright: local-001 step-1 confirmable; synthetic DICOM scan under
# fake HOME (never ~/Downloads/dicom). Screenshots → out/p4a_shots/.
from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path

import numpy as np
import pytest
from playwright.sync_api import sync_playwright
from termcolor import cprint

from test_p3_playwright import build_tmp_pipeline

ROOT = Path(__file__).resolve().parent
SHOTS = ROOT / "out" / "p4a_shots"
PORT = 8767
BASE = f"http://127.0.0.1:{PORT}"
VIEWPORT = {"width": 1400, "height": 900}


def _wait_server(timeout: float = 90.0) -> None:
    import urllib.request

    deadline = time.time() + timeout
    last_err = None
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(f"{BASE}/api/cases", timeout=2) as res:
                if res.status == 200:
                    return
        except Exception as exc:  # noqa: BLE001
            last_err = exc
            time.sleep(0.4)
    raise RuntimeError(f"server did not start: {last_err}")


def _write_tiny_dicom_series(folder: Path, n: int = 3) -> None:
    """Minimal DICOM files for scan table (will be skipped: <100 slices)."""
    pydicom = pytest.importorskip("pydicom")
    from pydicom.dataset import Dataset, FileDataset

    folder.mkdir(parents=True, exist_ok=True)
    uid = "1.2.840.10008.1.2.1.999"
    for i in range(n):
        path = folder / f"IM{i:04d}.dcm"
        file_meta = Dataset()
        file_meta.MediaStorageSOPClassUID = "1.2.840.10008.5.1.4.1.1.2"
        file_meta.MediaStorageSOPInstanceUID = f"{uid}.{i}"
        file_meta.TransferSyntaxUID = "1.2.840.10008.1.2"
        ds = FileDataset(str(path), {}, file_meta=file_meta, preamble=b"\0" * 128)
        ds.SOPClassUID = "1.2.840.10008.5.1.4.1.1.2"
        ds.SOPInstanceUID = f"{uid}.{i}"
        ds.SeriesInstanceUID = uid
        ds.StudyInstanceUID = "1.2.3.4.5"
        ds.Modality = "CT"
        ds.SeriesNumber = 1
        ds.SeriesDescription = "SYNTHETIC TINY"
        ds.ConvolutionKernel = "STANDARD"
        ds.SliceThickness = 1.0
        ds.PixelSpacing = [1.0, 1.0]
        ds.ImageOrientationPatient = [1, 0, 0, 0, 1, 0]
        ds.ImagePositionPatient = [0.0, 0.0, float(i)]
        ds.Rows = 8
        ds.Columns = 8
        ds.BitsAllocated = 16
        ds.BitsStored = 16
        ds.HighBit = 15
        ds.PixelRepresentation = 1
        ds.SamplesPerPixel = 1
        ds.PhotometricInterpretation = "MONOCHROME2"
        ds.RescaleSlope = 1
        ds.RescaleIntercept = -1024
        ds.PixelData = np.zeros((8, 8), dtype=np.int16).tobytes()
        ds.is_little_endian = True
        ds.is_implicit_VR = True
        ds.save_as(path)


@pytest.fixture(scope="module")
def tmp_pipeline(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("p4a_pipeline")
    root = build_tmp_pipeline(tmp)
    # Already-imported local case with source but no series_confirmed → step 1 available.
    case_path = root / "data" / "extracted" / "local" / "local-001" / "case.json"
    case = json.loads(case_path.read_text())
    case["source"] = {
        "series_uid_sha256": "deadbeef" * 8,
        "folder": "SERIES0",
        "series_number": 1,
        "series_description": "test",
        "convolution_kernel": "STANDARD",
        "spacing_mm": [1.0, 1.0, 1.5],
        "size": [24, 24, 16],
    }
    case["records"] = []
    case_path.write_text(json.dumps(case, indent=2) + "\n")

    home = root / "fake_home"
    home.mkdir()
    dicom = home / "dicom_synth" / "SERIES0"
    _write_tiny_dicom_series(dicom)
    return {"root": root, "home": home, "dicom": dicom.parent}


@pytest.fixture(scope="module")
def server(tmp_pipeline):
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    env["LUNGNAV_PIPELINE_ROOT"] = str(tmp_pipeline["root"])
    env["HOME"] = str(tmp_pipeline["home"])
    cprint(f"p4a server on {PORT} root={tmp_pipeline['root']}", "cyan")
    proc = subprocess.Popen(
        [
            "env", "-u", "PYTHONPATH",
            "uv", "run", "python", "app_server.py", "--port", str(PORT),
        ],
        cwd=str(ROOT),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    try:
        _wait_server()
        yield {"proc": proc, "pipeline": tmp_pipeline}
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


def _attach_console(page) -> None:
    page._p4a_errors = []

    def on_msg(msg):
        if msg.type == "error":
            page._p4a_errors.append(msg.text)

    def on_page_error(err):
        page._p4a_errors.append(str(err))

    page.on("console", on_msg)
    page.on("pageerror", on_page_error)


def test_p4a_import_panel_and_workflow(server):
    SHOTS.mkdir(parents=True, exist_ok=True)
    dicom = server["pipeline"]["dicom"]

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport=VIEWPORT)
        _attach_console(page)

        page.request.put(
            f"{BASE}/api/clinician",
            data='{"role":"Radiologist","initials":"A.B."}',
            headers={"Content-Type": "application/json"},
        )

        cprint("open app shell local-001 (step 1 confirmable)", "cyan")
        page.goto(
            f"{BASE}/?case=local-001&allow_review=1",
            wait_until="domcontentloaded",
            timeout=120_000,
        )
        page.wait_for_selector("#workflow .step", timeout=60_000)

        steps = page.locator("#workflow .step")
        assert steps.count() == 8
        first = page.locator("#workflow .step").nth(0)
        assert "available" in (first.get_attribute("class") or "")
        page.screenshot(path=str(SHOTS / "local-001_workflow.png"), full_page=True)

        page.click("#toggle-import")
        page.wait_for_selector("#import-panel.visible")
        confirm_existing = page.locator("#confirm-existing-series")
        assert confirm_existing.is_visible()
        assert confirm_existing.is_enabled()
        page.screenshot(path=str(SHOTS / "local-001_step1_confirmable.png"), full_page=True)

        # Synthetic series under fake HOME — never real ~/Downloads/dicom.
        page.fill("#import-src", str(dicom))
        cprint(f"scan synthetic {dicom}", "cyan")
        page.click("#import-scan")
        # Tiny series → 422 (no eligible CT); message shown in the panel.
        page.wait_for_function(
            """() => {
              const m = document.querySelector('#import-msg');
              return m && m.textContent && m.textContent.trim().length > 0
                && !m.textContent.includes('Scanning');
            }""",
            timeout=60_000,
        )
        msg = page.locator("#import-msg").inner_text().lower()
        assert "eligible" in msg or "series" in msg or "found" in msg
        page.screenshot(path=str(SHOTS / "import_scan_synthetic.png"), full_page=True)

        errors = [
            e for e in getattr(page, "_p4a_errors", [])
            if "422" not in e  # expected: tiny synthetic series is ineligible
        ]
        assert errors == [], errors
        browser.close()
    cprint(f"shots in {SHOTS}", "green")
