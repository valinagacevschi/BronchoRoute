# Changes: About dialog opens from a click on a free port and a tmp state root.
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
    root = tmp_path_factory.mktemp("about_pipeline")
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
    cprint(f"about server on {port}", "cyan")
    proc = subprocess.Popen(
        ["uv", "run", "python", "app_server.py", "--port", str(port)],
        cwd=Path(__file__).resolve().parent,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        for _ in range(200):
            assert proc.poll() is None, "about server exited before readiness"
            try:
                with urllib.request.urlopen(f"{base}/api/cases", timeout=1):
                    break
            except OSError:
                time.sleep(0.1)
        else:
            pytest.fail("about server did not start")
        yield base
    finally:
        proc.terminate()
        proc.wait(timeout=10)


def test_about_dialog(server):
    sync_playwright = _playwright_or_skip()
    shots = Path(os.environ.get("TMPDIR", "/tmp")) / "bronchoroute-46"
    shots.mkdir(parents=True, exist_ok=True)
    shot = shots / "about.png"
    with urllib.request.urlopen(f"{server}/api/about", timeout=5) as res:
        about = json.load(res)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page(viewport={"width": 1440, "height": 868})
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.on(
            "console",
            lambda message: errors.append(message.text) if message.type == "error" else None,
        )
        page.goto(f"{server}/?case=local-001&variant=topology&debug=1")
        page.wait_for_function(
            "() => document.querySelector('#topo-angle') || document.body.innerText.includes('Could not load')",
            timeout=120_000,
        )
        assert "Could not load" not in page.locator("body").inner_text()
        assert not errors, errors
        buttons = page.locator("#switcher button")
        assert buttons.count() == 4
        for index in range(buttons.count()):
            buttons.nth(index).click()
            assert page.locator("#about-open").evaluate("""link => {
                const rect = link.getBoundingClientRect();
                return rect.width > 0 && rect.height > 0 &&
                    rect.left >= 0 && rect.top >= 0 &&
                    rect.right <= innerWidth && rect.bottom <= innerHeight &&
                    document.elementFromPoint(
                        rect.left + rect.width / 2, rect.top + rect.height / 2
                    ) === link;
            }"""), f"About is unreachable in view {index}"
            page.locator("#about-open").click()
            page.locator("#about-dialog").wait_for(state="visible")
            page.locator("#about-close").click()
            page.locator("#about-dialog").wait_for(state="hidden")
        assert page.locator(".brand span").text_content() == "BronchoRoute"
        assert page.title() == "BronchoRoute"
        page.locator("#about-open").click()
        dialog = page.locator("#about-dialog")
        dialog.wait_for(state="visible")
        text = dialog.inner_text()
        assert "Research and education tool. Not a medical device. Not for diagnosis or treatment decisions." in text
        # inner_text follows CSS uppercase; the heading source is "Sponsors".
        assert page.locator("#about-sponsors-section h3").text_content() == "Sponsors"
        assert page.locator("#about-sponsors li").count() == 0
        assert page.locator("#about-name").inner_text() == about["name"]
        assert page.locator("#about-version").inner_text() == about["version"]
        licence = page.locator("#about-licence")
        assert licence.inner_text() == about["licence"]
        assert licence.get_attribute("href") == "/LICENSE"
        assert page.locator("#about-notices").get_attribute("href") == "/THIRD_PARTY_NOTICES.md"
        page.screenshot(path=str(shot))
        print(shot)
        cprint(f"about screenshot {shot}", "cyan")
        page.locator("#about-close").click()
        dialog.wait_for(state="hidden")
        assert not errors, errors
        browser.close()
