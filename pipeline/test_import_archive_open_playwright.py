# Changes: Archive-selected path must scan or show an in-page guard, never quit silently.
"""Open Archive through the Electron bridge reaches scan and never fails silently."""

from __future__ import annotations

import os
import socket
import subprocess
import time
import urllib.request
from pathlib import Path

import pytest

from test_viewer_v2_playwright import _playwright_or_skip
from tracer_fixture import write_archive

BRIDGE = """
window.__openArchive = null;
window.bronchoroute = {
  openArchive: async () => {},
  onOpenArchive: callback => { window.__openArchive = callback; },
  getPathForFile: () => null,
  weightsRequired: async () => false,
  weightsStatus: async () => ({ready: true}),
};
"""


@pytest.fixture
def archive_server(tmp_path):
    root = tmp_path / "state"
    root.mkdir()
    archive = write_archive(tmp_path / "input.zip")
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    base = f"http://127.0.0.1:{port}"
    env = {
        **os.environ,
        "LUNGNAV_PIPELINE_ROOT": str(root),
        "BRONCHOROUTE_FAKE_SEGMENT": "1",
        "BRONCHOROUTE_TESTING": "1",
        "BRONCHOROUTE_FAKE_STEP_SECONDS": "1",
        "HOME": str(tmp_path),
    }
    env.pop("PYTHONPATH", None)
    proc = subprocess.Popen(
        ["uv", "run", "python", "app_server.py", "--port", str(port)],
        cwd=Path(__file__).resolve().parent, env=env,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        for _ in range(200):
            assert proc.poll() is None
            try:
                with urllib.request.urlopen(f"{base}/healthz", timeout=1):
                    break
            except OSError:
                time.sleep(.1)
        else:
            pytest.fail("import server did not start")
        yield base, archive
    finally:
        proc.terminate()
        proc.wait(timeout=10)


def _scan_requests(page):
    requests = []
    page.on("request", lambda request: requests.append(request.url)
            if request.method == "POST" and request.url.endswith("/api/imports/scan") else None)
    return requests


def _open(page, archive):
    page.wait_for_function("() => typeof window.__openArchive === 'function'")
    page.evaluate("path => { window.__openArchive(path); }", str(archive))


def test_archive_selected_path_scans_and_shows_reading(archive_server):
    base, archive = archive_server
    with _playwright_or_skip()() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        scans = _scan_requests(page)
        page.add_init_script(BRIDGE)
        page.goto(base)
        page.locator("#archive-drop").wait_for(state="visible")

        def hold(route):
            time.sleep(0.4)
            route.continue_()

        page.route("**/api/imports/scan", hold)
        _open(page, archive)
        page.locator("#import-message").get_by_text("Reading Archive…").wait_for(timeout=10000)
        assert scans == [f"{base}/api/imports/scan"]
        page.locator("#series-picker").wait_for(state="visible", timeout=20000)
        assert not errors, errors
        browser.close()


def test_archive_selected_while_job_runs_shows_message(archive_server):
    base, archive = archive_server
    with _playwright_or_skip()() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        errors = []
        dialogs = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.on("dialog", lambda dialog: (dialogs.append(dialog.message), dialog.dismiss()))
        scans = _scan_requests(page)
        page.add_init_script(BRIDGE)
        page.goto(base)
        page.locator("#archive-drop").wait_for(state="visible")
        _open(page, archive)
        page.locator("#series-picker").wait_for(state="visible", timeout=20000)
        page.locator("#series-confirm").click()
        page.locator("#processing").wait_for(state="visible")
        page.wait_for_function("() => !document.querySelector('#processing-cancel').disabled")

        before = len(scans)
        _open(page, archive)
        page.locator("#import-message").get_by_text("Cancel current processing first.").wait_for(timeout=5000)
        assert dialogs == []
        assert len(scans) == before
        assert page.locator("#processing").is_visible()
        assert not errors, errors
        browser.close()
