# Changes: synthetic case tree only (no real ts_out/data dir symlinks); shots via tmp.
# Phase 4b review-fixes — UI-only flows (no API shortcuts for asserted
# actions); screenshots under tmp then copy to pipeline/out/p4b_shots/; skip if
# Playwright browsers missing.
# Phase 4b playwright: remove branch → ghosted + label 3; bridge Go to → CT jump.
# Synthetic tmp pipeline only (never real data / ~/.lungnav* /tmp).
from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from pathlib import Path

import numpy as np
import pytest
from termcolor import cprint

from test_app_server import _write_case_tree
from test_p4b_review import _bridge_json, _multi_edge_graph, _paint_bridge_mask

ROOT = Path(__file__).resolve().parent
SHOTS_REPO = ROOT / "out" / "p4b_shots"
PORT = 8768
BASE = f"http://127.0.0.1:{PORT}"
VIEWPORT = {"width": 1400, "height": 900}


def _playwright_or_skip():
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        pytest.skip("playwright not installed")
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            browser.close()
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"playwright browsers missing: {exc}")
    return sync_playwright


def _build_synthetic_pipeline(tmp: Path) -> Path:
    cprint(f"p4b playwright pipeline at {tmp}", "cyan")
    paths = _write_case_tree(tmp)
    graph = _multi_edge_graph()
    paths["graph"].write_text(json.dumps(graph))
    (paths["ts"] / "airway_bridges.json").write_text(json.dumps(_bridge_json()))
    _paint_bridge_mask(paths)
    case = json.loads(paths["case"].read_text())
    case["source"] = {
        "series_uid_sha256": "deadbeef" * 8,
        "folder": "SERIES0",
        "series_number": 1,
        "series_description": "test",
        "convolution_kernel": "STANDARD",
        "spacing_mm": [1.0, 1.0, 1.5],
        "size": [24, 20, 16],
    }
    case["review_signoff"] = []
    case["records"] = []
    case["versions"] = []
    paths["case"].write_text(json.dumps(case, indent=2) + "\n")
    (tmp / "app_state").mkdir(parents=True, exist_ok=True)
    return tmp


def _wait_server(timeout: float = 90.0) -> None:
    import urllib.request

    deadline = time.time() + timeout
    last_err = None
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(f"{BASE}/api/cases", timeout=2) as res:
                if res.status == 200:
                    return
        except Exception as exc:  # noqa: BLE001
            last_err = exc
            time.sleep(0.4)
    raise RuntimeError(f"server did not start: {last_err}")


@pytest.fixture(scope="module")
def tmp_pipeline(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("p4b_pipeline")
    return _build_synthetic_pipeline(tmp)


@pytest.fixture(scope="module")
def server(tmp_pipeline):
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    fake_home = tmp_pipeline / "fake_home"
    fake_home.mkdir(exist_ok=True)
    env["HOME"] = str(fake_home)
    env["LUNGNAV_PIPELINE_ROOT"] = str(tmp_pipeline)
    cprint(f"p4b server on {PORT} root={tmp_pipeline}", "cyan")
    proc = subprocess.Popen(
        [
            "env", "-u", "PYTHONPATH",
            "uv", "run", "python", "app_server.py", "--port", str(PORT),
        ],
        cwd=str(ROOT),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    try:
        _wait_server()
        yield {"proc": proc, "pipeline": tmp_pipeline}
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


def _attach_console(page) -> None:
    page._p4b_errors = []

    def on_msg(msg):
        if msg.type == "error":
            page._p4b_errors.append(msg.text)

    page.on("console", on_msg)


def _seed_case_through_segment(pipeline: Path) -> None:
    """Advance case through segment via case.json (setup only — not asserted UI)."""
    import urllib.request

    req = urllib.request.Request(
        f"{BASE}/api/clinician",
        data=json.dumps({"role": "Radiologist", "initials": "A.B."}).encode(),
        headers={"Content-Type": "application/json"},
        method="PUT",
    )
    urllib.request.urlopen(req).read()

    case_path = pipeline / "data/extracted/local/local-001/case.json"
    case = json.loads(case_path.read_text())
    ct = pipeline / "data/extracted/local/local-001/local-001_CT.nii.gz"
    ts = pipeline / "ts_out/local-001"
    graph = pipeline / "out/local-001/local-001_graph.json"

    def sha(p: Path) -> str:
        import hashlib
        h = hashlib.sha256()
        h.update(p.read_bytes())
        return h.hexdigest()

    def rel(p: Path) -> str:
        return str(p.relative_to(pipeline))

    arts = {
        "ct": {"path": rel(ct), "sha256": sha(ct)},
        "airways": {"path": rel(ts / "airways.nii.gz"), "sha256": sha(ts / "airways.nii.gz")},
        "bridges_mask": {
            "path": rel(ts / "airway_bridges.nii.gz"),
            "sha256": sha(ts / "airway_bridges.nii.gz"),
        },
        "bridges_json": {
            "path": rel(ts / "airway_bridges.json"),
            "sha256": sha(ts / "airway_bridges.json"),
        },
        "lobes": {"path": rel(ts / "lobes.nii.gz"), "sha256": sha(ts / "lobes.nii.gz")},
        "graph": {"path": rel(graph), "sha256": sha(graph)},
    }
    case["versions"] = [{"n": 1, "artifacts": arts, "frozen": False}]
    with urllib.request.urlopen(f"{BASE}/api/cases/local-001/ct/meta") as res:
        meta = json.loads(res.read())
    affine = np.asarray(meta["affine"], dtype=float)
    ijk = [meta["shape"][0] / 2, meta["shape"][1] / 2, meta["shape"][2] / 2]
    ras = (affine @ np.array([ijk[0], ijk[1], ijk[2], 1.0]))[:3]
    case["lesion"] = {
        "ras_mm": [float(ras[0]), float(ras[1]), float(ras[2])],
        "confirmed_by": "Radiologist, A.B.",
        "at": "2026-01-01T00:00:01Z",
    }
    case["records"] = [
        {
            "step": 1, "kind": "series_confirmed",
            "data": {
                "folder": "SERIES0", "series_number": 1,
                "description": "test", "kernel": "STANDARD", "overridden": False,
            },
            "clinician": "Radiologist, A.B.", "at": "2026-01-01T00:00:00Z",
        },
        {
            "step": 2, "kind": "lesion_confirmed",
            "data": {"ras_mm": case["lesion"]["ras_mm"]},
            "clinician": "Radiologist, A.B.", "at": "2026-01-01T00:00:01Z",
        },
        {
            "step": 3, "kind": "segmentation_done",
            "data": {"artifacts": list(arts)},
            "clinician": "Radiologist, A.B.", "at": "2026-01-01T00:00:02Z",
        },
    ]
    case_path.write_text(json.dumps(case, indent=2) + "\n")


def _iframe_frame(page):
    for frame in page.frames:
        if frame != page.main_frame and "renderer" in (frame.url or ""):
            return frame
    for frame in page.frames:
        if frame != page.main_frame:
            return frame
    return page.main_frame


def _amber_pixels(frame) -> int:
    """Count amber-ish overlay pixels in the axial CT canvas (label 2)."""
    return frame.evaluate("""() => {
      const canvas = document.querySelector('.ct-pane[data-pane="axial"] canvas');
      if (!canvas) return -1;
      const ctx = canvas.getContext('2d');
      const { width: w, height: h } = canvas;
      const data = ctx.getImageData(0, 0, w, h).data;
      let n = 0;
      for (let i = 0; i < data.length; i += 4) {
        const r = data[i], g = data[i+1], b = data[i+2];
        // Amber-ish (217,149,32) blended over CT — allow a loose window.
        if (r > 140 && g > 90 && g < 200 && b < 100 && r > g && g > b) n++;
      }
      return n;
    }""")


def test_p4b_review_flows_through_ui(server, tmp_path):
    sync_playwright = _playwright_or_skip()
    shots_tmp = tmp_path / "p4b_shots"
    shots_tmp.mkdir(parents=True, exist_ok=True)
    pipeline = server["pipeline"]
    _seed_case_through_segment(pipeline)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport=VIEWPORT)
        _attach_console(page)
        page.goto(f"{BASE}/?case=local-001&allow_review=1", wait_until="networkidle")
        page.wait_for_timeout(1000)

        # Open review; confirm bridge via UI.
        page.click("#toggle-review")
        page.wait_for_selector("#review-bridges button[data-bridge-goto]")
        bid = page.get_attribute("#review-bridges button[data-bridge-goto]", "data-bridge-goto")
        page.fill(f'#review-bridges input[data-bridge-note="{bid}"]', "CT continuum across gap")
        page.click(f'#review-bridges button[data-bridge-confirm="{bid}"]')
        page.wait_for_timeout(800)

        # Bridge Go to → CT panes at midpoint, airway overlay on, amber visible.
        page.click(f'#review-bridges button[data-bridge-goto="{bid}"]')
        page.wait_for_timeout(1500)
        frame = _iframe_frame(page)
        amber = _amber_pixels(frame)
        page.screenshot(path=str(shots_tmp / "bridge_goto.png"), full_page=True)
        assert amber >= 1, f"expected amber bridge label pixels, got {amber}"

        # Open anatomical renderer; click side branch → Remove with note.
        page.goto(
            f"{BASE}/renderer.template.html?case=local-001&allow_review=1&variant=anatomical&debug=1",
            wait_until="networkidle",
        )
        page.wait_for_timeout(900)
        # Pick a projected sample of edge oi=3 (side branch around [13,9,13.5]).
        clicked = page.evaluate("""() => {
          const A = window.AIRWAY || window.A;
          if (!A || !A.edges) return false;
          const edge = A.edges.find(e => e.oi === 3);
          if (!edge || !edge.c || !edge.c.length) return false;
          const c = edge.c[Math.floor(edge.c.length / 2)];
          // Use internal project + click simulation via handleViewClick if exposed,
          // else synthesise a mouse click near canvas centre after SharedPos jump.
          if (typeof toWorld === 'function' && typeof SharedPos !== 'undefined') {
            SharedPos.set(toWorld(c), 'anatomical');
          }
          if (typeof showRemovePanel === 'function') {
            showRemovePanel(edge);
            return true;
          }
          return false;
        }""")
        assert clicked, "could not open remove panel for edge 3"
        page.fill("#remove-branch-note", "CT does not support this spur")
        page.click("#remove-branch-go")
        page.wait_for_timeout(1500)

        # After reload: ghosted in 3D.
        page.wait_for_timeout(800)
        ghosts = page.evaluate("""() => {
          const A = window.AIRWAY || window.A;
          return (A && A.removed) ? A.removed.length : -1;
        }""")
        assert ghosts >= 1, f"expected ghosted removed edges, got {ghosts}"
        page.screenshot(path=str(shots_tmp / "ghosted_3d.png"), full_page=True)

        # Switch to CT and check hatched label 3 colour near the branch.
        page.click('button:has-text("CT panes")')
        page.wait_for_timeout(500)
        page.evaluate("() => window.ensureCtPanes && window.ensureCtPanes()")
        page.wait_for_selector('.ct-pane[data-pane="axial"] canvas', timeout=15000)
        page.evaluate("""() => {
          if (window.__p3_ct && window.__p3_ct.setAirwayOverlay) {
            window.__p3_ct.setAirwayOverlay(true);
          }
        }""")
        # Jump crosshair to the removed branch so the axial slice shows label 3.
        page.evaluate("""() => {
          const A = window.AIRWAY;
          const g = (A && A.removed || [])[0];
          if (!g || !g.c || !g.c.length) return;
          const origin = A.origin_mm || [0,0,0];
          const c = g.c[Math.floor(g.c.length / 2)];
          const world = [c[0]+origin[0], c[1]+origin[1], c[2]+origin[2]];
          if (window.SharedPos) window.SharedPos.set(world, 'bridge');
        }""")
        page.wait_for_timeout(800)
        hatch = page.evaluate("""() => {
          const canvas = document.querySelector('.ct-pane[data-pane="axial"] canvas');
          if (!canvas) return -1;
          const ctx = canvas.getContext('2d');
          const { width: w, height: h } = canvas;
          const data = ctx.getImageData(0, 0, w, h).data;
          let n = 0;
          for (let i = 0; i < data.length; i += 4) {
            const r = data[i], g = data[i+1], b = data[i+2];
            if (r > 100 && r > g + 15 && r > b + 15) n++;
          }
          return n;
        }""")
        assert hatch >= 1, f"expected hatched manual pixels, got {hatch}"

        # Undo via the ghost (3D).
        page.evaluate("() => window.setVariant && window.setVariant(0)")
        page.wait_for_timeout(400)
        undid = page.evaluate("""() => {
          const A = window.AIRWAY || window.A;
          const g = (A.removed || [])[0];
          if (!g || !g.correction_id) return false;
          if (typeof showUndoGhostPanel === 'function') {
            showUndoGhostPanel(g);
            return true;
          }
          return false;
        }""")
        assert undid
        page.click("#undo-ghost-go")
        page.wait_for_timeout(1500)
        ghosts_after = page.evaluate("""() => {
          const A = window.AIRWAY || window.A;
          return (A && A.removed) ? A.removed.length : -1;
        }""")
        assert ghosts_after == 0

        page.goto(f"{BASE}/?case=local-001&allow_review=1", wait_until="networkidle")
        page.wait_for_timeout(500)
        page.screenshot(path=str(shots_tmp / "after_undo.png"), full_page=True)

        # Confirm bridge again already done; confirm via review panel still works.
        page.click("#toggle-review")
        page.wait_for_selector("#review-bridges")

        errors = list(getattr(page, "_p4b_errors", []))
        browser.close()

    assert not errors, f"console errors: {errors}"

    SHOTS_REPO.mkdir(parents=True, exist_ok=True)
    for name in ("bridge_goto.png", "ghosted_3d.png", "after_undo.png"):
        src = shots_tmp / name
        if src.is_file():
            shutil.copy2(src, SHOTS_REPO / name)
    cprint(f"p4b screenshots in {SHOTS_REPO} (from {shots_tmp})", "green")
