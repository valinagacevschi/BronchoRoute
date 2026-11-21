# Changes: import no longer 412 for missing initials (settings auto-generate).
# Changes: Series override reason, junk-file scans, and a lungs failure.
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


def test_archive_kind_reads_signature(tmp_path):
    rar = tmp_path / "named.zip"
    rar.write_bytes(b"Rar!\x1a\x07\x01\x00")
    assert imports._archive_kind(rar) == "rar"
    zipped = tmp_path / "named.rar"
    zipped.write_bytes(b"PK\x03\x04")
    assert imports._archive_kind(zipped) == "zip"


def test_rar_without_bsdtar_refused(extraction, monkeypatch):
    archive = extraction / "input.rar"
    archive.write_bytes(b"Rar!\x1a\x07\x01\x00")
    monkeypatch.setattr(imports.shutil, "which", lambda _name: None)
    with pytest.raises(ValueError, match="unavailable"):
        imports.extract_archive(archive)
    assert not (extraction / "tmp").exists()


def test_unsafe_rar_rejected(extraction, monkeypatch):
    archive = extraction / "input.rar"
    archive.write_bytes(b"Rar!\x1a\x07\x01\x00")

    class Member:
        filename = "../x"
        file_size = 1

        @staticmethod
        def is_symlink():
            return False

    class Source:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def infolist(self):
            return [Member()]

    monkeypatch.setattr(imports.rarfile, "RarFile", lambda _path: Source())
    monkeypatch.setattr(imports.shutil, "which", lambda _name: "/usr/bin/bsdtar")
    with pytest.raises(ValueError, match="unsafe"):
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
    # Auto initials are enough to confirm; this test only checks scan privacy/cleanup.
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
    (root / "notes.txt").write_text("notes")
    (root / "setup.exe").write_bytes(b"MZ")
    (root / "autorun.inf").write_text("[autorun]\n")
    (root / "random.bin").write_bytes(bytes(range(256)))
    source = root
    extra = 0
    if dicomdir:
        source = root / "DICOMDIR"
        source.write_bytes(b"directory input")
        extra = 1
    result = lifecycle.post("/api/imports/scan", json={"path": str(source)})
    assert result.status_code == 200, result.text
    body = result.json()
    assert body["proposed"]["status"] == "recommended"
    assert body["unreadable_files"] == 4 + extra
    lifecycle.delete(f"/api/imports/{body['import_id']}")
    assert root.is_dir()


def _no_case_or_tmp():
    import paths
    assert list(paths.LOCAL_CASES.glob("local-*")) == []
    if paths.TMP_DIR.exists():
        assert list(paths.TMP_DIR.iterdir()) == []


def test_series_override_reason(lifecycle, extraction, monkeypatch):
    import json
    import paths
    from tracer_fixture import write_choice_folder, write_series
    monkeypatch.setattr("app.jobs.start_segment_job", lambda *args, **kwargs: {"status": "queued"})
    root = write_choice_folder(extraction / "choice")
    lifecycle.put("/api/clinician", json={"role": "Clinician", "initials": "A.B."})

    def scan():
        response = lifecycle.post("/api/imports/scan", json={"path": str(root)})
        assert response.status_code == 200, response.text
        body = response.json()
        usable = next(row for row in body["series"] if row["status"] == "usable")
        recommended = next(row for row in body["series"] if row["status"] == "recommended")
        rejected = next(row for row in body["series"] if row["status"] == "not_usable")
        return body, usable, recommended, rejected

    scanned, usable, _recommended, _rejected = scan()
    missing = lifecycle.post("/api/imports", json={
        "import_id": scanned["import_id"], "series_key": usable["series_key"],
    })
    assert missing.status_code == 422
    assert missing.json()["detail"] == "Give a reason for choosing this Series"
    _no_case_or_tmp()
    assert root.is_dir()
    assert lifecycle.post("/api/imports/scan", json={"path": str(root)}).status_code == 200

    scanned, usable, _recommended, _rejected = scan()
    too_long = lifecycle.post("/api/imports", json={
        "import_id": scanned["import_id"], "series_key": usable["series_key"],
        "override_reason": "x" * 201,
    })
    assert too_long.status_code == 422
    _no_case_or_tmp()

    scanned, _usable, _recommended, rejected = scan()
    refused = lifecycle.post("/api/imports", json={
        "import_id": scanned["import_id"], "series_key": rejected["series_key"],
        "override_reason": "sharper edges",
    })
    assert refused.status_code == 422
    _no_case_or_tmp()

    scanned, _usable, recommended, _rejected = scan()
    kept = lifecycle.post("/api/imports", json={
        "import_id": scanned["import_id"], "series_key": recommended["series_key"],
        "override_reason": "ignored on the recommended Series",
    })
    assert kept.status_code == 200, kept.text
    kept_body = kept.json()
    assert kept_body["record"]["data"]["overridden"] is False
    assert kept_body["record"]["data"]["override_reason"] == ""
    kept_doc = json.loads((paths.LOCAL_CASES / kept_body["case_id"] / "case.json").read_text())
    assert kept_doc["source"]["series_choice"] == {
        "status": "recommended",
        "status_reason": "",
        "overridden": False,
        "override_reason": "",
    }

    scanned, usable, _recommended, _rejected = scan()
    accepted = lifecycle.post("/api/imports", json={
        "import_id": scanned["import_id"], "series_key": usable["series_key"],
        "override_reason": "sharper edges",
    })
    assert accepted.status_code == 200, accepted.text
    body = accepted.json()
    assert body["record"]["data"]["override_reason"] == "sharper edges"
    assert body["record"]["data"]["status_reason"] == usable["reason"]
    assert isinstance(usable["reason"], str) and usable["reason"]
    for key in ("series_number", "description", "kernel", "overridden", "status"):
        assert key in body["record"]["data"]
    document = json.loads((paths.LOCAL_CASES / body["case_id"] / "case.json").read_text())
    assert document["source"]["series_choice"] == {
        "status": "usable",
        "status_reason": usable["reason"],
        "overridden": True,
        "override_reason": "sharper edges",
    }

    plain = extraction / "ge"
    write_series(
        plain / "one", 1, "1.2.826.0.1.3680043.10.999.31",
        manufacturer="GE", kernel="STANDARD",
    )
    write_series(
        plain / "two", 2, "1.2.826.0.1.3680043.10.999.32",
        manufacturer="GE", kernel="STANDARD", description="AXIAL STANDARD B",
    )
    ge = lifecycle.post("/api/imports/scan", json={"path": str(plain)}).json()
    assert ge["proposed"] is None
    assert {row["status"] for row in ge["series"]} == {"usable"}
    first, second = ge["series"]
    without = lifecycle.post("/api/imports", json={
        "import_id": ge["import_id"], "series_key": first["series_key"],
    })
    assert without.status_code == 200, without.text
    assert without.json()["record"]["data"]["overridden"] is False
    assert without.json()["record"]["data"]["override_reason"] == ""
    ge = lifecycle.post("/api/imports/scan", json={"path": str(plain)}).json()
    second = next(row for row in ge["series"] if row["series_key"] == second["series_key"])
    with_reason = lifecycle.post("/api/imports", json={
        "import_id": ge["import_id"], "series_key": second["series_key"],
        "override_reason": "local preference",
    })
    assert with_reason.status_code == 200, with_reason.text
    assert with_reason.json()["record"]["data"]["overridden"] is False
    assert with_reason.json()["record"]["data"]["override_reason"] == "local preference"


def test_lungs_failure_leaves_no_case(lifecycle, extraction, monkeypatch):
    import time
    import paths
    from tracer_fixture import write_archive
    monkeypatch.setenv("BRONCHOROUTE_FAKE_FAIL", "lungs")
    archive = write_archive(extraction / "input.zip")
    scan = lifecycle.post("/api/imports/scan", json={"path": str(archive)}).json()
    lifecycle.put("/api/clinician", json={"role": "Clinician", "initials": "A.B."})
    response = lifecycle.post("/api/imports", json={
        "import_id": scan["import_id"], "series_key": scan["proposed"]["series_key"],
    })
    assert response.status_code == 200, response.text
    case_id = response.json()["case_id"]
    job = {}
    for _ in range(300):
        current = lifecycle.get(f"/api/cases/{case_id}/jobs/latest")
        if current.status_code == 200:
            job = current.json()
            if job.get("status") in {"succeeded", "failed"}:
                break
        time.sleep(0.05)
    else:
        pytest.fail(f"processing did not finish: {job}")
    assert job["status"] == "failed"
    assert job["error"] == "segmentation step failed: no lungs found. Try another Series"
    for _ in range(50):
        tmp_clear = not paths.TMP_DIR.exists() or not list(paths.TMP_DIR.iterdir())
        if (lifecycle.get("/api/cases").json() == []
                and not (paths.LOCAL_CASES / case_id).exists()
                and tmp_clear):
            break
        time.sleep(0.05)
    else:
        pytest.fail("failed import was not cleaned up")


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
