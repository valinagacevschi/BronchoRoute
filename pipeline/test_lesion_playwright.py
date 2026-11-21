# Changes: stamp the synthetic Case with the current pipeline version.
# Changes: place a Lesion in the coronal pane and check CT, anatomical, and topology.
"""Lesion geometry and a real-click placement on a synthetic Case."""

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

from test_app_server import _stamp_pipeline_version, _write_case_tree
from test_viewer_v2_playwright import _playwright_or_skip


def _seed(root: Path) -> None:
    paths = _write_case_tree(
        root,
        shape=(48, 40, 64),
        affine=np.diag([1.0, 1.0, 1.0, 1.0]),
        mask_box=(slice(2, 46), slice(2, 38), slice(2, 62)),
    )
    graph = json.loads(paths["graph"].read_text())
    # CT-seen line at x=24. A closer interpolated branch must not win the marker.
    graph["nodes"] = [
        {"id": 0, "position_mm": [24.0, 20.0, 56.0], "radius_mm": 6.0},
        {"id": 1, "position_mm": [24.0, 20.0, 8.0], "radius_mm": 4.0},
        {"id": 2, "position_mm": [31.0, 20.0, 32.0], "radius_mm": 2.0},
    ]
    graph["edges"] = [
        {
            "from": 0, "to": 1, "generation": 0, "radius_mm": 5.0, "length_mm": 48.0,
            "lobe": None, "airway_name": "trachea", "provenance": "ct",
            "centreline_mm": [[24.0, 20.0, float(z)] for z in range(56, 7, -2)],
        },
        {
            "from": 0, "to": 2, "generation": 1, "radius_mm": 2.0, "length_mm": 25.0,
            "lobe": None, "airway_name": "bridge", "provenance": "interpolated",
            "centreline_mm": [[24.0, 20.0, 56.0], [31.0, 20.0, 32.0]],
        },
    ]
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
    }
    paths["case"].write_text(json.dumps(case, indent=2) + "\n")
    _stamp_pipeline_version(paths["case"])


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    root = tmp_path_factory.mktemp("lesion33")
    _seed(root)
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    base = f"http://127.0.0.1:{port}"
    home = root / "fake_home"
    home.mkdir()
    env = {**os.environ, "HOME": str(home), "LUNGNAV_PIPELINE_ROOT": str(root)}
    env.pop("PYTHONPATH", None)
    cprint(f"lesion server on {port}", "cyan")
    proc = subprocess.Popen(
        ["uv", "run", "python", "app_server.py", "--port", str(port)],
        cwd=Path(__file__).resolve().parent,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        for _ in range(200):
            assert proc.poll() is None, "lesion server exited before readiness"
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
        proc.wait(timeout=10)


def _open(page, server: str) -> None:
    page.goto(
        f"{server}/renderer.template.html?case=local-001&allow_review=1&debug=1&variant=ct-panes",
        wait_until="domcontentloaded",
        timeout=120_000,
    )


def _shot(page, shots: Path, name: str) -> Path:
    path = shots / name
    page.screenshot(path=str(path), full_page=True)
    print(path)
    cprint(f"screenshot {path}", "cyan")
    return path


def test_lesion_slice_and_ct_seen_geometry(server):
    sync_playwright = _playwright_or_skip()
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        _open(page, server)
        page.wait_for_function(
            "() => window.CtPanes && window.__p2_debug",
            timeout=120_000,
        )
        result = page.evaluate(
            """() => {
              const g = window.CtPanes.lesionSliceGeometry;
              const centre = [0, 0, 0];
              const axial = [0, 0, 1.5];
              const onPlane = g(centre, 20, [0, 0, 0], axial);
              const superior = g(centre, 20, [0, 0, -6], axial);
              const past = g(centre, 20, [0, 0, -12], axial);
              const anterior = g(centre, 20, [0, -4, 0], [0, 1, 0]);
              const right = g(centre, 20, [-3, 0, 0], [1, 0, 0]);
              const inferior = g(centre, 20, [0, 0, 5], axial);
              const edges = [
                { prov: "ct", f: 0, t: 1, c: [[0, 0, 0], [0, 0, 10]] },
                { prov: "interpolated", f: 0, t: 2, c: [[1, 0, 0], [1, 0, 1]] },
                { prov: "interpolated", f: 1, t: 3, ci: [0], c: [[0, 0, 0], [5, 0, 0], [10, 0, 0]] },
              ];
              const nearerBridge = window.CtPanes.nearestCtSeenSample(edges, [0, 0, 0], [1, 0, 0]);
              const ctPortion = window.CtPanes.nearestCtSeenSample(edges, [0, 0, 0], [10, 0, 0]);
              return {
                onPlane, superior, past, anterior, right, inferior,
                nearerBridge: { distMm: nearerBridge.distMm, prov: nearerBridge.edge.prov },
                ctPortion: { index: ctPortion.index, prov: ctPortion.edge.prov, distMm: ctPortion.distMm },
                hook: window.__p2_debug.routeTargetLesionAnchor(),
              };
            }"""
        )
        browser.close()
    assert result["onPlane"]["through"] is True
    assert result["onPlane"]["signedDistanceMm"] == pytest.approx(0, abs=1e-6)
    assert result["onPlane"]["inPlaneRadiusMm"] == pytest.approx(10)
    assert result["superior"]["through"] is True
    assert result["superior"]["signedDistanceMm"] == pytest.approx(6)
    assert result["superior"]["inPlaneRadiusMm"] == pytest.approx(8)
    assert result["past"]["through"] is False
    assert result["past"]["signedDistanceMm"] == pytest.approx(12)
    assert result["past"]["inPlaneRadiusMm"] == 0
    assert result["anterior"]["signedDistanceMm"] == pytest.approx(4)
    assert result["anterior"]["inPlaneRadiusMm"] == pytest.approx(84 ** 0.5)
    assert result["right"]["signedDistanceMm"] == pytest.approx(3)
    assert result["right"]["inPlaneRadiusMm"] == pytest.approx(91 ** 0.5)
    assert result["inferior"]["signedDistanceMm"] == pytest.approx(-5)
    assert result["nearerBridge"]["prov"] == "ct"
    assert result["nearerBridge"]["distMm"] == pytest.approx(1)
    assert result["ctPortion"]["prov"] == "interpolated"
    assert result["ctPortion"]["index"] == 2
    assert result["ctPortion"]["distMm"] == pytest.approx(0)
    assert result["hook"] is None


def test_lesion_place_diameter_and_views(server):
    sync_playwright = _playwright_or_skip()
    shots = Path(os.environ.get("TMPDIR", "/tmp")) / "bronchoroute-33"
    shots.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.on(
            "console",
            lambda message: errors.append(message.text) if message.type == "error" else None,
        )
        _open(page, server)
        page.wait_for_function(
            """() => {
              const ct = window.__p3_debug && window.__p3_debug.ct && window.__p3_debug.ct();
              return !!(ct && ct.isLoaded && ct.isLoaded());
            }""",
            timeout=180_000,
        )
        assert "Could not load" not in page.locator("body").inner_text()
        toolbar = page.locator(".ct-toolbar").inner_text()
        assert "confirm the Lesion" not in toolbar
        assert "Mark and confirm" not in toolbar
        airway = page.locator('[data-overlay="airway"]')
        assert airway.is_enabled()
        airway.click()
        assert airway.is_checked()
        _shot(page, shots, "overlays-before.png")

        page.locator("#ct-mark-lesion").click()
        placed = page.evaluate(
            """() => {
              const ct = window.__p3_debug.ct();
              const meta = ct.getMeta();
              const aff = meta.affine;
              const shape = meta.shape;
              const cont = ct.worldToVoxelCont([32, 20, 32]);
              const i = Math.max(0, Math.min(shape[0] - 1, Math.round(cont[0] - 0.5)));
              const j = Math.max(0, Math.min(shape[1] - 1, Math.round(cont[1] - 0.5)));
              const k = Math.max(0, Math.min(shape[2] - 1, Math.round(cont[2] - 0.5)));
              const world = [0, 1, 2].map(axis => (
                aff[axis][0] * (i + 0.5) + aff[axis][1] * (j + 0.5)
                + aff[axis][2] * (k + 0.5) + aff[axis][3]
              ));
              ct.setWorld(world);
              const scr = ct.screenOfWorld("coronal", world);
              const scr2 = ct.screenOfWorld("coronal", [world[0] + 10, world[1], world[2]]);
              return { world, scr, drag: [scr2.x - scr.x, scr2.y - scr.y] };
            }"""
        )
        assert placed["world"][0] == pytest.approx(32, abs=1.5)
        box = page.locator(".ct-pane-coronal canvas").bounding_box()
        x = box["x"] + placed["scr"]["x"]
        y = box["y"] + placed["scr"]["y"]
        page.mouse.move(x, y)
        page.mouse.down()
        page.mouse.up()
        page.keyboard.press("Escape")
        assert page.evaluate("() => window.__p3_debug.ct().getProvisional()") is None

        page.locator("#ct-mark-lesion").click()
        page.mouse.move(x, y)
        page.mouse.down()
        page.mouse.move(x + placed["drag"][0], y + placed["drag"][1], steps=8)
        page.mouse.up()
        status = page.locator("#ct-lesion-status").inner_text()
        assert "Ø" in status
        page.locator("#ct-confirm-lesion").click()
        page.wait_for_function(
            """() => {
              const lesion = window.__p3_debug.ct().getLesion();
              return !!(lesion && lesion.diameter_mm);
            }""",
            timeout=15_000,
        )
        with urllib.request.urlopen(
            f"{server}/api/cases/local-001/payload?allow_review=1", timeout=10,
        ) as res:
            payload = json.load(res)
        assert payload["lesion"]["diameter_mm"] == pytest.approx(20, abs=1.5)
        assert payload["lesion"]["ras_mm"] == pytest.approx(placed["world"], abs=2)
        assert "A.B." in payload["lesion"]["confirmed_by"]
        assert payload["lesion"]["at"]

        draws = page.evaluate("() => window.__p3_debug.ct().lesionDraws()")
        for pane in ("axial", "coronal", "sagittal"):
            assert draws[pane]["kind"] == "circle", draws[pane]
            assert draws[pane]["inPlaneRadiusMm"] == pytest.approx(10, abs=1.5)
        red = page.evaluate(
            """() => {
              const draws = window.__p3_debug.ct().lesionDraws();
              const out = {};
              for (const key of ["axial", "coronal", "sagittal"]) {
                const canvas = document.querySelector(`.ct-pane-${key} canvas`);
                const ctx = canvas.getContext("2d");
                const dpr = canvas.width / canvas.clientWidth;
                const draw = draws[key];
                let hits = 0;
                for (let a = 0; a < 12; a++) {
                  const ang = a * Math.PI / 6;
                  const px = Math.round((draw.cx + Math.cos(ang) * draw.radiusPx) * dpr);
                  const py = Math.round((draw.cy + Math.sin(ang) * draw.radiusPx) * dpr);
                  const p = ctx.getImageData(px, py, 1, 1).data;
                  if (p[0] > 140 && p[0] > p[1] + 25 && p[0] > p[2] + 25) hits += 1;
                }
                out[key] = hits;
              }
              return out;
            }"""
        )
        assert min(red.values()) >= 3, red
        _shot(page, shots, "ct-circles.png")

        axial = page.evaluate(
            """() => {
              const ct = window.__p3_debug.ct();
              const lesion = ct.getLesion().ras_mm;
              return ct.screenOfWorld("axial", lesion);
            }"""
        )
        axial_box = page.locator(".ct-pane-axial canvas").bounding_box()
        page.mouse.move(axial_box["x"] + axial["x"], axial_box["y"] + axial["y"])
        for _ in range(16):
            page.mouse.wheel(0, 120)
        off = page.evaluate("() => window.__p3_debug.ct().lesionDraws()")
        assert off["axial"]["kind"] == "cross", off["axial"]
        assert off["axial"]["label"].startswith("Lesion ")
        assert " mm" in off["axial"]["label"]
        assert off["axial"]["label"][7] in "+-"
        assert abs(off["axial"]["signedDistanceMm"]) > 10
        assert off["coronal"]["kind"] == "circle"
        assert off["sagittal"]["kind"] == "circle"
        _shot(page, shots, "ct-offslice.png")

        page.locator("#switcher button", has_text="Anatomical").click()
        page.wait_for_function(
            "() => { const v = window.__p2_debug.lesionViews().anatomical; return v && v.radiusPx > 4; }",
            timeout=10_000,
        )
        anatomical = page.evaluate("() => window.__p2_debug.lesionViews().anatomical")
        assert anatomical["label"] == "Lesion"
        assert anatomical["diameterMm"] == pytest.approx(20, abs=1.5)
        _shot(page, shots, "anatomical.png")

        page.locator("#switcher button", has_text="Topology").click()
        page.wait_for_function(
            """() => {
              const v = window.__p2_debug.lesionViews().topology;
              return v && v.label && v.label.includes("from airway");
            }""",
            timeout=10_000,
        )
        topology = page.evaluate("() => window.__p2_debug.lesionViews().topology")
        assert topology["label"].startswith("Lesion · ")
        assert topology["label"].endswith(" mm from airway")
        assert 5 < topology["distMm"] < 14, topology
        assert topology["from"] == 0 and topology["to"] == 1
        _shot(page, shots, "topology.png")
        assert not errors, errors
        browser.close()


def _api_call(server: str, method: str, path: str, body=None) -> dict | None:
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(
        f"{server}{path}",
        data=data,
        headers={"Content-Type": "application/json"},
        method=method,
    )
    with urllib.request.urlopen(req, timeout=10) as res:
        raw = res.read()
    return json.loads(raw) if raw else None


def test_candidates_toolbar_glyphs_and_confirm(server):
    """Detector candidates show as dashed glyphs and promote only on confirm."""
    sync_playwright = _playwright_or_skip()
    shots = Path(os.environ.get("TMPDIR", "/tmp")) / "bronchoroute-33"
    shots.mkdir(parents=True, exist_ok=True)
    _api_call(server, "DELETE", "/api/cases/local-001/lesion")
    _api_call(server, "DELETE", "/api/cases/local-001/candidates")
    near = [32.0, 20.0, 32.0]
    far = [28.0, 20.0, 28.0]
    _api_call(server, "PUT", "/api/cases/local-001/candidates", {
        "source": "lung_nodule_ct_detection",
        "candidates": [
            {"ras_mm": near, "diameter_mm": 6.0, "score": 0.91},
            {"ras_mm": far, "diameter_mm": 9.0, "score": 0.42},
        ],
    })
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        _open(page, server)
        page.wait_for_function(
            """() => {
              const ct = window.__p3_debug && window.__p3_debug.ct && window.__p3_debug.ct();
              return !!(ct && ct.isLoaded && ct.isLoaded());
            }""",
            timeout=180_000,
        )
        box = page.locator("#ct-candidates")
        assert box.is_visible()
        labels = box.locator("button").all_inner_texts()
        assert len(labels) == 3, labels
        assert labels[0].startswith("Candidate 1")
        assert "0.91" in labels[0] and "6.0 mm" in labels[0]
        assert labels[1].startswith("Candidate 2")
        assert "0.42" in labels[1] and "9.0 mm" in labels[1]
        assert labels[2] == "Clear candidates"

        page.evaluate("() => window.__p3_debug.ct().setWorld([32, 20, 32])")
        page.wait_for_timeout(300)
        orange = page.evaluate(
            """() => {
              const ct = window.__p3_debug.ct();
              const canvas = document.querySelector('.ct-pane-axial canvas');
              const ctx = canvas.getContext('2d');
              const dpr = canvas.width / canvas.clientWidth;
              const c = ct.screenOfWorld('axial', [32, 20, 32]);
              const edge = ct.screenOfWorld('axial', [35, 20, 32]);
              const r = Math.hypot(edge.x - c.x, edge.y - c.y);
              let hits = 0;
              for (let a = 0; a < 72; a++) {
                const ang = a * Math.PI / 36;
                const cx = Math.round((c.x + Math.cos(ang) * r) * dpr);
                const cy = Math.round((c.y + Math.sin(ang) * r) * dpr);
                let found = false;
                for (let dx = -1; dx <= 1 && !found; dx++) {
                  for (let dy = -1; dy <= 1 && !found; dy++) {
                    const p = ctx.getImageData(cx + dx, cy + dy, 1, 1).data;
                    if (p[0] > 180 && p[1] > 100 && p[1] < 190 && p[2] < 100) {
                      found = true;
                    }
                  }
                }
                if (found) hits += 1;
              }
              return hits;
            }"""
        )
        assert orange >= 6, orange
        _shot(page, shots, "ct-candidates.png")

        box.locator("button", has_text="Candidate 2").click()
        page.wait_for_function(
            """() => {
              const lesion = window.__p3_debug.ct().getLesion();
              return !!(lesion && Math.abs(lesion.ras_mm[0] - 28) < 1);
            }""",
            timeout=15_000,
        )
        page.wait_for_selector("#ct-candidates", state="hidden")
        with urllib.request.urlopen(
            f"{server}/api/cases/local-001/payload?allow_review=1", timeout=10,
        ) as res:
            payload = json.load(res)
        assert payload["lesion"]["ras_mm"] == pytest.approx(far, abs=1e-3)
        assert payload["lesion"]["diameter_mm"] == pytest.approx(9.0)
        assert payload["candidates"] == []

        _api_call(server, "PUT", "/api/cases/local-001/candidates", {
            "candidates": [{"ras_mm": near, "diameter_mm": 6.0, "score": 0.5}],
        })
        page.reload(wait_until="domcontentloaded", timeout=120_000)
        page.wait_for_function(
            """() => {
              const ct = window.__p3_debug && window.__p3_debug.ct && window.__p3_debug.ct();
              return !!(ct && ct.isLoaded && ct.isLoaded());
            }""",
            timeout=180_000,
        )
        box = page.locator("#ct-candidates")
        assert box.is_visible()
        box.locator("button", has_text="Clear candidates").click()
        page.wait_for_selector("#ct-candidates", state="hidden")
        with urllib.request.urlopen(
            f"{server}/api/cases/local-001/payload?allow_review=1", timeout=10,
        ) as res:
            payload = json.load(res)
        assert payload["candidates"] == []
        assert not errors, errors
        browser.close()
