# Changes: endoluminal position drives the blue marker on Topology and Anatomical 3D (#72).
"""The Endoluminal position drives the shared blue marker on all three views."""
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

from test_realism_viewer_playwright import _seed
from test_viewer_v2_playwright import VIEWPORT, _playwright_or_skip

ROOT = Path(__file__).resolve().parent
EDIT_PATH = "/renderer.template.html"
BLUE = "rgb(47, 123, 255)"


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
    root = tmp_path_factory.mktemp("endo_sync_marker")
    _seed(root)
    _web_scene(root)
    home = root / "fake_home"
    home.mkdir()
    env = {**os.environ, "HOME": str(home), "LUNGNAV_PIPELINE_ROOT": str(root)}
    env.pop("PYTHONPATH", None)
    with _server(env, "endo-sync-marker") as base:
        yield base


def _web_scene(root: Path) -> None:
    web = root / "data/extracted/local/local-001/web"
    web.mkdir(parents=True)
    tri = (np.asarray([[0, 0, 0], [1, 0, 0], [0, 1, 0]], np.float32).tobytes()
           + np.asarray([[0, 1, 2]], np.uint32).tobytes())
    (web / "airways.bin").write_bytes(tri)
    (web / "scene.json").write_text(json.dumps({
        "layers": [{"name": "airways", "file": "airways.bin", "color": "#ece6e8",
                    "opacity": 1.0, "vertices": 3, "triangles": 1}],
        "center_mm": [0, 0, 0],
    }))
    # Same nodes as the Case graph, converted RAS -> three.js (x=-RAS.x, y=RAS.z, z=RAS.y).
    nodes = [(0, 0, 0), (0, 0, -20), (16, 0, -20), (-12, 0, -26),
             (16, 12, -30), (16, -8, -28)]
    pos = [v for rx, ry, rz in nodes for v in (-rx, rz, ry)]
    (web / "graph.json").write_text(json.dumps({
        "pos": pos, "radius": [8, 5, 4, 4, 3, 3],
        "edges": [0, 1, 1, 2, 1, 3, 2, 4, 2, 5], "start": 0,
    }))


def _root_world_js() -> str:
    return """() => {
      const d = window.__p3_debug;
      const A = d.airway();
      const o = d.originMm();
      return A.nodes[String(A.root)].p.map((v, i) => v + o[i]);
    }"""


def _distance(a, b) -> float:
    return sum((x - y) ** 2 for x, y in zip(a, b)) ** 0.5


def _colour(page, selector: str) -> str:
    return page.evaluate(
        "(sel) => getComputedStyle(document.querySelector(sel)).backgroundColor",
        selector,
    )


def _topology_node(page):
    return page.evaluate(
        """() => {
          const el = document.getElementById("shared-marker-topology");
          return el && el.dataset.node ? Number(el.dataset.node) : null;
        }"""
    )


def _wait_topology_marker(page):
    page.wait_for_function(
        """() => {
          const el = document.getElementById("shared-marker-topology");
          return !!el && getComputedStyle(el).display === "block" && !!el.dataset.node;
        }""",
        timeout=30_000,
    )


def test_endoluminal_marker_syncs_with_topology_and_3d(server):
    sync_playwright = _playwright_or_skip()
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page(viewport=VIEWPORT)
        page.goto(
            f"{server}{EDIT_PATH}?case=local-001&allow_review=1&debug=1"
            "&variant=endoluminal",
            wait_until="domcontentloaded",
            timeout=120_000,
        )
        page.wait_for_function(
            "() => window.__p3_debug && window.__p3_debug.airway()",
            timeout=120_000,
        )

        root = page.evaluate(_root_world_js())
        opened = page.evaluate("() => window.__p3_debug.sharedPos()")
        assert _distance(opened, root) <= 0.5, opened

        # The default marker sits at the top of the trachea in the 3D view.
        page.evaluate("() => window.__p3_debug.setVariant(0)")
        page.wait_for_function(
            """() => {
              const el = document.getElementById("shared-marker-3d");
              return !!el && getComputedStyle(el).display === "block";
            }""",
            timeout=30_000,
        )
        assert _colour(page, "#shared-marker-3d") == BLUE
        default_highlight = page.evaluate("() => window.__p3_debug.nearestHighlight()")
        assert default_highlight is not None
        assert _distance(default_highlight["world"], root) <= 0.5
        assert default_highlight["dist"] <= 0.5

        # The topology marker starts on the root node.
        page.evaluate("() => window.__p3_debug.setVariant(2)")
        _wait_topology_marker(page)
        assert _topology_node(page) == 0
        assert _colour(page, "#shared-marker-topology") == BLUE

        # Move through the Endoluminal view and confirm the shared state follows.
        page.evaluate("() => window.__p3_debug.setVariant(1)")
        endo = None
        for _ in range(200):
            endo = next((f for f in page.frames if "endoluminal.html" in f.url), None)
            if endo is not None:
                break
            page.wait_for_timeout(100)
        assert endo is not None
        endo.wait_for_function("() => !!window.__endoDebug", timeout=120_000)
        endo.evaluate("() => window.dispatchEvent(new KeyboardEvent('keydown', {key: 'ArrowUp'}))")
        page.wait_for_timeout(200)
        page.wait_for_function(
            """() => {
              const d = window.__p3_debug;
              const p = d.sharedPos(); const A = d.airway(); const o = d.originMm();
              const r = A.nodes[String(A.root)].p.map((v, i) => v + o[i]);
              return !!p && Math.hypot(p[0] - r[0], p[1] - r[1], p[2] - r[2]) >= 12;
            }""",
            timeout=30_000,
        )
        endo.evaluate("() => window.dispatchEvent(new KeyboardEvent('keyup', {key: 'ArrowUp'}))")

        moved = page.evaluate(
            """() => {
              const d = window.__p3_debug;
              return { pos: d.sharedPos(), source: d.sharedPosSource(),
                       nearest: d.nearestHighlight() };
            }"""
        )
        assert moved["source"] == "endoluminal"
        assert moved["nearest"] is not None
        assert _distance(moved["nearest"]["world"], moved["pos"]) <= 8
        assert moved["nearest"]["dist"] <= 8
        assert _distance(moved["pos"], root) >= 12

        # The topology marker follows to the next node and stays blue.
        page.evaluate("() => window.__p3_debug.setVariant(2)")
        _wait_topology_marker(page)
        assert _topology_node(page) == 1
        assert _colour(page, "#shared-marker-topology") == BLUE

        # Switching to the 3D view shows the marker at the same shared position.
        page.evaluate("() => window.__p3_debug.setVariant(0)")
        page.wait_for_function(
            """() => {
              const el = document.getElementById("shared-marker-3d");
              return !!el && getComputedStyle(el).display === "block";
            }""",
            timeout=30_000,
        )
        assert _colour(page, "#shared-marker-3d") == BLUE
        browser.close()


def test_panel_four_defaults_to_split_and_remembers_the_choice(server):
    sync_playwright = _playwright_or_skip()
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page(viewport=VIEWPORT)
        try:
            page.goto(
                f"{server}/?case=local-001&variant=endoluminal&debug=1&allow_review=1",
                wait_until="domcontentloaded",
                timeout=120_000,
            )
            page.wait_for_function(
                "() => window.__p3_debug && window.__p3_debug.airway()",
                timeout=120_000,
            )
            endo = None
            for _ in range(200):
                endo = next((f for f in page.frames if "endoluminal.html" in f.url), None)
                if endo is not None:
                    break
                page.wait_for_timeout(100)
            assert endo is not None
            endo.wait_for_function("() => !!window.__endoDebug", timeout=120_000)
            # Panel 4 has no 2D fallback: the iframe is the only renderer.
            assert page.evaluate(
                "() => getComputedStyle(document.getElementById('c')).visibility") == "hidden"
            assert page.evaluate(
                "() => !document.getElementById('endo3d').hidden") is True
            assert endo.evaluate("() => window.__endoDebug.viewMode()") == "split"

            endo.evaluate(
                "() => window.dispatchEvent(new KeyboardEvent('keydown', {key: 'v'}))")
            endo.wait_for_function(
                "() => window.__endoDebug.viewMode() === 'inside'")

            page.evaluate("() => window.__p3_debug.setVariant(0)")
            page.wait_for_timeout(300)
            page.evaluate("() => window.__p3_debug.setVariant(1)")
            page.wait_for_timeout(300)
            assert endo.evaluate("() => window.__endoDebug.viewMode()") == "inside"
        finally:
            browser.close()


def test_desktop_record_sends_wall_clock_frames(server):
    sync_playwright = _playwright_or_skip()
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        context = browser.new_context(viewport=VIEWPORT)
        page = context.new_page()
        page.add_init_script(
            """
            window.__rec = {count: 0};
            window.bronchoroute = {
              recordStart: async () => ({recordingId: 'r1'}),
              recordFrame: async () => { window.__rec.count += 1; },
              recordStop: async () => {},
              recordCancel: async () => {},
            };
            """)
        try:
            page.goto(
                f"{server}/?case=local-001&variant=endoluminal&debug=1&allow_review=1",
                wait_until="domcontentloaded",
                timeout=120_000,
            )
            page.wait_for_function(
                "() => document.getElementById('endo3d')"
                " && !document.getElementById('endo3d').hidden",
                timeout=120_000,
            )
            endo = None
            for _ in range(150):
                endo = next((f for f in page.frames if "endoluminal.html" in f.url), None)
                if endo is not None:
                    break
                page.wait_for_timeout(100)
            assert endo is not None
            endo.wait_for_function("() => !!window.__endoDebug", timeout=120_000)
            page.locator("#record-btn").click()
            page.wait_for_timeout(1000)
            page.locator("#record-stop").click()
            page.wait_for_timeout(600)
            count = page.evaluate("() => window.__rec.count")
            # About 1.2 s at 30 fps. The old loop sent only as fast as readPixels.
            assert count >= 24, count
        finally:
            browser.close()


def test_panel4_nodes_select_route_and_hide_inside(server):
    """Panel 4 picks a node and routes to it; the inside view hides its dot."""
    sync_playwright = _playwright_or_skip()

    def call(path, method, body=None):
        data = None if body is None else json.dumps(body).encode()
        req = urllib.request.Request(
            f"{server}{path}", data=data,
            headers={"Content-Type": "application/json"}, method=method,
        )
        with urllib.request.urlopen(req, timeout=10) as res:
            raw = res.read()
        return json.loads(raw) if raw else None

    call("/api/clinician", "PUT", {"role": "Radiologist", "initials": "A.B."})
    meta = call("/api/cases/local-001/ct/meta", "GET")
    aff, shape = meta["affine"], meta["shape"]
    ras = [
        sum(aff[a][b] * (shape[b] / 2 if b < 3 else 1) for b in range(4))
        for a in range(3)
    ]
    call("/api/cases/local-001/nodes", "PUT", {
        "ras_mm": ras, "diameter_mm": 10, "label": "Node A",
    })
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page(viewport=VIEWPORT)
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        try:
            page.goto(
                f"{server}/renderer.template.html?case=local-001"
                "&variant=endoluminal&debug=1&allow_review=1",
                wait_until="domcontentloaded",
                timeout=120_000,
            )
            page.wait_for_function(
                "() => { const w = document.getElementById('endo3d');"
                " return w && !w.hidden; }",
                timeout=120_000,
            )
            page.wait_for_timeout(2500)
            frame = next(
                f for f in page.frames if "endoluminal.html" in f.url
            )
            frame.wait_for_function(
                "() => window.__endoDebug && window.__endoDebug.nodeCount",
                timeout=30_000,
            )
            assert frame.evaluate("() => window.__endoDebug.nodeCount()") == 1
            node = page.evaluate("() => window.AIRWAY.node_marks[0]")
            # This is exactly what a click in the outside half posts.
            frame.evaluate(
                """node => parent.postMessage({
                  type: 'endo3d-select-target', kind: 'node',
                  id: node.id, ras_mm: node.ras_mm,
                }, location.origin)""",
                node,
            )
            page.wait_for_function(
                "(id) => { const rt = window.AIRWAY.route_target;"
                " return !!(rt && String(rt.poi_kind || '') === 'node:' + id); }",
                arg=node["id"],
                timeout=30_000,
            )
            for _ in range(3):
                frame.evaluate(
                    "() => dispatchEvent(new KeyboardEvent('keydown', {key: 'v'}))"
                )
                page.wait_for_timeout(300)
                if frame.evaluate("() => window.__endoDebug.viewMode()") == "inside":
                    break
            assert frame.evaluate("() => window.__endoDebug.viewMode()") == "inside"
            assert frame.evaluate(
                "() => window.__endoDebug.nodesVisible()"
            ) is False
            assert not errors, errors
        finally:
            browser.close()
