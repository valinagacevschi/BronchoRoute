# Changes: tests for LUNGNAV_VIEWER_ONLY / CASES / CACHE_DIR / healthz.
"""Server deploy settings — no TotalSegmentator, Playwright, or real patient data."""

from __future__ import annotations

import json
import stat

import pytest
from fastapi.testclient import TestClient
from termcolor import cprint

import repo_root

repo_root.ensure()

from app.server import app  # noqa: E402
from test_app_server import _write_aeropath_case, _write_case_tree  # noqa: E402

pytestmark = pytest.mark.server


@pytest.fixture
def harness(tmp_path, monkeypatch):
    paths = _write_case_tree(tmp_path)
    _write_aeropath_case(tmp_path, "3")
    monkeypatch.setattr("paths.PIPELINE", tmp_path)
    monkeypatch.setattr("paths.LOCAL_CASES", tmp_path / "data/extracted/local")
    monkeypatch.setattr("paths.AEROPATH", tmp_path / "data/extracted/AeroPath")
    monkeypatch.setattr("paths.TS_OUT", tmp_path / "ts_out")
    monkeypatch.setattr("paths.OUT", tmp_path / "out")
    monkeypatch.setattr("paths.CACHE_DIR", None)
    return paths


@pytest.fixture
def client(harness):
    return TestClient(app, base_url="http://127.0.0.1")


def test_healthz_no_data(client):
    cprint("healthz", "cyan")
    res = client.get("/healthz")
    assert res.status_code == 200
    assert res.text == "ok"


def test_viewer_only_blocks_workflow_and_writes(client, monkeypatch):
    cprint("viewer-only 403/404", "cyan")
    monkeypatch.setenv("LUNGNAV_VIEWER_ONLY", "1")
    assert client.get("/").status_code == 200
    assert client.get("/workflow").status_code == 404
    assert client.get("/api/cases").status_code == 200
    assert client.post(
        "/api/imports/scan", json={"src_dir": "/tmp"},
    ).status_code == 403
    assert client.put(
        "/api/clinician",
        json={"role": "Radiologist", "initials": "A.B."},
    ).status_code == 403
    monkeypatch.delenv("LUNGNAV_VIEWER_ONLY", raising=False)


def test_cases_allowlist_hides_aeropath(client, monkeypatch):
    cprint("cases allowlist", "cyan")
    all_ids = {row["id"] for row in client.get("/api/cases").json()}
    assert "local-001" in all_ids
    assert "3" in all_ids

    monkeypatch.setenv("LUNGNAV_CASES", "local-001")
    body = client.get("/api/cases").json()
    assert [row["id"] for row in body] == ["local-001"]
    assert client.get("/api/cases/3/payload?allow_review=true").status_code == 404
    assert client.get("/api/cases/local-001/payload?allow_review=true").status_code in {
        200, 409,
    }
    monkeypatch.delenv("LUNGNAV_CASES", raising=False)


def test_readonly_data_root_with_cache_dir(tmp_path, monkeypatch):
    cprint("read-only data root + cache", "cyan")
    data_root = tmp_path / "data_root"
    cache_root = tmp_path / "cache"
    cache_root.mkdir()
    _write_case_tree(data_root)
    (data_root / "ts_out" / "local-001" / "airway_bridges.json").write_text(
        json.dumps([])
    )

    monkeypatch.setattr("paths.PIPELINE", data_root)
    monkeypatch.setattr("paths.LOCAL_CASES", data_root / "data/extracted/local")
    monkeypatch.setattr("paths.AEROPATH", data_root / "data/extracted/AeroPath")
    monkeypatch.setattr("paths.TS_OUT", data_root / "ts_out")
    monkeypatch.setattr("paths.OUT", data_root / "out")
    monkeypatch.setattr("paths.CACHE_DIR", cache_root.resolve())
    monkeypatch.setenv("LUNGNAV_CASES", "local-001")

    for path in sorted(data_root.rglob("*"), reverse=True):
        if path.is_dir():
            path.chmod(stat.S_IRUSR | stat.S_IXUSR | stat.S_IRGRP | stat.S_IXGRP)
        else:
            path.chmod(stat.S_IRUSR | stat.S_IRGRP)
    data_root.chmod(stat.S_IRUSR | stat.S_IXUSR | stat.S_IRGRP | stat.S_IXGRP)

    client = TestClient(app, base_url="http://127.0.0.1")
    try:
        assert client.get("/healthz").status_code == 200
        payload = client.get("/api/cases/local-001/payload?allow_review=true")
        assert payload.status_code == 200, payload.text
        assert payload.json()["case"] == "local-001"

        meta = client.get("/api/cases/local-001/ct/meta")
        assert meta.status_code == 200, meta.text
        ct = client.get("/api/cases/local-001/ct")
        assert ct.status_code == 200
        assert len(ct.content) > 0

        masks_meta = client.get("/api/cases/local-001/masks/meta")
        assert masks_meta.status_code == 200, masks_meta.text
        masks = client.get("/api/cases/local-001/masks")
        assert masks.status_code == 200

        assert any(cache_root.rglob("ct_crop.bin"))
        assert any(cache_root.rglob("masks_crop.bin"))
        assert list((data_root / "ts_out").rglob("ct_crop.bin")) == []
    finally:
        for path in sorted(data_root.rglob("*"), reverse=True):
            try:
                path.chmod(0o755 if path.is_dir() else 0o644)
            except OSError:
                pass
        try:
            data_root.chmod(0o755)
        except OSError:
            pass
