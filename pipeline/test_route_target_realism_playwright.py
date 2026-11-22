# Changes: RouteTarget is automatic; no cards, pick point, or realism controls.
"""A lesion auto-selects the RouteTarget, and no route-selection controls appear."""
from __future__ import annotations

import json
import os
import socket
import subprocess
import time
import urllib.request
from pathlib import Path

import pytest
from termcolor import cprint

from test_realism_viewer_playwright import _RealismMock, _fetch_payload, _on_route_uv, _open, _seed
from test_viewer_v2_playwright import VIEWPORT, _playwright_or_skip

ROOT = Path(__file__).resolve().parent


def _seed_target(tmp: Path) -> Path:
    case_path = _seed(tmp)
    document = json.loads(case_path.read_text())
    document["lesion"] = {
        "ras_mm": [16.0, 0.0, -20.0],
        "confirmed_by": "Radiologist, A.B.",
        "at": "2026-01-01T00:00:01Z",
    }
    case_path.write_text(json.dumps(document, indent=2) + "\n")
    return case_path


@pytest.fixture
def server(tmp_path: Path):
    _seed_target(tmp_path)
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    base = f"http://127.0.0.1:{port}"
    home = tmp_path / "fake_home"
    home.mkdir()
    env = {**os.environ, "HOME": str(home), "LUNGNAV_PIPELINE_ROOT": str(tmp_path)}
    env.pop("PYTHONPATH", None)
    log_path = tmp_path / "server.err"
    log = log_path.open("w")
    cprint(f"route-target realism server on {port}", "cyan")
    proc = subprocess.Popen(
        ["uv", "run", "python", "app_server.py", "--port", str(port)],
        cwd=ROOT,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=log,
    )
    try:
        for _ in range(200):
            if proc.poll() is not None:
                log.flush()
                pytest.fail(f"server exited\n{log_path.read_text()[-2000:]}")
            try:
                with urllib.request.urlopen(f"{base}/api/cases", timeout=1):
                    break
            except OSError:
                time.sleep(0.1)
        else:
            pytest.fail("route-target realism server did not start")
        body = json.dumps({"role": "Radiologist", "initials": "A.B."}).encode()
        request = urllib.request.Request(
            f"{base}/api/clinician",
            data=body,
            headers={"Content-Type": "application/json"},
            method="PUT",
        )
        with urllib.request.urlopen(request, timeout=5) as res:
            assert res.status == 200
        yield base
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)
        log.close()


def _mock_for(server: str) -> _RealismMock:
    payload = _fetch_payload(server)
    hold, u, v, name = _on_route_uv(payload)
    return _RealismMock(hold_s=hold, u=u, v=v, name=name)


def test_lesion_auto_selects_without_route_controls(server):
    mock = _mock_for(server)
    mock.mode = "done"
    with _playwright_or_skip()() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport=VIEWPORT)
        page.set_default_timeout(120_000)
        dialogs: list[str] = []
        page.on("dialog", lambda dialog: dialogs.append(dialog.message) or dialog.accept())
        mock.install(page)
        _open(page, server)
        posts_before = mock.post_hits
        page.wait_for_function(
            """() => {
              const rt = window.AIRWAY && window.AIRWAY.route_target;
              return !!(rt && !rt.stale && rt.rank === 1);
            }""",
            timeout=120_000,
        )
        top_edge = page.evaluate(
            "async () => (await fetch('/api/cases/local-001/route-candidates'))"
            ".json().then(body => body.candidates[0].edge_id)"
        )
        assert page.evaluate("() => Number(window.AIRWAY.route_target.edge_id)") == top_edge
        assert page.locator("#route-target-panel").is_visible()
        assert page.locator(".route-target-card").count() == 0
        assert page.locator("#route-target-pick").count() == 0
        assert page.locator("#route-target-realism").count() == 0
        assert page.locator("#route-realism-progress").count() == 0
        page.wait_for_timeout(500)
        assert mock.post_hits == posts_before
        assert dialogs == []
        browser.close()
