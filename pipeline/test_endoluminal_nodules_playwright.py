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
        {"name": "arteries", "file": "arteries.bin", "color": "#4f7fbf",
         "opacity": 1.0, "vertices": 3, "triangles": 1},
        {"name": "veins", "file": "veins.bin", "color": "#b4514f",
         "opacity": 1.0, "vertices": 3, "triangles": 1},
        {"name": "lungs", "file": "lungs.bin", "color": "#c9b8ac",
         "opacity": 0.4, "vertices": 3, "triangles": 1},
    ]
    offsets = [(0, 0, 0), (0, 0, -20), (0, 0, -40), (10, 0, -40),
               (0, 8, -20), (0, -8, -20), (200, 0, -20)]
    for layer, offset in zip(layers, offsets):
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


def test_v_cycles_inside_outside_split(server):
    from io import BytesIO

    from PIL import Image

    sync_playwright = _playwright_or_skip()
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page(viewport=VIEWPORT)
        try:
            page.goto(
                f"{server}/endoluminal.html?case=/case-web/local-001",
                wait_until="domcontentloaded",
            )
            page.wait_for_function(
                "() => window.__endoDebug && window.__endoDebug.meshes.arteries",
                timeout=120_000,
            )
            visible = (
                "() => Object.fromEntries(Object.entries(window.__endoDebug.meshes)"
                ".map(([n, m]) => [n, m.visible]))")
            clocks = (
                "() => { const c = document.getElementById('clocks');"
                " const d = c.getContext('2d').getImageData(0, 0, c.width, c.height).data;"
                " let n = 0; for (let i = 3; i < d.length; i += 4) if (d[i]) n++; return n; }")
            # Panel 4 opens in split view. Cycle split -> inside -> outside.
            assert page.evaluate("() => window.__endoDebug.viewMode()") == "split"
            page.keyboard.press("v")
            page.wait_for_function("() => window.__endoDebug.viewMode() === 'inside'")
            page.keyboard.press("v")
            page.wait_for_function("() => window.__endoDebug.viewMode() === 'outside'")
            layers = page.evaluate(visible)
            assert layers["airways"] is True
            assert layers["nodule-1"] is True and layers["nodule-2"] is True
            for name in ("arteries", "veins", "lungs"):
                assert layers[name] is False, (name, layers)
            assert page.evaluate(clocks) > 0
            # The airway tree fills 90% of the shorter screen side.
            frame = page.evaluate(
                """() => {
                  const d = window.__endoDebug, m = d.meshes.airways;
                  const a = m.geometry.attributes.position, v = new d.THREE.Vector3();
                  let x0 = 1e9, x1 = -1e9, y0 = 1e9, y1 = -1e9;
                  m.updateMatrixWorld(true);
                  for (let i = 0; i < a.count; i++) {
                    v.set(a.getX(i), a.getY(i), a.getZ(i))
                      .applyMatrix4(m.matrixWorld).project(d.camera);
                    x0 = Math.min(x0, v.x); x1 = Math.max(x1, v.x);
                    y0 = Math.min(y0, v.y); y1 = Math.max(y1, v.y);
                  }
                  return { w: (x1 - x0) / 2, h: (y1 - y0) / 2 };
                }""")
            assert max(frame.values()) == pytest.approx(0.9, abs=0.06), frame

            dist = (
                "() => window.__endoDebug.camera.position"
                ".distanceTo(window.__endoDebug.controls.target)")
            fit = page.evaluate(dist)
            assert fit < 100, fit   # hidden far layers are ignored
            point_dist = """() => {
              const d = window.__endoDebug, m = new d.THREE.Vector3(...d.markerPos());
              return d.camera.position.distanceTo(m);
            }"""
            before = page.evaluate(point_dist)
            page.keyboard.down("ArrowUp")
            page.wait_for_timeout(400)
            page.keyboard.up("ArrowUp")
            zoomed = page.evaluate(point_dist)
            lz = page.evaluate("() => window.__endoDebug.lastZoom")
            assert lz["after"] == pytest.approx(lz["before"] / 2, rel=0.01), lz
            assert zoomed == pytest.approx(lz["after"], rel=0.03), (zoomed, lz)
            assert zoomed < before, (before, zoomed)
            ndc = "() => window.__endoDebug.markerNDC()"
            x, y, _ = page.evaluate(ndc)
            assert x == pytest.approx(0, abs=0.05), (x, y)
            assert y == pytest.approx(0.4, abs=0.05), (x, y)   # 30% from top
            page.keyboard.down("ArrowUp")
            page.wait_for_timeout(300)
            page.keyboard.up("ArrowUp")
            assert page.evaluate(dist) == pytest.approx(zoomed, rel=0.05)
            x, y, _ = page.evaluate(ndc)
            assert x == pytest.approx(0, abs=0.05), (x, y)
            assert y == pytest.approx(0.4, abs=0.05), (x, y)

            page.keyboard.press("v")
            page.wait_for_function("() => window.__endoDebug.viewMode() === 'split'")
            page.wait_for_timeout(300)
            half = page.evaluate("() => Math.floor(innerWidth / 2)")
            left = Image.open(BytesIO(page.screenshot(
                clip={"x": 4, "y": 4, "width": 12, "height": 12}))).convert("RGB")
            right = Image.open(BytesIO(page.screenshot(
                clip={"x": half + 4, "y": 4, "width": 12, "height": 12}))).convert("RGB")
            lpx, rpx = left.getpixel((5, 5)), right.getpixel((5, 5))
            assert lpx[0] > lpx[2], lpx   # inside background is warm brown
            assert rpx[2] > rpx[0], rpx   # outside background is blue-black
            assert page.evaluate(clocks) > 0
            outside_before = page.evaluate(
                "() => window.__endoDebug.camera.position.toArray()")
            inside_before = page.evaluate(
                "() => window.__endoDebug.insideCam.position.toArray()")
            page.keyboard.down("ArrowUp")
            page.wait_for_timeout(400)
            page.keyboard.up("ArrowUp")
            # The split half must not chase or zoom the outside camera.
            assert page.evaluate(
                "() => window.__endoDebug.camera.position.toArray()") == outside_before
            assert page.evaluate(
                "() => window.__endoDebug.insideCam.position.toArray()") != inside_before
            # Split navigation refits the outside half to the airway.
            frame = page.evaluate(
                """() => {
                  const d = window.__endoDebug, m = d.meshes.airways;
                  const a = m.geometry.attributes.position, v = new d.THREE.Vector3();
                  let x0 = 1e9, x1 = -1e9, y0 = 1e9, y1 = -1e9;
                  m.updateMatrixWorld(true);
                  for (let i = 0; i < a.count; i++) {
                    v.set(a.getX(i), a.getY(i), a.getZ(i))
                      .applyMatrix4(m.matrixWorld).project(d.camera);
                    x0 = Math.min(x0, v.x); x1 = Math.max(x1, v.x);
                    y0 = Math.min(y0, v.y); y1 = Math.max(y1, v.y);
                  }
                  return { w: (x1 - x0) / 2, h: (y1 - y0) / 2 };
                }""")
            assert max(frame.values()) == pytest.approx(0.9, abs=0.06), frame

            # At the destination the split view switches to the 3D end view.
            page.keyboard.down("ArrowUp")
            page.wait_for_function(
                "() => window.__endoDebug.progress()"
                " >= window.__endoDebug.routeLength() - 0.5", timeout=15_000)
            page.keyboard.up("ArrowUp")
            page.wait_for_timeout(300)
            st = page.evaluate(
                "() => ({mode: window.__endoDebug.viewMode(),"
                " reveal: window.__endoDebug.reveal(),"
                " progress: window.__endoDebug.progress(),"
                " length: window.__endoDebug.routeLength()})")
            assert st["mode"] == "outside" and st["reveal"] is True, st

            page.keyboard.press("v")   # outside -> split
            page.wait_for_function("() => window.__endoDebug.viewMode() === 'split'")
            page.keyboard.press("v")   # split -> inside
            page.wait_for_function("() => window.__endoDebug.viewMode() === 'inside'")
            layers = page.evaluate(visible)
            assert layers["nodule-1"] is False and layers["arteries"] is False
            assert page.evaluate(clocks) > 0

            page.keyboard.press("v")   # inside -> outside
            page.wait_for_function("() => window.__endoDebug.viewMode() === 'outside'")
            layers = page.evaluate(visible)
            assert layers["nodule-1"] is True and layers["arteries"] is False
        finally:
            browser.close()


def test_case_click_does_not_retarget(server):
    sync_playwright = _playwright_or_skip()
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page(viewport=VIEWPORT)
        try:
            page.goto(
                f"{server}/endoluminal.html?case=/case-web/local-001",
                wait_until="domcontentloaded",
            )
            page.wait_for_function(
                """() => window.__endoDebug
                  && window.__endoDebug.routeInfo() != null""",
                timeout=120_000,
            )
            assert page.evaluate(
                "() => window.__endoDebug.targetPickAllowed()") is False
            page.keyboard.press("v")
            page.wait_for_function("() => window.__endoDebug.viewMode() === 'inside'")
            page.keyboard.press("v")
            page.wait_for_function("() => window.__endoDebug.viewMode() === 'outside'")
            before = page.evaluate("() => window.__endoDebug.routeInfo()")
            point = page.evaluate(
                """() => {
                  const d = window.__endoDebug, m = d.meshes['nodule-2'];
                  const a = m.geometry.attributes.position, v = new d.THREE.Vector3();
                  for (let i = 0; i < a.count; i++)
                    v.add(new d.THREE.Vector3(a.getX(i), a.getY(i), a.getZ(i)));
                  m.updateMatrixWorld(true);
                  v.divideScalar(a.count).applyMatrix4(m.matrixWorld).project(d.camera);
                  return { x: (v.x + 1) / 2 * innerWidth, y: (1 - v.y) / 2 * innerHeight };
                }""")
            page.mouse.click(point["x"], point["y"])
            page.wait_for_timeout(500)
            # A Case owns the route: clicking another nodule must not re-plan it.
            assert page.evaluate("() => window.__endoDebug.routeInfo()") == before
        finally:
            browser.close()


def _corners(data_url):
    from io import BytesIO

    from PIL import Image

    img = Image.open(BytesIO(__import__("base64").b64decode(
        data_url.split("base64,", 1)[1]))).convert("RGB")
    w, h = img.size
    return img.getpixel((4, 4)), img.getpixel((w - 5, 4))


def test_save_frame_matches_the_visible_view(server):
    sync_playwright = _playwright_or_skip()
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page(viewport=VIEWPORT)
        try:
            page.goto(
                f"{server}/endoluminal.html?case=/case-web/local-001",
                wait_until="domcontentloaded",
            )
            page.wait_for_function(
                "() => window.__endoDebug && window.__endoDebug.meshes.arteries",
                timeout=120_000,
            )
            capture = "() => window.__endoDebug.captureFrame(400, 300)"

            # Split: left inside (warm), right outside (blue-black).
            left, right = _corners(page.evaluate(capture))
            assert left[0] > left[2], (left, right)
            assert right[2] > right[0], (left, right)

            page.keyboard.press("v")   # split -> inside
            page.wait_for_function("() => window.__endoDebug.viewMode() === 'inside'")
            left, right = _corners(page.evaluate(capture))
            assert left[0] > left[2] and right[0] > right[2], (left, right)

            page.keyboard.press("v")   # inside -> outside
            page.wait_for_function("() => window.__endoDebug.viewMode() === 'outside'")
            left, right = _corners(page.evaluate(capture))
            assert left[2] > left[0] and right[2] > right[0], (left, right)
        finally:
            browser.close()


def test_record_frames_follow_the_visible_view(server):
    sync_playwright = _playwright_or_skip()
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page(viewport=VIEWPORT)
        try:
            page.goto(
                f"{server}/endoluminal.html?case=/case-web/local-001",
                wait_until="domcontentloaded",
            )
            page.wait_for_function(
                "() => window.__endoDebug && window.__endoDebug.meshes.arteries",
                timeout=120_000,
            )
            page.evaluate(
                """() => {
                  window.__recordFrame = null;
                  addEventListener('message', (ev) => {
                    if (ev.data && ev.data.type === 'endo3d-video-frame' && ev.data.dataUrl) {
                      window.__recordFrame = ev.data.dataUrl;
                    }
                  });
                }""")

            def grab(mode):
                assert page.evaluate("() => window.__endoDebug.viewMode()") == mode
                page.evaluate("() => { window.__recordFrame = null; }")
                page.evaluate(
                    """() => window.postMessage({ type: 'endo3d-record-start', fps: 30,
                      width: 400, height: 300 }, location.origin)""")
                page.wait_for_function("() => window.__recordFrame != null", timeout=15_000)
                page.evaluate(
                    "() => window.postMessage({ type: 'endo3d-record-stop' }, location.origin)")
                return _corners(page.evaluate("() => window.__recordFrame"))

            left, right = grab("split")
            assert left[0] > left[2] and right[2] > right[0], (left, right)

            page.keyboard.press("v")   # split -> inside
            page.wait_for_function("() => window.__endoDebug.viewMode() === 'inside'")
            left, right = grab("inside")
            assert left[0] > left[2] and right[0] > right[2], (left, right)

            page.keyboard.press("v")   # inside -> outside
            page.wait_for_function("() => window.__endoDebug.viewMode() === 'outside'")
            left, right = grab("outside")
            assert left[2] > left[0] and right[2] > right[0], (left, right)
        finally:
            browser.close()


def test_save_inside_repairs_a_stale_inside_camera(server):
    sync_playwright = _playwright_or_skip()
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page(viewport=VIEWPORT)
        try:
            page.goto(
                f"{server}/endoluminal.html?case=/case-web/local-001",
                wait_until="domcontentloaded",
            )
            page.wait_for_function(
                "() => window.__endoDebug && window.__endoDebug.routeLength()",
                timeout=120_000,
            )
            page.keyboard.press("v")   # split -> inside
            # A throttled animation loop leaves the camera far away; saving must recover.
            page.evaluate(
                "() => window.__endoDebug.insideCam.position.set(1000, 1000, 1000)")
            page.evaluate("() => window.__endoDebug.captureFrame(400, 300)")
            pos = page.evaluate(
                "() => window.__endoDebug.insideCam.position.toArray()")
            marker = page.evaluate("() => window.__endoDebug.markerPos()")
            assert pos == pytest.approx(marker, abs=0.05), (pos, marker)
        finally:
            browser.close()


def test_position_sidebar_shows_iframe_metrics(server):
    sync_playwright = _playwright_or_skip()
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page(viewport=VIEWPORT)
        page.goto(
            f"{server}/renderer.template.html?case=local-001&allow_review=1"
            "&debug=1&variant=endoluminal",
            wait_until="domcontentloaded",
            timeout=120_000,
        )
        page.wait_for_function(
            "() => window.__p3_debug && window.__p3_debug.airway()",
            timeout=120_000,
        )
        # Panel 4 sets the shared readout from the iframe's route metrics.
        page.wait_for_function(
            """() => {
              const v = id => document.getElementById(id).textContent;
              return v("r-gen") !== "—" && v("r-rad").endsWith(" mm")
                && v("r-togo").endsWith(" mm") && v("r-turns") !== "—";
            }""",
            timeout=120_000,
        )
        values = page.evaluate(
            """() => ["r-gen", "r-rad", "r-togo", "r-turns"]
              .map(id => document.getElementById(id).textContent)""")
        assert values[0].isdigit(), values
        assert values[1].endswith(" mm"), values
        assert values[2].endswith(" mm"), values
        assert values[3].isdigit(), values
        browser.close()


def test_position_rows_follow_the_panel(server):
    sync_playwright = _playwright_or_skip()
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page(viewport=VIEWPORT)
        page.goto(
            f"{server}/renderer.template.html?case=local-001&allow_review=1"
            "&debug=1&variant=ct-panes",
            wait_until="domcontentloaded",
            timeout=120_000,
        )
        page.wait_for_function(
            "() => window.__p3_debug && window.__p3_debug.airway()",
            timeout=120_000,
        )

        def shown():
            return page.evaluate(
                """() => {
                  const drawn = id => {
                    const el = document.getElementById(id);
                    return !!el && el.getClientRects().length > 0;
                  };
                  return {
                    group: drawn("g-nav"),
                    title: document.getElementById("g-nav-title").textContent,
                    position: drawn("row-gen"),
                    ct: drawn("row-ct-wl"),
                  };
                }""")

        # Panel 1 (CT panes) shows the CT rows only.
        assert shown() == {"group": True, "title": "CT",
                           "position": False, "ct": True}
        # Panels 2 and 3 show neither group.
        page.evaluate("() => window.__p3_debug.setVariant(0)")
        assert shown()["group"] is False
        page.evaluate("() => window.__p3_debug.setVariant(2)")
        assert shown()["group"] is False
        # Panel 4 (Endoluminal) shows the Position rows only.
        page.evaluate("() => window.__p3_debug.setVariant(1)")
        assert shown() == {"group": True, "title": "Position",
                           "position": True, "ct": False}
        browser.close()
