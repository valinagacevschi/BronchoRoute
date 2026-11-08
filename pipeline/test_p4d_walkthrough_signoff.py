# Changes: hide legacy review behind the default-off feature flag.
# Changes: Phase 4d review fixes — node-keyed walk; No leaves route_target;
# snapshot freeze; XSS/UID 422; duplicate sign-off 409; report from snapshot;
# rename freeze-immutability test; review change invalidates walk.
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from termcolor import cprint

import repo_root

repo_root.ensure()

from app.server import app  # noqa: E402
from app.versions import version_snapshot_dir  # noqa: E402
from test_app_server import _write_case_tree  # noqa: E402
from test_p4a_workflow import _seed_through_step  # noqa: E402
from test_p4c_route_target import _place_lesion, _rt_graph  # noqa: E402


@pytest.fixture
def harness(tmp_path, monkeypatch):
    monkeypatch.setenv("BRONCHOROUTE_LEGACY_REVIEW", "1")
    cprint(f"p4d harness at {tmp_path}", "cyan")
    assert not str(tmp_path).startswith("/tmp"), "tests must not use /tmp"
    paths = _write_case_tree(tmp_path)
    graph = _rt_graph()
    paths["graph"].write_text(json.dumps(graph))
    (paths["ts"] / "airway_bridges.json").write_text(json.dumps({
        "note": "interpolated, not CT-demonstrated airway",
        "radius_mm": 1.0,
        "bridges": [],
    }))
    case = json.loads(paths["case"].read_text())
    case["source"] = {
        "series_uid_sha256": "abc" * 21 + "ab",
        "folder": "SERIES0",
        "series_number": 1,
        "series_description": "test axial",
        "convolution_kernel": "STANDARD",
        "spacing_mm": [1.0, 1.0, 1.5],
        "size": [24, 20, 16],
    }
    case["review"] = []
    paths["case"].write_text(json.dumps(case, indent=2) + "\n")

    monkeypatch.setattr("paths.PIPELINE", tmp_path)
    monkeypatch.setattr("paths.LOCAL_CASES", tmp_path / "data/extracted/local")
    monkeypatch.setattr("paths.AEROPATH", tmp_path / "data/extracted/AeroPath")
    monkeypatch.setattr("paths.TS_OUT", tmp_path / "ts_out")
    monkeypatch.setattr("paths.OUT", tmp_path / "out")
    (tmp_path / "data/extracted/AeroPath").mkdir(parents=True, exist_ok=True)
    (tmp_path / "app_state").mkdir(parents=True, exist_ok=True)
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


def _set_clinician(client):
    res = client.put("/api/clinician", json={"role": "Radiologist", "initials": "A.B."})
    assert res.status_code == 200, res.text


def _choose_forked_target(client, harness):
    _set_clinician(client)
    _seed_through_step(client, harness, 6)
    _place_lesion(harness["case"], [20.0, 0.0, -10.0])
    cands = client.get("/api/cases/local-001/route-candidates").json()["candidates"]
    assert cands
    top = cands[0]
    res = client.put("/api/cases/local-001/route-target", json={
        "edge_id": top["edge_id"], "sample_index": top["sample_index"],
    })
    assert res.status_code == 200, res.text
    return top, cands


def _walk_all_yes(client):
    payload = client.get("/api/cases/local-001/payload?allow_review=1").json()
    forks = payload.get("forks") or []
    for i in range(len(forks)):
        assert client.put(f"/api/cases/local-001/walkthrough/{i}", json={
            "confirmed": True,
        }).status_code == 200
    return forks


def test_walkthrough_keyed_by_route_hash(client, harness, fake_home):
    cprint("walkthrough route_hash + node key persistence", "cyan")
    top, cands = _choose_forked_target(client, harness)
    payload = client.get("/api/cases/local-001/payload?allow_review=1").json()
    forks = payload["forks"]
    assert forks
    node0 = str(int(forks[0]["node"]))
    res = client.put("/api/cases/local-001/walkthrough/0", json={
        "confirmed": True, "note": "seen on CT",
    })
    assert res.status_code == 200, res.text
    route_hash = res.json()["route_hash"]
    case = json.loads(harness["case"].read_text())
    assert route_hash in case["walkthrough"]
    assert case["walkthrough"][route_hash][node0]["confirmed"] is True
    assert case["walkthrough"][route_hash][node0]["note"] == "seen on CT"
    assert case["records"][-1]["kind"] == "bifurcation_confirmed"

    new_sample = max(0, int(top["sample_index"]) - 5)
    assert new_sample != int(top["sample_index"])
    res = client.put("/api/cases/local-001/route-target", json={
        "edge_id": top["edge_id"], "sample_index": new_sample,
    })
    assert res.status_code == 200, res.text
    forks2 = client.get("/api/cases/local-001/payload?allow_review=1").json().get("forks") or []
    assert forks2, "retarget should keep forks"
    wf = client.get("/api/cases/local-001/workflow").json()
    assert wf["steps"][6]["status"] == "available", wf["steps"][6]
    case = json.loads(harness["case"].read_text())
    assert route_hash in case["walkthrough"]
    res2 = client.put("/api/cases/local-001/walkthrough/0", json={"confirmed": True})
    assert res2.status_code == 200
    assert res2.json()["route_hash"] != route_hash


def test_walk_no_leaves_route_target_reopens_step(client, harness, fake_home):
    cprint("walk No → return_to leaves route_target", "cyan")
    _choose_forked_target(client, harness)
    res = client.put("/api/cases/local-001/walkthrough/0", json={
        "confirmed": False, "note": "wrong branch", "return_to": 6,
    })
    assert res.status_code == 200, res.text
    assert res.json()["pending_return"]["step"] == 6
    wf = client.get("/api/cases/local-001/workflow").json()
    assert wf["steps"][5]["status"] == "available", wf["steps"][5]
    case = json.loads(harness["case"].read_text())
    assert "route_target" in case
    assert case["route_target"].get("edge_id") is not None

    # Re-choose same target clears pending and reopens walk.
    cands = client.get("/api/cases/local-001/route-candidates").json()["candidates"]
    client.put("/api/cases/local-001/route-target", json={
        "edge_id": cands[0]["edge_id"], "sample_index": cands[0]["sample_index"],
    })
    res = client.put("/api/cases/local-001/walkthrough/0", json={
        "confirmed": False, "return_to": 4,
    })
    assert res.status_code == 200, res.text
    wf = client.get("/api/cases/local-001/workflow").json()
    assert wf["steps"][3]["status"] == "available", wf["steps"][3]
    assert "walk-through" in (wf["steps"][3].get("reason") or "")
    case = json.loads(harness["case"].read_text())
    assert "route_target" in case
    # ack-return removed — review stays available until a real review action.


def test_tracing_order(client, harness, fake_home):
    cprint("tracing order", "cyan")
    _choose_forked_target(client, harness)
    forks = _walk_all_yes(client)
    tracing = client.get("/api/cases/local-001/tracing").json()
    assert len(tracing["forks"]) == len(forks)
    assert tracing["text"]
    assert tracing["text"].startswith("1. at ")
    lines = tracing["text"].splitlines()
    assert len(lines) == len(forks)


def test_signoff_freeze_immutability(client, harness, fake_home):
    """Vacuous walk (no forks) can freeze; later artifact change opens N+1."""
    cprint("signoff freeze immutability", "cyan")
    _set_clinician(client)
    _seed_through_step(client, harness, 7)
    res = client.post("/api/cases/local-001/signoff", json={
        "statement": "Reviewed synthetic case.",
    })
    assert res.status_code == 200, res.text
    ver = res.json()["version"]
    assert ver["frozen"] is True
    assert ver["signed_by"] == "Radiologist, A.B."
    arts = ver["artifacts"]
    assert "ct" in arts and "graph" in arts
    assert "route" in arts and "walkthrough" in arts and "tracing" in arts
    assert "snapshot" in arts
    old_sha = arts["ct"]["sha256"]
    vdir = version_snapshot_dir("local-001", ver["n"])
    assert (vdir / "snapshot.json").is_file()
    assert (vdir / "route.json").is_file()

    # Register a new artifact after freeze → opens N+1; old hashes untouched.
    extra = harness["ts"] / "post_freeze_note.txt"
    extra.write_text("downstream edit\n")
    case = json.loads(harness["case"].read_text())
    from app.versions import register_artifact
    register_artifact(case, "post_freeze_note", extra)
    harness["case"].write_text(json.dumps(case, indent=2) + "\n")
    case = json.loads(harness["case"].read_text())
    assert case["versions"][0]["frozen"] is True
    assert case["versions"][0]["artifacts"]["ct"]["sha256"] == old_sha
    assert case["versions"][-1]["n"] == 2
    assert case["versions"][-1]["frozen"] is False
    # N+1 inherits prior hashes.
    assert case["versions"][-1]["artifacts"]["ct"]["sha256"] == old_sha
    wf = client.get("/api/cases/local-001/workflow").json()
    assert wf["steps"][7]["status"] == "available", wf["steps"][7]
    assert "needs sign-off" in (wf["steps"][7].get("reason") or "")


def test_signoff_409_before_walk(client, harness, fake_home):
    _choose_forked_target(client, harness)
    res = client.post("/api/cases/local-001/signoff", json={"statement": "nope"})
    assert res.status_code == 409


def test_duplicate_signoff_409(client, harness, fake_home):
    cprint("duplicate sign-off 409", "cyan")
    _set_clinician(client)
    _seed_through_step(client, harness, 7)
    res = client.post("/api/cases/local-001/signoff", json={
        "statement": "First freeze.",
    })
    assert res.status_code == 200, res.text
    res2 = client.post("/api/cases/local-001/signoff", json={
        "statement": "Second freeze without changes.",
    })
    assert res2.status_code == 409, res2.text


def test_vn_files_never_overwritten(client, harness, fake_home):
    _set_clinician(client)
    _seed_through_step(client, harness, 7)
    res = client.post("/api/cases/local-001/signoff", json={
        "statement": "Freeze once.",
    })
    assert res.status_code == 200, res.text
    n = res.json()["version"]["n"]
    vdir = version_snapshot_dir("local-001", n)
    snap1 = (vdir / "snapshot.json").read_text()
    # Force another freeze attempt into same dir via apply_signoff internals —
    # open N+1 then try writing same n by corrupting: use FreezeDirExists path.
    from app.versions import FreezeDirExists, write_version_snapshot
    with pytest.raises(FreezeDirExists):
        write_version_snapshot(
            "local-001", n,
            route={}, walkthrough={}, tracing={}, snapshot={"n": n},
        )
    assert (vdir / "snapshot.json").read_text() == snap1


def test_report_after_route_change_shows_signed_data(client, harness, fake_home):
    cprint("report frozen under route change", "cyan")
    top, _ = _choose_forked_target(client, harness)
    _walk_all_yes(client)
    res = client.post("/api/cases/local-001/signoff", json={
        "statement": "Signed before retarget.",
    })
    assert res.status_code == 200, res.text
    n = res.json()["version"]["n"]
    report1 = client.get(f"/api/cases/local-001/versions/{n}/report")
    assert report1.status_code == 200
    body1 = report1.text
    assert "Signed before retarget." in body1
    assert "Content-Security-Policy" in report1.headers or True
    assert report1.headers.get("content-security-policy", "").startswith("default-src")

    new_sample = max(0, int(top["sample_index"]) - 5)
    client.put("/api/cases/local-001/route-target", json={
        "edge_id": top["edge_id"], "sample_index": new_sample,
    })
    report2 = client.get(f"/api/cases/local-001/versions/{n}/report")
    assert report2.status_code == 200
    assert report2.text == body1
    # Live case moved on; frozen report unchanged.
    case = json.loads(harness["case"].read_text())
    assert case["route_target"]["sample_index"] == new_sample


def test_review_change_adding_fork_invalidates_walk(client, harness, fake_home):
    """Retarget that changes fork set (via route_hash) invalidates step 7."""
    cprint("route change invalidates step 7", "cyan")
    top, _ = _choose_forked_target(client, harness)
    _walk_all_yes(client)
    wf = client.get("/api/cases/local-001/workflow").json()
    assert wf["steps"][6]["status"] == "done"
    new_sample = max(0, int(top["sample_index"]) - 5)
    assert new_sample != int(top["sample_index"])
    import time
    time.sleep(1.1)
    res = client.put("/api/cases/local-001/route-target", json={
        "edge_id": top["edge_id"], "sample_index": new_sample,
    })
    assert res.status_code == 200
    wf = client.get("/api/cases/local-001/workflow").json()
    assert wf["steps"][6]["status"] == "available", wf["steps"][6]


def test_statement_uid_path_422(client, harness, fake_home):
    cprint("UID/path in statement → 422", "cyan")
    _set_clinician(client)
    _seed_through_step(client, harness, 7)
    res = client.post("/api/cases/local-001/signoff", json={
        "statement": "Patient StudyUID 1.2.840.10008.1.2.1 present",
    })
    assert res.status_code == 422, res.text
    res = client.post("/api/cases/local-001/signoff", json={
        "statement": "Saved under /Users/val/secret/path",
    })
    assert res.status_code == 422, res.text


def test_report_xss_escaped(client, harness, fake_home):
    cprint("XSS in statement escaped in report", "cyan")
    _set_clinician(client)
    _seed_through_step(client, harness, 7)
    evil = 'Reviewed <script>alert(1)</script> & "ok".'
    res = client.post("/api/cases/local-001/signoff", json={"statement": evil})
    assert res.status_code == 200, res.text
    n = res.json()["version"]["n"]
    report = client.get(f"/api/cases/local-001/versions/{n}/report")
    assert report.status_code == 200
    text = report.text
    assert "<script>" not in text
    assert "&lt;script&gt;" in text
    assert "&amp;" in text or "&quot;" in text or "&#" in text


def test_uncertainty_contents(client, harness, fake_home):
    cprint("uncertainty contents", "cyan")
    _choose_forked_target(client, harness)
    _walk_all_yes(client)
    client.post("/api/cases/local-001/records", json={
        "step": 7, "kind": "note", "data": {"text": "check distal tip"},
    })
    res = client.get("/api/cases/local-001/uncertainty")
    assert res.status_code == 200, res.text
    kinds = {u["kind"] for u in res.json()["uncertainty"]}
    assert "target_gap" in kinds
    assert "open_note" in kinds


def test_report_no_identifiers_lists_hashes(client, harness, fake_home):
    cprint("report hygiene", "cyan")
    _set_clinician(client)
    _seed_through_step(client, harness, 7)
    res = client.post("/api/cases/local-001/signoff", json={
        "statement": "Reviewed synthetic case.",
    })
    assert res.status_code == 200, res.text
    n = res.json()["version"]["n"]
    report = client.get(f"/api/cases/local-001/versions/{n}/report")
    assert report.status_code == 200
    assert "text/html" in report.headers.get("content-type", "")
    text = report.text
    assert "PatientName" not in text
    assert "PatientID" not in text
    assert not re.search(r"/Users/|/home/|/tmp/", text)
    assert not re.search(r"\b1\.2\.840\.\d+", text)
    case = json.loads(harness["case"].read_text())
    for name, entry in case["versions"][0]["artifacts"].items():
        assert entry["sha256"] in text, name
    assert "Limitations" in text
    assert "Simulated steering" in text or "simulated" in text.lower()
    assert "Version chain" in text


def test_walk_409_before_priors(client, harness, fake_home):
    _set_clinician(client)
    res = client.put("/api/cases/local-001/walkthrough/0", json={"confirmed": True})
    assert res.status_code == 409


def test_unfrozen_report_409(client, harness, fake_home):
    _set_clinician(client)
    _seed_through_step(client, harness, 7)
    # Open version exists but not frozen.
    case = json.loads(harness["case"].read_text())
    versions = case.get("versions") or []
    if not versions:
        case["versions"] = [{"n": 1, "artifacts": {}, "frozen": False}]
        harness["case"].write_text(json.dumps(case, indent=2) + "\n")
        n = 1
    else:
        n = versions[-1]["n"]
    res = client.get(f"/api/cases/local-001/versions/{n}/report")
    assert res.status_code == 409
