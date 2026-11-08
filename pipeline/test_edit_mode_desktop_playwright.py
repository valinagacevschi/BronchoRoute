# Changes: desktop edit mode uses the editing page, not the read-only viewer (#70).
"""The desktop start page is the editing page: top-level, Mark Lesion visible.

The /workflow shell was removed in 00974a4. The renderer is in edit mode when it
is top-level and the path is not "/", so the desktop opens the renderer file.
"""
from __future__ import annotations

import os
import socket
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest
from termcolor import cprint

from test_realism_viewer_playwright import _seed
from test_viewer_v2_playwright import VIEWPORT, _playwright_or_skip
from tracer_fixture import write_archive

ROOT = Path(__file__).resolve().parent
EDIT_PATH = "/renderer.template.html"


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    root = tmp_path_factory.mktemp("edit_mode")
    _seed(root)
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    base = f"http://127.0.0.1:{port}"
    home = root / "fake_home"
    home.mkdir()
    env = {**os.environ, "HOME": str(home), "LUNGNAV_PIPELINE_ROOT": str(root)}
    env.pop("PYTHONPATH", None)
    cprint(f"edit-mode server on {port}", "cyan")
    proc = subprocess.Popen(
        ["uv", "run", "python", "app_server.py", "--port", str(port)],
        cwd=ROOT,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        for _ in range(200):
            if proc.poll() is not None:
                pytest.fail("edit-mode server exited")
            try:
                with urllib.request.urlopen(f"{base}/healthz", timeout=1):
                    break
            except OSError:
                time.sleep(0.1)
        else:
            pytest.fail("edit-mode server did not start")
        yield base
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)


@pytest.fixture
def import_server(tmp_path):
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
        cwd=ROOT,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        for _ in range(200):
            if proc.poll() is not None:
                pytest.fail("import server exited")
            try:
                with urllib.request.urlopen(f"{base}/healthz", timeout=1):
                    break
            except OSError:
                time.sleep(0.1)
        else:
            pytest.fail("import server did not start")
        yield base, archive
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)


def test_workflow_shell_is_gone(server):
    with pytest.raises(urllib.error.HTTPError) as error:
        urllib.request.urlopen(f"{server}/workflow?case=local-001", timeout=10)
    assert error.value.code == 404


def test_edit_page_is_top_level_and_marks_lesion(server):
    sync_playwright = _playwright_or_skip()
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page(viewport=VIEWPORT)
        page.goto(
            f"{server}{EDIT_PATH}?case=local-001&allow_review=1&debug=1&variant=ct-panes",
            wait_until="domcontentloaded",
            timeout=120_000,
        )
        page.wait_for_function(
            "() => window.__VIEWER_MODE__ === false",
            timeout=120_000,
        )
        mark = page.locator("#ct-mark-lesion")
        mark.wait_for(state="visible", timeout=120_000)
        assert mark.is_enabled()
        browser.close()


def test_import_screen_opens_with_no_case(server):
    sync_playwright = _playwright_or_skip()
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page(viewport=VIEWPORT)
        page.goto(f"{server}{EDIT_PATH}", wait_until="domcontentloaded", timeout=120_000)
        assert page.evaluate("() => window.__VIEWER_MODE__") is False
        open_button = page.locator("#archive-open")
        open_button.wait_for(state="visible", timeout=60_000)
        open_button.click()
        page.locator("#archive-drop").wait_for(state="visible", timeout=60_000)
        browser.close()


def test_import_on_edit_page_stays_in_edit_mode(import_server):
    base, archive = import_server
    sync_playwright = _playwright_or_skip()
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page(viewport=VIEWPORT)
        page.goto(f"{base}{EDIT_PATH}", wait_until="domcontentloaded", timeout=120_000)
        page.locator("#archive-choose").wait_for(state="visible", timeout=60_000)
        with page.expect_file_chooser() as chooser:
            page.locator("#archive-choose").click()
        chooser.value.set_files(archive)
        page.locator("#series-picker").wait_for(state="visible", timeout=60_000)
        page.locator("#series-confirm").click()
        page.locator("#processing").wait_for(state="visible", timeout=60_000)
        page.wait_for_url(f"**{EDIT_PATH}?case=local-001", timeout=60_000)
        page.wait_for_function("() => window.__VIEWER_MODE__ === false", timeout=60_000)
        page.locator("#switcher button", has_text="CT panes").click()
        page.locator("#ct-mark-lesion").wait_for(state="visible", timeout=120_000)
        browser.close()
