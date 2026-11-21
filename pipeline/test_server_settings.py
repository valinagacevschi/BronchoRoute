# Changes: hide legacy review behind the default-off feature flag.
# Changes: viewer-only read-only root, blank token, and scoped path redaction.
# Changes: sidecar subprocess recovery, token, logs and global job guard tests.
# Changes: tests for LUNGNAV_VIEWER_ONLY / CASES / CACHE_DIR / healthz.
"""Server deploy settings — no TotalSegmentator, Playwright, or real patient data."""

from __future__ import annotations

import json
import os
import selectors
import stat
import subprocess
import sys
import time
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

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
@pytest.mark.parametrize("url", ["/", "/api/cases", "/ct_panes.js", "/healthz"])
def test_sidecar_token(client, monkeypatch, url):
    monkeypatch.setenv("BRONCHOROUTE_TOKEN", "test-launch-secret")
    assert client.get(url).status_code == 401
    assert client.get(url, headers={"X-BronchoRoute-Token": "wrong"}).status_code == 401
    assert client.get(url, headers={"X-BronchoRoute-Token": "test-launch-secret"}).status_code == 200


@pytest.mark.parametrize("token", ["", " ", "\t", " \n"])
def test_blank_token_disables_guard(client, monkeypatch, token):
    monkeypatch.setenv("BRONCHOROUTE_TOKEN", token)
    assert client.get("/healthz").status_code == 200
    assert client.get("/healthz", headers={"X-BronchoRoute-Token": "wrong"}).status_code == 200
    assert client.get("/api/cases").status_code == 200


def test_job_child_env_omits_token(monkeypatch):
    from app.jobs import _clean_env

    monkeypatch.setenv("BRONCHOROUTE_TOKEN", "test-launch-secret")
    assert "BRONCHOROUTE_TOKEN" not in _clean_env()


def test_sidecar_process_lifecycle(tmp_path):
    script = tmp_path / "run_totalsegmentator.py"
    script.write_text("import time\ntime.sleep(60)\n")
    orphan = subprocess.Popen([sys.executable, str(script)], start_new_session=True)
    unrelated = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(60)"], start_new_session=True,
    )
    job_path = tmp_path / "ts_out/local-001/job.json"
    job_path.parent.mkdir(parents=True)
    job_path.write_text(json.dumps({"status": "running", "pid": orphan.pid}))
    registry = tmp_path / "app_state/jobs.pids"
    registry.parent.mkdir()
    registry.write_text(json.dumps([
        {"pid": orphan.pid, "pgid": orphan.pid, "case_id": "local-001"},
        {"pid": unrelated.pid, "pgid": unrelated.pid, "case_id": "local-002"},
    ]))
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    (scratch / "discard").write_text("temporary")
    env = dict(os.environ)
    env.pop("PYTHONPATH", None)
    env.pop("LUNGNAV_PORT", None)
    env.pop("LUNGNAV_HOST", None)
    env.update(
        LUNGNAV_DATA_ROOT=str(tmp_path), LUNGNAV_TMP_DIR=str(scratch),
        LUNGNAV_LOG_DIR=str(tmp_path / "log-output"),
        BRONCHOROUTE_TOKEN="test-launch-secret",
    )
    proc = subprocess.Popen(
        [sys.executable, "app_server.py", "--port", "0", "--exit-on-stdin-close"],
        cwd=Path(__file__).parent, env=env, stdin=subprocess.PIPE,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    try:
        with selectors.DefaultSelector() as selector:
            selector.register(proc.stdout, selectors.EVENT_READ)
            assert selector.select(timeout=20), "missing port announcement"
        line = proc.stdout.readline().strip()
        assert line.startswith("BRONCHOROUTE_PORT=")
        port = int(line.split("=", 1)[1])
        base = f"http://127.0.0.1:{port}"
        for _ in range(100):
            try:
                with urlopen(Request(base + "/healthz", headers={
                    "X-BronchoRoute-Token": "test-launch-secret",
                }), timeout=0.2) as response:
                    assert response.status == 200
                break
            except OSError:
                time.sleep(0.03)
        else:
            proc.kill()
            pytest.fail("sidecar did not start: " + proc.communicate()[1])
        for url in ("/", "/api/cases", "/ct_panes.js", "/healthz"):
            with pytest.raises(HTTPError) as error:
                urlopen(base + url, timeout=1)
            assert error.value.code == 401
            with urlopen(Request(base + url, headers={
                "X-BronchoRoute-Token": "test-launch-secret",
            }), timeout=1) as response:
                assert response.status == 200
        assert orphan.wait(timeout=2) < 0
        with pytest.raises(ProcessLookupError):
            os.killpg(orphan.pid, 0)
        assert unrelated.poll() is None
        assert not registry.exists()
        assert json.loads(job_path.read_text())["error"] == "app restarted"
        assert json.loads(job_path.read_text())["status"] == "failed"
        assert list(scratch.iterdir()) == []
        assert "sidecar started" in (tmp_path / "log-output/app.log").read_text()
        proc.stdin.close()
        assert proc.wait(timeout=5) == 0
        assert proc.stdout.read() == ""
    finally:
        for child in (proc, orphan, unrelated):
            if child.poll() is None:
                child.kill()
            child.wait(timeout=5)


def test_viewer_only_readonly_root_serves_healthz(tmp_path):
    data_root = tmp_path / "data-root"
    data_root.mkdir()
    data_root.chmod(0o555)
    proc = None
    try:
        env = dict(os.environ)
        for name in (
            "PYTHONPATH",
            "LUNGNAV_PORT",
            "LUNGNAV_HOST",
            "LUNGNAV_TMP_DIR",
            "LUNGNAV_LOG_DIR",
            "LUNGNAV_CACHE_DIR",
            "LUNGNAV_PIPELINE_ROOT",
            "BRONCHOROUTE_TOKEN",
            "LUNGNAV_ALLOWED_HOSTS",
        ):
            env.pop(name, None)
        env["LUNGNAV_DATA_ROOT"] = str(data_root)
        env["LUNGNAV_VIEWER_ONLY"] = "1"
        proc = subprocess.Popen(
            [sys.executable, "app_server.py", "--port", "0", "--exit-on-stdin-close"],
            cwd=Path(__file__).parent,
            env=env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        with selectors.DefaultSelector() as selector:
            selector.register(proc.stdout, selectors.EVENT_READ)
            announced = selector.select(timeout=20)
        if not announced:
            proc.kill()
            pytest.fail("missing port announcement: " + proc.communicate()[1])
        line = proc.stdout.readline().strip()
        assert line.startswith("BRONCHOROUTE_PORT=")
        port = int(line.split("=", 1)[1])
        healthz = f"http://127.0.0.1:{port}/healthz"
        for _ in range(100):
            try:
                with urlopen(healthz, timeout=0.2) as response:
                    assert response.status == 200
                    assert response.read() == b"ok"
                break
            except OSError:
                time.sleep(0.03)
        else:
            proc.kill()
            pytest.fail("sidecar did not start: " + proc.communicate()[1])
        assert list(data_root.iterdir()) == []
        proc.stdin.close()
        assert proc.wait(timeout=5) == 0
        assert list(data_root.iterdir()) == []
    finally:
        if proc is not None and proc.poll() is None:
            proc.kill()
            proc.wait(timeout=5)
        data_root.chmod(0o755)


def test_rotating_log_redacts(tmp_path, monkeypatch):
    import logging
    import paths
    from app.jobs import JobState, initialize_sidecar

    monkeypatch.delenv("LUNGNAV_VIEWER_ONLY", raising=False)
    monkeypatch.setattr(paths, "PIPELINE", tmp_path)
    monkeypatch.setattr(paths, "TMP_DIR", tmp_path / "tmp")
    monkeypatch.setattr(paths, "LOG_DIR", tmp_path / "logs")
    initialize_sidecar()
    logger = logging.getLogger("bronchoroute.jobs")
    fake_uid = ".".join(["98765", "43210", "12345", "67890"])
    logger.info("synthetic value %s", fake_uid)
    job = JobState("local-001", "segment")
    job.append_log("synthetic job value " + fake_uid)
    assert job.log_lines == ["synthetic job value [redacted]"]
    output = (paths.LOG_DIR / "app.log").read_text()
    assert fake_uid not in output
    assert "synthetic job value [redacted]" in output
    from logging.handlers import RotatingFileHandler

    handlers = [h for h in logging.getLogger().handlers if isinstance(h, RotatingFileHandler)]
    assert any(h.maxBytes == 5 * 1024 * 1024 and h.backupCount == 5 for h in handlers)
    for owner in (logging.getLogger(), logging.getLogger("uvicorn"), logging.getLogger("uvicorn.error")):
        for handler in handlers:
            owner.removeHandler(handler)
    for handler in handlers:
        handler.close()


def test_redact_log_line_keeps_routes(monkeypatch):
    import paths
    from app.jobs import redact_log_line

    monkeypatch.setattr(paths, "PIPELINE", Path("/opt/lungnav/pipeline"))
    route = "/api/cases/local-001/payload"
    assert redact_log_line(route) == route
    assert redact_log_line("step run_totalsegmentator.py") == "step run_totalsegmentator.py"
    assert redact_log_line("/Users/x/Downloads/CT-1.zip") == "[redacted]"
    uid = ".".join(["11", "22", "33", "44"])
    assert redact_log_line("series " + uid) == "series [redacted]"
    assert redact_log_line("GET " + route + " /home/demo/scan.nii.gz") == "GET " + route + " [path]"
    assert redact_log_line("read /tmp/scratch/a.bin") == "read [path]"
    assert redact_log_line("read /var/folders/x/a.bin") == "read [path]"
    assert redact_log_line("read /private/var/x/a.bin") == "read [path]"
    assert redact_log_line("read /Volumes/Data/a.bin") == "read [path]"
    saved = "/opt/lungnav/pipeline/ts_out/local-001/job.json"
    assert redact_log_line("saved " + saved) == "saved [path]"
    other = "/opt/lungnav/other/job.json"
    assert redact_log_line("saved " + other) == "saved " + other
    assert redact_log_line("path data/extracted/local/ct.nii.gz") == "path ct.nii.gz"


def test_job_commands_use_current_python(harness):
    from app.caseref import CaseRef
    from app.jobs import _segment_commands

    commands = _segment_commands(CaseRef.parse("local-001"))
    assert [cmd[:2] for cmd in commands] == [
        [sys.executable, "run_totalsegmentator.py"],
        [sys.executable, "build_graph.py"],
        [sys.executable, "run_nodules.py"],
    ]
    nodule = commands[2]
    assert nodule[nodule.index("--case-json") + 1] == str(
        CaseRef.parse("local-001").case_json
    )
    assert nodule[nodule.index("--graph") + 1].endswith("local-001_graph.json")


def test_job_conflict_across_cases(client, harness, monkeypatch):
    import threading
    import paths
    from app import jobs

    _write_case_tree(paths.PIPELINE, "local-002")
    monkeypatch.setattr(jobs, "require_prior_steps", lambda *args: None)
    released = threading.Event()
    launched = threading.Event()

    def launcher(cmd, cwd):
        class Proc:
            pid = 2_000_000_001
            stdout = iter(["synthetic pipeline output\n"])

            def wait(self, timeout=None):
                released.wait(timeout=2)
                return 0

        launched.set()
        return Proc()

    jobs.set_job_launcher(launcher)
    try:
        assert client.put("/api/clinician", json={
            "role": "Radiologist", "initials": "A.B.",
        }).status_code == 200
        assert client.post("/api/cases/local-001/segment").status_code == 200
        assert launched.wait(timeout=2)
        registry = paths.PIPELINE / "app_state/jobs.pids"
        for _ in range(100):
            if registry.exists():
                break
            time.sleep(0.01)
        entries = json.loads(registry.read_text())
        assert entries[0]["pid"] == 2_000_000_001
        assert entries[0]["pgid"] == 2_000_000_001
        response = client.post("/api/cases/local-002/segment")
        assert response.status_code == 409
        assert "another pipeline job" in response.json()["detail"]
    finally:
        released.set()
        jobs.reset_jobs_for_tests()
    assert not registry.exists()
