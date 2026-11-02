# Changes: assert #archive-open never intersects Limitations or other fixed chrome.
"""Open Archive button stays clear of fixed viewer chrome across viewports and views."""

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

from test_app_server import _stamp_pipeline_version, _write_case_tree
from test_viewer_v2_playwright import _playwright_or_skip

VIEWPORTS = (
    {"width": 1280, "height": 800},
    {"width": 1440, "height": 900},
    {"width": 1920, "height": 1080},
)
VIEWS = (
    ("CT panes", "ct-panes"),
    ("Anatomical", "anatomical"),
    ("Topology", "topology"),
    ("Endoluminal", "endoluminal"),
)
SHOT_VIEWS = {"anatomical", "endoluminal"}
CHROME = (
    ("#limitations-panel", True),
    ("#review-warnings", False),
    ("#save-image", False),
    ("#switcher", True),
    (".rail", True),
)


def _write_case(root: Path) -> None:
    paths = _write_case_tree(root, "local-001")
    graph = json.loads(paths["graph"].read_text())
    graph["review"] = [
        {"kind": "lobe_without_airways", "lobe": "left upper lobe"},
    ]
    paths["graph"].write_text(json.dumps(graph))
    _stamp_pipeline_version(paths["case"])


@pytest.fixture(scope="module")
def tmp_pipeline(tmp_path_factory):
    root = tmp_path_factory.mktemp("archive_open_layout")
    _write_case(root)
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
    env.pop("BRONCHOROUTE_LEGACY_REVIEW", None)
    proc = subprocess.Popen(
        ["uv", "run", "python", "app_server.py", "--port", str(port)],
        cwd=Path(__file__).resolve().parent,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        for _ in range(200):
            assert proc.poll() is None, "archive-open layout server exited before readiness"
            try:
                with urllib.request.urlopen(f"{base}/api/cases", timeout=1):
                    break
            except OSError:
                time.sleep(0.1)
        else:
            pytest.fail("archive-open layout server did not start")
        cprint(f"archive-open layout server ready on port {port}", "cyan")
        yield base
    finally:
        proc.terminate()
        proc.wait(timeout=10)


def _shots() -> Path:
    path = Path(os.environ.get("TMPDIR", "/tmp")) / "bronchoroute-53"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _box(locator):
    box = locator.bounding_box()
    assert box is not None, locator
    return box


def _overlaps(a: dict, b: dict) -> bool:
    separated = a["x"] + a["width"] <= b["x"]
    separated = separated or b["x"] + b["width"] <= a["x"]
    separated = separated or a["y"] + a["height"] <= b["y"]
    separated = separated or b["y"] + b["height"] <= a["y"]
    return not separated


def _inside_viewport(box: dict, viewport: dict) -> bool:
    return (
        box["x"] >= -0.5
        and box["y"] >= -0.5
        and box["x"] + box["width"] <= viewport["width"] + 0.5
        and box["y"] + box["height"] <= viewport["height"] + 0.5
    )


def _open(page, base: str) -> None:
    page.goto(
        f"{base}/?case=local-001&debug=1",
        wait_until="domcontentloaded",
        timeout=120_000,
    )
    page.wait_for_function(
        """() => {
          const source = document.getElementById("r-source");
          const open = document.getElementById("archive-open");
          return !!(
            window.AIRWAY
            && window.__p2_debug
            && source
            && source.textContent !== "unknown"
            && open
            && !open.hidden
            && open.getClientRects().length
            && document.querySelectorAll("#switcher button").length >= 4
          );
        }""",
        timeout=120_000,
    )


def _show(page, label: str, key: str) -> None:
    page.locator("#switcher button", has_text=label).click()
    page.wait_for_function(
        "(expected) => window.__p2_debug && window.__p2_debug.getVariant() === expected",
        arg=key,
        timeout=30_000,
    )
    if key == "ct-panes":
        page.wait_for_selector(".ct-toolbar", timeout=60_000)
    page.wait_for_timeout(50)


def _assert_clear(page, viewport: dict, view: str) -> None:
    open_box = _box(page.locator("#archive-open"))
    assert _inside_viewport(open_box, viewport), (view, open_box, viewport)
    for selector, required in CHROME:
        loc = page.locator(selector)
        if not required and not loc.is_visible():
            continue
        other = _box(loc)
        assert not _overlaps(open_box, other), (view, selector, open_box, other)


def test_archive_open_clears_fixed_chrome(server):
    sync_playwright = _playwright_or_skip()
    cprint("archive-open layout: viewports × views, no chrome overlap", "cyan")
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        errors = []
        for viewport in VIEWPORTS:
            page = browser.new_page(viewport=viewport)
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.on(
                "console",
                lambda message: errors.append(message.text)
                if message.type == "error"
                else None,
            )
            _open(page, server)
            assert page.locator("#archive-open").is_visible()
            keys = page.evaluate("() => window.__p2_debug.switcherKeys()")
            views = [(label, key) for label, key in VIEWS if key in keys]
            if "walkthrough" in keys:
                views.append(("Walk-through", "walkthrough"))
            for label, key in views:
                _show(page, label, key)
                _assert_clear(page, viewport, key)
                assert not errors, (viewport, key, errors)
                if key in SHOT_VIEWS:
                    path = (
                        _shots()
                        / f"archive-open-{viewport['width']}x{viewport['height']}-{key}.png"
                    )
                    page.screenshot(path=str(path))
                    cprint(str(path), "green")
            page.close()
        browser.close()
