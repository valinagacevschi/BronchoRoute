# Changes: two nodule scene entries render two meshes and two legend items.
"""Endoluminal viewer: several nodule entries keyed by id, not name."""

from __future__ import annotations

import json
import os
import socket
import subprocess
import time
import urllib.request
from pathlib import Path

import numpy as np
import pytest
from termcolor import cprint

from test_app_server import _write_case_tree
from test_viewer_v2_playwright import VIEWPORT, _playwright_or_skip


def _triangle(offset) -> bytes:
    vertices = (np.array([[0, 0, 0], [1, 0, 0], [0, 1, 0]], np.float32)
                + np.asarray(offset, np.float32))
    faces = np.array([[0, 1, 2]], np.uint32)
    return vertices.tobytes() + faces.tobytes()


def _seed(root: Path) -> None:
    paths = _write_case_tree(root)
    web = paths["local"] / "web"
    web.mkdir()
    layers = [
        {"name": "airways", "file": "airways.bin", "color": "#ece6e8",
         "opacity": 1.0, "vertices": 3, "triangles": 1},
        # Historical merged entry. The viewer must render it only when no
        # per-nodule ids exist, or every nodule would be drawn twice.
        {"name": "nodule", "file": "nodule.bin", "color": "#f2b632",
         "opacity": 1.0, "vertices": 3, "triangles": 1},
        {"name": "nodule", "id": 1, "file": "nodule-1.bin", "color": "#f2b632",
         "opacity": 1.0, "vertices": 3, "triangles": 1},
        {"name": "nodule", "id": 2, "file": "nodule-2.bin", "color": "#f2b632",
         "opacity": 1.0, "vertices": 3, "triangles": 1},
    ]
    for layer, offset in zip(layers, [(0, 0, 0), (0, 0, -20), (0, 0, -40), (10, 0, -40)]):
        (web / layer["file"]).write_bytes(_triangle(offset))
    (web / "scene.json").write_text(
        json.dumps({"layers": layers, "center_mm": [0, 0, 0]}))
    graph = {
        "pos": [0, 0, 0, 0, 0, -20, 0, 0, -40],
        "radius": [8, 6, 4],
        "edges": [0, 1, 1, 2],
        "start": 0,
    }
    (web / "graph.json").write_text(json.dumps(graph))


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    root = tmp_path_factory.mktemp("endo_nodules_72")
    _seed(root)
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    base = f"http://127.0.0.1:{port}"
    home = root / "fake_home"
    home.mkdir()
    env = {**os.environ, "HOME": str(home), "LUNGNAV_PIPELINE_ROOT": str(root)}
    env.pop("PYTHONPATH", None)
    log_path = root / "server.err"
    log = log_path.open("w")
    cprint(f"endoluminal nodule server on {port}", "cyan")
    proc = subprocess.Popen(
        ["uv", "run", "python", "app_server.py", "--port", str(port)],
        cwd=Path(__file__).resolve().parent,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=log,
    )
    try:
        for _ in range(200):
            if proc.poll() is not None:
                log.flush()
                pytest.fail(f"nodule server exited\n{log_path.read_text()[-2000:]}")
            try:
                with urllib.request.urlopen(f"{base}/api/cases", timeout=1):
                    break
            except OSError:
                time.sleep(0.1)
        else:
            pytest.fail("nodule server did not start")
        yield base
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)
        log.close()


def test_two_nodules_render_two_meshes_and_two_legend_items(server):
    sync_playwright = _playwright_or_skip()
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page(viewport=VIEWPORT)
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto(
            f"{server}/endoluminal.html?mode=a1&case=/case-web/local-001",
            wait_until="domcontentloaded",
        )
        page.wait_for_function(
            """() => window.__endoDebug
              && window.__endoDebug.noduleMeshes.length === 2""",
            timeout=120_000,
        )
        assert not errors, errors

        keys = page.evaluate("() => Object.keys(window.__endoDebug.meshes)")
        assert "nodule-1" in keys and "nodule-2" in keys
        # The merged compatibility entry is not drawn when ids exist.
        assert "nodule" not in keys
        ids = page.evaluate(
            "() => window.__endoDebug.noduleMeshes.map(m => m.userData.noduleId)")
        assert ids == [1, 2]

        legend = page.locator("#legend").inner_text()
        assert "target nodule 1" in legend
        assert "target nodule 2" in legend
        assert legend.count("target nodule") == 2

        # The initial selection comes from the first scene entry.
        status = page.locator("#status").inner_text()
        assert "route to nodule 1" in status, status
        browser.close()
