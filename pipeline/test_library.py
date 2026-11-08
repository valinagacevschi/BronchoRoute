# Changes: Case Library listing, deletion, reprocess, and library-hit coverage.
from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

import repo_root
repo_root.ensure()

from app.jobs import reset_jobs_for_tests
from test_app_server import _write_case_tree

FORBIDDEN_KEY_PARTS = ("patient", "name", "birth", "mrn", "accession", "uid", "folder")
SECRET = "series-hash-not-a-patient"


def _tree_bytes(path: Path) -> int:
    if path.is_symlink() or not path.is_dir():
        return 0
    total = 0
    for item in path.rglob("*"):
        if item.is_symlink() or not item.is_file():
            continue
        total += item.stat().st_size
    return total


def _assert_no_identifiers(value) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            lowered = str(key).lower()
            assert not any(part in lowered for part in FORBIDDEN_KEY_PARTS), key
            _assert_no_identifiers(item)
    elif isinstance(value, list):
        for item in value:
            _assert_no_identifiers(item)


def _isolate(monkeypatch, root: Path) -> None:
    for name, relative in {
        "PIPELINE": ".",
        "LOCAL_CASES": "data/extracted/local",
        "AEROPATH": "data/extracted/AeroPath",
        "OUT": "out",
        "TS_OUT": "ts_out",
        "TMP_DIR": "tmp",
        "LOG_DIR": "logs",
    }.items():
        monkeypatch.setattr(f"paths.{name}", root / relative)
    monkeypatch.setattr("paths.CACHE_DIR", None)


@pytest.fixture
def library_client(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from app.server import app
    reset_jobs_for_tests()
    _isolate(monkeypatch, tmp_path)
    _write_case_tree(tmp_path, "local-001")
    _write_case_tree(tmp_path, "local-002")
    case = tmp_path / "data/extracted/local/local-001/case.json"
    document = json.loads(case.read_text())
    document["source"] = {
        "study_date": "20260101",
        "series_description": "AXIAL STANDARD",
        "series_uid_sha256": SECRET,
    }
    document["pipeline_version"] = "0"
    document["processed_at"] = "2026-01-02T03:04:05Z"
    case.write_text(json.dumps(document))
    with TestClient(app, base_url="http://127.0.0.1") as client:
        yield client, tmp_path
    reset_jobs_for_tests()


def test_library_listing_outdated_and_warning(library_client, monkeypatch):
    import paths
    from app.library import LIBRARY_WARN_BYTES
    client, root = library_client
    body = client.get("/api/library").json()
    assert set(body) == {"cases", "total_bytes", "warn"}
    assert [row["id"] for row in body["cases"]] == ["local-001", "local-002"]
    _assert_no_identifiers(body)
    assert SECRET not in json.dumps(body)
    first, second = body["cases"]
    assert set(first) == {
        "id", "study_date", "series_description", "processed_at", "size_bytes", "outdated",
    }
    assert first["study_date"] == "20260101"
    assert first["series_description"] == "AXIAL STANDARD"
    assert first["processed_at"] == "2026-01-02T03:04:05Z"
    assert first["outdated"] is True
    assert second["study_date"] == ""
    assert second["series_description"] == ""
    assert second["processed_at"] is None
    assert second["outdated"] is True
    expected = 0
    for case_id in ("local-001", "local-002"):
        size = (
            _tree_bytes(root / "data/extracted/local" / case_id)
            + _tree_bytes(root / "ts_out" / case_id)
            + _tree_bytes(root / "out" / case_id)
        )
        assert size > 0
        row = next(item for item in body["cases"] if item["id"] == case_id)
        assert row["size_bytes"] == size
        expected += size
    assert body["total_bytes"] == expected
    assert body["warn"] is False
    assert expected < LIBRARY_WARN_BYTES
    monkeypatch.setattr("app.library.LIBRARY_WARN_BYTES", expected)
    assert client.get("/api/library").json()["warn"] is False
    monkeypatch.setattr("app.library.LIBRARY_WARN_BYTES", expected - 1)
    assert client.get("/api/library").json()["warn"] is True

    current = json.loads((root / "data/extracted/local/local-002/case.json").read_text())
    current["pipeline_version"] = paths.PIPELINE_VERSION
    (root / "data/extracted/local/local-002/case.json").write_text(json.dumps(current))
    refreshed = client.get("/api/library").json()["cases"]
    assert refreshed[0]["outdated"] is True
    assert refreshed[1]["outdated"] is False


def test_delete_one_and_all(library_client):
    client, root = library_client
    removed = client.delete("/api/cases/local-002")
    assert removed.status_code == 200
    assert removed.json() == {"deleted": ["local-002"]}
    for name in ("data/extracted/local", "ts_out", "out"):
        assert not (root / name / "local-002").exists()
        assert (root / name / "local-001").is_dir()
    assert [row["id"] for row in client.get("/api/library").json()["cases"]] == ["local-001"]
    assert client.delete("/api/cases/local-999").status_code == 404
    cleared = client.delete("/api/library")
    assert cleared.status_code == 200
    assert cleared.json() == {"deleted": ["local-001"]}
    for name in ("data/extracted/local", "ts_out", "out"):
        assert not (root / name / "local-001").exists()
    assert client.get("/api/library").json() == {"cases": [], "total_bytes": 0, "warn": False}


@pytest.fixture
def lifecycle(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from app.server import app
    reset_jobs_for_tests()
    _isolate(monkeypatch, tmp_path)
    monkeypatch.setenv("BRONCHOROUTE_FAKE_SEGMENT", "1")
    monkeypatch.setenv("BRONCHOROUTE_TESTING", "1")
    monkeypatch.setenv("BRONCHOROUTE_FAKE_STEP_SECONDS", "0.05")
    with TestClient(app, base_url="http://127.0.0.1") as client:
        yield client, tmp_path
    reset_jobs_for_tests()


def _wait_job(client, case_id: str) -> dict:
    for _ in range(300):
        job = client.get(f"/api/cases/{case_id}/jobs/latest").json()
        if job["status"] in {"succeeded", "failed"}:
            return job
        time.sleep(0.05)
    pytest.fail("processing did not finish")


def test_reprocess_delete_guard_and_library_hit(lifecycle, monkeypatch):
    import paths
    from tracer_fixture import write_archive
    client, root = lifecycle
    archive = write_archive(root / "input.zip")
    scan = client.post("/api/imports/scan", json={"path": str(archive)}).json()
    assert client.put("/api/clinician", json={"role": "Clinician", "initials": "A.B."}).status_code == 200
    created = client.post("/api/imports", json={
        "import_id": scan["import_id"],
        "series_key": scan["proposed"]["series_key"],
    })
    assert created.status_code == 200
    case_id = created.json()["case_id"]
    job = _wait_job(client, case_id)
    assert job["status"] == "succeeded"
    published = None
    for _ in range(50):
        body = client.get("/api/library").json()
        row = next((item for item in body["cases"] if item["id"] == case_id), None)
        if row is not None and row["outdated"] is False:
            published = body
            break
        time.sleep(0.05)
    assert published is not None
    row = next(item for item in published["cases"] if item["id"] == case_id)
    assert row["study_date"] == "20260101"
    assert row["series_description"] == "AXIAL STANDARD"
    assert isinstance(row["processed_at"], str) and row["processed_at"]
    assert row["size_bytes"] > 0
    assert published["warn"] is False
    _assert_no_identifiers(published)
    document = json.loads((paths.LOCAL_CASES / case_id / "case.json").read_text())
    assert document["pipeline_version"] == paths.PIPELINE_VERSION
    assert "folder" not in document["source"]
    monkeypatch.setattr("app.library.LIBRARY_WARN_BYTES", published["total_bytes"] - 1)
    assert client.get("/api/library").json()["warn"] is True
    monkeypatch.setattr("app.library.LIBRARY_WARN_BYTES", 10 * 1024 ** 3)

    monkeypatch.setenv("BRONCHOROUTE_FAKE_STEP_SECONDS", "5")
    started = client.post(f"/api/cases/{case_id}/reprocess")
    assert started.status_code == 200, started.text
    assert started.json()["case_id"] == case_id
    assert started.json()["status"] in {"queued", "running"}
    denied = client.delete(f"/api/cases/{case_id}")
    assert denied.status_code == 409
    denied_all = client.delete("/api/library")
    assert denied_all.status_code == 409
    for directory in (
        paths.LOCAL_CASES / case_id,
        paths.TS_OUT / case_id,
        paths.OUT / case_id,
    ):
        assert directory.is_dir()
    cancelled = client.post(f"/api/cases/{case_id}/jobs/latest/cancel")
    assert cancelled.status_code == 200
    assert (paths.LOCAL_CASES / case_id / "case.json").is_file()

    # processed_at is second resolution; don't let the next run share that second.
    time.sleep(1.1)
    monkeypatch.setenv("BRONCHOROUTE_FAKE_STEP_SECONDS", "0.05")
    before = document["processed_at"]
    again = client.post(f"/api/cases/{case_id}/reprocess")
    assert again.status_code == 200
    assert _wait_job(client, case_id)["status"] == "succeeded"
    updated = json.loads((paths.LOCAL_CASES / case_id / "case.json").read_text())
    assert updated["pipeline_version"] == paths.PIPELINE_VERSION
    assert updated["processed_at"] != before

    hit = client.post("/api/imports/scan", json={"path": str(archive)})
    assert hit.status_code == 200
    scanned = hit.json()
    proposed = next(item for item in scanned["series"] if item["series_key"] == scan["proposed"]["series_key"])
    other = next(item for item in scanned["series"] if item["series_key"] != scan["proposed"]["series_key"])
    assert proposed["in_library"] == case_id
    assert other["in_library"] is None
    client.delete(f"/api/imports/{scanned['import_id']}")

    pending = paths.LOCAL_CASES / "local-009"
    pending.mkdir()
    (pending / "case.json").write_text(json.dumps({
        "id": "local-009",
        "import_processing": True,
        "source": {"series_uid_sha256": other["series_key"]},
    }))
    hidden = client.post("/api/imports/scan", json={"path": str(archive)}).json()
    hidden_other = next(item for item in hidden["series"] if item["series_key"] == other["series_key"])
    assert hidden_other["in_library"] is None
    client.delete(f"/api/imports/{hidden['import_id']}")
    (pending / "case.json").write_text(json.dumps({
        "id": "local-009",
        "source": {"series_uid_sha256": other["series_key"]},
    }))
    visible = client.post("/api/imports/scan", json={"path": str(archive)}).json()
    visible_other = next(item for item in visible["series"] if item["series_key"] == other["series_key"])
    assert visible_other["in_library"] == "local-009"
    client.delete(f"/api/imports/{visible['import_id']}")
