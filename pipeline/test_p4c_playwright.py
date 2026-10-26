# Changes: Phase 4c review — CT-pane hover highlight + selected RouteTarget/gap
# screenshots; selection updates 3D and CT panes.
from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from pathlib import Path

import pytest
from termcolor import cprint

from test_p4c_route_target import _rt_graph

ROOT = Path(__file__).resolve().parent
SHOTS_REPO = ROOT / "out" / "p4c_shots"
PORT = 8769
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


def _build_pipeline(tmp: Path) -> Path:
    from test_app_server import _write_case_tree

    cprint(f"p4c playwright pipeline at {tmp}", "cyan")
    assert not str(tmp).startswith("/tmp")
    paths = _write_case_tree(tmp)
    paths["graph"].write_text(json.dumps(_rt_graph()))
    (paths["ts"] / "airway_bridges.json").write_text(json.dumps({
        "note": "interpolated, not CT-demonstrated airway",
        "radius_mm": 1.0,
        "bridges": [],
    }))
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
    case["review"] = []
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
    tmp = tmp_path_factory.mktemp("p4c_pipeline")
    return _build_pipeline(tmp)


@pytest.fixture(scope="module")
def server(tmp_pipeline):
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    fake_home = tmp_pipeline / "fake_home"
    fake_home.mkdir(exist_ok=True)
    env["HOME"] = str(fake_home)
    env["LUNGNAV_PIPELINE_ROOT"] = str(tmp_pipeline)
    cprint(f"p4c server on {PORT} root={tmp_pipeline}", "cyan")
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
    page._p4c_errors = []

    def on_msg(msg):
        if msg.type == "error":
            page._p4c_errors.append(msg.text)

    page.on("console", on_msg)


def _seed_through_route_start(pipeline: Path) -> None:
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
    case["lesion"] = {
        "ras_mm": [20.0, 0.0, -10.0],
        "confirmed_by": "Radiologist, A.B.",
        "at": "2026-01-01T00:00:01Z",
    }
    case["route_start"] = {
        "node": 0, "ras_mm": [0.0, 0.0, 0.0],
        "confirmed_by": "Radiologist, A.B.", "at": "2026-01-01T00:00:03Z",
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
        {
            "step": 5, "kind": "route_start_confirmed",
            "data": {"node": 0, "ras_mm": [0.0, 0.0, 0.0], "moved": False},
            "clinician": "Radiologist, A.B.", "at": "2026-01-01T00:00:03Z",
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


def test_p4c_route_target_cards(server, tmp_path):
    sync_playwright = _playwright_or_skip()
    shots_tmp = tmp_path / "p4c_shots"
    shots_tmp.mkdir(parents=True, exist_ok=True)
    pipeline = server["pipeline"]
    _seed_through_route_start(pipeline)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport=VIEWPORT)
        _attach_console(page)
        page.goto(
            f"{BASE}/?case=local-001&allow_review=1&debug=1",
            wait_until="networkidle",
        )
        page.wait_for_timeout(1200)

        page.click("#toggle-route-target")
        page.wait_for_selector("#route-target-cards .rt-card")
        cards = page.query_selector_all("#route-target-cards .rt-card")
        assert len(cards) >= 1
        # Cards expose lobe and airway as separate fields.
        card_text = cards[0].inner_text()
        assert "lobe" in card_text.lower()
        assert "airway" in card_text.lower()
        page.screenshot(path=str(shots_tmp / "candidates.png"), full_page=True)

        cards[0].hover()
        page.wait_for_timeout(400)
        page.screenshot(path=str(shots_tmp / "hover.png"), full_page=True)

        frame = _iframe_frame(page)
        # Switch renderer to CT panes (variant D) for hover + selection shots.
        switched = frame.evaluate("""async () => {
          if (typeof window.setVariant === "function") window.setVariant(3);
          const api = (typeof window.ensureCtPanes === "function")
            ? await window.ensureCtPanes() : null;
          return !!(api && typeof api.setHighlightRoute === "function");
        }""")
        assert switched, "CT panes missing setHighlightRoute"
        page.wait_for_timeout(800)
        page.query_selector_all("#route-target-cards .rt-card")[0].hover()
        page.wait_for_timeout(500)
        page.screenshot(path=str(shots_tmp / "hover_ct_panes.png"), full_page=True)

        page.query_selector_all("#route-target-cards .rt-card")[0].click()
        page.wait_for_timeout(1800)
        page.screenshot(path=str(shots_tmp / "selected.png"), full_page=True)

        frame = _iframe_frame(page)
        has_endpoint = frame.evaluate("""() => {
          const A = window.AIRWAY;
          return !!(A && A.endpoint && A.route_target && !A.route_target.stale);
        }""")
        assert has_endpoint, "expected route_target + endpoint in payload after select"

        # CT panes after selection: RouteTarget + lesion (gap line).
        frame.evaluate("""async () => {
          if (typeof window.setVariant === "function") window.setVariant(3);
          if (typeof window.ensureCtPanes === "function") await window.ensureCtPanes();
        }""")
        page.wait_for_timeout(1000)
        page.screenshot(path=str(shots_tmp / "selected_ct_panes.png"), full_page=True)
        ct_has_target = frame.evaluate("""() => {
          const A = window.AIRWAY;
          return !!(A && A.endpoint && A.route_target && A.lesion);
        }""")
        assert ct_has_target, "expected RouteTarget + lesion for CT gap line"

        errs = getattr(page, "_p4c_errors", [])
        assert not errs, f"console errors: {errs}"
        browser.close()

    SHOTS_REPO.mkdir(parents=True, exist_ok=True)
    for src in shots_tmp.glob("*.png"):
        shutil.copy2(src, SHOTS_REPO / src.name)
    cprint(f"p4c screenshots → {SHOTS_REPO}", "green")
