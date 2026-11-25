# Changes: CT estimated stroke pixels, not dash gaps, must clear the lung grey by 1.6:1.
# Changes: estimated airway draws in topology and CT panes and stays out of the statistics.
# Changes: real local-001 screenshots; the layer toggle stays inside the viewport.
# Changes: anatomical estimated-airway layer toggles on and off; endoluminal omits it.
"""Browser check for the estimated airway layer."""

from __future__ import annotations

import base64
import json
import os
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
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
        assert page.evaluate("window.__p2_debug.estimatedLayer().visible")
        on_shot = shots / "estimated-on.png"
        page.screenshot(path=str(on_shot))
        cprint(f"screenshot {on_shot}", "cyan")

        page.keyboard.press("e")
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

        page.keyboard.press("e")
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
    trachea = pipe / "ts_out/local-001/lobes/trachea.nii.gz"
    if trachea.is_file():
        symlink_input_file(trachea, ts / "lobes" / "trachea.nii.gz")
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
    env = {
        **os.environ,
        "HOME": str(home),
        "LUNGNAV_PIPELINE_ROOT": str(root),
        "LUNGNAV_DATA_ROOT": str(root),
        "LUNGNAV_CACHE_DIR": str(root / "cache"),
    }
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
        on_shot = shots / "estimated-real-on.png"
        page.screenshot(path=str(on_shot))
        cprint(f"screenshot {on_shot}", "cyan")

        page.keyboard.press("e")
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

        page.keyboard.press("e")
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


def _shots41() -> Path:
    path = Path(os.environ.get("TMPDIR", "/tmp")) / "bronchoroute-41"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _rail_stats(page) -> dict:
    return page.evaluate(
        """() => {
          const edges = window.AIRWAY.edges || [];
          const gens = edges.reduce((max, edge) => Math.max(max, Number(edge.g) || 0), 0);
          const length = edges.reduce((sum, edge) => sum + Number(edge.l || 0), 0);
          return {
            rail: {
              branches: document.getElementById("r-branches").textContent,
              gens: document.getElementById("r-gens").textContent,
              length: document.getElementById("r-length").textContent,
            },
            expected: {
              branches: String(edges.length),
              gens: String(gens),
              length: (length / 10).toFixed(0) + " cm",
            },
            estimatedInEdges: edges.some(edge => edge.prov === "estimated"),
          };
        }"""
    )


def _assert_rail(page, baseline: dict) -> None:
    stats = _rail_stats(page)
    assert stats["estimatedInEdges"] is False
    assert stats["rail"] == stats["expected"] == baseline, stats


def _warm_ct(base: str) -> None:
    cprint("warming CT crop and masks", "cyan")
    for path in ("ct/meta", "masks/meta"):
        url = f"{base}/api/cases/local-001/{path}"
        try:
            with urllib.request.urlopen(url, timeout=180) as response:
                body = json.load(response)
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")
            pytest.fail(f"{path} {exc.code}: {detail}")
        assert body.get("shape"), body
        cprint(f"{path} shape {body['shape']}", "cyan")


def _click_ct_world(page, world) -> dict:
    hit = page.evaluate(
        """(world) => {
          const ct = window.__p3_debug.ct();
          ct.setWorld(world);
          for (const pane of ["coronal", "axial", "sagittal"]) {
            const scr = ct.screenOfWorld(pane, world);
            if (!scr || scr.inCrop === false) continue;
            const canvas = document.querySelector(`.ct-pane-${pane} canvas`);
            if (!canvas) continue;
            const rect = canvas.getBoundingClientRect();
            if (scr.x < 2 || scr.y < 2 || scr.x > rect.width - 2 || scr.y > rect.height - 2) {
              continue;
            }
            const el = document.elementFromPoint(rect.left + scr.x, rect.top + scr.y);
            if (el !== canvas) continue;
            return { pane, x: rect.left + scr.x, y: rect.top + scr.y };
          }
          return null;
        }""",
        world,
    )
    assert hit, world
    page.mouse.click(hit["x"], hit["y"])
    return hit


def test_estimated_airway_in_topology_and_ct(real_server):
    base, count = real_server
    _warm_ct(base)
    sync_playwright = _playwright_or_skip()
    shots = _shots41()
    cprint(f"screenshots in {shots}", "cyan")
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))

        def _console(message):
            if message.type != "error":
                return
            if "422" in message.text:
                return
            errors.append(message.text)

        page.on("console", _console)
        page.goto(
            f"{base}/renderer.template.html?case=local-001&allow_review=1"
            "&debug=1&variant=topology",
            wait_until="domcontentloaded",
            timeout=120_000,
        )
        try:
            page.wait_for_function(
                """(expected) => {
                  const hook = window.__p2_debug && window.__p2_debug.estimatedLayer;
                  if (!hook) return false;
                  const layer = hook();
                  return layer.visible && layer.segments === expected
                    && layer.topology.drawn > 0;
                }""",
                arg=count,
                timeout=30_000,
            )
        except Exception:
            cprint(str(page.evaluate(
                "() => window.__p2_debug && window.__p2_debug.estimatedLayer()"
            )), "red")
            raise
        assert page.evaluate("window.__p2_debug.estimatedLayer().visible")
        baseline = _rail_stats(page)["rail"]
        _assert_rail(page, baseline)
        cprint(f"rail {baseline}", "cyan")

        hit = page.evaluate(
            """() => {
              const airway = window.AIRWAY;
              const topo = window.__p2_debug.topology();
              const cam = window.__p2_debug.cam();
              const canvas = document.getElementById("c");
              const rect = canvas.getBoundingClientRect();
              const scale = 260 / cam.dist;
              const cx = canvas.clientWidth / 2 + topo.pan[0];
              const cy = canvas.clientHeight / 2 + topo.pan[1];
              const byId = new Map(topo.points.map(point => [point.id, point]));
              const rejected = { parent: 0, short: 0, covered: 0, node: 0 };
              let best = null;
              for (const segment of airway.estimated || []) {
                const edges = airway.edges || [];
                let edge = edges.find(item => Number(item.oi) === Number(segment.from_edge));
                if (!edge) edge = edges[segment.from_edge];
                const parent = edge && byId.get(edge.t);
                if (!parent) { rejected.parent += 1; continue; }
                const dx = parent.x - cx;
                const dy = parent.y - cy;
                const len = Math.hypot(dx, dy);
                if (len < 8) { rejected.short += 1; continue; }
                const distances = segment.d || [];
                const maxD = distances.reduce((max, value) => Math.max(max, Number(value) || 0), 0);
                const stub = Math.max(0, Math.min(1, maxD / 15)) * 34 * scale;
                if (stub < 16) { rejected.short += 1; continue; }
                const x = parent.x + (dx / len) * 8;
                const y = parent.y + (dy / len) * 8;
                const vx = rect.left + x;
                const vy = rect.top + y;
                if (x < 4 || y < 4 || x > rect.width - 4 || y > rect.height - 4) {
                  rejected.covered += 1;
                  continue;
                }
                const el = document.elementFromPoint(vx, vy);
                if (!el || el.id !== "c") { rejected.covered += 1; continue; }
                let nearest = null;
                let nearestD = Infinity;
                for (const point of topo.points) {
                  const dist = Math.hypot(point.x - x, point.y - y);
                  if (dist < nearestD) { nearestD = dist; nearest = point; }
                }
                if (!nearest || nearest.id !== parent.id || Math.abs(nearestD - 8) > 1.5) {
                  rejected.node += 1;
                  continue;
                }
                if (!best || stub > best.stub) {
                  best = {
                    x: vx, y: vy, stub, parentId: parent.id,
                    nodeDist: nearestD, generation: Number(edge.g) + 1,
                  };
                }
              }
              return { hit: best, rejected };
            }"""
        )
        assert hit["hit"], hit
        cprint(f"topology stub {hit['hit']}", "cyan")
        page.mouse.move(hit["hit"]["x"], hit["hit"]["y"])
        page.wait_for_function(
            "() => window.__p2_debug.estimatedLayer().topology.labels === 1",
            timeout=10_000,
        )
        on_shot = shots / "topology-estimated.png"
        page.screenshot(path=str(on_shot))
        cprint(f"screenshot {on_shot}", "cyan")
        before = page.evaluate("() => window.__p3_debug.sharedPos()")
        page.mouse.click(hit["hit"]["x"], hit["hit"]["y"])
        after = page.evaluate(
            """() => {
              const panel = document.getElementById("remove-branch-panel");
              return {
                pos: window.__p3_debug.sharedPos(),
                panel: !!(panel && !panel.hidden),
              };
            }"""
        )
        assert after["panel"] is False, after
        assert after["pos"] == before, (before, after["pos"])
        cprint("topology click selected nothing", "cyan")

        page.evaluate("() => window.__p2_debug.setVariant(3)")
        page.wait_for_function(
            """() => {
              const canvas = document.querySelector(".ct-pane-axial canvas");
              const ct = window.__p3_debug.ct && window.__p3_debug.ct();
              return !!(canvas && canvas.clientWidth > 10 && ct && ct.isLoaded());
            }""",
            timeout=180_000,
        )
        estimated_key = page.locator('[data-overlay-key="estimated"]')
        estimated_key.wait_for()
        assert estimated_key.get_attribute("aria-pressed") == "false"
        assert page.evaluate("!window.__p2_debug.estimatedLayer().visible")
        page.keyboard.press("e")
        page.wait_for_function("() => window.__p2_debug.estimatedLayer().visible")
        assert "Estimated airway" in page.locator("#ct-overlay-keys").inner_text()
        cluster = page.evaluate(
            """() => {
              const airway = window.AIRWAY;
              const ct = window.__p3_debug.ct();
              const origin = airway.origin_mm || [0, 0, 0];
              const shape = ct.getMeta().shape;
              const inCrop = (ijk) => ijk[0] >= -0.5 && ijk[0] < shape[0] - 0.5
                && ijk[1] >= -0.5 && ijk[1] < shape[1] - 0.5
                && ijk[2] >= -0.5 && ijk[2] < shape[2] - 0.5;
              const axes = [
                { name: "axial", axis: 2 },
                { name: "coronal", axis: 1 },
                { name: "sagittal", axis: 0 },
              ];
              const tally = new Map();
              for (const segment of airway.estimated || []) {
                const samples = (segment.c || []).map((point, index) => {
                  const world = point.map((value, axis) => value + origin[axis]);
                  return { world, ijk: ct.worldToVoxelCont(world), index };
                });
                for (const { name, axis } of axes) {
                  const slices = new Set();
                  for (const sample of samples) {
                    if (!inCrop(sample.ijk)) continue;
                    slices.add(Math.floor(sample.ijk[axis] + 1e-9));
                  }
                  for (const slice of slices) {
                    let count = 0;
                    let pairs = 0;
                    let prev = false;
                    let world = null;
                    for (const sample of samples) {
                      const inside = inCrop(sample.ijk)
                        && Math.abs(sample.ijk[axis] - slice) <= 1;
                      if (inside) {
                        count += 1;
                        if (world == null && Math.floor(sample.ijk[axis] + 1e-9) === slice) {
                          world = sample.world;
                        }
                      }
                      if (inside && prev) pairs += 1;
                      prev = inside;
                    }
                    if (!world || pairs < 1) continue;
                    const key = name + ":" + slice;
                    const row = tally.get(key) || {
                      name, slice, count: 0, pairs: 0, world,
                    };
                    row.count += count;
                    row.pairs += pairs;
                    tally.set(key, row);
                  }
                }
              }
              let best = null;
              for (const row of tally.values()) {
                if (!best || row.count > best.count
                    || (row.count === best.count && row.pairs > best.pairs)) {
                  best = row;
                }
              }
              if (!best) return { best: null, slices: tally.size };
              ct.setWorld(best.world);
              return {
                best: {
                  name: best.name,
                  slice: best.slice,
                  count: best.count,
                  pairs: best.pairs,
                  world: best.world,
                },
                ct: window.__p2_debug.estimatedLayer().ct,
              };
            }"""
        )
        assert cluster["best"], cluster
        drawn = sum(cluster["ct"].values())
        assert drawn > 0, cluster
        assert cluster["ct"][cluster["best"]["name"]] > 0, cluster
        cprint(f"ct slab {cluster}", "cyan")
        ct_shot = shots / "ct-estimated.png"
        page.screenshot(path=str(ct_shot))
        cprint(f"screenshot {ct_shot}", "cyan")
        contrast = page.evaluate(
            """(pane) => {
              const airway = window.AIRWAY;
              const ct = window.__p3_debug.ct();
              const origin = airway.origin_mm || [0, 0, 0];
              const shape = ct.getMeta().shape;
              const fixedAxis = { axial: 2, coronal: 1, sagittal: 0 }[pane];
              const fixed = ct.getSlices()[pane];
              const canvas = document.querySelector(`.ct-pane-${pane} canvas`);
              const bitmap = canvas.getContext("2d").getImageData(0, 0, canvas.width, canvas.height);
              const scaleX = canvas.width / canvas.clientWidth;
              const scaleY = canvas.height / canvas.clientHeight;
              const inCrop = (ijk) => ijk[0] >= -0.5 && ijk[0] < shape[0] - 0.5
                && ijk[1] >= -0.5 && ijk[1] < shape[1] - 0.5
                && ijk[2] >= -0.5 && ijk[2] < shape[2] - 0.5;
              const rgbAt = (x, y) => {
                // Pixel (i, j) covers [i, i+1). Rounding lands on the far edge of a 2px stroke.
                const ix = Math.floor(x * scaleX);
                const iy = Math.floor(y * scaleY);
                if (ix < 0 || iy < 0 || ix >= bitmap.width || iy >= bitmap.height) return null;
                const index = (iy * bitmap.width + ix) * 4;
                return [bitmap.data[index], bitmap.data[index + 1], bitmap.data[index + 2]];
              };
              const lin = (channel) => {
                const s = channel / 255;
                return s <= 0.04045 ? s / 12.92 : Math.pow((s + 0.055) / 1.055, 2.4);
              };
              const lum = (rgb) => 0.2126 * lin(rgb[0]) + 0.7152 * lin(rgb[1]) + 0.0722 * lin(rgb[2]);
              const cross = ct.getCrosshair();
              const crossScr = cross && ct.screenOfWorld(pane, cross);
              const strokeL = [];
              const backL = [];
              let bestRun = null;
              for (const segment of airway.estimated || []) {
                const runs = [];
                let run = [];
                const flush = () => {
                  if (run.length > 1) runs.push(run);
                  run = [];
                };
                for (const point of segment.c || []) {
                  const world = point.map((value, axis) => value + origin[axis]);
                  const ijk = ct.worldToVoxelCont(world);
                  if (!inCrop(ijk) || Math.abs(ijk[fixedAxis] - fixed) > 1) {
                    flush();
                    continue;
                  }
                  const scr = ct.screenOfWorld(pane, world);
                  if (!scr) { flush(); continue; }
                  run.push(scr);
                }
                flush();
                for (const pts of runs) {
                  let length = 0;
                  let dashAlong = 0;
                  const samples = [];
                  for (let i = 1; i < pts.length; i++) {
                    const dx = pts[i].x - pts[i - 1].x;
                    const dy = pts[i].y - pts[i - 1].y;
                    const step = Math.hypot(dx, dy);
                    if (step > 0) {
                      const nx = -dy / step;
                      const ny = dx / step;
                      const count = Math.max(1, Math.round(step));
                      for (let s = 0; s <= count; s++) {
                        const u = s / count;
                        const x = pts[i - 1].x + dx * u;
                        const y = pts[i - 1].y + dy * u;
                        samples.push({ x, y });
                        // Same [5, 3] dash as the pane. Gaps are not stroke pixels.
                        const phase = (((dashAlong + step * u) % 8) + 8) % 8;
                        if (phase >= 5) continue;
                        if (crossScr && (Math.abs(x - crossScr.x) <= 2 || Math.abs(y - crossScr.y) <= 2)) {
                          continue;
                        }
                        // A 2 px stroke sits on a half-pixel grid; take the stroke
                        // pixel nearest the sample, not the mathematical centreline.
                        let fg = null;
                        let fgScore = -Infinity;
                        for (let oy = -1; oy <= 1; oy++) {
                          for (let ox = -1; ox <= 1; ox++) {
                            const rgb = rgbAt(x + ox, y + oy);
                            if (!rgb) continue;
                            const score = rgb[0] + rgb[2] - 2 * rgb[1];
                            if (score > fgScore) {
                              fg = rgb;
                              fgScore = score;
                            }
                          }
                        }
                        const left = rgbAt(x + nx * 4, y + ny * 4);
                        const right = rgbAt(x - nx * 4, y - ny * 4);
                        if (!fg || (!left && !right)) continue;
                        strokeL.push(lum(fg));
                        if (left) backL.push(lum(left));
                        if (right) backL.push(lum(right));
                      }
                      length += step;
                    }
                    dashAlong += step;
                  }
                  if (!bestRun || length > bestRun.length) bestRun = { length, samples };
                }
              }
              const mean = (values) => values.reduce((sum, value) => sum + value, 0) / values.length;
              const stroke = strokeL.length ? mean(strokeL) : 0;
              const background = backL.length ? mean(backL) : 0;
              const ratio = (Math.max(stroke, background) + 0.05) / (Math.min(stroke, background) + 0.05);
              let png = null;
              if (bestRun && bestRun.samples.length) {
                let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
                for (const point of bestRun.samples) {
                  minX = Math.min(minX, point.x);
                  minY = Math.min(minY, point.y);
                  maxX = Math.max(maxX, point.x);
                  maxY = Math.max(maxY, point.y);
                }
                const pad = 18;
                const sx = Math.max(0, Math.floor((minX - pad) * scaleX));
                const sy = Math.max(0, Math.floor((minY - pad) * scaleY));
                const sw = Math.min(bitmap.width - sx, Math.ceil((maxX - minX + pad * 2) * scaleX));
                const sh = Math.min(bitmap.height - sy, Math.ceil((maxY - minY + pad * 2) * scaleY));
                const zoom = 4;
                const out = document.createElement("canvas");
                out.width = Math.max(1, sw * zoom);
                out.height = Math.max(1, sh * zoom);
                const ctx = out.getContext("2d");
                ctx.imageSmoothingEnabled = false;
                ctx.drawImage(canvas, sx, sy, Math.max(1, sw), Math.max(1, sh), 0, 0, out.width, out.height);
                png = out.toDataURL("image/png");
              }
              return { contrast: ratio, samples: strokeL.length, stroke, background, png };
            }""",
            cluster["best"]["name"],
        )
        assert contrast["png"], contrast
        zoom_shot = shots / "ct-estimated-zoom.png"
        zoom_shot.write_bytes(base64.b64decode(contrast["png"].split(",", 1)[1]))
        cprint(
            f"ct contrast {contrast['contrast']:.2f}:1 "
            f"({contrast['samples']} px) screenshot {zoom_shot}",
            "cyan",
        )
        assert contrast["samples"] >= 12, contrast
        assert contrast["contrast"] >= 1.6, contrast

        page.evaluate("() => window.__p2_debug.setVariant(0)")
        page.wait_for_function(
            """(expected) => {
              const layer = window.__p2_debug.estimatedLayer();
              return window.__p2_debug.getVariant() === "anatomical"
                && layer.visible && layer.segments === expected && layer.drawnPoints > 0;
            }""",
            arg=count,
            timeout=10_000,
        )
        assert page.evaluate("window.__p2_debug.estimatedLayer().visible")
        page.evaluate("() => window.__p2_debug.setVariant(2)")
        page.wait_for_function(
            """() => window.__p2_debug.getVariant() === "topology"
              && window.__p2_debug.estimatedLayer().topology.drawn > 0""",
            timeout=10_000,
        )
        _assert_rail(page, baseline)

        page.keyboard.press("e")
        page.wait_for_function(
            """() => {
              const layer = window.__p2_debug.estimatedLayer();
              return layer.visible === false && layer.topology.drawn === 0
                && layer.topology.labels === 0;
            }""",
            timeout=10_000,
        )
        _assert_rail(page, baseline)
        off_shot = shots / "topology-estimated-off.png"
        page.screenshot(path=str(off_shot))
        cprint(f"screenshot {off_shot}", "cyan")

        page.evaluate("() => window.__p2_debug.setVariant(0)")
        page.wait_for_function(
            """(expected) => {
              const layer = window.__p2_debug.estimatedLayer();
              return window.__p2_debug.getVariant() === "anatomical"
                && layer.visible && layer.segments === expected
                && layer.drawnPoints > 0;
            }""",
            arg=count,
            timeout=10_000,
        )
        assert page.evaluate("window.__p2_debug.estimatedLayer().visible")

        page.evaluate("() => window.__p2_debug.setVariant(3)")
        page.wait_for_function(
            """() => {
              const layer = window.__p2_debug.estimatedLayer();
              return window.__p2_debug.getVariant() === "ct-panes"
                && layer.ct.axial === 0 && layer.ct.coronal === 0 && layer.ct.sagittal === 0;
            }""",
            timeout=10_000,
        )
        cprint("toggle in topology hid anatomical and CT", "cyan")

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
              const layer = window.__p2_debug.estimatedLayer();
              return {
                variant: window.__p2_debug.getVariant(),
                drawnPoints: layer.drawnPoints,
                topology: layer.topology.drawn,
                edgeEstimated: airway.edges.some(edge => edge.prov === "estimated"),
                flyEstimated: (airway.fly.prov || []).some(prov => prov === "estimated"),
                flyHitsEstimated: estimated.some(point => fly.some(sample => near(point, sample))),
              };
            }"""
        )
        assert endoluminal["variant"] == "endoluminal"
        assert endoluminal["drawnPoints"] == 0
        assert endoluminal["topology"] == 0
        assert endoluminal["edgeEstimated"] is False
        assert endoluminal["flyEstimated"] is False
        assert endoluminal["flyHitsEstimated"] is False
        assert page.evaluate("!window.__p2_debug.estimatedLayer().visible")
        cprint("endoluminal has no estimated layer", "cyan")

        page.evaluate("() => window.__p2_debug.setVariant(4)")
        page.wait_for_function(
            """() => window.__p2_debug.getVariant() === "walkthrough"
              && !window.__p2_debug.estimatedLayer().visible""",
            timeout=10_000,
        )

        page.evaluate("() => window.__p2_debug.setVariant(3)")
        page.wait_for_function(
            "() => window.__p2_debug.getVariant() === 'ct-panes'",
            timeout=10_000,
        )
        page.keyboard.press("e")
        page.wait_for_function(
            "() => window.__p2_debug.estimatedLayer().visible",
            timeout=10_000,
        )
        restored = page.evaluate(
            """(world) => {
              window.__p3_debug.ct().setWorld(world);
              return window.__p2_debug.estimatedLayer().ct;
            }""",
            cluster["best"]["world"],
        )
        assert sum(restored.values()) > 0, restored
        cprint("CT checkbox restored the shared layer", "cyan")

        lesion = page.evaluate(
            """() => {
              const airway = window.AIRWAY;
              const origin = airway.origin_mm || [0, 0, 0];
              const ct = window.__p3_debug.ct();
              const shape = ct.getMeta().shape;
              const inCrop = (ijk) => ijk[0] >= -0.5 && ijk[0] < shape[0] - 0.5
                && ijk[1] >= -0.5 && ijk[1] < shape[1] - 0.5
                && ijk[2] >= -0.5 && ijk[2] < shape[2] - 0.5;
              const edges = (airway.edges || [])
                .filter(edge => edge.prov === "ct" && (edge.c || []).length >= 2)
                .sort((a, b) => Number(a.g) - Number(b.g));
              for (const edge of edges) {
                const point = edge.c[Math.floor(edge.c.length / 2)];
                const world = point.map((value, axis) => value + origin[axis]);
                if (inCrop(ct.worldToVoxelCont(world))) return world;
              }
              return null;
            }"""
        )
        assert lesion, "no in-crop CT centreline sample"
        cprint("confirming imported series", "cyan")
        request = urllib.request.Request(
            f"{base}/api/cases/local-001/series/confirm",
            data=b"{}",
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                assert response.status == 200
        except urllib.error.HTTPError as exc:
            pytest.fail(exc.read().decode("utf-8", "replace"))
        page.locator("#ct-mark-lesion").click()
        _click_ct_world(page, lesion)
        try:
            page.wait_for_function(
                """() => {
                  const rt = window.__p3_debug.airway().route_target;
                  return !!(rt && !rt.stale && rt.rank === 1);
                }""",
                timeout=60_000,
            )
        except Exception:
            detail = page.evaluate(
                """() => ({
                  lesion: window.__p3_debug.airway().lesion,
                  ctStatus: (document.getElementById("ct-lesion-status") || {}).textContent,
                  routeStatus: (document.getElementById("route-target-status") || {}).textContent,
                  panelHidden: document.getElementById("route-target-panel").hidden,
                })"""
            )
            cprint(str(detail), "red")
            raise
        cprint("lesion placed", "cyan")
        far = page.evaluate(
            """() => {
              const airway = window.AIRWAY;
              const origin = airway.origin_mm || [0, 0, 0];
              const ct = window.__p3_debug.ct();
              const shape = ct.getMeta().shape;
              const inCrop = (ijk) => ijk[0] >= -0.5 && ijk[0] < shape[0] - 0.5
                && ijk[1] >= -0.5 && ijk[1] < shape[1] - 0.5
                && ijk[2] >= -0.5 && ijk[2] < shape[2] - 0.5;
              const lines = [];
              for (const edge of airway.edges || []) {
                const line = edge.c || [];
                for (let i = 1; i < line.length; i++) lines.push([line[i - 1], line[i]]);
              }
              const distToSeg = (point, a, b) => {
                const ab = [b[0] - a[0], b[1] - a[1], b[2] - a[2]];
                const len2 = ab[0] * ab[0] + ab[1] * ab[1] + ab[2] * ab[2];
                if (len2 < 1e-9) {
                  return Math.hypot(point[0] - a[0], point[1] - a[1], point[2] - a[2]);
                }
                let t = ((point[0] - a[0]) * ab[0] + (point[1] - a[1]) * ab[1]
                  + (point[2] - a[2]) * ab[2]) / len2;
                t = Math.max(0, Math.min(1, t));
                return Math.hypot(
                  point[0] - (a[0] + t * ab[0]),
                  point[1] - (a[1] + t * ab[1]),
                  point[2] - (a[2] + t * ab[2]),
                );
              };
              let best = null;
              for (const segment of airway.estimated || []) {
                const line = segment.c || [];
                const distances = segment.d || [];
                for (let i = 0; i < line.length; i++) {
                  if (Number(distances[i]) < 8) continue;
                  const rel = line[i];
                  let clearance = Infinity;
                  let beaten = false;
                  for (const [a, b] of lines) {
                    const dist = distToSeg(rel, a, b);
                    if (dist < clearance) clearance = dist;
                    if (clearance < 4 || (best && clearance <= best.clearance)) {
                      beaten = true;
                      break;
                    }
                  }
                  if (beaten || clearance < 4) continue;
                  const world = rel.map((value, axis) => value + origin[axis]);
                  if (!inCrop(ct.worldToVoxelCont(world))) continue;
                  if (!best || clearance > best.clearance) {
                    best = { world, clearance, distance: Number(distances[i]) };
                  }
                }
              }
              return best;
            }"""
        )
        assert far and far["clearance"] > 4, far
        cprint(f"far estimated point clearance {far['clearance']:.1f} mm", "cyan")
        # The automatic target stays on the CT-seen centreline, never on the
        # estimated airway that sits away from it.
        chosen = page.evaluate(
            """async () => {
              const body = await (await fetch('/api/cases/local-001/route-candidates')).json();
              const target = window.__p3_debug.airway().route_target;
              return { top: body.candidates[0], target };
            }"""
        )
        assert chosen["target"]["edge_id"] == chosen["top"]["edge_id"], chosen
        assert chosen["target"]["target_gap_mm"] == chosen["top"]["target_gap_mm"], chosen
        cprint(f"auto route target {chosen['target']['edge_id']}", "cyan")

        page.keyboard.press("e")
        page.wait_for_function(
            """() => {
              const layer = window.__p2_debug.estimatedLayer();
              return layer.visible === false
                && layer.ct.axial === 0 && layer.ct.coronal === 0 && layer.ct.sagittal === 0;
            }""",
            timeout=10_000,
        )
        page.evaluate("() => window.__p2_debug.setVariant(2)")
        page.wait_for_function(
            """() => window.__p2_debug.getVariant() === "topology"
              && window.__p2_debug.estimatedLayer().topology.drawn === 0
              && !window.__p2_debug.estimatedLayer().visible""",
            timeout=10_000,
        )
        _assert_rail(page, baseline)
        assert not errors, errors
        browser.close()
