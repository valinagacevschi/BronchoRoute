# Changes: RouteTarget confirm dialog, Generate realistic images checkbox, fork-still progress (#68).
"""RouteTarget confirm controls realism generation and shows fork-still progress."""
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

from test_realism_viewer_playwright import (
    _RealismMock, _fetch_payload, _on_route_uv, _open, _seed,
)
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


def _open_target(page, server: str, mock: _RealismMock) -> None:
    mock.install(page)
    _open(page, server)
    page.wait_for_function(
        "() => document.querySelectorAll('.route-target-card').length > 0",
        timeout=120_000,
    )


def _wait_target(page, edge_id: int) -> None:
    page.wait_for_function(
        "(edge) => window.AIRWAY.route_target"
        " && Number(window.AIRWAY.route_target.edge_id) === Number(edge)",
        arg=edge_id,
        timeout=20_000,
    )


def test_first_target_has_no_dialog_and_generates(server):
    mock = _mock_for(server)
    mock.mode = "done"
    with _playwright_or_skip()() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport=VIEWPORT)
        page.set_default_timeout(120_000)
        dialogs: list[str] = []
        page.on("dialog", lambda dialog: dialogs.append(dialog.message) or dialog.accept())
        _open_target(page, server, mock)
        page.wait_for_function("() => window.__realism.state === 'done'")
        checkbox = page.locator("#route-target-realism")
        assert checkbox.is_visible()
        assert checkbox.is_checked()

        candidates = page.evaluate(
            "() => [...document.querySelectorAll('.route-target-card')]"
            ".map(card => card.textContent)"
        )
        assert candidates
        first_edge = page.evaluate(
            "async () => (await fetch('/api/cases/local-001/route-candidates'))"
            ".json().then(body => body.candidates[0].edge_id)"
        )
        posts_before = mock.post_hits
        mock.mode = "not_ready"
        page.locator(".route-target-card").first.click()
        _wait_target(page, first_edge)
        page.wait_for_function(
            "() => window.__realism.state === 'running'",
            timeout=20_000,
        )
        assert mock.post_hits > posts_before
        assert dialogs == []
        browser.close()


def test_replace_dialog_lists_old_coordinates_and_cancel_keeps_target(server):
    mock = _mock_for(server)
    mock.mode = "done"
    with _playwright_or_skip()() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport=VIEWPORT)
        page.set_default_timeout(120_000)
        _open_target(page, server, mock)
        candidates = page.evaluate(
            "async () => (await fetch('/api/cases/local-001/route-candidates'))"
            ".json().then(body => body.candidates.map(card => card.edge_id))"
        )
        assert len(candidates) >= 2

        page.locator(".route-target-card").first.click()
        _wait_target(page, candidates[0])
        old = page.evaluate("() => window.AIRWAY.route_target.ras_mm")
        old_text = ", ".join(f"{float(value):.2f}" for value in old)

        messages: list[str] = []

        def dismiss(dialog):
            messages.append(dialog.message)
            dialog.dismiss()

        page.once("dialog", dismiss)
        page.locator(".route-target-card").nth(1).click()
        assert messages, "replace dialog missing"
        assert "Replace the current route target?" in messages[0]
        assert "tissue images will be deleted" in messages[0]
        assert old_text in messages[0]
        assert page.evaluate("() => Number(window.AIRWAY.route_target.edge_id)") == candidates[0]

        page.once("dialog", lambda dialog: dialog.accept())
        page.locator(".route-target-card").nth(1).click()
        _wait_target(page, candidates[1])
        browser.close()


def test_checkbox_off_skips_realism_start(server):
    mock = _mock_for(server)
    mock.mode = "done"
    with _playwright_or_skip()() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport=VIEWPORT)
        page.set_default_timeout(120_000)
        _open_target(page, server, mock)
        checkbox = page.locator("#route-target-realism")
        assert checkbox.is_visible()
        checkbox.uncheck()
        assert not checkbox.is_checked()
        posts_before = mock.post_hits
        first_edge = page.evaluate(
            "async () => (await fetch('/api/cases/local-001/route-candidates'))"
            ".json().then(body => body.candidates[0].edge_id)"
        )
        page.locator(".route-target-card").first.click()
        _wait_target(page, first_edge)
        page.wait_for_timeout(500)
        assert mock.post_hits == posts_before
        browser.close()


class _ProgressMock(_RealismMock):
    def __init__(self, *, total: int, ready: int, **kwargs):
        super().__init__(**kwargs)
        self.total = total
        self.ready = ready
        self.status = "running"

    def body(self) -> dict:
        frames = [{
            "group": index,
            "forks": [index + 1],
            "hold_s": self.hold_s,
            "ready": index < self.ready,
            "url": (
                f"/api/cases/local-001/realism/{self.key}/group-{index}.png"
                if index < self.ready else None
            ),
        } for index in range(self.total)]
        return {
            "status": self.status,
            "reason": None,
            "key": self.key,
            "frames": frames,
        }


def test_progress_bar_visible_while_generating_and_hidden_when_done(server):
    payload = _fetch_payload(server)
    hold, u, v, name = _on_route_uv(payload)
    mock = _ProgressMock(hold_s=hold, u=u, v=v, name=name, total=3, ready=1)
    with _playwright_or_skip()() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport=VIEWPORT)
        page.set_default_timeout(120_000)
        _open_target(page, server, mock)
        page.wait_for_function(
            """() => {
              const bar = document.getElementById('route-realism-count');
              return bar && bar.textContent === '1/3 fork stills — group 2 rendering';
            }""",
        )
        assert page.locator("#route-realism-progress").is_visible()
        assert page.locator("#flythrough-progress").is_visible()
        assert page.locator("#flythrough-count").inner_text() == "1/3 fork stills — group 2 rendering"

        mock.ready = 3
        mock.status = "done"
        page.wait_for_function(
            """() => window.__realism.state === 'done'
              && document.getElementById('route-realism-progress').hidden
              && document.getElementById('flythrough-progress').hidden""",
            timeout=20_000,
        )
        browser.close()
