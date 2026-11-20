"""Demo viewer affordances: aids toggle, outside reveal, and click-to-retarget.

Ported from examples/airway_nav: minimap, green distance rings, end-of-route
reveal, lesion click opening the RouteTarget panel, and double-click on a branch
setting the RouteTarget through the existing server call.
"""

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

import repo_root

repo_root.ensure()

from test_app_server import _stamp_pipeline_version  # noqa: E402
from test_flythrough_playwright import _seed  # noqa: E402
from test_viewer_v2_playwright import VIEWPORT, _playwright_or_skip  # noqa: E402

ROOT = Path(__file__).resolve().parent


def _seed_with_lesion(tmp: Path) -> Path:
    case_path = _seed(tmp)
    document = json.loads(case_path.read_text())
    # Synthetic graph: node 4 sits at (16, 12, -30) in absolute RAS.
    document["lesion"] = {
        "ras_mm": [16.0, 12.0, -30.0],
        "diameter_mm": 8.0,
        "confirmed_by": "test",
    }
    case_path.write_text(json.dumps(document, indent=2) + "\n")
    _stamp_pipeline_version(case_path)
    return case_path


@pytest.fixture
def server(tmp_path: Path):
    case_path = _seed_with_lesion(tmp_path)
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
    cprint(f"viewer-affordances server on {port}", "cyan")
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
                pytest.fail(f"viewer-affordances server exited\n{log_path.read_text()[-2000:]}")
            try:
                with urllib.request.urlopen(f"{base}/api/cases", timeout=1):
                    break
            except OSError:
                time.sleep(0.1)
        else:
            pytest.fail("viewer-affordances server did not start")
        yield base, case_path
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)
        log.close()


def _open(page, server: str, variant: str):
    page.goto(
        f"{server}/renderer.template.html?case=local-001&allow_review=1&debug=1&variant={variant}",
        wait_until="domcontentloaded",
    )
    page.wait_for_function(
        """() => window.__p2_debug
          && document.getElementById('c').clientWidth > 100
          && window.AIRWAY""",
    )
    page.wait_for_function("() => document.fonts.status === 'loaded'")
    index = {"anatomical": 0, "endoluminal": 1}[variant]
    page.evaluate(f"() => window.__p2_debug.setVariant({index})")
    page.wait_for_function(
        "() => document.getElementById('c').clientWidth > 100",
    )


def test_aids_toggle_minimap_and_variant_key(server):
    base, _case = server
    sync_playwright = _playwright_or_skip()
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        context = browser.new_context(viewport=VIEWPORT)
        page = context.new_page()
        try:
            _open(page, base, "endoluminal")
            page.wait_for_function("() => window.__p2_debug.aids().minimap > 0")
            state = page.evaluate("() => window.__p2_debug.aids()")
            assert state["on"] is True
            assert state["minimap"] > 0
            assert state["rings"] > 0

            page.keyboard.press("m")
            page.wait_for_function("() => !window.__p2_debug.aids().on")
            assert page.evaluate("() => window.__p2_debug.aids().toast") == "Aids off"

            page.keyboard.press("a")
            page.wait_for_function("() => window.__p2_debug.getVariant() === 'anatomical'")
            page.keyboard.press("a")
            page.wait_for_function("() => window.__p2_debug.getVariant() === 'endoluminal'")
        finally:
            context.close()
            browser.close()


def test_reveal_at_route_end_and_enter_dismisses(server):
    base, _case = server
    sync_playwright = _playwright_or_skip()
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        context = browser.new_context(viewport=VIEWPORT)
        page = context.new_page()
        try:
            _open(page, base, "endoluminal")
            end = page.evaluate("() => window.AIRWAY.fly.s.at(-1)")
            page.evaluate(
                f"() => window.__p2_debug.setFly({{ s: {end - 0.1}, playing: false }})"
            )
            page.wait_for_function("() => window.__p2_debug.aids().reveal > 0")
            state = page.evaluate("() => window.__p2_debug.aids()")
            assert state["atEnd"] is True
            assert state["revealDismissed"] is False

            page.keyboard.press("Enter")
            page.wait_for_function("() => window.__p2_debug.aids().revealDismissed === true")
            assert page.evaluate("() => window.__p2_debug.aids().toast") == "Back inside"

            page.evaluate(
                f"() => window.__p2_debug.setFly({{ s: {max(0.0, end - 6)}, playing: false }})"
            )
            page.wait_for_function(
                "() => window.__p2_debug.aids().revealDismissed === false"
            )
        finally:
            context.close()
            browser.close()


def test_lesion_click_opens_panel_and_double_click_sets_target(server):
    base, _case = server
    sync_playwright = _playwright_or_skip()
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        context = browser.new_context(viewport=VIEWPORT)
        page = context.new_page()
        page.on("dialog", lambda dialog: dialog.accept())
        try:
            _open(page, base, "anatomical")
            page.wait_for_function(
                "() => window.__p2_debug.lesionViews().anatomical != null"
            )
            lesion = page.evaluate("() => window.__p2_debug.lesionViews().anatomical")
            rect = page.evaluate(
                "() => { const r = document.getElementById('c').getBoundingClientRect();"
                " return { left: r.left, top: r.top }; }"
            )
            page.mouse.click(rect["left"] + lesion["x"], rect["top"] + lesion["y"])
            page.wait_for_function(
                "() => !document.getElementById('route-target-panel').hidden"
            )

            hit = page.evaluate("() => window.__p2_debug.edgeHitPoint()")
            assert hit is not None
            page.mouse.dblclick(rect["left"] + hit["x"], rect["top"] + hit["y"])
            page.wait_for_function(
                "() => window.AIRWAY.route_target"
                " && Array.isArray(window.AIRWAY.route_target.ras_mm)"
            )
            target = page.evaluate("() => window.AIRWAY.route_target")
            assert len(target["ras_mm"]) == 3
            assert target.get("stale") is not True
        finally:
            context.close()
            browser.close()
