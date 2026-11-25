# Changes: stub-mode tests for realism grouping, cache, API, cancel, pause (#57).
"""Realistic fork worker: groups, cache keys, stub generation, sidecar routes."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from termcolor import cprint

import repo_root

repo_root.ensure()

pytestmark = pytest.mark.server

from app.realism import worker as realism_worker  # noqa: E402
from app.realism.worker import (  # noqa: E402
    cache_key,
    group_forks,
    group_seed,
    reset_realism_state_for_tests,
    verify_model,
)
from app.server import app  # noqa: E402


def _case_root() -> Path | None:
    raw = os.environ.get("REALISM_CASE_ROOT")
    if not raw:
        return None
    return Path(raw).expanduser().resolve()


def _cp(src: Path, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        return
    try:
        subprocess.run(
            ["cp", "-Rc", str(src), str(dest)],
            check=True,
            capture_output=True,
            text=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        if src.is_dir():
            shutil.copytree(src, dest)
        else:
            shutil.copy2(src, dest)


def _clone_local001(src: Path, dest: Path) -> None:
    """Copy only local-001 case artifacts into a writable tmp pipeline root."""
    case_id = "local-001"
    _cp(
        src / "data/extracted/local" / case_id,
        dest / "data/extracted/local" / case_id,
    )
    _cp(src / "ts_out" / case_id, dest / "ts_out" / case_id)
    _cp(src / "out" / case_id, dest / "out" / case_id)
    (dest / "data/extracted/AeroPath").mkdir(parents=True, exist_ok=True)


@pytest.fixture(autouse=True)
def _reset_worker(monkeypatch):
    monkeypatch.setenv("BRONCHOROUTE_TESTING", "1")
    monkeypatch.delenv("BRONCHOROUTE_REALISM_MODEL", raising=False)
    monkeypatch.delenv("BRONCHOROUTE_REALISM_STUB", raising=False)
    reset_realism_state_for_tests()
    yield
    reset_realism_state_for_tests()


def test_group_forks_boundary():
    cprint("group_forks 2.99 joins, 3.0 does not", "cyan")
    forks = [
        {"fork": 1, "s": 10.0, "node": 1},
        {"fork": 2, "s": 12.99, "node": 2},
        {"fork": 3, "s": 13.0, "node": 3},
        {"fork": 4, "s": 20.0, "node": 4},
    ]
    groups = group_forks(forks)
    assert [[f["fork"] for f in g] for g in groups] == [[1, 2], [3], [4]]

    boundary = [
        {"fork": 1, "s": 0.0, "node": 1},
        {"fork": 2, "s": 2.99, "node": 2},
        {"fork": 3, "s": 0.0, "node": 3},
        {"fork": 4, "s": 3.0, "node": 4},
    ]
    # Two separate walks: first pair joins; second pair (0 + 3.0) does not.
    assert [[f["fork"] for f in g] for g in group_forks(boundary[:2])] == [[1, 2]]
    assert [[f["fork"] for f in g] for g in group_forks(boundary[2:])] == [[3], [4]]


def test_seeds_and_cache_key_stable(monkeypatch):
    cprint("seeds + cache key stability", "cyan")
    seed_a = group_seed("local-001", 36)
    seed_b = group_seed("local-001", 36)
    assert seed_a == seed_b
    assert seed_a != group_seed("local-001", 68)

    poses = [
        {
            "hold_s": 31.012,
            "position_ras_mm": [1.004, 2.006, 3.008],
            "forward": [0.1111, 0.2222, 0.3333],
            "up": [0.0, 1.0001, 0.0],
        }
    ]
    key1 = cache_key(model_sha="stub", route=[0, 1, 2], group_poses=poses)
    key2 = cache_key(model_sha="stub", route=[0, 1, 2], group_poses=poses)
    assert key1 == key2
    assert len(key1) == 16
    key_route = cache_key(model_sha="stub", route=[0, 1, 9], group_poses=poses)
    assert key_route != key1


def test_unavailable_missing_and_hash_mismatch(tmp_path, monkeypatch):
    cprint("unavailable model reasons", "cyan")
    monkeypatch.setenv("BRONCHOROUTE_REALISM_MODEL", str(tmp_path / "missing.onnx"))
    ok, reason, sha = verify_model()
    assert ok is False
    assert reason == "model not found"
    assert sha is None

    bad = tmp_path / "bad.onnx"
    bad.write_bytes(b"not-a-real-model")
    monkeypatch.setenv("BRONCHOROUTE_REALISM_MODEL", str(bad))
    ok, reason, sha = verify_model()
    assert ok is False
    assert reason == "model hash mismatch"
    assert sha is None


@pytest.fixture
def local001_client(tmp_path, monkeypatch):
    src = _case_root()
    if src is None:
        pytest.skip("set REALISM_CASE_ROOT for local-001 stub e2e")
    root = tmp_path / "pipeline"
    cprint(f"clone local-001 tree → {root}", "cyan")
    _clone_local001(src, root)
    monkeypatch.setenv("BRONCHOROUTE_REALISM_STUB", "1")
    monkeypatch.setenv("BRONCHOROUTE_TESTING", "1")
    monkeypatch.setattr("paths.PIPELINE", root)
    monkeypatch.setattr("paths.LOCAL_CASES", root / "data/extracted/local")
    monkeypatch.setattr("paths.AEROPATH", root / "data/extracted/AeroPath")
    monkeypatch.setattr("paths.TS_OUT", root / "ts_out")
    monkeypatch.setattr("paths.OUT", root / "out")
    (root / "data/extracted/AeroPath").mkdir(parents=True, exist_ok=True)
    reset_realism_state_for_tests()
    client = TestClient(app, base_url="http://127.0.0.1")
    return client, root


def _wait_status(client, want, *, timeout=30.0):
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        last = client.get("/api/cases/local-001/realism").json()
        if last["status"] == want:
            return last
        time.sleep(0.05)
    raise AssertionError(f"timed out waiting for {want}: {last}")


def test_stub_e2e_cache_png_and_manifest(local001_client):
    client, root = local001_client
    cprint("stub e2e start → ready order → cache hit", "cyan")

    # Slow each Euler/stub call so ready flips are observable in order.
    original = realism_worker._run_euler
    gate = {"n": 0}

    def slow_euler(encoded, seed, cancel):
        time.sleep(0.08)
        gate["n"] += 1
        return original(encoded, seed, cancel)

    realism_worker._run_euler = slow_euler
    try:
        started = client.post("/api/cases/local-001/realism/start")
        assert started.status_code == 200
        body = started.json()
        assert body["status"] in {"running", "done"}
        assert body["key"]
        assert body["frames"]
        assert body["frames"][0]["ready"] is False or body["status"] == "done"

        seen_prefix = []
        deadline = time.monotonic() + 60.0
        while time.monotonic() < deadline:
            mid = client.get("/api/cases/local-001/realism").json()
            ready_flags = [f["ready"] for f in mid["frames"]]
            # Ready flags must be a True-prefix then Falses.
            if False in ready_flags:
                first_false = ready_flags.index(False)
                assert all(ready_flags[:first_false])
                assert not any(ready_flags[first_false:])
            seen_prefix.append(tuple(ready_flags))
            if mid["status"] == "done":
                break
            time.sleep(0.02)
        else:
            raise AssertionError("generation did not finish")

        done = client.get("/api/cases/local-001/realism").json()
        assert done["status"] == "done"
        assert all(f["ready"] for f in done["frames"])
        key = done["key"]
        cache_dir = root / "ts_out" / "local-001" / "realism" / key
        manifest = json.loads((cache_dir / "manifest.json").read_text())
        assert manifest == done["frames"]

        png = client.get(f"/api/cases/local-001/realism/{key}/group-0.png")
        assert png.status_code == 200
        assert png.headers["content-type"].startswith("image/png")
        assert client.get(f"/api/cases/local-001/realism/{key}/../x.png").status_code == 404
        assert client.get(
            f"/api/cases/local-001/realism/{key}/group-999.png"
        ).status_code == 404
        assert client.get(
            f"/api/cases/local-001/realism/{key}/nope.png"
        ).status_code == 404

        mtimes = {
            p.name: p.stat().st_mtime_ns
            for p in cache_dir.iterdir()
            if p.suffix in {".png", ".json"}
        }
        again = client.post("/api/cases/local-001/realism/start").json()
        assert again["status"] == "done"
        assert again["key"] == key
        assert gate["n"] > 0
        after = {
            p.name: p.stat().st_mtime_ns
            for p in cache_dir.iterdir()
            if p.suffix in {".png", ".json"}
        }
        assert after == mtimes
    finally:
        realism_worker._run_euler = original


def test_cancel_on_route_change(local001_client, monkeypatch):
    client, root = local001_client
    cprint("cancel A when B starts with new route", "cyan")

    hold = threading.Event()
    released = threading.Event()
    original_busy = realism_worker.job_in_progress

    def busy():
        if not released.is_set():
            hold.set()
            return True
        return original_busy()

    monkeypatch.setattr(realism_worker, "job_in_progress", busy)

    first = client.post("/api/cases/local-001/realism/start").json()
    assert first["status"] == "running"
    key_a = first["key"]
    assert hold.wait(timeout=5.0)

    # Force a different cache key by changing the planned route.
    real_plan = realism_worker.plan_frames

    def plan_b(case_id, payload, model_sha):
        key, frames, groups, poses = real_plan(case_id, payload, model_sha)
        route = list(payload.get("route") or []) + [-1]
        key_b = cache_key(model_sha=model_sha, route=route, group_poses=poses)
        return key_b, frames, groups, poses

    monkeypatch.setattr(realism_worker, "plan_frames", plan_b)
    second = client.post("/api/cases/local-001/realism/start").json()
    key_b = second["key"]
    assert key_b != key_a

    released.set()
    done = _wait_status(client, "done", timeout=60.0)
    assert done["key"] == key_b
    assert all(f["ready"] for f in done["frames"])

    dir_a = root / "ts_out" / "local-001" / "realism" / key_a
    dir_b = root / "ts_out" / "local-001" / "realism" / key_b
    assert not dir_a.exists()
    assert dir_b.is_dir()


def test_pause_while_job_in_progress(local001_client, monkeypatch):
    client, root = local001_client
    cprint("pause while job_in_progress", "cyan")
    busy = {"on": True}

    monkeypatch.setattr(
        realism_worker, "job_in_progress", lambda: bool(busy["on"])
    )
    started = client.post("/api/cases/local-001/realism/start").json()
    key = started["key"]
    time.sleep(0.4)
    mid = client.get("/api/cases/local-001/realism").json()
    assert mid["status"] == "running"
    assert not any(f["ready"] for f in mid["frames"])
    cache_dir = root / "ts_out" / "local-001" / "realism" / key
    pngs = list(cache_dir.glob("group-*.png")) if cache_dir.exists() else []
    assert pngs == []

    busy["on"] = False
    done = _wait_status(client, "done", timeout=60.0)
    assert all(f["ready"] for f in done["frames"])


def test_unavailable_via_api(tmp_path, monkeypatch):
    cprint("API unavailable on hash mismatch", "cyan")
    # Minimal case tree so CaseRef.parse succeeds.
    case_id = "local-001"
    local = tmp_path / "data/extracted/local" / case_id
    local.mkdir(parents=True)
    (local / "case.json").write_text(json.dumps({
        "id": case_id,
        "records": [],
        "artifacts": {},
    }))
    (tmp_path / "ts_out" / case_id).mkdir(parents=True)
    (tmp_path / "out" / case_id).mkdir(parents=True)
    monkeypatch.setattr("paths.PIPELINE", tmp_path)
    monkeypatch.setattr("paths.LOCAL_CASES", tmp_path / "data/extracted/local")
    monkeypatch.setattr("paths.AEROPATH", tmp_path / "data/extracted/AeroPath")
    monkeypatch.setattr("paths.TS_OUT", tmp_path / "ts_out")
    monkeypatch.setattr("paths.OUT", tmp_path / "out")
    bad = tmp_path / "tiny.onnx"
    bad.write_bytes(b"x")
    monkeypatch.setenv("BRONCHOROUTE_REALISM_MODEL", str(bad))
    monkeypatch.delenv("BRONCHOROUTE_REALISM_STUB", raising=False)
    reset_realism_state_for_tests()
    client = TestClient(app, base_url="http://127.0.0.1")
    body = client.post(f"/api/cases/{case_id}/realism/start").json()
    assert body["status"] == "unavailable"
    assert body["reason"] == "model hash mismatch"


def test_local001_groups_include_6_and_7(local001_client):
    client, _root = local001_client
    cprint("local-001 forks 6+7 share a group", "cyan")
    body = client.post("/api/cases/local-001/realism/start").json()
    done = _wait_status(client, "done", timeout=60.0)
    groups = {tuple(frame["forks"]) for frame in done["frames"]}
    assert (6, 7) in groups or any(
        frame["forks"] == [6, 7] for frame in done["frames"]
    )
    assert body["key"] == done["key"]
