# Changes: exercise topology rotation through real pointer and keyboard input.
from __future__ import annotations

import os
import json
import socket
import subprocess
import time
import urllib.request
from pathlib import Path

import pytest

from test_viewer_v2_playwright import (
    VIEWPORT, _playwright_or_skip,
)
from test_app_server import _write_case_tree


@pytest.fixture(scope="module")
def tmp_pipeline(tmp_path_factory):
    root = tmp_path_factory.mktemp("topology_pipeline")
    paths = _write_case_tree(root)
    graph = json.loads(paths["graph"].read_text())
    for node_id in range(2, 16):
        parent = node_id // 2
        start = graph["nodes"][parent]["position_mm"]
        end = [start[0] + (18 if node_id % 2 else -18), 0, start[2] - 20]
        graph["nodes"].append({"id": node_id, "position_mm": end, "radius_mm": 3})
        graph["edges"].append({
            "from": parent, "to": node_id, "generation": node_id.bit_length() - 1,
            "radius_mm": 3, "length_mm": 27, "lobe": None,
            "provenance": "ct", "centreline_mm": [start, end],
        })
    paths["graph"].write_text(json.dumps(graph))
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
    proc = subprocess.Popen(
        ["uv", "run", "python", "app_server.py", "--port", str(port)],
        cwd=Path(__file__).resolve().parent,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        for _ in range(200):
            assert proc.poll() is None, "topology server exited before readiness"
            try:
                with urllib.request.urlopen(f"{base}/api/cases", timeout=1):
                    break
            except OSError:
                time.sleep(0.1)
        else:
            pytest.fail("topology server did not start")
        yield base
    finally:
        proc.terminate()
        proc.wait(timeout=10)


def test_topology_rotation(server):
    sync_playwright = _playwright_or_skip()
    shots = Path(os.environ.get("TMPDIR", "/tmp")) / "topo_shots"
    shots.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport=VIEWPORT)
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.on("console", lambda message: errors.append(message.text) if message.type == "error" else None)
        response = page.goto(f"{server}/?case=local-001&variant=topology&debug=1")
        assert "let topoRot = 0" in response.text()
        page.wait_for_timeout(1000)
        assert not errors, errors
        angle = page.locator("#topo-angle")
        page.wait_for_function("() => document.querySelector(\"#topo-angle\") || document.body.innerText.includes(\"Could not load\")", timeout=120_000)
        assert not errors, errors
        angle.wait_for()
        assert angle.inner_text() == "Rotation 0°"

        def shot(degrees):
            path = shots / f"topology_{degrees}.png"
            page.screenshot(path=str(path))
            print(path)

        shot(0)
        page.keyboard.press("]")
        page.keyboard.press("]")
        page.keyboard.press("]")
        assert angle.inner_text() == "Rotation 45°"
        shot(45)
        page.locator("#case-picker").click()
        page.keyboard.press("]")
        assert angle.inner_text() == "Rotation 45°"
        page.keyboard.press("Escape")
        page.keyboard.press("Tab")
        page.mouse.move(900, 450)
        page.keyboard.down("Shift")
        page.mouse.down()
        page.mouse.move(700, 650, steps=12)
        page.mouse.up()
        page.keyboard.up("Shift")
        assert angle.inner_text() != "Rotation 45°"
        before = angle.inner_text()
        pan = page.evaluate("() => window.__p2_debug.topology().pan")
        page.mouse.move(900, 450)
        page.mouse.down()
        page.mouse.move(920, 470, steps=5)
        page.mouse.up()
        assert angle.inner_text() == before
        assert page.evaluate("() => window.__p2_debug.topology().pan") != pan
        page.mouse.wheel(0, 100)
        assert angle.inner_text() == before
        page.locator("#topo-reset").click()
        assert angle.inner_text() == "Rotation 0°"
        page.locator("#topo-spin").click()
        page.wait_for_timeout(1000)
        degrees = float(angle.inner_text().split()[1][:-1])
        assert 20 <= degrees <= 45
        page.keyboard.press("[")
        stopped = angle.inner_text()
        page.wait_for_timeout(250)
        assert angle.inner_text() == stopped
        assert page.locator("#topo-spin").get_attribute("aria-pressed") == "false"
        page.locator("#topo-reset").click()
        # Read geometry only; all state changes use actual input.
        point = page.evaluate("() => window.__p2_debug.topology().points[0]")
        page.mouse.click(point["x"], point["y"])
        picked = page.evaluate("() => SharedPos.get()")
        assert page.evaluate("() => window.__p2_debug.topology().pickedEdge") == point["edge"]
        for _ in range(6):
            page.keyboard.press("]")
        rotated = page.evaluate("() => window.__p2_debug.topology().points[0]")
        page.mouse.click(rotated["x"], rotated["y"])
        assert page.evaluate("() => SharedPos.get()") == picked
        assert page.evaluate("() => window.__p2_debug.topology().pickedEdge") == point["edge"]
        for _ in range(6):
            page.keyboard.press("]")
        assert angle.inner_text() == "Rotation 180°"
        shot(180)
        page.keyboard.press("]")
        assert angle.inner_text() == "Rotation -165°"
        page.locator("#switcher button", has_text="Anatomical").click()
        page.keyboard.press("]")
        page.locator("#switcher button", has_text="Topology").click()
        assert angle.inner_text() == "Rotation 0°"
        browser.close()


def test_template_splice():
    root = Path(__file__).resolve().parents[1] / "prototype"
    template = (root / "renderer.template.html").read_text()
    rendered = (root / "renderer.html").read_text()
    prefix, suffix = template.split("/*__AIRWAY_EMBED__*/")
    assert rendered.startswith(prefix)
    assert rendered.endswith(suffix)
    data = rendered[len(prefix):-len(suffix)]
    assert rendered == template.replace("/*__AIRWAY_EMBED__*/", data)
