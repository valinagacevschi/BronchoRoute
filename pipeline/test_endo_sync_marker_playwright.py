# Changes: endoluminal position drives the blue marker on Topology and Anatomical 3D (#72).
"""The Endoluminal position drives the shared blue marker on all three views."""
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
    home = root / "fake_home"
    home.mkdir()
    env = {**os.environ, "HOME": str(home), "LUNGNAV_PIPELINE_ROOT": str(root)}
    env.pop("PYTHONPATH", None)
    with _server(env, "endo-sync-marker") as base:
        yield base


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
        page.keyboard.down("ArrowUp")
        page.wait_for_function(
            """() => {
              const d = window.__p3_debug;
              const p = d.sharedPos(); const A = d.airway(); const o = d.originMm();
              const r = A.nodes[String(A.root)].p.map((v, i) => v + o[i]);
              return !!p && Math.hypot(p[0] - r[0], p[1] - r[1], p[2] - r[2]) >= 12;
            }""",
            timeout=30_000,
        )
        page.keyboard.up("ArrowUp")

        moved = page.evaluate(
            """() => {
              const d = window.__p3_debug;
              return { pos: d.sharedPos(), source: d.sharedPosSource(),
                       nearest: d.nearestHighlight() };
            }"""
        )
        assert moved["source"] == "endoluminal"
        assert moved["nearest"] is not None
        assert _distance(moved["nearest"]["world"], moved["pos"]) <= 5
        assert moved["nearest"]["dist"] <= 5
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
