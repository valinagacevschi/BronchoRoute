# Changes: RouteTarget is auto-picked; no cards, point picking, or replace dialog.
# Changes: stamp the synthetic Case with the current pipeline version.
# Changes: RouteTarget gap, generation, route length and place in the rail readout.
"""Place a Lesion, then check the auto-picked RouteTarget in the rail and the CT."""

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


def _seg(start, end, step=1.0):
    p = np.asarray(start, dtype=float)
    q = np.asarray(end, dtype=float)
    n = max(1, int(np.ceil(np.linalg.norm(q - p) / step)))
    out = []
    for i in range(n + 1):
        t = i / n
        point = (1.0 - t) * p + t * q
        out.append([round(float(v), 3) for v in point])
    return out


def _seed(root: Path) -> None:
    paths = _write_case_tree(
        root,
        shape=(48, 40, 64),
        affine=np.diag([1.0, 1.0, 1.0, 1.0]),
        mask_box=(slice(2, 46), slice(2, 38), slice(2, 62)),
    )
    nodes = [
        {"id": 0, "position_mm": [24.5, 20.5, 56.5], "radius_mm": 6.0},
        {"id": 1, "position_mm": [24.5, 20.5, 44.5], "radius_mm": 5.0},
        {"id": 2, "position_mm": [24.5, 20.5, 32.5], "radius_mm": 4.0},
        {"id": 3, "position_mm": [24.5, 20.5, 20.5], "radius_mm": 2.5},
        {"id": 4, "position_mm": [40.5, 20.5, 28.5], "radius_mm": 2.0},
        {"id": 5, "position_mm": [8.5, 28.5, 26.5], "radius_mm": 2.0},
    ]
    edges = [
        {
            "from": 0, "to": 1, "generation": 0, "radius_mm": 6.0, "length_mm": 12.0,
            "lobe": None, "airway_name": "trachea", "provenance": "ct",
            "interpolated_mm": 2.0, "interpolated_spans_mm": [[0.0, 2.0]],
            "downstream_of_interpolated": False,
            "centreline_mm": _seg([24.5, 20.5, 56.5], [24.5, 20.5, 44.5]),
        },
        {
            "from": 1, "to": 2, "generation": 1, "radius_mm": 4.0, "length_mm": 12.0,
            "lobe": "right lower lobe", "airway_name": "intermediate", "provenance": "ct",
            "interpolated_mm": 0.0, "interpolated_spans_mm": [],
            "downstream_of_interpolated": False,
            "centreline_mm": _seg([24.5, 20.5, 44.5], [24.5, 20.5, 32.5]),
        },
        {
            "from": 2, "to": 3, "generation": 2, "radius_mm": 2.5, "length_mm": 12.0,
            "lobe": "right lower lobe", "airway_name": "right basal", "provenance": "ct",
            "interpolated_mm": 0.0, "interpolated_spans_mm": [],
            "downstream_of_interpolated": False,
            "centreline_mm": _seg([24.5, 20.5, 32.5], [24.5, 20.5, 20.5]),
        },
        {
            "from": 2, "to": 4, "generation": 3, "radius_mm": 2.0, "length_mm": 8.0,
            "lobe": "right upper lobe", "airway_name": "apical", "provenance": "ct",
            "interpolated_mm": 0.0, "interpolated_spans_mm": [],
            "downstream_of_interpolated": False,
            "centreline_mm": _seg([40.5, 20.5, 36.5], [40.5, 20.5, 28.5]),
        },
        {
            "from": 2, "to": 5, "generation": 1, "radius_mm": 2.0, "length_mm": 8.0,
            "lobe": "left lower lobe", "airway_name": "left basal", "provenance": "ct",
            "interpolated_mm": 0.0, "interpolated_spans_mm": [],
            "downstream_of_interpolated": False,
            "centreline_mm": _seg([8.5, 28.5, 34.5], [8.5, 28.5, 26.5]),
        },
    ]
    graph = {
        "case": "local-001",
        "root_node_id": 0,
        "coordinate_system": "RAS",
        "nodes": nodes,
        "edges": edges,
        "review": [],
    }
    paths["graph"].write_text(json.dumps(graph))
    (paths["ts"] / "airway_bridges.json").write_text(json.dumps({
        "note": "interpolated, not CT-demonstrated airway",
        "radius_mm": 1.0,
        "bridges": [],
    }))
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
    root = tmp_path_factory.mktemp("route34")
    _seed(root)
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    base = f"http://127.0.0.1:{port}"
    home = root / "fake_home"
    home.mkdir()
    env = {**os.environ, "HOME": str(home), "LUNGNAV_PIPELINE_ROOT": str(root)}
    env.pop("PYTHONPATH", None)
    cprint(f"route-target server on {port}", "cyan")
    proc = subprocess.Popen(
        ["uv", "run", "python", "app_server.py", "--port", str(port)],
        cwd=Path(__file__).resolve().parent,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    try:
        for _ in range(200):
            assert proc.poll() is None, "route-target server exited before readiness"
            try:
                with urllib.request.urlopen(f"{base}/api/cases", timeout=1):
                    break
            except OSError:
                time.sleep(0.1)
        else:
            err = proc.stderr.read().decode("utf-8", "replace") if proc.stderr else ""
            pytest.fail(f"route-target server did not start\n{err}")
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


def _shot(page, shots: Path, name: str) -> None:
    path = shots / name
    page.screenshot(path=str(path), full_page=True)
    cprint(f"screenshot {path}", "cyan")


def _assert_no_start_ui(page) -> None:
    assert page.locator("#ct-confirm-route-start").count() == 0
    assert page.locator("#ct-route-start-box").count() == 0
    assert page.locator("#ct-move-route-start").count() == 0
    text = page.locator("body").inner_text()
    assert "Confirm Start" not in text
    assert "Move RouteStart" not in text


def _click_world(page, world, pane="coronal") -> None:
    scr = page.evaluate(
        """(args) => {
          const ct = window.__p3_debug.ct();
          ct.setWorld(args.world);
          return ct.screenOfWorld(args.pane, args.world);
        }""",
        {"world": world, "pane": pane},
    )
    box = page.locator(f".ct-pane-{pane} canvas").bounding_box()
    assert scr and box, (scr, box)
    page.mouse.click(box["x"] + scr["x"], box["y"] + scr["y"])


def test_route_target_is_chosen_automatically(server):
    sync_playwright = _playwright_or_skip()
    shots = Path(os.environ.get("TMPDIR", "/tmp")) / "bronchoroute-34"
    shots.mkdir(parents=True, exist_ok=True)
    cprint(f"screenshots in {shots}", "cyan")
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        page.on("dialog", lambda dialog: dialog.accept())
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        def _console(message):
            if message.type != "error":
                return
            errors.append(message.text)

        page.on("console", _console)
        _open(page, server)
        page.wait_for_function(
            """() => {
              const ct = window.__p3_debug && window.__p3_debug.ct && window.__p3_debug.ct();
              return !!(ct && ct.isLoaded && ct.isLoaded());
            }""",
            timeout=180_000,
        )
        _assert_no_start_ui(page)
        assert page.locator("#route-target-panel").is_hidden()
        order = page.evaluate(
            """() => {
              const review = document.getElementById("review-warnings");
              const panel = document.getElementById("route-target-panel");
              return !!(review.compareDocumentPosition(panel)
                & Node.DOCUMENT_POSITION_FOLLOWING);
            }"""
        )
        assert order

        page.locator("#ct-mark-lesion").click()
        placed = page.evaluate(
            """() => {
              const ct = window.__p3_debug.ct();
              const world = [24.5, 20.5, 16.5];
              ct.setWorld(world);
              return { world, scr: ct.screenOfWorld("coronal", world) };
            }"""
        )
        box = page.locator(".ct-pane-coronal canvas").bounding_box()
        page.mouse.click(box["x"] + placed["scr"]["x"], box["y"] + placed["scr"]["y"])
        page.locator("#ct-confirm-lesion").click()
        page.wait_for_function(
            """() => {
              const rt = window.__p3_debug.airway().route_target;
              return !!(rt && !rt.stale && rt.rank === 1 && rt.ras_mm);
            }""",
            timeout=30_000,
        )
        assert page.locator("#route-target-panel").is_visible()
        # No route selection UI remains.
        assert page.locator(".route-target-card").count() == 0
        assert page.locator("#route-target-pick").count() == 0

        with urllib.request.urlopen(
            f"{server}/api/cases/local-001/route-candidates", timeout=10,
        ) as res:
            cands = json.load(res)["candidates"]
        top = cands[0]
        assert top["target_gap_mm"] == min(c["target_gap_mm"] for c in cands)
        chosen = page.evaluate("() => window.__p3_debug.airway().route_target")
        assert chosen["edge_id"] == top["edge_id"]
        assert chosen["sample_index"] == top["sample_index"]
        assert chosen["target_gap_mm"] == pytest.approx(top["target_gap_mm"], abs=0.01)

        readout = page.evaluate(
            """() => ({
              gap: document.getElementById("route-target-gap").textContent,
              generation: document.getElementById("route-target-generation").textContent,
              length: document.getElementById("route-target-length").textContent,
              place: document.getElementById("route-target-place").textContent,
            })"""
        )
        assert readout["gap"] == f"{top['target_gap_mm']:.2f} mm"
        assert readout["generation"] == str(top["generation"])
        assert readout["length"] == f"{top['route_length_mm']:.2f} mm"
        place = str(top["lobe"] or "")
        if top.get("airway_name"):
            place = f"{place} / {top['airway_name']}" if place else str(top["airway_name"])
        assert readout["place"] == place
        _shot(page, shots, "auto-target.png")

        draws = page.evaluate("() => window.__p3_debug.ct().routeTargetDraws()")
        for pane in ("axial", "coronal", "sagittal"):
            assert draws[pane]["marker"] is True, draws
        assert draws["coronal"]["gapDashed"] is True, draws

        page.keyboard.press("2")
        page.wait_for_function(
            """() => {
              const state = window.__p2_debug.routeTargetState();
              const lesion = window.__p2_debug.lesionViews().anatomical;
              return state.visible
                && state.anatomicalTarget && state.anatomicalTarget.label === "RouteTarget"
                && state.anatomicalGap && state.anatomicalGap.dashed === true
                && lesion && lesion.label === "Lesion";
            }""",
            timeout=10_000,
        )
        assert page.locator("#route-target-panel").is_visible()
        _shot(page, shots, "anatomical.png")

        page.keyboard.press("3")
        page.wait_for_function(
            """() => {
              const state = window.__p2_debug.routeTargetState();
              const lesion = state.topologyLesion;
              return state.visible
                && !state.topologyTarget
                && lesion && lesion.label === "Nodule"
                && lesion.color === "#ffd400"
                && lesion.onRouteTarget === true;
            }""",
            timeout=10_000,
        )
        anchor = page.evaluate("() => window.__p2_debug.routeTargetLesionAnchor()")
        assert anchor and anchor["onRouteTarget"] is True
        assert anchor["distMm"] == pytest.approx(top["target_gap_mm"], abs=0.05)
        _shot(page, shots, "topology.png")

        _assert_no_start_ui(page)
        assert not errors, errors
        browser.close()
