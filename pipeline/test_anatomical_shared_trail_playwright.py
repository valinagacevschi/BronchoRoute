# Changes: Anatomical 3D draws the same trail and destination nodule as panel 4.
"""Anatomical 3D and the Endoluminal outside view share one trail and target.

Both read the case-web scene graph, so the node path and the destination
nodule agree, and each panel moves the other along that trail.
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import time
import urllib.request
from contextlib import contextmanager
from pathlib import Path

import numpy as np
import pytest
from termcolor import cprint

from test_app_server import _write_case_tree
from test_viewer_v2_playwright import VIEWPORT, _playwright_or_skip

ROOT = Path(__file__).resolve().parent


def _triangle(offset) -> bytes:
    verts = (np.asarray([[0, 0, 0], [1, 0, 0], [0, 1, 0]], np.float32)
             + np.asarray(offset, np.float32))
    return verts.tobytes() + np.asarray([[0, 1, 2]], np.uint32).tobytes()


def _scene_assets(root: Path) -> None:
    """Case graph is a trachea; the 3D scene graph routes to one nodule."""
    web = root / "data/extracted/local/local-001/web"
    web.mkdir(parents=True)
    # Same nodes in the scene graph as the Endoluminal marker test. The scene
    # centre is non-zero, so the RAS<->scene inverse must use centre[1]/[2]
    # the right way round. Offsets keep the trail inside the fitted view.
    c1, c2 = 100, 200
    nodes = [(0, 0, 0), (0, 0, -20), (16, 0, -20), (-12, 0, -26),
             (16, 12, -30), (16, -8, -28)]
    pos = [v for rx, ry, rz in nodes for v in (-rx, rz - c1, ry - c2)]
    (web / "graph.json").write_text(json.dumps({
        "pos": pos, "radius": [8, 5, 4, 4, 3, 3],
        "edges": [0, 1, 1, 2, 1, 3, 2, 4, 2, 5], "start": 0,
    }))
    (web / "airways.bin").write_bytes(_triangle((0, 0, 0)))
    # Nodule 1 sits on scene node 4.
    nodule = (-16, -30 - c1, 12 - c2)
    (web / "nodule-1.bin").write_bytes(_triangle(nodule))
    (web / "scene.json").write_text(json.dumps({
        "layers": [
            {"name": "airways", "file": "airways.bin", "color": "#ece6e8",
             "opacity": 1.0, "vertices": 3, "triangles": 1},
            {"name": "nodule", "id": 1, "file": "nodule-1.bin",
             "color": "#f2b632", "opacity": 1.0, "vertices": 3,
             "triangles": 1, "center_mm": list(nodule)},
        ],
        "center_mm": [0, c1, c2],
    }))


@contextmanager
def _server(env):
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    base = f"http://127.0.0.1:{port}"
    proc = subprocess.Popen(
        ["uv", "run", "python", "app_server.py", "--port", str(port)],
        cwd=ROOT, env=env,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        for _ in range(200):
            if proc.poll() is not None:
                pytest.fail("shared-trail server exited")
            try:
                with urllib.request.urlopen(f"{base}/healthz", timeout=1):
                    break
            except OSError:
                time.sleep(0.1)
        else:
            pytest.fail("shared-trail server did not start")
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
    root = tmp_path_factory.mktemp("anatomical_shared_trail")
    _write_case_tree(root)
    _scene_assets(root)
    home = root / "fake_home"
    home.mkdir()
    env = {**os.environ, "HOME": str(home), "LUNGNAV_PIPELINE_ROOT": str(root)}
    env.pop("PYTHONPATH", None)
    cprint("shared-trail server", "cyan")
    with _server(env) as base:
        yield base


def _endo_frame(page):
    for _ in range(200):
        frame = next((f for f in page.frames if "endoluminal.html" in f.url), None)
        if frame is not None:
            frame.wait_for_function("() => !!window.__endoDebug", timeout=120_000)
            return frame
        page.wait_for_timeout(100)
    raise AssertionError("endoluminal frame did not load")


def test_anatomical_and_outside_share_the_trail_and_destination(server):
    sync_playwright = _playwright_or_skip()
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page(viewport=VIEWPORT)
        try:
            page.goto(
                f"{server}/renderer.template.html?case=local-001&allow_review=1"
                "&debug=1&variant=anatomical",
                wait_until="domcontentloaded",
                timeout=120_000,
            )
            page.wait_for_function(
                "() => window.__p2_debug && window.__p2_debug.sceneTrail()",
                timeout=120_000,
            )
            panel_two = page.evaluate("() => window.__p2_debug.sceneTrail()")
            assert panel_two["nodule"]["id"] == 1, panel_two
            assert panel_two["path"] == [0, 1, 2, 4], panel_two
            # The panel draws the destination nodule and its label.
            page.wait_for_function(
                "() => { const t = window.__p2_debug.sceneTrail();"
                " return !!t && !!t.lastNodule; }",
                timeout=30_000,
            )
            drawn = page.evaluate("() => window.__p2_debug.sceneTrail().lastNodule")
            assert drawn["label"] == "target nodule 1", drawn

            # Panel 4 computes its route from the same scene graph.
            page.evaluate("() => window.__p2_debug.setVariant(1)")
            endo = _endo_frame(page)
            endo.wait_for_function(
                "() => !!window.__endoDebug.trail()", timeout=120_000)
            outside = endo.evaluate("() => window.__endoDebug.trail()")
            assert outside["nodule"] == 1, outside
            assert outside["path"] == panel_two["path"], (outside["path"], panel_two["path"])
            # Same end point and gap in both frames of reference.
            assert outside["gap"] == pytest.approx(panel_two["gap"], abs=0.5)

            # Moving in Anatomical moves the Endoluminal scope along the trail.
            page.evaluate("() => window.__p2_debug.setVariant(0)")
            page.wait_for_timeout(200)
            page.evaluate(
                "() => window.dispatchEvent(new KeyboardEvent('keydown', {key: 'ArrowUp'}))")
            page.wait_for_timeout(400)
            page.evaluate(
                "() => window.dispatchEvent(new KeyboardEvent('keyup', {key: 'ArrowUp'}))")
            moved = page.evaluate("() => window.__p3_debug.sharedPos()")
            page.evaluate("() => window.__p2_debug.setVariant(1)")
            endo.wait_for_function(
                "() => window.__endoDebug.progress() > 0", timeout=15_000)
            # The Endoluminal marker lands on the point Anatomical requested.
            marker = endo.evaluate("() => window.__endoDebug.markerPos()")
            expected = [-moved[0], moved[2] - 100, moved[1] - 200]
            error = sum((a - b) ** 2 for a, b in zip(marker, expected)) ** 0.5
            assert error < 2.0, (marker, expected, error)

            # Moving in Endoluminal moves the Anatomical shared position.
            endo.evaluate(
                "() => window.dispatchEvent(new KeyboardEvent('keydown', {key: 'ArrowUp'}))")
            page.wait_for_timeout(400)
            endo.evaluate(
                "() => window.dispatchEvent(new KeyboardEvent('keyup', {key: 'ArrowUp'}))")
            page.evaluate("() => window.__p2_debug.setVariant(0)")
            page.wait_for_function(
                "() => window.__p3_debug"
                " && window.__p3_debug.sharedPosSource() === 'endoluminal'",
                timeout=15_000)
        finally:
            browser.close()


def test_endoluminal_opens_at_the_anatomical_position(server):
    """Moving on Anatomical before panel 4 exists still lands on the same point."""
    sync_playwright = _playwright_or_skip()
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page(viewport=VIEWPORT)
        try:
            page.goto(
                f"{server}/renderer.template.html?case=local-001&allow_review=1"
                "&debug=1&variant=anatomical",
                wait_until="domcontentloaded",
                timeout=120_000,
            )
            page.wait_for_function(
                "() => window.__p2_debug && window.__p2_debug.sceneTrail()",
                timeout=120_000,
            )
            start = page.evaluate("() => window.__p3_debug.sharedPos()")
            page.evaluate(
                "() => window.dispatchEvent(new KeyboardEvent('keydown', {key: 'ArrowUp'}))")
            page.wait_for_timeout(300)
            page.evaluate(
                "() => window.dispatchEvent(new KeyboardEvent('keyup', {key: 'ArrowUp'}))")
            moved = page.evaluate("() => window.__p3_debug.sharedPos()")
            assert moved != start, (start, moved)

            # Panel 4 loads after the move; it must open on that point, not 0.
            page.evaluate("() => window.__p2_debug.setVariant(1)")
            endo = _endo_frame(page)
            endo.wait_for_function(
                "() => window.__endoDebug.progress() > 0", timeout=30_000)
        finally:
            browser.close()


def test_sidebar_camera_section_and_a_shortcut_are_gone(server):
    sync_playwright = _playwright_or_skip()
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page(viewport=VIEWPORT)
        try:
            page.goto(
                f"{server}/renderer.template.html?case=local-001&allow_review=1"
                "&debug=1&variant=anatomical",
                wait_until="domcontentloaded",
                timeout=120_000,
            )
            page.wait_for_function(
                "() => window.__p2_debug && window.AIRWAY",
                timeout=120_000,
            )
            assert page.evaluate("() => document.getElementById('g-cam')") is None
            hint = page.locator("#hint").inner_text()
            assert "click Lesion" not in hint
            assert "Endoluminal" not in hint
            # A no longer switches panels; the number keys still do.
            page.keyboard.press("a")
            page.wait_for_timeout(200)
            assert page.evaluate("() => window.__p2_debug.getVariant()") == "anatomical"
            page.keyboard.press("4")
            page.wait_for_function(
                "() => window.__p2_debug.getVariant() === 'endoluminal'")
        finally:
            browser.close()
