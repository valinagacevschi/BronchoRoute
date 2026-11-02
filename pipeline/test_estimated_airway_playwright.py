# Changes: real local-001 screenshots; the layer toggle stays inside the viewport.
# Changes: anatomical estimated-airway layer toggles on and off; endoluminal omits it.
"""Browser check for the estimated airway layer."""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import pytest
from termcolor import cprint

from test_app_server import _stamp_pipeline_version, _write_case_tree
from test_viewer_v2_playwright import _playwright_or_skip


def _seed(root: Path) -> None:
    paths = _write_case_tree(root)
    tip = [0.0, 0.0, 10.0]
    points = []
    distances = []
    for index in range(31):
        distance = index * 0.5
        points.append([0.0, 0.0, round(10.0 - distance, 3)])
        distances.append(round(distance, 3))
    graph = {
        "case": "local-001",
        "root_node_id": 0,
        "coordinate_system": "patient_physical_mm",
        "nodes": [
            {"id": 0, "position_mm": [0.0, 0.0, 40.0], "radius_mm": 4.0},
            {"id": 1, "position_mm": tip, "radius_mm": 1.5},
        ],
        "edges": [{
            "from": 0,
            "to": 1,
            "generation": 1,
            "radius_mm": 2.0,
            "length_mm": 30.0,
            "lobe": 5,
            "provenance": "ct",
            "centreline_mm": [[0.0, 0.0, float(z)] for z in range(40, 9, -1)],
        }],
        "estimated": [{
            "id": "est-0",
            "from_edge": 0,
            "provenance": "estimated",
            "points_mm": points,
            "distance_mm": distances,
            "length_mm": 15.0,
            "stop_reason": "max_len",
        }],
        "estimated_summary": {
            "terminals": 1, "estimated": 1, "total_mm": 15.0, "max_mm": 15.0,
        },
        "review": [],
    }
    paths["graph"].write_text(json.dumps(graph))
    _stamp_pipeline_version(paths["case"])


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    root = tmp_path_factory.mktemp("estimated40")
    _seed(root)
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    base = f"http://127.0.0.1:{port}"
    home = root / "fake_home"
    home.mkdir()
    env = {**os.environ, "HOME": str(home), "LUNGNAV_PIPELINE_ROOT": str(root)}
    env.pop("PYTHONPATH", None)
    cprint(f"estimated-airway server on {port}", "cyan")
    proc = subprocess.Popen(
        ["uv", "run", "python", "app_server.py", "--port", str(port)],
        cwd=Path(__file__).resolve().parent,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    try:
        ready = False
        for _ in range(200):
            assert proc.poll() is None, "estimated-airway server exited before readiness"
            try:
                with urllib.request.urlopen(f"{base}/api/cases", timeout=1):
                    ready = True
                    break
            except OSError:
                time.sleep(0.1)
        if not ready:
            err = proc.stderr.read().decode("utf-8", "replace") if proc.stderr else ""
            pytest.fail(f"estimated-airway server did not start\n{err}")
        body = json.dumps({"role": "Radiologist", "initials": "A.B."}).encode()
        request = urllib.request.Request(
            f"{base}/api/clinician",
            data=body,
            headers={"Content-Type": "application/json"},
            method="PUT",
        )
        with urllib.request.urlopen(request, timeout=5) as response:
            assert response.status == 200
        yield base
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()


def _shots() -> Path:
    path = Path(os.environ.get("TMPDIR", "/tmp")) / "bronchoroute-40"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _assert_toggle_in_viewport(page) -> None:
    """The layer control is on screen at 1440×900 and clear of the footer."""
    viewport = page.viewport_size
    footer = page.locator(".research-footer").bounding_box()
    assert viewport == {"width": 1440, "height": 900}
    assert footer is not None
    for selector in ("#estimated-airway-toggle", "#g-layers"):
        box = page.locator(selector).bounding_box()
        assert box is not None and box["width"] > 0 and box["height"] > 0, selector
        assert box["x"] >= -0.5 and box["y"] >= -0.5, (selector, box)
        assert box["x"] + box["width"] <= viewport["width"] + 0.5, (selector, box)
        assert box["y"] + box["height"] <= viewport["height"] + 0.5, (selector, box)
        clear = (
            box["x"] + box["width"] <= footer["x"] + 0.5
            or footer["x"] + footer["width"] <= box["x"] + 0.5
            or box["y"] + box["height"] <= footer["y"] + 0.5
            or footer["y"] + footer["height"] <= box["y"] + 0.5
        )
        assert clear, (selector, box, footer)


def test_anatomical_estimated_layer_toggles(server):
    sync_playwright = _playwright_or_skip()
    shots = _shots()
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        page.goto(
            f"{server}/renderer.template.html?case=local-001&allow_review=1"
            "&debug=1&variant=anatomical",
            wait_until="domcontentloaded",
            timeout=120_000,
        )
        try:
            page.wait_for_function(
                """() => {
                  const hook = window.__p2_debug && window.__p2_debug.estimatedLayer;
                  if (!hook) return false;
                  const layer = hook();
                  return layer.visible && layer.segments > 0 && layer.drawnPoints > 0;
                }""",
                timeout=30_000,
            )
        except Exception:
            cprint(page.locator("body").inner_text()[:800], "red")
            raise
        page.locator("#estimated-airway-toggle").wait_for(state="visible")
        assert "Estimated airway" in page.locator("#g-layers").inner_text()
        _assert_toggle_in_viewport(page)
        on_shot = shots / "estimated-on.png"
        page.screenshot(path=str(on_shot))
        cprint(f"screenshot {on_shot}", "cyan")

        page.locator("#estimated-airway-toggle").click()
        page.wait_for_function(
            """() => {
              const layer = window.__p2_debug.estimatedLayer();
              return layer.visible === false && layer.drawnPoints === 0 && layer.segments > 0;
            }""",
            timeout=10_000,
        )
        off_shot = shots / "estimated-off.png"
        page.screenshot(path=str(off_shot))
        cprint(f"screenshot {off_shot}", "cyan")

        page.locator("#estimated-airway-toggle").click()
        page.wait_for_function(
            """() => {
              const layer = window.__p2_debug.estimatedLayer();
              return layer.visible && layer.drawnPoints > 0;
            }""",
            timeout=10_000,
        )
        endoluminal = page.evaluate(
            """async () => {
              window.__p2_debug.setVariant(1);
              await new Promise(resolve => requestAnimationFrame(
                () => requestAnimationFrame(resolve)));
              const airway = window.AIRWAY;
              const fly = airway.fly.p || [];
              const estimated = (airway.estimated || []).flatMap(segment =>
                (segment.c || []).filter((_, index) => (segment.d || [])[index] > 1));
              const near = (point, other) => {
                const dx = point[0] - other[0];
                const dy = point[1] - other[1];
                const dz = point[2] - other[2];
                return Math.sqrt(dx * dx + dy * dy + dz * dz) < 0.6;
              };
              return {
                edgeEstimated: airway.edges.some(edge => edge.prov === "estimated"),
                flyEstimated: (airway.fly.prov || []).some(prov => prov === "estimated"),
                flyHitsEstimated: estimated.some(point => fly.some(sample => near(point, sample))),
                variant: window.__p2_debug.getVariant(),
              };
            }"""
        )
        assert endoluminal["variant"] == "endoluminal"
        assert endoluminal["edgeEstimated"] is False
        assert endoluminal["flyEstimated"] is False
        assert endoluminal["flyHitsEstimated"] is False
        browser.close()


def _rebuild_local_001(root: Path) -> int:
    pipe = Path(__file__).resolve().parent
    masks = {
        "airways": pipe / "ts_out/local-001/airways.nii.gz",
        "lobes": pipe / "ts_out/local-001/lobes.nii.gz",
        "bridges": pipe / "ts_out/local-001/airway_bridges.nii.gz",
        "arteries": pipe / "ts_out/local-001/airways_source/lung_arteries.nii.gz",
        "case": pipe / "data/extracted/local/local-001/case.json",
    }
    if any(not path.is_file() for path in masks.values()):
        pytest.skip("local-001 masks are not in this worktree")
    from conftest import symlink_input_file

    case_dir = root / "data/extracted/local/local-001"
    case_dir.mkdir(parents=True)
    case_file = case_dir / "case.json"
    shutil.copy2(masks["case"], case_file)
    _stamp_pipeline_version(case_file)
    ct = pipe / "data/extracted/local/local-001/local-001_CT.nii.gz"
    if ct.is_file():
        symlink_input_file(ct, case_dir / ct.name)
    ts = root / "ts_out/local-001"
    symlink_input_file(masks["airways"], ts / "airways.nii.gz")
    symlink_input_file(masks["lobes"], ts / "lobes.nii.gz")
    symlink_input_file(masks["bridges"], ts / "airway_bridges.nii.gz")
    symlink_input_file(masks["arteries"], ts / "airways_source/lung_arteries.nii.gz")
    out = root / "out" / "local-001"
    env = {
        **os.environ,
        "LUNGNAV_DATA_ROOT": str(root),
        "LUNGNAV_PIPELINE_ROOT": str(root),
    }
    env.pop("PYTHONPATH", None)
    cprint("rebuilding local-001 with arteries", "cyan")
    result = subprocess.run(
        [
            sys.executable, str(pipe / "build_graph.py"), str(case_dir),
            "--airways", str(ts / "airways.nii.gz"),
            "--lobes", str(ts / "lobes.nii.gz"),
            "--bridges", str(ts / "airway_bridges.nii.gz"),
            "--arteries", str(ts / "airways_source/lung_arteries.nii.gz"),
            "-o", str(out),
        ],
        capture_output=True, text=True, env=env, timeout=180,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    graph = json.loads((out / "local-001_graph.json").read_text())
    count = len(graph.get("estimated") or [])
    assert count > 0
    assert count == graph["estimated_summary"]["estimated"]
    cprint(f"local-001 estimated segments {count}", "cyan")
    return count


def _launch(root: Path):
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    base = f"http://127.0.0.1:{port}"
    home = root / "fake_home"
    home.mkdir(exist_ok=True)
    env = {**os.environ, "HOME": str(home), "LUNGNAV_PIPELINE_ROOT": str(root)}
    env.pop("PYTHONPATH", None)
    proc = subprocess.Popen(
        ["uv", "run", "python", "app_server.py", "--port", str(port)],
        cwd=Path(__file__).resolve().parent,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    return proc, base


def _wait_ready(proc, base: str) -> None:
    for _ in range(200):
        assert proc.poll() is None, "estimated-airway server exited before readiness"
        try:
            with urllib.request.urlopen(f"{base}/api/cases", timeout=1):
                break
        except OSError:
            time.sleep(0.1)
    else:
        err = proc.stderr.read().decode("utf-8", "replace") if proc.stderr else ""
        pytest.fail(f"estimated-airway server did not start\n{err}")
    body = json.dumps({"role": "Radiologist", "initials": "A.B."}).encode()
    request = urllib.request.Request(
        f"{base}/api/clinician",
        data=body,
        headers={"Content-Type": "application/json"},
        method="PUT",
    )
    with urllib.request.urlopen(request, timeout=5) as response:
        assert response.status == 200


@pytest.fixture(scope="module")
def real_server(tmp_path_factory):
    root = tmp_path_factory.mktemp("estimated40-real")
    count = _rebuild_local_001(root)
    proc, base = _launch(root)
    cprint(f"estimated-airway real server {base}", "cyan")
    try:
        _wait_ready(proc, base)
        yield base, count
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()


def test_real_local_001_estimated_layer(real_server):
    base, count = real_server
    sync_playwright = _playwright_or_skip()
    shots = _shots()
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        page.goto(
            f"{base}/renderer.template.html?case=local-001&allow_review=1"
            "&debug=1&variant=anatomical",
            wait_until="domcontentloaded",
            timeout=120_000,
        )
        page.wait_for_function(
            """(expected) => {
              const hook = window.__p2_debug && window.__p2_debug.estimatedLayer;
              if (!hook) return false;
              const layer = hook();
              return layer.visible && layer.segments === expected && layer.drawnPoints > 0;
            }""",
            arg=count,
            timeout=30_000,
        )
        _assert_toggle_in_viewport(page)
        on_shot = shots / "estimated-real-on.png"
        page.screenshot(path=str(on_shot))
        cprint(f"screenshot {on_shot}", "cyan")

        page.locator("#estimated-airway-toggle").click()
        page.wait_for_function(
            """(expected) => {
              const layer = window.__p2_debug.estimatedLayer();
              return layer.visible === false && layer.drawnPoints === 0
                && layer.segments === expected;
            }""",
            arg=count,
            timeout=10_000,
        )
        off_shot = shots / "estimated-real-off.png"
        page.screenshot(path=str(off_shot))
        cprint(f"screenshot {off_shot}", "cyan")

        page.locator("#estimated-airway-toggle").click()
        page.wait_for_function(
            """(expected) => {
              const layer = window.__p2_debug.estimatedLayer();
              return layer.visible && layer.segments === expected && layer.drawnPoints > 0;
            }""",
            arg=count,
            timeout=10_000,
        )
        cluster = page.evaluate(
            """() => {
              const airway = window.AIRWAY;
              const lower = new Set([2, 5]);
              const tips = [];
              for (const segment of airway.estimated || []) {
                const edge = (airway.edges || [])[segment.from_edge];
                if (!edge || !lower.has(edge.lobe)) continue;
                const line = segment.c || [];
                if (line.length) tips.push(line[line.length - 1]);
              }
              if (!tips.length) return null;
              let best = tips[0];
              let bestCount = -1;
              for (const tip of tips) {
                let near = 0;
                for (const other of tips) {
                  const dx = tip[0] - other[0], dy = tip[1] - other[1], dz = tip[2] - other[2];
                  if (dx * dx + dy * dy + dz * dz <= 20 * 20) near += 1;
                }
                if (near > bestCount) { best = tip; bestCount = near; }
              }
              const cluster = tips.filter(other => {
                const dx = best[0] - other[0], dy = best[1] - other[1], dz = best[2] - other[2];
                return dx * dx + dy * dy + dz * dz <= 20 * 20;
              });
              const centre = cluster.reduce((sum, point) => [
                sum[0] + point[0], sum[1] + point[1], sum[2] + point[2],
              ], [0, 0, 0]).map(value => value / cluster.length);
              window.__p2_debug.focusPoint(centre, 90);
              return { terminals: cluster.length, centre };
            }"""
        )
        assert cluster and cluster["terminals"] >= 1, cluster
        page.wait_for_function(
            """() => window.__p2_debug.estimatedLayer().drawnPoints > 0""",
            timeout=10_000,
        )
        zoom_shot = shots / "estimated-real-zoom.png"
        page.screenshot(path=str(zoom_shot))
        cprint(f"screenshot {zoom_shot} cluster {cluster}", "cyan")
        browser.close()
