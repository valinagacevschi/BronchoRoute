# Changes: use eligible full-thorax Series in import override coverage.
# Phase 4a review-fix tests: ordering-aware workflow, series/confirm,
# register-existing, /records 403, jobs restart/timeout/redaction, import
# isolation under fake HOME. Never writes ~/.lungnav* or /tmp/lungnav-*.
from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path

import numpy as np
import pytest
from fastapi.testclient import TestClient
from termcolor import cprint

import repo_root

repo_root.ensure()

from app.jobs import (  # noqa: E402
    redact_log_line,
    reset_jobs_for_tests,
    set_job_launcher,
    set_job_timeout_s,
)
from app.server import app  # noqa: E402
from app.versions import register_artifact, sha256_file  # noqa: E402
from app.workflow import AEROPATH_STEP1_REASON, SEGMENT_ARTIFACT_NAMES  # noqa: E402
from run_totalsegmentator import ct_identity  # noqa: E402
from test_app_server import _write_aeropath_case, _write_case_tree  # noqa: E402


@pytest.fixture
def harness(tmp_path, monkeypatch):
    cprint(f"p4a harness at {tmp_path}", "cyan")
    paths = _write_case_tree(tmp_path)
    graph = json.loads(paths["graph"].read_text())
    graph["review"] = []
    paths["graph"].write_text(json.dumps(graph))
    (paths["ts"] / "airway_bridges.json").write_text(json.dumps({
        "note": "interpolated, not CT-demonstrated airway",
        "radius_mm": 1.0,
        "bridges": [],
    }))
    # Source block so series/confirm can stamp from an already-imported case.
    case = json.loads(paths["case"].read_text())
    case["source"] = {
        "series_uid_sha256": "abc" * 21 + "ab",
        "folder": "SERIES0",
        "series_number": 1,
        "series_description": "test",
        "convolution_kernel": "STANDARD",
        "spacing_mm": [1.0, 1.0, 1.5],
        "size": [24, 24, 16],
    }
    paths["case"].write_text(json.dumps(case, indent=2) + "\n")

    monkeypatch.setattr("paths.PIPELINE", tmp_path)
    monkeypatch.setattr("paths.LOCAL_CASES", tmp_path / "data/extracted/local")
    monkeypatch.setattr("paths.AEROPATH", tmp_path / "data/extracted/AeroPath")
    monkeypatch.setattr("paths.TS_OUT", tmp_path / "ts_out")
    monkeypatch.setattr("paths.OUT", tmp_path / "out")
    (tmp_path / "data/extracted/AeroPath").mkdir(parents=True, exist_ok=True)
    (tmp_path / "app_state").mkdir(parents=True, exist_ok=True)
    reset_jobs_for_tests()
    set_job_launcher(None)
    set_job_timeout_s(None)
    return paths


@pytest.fixture
def client(harness):
    return TestClient(app, base_url="http://127.0.0.1")


@pytest.fixture
def fake_home(tmp_path, monkeypatch):
    home = tmp_path / "fake_home"
    home.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    monkeypatch.setenv("HOME", str(home))
    return home


def _set_clinician(client, role="Radiologist", initials="A.B."):
    res = client.put("/api/clinician", json={"role": role, "initials": initials})
    assert res.status_code == 200, res.text
    return res.json()


def _register_segment(case: dict, harness) -> None:
    register_artifact(case, "ct", harness["ct"])
    for name, path in [
        ("airways", harness["ts"] / "airways.nii.gz"),
        ("bridges_mask", harness["ts"] / "airway_bridges.nii.gz"),
        ("bridges_json", harness["ts"] / "airway_bridges.json"),
        ("lobes", harness["ts"] / "lobes.nii.gz"),
        ("graph", harness["graph"]),
    ]:
        register_artifact(case, name, path)


def _seed_through_step(client, harness, step: int, *, at_base: str = "2026-01-01T00:00:0"):
    """Advance synthetic case so priors for `step` are done (ordered timestamps)."""
    case_path = harness["case"]
    case = json.loads(case_path.read_text())
    records = case.setdefault("records", [])
    if step > 1:
        records.append({
            "step": 1, "kind": "series_confirmed",
            "data": {
                "folder": "SERIES0", "series_number": 1,
                "description": "test", "kernel": "STANDARD", "overridden": False,
            },
            "clinician": "Radiologist, A.B.", "at": f"{at_base}0Z",
        })
    if step > 2:
        meta = client.get("/api/cases/local-001/ct/meta").json()
        affine = np.asarray(meta["affine"], dtype=float)
        ijk = [meta["shape"][0] / 2, meta["shape"][1] / 2, meta["shape"][2] / 2]
        ras = (affine @ np.array([ijk[0], ijk[1], ijk[2], 1.0]))[:3]
        case["lesion"] = {
            "ras_mm": [float(ras[0]), float(ras[1]), float(ras[2])],
            "confirmed_by": "Radiologist, A.B.",
            "at": f"{at_base}1Z",
        }
        records.append({
            "step": 2, "kind": "lesion_confirmed",
            "data": {"ras_mm": case["lesion"]["ras_mm"]},
            "clinician": "Radiologist, A.B.", "at": f"{at_base}1Z",
        })
    if step > 3:
        _register_segment(case, harness)
        records.append({
            "step": 3, "kind": "segmentation_done",
            "data": {"artifacts": list(SEGMENT_ARTIFACT_NAMES)},
            "clinician": "Radiologist, A.B.", "at": f"{at_base}2Z",
        })
    if step > 4:
        pass  # vacuous review once segment is done
    if step > 5:
        records.append({
            "step": 5, "kind": "route_start_confirmed",
            "data": {"node": 0, "ras_mm": [0.0, 0.0, 0.0], "moved": False},
            "clinician": "Radiologist, A.B.", "at": f"{at_base}3Z",
        })
        case["route_start"] = {
            "node": 0, "ras_mm": [0.0, 0.0, 0.0],
            "confirmed_by": "Radiologist, A.B.", "at": f"{at_base}3Z",
        }
    if step > 6:
        records.append({
            "step": 6, "kind": "route_target_chosen",
            "data": {
                "node": 1, "edge_id": 0, "sample_index": 0,
                "ras_mm": [0.0, 0.0, -20.0], "rank": 1,
                "target_gap_mm": 0.0, "target_inside_airway": True,
            },
            "clinician": "Radiologist, A.B.", "at": f"{at_base}4Z",
        })
        case["route_target"] = {
            "node": 1, "edge_id": 0, "sample_index": 0,
            "ras_mm": [0.0, 0.0, -20.0], "rank": 1,
            "target_gap_mm": 0.0, "target_inside_airway": True,
            "confirmed_by": "Radiologist, A.B.", "at": f"{at_base}4Z",
        }
    if step > 7:
        # Graph has root→1 with no bifurcation (degree-1 terminals only on route).
        pass
    if step > 8:
        if case.get("versions"):
            case["versions"][-1]["frozen"] = True
            case["versions"][-1]["frozen_at"] = f"{at_base}5Z"
        records.append({
            "step": 8, "kind": "signoff",
            "data": {},
            "clinician": "Radiologist, A.B.", "at": f"{at_base}5Z",
        })
    case_path.write_text(json.dumps(case, indent=2) + "\n")


def test_workflow_empty_then_progress(client, harness):
    body = client.get("/api/cases/local-001/workflow").json()
    assert body["steps"][0]["status"] == "available"
    assert body["steps"][1]["status"] == "blocked"
    assert "step 1" in body["steps"][1]["reason"]

    _seed_through_step(client, harness, 2)
    body = client.get("/api/cases/local-001/workflow").json()
    assert body["steps"][0]["status"] == "done"
    assert body["steps"][1]["status"] == "available"

    _seed_through_step(client, harness, 3)
    body = client.get("/api/cases/local-001/workflow").json()
    assert body["steps"][1]["status"] == "done"
    # No registration → step 3 available (files alone do not count).
    assert body["steps"][2]["status"] == "available"


def test_per_step_derivation_through_signoff(client, harness):
    _set_clinician(client)
    _seed_through_step(client, harness, 9)
    body = client.get("/api/cases/local-001/workflow").json()
    statuses = [s["status"] for s in body["steps"]]
    # Steps 1–8: walk is vacuous (no bifurcations) once target chosen.
    assert statuses[:6] == ["done"] * 6
    assert statuses[6] == "done"  # walk vacuous
    assert statuses[7] == "done"  # signoff


def test_out_of_order_segment_409(client, harness):
    _set_clinician(client)
    res = client.post("/api/cases/local-001/segment")
    assert res.status_code == 409
    assert "step 1" in res.json()["detail"]


def test_out_of_order_route_start_409(client, harness):
    _set_clinician(client)
    res = client.put("/api/cases/local-001/route-start", json={"node": 0})
    assert res.status_code == 409


def test_back_step_invalidates_downstream(client, harness):
    _set_clinician(client)
    _seed_through_step(client, harness, 6)
    body = client.get("/api/cases/local-001/workflow").json()
    assert body["steps"][4]["status"] == "done"

    res = client.delete("/api/cases/local-001/lesion")
    assert res.status_code == 200
    body = client.get("/api/cases/local-001/workflow").json()
    assert body["steps"][1]["status"] == "available"
    assert body["steps"][4]["status"] == "blocked"
    case = json.loads(harness["case"].read_text())
    kinds = [r["kind"] for r in case["records"]]
    assert "route_start_confirmed" in kinds
    assert "lesion_cleared" in kinds


def test_ordering_invalidation_reconfirm_lesion(client, harness):
    """Re-confirm lesion after route start → steps 3+ no longer done."""
    _set_clinician(client)
    _seed_through_step(client, harness, 6)
    body = client.get("/api/cases/local-001/workflow").json()
    assert body["steps"][2]["status"] == "done"
    assert body["steps"][4]["status"] == "done"

    meta = client.get("/api/cases/local-001/ct/meta").json()
    affine = np.asarray(meta["affine"], dtype=float)
    ijk = [meta["shape"][0] / 2 + 0.2, meta["shape"][1] / 2, meta["shape"][2] / 2]
    ras = (affine @ np.array([ijk[0], ijk[1], ijk[2], 1.0]))[:3]
    res = client.put("/api/cases/local-001/lesion", json={
        "ras_mm": [float(ras[0]), float(ras[1]), float(ras[2])],
    })
    assert res.status_code == 200, res.text
    body = client.get("/api/cases/local-001/workflow").json()
    assert body["steps"][1]["status"] == "done"
    assert body["steps"][2]["status"] == "available"  # segment record now stale
    assert body["steps"][4]["status"] == "blocked"


def test_records_403_for_completion_kinds(client, harness):
    _set_clinician(client)
    _seed_through_step(client, harness, 2)
    for kind, step, data in [
        ("series_confirmed", 1, {
            "folder": "S", "series_number": 1, "description": "d",
            "kernel": "k", "overridden": False,
        }),
        ("segmentation_done", 3, {}),
        ("route_start_confirmed", 5, {"node": 0, "ras_mm": [0, 0, 0], "moved": False}),
        ("lesion_confirmed", 2, {"ras_mm": [0, 0, 0]}),
        ("signoff", 8, {}),
        ("bridge_decision", 4, {"decision": "confirmed"}),
    ]:
        res = client.post("/api/cases/local-001/records", json={
            "step": step, "kind": kind, "data": data,
        })
        assert res.status_code == 403, kind
        assert "step endpoint" in res.json()["detail"]

    res = client.post("/api/cases/local-001/records", json={
        "step": 2, "kind": "note", "data": {"text": "ok"},
    })
    assert res.status_code == 200, res.text


def test_lesion_409_before_step1(client, harness):
    _set_clinician(client)
    res = client.put("/api/cases/local-001/lesion", json={"ras_mm": [0.0, 0.0, 0.0]})
    assert res.status_code == 409
    assert "step 1" in res.json()["detail"]


def test_series_confirm_endpoint(client, harness):
    _set_clinician(client)
    body = client.get("/api/cases/local-001/workflow").json()
    assert body["steps"][0]["status"] == "available"
    res = client.post("/api/cases/local-001/series/confirm", json={"overridden": False})
    assert res.status_code == 200, res.text
    assert res.json()["record"]["kind"] == "series_confirmed"
    assert res.json()["record"]["data"]["folder"] == "SERIES0"
    assert res.json()["record"]["data"]["overridden"] is False
    body = client.get("/api/cases/local-001/workflow").json()
    assert body["steps"][0]["status"] == "done"


def test_register_existing_endpoint(client, harness):
    _set_clinician(client)
    _seed_through_step(client, harness, 3)
    # Markers matching current CT identity.
    ident = ct_identity(harness["ct"])
    for sub in ("airways_source", "lobes"):
        d = harness["ts"] / sub
        d.mkdir(parents=True, exist_ok=True)
        (d / "run.json").write_text(json.dumps({
            "ct": ident, "device": "mps", "fast": False,
        }) + "\n")

    res = client.post("/api/cases/local-001/segment/register-existing")
    assert res.status_code == 200, res.text
    assert res.json()["registered_existing"] is True
    assert res.json()["record"]["data"]["registered_existing"] is True
    body = client.get("/api/cases/local-001/workflow").json()
    assert body["steps"][2]["status"] == "done"

    case = json.loads(harness["case"].read_text())
    arts = case["versions"][-1]["artifacts"]
    assert "ct" in arts
    assert arts["ct"]["sha256"] == sha256_file(harness["ct"])
    for name in SEGMENT_ARTIFACT_NAMES:
        assert name in arts


def test_register_existing_blocked_until_step2(client, harness):
    _set_clinician(client)
    res = client.post("/api/cases/local-001/segment/register-existing")
    assert res.status_code == 409


def test_aeropath_step1_done(client, harness, tmp_path, monkeypatch):
    aero = _write_aeropath_case(tmp_path, "3")
    monkeypatch.setattr("paths.AEROPATH", tmp_path / "data/extracted/AeroPath")
    monkeypatch.setattr("paths.OUT", tmp_path / "out")
    body = client.get("/api/cases/3/workflow").json()
    assert body["steps"][0]["status"] == "done"
    assert body["steps"][0]["reason"] == AEROPATH_STEP1_REASON


def test_import_scan_outside_home_422(client, harness, fake_home, tmp_path):
    _set_clinician(client)
    outside = tmp_path / "outside_home_scan"
    outside.mkdir()
    res = client.post("/api/imports/scan", json={"src_dir": str(outside)})
    assert res.status_code == 422
    assert "home" in res.json()["detail"].lower()


def test_import_scan_empty_under_fake_home(client, harness, fake_home):
    _set_clinician(client)
    empty = fake_home / "empty_dicom"
    empty.mkdir()
    res = client.post("/api/imports/scan", json={"src_dir": str(empty)})
    assert res.status_code == 422


def test_import_happy_duplicate_and_override(client, harness, fake_home, monkeypatch):
    """Import endpoint: happy path, duplicate 409, overridden flag."""
    _set_clinician(client)
    pydicom = pytest.importorskip("pydicom")
    from pydicom.dataset import Dataset, FileDataset

    def _write_series(folder: Path, series_number: int, uid: str, n: int = 251):
        folder.mkdir(parents=True, exist_ok=True)
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
            ds.SeriesNumber = series_number
            ds.SeriesDescription = "AXIAL STANDARD"
            ds.ConvolutionKernel = ["Bf37f", "3"]
            ds.Manufacturer = "SIEMENS"
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
            ds.PixelData = (np.zeros((8, 8), dtype=np.int16)).tobytes()
            ds.is_little_endian = True
            ds.is_implicit_VR = True
            ds.save_as(path)

    src = fake_home / "dicom_src"
    _write_series(src / "THIN", 1, "1.2.840.10008.1.2.1.100", n=251)
    # Alternate usable Series has 1.5 mm steps; auto-pick prefers 1 mm.
    thick = src / "THICK"
    thick.mkdir(parents=True, exist_ok=True)
    for i in range(200):
        path = thick / f"IM{i:04d}.dcm"
        file_meta = pydicom.dataset.Dataset()
        file_meta.MediaStorageSOPClassUID = "1.2.840.10008.5.1.4.1.1.2"
        file_meta.MediaStorageSOPInstanceUID = f"1.2.840.10008.1.2.1.200.{i}"
        file_meta.TransferSyntaxUID = "1.2.840.10008.1.2"
        ds = pydicom.dataset.FileDataset(
            str(path), {}, file_meta=file_meta, preamble=b"\0" * 128
        )
        ds.SOPClassUID = "1.2.840.10008.5.1.4.1.1.2"
        ds.SOPInstanceUID = f"1.2.840.10008.1.2.1.200.{i}"
        ds.SeriesInstanceUID = "1.2.840.10008.1.2.1.200"
        ds.StudyInstanceUID = "1.2.3.4.5"
        ds.Modality = "CT"
        ds.SeriesNumber = 2
        ds.SeriesDescription = "AXIAL"
        ds.Manufacturer = "SIEMENS"
        ds.ConvolutionKernel = "STANDARD"
        ds.SliceThickness = 1.5
        ds.PixelSpacing = [1.0, 1.0]
        ds.ImageOrientationPatient = [1, 0, 0, 0, 1, 0]
        ds.ImagePositionPatient = [0.0, 0.0, float(i) * 1.5]
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
        ds.PixelData = (np.zeros((8, 8), dtype=np.int16)).tobytes()
        ds.is_little_endian = True
        ds.is_implicit_VR = True
        ds.save_as(path)

    scan = client.post("/api/imports/scan", json={"src_dir": str(src)})
    assert scan.status_code == 200, scan.text
    assert scan.json()["proposed"]["folder"] == "THIN"
    assert scan.json()["proposed"]["kernel"] == "Bf37f"
    assert scan.json()["proposed"]["status"] == "recommended"
    assert scan.json()["proposed"]["z_span_mm"] == 250.0

    # Override with folder (unique).
    res = client.post("/api/imports", json={
        "src_dir": str(src), "series": "THICK", "confirm": True,
    })
    assert res.status_code == 200, res.text
    assert res.json()["record"]["data"]["overridden"] is True
    assert res.json()["record"]["data"]["folder"] == "THICK"
    assert res.json()["source"]["status"] == "usable"
    assert res.json()["record"]["data"]["override_reason"]
    case_id = res.json()["case_id"]

    # Duplicate → 409
    dup = client.post("/api/imports", json={
        "src_dir": str(src), "series": "THICK", "confirm": True,
    })
    assert dup.status_code == 409
    assert "already imported" in dup.json()["detail"]

    # Happy non-override path on a fresh series tree.
    src2 = fake_home / "dicom_src2"
    _write_series(src2 / "ONLY", 3, "1.2.840.10008.1.2.1.300", n=251)
    ok = client.post("/api/imports", json={
        "src_dir": str(src2), "series": "ONLY", "confirm": True,
    })
    assert ok.status_code == 200, ok.text
    assert ok.json()["record"]["data"]["overridden"] is False
    assert case_id.startswith("local-")


def test_error_redaction_forms():
    cases = [
        ('failed data/extracted/local/local-001/ct.nii.gz', "ct.nii.gz"),
        ('path "/data/extracted/x/y.nii.gz" end', "y.nii.gz"),
        ("path 'data/foo/bar.nii.gz' end", "bar.nii.gz"),
        ("msg (data/extracted/local/a.nii.gz) done", "a.nii.gz"),
        ("--ct=/data/extracted/local/ct.nii.gz", "ct.nii.gz"),
        ("--out data/extracted/local/out", "out"),
        ("relative ./data/extracted/z.nii.gz ok", "z.nii.gz"),
        ("abs /Users/x/data/extracted/local/ct.nii.gz", "ct.nii.gz"),
    ]
    for raw, basename in cases:
        out = redact_log_line(raw)
        assert "data/" not in out or out.endswith(basename) or basename in out, (raw, out)
        assert basename in out
        assert "/extracted/" not in out


def test_segment_job_lifecycle(client, harness):
    _set_clinician(client)
    _seed_through_step(client, harness, 3)

    def fake_launcher(cmd, cwd):
        class Proc:
            def __init__(self):
                self.pid = 424242
                self.stdout = iter([
                    "fake segment ok\n",
                    "path data/extracted/local/local-001/ct.nii.gz\n",
                ])
                self._code = 0

            def wait(self, timeout=None):
                return self._code

        return Proc()

    set_job_launcher(fake_launcher)
    res = client.post("/api/cases/local-001/segment")
    assert res.status_code == 200, res.text

    for _ in range(50):
        latest = client.get("/api/cases/local-001/jobs/latest").json()
        if latest["status"] in {"succeeded", "failed"}:
            break
        time.sleep(0.05)
    latest = client.get("/api/cases/local-001/jobs/latest").json()
    assert latest["status"] == "succeeded", latest
    joined = "\n".join(latest["log_tail"])
    assert "data/extracted" not in joined
    assert "ct.nii.gz" in joined

    case = json.loads(harness["case"].read_text())
    arts = case["versions"][-1]["artifacts"]
    assert "ct" in arts
    for name in SEGMENT_ARTIFACT_NAMES:
        assert name in arts
    assert any(r["kind"] == "segmentation_done" for r in case["records"])

    reset_jobs_for_tests()

    def slow_launcher(cmd, cwd):
        class Proc:
            def __init__(self):
                self.pid = 434343
                self.stdout = iter(["slow\n"])

            def wait(self, timeout=None):
                time.sleep(0.4)
                return 0

        return Proc()

    set_job_launcher(slow_launcher)
    assert client.post("/api/cases/local-001/segment").status_code == 200
    conflict = client.post("/api/cases/local-001/segment")
    assert conflict.status_code == 409
    for _ in range(40):
        if client.get("/api/cases/local-001/jobs/latest").json()["status"] != "running":
            break
        time.sleep(0.05)


def test_job_restart_detection(client, harness):
    _set_clinician(client)
    _seed_through_step(client, harness, 3)
    job_path = harness["ts"] / "job.json"
    job_path.parent.mkdir(parents=True, exist_ok=True)
    job_path.write_text(json.dumps({
        "case_id": "local-001",
        "kind": "segment",
        "status": "running",
        "pid": 1,  # unlikely to be a live unrelated process we own; may be alive on macOS
        "started_at": time.monotonic(),
        "finished_at": None,
        "error": None,
        "wall_start": "2026-01-01T00:00:00Z",
    }) + "\n")
    # Use a pid that is definitely dead.
    dead_pid = 2_000_000_001
    while True:
        try:
            os.kill(dead_pid, 0)
            dead_pid += 1
        except ProcessLookupError:
            break
        except PermissionError:
            dead_pid += 1
    job_path.write_text(json.dumps({
        "case_id": "local-001",
        "kind": "segment",
        "status": "running",
        "pid": dead_pid,
        "started_at": 1.0,
        "finished_at": None,
        "error": None,
        "wall_start": "2026-01-01T00:00:00Z",
    }) + "\n")
    reset_jobs_for_tests()
    latest = client.get("/api/cases/local-001/jobs/latest").json()
    assert latest["status"] == "failed"
    assert "server restarted" in (latest.get("error") or "")


def test_job_timeout_kills_process_group(client, harness):
    _set_clinician(client)
    _seed_through_step(client, harness, 3)
    set_job_timeout_s(0.3)

    def long_launcher(cmd, cwd):
        return subprocess.Popen(
            ["python3", "-c", "import time; time.sleep(30)"],
            cwd=str(cwd),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            start_new_session=True,
            env={k: v for k, v in os.environ.items() if k != "PYTHONPATH"},
        )

    set_job_launcher(long_launcher)
    assert client.post("/api/cases/local-001/segment").status_code == 200
    for _ in range(80):
        latest = client.get("/api/cases/local-001/jobs/latest").json()
        if latest["status"] in {"succeeded", "failed"}:
            break
        time.sleep(0.05)
    latest = client.get("/api/cases/local-001/jobs/latest").json()
    assert latest["status"] == "failed", latest
    assert "timed out" in (latest.get("error") or "").lower()
    set_job_timeout_s(None)


def test_route_start_validation(client, harness):
    _set_clinician(client)
    _seed_through_step(client, harness, 5)

    res = client.put("/api/cases/local-001/route-start", json={"node": 1})
    assert res.status_code == 422
    assert "qualifying" in res.json()["detail"].lower() or "radius" in res.json()["detail"].lower()

    res = client.put("/api/cases/local-001/route-start", json={"node": 0})
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["route_start"]["node"] == 0
    assert body["record"]["kind"] == "route_start_confirmed"
    assert body["record"]["data"]["moved"] is False

    payload = client.get("/api/cases/local-001/payload?allow_review=true").json()
    assert payload["route_start"]["node"] == 0
    assert payload["route_start"]["confirmed"] is True

    wf = client.get("/api/cases/local-001/workflow").json()
    assert wf["steps"][4]["status"] == "done"


def test_payload_route_start_default(client, harness):
    payload = client.get("/api/cases/local-001/payload?allow_review=true").json()
    assert "route_start" in payload
    assert payload["route_start"]["node"] == 0
    assert payload["route_start"]["confirmed"] is False


def test_missing_radius_mm_422(client, harness):
    _set_clinician(client)
    _seed_through_step(client, harness, 5)
    graph = json.loads(harness["graph"].read_text())
    del graph["nodes"][0]["radius_mm"]
    harness["graph"].write_text(json.dumps(graph))
    res = client.get("/api/cases/local-001/route-start/candidates")
    assert res.status_code == 422
    assert "radius_mm" in res.json()["detail"]


def test_scan_status_and_rejected_override(client, fake_home, monkeypatch):
    from test_series_rule import survivor

    _set_clinician(client)
    rows = {
        "a": survivor(series_uid_sha256="a" * 64),
        "b": survivor(number=9, spacing=5.0, series_uid_sha256="b" * 64),
    }
    monkeypatch.setattr("app.imports.read_series", lambda root: (rows, 0))
    scan = client.post("/api/imports/scan", json={"src_dir": str(fake_home)})
    assert scan.status_code == 200
    result = scan.json()
    assert result["proposed"]["status"] == "recommended"
    assert result["proposed"]["kernel_class"] == "soft"
    assert result["proposed"]["z_span_mm"] >= 250
    assert result["proposed"]["contrast"] is False
    assert result["series"][1]["status"] == "not_usable"
    refused = client.post("/api/imports", json={
        "src_dir": str(fake_home), "series": "b" * 64, "confirm": True,
    })
    assert refused.status_code == 422
    assert "step" in refused.json()["detail"]

    rows.pop("b")
    rows["a"]["manufacturer"] = "GE"
    result = client.post("/api/imports/scan", json={"src_dir": str(fake_home)}).json()
    assert result["proposed"] is None
    assert result["series"][0]["status"] == "usable"
    assert result["series"][0]["skip_reason"] is None

    rows["a"]["z_span_mm"] = 190
    result = client.post("/api/imports/scan", json={"src_dir": str(fake_home)}).json()
    assert result["proposed"] is None
    assert result["series"][0]["status"] == "not_usable"


def test_existing_series_confirmation_rechecks_facts(client, harness):
    from test_series_rule import survivor

    _set_clinician(client)
    case = json.loads(harness["case"].read_text())
    case["source"]["series_facts"] = survivor(z_span_mm=190)
    harness["case"].write_text(json.dumps(case))
    res = client.post("/api/cases/local-001/series/confirm", json={})
    assert res.status_code == 422
    assert "coverage" in res.json()["detail"]
