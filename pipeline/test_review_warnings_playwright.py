# Changes: stamp synthetic Cases with the current pipeline version.
# Changes: review-warning panel lists plain-language flags in every view.
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
from test_viewer_v2_playwright import VIEWPORT, _playwright_or_skip

AIRWAY_LINE = "Left upper lobe: no airways found in this lobe"
SMALL_LINE = "Right middle lobe: smaller than expected (42.5 ml, median 180 ml)"
BANNED = ("Clinical review pending", "not for training use")
VIEWS = (
    ("CT panes", "ct-panes"),
    ("Anatomical", "anatomical"),
    ("Topology", "topology"),
    ("Endoluminal", "endoluminal"),
)


def _extend_graph(graph: dict) -> None:
    for node_id in range(2, 16):
        parent = node_id // 2
        start = graph["nodes"][parent]["position_mm"]
        end = [start[0] + (18 if node_id % 2 else -18), 0, start[2] - 20]
        graph["nodes"].append({"id": node_id, "position_mm": end, "radius_mm": 3})
        graph["edges"].append({
            "from": parent,
            "to": node_id,
            "generation": node_id.bit_length() - 1,
            "radius_mm": 3,
            "length_mm": 27,
            "lobe": None,
            "provenance": "ct",
            "centreline_mm": [start, end],
        })


def _write_reviewed_case(root: Path, case_id: str, review: list[dict]) -> None:
    paths = _write_case_tree(root, case_id)
    graph = json.loads(paths["graph"].read_text())
    _extend_graph(graph)
    graph["review"] = review
    paths["graph"].write_text(json.dumps(graph))
    _stamp_pipeline_version(paths["case"])


@pytest.fixture(scope="module")
def tmp_pipeline(tmp_path_factory):
    root = tmp_path_factory.mktemp("review_warnings_pipeline")
    _write_reviewed_case(root, "local-001", [
        {"kind": "lobe_without_airways", "lobe": "left upper lobe"},
        {
            "kind": "small_lobe",
            "lobe": "right middle lobe",
            "ml": 42.5,
            "median_ml": 180,
        },
    ])
    _write_reviewed_case(root, "local-002", [])
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
            assert proc.poll() is None, "review-warnings server exited before readiness"
            try:
                with urllib.request.urlopen(f"{base}/api/cases", timeout=1):
                    break
            except OSError:
                time.sleep(0.1)
        else:
            pytest.fail("review-warnings server did not start")
        cprint(f"review-warnings server ready on port {port}", "cyan")
        yield base
    finally:
        proc.terminate()
        proc.wait(timeout=10)


def _shots() -> Path:
    shots = Path(os.environ.get("TMPDIR", "/tmp")) / "bronchoroute-36"
    shots.mkdir(parents=True, exist_ok=True)
    return shots


def _shot(page, name: str) -> None:
    path = _shots() / name
    page.screenshot(path=str(path))
    cprint(str(path), "green")


def _box(locator):
    box = locator.bounding_box()
    assert box is not None
    return box


def _overlaps(a: dict, b: dict) -> bool:
    separated = a["x"] + a["width"] <= b["x"]
    separated = separated or b["x"] + b["width"] <= a["x"]
    separated = separated or a["y"] + a["height"] <= b["y"]
    separated = separated or b["y"] + b["height"] <= a["y"]
    return not separated


def _assert_clear(page) -> None:
    panel = _box(page.locator("#review-warnings"))
    rail = _box(page.locator(".rail"))
    assert panel["x"] >= rail["x"] - 1
    assert panel["y"] >= rail["y"] - 1
    assert panel["x"] + panel["width"] <= rail["x"] + rail["width"] + 1
    assert panel["y"] + panel["height"] <= rail["y"] + rail["height"] + 1
    switcher = _box(page.locator("#switcher"))
    limitations = _box(page.locator("#limitations-panel"))
    assert not _overlaps(panel, switcher), (panel, switcher)
    assert not _overlaps(panel, limitations), (panel, limitations)


def _assert_lines(page) -> None:
    panel = page.locator("#review-warnings")
    assert panel.is_visible()
    texts = page.locator("#review-warnings-list li").all_text_contents()
    assert texts == [AIRWAY_LINE, SMALL_LINE]
    assert page.locator("#review-warnings-toggle").text_content() == "Review warnings"
    _assert_clear(page)


def _assert_not_banned(page) -> None:
    html = page.content()
    visible = page.locator("body").inner_text()
    for phrase in BANNED:
        assert phrase not in html
        assert phrase not in visible


def _open(page, base: str, case_id: str, lines: int) -> None:
    page.goto(
        f"{base}/?case={case_id}&debug=1",
        wait_until="domcontentloaded",
        timeout=120_000,
    )
    page.wait_for_function(
        """(count) => {
          const source = document.getElementById("r-source");
          return !!(
            window.AIRWAY
            && typeof reviewWarningLines === "function"
            && window.__p2_debug
            && source
            && source.textContent !== "unknown"
            && document.querySelectorAll("#switcher button").length >= 4
            && document.querySelectorAll("#review-warnings-list li").length === count
          );
        }""",
        arg=lines,
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


def test_review_warnings_in_every_view(server):
    sync_playwright = _playwright_or_skip()
    cprint("review warnings: four views, collapse, no sign-off wording", "cyan")
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page(viewport=VIEWPORT)
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.on(
            "console",
            lambda message: errors.append(message.text) if message.type == "error" else None,
        )
        _open(page, server, "local-001", 2)
        assert not errors, errors
        review = page.evaluate("() => window.AIRWAY.review")
        assert review == [
            {"kind": "lobe_without_airways", "lobe": "left upper lobe"},
            {
                "kind": "small_lobe",
                "lobe": "right middle lobe",
                "ml": 42.5,
                "median_ml": 180,
            },
        ]
        mapped = page.evaluate(
            """() => reviewWarningLines({
              review: [
                {kind: "lobe_without_airways", lobe: 1},
                {kind: "lobe_without_airways", lobe: "lung_lower_lobe_right"},
                {kind: "small_lobe", lobe: "right middle lobe", ml: 10, median_ml: 80.5},
                {kind: "small_lobe", lobe: "lung_upper_lobe_right", ml: 12.5},
              ],
            })"""
        )
        assert mapped == [
            AIRWAY_LINE,
            "Right lower lobe: no airways found in this lobe",
            "Right middle lobe: smaller than expected (10 ml, median 80.5 ml)",
            "Right upper lobe: smaller than expected (12.5 ml)",
        ]
        for label, key in VIEWS:
            _show(page, label, key)
            _assert_lines(page)
            _assert_not_banned(page)
            assert not errors, (key, errors)
            _shot(page, f"{key}.png")
        page.locator("#review-warnings-toggle").click()
        assert page.locator("#review-warnings").evaluate(
            "el => el.classList.contains('collapsed')"
        )
        assert page.locator("#review-warnings-list").is_hidden()
        assert page.locator("#review-warnings-toggle").get_attribute("aria-expanded") == "false"
        assert page.locator("#review-warnings-toggle").is_visible()
        _show(page, "CT panes", "ct-panes")
        assert page.locator("#review-warnings-list").is_hidden()
        _shot(page, "collapsed.png")
        page.locator("#review-warnings-toggle").click()
        _assert_lines(page)
        assert not errors, errors
        browser.close()


def test_no_review_flags_hides_panel(server):
    sync_playwright = _playwright_or_skip()
    cprint("review warnings: empty review hides the panel", "cyan")
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page(viewport=VIEWPORT)
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.on(
            "console",
            lambda message: errors.append(message.text) if message.type == "error" else None,
        )
        _open(page, server, "local-002", 0)
        assert page.evaluate("() => window.AIRWAY.review") == []
        for label, key in VIEWS:
            _show(page, label, key)
            assert page.locator("#review-warnings").is_hidden()
            visible = page.locator("body").inner_text()
            assert "Review warnings" not in visible
            assert "no airways found" not in visible
            assert "smaller than expected" not in visible
            _assert_not_banned(page)
            assert not errors, (key, errors)
        _shot(page, "no-flags.png")
        browser.close()
