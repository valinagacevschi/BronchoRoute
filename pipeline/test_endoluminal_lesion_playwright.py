# Changes: Lesion label clearance uses the visible canvas, arrowhead, and limitations panel.
# Changes: endoluminal Lesion sphere, arrow, reached, and absent Playwright checks.
"""Endoluminal Lesion: sized sphere, edge arrow, reached, and no demo marker."""

from __future__ import annotations

import json
import math
import os
import socket
import subprocess
import time
import urllib.request
from pathlib import Path

import numpy as np
import pytest
from termcolor import cprint

from test_app_server import _stamp_pipeline_version, _write_case_tree
from test_viewer_v2_playwright import VIEWPORT, _playwright_or_skip

_PLACE = """(spec) => {
  const cam = window.__p2_debug.endoCamera();
  const air = window.__p3_debug.airway();
  if (!spec.lesion) {
    air.lesion = null;
  } else {
    const centre = [0, 1, 2].map(i =>
      cam.pos[i] + cam.dir[i] * spec.along
      + cam.right[i] * spec.right + cam.up[i] * spec.up
    );
    air.lesion = {
      ras_mm: centre.map((value, i) => value + cam.origin[i]),
      diameter_mm: spec.diameter,
    };
  }
  window.__p2_debug.setFlyS(cam.s);
  return window.__p2_debug.lesionViews().endoluminal;
}"""


def _seed(root: Path) -> None:
    paths = _write_case_tree(
        root,
        shape=(48, 40, 64),
        affine=np.diag([1.0, 1.0, 1.0, 1.0]),
        mask_box=(slice(2, 46), slice(2, 38), slice(2, 62)),
    )
    graph = json.loads(paths["graph"].read_text())
    graph["review"] = []
    graph["root_node_id"] = 0
    graph["nodes"] = [
        {"id": 0, "position_mm": [0.0, 0.0, 0.0], "radius_mm": 8.0},
        {"id": 1, "position_mm": [0.0, 0.0, -160.0], "radius_mm": 6.0},
    ]
    graph["edges"] = [{
        "from": 0, "to": 1, "generation": 0, "radius_mm": 7.0, "length_mm": 160.0,
        "lobe": None, "airway_name": "trachea", "provenance": "ct",
        "centreline_mm": [[0.0, 0.0, -float(z)] for z in range(0, 161, 2)],
    }]
    paths["graph"].write_text(json.dumps(graph))
    case = {
        "id": "local-001",
        "records": [{
            "step": 1,
            "kind": "series_confirmed",
            "data": {
                "folder": "SERIES0", "series_number": 1,
                "description": "synthetic", "kernel": "STANDARD", "overridden": False,
            },
            "clinician": "Radiologist, A.B.",
            "at": "2026-01-01T00:00:00Z",
        }],
        "versions": [],
        "review_signoff": [],
        "source": {
            "folder": "SERIES0", "series_number": 1,
            "series_description": "synthetic", "convolution_kernel": "STANDARD",
        },
        "lesion": {
            "ras_mm": [10.0, 0.0, -50.0],
            "diameter_mm": 20,
            "confirmed_by": "Radiologist, A.B.",
            "at": "2026-01-01T00:00:00Z",
        },
    }
    paths["case"].write_text(json.dumps(case, indent=2) + "\n")
    _stamp_pipeline_version(paths["case"])


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    root = tmp_path_factory.mktemp("endo_lesion_38")
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
    cprint(f"endoluminal lesion server on {port}", "cyan")
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
                pytest.fail(f"lesion server exited\n{log_path.read_text()[-2000:]}")
            try:
                with urllib.request.urlopen(f"{base}/api/cases", timeout=1):
                    break
            except OSError:
                time.sleep(0.1)
        else:
            pytest.fail("lesion server did not start")
        body = json.dumps({"role": "Radiologist", "initials": "A.B."}).encode()
        req = urllib.request.Request(
            f"{base}/api/clinician",
            data=body,
            headers={"Content-Type": "application/json"},
            method="PUT",
        )
        with urllib.request.urlopen(req, timeout=5) as res:
            assert res.status == 200
        yield base
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)
        log.close()


def _shot(page, shots: Path, name: str) -> Path:
    path = shots / name
    page.screenshot(path=str(path))
    cprint(str(path), "cyan")
    assert path.stat().st_size > 10_000
    return path


def _overlap(rect, box) -> bool:
    return (
        rect["x"] < box["r"]
        and rect["x"] + rect["w"] > box["l"]
        and rect["y"] < box["b"]
        and rect["y"] + rect["h"] > box["t"]
    )


def _inside(rect, box) -> bool:
    return (
        rect["x"] >= box["l"] - 0.75
        and rect["y"] >= box["t"] - 0.75
        and rect["x"] + rect["w"] <= box["r"] + 0.75
        and rect["y"] + rect["h"] <= box["b"] + 0.75
    )


def _js_round(value: float) -> int:
    return math.floor(value + 0.5)


def _box(rect) -> dict:
    return {
        "l": rect["x"],
        "t": rect["y"],
        "r": rect["x"] + rect["w"],
        "b": rect["y"] + rect["h"],
    }


_GEOM = """() => {
  const canvas = document.getElementById("c").getBoundingClientRect();
  const viewPort = window.visualViewport;
  const view = viewPort ? {
    l: viewPort.offsetLeft,
    t: viewPort.offsetTop,
    r: viewPort.offsetLeft + viewPort.width,
    b: viewPort.offsetTop + viewPort.height,
  } : { l: 0, t: 0, r: window.innerWidth, b: window.innerHeight };
  const limits = document.getElementById("limitations-panel").getBoundingClientRect();
  const rail = document.querySelector(".rail").getBoundingClientRect();
  const cam = window.__p2_debug.endoCamera();
  const rig = window.__p2_debug.rig();
  return {
    canvas: {
      left: canvas.left, top: canvas.top, right: canvas.right, bottom: canvas.bottom,
      width: canvas.width, height: canvas.height,
    },
    view,
    layout: { l: 0, t: 0, r: window.innerWidth, b: window.innerHeight },
    limitations: { l: limits.left, t: limits.top, r: limits.right, b: limits.bottom },
    rail: { l: rail.left, t: rail.top, r: rail.right, b: rail.bottom },
    clocks: rig.panel,
    camW: cam.w,
    camH: cam.h,
  };
}"""

_LABEL_READING = """() => {
  window.__p2_debug.setFlyS(window.__p2_debug.endoCamera().s);
  const lesion = window.__p2_debug.lesionViews().endoluminal;
  const rect = lesion.labelRect;
  const cv = document.getElementById("c");
  const ctx = cv.getContext("2d");
  const dpr = cv.width / cv.clientWidth;
  const x0 = Math.max(0, Math.floor(rect.x * dpr));
  const y0 = Math.max(0, Math.floor(rect.y * dpr));
  const x1 = Math.min(cv.width, Math.ceil((rect.x + rect.w) * dpr));
  const y1 = Math.min(cv.height, Math.ceil((rect.y + rect.h) * dpr));
  const data = ctx.getImageData(x0, y0, Math.max(1, x1 - x0), Math.max(1, y1 - y0)).data;
  const lin = (c) => {
    const s = c / 255;
    return s <= 0.04045 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4;
  };
  let minL = 1;
  let maxL = 0;
  for (let i = 0; i < data.length; i += 4) {
    const L = 0.2126 * lin(data[i]) + 0.7152 * lin(data[i + 1]) + 0.0722 * lin(data[i + 2]);
    if (L < minL) minL = L;
    if (L > maxL) maxL = L;
  }
  return { lesion, contrast: (maxL + 0.05) / (minL + 0.05) };
}"""

_PIXEL = """(pt) => {
  window.__p2_debug.setFlyS(window.__p2_debug.endoCamera().s);
  const cv = document.getElementById("c");
  const ctx = cv.getContext("2d");
  const dpr = cv.width / cv.clientWidth;
  const x = Math.min(cv.width - 1, Math.max(0, Math.floor(pt.x * dpr)));
  const y = Math.min(cv.height - 1, Math.max(0, Math.floor(pt.y * dpr)));
  const data = ctx.getImageData(x, y, 1, 1).data;
  const lin = (c) => {
    const s = c / 255;
    return s <= 0.04045 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4;
  };
  return {
    rgb: [data[0], data[1], data[2]],
    L: 0.2126 * lin(data[0]) + 0.7152 * lin(data[1]) + 0.0722 * lin(data[2]),
  };
}"""


def _visible_canvas(geom) -> dict:
    canvas = geom["canvas"]
    view = geom["view"]
    return {
        "l": max(canvas["left"], view["l"]),
        "t": max(canvas["top"], view["t"]),
        "r": min(canvas["right"], view["r"]),
        "b": min(canvas["bottom"], view["b"]),
    }


def _map_rect(rect, geom) -> dict:
    scale_x = geom["canvas"]["width"] / geom["camW"]
    scale_y = geom["canvas"]["height"] / geom["camH"]
    return {
        "x": geom["canvas"]["left"] + rect["x"] * scale_x,
        "y": geom["canvas"]["top"] + rect["y"] * scale_y,
        "w": rect["w"] * scale_x,
        "h": rect["h"] * scale_y,
    }


def _expected_arrow_rect(endo) -> dict:
    ex, ey = endo["view"][0], -endo["view"][1]
    if math.hypot(ex, ey) < 1e-4:
        ex, ey = 0.0, 1.0
    length = math.hypot(ex, ey)
    dx, dy = ex / length, ey / length
    angle = math.atan2(dy, dx)
    cosine, sine = math.cos(angle), math.sin(angle)
    points = []
    for lx, ly in ((0, 0), (-16, -6), (-16, 6)):
        points.append((
            endo["x"] + lx * cosine - ly * sine,
            endo["y"] + lx * sine + ly * cosine,
        ))
    min_x = min(point[0] for point in points)
    min_y = min(point[1] for point in points)
    max_x = max(point[0] for point in points)
    max_y = max(point[1] for point in points)
    return {"x": min_x, "y": min_y, "w": max_x - min_x, "h": max_y - min_y}


def _assert_label_clear(page, endo) -> dict:
    reading = page.evaluate(_LABEL_READING)
    fresh = reading["lesion"]
    assert fresh["mode"] == endo["mode"]
    assert fresh["label"] == endo["label"]
    assert fresh["labelRect"]["h"] >= 18, fresh["labelRect"]
    cprint(f"label contrast {reading['contrast']:.2f}", "cyan")
    assert reading["contrast"] >= 4.5, reading["contrast"]
    geom = page.evaluate(_GEOM)
    visible = _visible_canvas(geom)
    assert visible["r"] - visible["l"] > 100 and visible["b"] - visible["t"] > 100
    label = _map_rect(fresh["labelRect"], geom)
    assert _inside(label, visible), (label, visible)
    assert _inside(label, geom["view"]), (label, geom["view"])
    assert _inside(label, geom["layout"]), (label, geom["layout"])
    limits = geom["limitations"]
    assert limits["r"] > limits["l"] and limits["b"] > limits["t"]
    assert not _overlap(label, limits), (label, limits)
    assert not _overlap(label, geom["rail"]), (label, geom["rail"])
    clocks = _map_rect({
        "x": geom["clocks"]["l"],
        "y": geom["clocks"]["t"],
        "w": geom["clocks"]["r"] - geom["clocks"]["l"],
        "h": geom["clocks"]["b"] - geom["clocks"]["t"],
    }, geom)
    assert geom["clocks"]["r"] > geom["clocks"]["l"]
    assert not _overlap(label, _box(clocks)), (label, clocks)
    return fresh


def _assert_core(page, sphere) -> None:
    centre = page.evaluate(_PIXEL, {"x": sphere["x"], "y": sphere["y"]})
    wall_y = sphere["y"] + sphere["glowPx"] + 30
    if wall_y >= sphere["viewport"]["h"] - 2:
        wall_y = sphere["y"] - sphere["glowPx"] - 30
    wall = page.evaluate(_PIXEL, {"x": sphere["x"], "y": wall_y})
    red, green, blue = centre["rgb"]
    cprint(f"core rgb {centre['rgb']} wall {wall['rgb']}", "cyan")
    assert red > 180 and red > green > blue and (red - blue) > 90, (centre, wall)
    assert centre["L"] > wall["L"] + 0.12, (centre, wall)


def _assert_arrow(page, endo, along: float, right: float, up: float) -> None:
    assert endo["mode"] == "arrow"
    assert endo["label"] == f"Lesion {_js_round(endo['distanceMm'])} mm"
    assert "(demo)" not in endo["label"]
    fresh = _assert_label_clear(page, endo)
    geom = page.evaluate(_GEOM)
    visible = _visible_canvas(geom)
    arrow = fresh["arrowRect"]
    assert arrow is not None and arrow["w"] > 0 and arrow["h"] > 0
    assert not _overlap(fresh["labelRect"], _box(arrow)), (fresh["labelRect"], arrow)
    expected = _expected_arrow_rect(fresh)
    for key in ("x", "y", "w", "h"):
        assert abs(arrow[key] - expected[key]) <= 0.05, (arrow, expected)
    mapped_arrow = _map_rect(arrow, geom)
    assert _inside(mapped_arrow, visible), (mapped_arrow, visible)
    assert _inside(mapped_arrow, geom["view"]), (mapped_arrow, geom["view"])
    assert _inside(mapped_arrow, geom["layout"]), (mapped_arrow, geom["layout"])
    assert not _overlap(mapped_arrow, geom["limitations"]), (mapped_arrow, geom["limitations"])
    label_x, label_y = (
        fresh["labelRect"]["x"] + fresh["labelRect"]["w"] / 2,
        fresh["labelRect"]["y"] + fresh["labelRect"]["h"] / 2,
    )
    arrow_x, arrow_y = arrow["x"] + arrow["w"] / 2, arrow["y"] + arrow["h"] / 2
    inward_x = geom["camW"] / 2 - arrow_x
    inward_y = geom["camH"] / 2 - arrow_y
    toward_centre = inward_x * (label_x - arrow_x) + inward_y * (label_y - arrow_y)
    assert toward_centre > 0, (fresh["labelRect"], arrow)
    placed = math.hypot(along, right, up)
    assert abs(fresh["distanceMm"] - placed) <= 0.5
    assert abs(fresh["distanceMm"] - math.hypot(*fresh["view"])) <= 0.5
    ex, ey = fresh["view"][0], -fresh["view"][1]
    if math.hypot(ex, ey) < 1e-4:
        ex, ey = 0.0, 1.0
    dx = fresh["x"] - geom["camW"] / 2
    dy = fresh["y"] - geom["camH"] / 2
    if abs(ex) >= 1e-3:
        assert dx * ex > 0, (dx, ex, fresh["view"])
    if abs(ey) >= 1e-3:
        assert dy * ey > 0, (dy, ey, fresh["view"])
    scale_x = geom["canvas"]["width"] / geom["camW"]
    rail_right = (geom["rail"]["r"] - geom["canvas"]["left"]) / scale_x
    assert fresh["x"] > rail_right
    clocks = _map_rect({
        "x": geom["clocks"]["l"],
        "y": geom["clocks"]["t"],
        "w": geom["clocks"]["r"] - geom["clocks"]["l"],
        "h": geom["clocks"]["b"] - geom["clocks"]["t"],
    }, geom)
    assert not _overlap(mapped_arrow, _box(clocks)), (mapped_arrow, clocks)


def _place(page, **spec):
    return page.evaluate(_PLACE, spec)


def test_endoluminal_lesion_sphere_arrow_reached_and_absent(server):
    sync_playwright = _playwright_or_skip()
    shots = Path(os.environ.get("TMPDIR", "/tmp")) / "bronchoroute-38"
    shots.mkdir(parents=True, exist_ok=True)
    cprint("open synthetic straight route", "cyan")
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page(viewport=VIEWPORT)
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.on(
            "console",
            lambda message: errors.append(message.text) if message.type == "error" else None,
        )
        page.goto(
            f"{server}/?case=local-001&variant=endoluminal&debug=1&allow_review=1",
            wait_until="domcontentloaded",
            timeout=120_000,
        )
        page.wait_for_function(
            """() => {
              const cam = window.__p2_debug && window.__p2_debug.endoCamera
                && window.__p2_debug.endoCamera();
              const lesion = window.__p2_debug && window.__p2_debug.lesionViews
                && window.__p2_debug.lesionViews().endoluminal;
              return !!(cam && cam.w > 100 && cam.h > 100 && lesion);
            }""",
            timeout=120_000,
        )
        assert not errors, errors
        cprint("sphere ahead, off the centreline", "cyan")
        sphere = _place(page, lesion=True, along=50, right=12, up=0, diameter=20)
        assert sphere["mode"] == "sphere"
        assert sphere["radiusMm"] == pytest.approx(10)
        assert sphere["view"][2] == pytest.approx(50, abs=0.5)
        assert sphere["view"][0] == pytest.approx(12, abs=0.5)
        expected = (
            sphere["focalPx"] * sphere["radiusMm"]
            / math.sqrt(sphere["view"][2] ** 2 - sphere["radiusMm"] ** 2)
        )
        assert abs(sphere["radiusPx"] - expected) <= 1
        cap = 0.45 * min(sphere["viewport"]["w"], sphere["viewport"]["h"])
        assert sphere["glowPx"] == pytest.approx(min(sphere["radiusPx"] * 1.25, cap), abs=1e-6)
        assert sphere["glowPx"] <= sphere["radiusPx"] * 1.25 + 1
        placed = math.hypot(50, 12, 0)
        assert abs(sphere["distanceMm"] - placed) <= 0.5
        assert abs(sphere["distanceMm"] - math.hypot(*sphere["view"])) <= 0.5
        assert sphere["label"] == f"Lesion {_js_round(sphere['distanceMm'])} mm"
        assert "(demo)" not in sphere["label"]
        assert sphere["arrowRect"] is None
        _assert_label_clear(page, sphere)
        _assert_core(page, sphere)
        _shot(page, shots, "lesion-sphere.png")

        cprint("arrow behind the tip", "cyan")
        behind = _place(page, lesion=True, along=-35, right=0, up=0, diameter=16)
        _assert_arrow(page, behind, -35, 0, 0)
        assert behind["view"][2] < 0

        cprint("arrow far to the side", "cyan")
        side = _place(page, lesion=True, along=30, right=180, up=0, diameter=16)
        _assert_arrow(page, side, 30, 180, 0)
        assert side["view"][0] > 0
        assert side["x"] > side["viewport"]["w"] / 2
        _shot(page, shots, "lesion-arrow.png")

        cprint("tip inside the Lesion", "cyan")
        reached = _place(page, lesion=True, along=3, right=1, up=0, diameter=24)
        assert reached["mode"] == "reached"
        assert reached["label"] == "Lesion reached"
        assert reached["radiusPx"] is None
        assert reached["glowPx"] is None
        assert reached["arrowRect"] is None
        assert abs(reached["distanceMm"] - math.hypot(3, 1, 0)) <= 0.5
        _assert_label_clear(page, reached)

        cprint("no Lesion", "cyan")
        absent = _place(page, lesion=False)
        assert absent is None
        assert page.evaluate("() => window.__p2_debug.lesionViews().endoluminal") is None
        text = page.locator("body").inner_text()
        assert "Lesion" not in text
        assert "Lesion (demo)" not in text
        assert not errors, errors
        browser.close()
