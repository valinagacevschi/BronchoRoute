# Changes: check pass at 640x360 with sampled lumen; one 720p encode (#45).
# Changes: fork hold looks ahead so both child orifices are on screen.
# Changes: fork-hold on-route at screen 12; no synthesised orifices; rolled parity.
# Changes: lumen fill, interactive/video parity, and HUD left-inset checks on every frame.
# Changes: offline fly-through frame count, pose match, cancel, and 720p encode.
"""Offline endoluminal fly-through via the debug hook and Record encoder."""

from __future__ import annotations

import base64
import json
import math
import os
import shutil
import socket
import subprocess
import time
import urllib.request
from pathlib import Path

import numpy as np
import pytest
from termcolor import cprint

import repo_root

repo_root.ensure()

from app.review import densify_centreline  # noqa: E402
from app.route_target import CANDIDATE_STEP_MM, resolve_edge_sample  # noqa: E402
from test_app_server import _stamp_pipeline_version, _write_case_tree  # noqa: E402
from test_viewer_v2_playwright import VIEWPORT, _playwright_or_skip  # noqa: E402

ROOT = Path(__file__).resolve().parent
DESKTOP = ROOT.parent / "desktop"


def _polyline(start, end, step=1.0):
    start = np.asarray(start, dtype=float)
    end = np.asarray(end, dtype=float)
    count = max(1, int(math.ceil(float(np.linalg.norm(end - start)) / step)))
    return [((1.0 - i / count) * start + (i / count) * end).tolist() for i in range(count + 1)]


def _synthetic_graph(case_id: str = "local-001") -> dict:
    return {
        "case": case_id,
        "root_node_id": 0,
        "coordinate_system": "RAS",
        "nodes": [
            {"id": 0, "position_mm": [0.0, 0.0, 0.0], "radius_mm": 8.0},
            {"id": 1, "position_mm": [0.0, 0.0, -20.0], "radius_mm": 5.0},
            {"id": 2, "position_mm": [16.0, 0.0, -20.0], "radius_mm": 4.0},
            {"id": 3, "position_mm": [-12.0, 0.0, -26.0], "radius_mm": 4.0},
            {"id": 4, "position_mm": [16.0, 12.0, -30.0], "radius_mm": 3.0},
            {"id": 5, "position_mm": [16.0, -8.0, -28.0], "radius_mm": 3.0},
        ],
        "edges": [
            {
                "from": 0, "to": 1, "generation": 0, "radius_mm": 6.0, "length_mm": 20.0,
                "lobe": None, "airway_name": "trachea", "provenance": "ct",
                "centreline_mm": _polyline([0, 0, 0], [0, 0, -20]),
            },
            {
                "from": 1, "to": 2, "generation": 1, "radius_mm": 4.0, "length_mm": 16.0,
                "lobe": None, "airway_name": "right", "provenance": "ct",
                "centreline_mm": _polyline([0, 0, -20], [16, 0, -20]),
            },
            {
                "from": 1, "to": 3, "generation": 1, "radius_mm": 4.0, "length_mm": 13.4,
                "lobe": None, "airway_name": "left", "provenance": "ct",
                "centreline_mm": _polyline([0, 0, -20], [-12, 0, -26]),
            },
            {
                "from": 2, "to": 4, "generation": 2, "radius_mm": 3.0, "length_mm": 15.6,
                "lobe": None, "airway_name": "anterior", "provenance": "ct",
                "centreline_mm": _polyline([16, 0, -20], [16, 12, -30]),
            },
            {
                "from": 2, "to": 5, "generation": 2, "radius_mm": 3.0, "length_mm": 12.8,
                "lobe": None, "airway_name": "posterior", "provenance": "ct",
                "centreline_mm": _polyline([16, 0, -20], [16, -8, -28]),
            },
        ],
        "review": [],
    }


def _seed(tmp: Path) -> Path:
    paths = _write_case_tree(tmp, with_bridges=False)
    graph_path = paths["graph"]
    graph_path.write_text(json.dumps(_synthetic_graph()))
    case_path = paths["case"]
    document = json.loads(case_path.read_text())
    document["records"] = []
    document["versions"] = []
    document["review_signoff"] = []
    case_path.write_text(json.dumps(document, indent=2) + "\n")
    _stamp_pipeline_version(case_path)
    (tmp / "app_state").mkdir(exist_ok=True)
    cprint(f"seeded synthetic fly-through case at {tmp}", "cyan")
    return case_path


def _write_route_target(case_path: Path, graph: dict, *, edge_id: int, arc_mm: float) -> dict:
    edge = graph["edges"][edge_id]
    samples, arcs = densify_centreline(
        np.asarray(edge["centreline_mm"], dtype=float), CANDIDATE_STEP_MM,
    )
    sample_index = int(np.argmin(np.abs(arcs - float(arc_mm))))
    resolved = resolve_edge_sample(graph, edge_id, sample_index)
    document = json.loads(case_path.read_text())
    document["route_target"] = {
        "edge_id": int(edge_id),
        "sample_index": int(sample_index),
        "ras_mm": [float(v) for v in resolved["ras_mm"]],
        "target_gap_mm": 0.0,
        "target_inside_airway": True,
        "rank": 1,
    }
    case_path.write_text(json.dumps(document, indent=2) + "\n")
    return document["route_target"]


@pytest.fixture
def server(tmp_path: Path):
    case_path = _seed(tmp_path)
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    base = f"http://127.0.0.1:{port}"
    home = tmp_path / "fake_home"
    home.mkdir()
    env = {**os.environ, "HOME": str(home), "LUNGNAV_PIPELINE_ROOT": str(tmp_path)}
    env.pop("PYTHONPATH", None)
    log_path = tmp_path / "server.err"
    log = log_path.open("w")
    cprint(f"flythrough server on {port}", "cyan")
    proc = subprocess.Popen(
        ["uv", "run", "python", "app_server.py", "--port", str(port)],
        cwd=ROOT,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=log,
    )
    try:
        for _ in range(200):
            if proc.poll() is not None:
                log.flush()
                pytest.fail(f"flythrough server exited\n{log_path.read_text()[-2000:]}")
            try:
                with urllib.request.urlopen(f"{base}/api/cases", timeout=1):
                    break
            except OSError:
                time.sleep(0.1)
        else:
            pytest.fail("flythrough server did not start")
        yield base, case_path, tmp_path
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)
        log.close()


def _open_endoluminal(page, server: str):
    page.goto(
        f"{server}/renderer.template.html?case=local-001&allow_review=1&debug=1&variant=endoluminal",
        wait_until="domcontentloaded",
    )
    page.wait_for_function(
        """() => window.__p2_debug
          && document.getElementById('c').clientWidth > 100
          && window.AIRWAY
          && Array.isArray(window.AIRWAY.fly && window.AIRWAY.fly.s)
          && window.AIRWAY.fly.s.length > 1""",
    )
    page.wait_for_function("() => document.fonts.status === 'loaded'")
    page.evaluate("() => window.__p2_debug.setVariant(1)")
    page.wait_for_function(
        """() => document.getElementById('flythrough-btn')
          && !document.getElementById('flythrough-btn').hidden
          && document.getElementById('c').clientWidth > 100""",
    )


def test_flythrough_offline_render_cancel_and_encode(server):
    sync_playwright = _playwright_or_skip()
    base, case_path, tmp_pipeline = server
    artifacts = Path(os.environ.get("TMPDIR", "/tmp")) / "bronchoroute-44"
    if artifacts.exists():
        shutil.rmtree(artifacts)
    artifacts.mkdir(parents=True)
    frames_dir = artifacts / "frames"
    frames_dir.mkdir()
    cprint(f"artifacts in {artifacts}", "cyan")

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            args=[
                "--disable-background-timer-throttling",
                "--disable-backgrounding-occluded-windows",
                "--disable-renderer-backgrounding",
            ],
        )
        context = browser.new_context(viewport=VIEWPORT)
        page = context.new_page()
        page.set_default_timeout(300_000)
        page.on("pageerror", lambda err: cprint(f"pageerror {err}", "red"))

        cprint("no RouteTarget → disabled button", "cyan")
        _open_endoluminal(page, base)
        blocked = page.evaluate(
            """() => ({
              hidden: document.getElementById('flythrough-btn').hidden,
              disabled: document.getElementById('flythrough-btn').disabled,
              reason: document.getElementById('flythrough-reason').textContent,
              title: document.getElementById('flythrough-btn').title,
              block: window.__p2_debug.flythroughBlockReason(),
              width: document.getElementById('c').clientWidth,
            })"""
        )
        assert blocked["hidden"] is False
        assert blocked["disabled"] is True
        assert blocked["reason"] == "Choose a RouteTarget"
        assert blocked["title"] == "Choose a RouteTarget"
        assert blocked["block"] == "Choose a RouteTarget"
        assert blocked["width"] > 100

        cprint("stale RouteTarget reason", "cyan")
        document = json.loads(case_path.read_text())
        document["route_target"] = {
            "edge_id": 9999,
            "sample_index": 0,
            "ras_mm": [0.0, 0.0, -10.0],
        }
        case_path.write_text(json.dumps(document, indent=2) + "\n")
        _open_endoluminal(page, base)
        stale = page.evaluate("() => window.__p2_debug.flythroughBlockReason()")
        assert stale == "not reachable from RouteStart"
        reason_text = page.evaluate(
            "() => document.getElementById('flythrough-reason').textContent"
        )
        assert reason_text == "not reachable from RouteStart"

        cprint("active RouteTarget on anterior edge", "cyan")
        graph = json.loads((tmp_pipeline / "out/local-001/local-001_graph.json").read_text())
        _write_route_target(case_path, graph, edge_id=3, arc_mm=12.0)
        _open_endoluminal(page, base)
        ready = page.evaluate(
            """() => ({
              block: window.__p2_debug.flythroughBlockReason(),
              disabled: document.getElementById('flythrough-btn').disabled,
              hidden: document.getElementById('flythrough-btn').hidden,
              forks: (window.AIRWAY.forks || []).map(f => f.s),
              length: window.AIRWAY.fly.s.at(-1),
              width: document.getElementById('c').clientWidth,
            })"""
        )
        assert ready["block"] == ""
        assert ready["disabled"] is False
        assert ready["hidden"] is False
        assert len(ready["forks"]) == 2
        assert ready["width"] > 100
        length = float(ready["length"])
        fork_count = len(ready["forks"])
        expected_m = math.ceil(length / (10 / 30)) + 30 * (fork_count + 1)
        cprint(f"L={length:.3f} forks={fork_count} M={expected_m}", "cyan")

        page.evaluate(
            """() => {
              window.__p2_debug.setFly({ s: 4.5, phi: 0.37, playing: false });
              window.__rafTicks = 0;
              function tick() {
                window.__rafTicks += 1;
                requestAnimationFrame(tick);
              }
              requestAnimationFrame(tick);
            }"""
        )
        before = page.evaluate("() => window.__p2_debug.flyState()")
        assert abs(before["s"] - 4.5) < 1e-9
        assert abs(before["phi"] - 0.37) < 1e-9

        cprint("renderFlythrough check pass @640x360 (sampled lumen)", "cyan")
        result = page.evaluate(
            """async (expectedM) => {
              const close = (a, b, eps = 1e-6) => {
                if (!a || !b || a.length !== b.length) return false;
                for (let i = 0; i < a.length; i += 1) {
                  if (Math.abs(a[i] - b[i]) > eps) return false;
                }
                return true;
              };
              const width = 640;
              const height = 360;
              const SAMPLE_N = 5;
              const lumOf = (r, g, b) => 0.2126 * r + 0.7152 * g + 0.0722 * b;
              const inRect = (x, y, r) => !!(r && x >= r.l && x < r.r && y >= r.t && y < r.b);
              async function lumenStats(bytes, meta) {
                const blob = new Blob([bytes], { type: "image/jpeg" });
                const bmp = await createImageBitmap(blob);
                const c = document.createElement("canvas");
                c.width = bmp.width;
                c.height = bmp.height;
                const g = c.getContext("2d", { willReadFrequently: true });
                g.drawImage(bmp, 0, 0);
                bmp.close();
                const { data } = g.getImageData(0, 0, c.width, c.height);
                const bg = [data[0], data[1], data[2]];
                const cssW = document.getElementById("c").clientWidth;
                const cssH = document.getElementById("c").clientHeight;
                const scale = (cssW / cssH) >= (width / height) ? width / cssW : height / cssH;
                const contentW = cssW * scale;
                const contentH = cssH * scale;
                const offsetX = (width - contentW) / 2;
                const offsetY = (height - contentH) / 2;
                const panel = meta.guidance && meta.guidance.panel;
                const burnTop = {
                  l: offsetX + 8 * scale,
                  t: offsetY + 8 * scale,
                  r: offsetX + Math.min(contentW - 8 * scale, (12 + 560) * scale),
                  b: offsetY + (12 + 48) * scale,
                };
                const burnFoot = {
                  l: offsetX + contentW * 0.2,
                  t: offsetY + contentH - 44 * scale,
                  r: offsetX + contentW * 0.8,
                  b: offsetY + contentH - 6 * scale,
                };
                let total = 0;
                let differ = 0;
                let sumY = 0;
                let sumAbs = 0;
                const bgY = lumOf(bg[0], bg[1], bg[2]);
                for (let y = Math.floor(offsetY) + 2; y < offsetY + contentH - 2; y += 2) {
                  for (let x = Math.floor(offsetX) + 2; x < offsetX + contentW - 2; x += 2) {
                    if (inRect(x, y, panel) || inRect(x, y, burnTop) || inRect(x, y, burnFoot)) {
                      continue;
                    }
                    const i = (y * width + x) * 4;
                    const dr = Math.abs(data[i] - bg[0]);
                    const dg = Math.abs(data[i + 1] - bg[1]);
                    const db = Math.abs(data[i + 2] - bg[2]);
                    total += 1;
                    sumY += lumOf(data[i], data[i + 1], data[i + 2]);
                    sumAbs += (dr + dg + db) / 3;
                    if (dr > 8 || dg > 8 || db > 8) differ += 1;
                  }
                }
                return {
                  index: meta.index,
                  hold: meta.hold || null,
                  s: meta.s,
                  phi: meta.phi,
                  differFrac: total ? differ / total : 0,
                  meanY: total ? sumY / total : 0,
                  bgY,
                  meanAbs: total ? sumAbs / total : 0,
                  panelL: panel ? panel.l : null,
                  total,
                };
              }
              async function videoLumenThumb(bytes, meta) {
                const blob = new Blob([bytes], { type: "image/jpeg" });
                const bmp = await createImageBitmap(blob);
                const c = document.createElement("canvas");
                c.width = bmp.width;
                c.height = bmp.height;
                const g = c.getContext("2d", { willReadFrequently: true });
                g.drawImage(bmp, 0, 0);
                bmp.close();
                const { data } = g.getImageData(0, 0, c.width, c.height);
                const cssW = document.getElementById("c").clientWidth;
                const cssH = document.getElementById("c").clientHeight;
                const scale = (cssW / cssH) >= (width / height) ? width / cssW : height / cssH;
                const contentW = cssW * scale;
                const contentH = cssH * scale;
                const offsetX = (width - contentW) / 2;
                const offsetY = (height - contentH) / 2;
                const padX = cssW * 0.18;
                const padY = cssH * 0.18;
                let cssL = padX;
                let cssT = padY;
                let cssR = cssW - padX;
                let cssB = cssH - padY;
                const panel = meta.guidance && meta.guidance.panel;
                if (panel && Number.isFinite(panel.t)) {
                  const panelCssT = (panel.t - offsetY) / scale;
                  cssB = Math.min(cssB, panelCssT - 4);
                }
                const tw = 64;
                const th = 36;
                const out = new Float32Array(tw * th);
                const rw = Math.max(1, cssR - cssL);
                const rh = Math.max(1, cssB - cssT);
                for (let ty = 0; ty < th; ty += 1) {
                  for (let tx = 0; tx < tw; tx += 1) {
                    const cssX = cssL + (tx + 0.5) * rw / tw;
                    const cssY = cssT + (ty + 0.5) * rh / th;
                    const vx = Math.min(width - 1, Math.max(0, Math.floor(offsetX + cssX * scale)));
                    const vy = Math.min(height - 1, Math.max(0, Math.floor(offsetY + cssY * scale)));
                    const i = (vy * width + vx) * 4;
                    out[ty * tw + tx] = lumOf(data[i], data[i + 1], data[i + 2]);
                  }
                }
                return Array.from(out);
              }
              async function orificeDarkBlobs(bytes, orifices) {
                const blob = new Blob([bytes], { type: "image/jpeg" });
                const bmp = await createImageBitmap(blob);
                const c = document.createElement("canvas");
                c.width = bmp.width;
                c.height = bmp.height;
                const g = c.getContext("2d", { willReadFrequently: true });
                g.drawImage(bmp, 0, 0);
                bmp.close();
                const { data } = g.getImageData(0, 0, c.width, c.height);
                const cssW = document.getElementById("c").clientWidth;
                const cssH = document.getElementById("c").clientHeight;
                const scale = (cssW / cssH) >= (width / height) ? width / cssW : height / cssH;
                const contentW = cssW * scale;
                const contentH = cssH * scale;
                const offsetX = (width - contentW) / 2;
                const offsetY = (height - contentH) / 2;
                const blobs = [];
                for (const o of orifices || []) {
                  if (!Number.isFinite(o.x) || !Number.isFinite(o.y)) continue;
                  const cx = offsetX + o.x * scale;
                  const cy = offsetY + o.y * scale;
                  const rad = Math.max(6, (o.pixels || 20) * scale * 0.85);
                  let n = 0;
                  let sx = 0;
                  let sy = 0;
                  const x0 = Math.max(0, Math.floor(cx - rad));
                  const x1 = Math.min(width - 1, Math.ceil(cx + rad));
                  const y0 = Math.max(0, Math.floor(cy - rad));
                  const y1 = Math.min(height - 1, Math.ceil(cy + rad));
                  for (let y = y0; y <= y1; y += 1) {
                    for (let x = x0; x <= x1; x += 1) {
                      if (Math.hypot(x - cx, y - cy) > rad) continue;
                      const i = (y * width + x) * 4;
                      if (lumOf(data[i], data[i + 1], data[i + 2]) > 28) continue;
                      n += 1;
                      sx += x;
                      sy += y;
                    }
                  }
                  if (n < 12) continue;
                  const bx = sx / n;
                  const by = sy / n;
                  blobs.push({
                    n,
                    x: bx,
                    y: by,
                    on_route: !!o.on_route,
                    angle: Math.atan2(bx - width / 2, -(by - height / 2)) * 180 / Math.PI,
                  });
                }
                return blobs;
              }
              const labels = [];
              const forkHolds = [];
              const lumenFails = [];
              const hudFails = [];
              const forkAlignFails = [];
              const synthFails = [];
              let firstForkMeta = null;
              let firstForkBytes = null;
              let poseMismatch = null;
              let burnMismatch = null;
              let forkHoldCount = 0;
              let lumenChecked = 0;
              const rafStart = window.__rafTicks;
              const when = new Date(2026, 10, 2, 21, 40, 0);
              const result = await window.__p2_debug.renderFlythrough({
                size: { width, height },
                hud: true,
                lesion: true,
                when,
                yieldEvery: 5,
                onProgress(done, total) {
                  labels.push(`Rendering ${done} / ${total} frames`);
                },
                async sink(bytes, meta) {
                  const orifices = Array.isArray(meta.orifices) ? meta.orifices : [];
                  for (const o of orifices) {
                    if (!o || o.projected !== true || !Number.isFinite(o.edge)) {
                      if (synthFails.length < 8) {
                        synthFails.push({ index: meta.index, orifice: o });
                      }
                    }
                  }
                  const isHold = meta.hold === "fork" || meta.hold === "end";
                  const sampleLumen = isHold || (meta.index % SAMPLE_N === 0);
                  if (meta.hold === "fork") {
                    forkHoldCount += 1;
                    const cam = meta.camera;
                    const on = orifices.find(o => o.on_route);
                    let orificeAngle = null;
                    if (on && cam && Number.isFinite(on.x) && Number.isFinite(on.y)) {
                      orificeAngle = Math.atan2(
                        on.x - cam.w / 2, -(on.y - cam.h / 2),
                      ) * 180 / Math.PI;
                    }
                    const blobs = await orificeDarkBlobs(bytes, orifices);
                    const routeBlob = blobs.find(b => b.on_route) || blobs[0] || null;
                    const inViewport = (o) => {
                      if (!cam || !Number.isFinite(o.x) || !Number.isFinite(o.y)) return false;
                      const margin = Math.max(8, (o.pixels || 0) * 0.25);
                      return o.x > margin && o.x < cam.w - margin
                        && o.y > margin && o.y < cam.h - margin;
                    };
                    const hold = {
                      index: meta.index,
                      s: meta.s,
                      phi: meta.phi,
                      orificeAngle,
                      blobAngle: routeBlob ? routeBlob.angle : null,
                      blobCount: blobs.length,
                      orifices: orifices.map(o => ({
                        name: o.name,
                        on_route: o.on_route,
                        edge: o.edge,
                        x: o.x,
                        y: o.y,
                        pixels: o.pixels,
                        projected: o.projected,
                        inViewport: inViewport(o),
                      })),
                      guidance: meta.guidance && {
                        aligned: meta.guidance.aligned,
                        hint: meta.guidance.hint,
                        delta: meta.guidance.delta,
                        ahead: meta.guidance.ahead,
                        degenerate: meta.guidance.degenerate,
                        panel: meta.guidance.panel && { ...meta.guidance.panel },
                      },
                    };
                    forkHolds.push(hold);
                    const bothOnScreen = orifices.length >= 2
                      && orifices.every(o => o.projected === true && inViewport(o));
                    if (!bothOnScreen || orificeAngle == null || Math.abs(orificeAngle) > 15
                      || !routeBlob || Math.abs(routeBlob.angle) > 30
                      || blobs.length < 2) {
                      if (forkAlignFails.length < 8) forkAlignFails.push(hold);
                    }
                    if (!firstForkMeta) {
                      firstForkMeta = {
                        s: meta.s,
                        phi: meta.phi,
                        guidance: meta.guidance && {
                          panel: meta.guidance.panel && { ...meta.guidance.panel },
                        },
                      };
                      firstForkBytes = bytes;
                    }
                  }
                  const cam = meta.camera;
                  if (!cam || cam.s !== meta.s || cam.phi !== meta.phi) {
                    if (!poseMismatch) poseMismatch = { index: meta.index, s: meta.s, phi: meta.phi, why: "meta" };
                  } else if (sampleLumen && !poseMismatch) {
                    const interactive = window.__p2_debug.poseCamera(meta.s, meta.phi);
                    if (!interactive
                      || interactive.s !== meta.s || interactive.phi !== meta.phi
                      || cam.w !== interactive.w || cam.h !== interactive.h
                      || Math.abs(cam.focalPx - interactive.focalPx) > 1e-6
                      || !close(cam.pos, interactive.pos)
                      || !close(cam.right, interactive.right)
                      || !close(cam.up, interactive.up)
                      || !close(cam.dir, interactive.dir)) {
                      poseMismatch = { index: meta.index, s: meta.s, phi: meta.phi, why: "interactive" };
                    }
                  }
                  const burn = meta.burn;
                  if (!burnMismatch && (!burn
                    || burn.viewName !== "Endoluminal fly-through"
                    || burn.caseId !== "local-001"
                    || burn.footer !== "Simulated · CT-derived · research use")) {
                    burnMismatch = burn;
                  }
                  if (sampleLumen) {
                    lumenChecked += 1;
                    const stats = await lumenStats(bytes, meta);
                    if (stats.differFrac < 0.60
                      || Math.abs(stats.meanY - stats.bgY) < 10
                      || stats.meanAbs < 16) {
                      if (lumenFails.length < 8) lumenFails.push(stats);
                    }
                    if (!(stats.panelL != null && stats.panelL <= 25)) {
                      if (hudFails.length < 8) {
                        hudFails.push({ index: meta.index, panelL: stats.panelL });
                      }
                    }
                  }
                },
              });
              const after = window.__p2_debug.flyState();
              let parity = null;
              if (firstForkBytes && firstForkMeta) {
                const videoThumb = await videoLumenThumb(firstForkBytes, {
                  guidance: firstForkMeta.guidance,
                });
                window.__p2_debug.setFly({
                  s: firstForkMeta.s, phi: firstForkMeta.phi, playing: false,
                });
                const interactiveThumb = window.__p2_debug.lumenThumbAt(
                  firstForkMeta.s, firstForkMeta.phi,
                );
                let mad = 0;
                for (let i = 0; i < videoThumb.length; i += 1) {
                  mad += Math.abs(videoThumb[i] - interactiveThumb[i]);
                }
                mad /= videoThumb.length;
                parity = {
                  mad,
                  madPct: mad / 255,
                  s: firstForkMeta.s,
                  phi: firstForkMeta.phi,
                };
              }
              return {
                result,
                labelsFirst: labels[0] || null,
                labelsLast: labels.length ? labels[labels.length - 1] : null,
                labelCount: labels.length,
                rafStart,
                rafEnd: window.__rafTicks,
                after,
                forkHoldCount,
                forkHolds: forkHolds.filter((_, i) => i % 30 === 0),
                poseMismatch,
                burnMismatch,
                firstForkIndex: forkHolds.length ? forkHolds[0].index : null,
                lumenFails,
                hudFails,
                forkAlignFails,
                synthFails,
                parity,
                lumenChecked,
              };
            }""",
            expected_m,
        )
        assert result["result"]["cancelled"] is False
        assert result["result"]["total"] == expected_m
        assert result["result"]["frames"] == expected_m
        assert result["labelCount"] == expected_m
        assert result["labelsFirst"] == f"Rendering 1 / {expected_m} frames"
        assert result["labelsLast"] == f"Rendering {expected_m} / {expected_m} frames"
        assert abs(result["after"]["s"] - before["s"]) < 1e-9
        assert abs(result["after"]["phi"] - before["phi"]) < 1e-9
        assert result["rafEnd"] > result["rafStart"], "rAF must advance during render"
        assert result["poseMismatch"] is None, result["poseMismatch"]
        assert result["burnMismatch"] is None, result["burnMismatch"]
        assert result["forkHoldCount"] == 30 * fork_count
        for shot in result["forkHolds"]:
            g = shot["guidance"]
            assert g is not None
            assert g["aligned"] is True
            assert str(g["hint"]).startswith("aligned")
            assert abs(float(g["delta"])) <= 15.0
            assert g["ahead"] is False
            assert g["degenerate"] is False
        cprint(
            f"lumen checked={result['lumenChecked']} fails={len(result['lumenFails'])} "
            f"hudFails={len(result['hudFails'])} alignFails={len(result['forkAlignFails'])} "
            f"synthFails={len(result['synthFails'])} parity={result['parity']}",
            "cyan",
        )
        assert result["lumenFails"] == [], result["lumenFails"]
        assert result["hudFails"] == [], result["hudFails"]
        assert result["synthFails"] == [], result["synthFails"]
        assert result["forkAlignFails"] == [], result["forkAlignFails"]
        for shot in result["forkHolds"]:
            assert len(shot["orifices"]) >= 2, shot
            assert all(o.get("projected") is True and o.get("inViewport") for o in shot["orifices"]), shot
            on = next((o for o in shot["orifices"] if o.get("on_route")), None)
            assert on is not None, shot
            assert abs(float(shot["orificeAngle"])) <= 15.0, shot
            assert int(shot["blobCount"]) >= 2, shot
            assert abs(float(shot["blobAngle"])) <= 30.0, shot
        assert result["parity"] is not None
        assert result["parity"]["madPct"] <= 0.10, result["parity"]

        cprint("cancel after 4 frames", "cyan")
        cancel = page.evaluate(
            """async () => {
              const calls = [];
              const controller = new AbortController();
              window.__p2_debug.setFly({ s: 4.5, phi: 0.37, playing: false });
              const before = window.__p2_debug.flyState();
              const result = await window.__p2_debug.renderFlythrough({
                size: { width: 640, height: 360 },
                hud: true,
                lesion: true,
                yieldEvery: 1,
                signal: controller.signal,
                sink() {
                  calls.push(1);
                  if (calls.length === 4) controller.abort();
                },
              });
              const after = window.__p2_debug.flyState();
              const phiBefore = after.phi;
              window.dispatchEvent(new KeyboardEvent('keydown', {
                key: 'ArrowRight', bubbles: true, cancelable: true,
              }));
              const afterKey = window.__p2_debug.flyState();
              return {
                result, calls: calls.length, before, after, phiBefore, afterKey,
              };
            }"""
        )
        assert cancel["result"]["cancelled"] is True
        assert cancel["result"]["frames"] == 4
        assert cancel["calls"] == 4
        assert abs(cancel["after"]["s"] - cancel["before"]["s"]) < 1e-9
        assert abs(cancel["after"]["phi"] - cancel["before"]["phi"]) < 1e-9
        step = 5 * math.pi / 180
        assert abs(cancel["afterKey"]["phi"] - (cancel["phiBefore"] + step)) < 1e-9

        first_fork = int(result["firstForkIndex"])
        last = expected_m - 1
        cprint("encode pass @1280x720 (no per-frame lumen)", "cyan")
        encode_meta = page.evaluate(
            """async (expectedM) => {
              const jpegFrames = [];
              const when = new Date(2026, 10, 2, 21, 40, 0);
              const result = await window.__p2_debug.renderFlythrough({
                size: { width: 1280, height: 720 },
                hud: true,
                lesion: true,
                when,
                yieldEvery: 30,
                async sink(bytes, meta) {
                  jpegFrames.push(bytes);
                  if (meta.index === 0 || meta.hold === "fork" || meta.index === expectedM - 1) {
                    // keep landmark frames for artifacts via side channel indices
                  }
                },
              });
              window.__flythroughJpegFrames = jpegFrames;
              const forkIndex = jpegFrames.findIndex((_, i) => false);
              return {
                frames: result.frames,
                total: result.total,
                cancelled: result.cancelled,
                jpegCount: jpegFrames.length,
              };
            }""",
            expected_m,
        )
        assert encode_meta["cancelled"] is False
        assert encode_meta["frames"] == expected_m
        assert encode_meta["jpegCount"] == expected_m

        cprint("flush JPEG frames to disk for encode", "cyan")
        batch = 8
        for start_i in range(0, expected_m, batch):
            payload = page.evaluate(
                """({ start, count }) => {
                  const out = [];
                  for (let i = 0; i < count; i += 1) {
                    const bytes = window.__flythroughJpegFrames[start + i];
                    let binary = "";
                    const chunk = 0x2000;
                    for (let j = 0; j < bytes.length; j += chunk) {
                      binary += String.fromCharCode.apply(
                        null, bytes.subarray(j, Math.min(bytes.length, j + chunk)),
                      );
                    }
                    out.push(btoa(binary));
                  }
                  return out;
                }""",
                {"start": start_i, "count": min(batch, expected_m - start_i)},
            )
            for offset, b64 in enumerate(payload):
                index = start_i + offset
                (frames_dir / f"frame-{index:05d}.jpg").write_bytes(base64.b64decode(b64))
        page.evaluate("() => { window.__flythroughJpegFrames = null; }")
        for label, index in (("start", 0), ("fork", first_fork), ("end", last)):
            src = frames_dir / f"frame-{index:05d}.jpg"
            dest = artifacts / f"{label}.jpg"
            shutil.copyfile(src, dest)
            cprint(f"artifact {dest}", "green")

        cprint("encode 720p through desktop encoder", "cyan")
        encode_script = tmp_pipeline / "encode_flythrough.js"
        out_mp4 = artifacts / "flythrough-720p.mp4"
        encode_script.write_text(
            "\n".join([
                '"use strict";',
                'const fs = require("node:fs");',
                'const path = require("node:path");',
                'const { resolveFfmpeg } = require(process.argv[2]);',
                'const { startEncoder } = require(process.argv[3]);',
                "async function main() {",
                "  const framesDir = process.argv[4];",
                "  const outPath = process.argv[5];",
                "  const expected = Number(process.argv[6]);",
                '  const ffmpegPath = resolveFfmpeg({ packaged: false, env: process.env });',
                "  const enc = startEncoder({",
                "    ffmpegPath, width: 1280, height: 720, fps: 30, outPath,",
                "  });",
                "  try {",
                "    for (let i = 0; i < expected; i += 1) {",
                '      const file = path.join(framesDir, `frame-${String(i).padStart(5, "0")}.jpg`);',
                "      await enc.writeFrame(fs.readFileSync(file));",
                "    }",
                "    await enc.finish();",
                "  } catch (err) {",
                "    try { await enc.cancel(); } catch (_) {}",
                "    throw err;",
                "  }",
                '  process.stdout.write("encoded\\n");',
                "}",
                "main().catch((err) => { console.error(err); process.exit(1); });",
                "",
            ])
        )
        encode = subprocess.run(
            [
                "node",
                str(encode_script),
                str(DESKTOP / "lib.js"),
                str(DESKTOP / "encoder.js"),
                str(frames_dir),
                str(out_mp4),
                str(expected_m),
            ],
            cwd=str(DESKTOP),
            capture_output=True,
            text=True,
            timeout=180,
            check=False,
        )
        if encode.returncode != 0:
            pytest.fail(f"encode failed\n{encode.stdout}\n{encode.stderr}")
        assert out_mp4.is_file()
        probe = subprocess.run(
            [
                "ffprobe", "-v", "error",
                "-select_streams", "v:0",
                "-show_entries", "stream=codec_name,avg_frame_rate,r_frame_rate,width,height,nb_frames,duration",
                "-show_entries", "format=duration",
                "-of", "json",
                str(out_mp4),
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        info = json.loads(probe.stdout)
        stream = info["streams"][0]
        duration = float(stream.get("duration") or info["format"]["duration"])
        expected_duration = expected_m / 30.0
        cprint(probe.stdout, "green")
        assert stream["codec_name"] == "h264"
        assert int(stream["width"]) == 1280
        assert int(stream["height"]) == 720
        rate = stream.get("avg_frame_rate") or stream.get("r_frame_rate")
        num, den = [int(part) for part in str(rate).split("/")]
        assert abs((num / den) - 30.0) < 0.05
        assert abs(duration - expected_duration) < 0.35
        cprint(
            f"ffprobe duration={duration:.3f}s expected≈{expected_duration:.3f}s "
            f"frames={expected_m}",
            "green",
        )
        browser.close()
