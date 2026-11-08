# Changes: anatomical 3D fits the airway and pans by drag (#73).
"""Anatomical 3D: open fitted and pan the model with a plain drag."""
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
    root = tmp_path_factory.mktemp("anatomical_fit_pan")
    _seed(root)
    home = root / "fake_home"
    home.mkdir()
    env = {**os.environ, "HOME": str(home), "LUNGNAV_PIPELINE_ROOT": str(root)}
    env.pop("PYTHONPATH", None)
    with _server(env, "anatomical-fit-pan") as base:
        yield base


def _canvas(page):
    return page.evaluate(
        """() => {
          const el = document.getElementById("c");
          const r = el.getBoundingClientRect();
          return {
            W: el.clientWidth, H: el.clientHeight,
            left: r.left, top: r.top,
          };
        }"""
    )


def _bbox(page):
    return page.evaluate(
        """() => {
          const d = window.__p2_debug;
          const A = window.__p3_debug.airway();
          let minX = Infinity, minY = Infinity;
          let maxX = -Infinity, maxY = -Infinity;
          for (const node of Object.values(A.nodes)) {
            const p = d.project(node.p);
            if (!p) return null;
            minX = Math.min(minX, p.x); maxX = Math.max(maxX, p.x);
            minY = Math.min(minY, p.y); maxY = Math.max(maxY, p.y);
          }
          const el = document.getElementById("c");
          return {
            minX, minY, maxX, maxY,
            W: el.clientWidth, H: el.clientHeight,
          };
        }"""
    )


def _cam(page):
    return page.evaluate("() => window.__p2_debug.cam()")


def _assert_fitted(page, why):
    box = _bbox(page)
    assert box is not None, why
    W, H = box["W"], box["H"]
    assert box["minX"] >= 0.04 * W, (why, box)
    assert box["maxX"] <= 0.96 * W, (why, box)
    assert box["minY"] >= 0.04 * H, (why, box)
    assert box["maxY"] <= 0.96 * H, (why, box)
    cx = (box["minX"] + box["maxX"]) / 2
    cy = (box["minY"] + box["maxY"]) / 2
    assert abs(cx - W / 2) <= 0.03 * W, (why, box)
    assert abs(cy - H / 2) <= 0.03 * H, (why, box)


def test_anatomical_open_fit_and_pan(server):
    sync_playwright = _playwright_or_skip()
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page(viewport=VIEWPORT)
        page.goto(
            f"{server}{EDIT_PATH}?case=local-001&allow_review=1&debug=1"
            "&variant=anatomical",
            wait_until="domcontentloaded",
            timeout=120_000,
        )
        page.wait_for_function(
            "() => window.__p2_debug && window.__p3_debug"
            " && window.__p3_debug.airway()",
            timeout=120_000,
        )
        page.wait_for_function(
            "() => window.__p2_debug.getVariant() === 'anatomical'",
            timeout=30_000,
        )
        page.wait_for_timeout(600)

        # Open: the whole airway is inside the canvas and centred.
        _assert_fitted(page, "after open")
        opened = _cam(page)

        # Plain drag pans by the pointer delta and leaves the orbit angles alone.
        canvas = _canvas(page)
        cx = canvas["left"] + canvas["W"] / 2
        cy = canvas["top"] + canvas["H"] / 2
        before = _cam(page)
        page.mouse.move(cx, cy)
        page.mouse.down()
        page.mouse.move(cx, cy + 80, steps=8)
        page.mouse.up()
        after = _cam(page)
        assert abs(after["panY"] - before["panY"] - 80) <= 2, (before, after)
        assert abs(after["panX"] - before["panX"]) <= 1, (before, after)
        assert abs(after["yaw"] - before["yaw"]) < 1e-9, (before, after)
        assert abs(after["pitch"] - before["pitch"]) < 1e-9, (before, after)

        # Shift-drag orbits and leaves the pan alone.
        before = _cam(page)
        page.mouse.move(cx, cy)
        page.keyboard.down("Shift")
        page.mouse.down()
        page.mouse.move(cx - 60, cy + 40, steps=8)
        page.mouse.up()
        page.keyboard.up("Shift")
        after = _cam(page)
        assert (
            abs(after["yaw"] - before["yaw"]) > 1e-6
            or abs(after["pitch"] - before["pitch"]) > 1e-6
        ), (before, after)
        assert abs(after["panX"] - before["panX"]) < 1e-6, (before, after)
        assert abs(after["panY"] - before["panY"]) < 1e-6, (before, after)

        # A preset key re-centres the model after it has been dragged away.
        page.mouse.move(cx, cy)
        page.mouse.down()
        page.mouse.move(cx + 140, cy + 90, steps=10)
        page.mouse.up()
        displaced = _cam(page)
        assert abs(displaced["panX"] - after["panX"]) > 50, (after, displaced)
        page.keyboard.press("f")
        fitted = _cam(page)
        assert fitted["preset"] == "radiologist", fitted
        assert abs(fitted["panY"] - displaced["panY"]) > 50, (displaced, fitted)
        _assert_fitted(page, "after preset F")

        # Opening values are reported for the record.
        print("anatomical open cam:", opened)
        browser.close()
