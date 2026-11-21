# Changes: ? help button and shared help sheet on the case, import and viewer pages (#71).
"""The help button opens one help sheet on every screen, and Escape closes it."""
from __future__ import annotations

import os
import socket
import subprocess
import time
import urllib.request
from contextlib import contextmanager
from pathlib import Path

import pytest
from termcolor import cprint

from test_realism_viewer_playwright import _seed
from test_viewer_v2_playwright import VIEWPORT, _playwright_or_skip

ROOT = Path(__file__).resolve().parent
EDIT_PATH = "/renderer.template.html"


@contextmanager
def _server(env, label):
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    base = f"http://127.0.0.1:{port}"
    cprint(f"{label} server on {port}", "cyan")
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
                pytest.fail(f"{label} server exited")
            try:
                with urllib.request.urlopen(f"{base}/healthz", timeout=1):
                    break
            except OSError:
                time.sleep(0.1)
        else:
            pytest.fail(f"{label} server did not start")
        yield base
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    root = tmp_path_factory.mktemp("help_page")
    _seed(root)
    home = root / "fake_home"
    home.mkdir()
    env = {**os.environ, "HOME": str(home), "LUNGNAV_PIPELINE_ROOT": str(root)}
    env.pop("PYTHONPATH", None)
    with _server(env, "help") as base:
        yield base


@pytest.fixture(scope="module")
def viewer_server(tmp_path_factory):
    root = tmp_path_factory.mktemp("help_viewer")
    _seed(root)
    home = root / "fake_home"
    home.mkdir()
    env = {
        **os.environ,
        "HOME": str(home),
        "LUNGNAV_PIPELINE_ROOT": str(root),
        "LUNGNAV_VIEWER_ONLY": "1",
    }
    env.pop("PYTHONPATH", None)
    with _server(env, "help-viewer") as base:
        yield base


def _open_edit(page, base):
    page.goto(
        f"{base}{EDIT_PATH}?case=local-001&allow_review=1&debug=1&variant=ct-panes",
        wait_until="domcontentloaded",
        timeout=120_000,
    )
    page.wait_for_function("() => window.__VIEWER_MODE__ === false", timeout=120_000)
    page.locator("#ct-mark-lesion").wait_for(state="visible", timeout=120_000)


def test_help_button_on_case_page(server):
    sync_playwright = _playwright_or_skip()
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page(viewport=VIEWPORT)
        _open_edit(page, server)
        button = page.locator("#help-open")
        button.wait_for(state="visible", timeout=60_000)
        assert button.get_attribute("aria-label") == "Help"
        browser.close()


def test_help_button_on_import_screen(server):
    sync_playwright = _playwright_or_skip()
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page(viewport=VIEWPORT)
        page.goto(f"{server}{EDIT_PATH}", wait_until="domcontentloaded", timeout=120_000)
        open_button = page.locator("#archive-open")
        open_button.wait_for(state="visible", timeout=60_000)
        open_button.click()
        page.locator("#archive-drop").wait_for(state="visible", timeout=60_000)
        assert page.locator("#help-open").is_visible()
        browser.close()


def test_help_button_on_viewer_page(viewer_server):
    sync_playwright = _playwright_or_skip()
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page(viewport=VIEWPORT)
        page.goto(f"{viewer_server}/", wait_until="domcontentloaded", timeout=120_000)
        page.wait_for_function("() => window.__VIEWER_MODE__ === true", timeout=120_000)
        button = page.locator("#help-open")
        button.wait_for(state="visible", timeout=60_000)
        assert button.get_attribute("aria-label") == "Help"
        browser.close()


def test_help_sheet_sections_and_escape(server):
    sync_playwright = _playwright_or_skip()
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page(viewport=VIEWPORT)
        _open_edit(page, server)
        page.locator("#help-open").click()
        overlay = page.locator("#help-overlay")
        overlay.wait_for(state="visible", timeout=30_000)
        headings = overlay.locator(".help-section h2").all_inner_texts()
        for wanted in [
            "Mark a lesion",
            "RouteTarget",
            "Layers and panels",
            "Viewer and edit mode",
        ]:
            assert wanted in headings
        text = overlay.inner_text()
        assert "Mark Lesion" in text
        assert "Confirm Lesion" in text
        page.keyboard.press("Escape")
        overlay.wait_for(state="hidden", timeout=10_000)
        browser.close()
