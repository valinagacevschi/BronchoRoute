# Changes: scan rows include the library hit field.
# Changes: synthetic Archive safety, privacy and processing lifecycle coverage.
from __future__ import annotations

import stat
import zipfile
from pathlib import Path

import pytest

import repo_root
repo_root.ensure()
from app import imports


@pytest.fixture
def extraction(tmp_path, monkeypatch):
    monkeypatch.setattr("paths.TMP_DIR", tmp_path / "tmp")
    return tmp_path


@pytest.mark.parametrize("entry,mode", [("../x", 0), ("/x", 0), ("link", stat.S_IFLNK | 0o777), ("C:/x", 0), ("a/../../x", 0)])
def test_unsafe_zip_rejected(extraction, entry, mode):
    archive = extraction / "input.zip"
    with zipfile.ZipFile(archive, "w") as output:
        info = zipfile.ZipInfo(entry)
        info.external_attr = mode << 16
        output.writestr(info, b"x")
    with pytest.raises(ValueError, match="unsafe"):
        imports.extract_archive(archive)
    assert not list((extraction / "tmp").iterdir())


@pytest.mark.parametrize("limit,value", [("MAX_ARCHIVE_BYTES", 1), ("MAX_ARCHIVE_FILES", 0)])
def test_zip_caps(extraction, monkeypatch, limit, value):
    archive = extraction / "input.zip"
    with zipfile.ZipFile(archive, "w") as output:
        output.writestr("ignored.exe", b"xx")
    monkeypatch.setattr(imports, limit, value)
    with pytest.raises(ValueError, match="limit"):
        imports.extract_archive(archive)
    assert not list((extraction / "tmp").iterdir())


@pytest.fixture
def lifecycle(extraction, monkeypatch):
    from fastapi.testclient import TestClient
    from app.server import app
    from app.jobs import reset_jobs_for_tests
    for name, relative in {
        "PIPELINE": ".", "LOCAL_CASES": "data/extracted/local", "AEROPATH": "data/extracted/AeroPath",
        "OUT": "out", "TS_OUT": "ts_out", "TMP_DIR": "tmp", "LOG_DIR": "logs",
    }.items():
        monkeypatch.setattr(f"paths.{name}", extraction / relative)
    monkeypatch.setenv("BRONCHOROUTE_FAKE_SEGMENT", "1")
    monkeypatch.setenv("BRONCHOROUTE_TESTING", "1")
    monkeypatch.setenv("BRONCHOROUTE_FAKE_STEP_SECONDS", "0.1")
    reset_jobs_for_tests()
    with TestClient(app, base_url="http://127.0.0.1") as client:
        yield client
        reset_jobs_for_tests()


def test_scan_privacy_and_non_dicom(lifecycle, extraction):
    from tracer_fixture import write_archive
    import paths
    archive = write_archive(extraction / "input.zip")
    response = lifecycle.post("/api/imports/scan", json={"path": str(archive)})
    assert response.status_code == 200
    result = response.json()
    assert set(result) == {"import_id", "unreadable_files", "proposed", "series", "studies"}
    assert set(result["studies"][0]) == {"study_key", "date", "description", "series"}
    assert set(result["series"][0]) == {
        "series_key", "series_number", "description", "kernel", "modality", "slice_thickness_mm",
        "spacing_mm", "axial", "size", "count", "status", "reason", "kernel_class", "contrast",
        "z_span_mm", "skip_reason", "proposed", "in_library",
    }
    root = next(paths.TMP_DIR.iterdir())
    assert len(list(root.iterdir())) == 255
    assert all(path.suffix == ".dcm" for path in root.iterdir())
    assert result["series"][1]["status"] == "not_usable"
    refused = lifecycle.post("/api/imports", json={
        "import_id": result["import_id"], "series_key": result["proposed"]["series_key"],
    })
    assert refused.status_code == 412
    lifecycle.delete(f"/api/imports/{result['import_id']}")
    assert not list(paths.TMP_DIR.iterdir())


@pytest.mark.parametrize("outcome", ["success", "failure", "cancel"])
def test_processing_cleanup(lifecycle, extraction, monkeypatch, outcome):
    import json
    import time
    import paths
    from tracer_fixture import write_archive
    if outcome == "failure":
        monkeypatch.setenv("BRONCHOROUTE_FAKE_FAIL", "1")
    if outcome == "cancel":
        monkeypatch.setenv("BRONCHOROUTE_FAKE_STEP_SECONDS", "5")
    archive = write_archive(extraction / "input.zip")
    scan = lifecycle.post("/api/imports/scan", json={"path": str(archive)}).json()
    lifecycle.put("/api/clinician", json={"role": "Clinician", "initials": "A.B."})
    response = lifecycle.post("/api/imports", json={
        "import_id": scan["import_id"], "series_key": scan["proposed"]["series_key"],
    })
    assert response.status_code == 200
    case_id = response.json()["case_id"]
    assert lifecycle.get("/api/cases").json() == []
    if outcome == "cancel":
        cancelled = lifecycle.post(f"/api/cases/{case_id}/jobs/latest/cancel")
        assert cancelled.status_code == 200
        assert cancelled.json()["error"] == "cancelled"
    for _ in range(300):
        job = lifecycle.get(f"/api/cases/{case_id}/jobs/latest").json()
        if job["status"] in {"succeeded", "failed"}:
            break
        time.sleep(.05)
    else:
        pytest.fail("processing did not finish")
    assert job["status"] == ("succeeded" if outcome == "success" else "failed")
    assert not list(paths.TMP_DIR.iterdir())
    if outcome == "success":
        assert len(lifecycle.get("/api/cases").json()) == 1
        document = json.loads((paths.LOCAL_CASES / case_id / "case.json").read_text())
        assert "folder" not in document["source"]
        assert document["source"]["series_uid_sha256"] == scan["proposed"]["series_key"]
        assert lifecycle.get(f"/api/cases/{case_id}/payload?allow_review=1").status_code == 200
    else:
        assert lifecycle.get("/api/cases").json() == []
        assert not (paths.LOCAL_CASES / case_id).exists()


@pytest.mark.parametrize("dicomdir", [False, True])
def test_folder_and_dicomdir_scan(lifecycle, extraction, dicomdir):
    from tracer_fixture import write_series
    root = extraction / "folder"
    write_series(root, 1, "1.2.826.0.1.3680043.10.999.5")
    source = root
    if dicomdir:
        source = root / "DICOMDIR"
        source.write_bytes(b"directory input")
    result = lifecycle.post("/api/imports/scan", json={"path": str(source)})
    assert result.status_code == 200
    assert result.json()["proposed"]["status"] == "recommended"
    lifecycle.delete(f"/api/imports/{result.json()['import_id']}")
    assert root.is_dir()


def test_fake_launcher_requires_test_context(monkeypatch):
    from app.jobs import _segment_commands
    from app.caseref import CaseRef
    monkeypatch.setenv("BRONCHOROUTE_FAKE_SEGMENT", "1")
    monkeypatch.delenv("BRONCHOROUTE_TESTING", raising=False)
    monkeypatch.delenv("BRONCHOROUTE_SMOKE", raising=False)
    assert "run_totalsegmentator.py" in _segment_commands(CaseRef("local-001", "local"))[0]


def test_fake_script_refuses_real_root():
    import os
    import subprocess
    import sys
    root = Path(__file__).resolve().parent
    env = {**os.environ, "BRONCHOROUTE_FAKE_SEGMENT": "1", "BRONCHOROUTE_TESTING": "1",
           "LUNGNAV_DATA_ROOT": str(root)}
    env.pop("PYTHONPATH", None)
    result = subprocess.run([sys.executable, "fake_segment.py", "local-001"],
                            cwd=root, env=env, capture_output=True, text=True)
    assert result.returncode != 0
    assert "requires an isolated state root" in result.stderr


def test_startup_removes_unfinished_import(lifecycle):
    import json
    import paths
    from app.jobs import initialize_sidecar, close_sidecar_log
    case = paths.LOCAL_CASES / "local-001"
    case.mkdir(parents=True)
    (case / "case.json").write_text(json.dumps({"import_processing": True}))
    for directory in (paths.TS_OUT / "local-001", paths.OUT / "local-001", paths.TMP_DIR / "stale"):
        directory.mkdir(parents=True)
        (directory / "partial").write_bytes(b"partial")
    handler = initialize_sidecar()
    close_sidecar_log(handler)
    assert not case.exists()
    assert not list(paths.TMP_DIR.iterdir())
    assert not (paths.OUT / "local-001").exists()
