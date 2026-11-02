# Changes: hide legacy review behind the default-off feature flag.
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
    monkeypatch.setenv("BRONCHOROUTE_LEGACY_REVIEW", "1")
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


@pytest.mark.parametrize("method,suffix", [
    ("GET", "review"), ("POST", "corrections"),
    ("DELETE", "corrections/example"), ("POST", "review-signoff"),
    ("GET", "versions"), ("PUT", "walkthrough/0"), ("GET", "tracing"),
    ("GET", "uncertainty"), ("POST", "signoff"),
    ("GET", "versions/1/report"),
])
def test_legacy_endpoints_hidden(client, monkeypatch, method, suffix):
    monkeypatch.delenv("BRONCHOROUTE_LEGACY_REVIEW", raising=False)
    assert client.request(method, f"/api/cases/local-001/{suffix}").status_code == 404


def test_default_features_and_cases(client, monkeypatch):
    monkeypatch.delenv("BRONCHOROUTE_LEGACY_REVIEW", raising=False)
    monkeypatch.delenv("LUNGNAV_VIEWER_ONLY", raising=False)
    assert client.get("/api/features").json() == {
        "legacy_review": False, "viewer_only": False,
    }
    assert client.get("/workflow").status_code == 404
    assert client.get("/app.html").status_code == 404
    assert [row["id"] for row in client.get("/api/cases").json()] == ["local-001"]
    assert client.get("/api/cases/3/payload").status_code == 404
    payload = client.get("/api/cases/local-001/payload?allow_review=true").json()
    assert payload["review"][0]["kind"] == "small_lobe"


def test_lesion_does_not_create_version(client, harness, monkeypatch):
    from app.cases import read_case_json, set_lesion
    monkeypatch.delenv("BRONCHOROUTE_LEGACY_REVIEW", raising=False)
    set_lesion("local-001", [0, 0, 0], "AB")
    record = read_case_json("local-001")
    assert not record.get("versions")
    assert not record.get("review_signoff")


@pytest.mark.parametrize("value,enabled", [
    ("1", True),
    ("TRUE", True),
    (" yes ", True),
    ("on", True),
    ("0", False),
    ("false", False),
    ("", False),
])
def test_features_flag_values(client, monkeypatch, value, enabled):
    monkeypatch.setenv("BRONCHOROUTE_LEGACY_REVIEW", value)
    monkeypatch.setenv("LUNGNAV_VIEWER_ONLY", "1")
    assert client.get("/api/features").json() == {
        "legacy_review": enabled, "viewer_only": True,
    }


def test_disabled_version_hooks_preserve_existing_state(monkeypatch):
    from app.versions import note_change, register_artifact
    monkeypatch.delenv("BRONCHOROUTE_LEGACY_REVIEW", raising=False)
    record = {"versions": [{"n": 1, "frozen": True}]}
    before = json.loads(json.dumps(record))
    note_change(record, "lesion")
    register_artifact(record, "graph", "unused")
    assert record == before
