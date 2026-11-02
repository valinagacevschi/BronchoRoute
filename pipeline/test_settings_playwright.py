# Changes: Settings persist through real clicks in every view on a tmp state root.
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

from test_app_server import _write_case_tree
from test_viewer_v2_playwright import _playwright_or_skip


@pytest.fixture(scope="module")
def tmp_pipeline(tmp_path_factory):
    root = tmp_path_factory.mktemp("settings_pipeline")
    _write_case_tree(root)
    return root


@pytest.fixture(scope="module")
def server(tmp_pipeline):
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    base = f"http://127.0.0.1:{port}"
    home = tmp_pipeline / "fake_home"
    home.mkdir()
    env = {**os.environ, "HOME": str(home), "LUNGNAV_PIPELINE_ROOT": str(tmp_pipeline)}
    env.pop("PYTHONPATH", None)
    cprint(f"settings server on {port}", "cyan")
    proc = subprocess.Popen(
        ["uv", "run", "python", "app_server.py", "--port", str(port)],
        cwd=Path(__file__).resolve().parent,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        for _ in range(200):
            assert proc.poll() is None, "settings server exited before readiness"
            try:
                with urllib.request.urlopen(f"{base}/api/cases", timeout=1):
                    break
            except OSError:
                time.sleep(0.1)
        else:
            pytest.fail("settings server did not start")
        yield base
    finally:
        proc.terminate()
        proc.wait(timeout=10)


def test_settings_dialog(server):
    sync_playwright = _playwright_or_skip()
    shots = Path(os.environ.get("TMPDIR", "/tmp")) / "bronchoroute-32"
    shots.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page(viewport={"width": 1440, "height": 868})
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.on("console", lambda message: errors.append(message.text) if message.type == "error" else None)
        writes = []
        page.on("request", lambda request: writes.append(request.url) if request.method == "PUT" else None)
        page.goto(f"{server}/?case=local-001&variant=topology&debug=1")
        page.locator("#switcher button").first.wait_for(timeout=120_000)
        buttons = page.locator("#switcher button")
        assert buttons.count() == 4
        for index in range(4):
            buttons.nth(index).click()
            page.locator("#settings-open").click()
            page.locator("#settings-save").wait_for()
            page.wait_for_function("!document.getElementById('settings-save').disabled")
            page.locator("#settings-initials").fill("A.B.")
            page.locator("#settings-role").fill("Research")
            page.locator("#settings-updates").set_checked(index % 2 == 1)
            page.locator("#settings-save").click()
            page.locator("#settings-dialog").wait_for(state="hidden")
            page.locator("#settings-open").click()
            page.wait_for_function("!document.getElementById('settings-save').disabled")
            assert page.locator("#settings-initials").input_value() == "A.B."
            assert page.locator("#settings-role").input_value() == "Research"
            assert page.locator("#settings-updates").is_checked() == (index % 2 == 1)
            page.locator("#settings-cancel").click()
        page.reload()
        page.locator("#settings-open").click()
        page.wait_for_function("!document.getElementById('settings-save').disabled")
        assert page.locator("#settings-initials").input_value() == "A.B."
        assert page.locator("#settings-updates").is_checked()
        page.screenshot(path=str(shots / "settings.png"))
        before = len(writes)
        page.locator("#settings-initials").fill("A1")
        page.locator("#settings-save").click()
        assert page.locator("#settings-error").inner_text() == "Initials must be 1–4 letters with optional dots."
        assert len(writes) == before
        with urllib.request.urlopen(f"{server}/api/settings") as response:
            assert json.load(response)["initials"] == "A.B."
        page.keyboard.press("Escape")
        page.locator("#settings-dialog").wait_for(state="hidden")
        assert not errors, errors
        browser.close()
