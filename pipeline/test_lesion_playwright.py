# Changes: axial nodule contours replace the candidate circle on their own slice.
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


_CASE_ROOT: Path | None = None


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    global _CASE_ROOT
    root = tmp_path_factory.mktemp("lesion33")
    _CASE_ROOT = root
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
        toolbar = page.locator("#g-manual-lesion").inner_text()
        assert "confirm the Lesion" not in toolbar
        assert "Mark and confirm" not in toolbar
        airway = page.locator('[data-overlay-key="airway"]')
        assert airway.is_enabled()
        assert airway.get_attribute("aria-pressed") == "true"
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
        page.keyboard.press("Escape")
        assert page.evaluate("() => window.__p3_debug.ct().getProvisional()") is None

        page.locator("#ct-mark-lesion").click()
        assert page.locator("#ct-mark-lesion").is_disabled()
        assert page.locator("#ct-lesion-status").inner_text() == "Click a point on any CT pane"
        assert page.locator("#ct-clear-lesion").is_hidden()
        page.mouse.move(x, y)
        page.mouse.down()
        page.mouse.move(x + placed["drag"][0], y + placed["drag"][1], steps=8)
        page.mouse.up()
        page.wait_for_function(
            """() => {
              const lesion = window.__p3_debug.ct().getLesion();
              return !!(lesion && lesion.diameter_mm);
            }""",
            timeout=15_000,
        )
        status = page.locator("#ct-lesion-status").inner_text()
        assert "Lesion confirmed · Ø" in status
        assert page.locator("#ct-clear-lesion").is_visible()
        assert page.locator("#ct-mark-lesion").is_enabled()
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
              return v && v.label === "Nodule";
            }""",
            timeout=10_000,
        )
        topology = page.evaluate("() => window.__p2_debug.lesionViews().topology")
        assert topology["label"] == "Nodule"
        assert 5 < topology["distMm"] < 14, topology
        assert topology["from"] == 0 and topology["to"] == 1
        _shot(page, shots, "topology.png")

        page.locator("#ct-clear-lesion").click()
        page.wait_for_function(
            "() => window.__p3_debug.ct().getLesion() === null",
            timeout=15_000,
        )
        assert page.locator("#ct-clear-lesion").is_hidden()
        assert page.locator("#ct-mark-lesion").is_enabled()
        assert page.locator("#ct-lesion-status").inner_text() == ""
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
            {
                "ras_mm": near, "diameter_mm": 6.0, "score": 0.91,
                "id": 7, "volume_mm3": 113.1,
                "lobe": "lung_upper_lobe_left", "to_airway_mm": 4.5,
                "size_mm": [7.0, 6.0, 5.0],
            },
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
        assert "NODULES" in page.locator("#g-nodules").inner_text()
        labels = box.locator(".ct-candidate-confirm").all_inner_texts()
        assert len(labels) == 2, labels
        assert labels[0].startswith("Candidate 1")
        assert "0.91" in labels[0] and "6.0 mm" in labels[0]
        assert "113.1 mm³" in labels[0]
        assert "id 7" in labels[0]
        assert "lung_upper_lobe_left" in labels[0]
        assert "4.5 mm to airway" in labels[0]
        # The list says candidate, not nodule: the glossary term is
        # "Nodule candidate", and the id keeps the stable link.
        assert "nodule" not in labels[0].lower()
        assert box.locator(".ct-candidate-confirm").nth(0).get_attribute("data-nodule-id") == "7"
        assert labels[1].startswith("Candidate 2")
        assert "0.42" in labels[1] and "9.0 mm" in labels[1]
        assert "mm³" not in labels[1]
        assert "to airway" not in labels[1]
        assert box.locator(".ct-candidate-confirm").nth(1).get_attribute("data-nodule-id") is None
        assert box.locator(".ct-candidate-goto").all_inner_texts() == ["→", "→"]
        assert box.get_by_role("button", name="Go to candidate 1").is_visible()
        assert box.get_by_role("button", name="Go to candidate 2").is_visible()
        assert box.locator("button", has_text="Clear candidates").count() == 0

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
        # The detector list stays after a nodule becomes the Lesion.
        assert box.is_visible()
        assert box.locator(".ct-candidate-confirm").count() == 2
        with urllib.request.urlopen(
            f"{server}/api/cases/local-001/payload?allow_review=1", timeout=10,
        ) as res:
            payload = json.load(res)
        assert payload["lesion"]["ras_mm"] == pytest.approx(far, abs=1e-3)
        assert payload["lesion"]["diameter_mm"] == pytest.approx(9.0)
        assert len(payload["candidates"]) == 2

        assert not errors, errors
        browser.close()


def test_candidate_goto_moves_ct_without_confirm(server):
    """Go to moves the shared CT position without confirming or clearing."""
    sync_playwright = _playwright_or_skip()
    _api_call(server, "POST", "/api/cases/local-001/reset")
    _api_call(server, "DELETE", "/api/cases/local-001/candidates")
    near = [32.0, 20.0, 32.0]
    far = [28.0, 20.0, 28.0]
    _api_call(server, "PUT", "/api/cases/local-001/candidates", {
        "candidates": [
            {"ras_mm": near, "diameter_mm": 6.0, "score": 0.91, "id": 7},
            {"ras_mm": far, "diameter_mm": 9.0, "score": 0.42},
        ],
    })
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        errors = []
        requests = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.on("request", lambda request: requests.append(request.url))
        _open(page, server)
        page.wait_for_function(
            """() => {
              const ct = window.__p3_debug && window.__p3_debug.ct && window.__p3_debug.ct();
              return !!(ct && ct.isLoaded && ct.isLoaded());
            }""",
            timeout=180_000,
        )
        page.evaluate("() => window.__p3_debug.ct().setWorld([32, 20, 32])")
        page.wait_for_function(
            "() => window.__p3_debug.ct().getSlices().axial === 32",
            timeout=15_000,
        )
        box = page.locator("#ct-candidates")
        assert box.is_visible()
        box.get_by_role("button", name="Go to candidate 2").click()
        page.wait_for_function(
            "() => window.__p3_debug.ct().getSlices().axial === 28",
            timeout=15_000,
        )
        crosshair = page.evaluate("() => window.__p3_debug.ct().getCrosshair()")
        assert crosshair == pytest.approx(far)
        assert page.evaluate("() => window.__p3_debug.sharedPosSource()") == "candidate"
        # Go to is read-only: the list stays and nothing is confirmed.
        assert box.is_visible()
        assert box.get_by_role("button", name="Go to candidate 1").is_visible()
        assert box.get_by_role("button", name="Go to candidate 2").is_visible()
        assert not any("/confirm" in url for url in requests), requests
        with urllib.request.urlopen(
            f"{server}/api/cases/local-001/payload?allow_review=1", timeout=10,
        ) as res:
            payload = json.load(res)
        assert payload.get("lesion") is None
        assert len(payload["candidates"]) == 2
        assert not errors, errors
        browser.close()


def test_candidate_list_shows_measured_diameter(server):
    """A mask measurement outside the Lesion range stays visible in the list."""
    sync_playwright = _playwright_or_skip()
    assert _CASE_ROOT is not None
    _api_call(server, "POST", "/api/cases/local-001/reset")
    _api_call(server, "DELETE", "/api/cases/local-001/candidates")
    case_path = _CASE_ROOT / "data/extracted/local/local-001/case.json"
    case = json.loads(case_path.read_text())
    # The nodule stage writes measured candidates straight to the Case record.
    case["candidates"] = {
        "source": "lung_nodules",
        "at": "2026-01-01T00:00:00Z",
        "by": "pipeline",
        "items": [
            {
                "ras_mm": [32.0, 20.0, 32.0], "diameter_mm": 64.5,
                "score": 0.91, "id": 7,
            },
            # No measured diameter: keep the 10 mm display default.
            {"ras_mm": [28.0, 20.0, 28.0], "score": 0.42},
        ],
    }
    case_path.write_text(json.dumps(case, indent=2) + "\n")

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        try:
            _open(page, server)
            page.wait_for_function(
                """() => {
                  const ct = window.__p3_debug && window.__p3_debug.ct && window.__p3_debug.ct();
                  return !!(ct && ct.isLoaded && ct.isLoaded());
                }""",
                timeout=180_000,
            )
            labels = page.locator("#ct-candidates .ct-candidate-confirm").all_inner_texts()
        finally:
            browser.close()
    assert len(labels) == 2, labels
    assert "Ø 64.5 mm" in labels[0], labels[0]
    assert "Ø 10.0 mm" in labels[1], labels[1]


def _world_of(affine, i, j, k):
    return [
        affine[a][0] * i + affine[a][1] * j + affine[a][2] * k + affine[a][3]
        for a in range(3)
    ]


def test_axial_contour_replaces_dashed_circle_for_mask_candidate(server):
    """A contour follows the candidate's stable id, not its list position."""
    sync_playwright = _playwright_or_skip()
    assert _CASE_ROOT is not None
    _api_call(server, "POST", "/api/cases/local-001/reset")
    _api_call(server, "DELETE", "/api/cases/local-001/candidates")

    meta = _api_call(server, "GET", "/api/cases/local-001/ct/meta")
    shape = meta["shape"]
    affine = meta["affine"]
    offset = meta.get("crop_offset_voxels") or [0, 0, 0]
    cx, cy, ck = shape[0] // 2, shape[1] // 2, shape[2] // 2
    reach = 3
    contour_world = _world_of(affine, cx + 0.5, cy + 0.5, ck + 0.5)
    box_world = _world_of(affine, cx + 12.5, cy + 0.5, ck + 0.5)
    null_world = _world_of(affine, cx - 12.5, cy + 0.5, ck + 0.5)
    far_world = _world_of(affine, cx + 0.5, cy + 12.5, ck + 0.5)

    def square(origin_x, origin_y):
        return [
            [origin_x - reach + offset[0], origin_y - reach + offset[1]],
            [origin_x + reach + offset[0], origin_y - reach + offset[1]],
            [origin_x + reach + offset[0], origin_y + reach + offset[1]],
            [origin_x - reach + offset[0], origin_y + reach + offset[1]],
        ]

    artifact = {
        "shape": [shape[axis] + offset[axis] for axis in range(3)],
        "nodules": [
            {
                "id": 2,
                "slices": [{
                    "slice_index": ck + offset[2],
                    "polygons": [square(cx, cy)],
                }],
            },
            # A contour at id 3 must never be borrowed by the id-less
            # candidate at list position 3.
            {
                "id": 3,
                "slices": [{
                    "slice_index": ck + offset[2],
                    "polygons": [square(cx, cy + 12)],
                }],
            },
        ],
    }
    artifact_path = _CASE_ROOT / "ts_out" / "local-001" / "nodule_contours.json"
    artifact_path.write_text(json.dumps(artifact))
    _api_call(server, "PUT", "/api/cases/local-001/candidates", {
        "source": "lung_nodule_ct_detection",
        "candidates": [
            {
                "ras_mm": contour_world, "diameter_mm": 6.0, "score": 0.91,
                "id": 2,
            },
            {
                "ras_mm": box_world, "diameter_mm": 6.0, "score": 0.42,
                "id": 1,
            },
            # No id: this is the dashed-circle path, even though the contour
            # artifact has an entry with id 3 at the same list position.
            {"ras_mm": null_world, "diameter_mm": 6.0, "score": 0.10},
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
        result = page.evaluate(
            """(args) => {
              const ct = window.__p3_debug.ct();
              const canvas = document.querySelector('.ct-pane-axial canvas');
              const ctx = canvas.getContext('2d');
              const dpr = canvas.width / canvas.clientWidth;
              const orangeAt = (pt) => {
                if (!pt) return false;
                const x = Math.round(pt.x * dpr);
                const y = Math.round(pt.y * dpr);
                for (let dx = -2; dx <= 2; dx++) {
                  for (let dy = -2; dy <= 2; dy++) {
                    const p = ctx.getImageData(x + dx, y + dy, 1, 1).data;
                    if (p[0] > 180 && p[1] > 100 && p[1] < 190 && p[2] < 100) {
                      return true;
                    }
                  }
                }
                return false;
              };
              const aff = args.aff;
              const world = (i, j, k) => [0, 1, 2].map(a =>
                aff[a][0] * i + aff[a][1] * j + aff[a][2] * k + aff[a][3]);
              const {cx, cy, ck, reach} = args;
              const edges = [];
              const n = 8;
              for (let t = 0; t <= n; t++) {
                const f = t / n;
                const xs = cx - reach + 0.5 + f * (2 * reach);
                const ys = cy - reach + 0.5 + f * (2 * reach);
                edges.push([xs, cy - reach + 0.5, ck + 0.5]);
                edges.push([xs, cy + reach + 0.5, ck + 0.5]);
                edges.push([cx - reach + 0.5, ys, ck + 0.5]);
                edges.push([cx + reach + 0.5, ys, ck + 0.5]);
              }
              ct.setWorld(args.contourWorld);
              const onSlice = edges.map(([i, j, k]) => {
                const scr = ct.screenOfWorld('axial', world(i, j, k));
                return {scr, hit: orangeAt(scr)};
              });
              // Move one slice away, with the crosshair clear of the outline.
              ct.setWorld(world(2.5, 2.5, ck + 1.5));
              const offSlice = edges.map(([i, j, k]) => {
                const scr = ct.screenOfWorld('axial', world(i, j, k));
                return {scr, hit: orangeAt(scr)};
              });
              const ringAt = (world) => {
                ct.setWorld(world);
                const centre = ct.screenOfWorld('axial', world);
                const edge = ct.screenOfWorld('axial', [
                  world[0] + 3, world[1], world[2],
                ]);
                const radius = Math.hypot(edge.x - centre.x, edge.y - centre.y);
                let ring = 0;
                for (let a = 0; a < 72; a++) {
                  const ang = a * Math.PI / 36;
                  if (orangeAt({
                    x: centre.x + Math.cos(ang) * radius,
                    y: centre.y + Math.sin(ang) * radius,
                  })) ring += 1;
                }
                return ring;
              };
              return {
                onSliceHits: onSlice.filter(e => e.hit).length,
                offSliceHits: offSlice.filter(e => e.hit).length,
                ring: ringAt(args.boxWorld),
                nullRing: ringAt(args.nullWorld),
                pane: {w: canvas.clientWidth, h: canvas.clientHeight},
                screens: onSlice.map(e => e.scr),
              };
            }""",
            {
                "aff": affine, "cx": cx, "cy": cy, "ck": ck, "reach": reach,
                "contourWorld": contour_world, "boxWorld": box_world,
                "nullWorld": null_world,
            },
        )
        assert not errors, errors
        browser.close()

    assert result["onSliceHits"] >= 12, result
    assert result["offSliceHits"] == 0, result
    assert result["ring"] >= 6, result
    # The id-less candidate keeps its dashed circle; it must not draw contour 3.
    assert result["nullRing"] >= 6, result
    for scr in result["screens"]:
        assert 0 <= scr["x"] <= result["pane"]["w"], scr
        assert 0 <= scr["y"] <= result["pane"]["h"], scr
