# Changes: unsnapped orifice edge-pill placement + roll follow (#60d);
# realism label scope-clock parity, collision avoid, schematic skip (#60b);
# auto-play pause, unavailable/404 fallback, roll clock parity (#60).
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

import repo_root

repo_root.ensure()

from app.realism.poses import fisheye_project, fork_poses, orifice_points  # noqa: E402
from test_app_server import _stamp_pipeline_version, _write_case_tree  # noqa: E402
from test_flythrough_playwright import _synthetic_graph  # noqa: E402
from test_viewer_v2_playwright import VIEWPORT, _playwright_or_skip  # noqa: E402

ROOT = Path(__file__).resolve().parent
SHOTS = Path(os.environ.get("TMPDIR", "/tmp")) / "bronchoroute-60b-realism-labels"


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
    cprint(f"seeded realism viewer case at {tmp}", "cyan")
    return case_path


def _write_route_target(case_path: Path, graph: dict, *, edge_id: int, arc_mm: float) -> None:
    from app.review import densify_centreline
    from app.route_target import CANDIDATE_STEP_MM, resolve_edge_sample

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


def _png_with_red_dot(u: float, v: float, size: int = 256) -> bytes:
    """256² RGB PNG; dark field with a saturated red disc at (u, v)."""
    import cv2

    img = np.zeros((size, size, 3), dtype=np.uint8)
    img[:, :] = (40, 20, 18)
    uu = int(round(u))
    vv = int(round(v))
    cv2.circle(img, (uu, vv), 6, (0, 0, 255), thickness=-1)  # BGR
    ok, buf = cv2.imencode(".png", img)
    assert ok
    return buf.tobytes()


def _fetch_payload(base: str) -> dict:
    url = f"{base}/api/cases/local-001/payload?allow_review=1"
    with urllib.request.urlopen(url, timeout=5) as res:
        return json.loads(res.read().decode())


def _on_route_uv(payload: dict) -> tuple[float, float, float, str]:
    poses = fork_poses(payload)
    assert poses, "payload has no fork poses"
    pose = poses[0]
    fork = payload["forks"][0]
    edges_full = [
        {**edge, "centreline_mm": edge.get("centreline_mm", edge.get("c"))}
        for edge in payload["edges"]
    ]
    rows = orifice_points(payload, fork, edges_full)
    on_route = next(row for row in rows if row["on_route"])
    u, v, inside = fisheye_project(
        on_route["point_ras_mm"],
        pose["position_ras_mm"],
        pose["forward"],
        pose["up"],
    )
    assert inside, (u, v)
    assert u == u and v == v
    name = next(
        (c.get("name") or "branch")
        for c in fork["children"]
        if c.get("on_route")
    )
    return float(pose["hold_s"]), float(u), float(v), name


@pytest.fixture
def server(tmp_path: Path):
    case_path = _seed(tmp_path)
    graph = _synthetic_graph()
    _write_route_target(case_path, graph, edge_id=3, arc_mm=8.0)
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    base = f"http://127.0.0.1:{port}"
    home = tmp_path / "fake_home"
    home.mkdir()
    env = {**os.environ, "HOME": str(home), "LUNGNAV_PIPELINE_ROOT": str(tmp_path)}
    env.pop("PYTHONPATH", None)
    env.pop("NODE_ENV", None)
    log_path = tmp_path / "server.err"
    log = log_path.open("w")
    cprint(f"realism viewer server on {port}", "cyan")
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
                pytest.fail(f"realism server exited\n{log_path.read_text()[-2000:]}")
            try:
                with urllib.request.urlopen(f"{base}/api/cases", timeout=1):
                    break
            except OSError:
                time.sleep(0.1)
        else:
            pytest.fail("realism server did not start")
        yield base, case_path, tmp_path
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)
        log.close()


def _clock_delta(a, b) -> int:
    if a is None or b is None:
        return 99
    return min((int(a) - int(b)) % 12, (int(b) - int(a)) % 12)


class _RealismMock:
    def __init__(
        self,
        *,
        hold_s: float,
        u: float,
        v: float,
        name: str,
        mode: str = "done",
        orifices: list[dict] | None = None,
    ):
        self.hold_s = float(hold_s)
        self.u = float(u)
        self.v = float(v)
        self.name = name
        self.mode = mode  # done | not_ready | unavailable | missing
        self.key = "testkey012345678"
        self.png = _png_with_red_dot(self.u, self.v)
        self.post_hits = 0
        self.get_hits = 0
        self.png_hits = 0
        self.orifices = orifices

    def body(self) -> dict:
        if self.mode == "unavailable":
            return {
                "status": "unavailable",
                "reason": "model hash mismatch",
                "key": None,
                "frames": [],
            }
        ready = self.mode == "done"
        if self.orifices is not None:
            orifices = self.orifices
        else:
            orifices = [{
                "fork": 1,
                "edge": 1,
                "on_route": True,
                "name": self.name,
                "u": self.u,
                "v": self.v,
                "inside": True,
            }]
        return {
            "status": "done" if ready else "running",
            "reason": None,
            "key": self.key,
            "frames": [{
                "group": 0,
                "forks": [1],
                "hold_s": self.hold_s,
                "ready": ready,
                "url": (
                    f"/api/cases/local-001/realism/{self.key}/group-0.png"
                    if ready else None
                ),
                "orifices": orifices,
            }],
        }

    def install(self, page) -> None:
        def handler(route):
            url = route.request.url
            method = route.request.method
            if self.mode == "missing":
                route.fulfill(status=404, body="not found")
                return
            if "/realism/start" in url and method == "POST":
                self.post_hits += 1
                route.fulfill(
                    status=200,
                    content_type="application/json",
                    body=json.dumps(self.body()),
                )
                return
            if "/realism/" in url and url.endswith(".png"):
                self.png_hits += 1
                if self.mode != "done":
                    route.fulfill(status=404, body="not ready")
                    return
                route.fulfill(status=200, content_type="image/png", body=self.png)
                return
            if url.rstrip("/").endswith("/realism") and method == "GET":
                self.get_hits += 1
                route.fulfill(
                    status=200,
                    content_type="application/json",
                    body=json.dumps(self.body()),
                )
                return
            route.continue_()

        page.route("**/api/cases/*/realism**", handler)


def _open(page, base: str) -> None:
    page.goto(
        f"{base}/renderer.template.html?case=local-001&allow_review=1&debug=1&variant=endoluminal",
        wait_until="domcontentloaded",
    )
    page.wait_for_function(
        """() => window.__p2_debug
          && window.__realism
          && document.getElementById('c').clientWidth > 100
          && window.AIRWAY
          && Array.isArray(window.AIRWAY.fly && window.AIRWAY.fly.s)
          && window.AIRWAY.fly.s.length > 1""",
        timeout=120_000,
    )
    page.wait_for_function("() => document.fonts.status === 'loaded'")
    page.evaluate("() => window.__p2_debug.setVariant(1)")


def _wait_shown(page, group: int = 0, timeout: float = 10_000) -> None:
    page.wait_for_function(
        """(g) => window.__realism
          && window.__realism.on
          && window.__realism.shownGroup === g
          && window.__realism.opacity > 0.9""",
        arg=group,
        timeout=timeout,
    )


def _shot(page, name: str) -> Path:
    SHOTS.mkdir(parents=True, exist_ok=True)
    path = SHOTS / name
    page.screenshot(path=str(path))
    cprint(str(path), "cyan")
    assert path.stat().st_size > 5_000
    return path


def _label_clock_state(page) -> dict:
    return page.evaluate(
        """() => {
          const orifices = window.__realism.lastOrifices || [];
          const labels = window.__realism.lastLabels || [];
          const on = orifices.find(o => o.on_route) || orifices[0];
          const lab = labels.find(l => l.on_route) || labels[0];
          const guidance = window.__p2_debug.rig();
          return {
            orifice: on,
            label: lab,
            scopeClock: guidance && guidance.scopeClock,
            orificeClock: on ? on.clock : null,
            labelClock: lab ? lab.clock : null,
            phi: (window.__p2_debug.endoCamera() || {}).phi,
            drawn: window.__p2_debug.forkOrificesDrawn(),
            skips: window.__p2_debug.forkOrificesSkipCount(),
            opacity: window.__realism.opacity,
          };
        }"""
    )


def test_realism_hold_toggle_autoplay_roll_and_fallback(server):
    os.environ.pop("NODE_ENV", None)
    sync_playwright = _playwright_or_skip()
    base, _case_path, _tmp = server
    cprint("fetch payload + fisheye orifice for mock frame", "cyan")
    payload = _fetch_payload(base)
    hold_s, u, v, name = _on_route_uv(payload)
    cprint(f"hold_s={hold_s:.2f} orifice u={u:.1f} v={v:.1f} name={name}", "yellow")

    mock = _RealismMock(hold_s=hold_s, u=u, v=v, name=name, mode="done")
    errors: list[str] = []

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            args=["--disable-background-timer-throttling"],
        )
        page = browser.new_page(viewport=VIEWPORT)
        page.on("pageerror", lambda err: errors.append(str(err)))
        page.on(
            "console",
            lambda msg: errors.append(msg.text) if msg.type == "error" else None,
        )
        mock.install(page)
        _open(page, base)
        page.wait_for_function(
            "() => window.__realism && window.__realism.state === 'done'",
            timeout=10_000,
        )
        assert mock.post_hits >= 1

        cprint("frame shows within ±2 mm of hold_s", "cyan")
        page.evaluate(
            """(s) => window.__p2_debug.setFly({ s, phi: 0, playing: false, pausedFork: null })""",
            hold_s,
        )
        page.evaluate("(s) => window.__p2_debug.setFlyS(s)", hold_s)
        _wait_shown(page, 0)
        info = page.evaluate(
            """() => ({
              state: window.__realism.state,
              shown: window.__realism.shownGroup,
              on: window.__realism.on,
              s: (window.__p2_debug.endoCamera() || {}).s,
              opacity: window.__realism.opacity,
            })"""
        )
        assert info["shown"] == 0
        assert info["on"] is True
        assert abs(info["s"] - hold_s) <= 2.0
        _shot(page, "realism-roll0.png")

        cprint("roll 0°: on-route label clock equals HUD scope clock", "cyan")
        page.wait_for_timeout(300)
        roll0 = _label_clock_state(page)
        assert roll0["scopeClock"] is not None, roll0
        assert roll0["orificeClock"] == roll0["scopeClock"], roll0
        assert roll0["labelClock"] == roll0["scopeClock"], roll0
        assert roll0["drawn"] is False, roll0
        assert roll0["skips"] >= 1, roll0
        cprint(
            f"  roll0 scope={roll0['scopeClock']} label={roll0['labelClock']} "
            f"skips={roll0['skips']}",
            "green",
        )

        cprint("outside ±2 mm → schematic (shownGroup null)", "cyan")
        page.evaluate("(s) => window.__p2_debug.setFlyS(s)", hold_s + 5.0)
        page.wait_for_function(
            "() => window.__realism.shownGroup === null && window.__realism.opacity < 0.1",
            timeout=5_000,
        )

        cprint("V toggles realism off at hold", "cyan")
        page.evaluate("(s) => window.__p2_debug.setFlyS(s)", hold_s)
        _wait_shown(page, 0)
        page.keyboard.press("v")
        page.wait_for_function(
            "() => window.__realism.on === false && window.__realism.shownGroup === null",
            timeout=5_000,
        )
        _shot(page, "realism-toggled-off.png")
        page.keyboard.press("v")
        page.wait_for_function("() => window.__realism.on === true", timeout=5_000)
        _wait_shown(page, 0)

        cprint("auto-play stops at hold_s when ready frame exists", "cyan")
        page.keyboard.press("t")  # reset + re-arm realism holds
        page.evaluate(
            """(s) => window.__p2_debug.setFly({
              s: Math.max(0, s - 3),
              playing: true,
              pausedFork: null,
              phi: 0,
            })""",
            hold_s,
        )
        page.wait_for_function(
            """(s) => {
              const cam = window.__p2_debug.endoCamera();
              const st = window.__p2_debug.flyState();
              return cam && Math.abs(cam.s - s) < 0.05 && st && st.playing === false;
            }""",
            arg=hold_s,
            timeout=30_000,
        )
        at = page.evaluate("() => (window.__p2_debug.endoCamera() || {}).s")
        assert abs(at - hold_s) < 0.05, (at, hold_s)

        cprint("roll 90°: label clock equals HUD scope clock exactly", "cyan")
        page.evaluate(
            """(s) => {
              window.__p2_debug.setFly({
                s, playing: false, pausedFork: null, phi: Math.PI / 2,
              });
              return window.__p2_debug.setFlyS(s);
            }""",
            hold_s,
        )
        _wait_shown(page, 0)
        page.wait_for_timeout(300)
        phi = page.evaluate("() => (window.__p2_debug.endoCamera() || {}).phi")
        assert abs(phi - math.pi / 2) < 1e-6, phi
        roll = page.evaluate(
            """() => {
              const cv = document.getElementById('c');
              const ctx = cv.getContext('2d');
              const w = cv.width, h = cv.height;
              const data = ctx.getImageData(0, 0, w, h).data;
              let best = null;
              for (let y = 0; y < h; y++) {
                for (let x = 0; x < w; x++) {
                  const i = (y * w + x) * 4;
                  const r = data[i], g = data[i + 1], b = data[i + 2];
                  if (r < 200 || g > 80 || b > 80) continue;
                  const score = r - g - b;
                  if (!best || score > best.score) best = { x, y, score, r, g, b };
                }
              }
              const orifices = window.__realism.lastOrifices || [];
              const labels = window.__realism.lastLabels || [];
              const on = orifices.find(o => o.on_route) || orifices[0];
              const lab = labels.find(l => l.on_route) || labels[0];
              const guidance = window.__p2_debug.rig();
              const cx = w / 2, cy = h / 2;
              const clockOf = (x, y) => {
                const ang = Math.atan2(x - cx, -(y - cy));
                const hours = Math.round(ang * 6 / Math.PI);
                const hour = ((hours % 12) + 12) % 12;
                return hour || 12;
              };
              return {
                red: best,
                orifice: on,
                label: lab,
                scopeClock: guidance && guidance.scopeClock,
                redClock: best ? clockOf(best.x, best.y) : null,
                orificeClock: on ? on.clock : null,
                labelClock: lab ? lab.clock : null,
                drawn: window.__p2_debug.forkOrificesDrawn(),
                skips: window.__p2_debug.forkOrificesSkipCount(),
                phi: (window.__p2_debug.endoCamera() || {}).phi,
              };
            }"""
        )
        assert roll["red"] is not None, roll
        assert roll["orifice"] is not None, roll
        assert roll["label"] is not None, roll
        dist_orifice = math.hypot(
            roll["red"]["x"] - roll["orifice"]["x"],
            roll["red"]["y"] - roll["orifice"]["y"],
        )
        dist_label = math.hypot(
            roll["red"]["x"] - roll["label"]["x"],
            roll["red"]["y"] - roll["label"]["y"],
        )
        assert dist_orifice < 20, (dist_orifice, roll)
        assert dist_label > 10, (dist_label, roll)
        assert _clock_delta(roll["redClock"], roll["scopeClock"]) <= 1, roll
        assert roll["orificeClock"] == roll["scopeClock"], roll
        assert roll["labelClock"] == roll["scopeClock"], roll
        assert roll["drawn"] is False, roll
        assert roll["skips"] >= 1, roll
        _shot(page, "realism-roll90.png")
        assert not errors, errors
        browser.close()

    cprint("fallback: not ready / unavailable / 404 → schematic, no console errors", "cyan")

    def _is_benign_404(text: str) -> bool:
        # Chromium logs fetch/XHR 404 as console "error" even when JS handles res.ok.
        return "Failed to load resource" in text and "404" in text

    for mode in ("not_ready", "unavailable", "missing"):
        errors2: list[str] = []
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(
                args=["--disable-background-timer-throttling"],
            )
            page = browser.new_page(viewport=VIEWPORT)
            page.on("pageerror", lambda err: errors2.append(str(err)))
            page.on(
                "console",
                lambda msg: (
                    errors2.append(msg.text)
                    if msg.type == "error" and not _is_benign_404(msg.text)
                    else None
                ),
            )
            fb = _RealismMock(hold_s=hold_s, u=u, v=v, name=name, mode=mode)
            fb.install(page)
            _open(page, base)
            page.wait_for_timeout(600)
            page.evaluate("(s) => window.__p2_debug.setFlyS(s)", hold_s)
            page.wait_for_timeout(400)
            shown = page.evaluate(
                """() => ({
                  shown: window.__realism.shownGroup,
                  on: window.__realism.on,
                  state: window.__realism.state,
                  opacity: window.__realism.opacity,
                })"""
            )
            assert shown["shown"] is None, (mode, shown)
            assert shown["opacity"] < 0.1, (mode, shown)
            if mode == "missing":
                assert shown["state"] == "off", shown
            assert not errors2, (mode, errors2)
            cprint(f"  {mode}: schematic ok state={shown['state']}", "green")
            browser.close()


def test_realism_label_collision_and_schematic_skip(server):
    """Two on-route orifices 6 px apart must not produce intersecting label boxes."""
    os.environ.pop("NODE_ENV", None)
    sync_playwright = _playwright_or_skip()
    base, _case_path, _tmp = server
    cprint("collision mock: two on-route orifices 6 px apart", "cyan")
    payload = _fetch_payload(base)
    hold_s, u, v, name = _on_route_uv(payload)
    # ~6 px on the endoluminal circle: fisheye δu ≈ 6 * 128 / R, R≈0.47*min(W,H).
    min_side = min(VIEWPORT["width"], VIEWPORT["height"])
    r_px = 0.47 * min_side
    du = 6.0 * 128.0 / r_px
    orifices = [
        {
            "fork": 1,
            "edge": 1,
            "on_route": True,
            "name": "right lower lobe",
            "u": u,
            "v": v,
            "inside": True,
        },
        {
            "fork": 1,
            "edge": 2,
            "on_route": True,
            "name": "right lower lobe",
            "u": u + du,
            "v": v,
            "inside": True,
        },
    ]
    mock = _RealismMock(
        hold_s=hold_s, u=u, v=v, name=name, mode="done", orifices=orifices,
    )
    errors: list[str] = []

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            args=["--disable-background-timer-throttling"],
        )
        page = browser.new_page(viewport=VIEWPORT)
        page.on("pageerror", lambda err: errors.append(str(err)))
        page.on(
            "console",
            lambda msg: errors.append(msg.text) if msg.type == "error" else None,
        )
        mock.install(page)
        _open(page, base)
        page.wait_for_function(
            "() => window.__realism && window.__realism.state === 'done'",
            timeout=10_000,
        )
        page.evaluate(
            """(s) => window.__p2_debug.setFly({ s, phi: 0, playing: false, pausedFork: null })""",
            hold_s,
        )
        page.evaluate("(s) => window.__p2_debug.setFlyS(s)", hold_s)
        _wait_shown(page, 0)
        page.wait_for_timeout(350)

        report = page.evaluate(
            """() => {
              const orifices = window.__realism.lastOrifices || [];
              const labels = window.__realism.lastLabels || [];
              let orificeDist = null;
              if (orifices.length >= 2) {
                orificeDist = Math.hypot(
                  orifices[1].x - orifices[0].x,
                  orifices[1].y - orifices[0].y,
                );
              }
              const pairs = [];
              for (let i = 0; i < labels.length; i++) {
                for (let j = i + 1; j < labels.length; j++) {
                  const a = labels[i], b = labels[j];
                  const clear = a.right + 14 <= b.left || b.right + 14 <= a.left
                    || a.bottom + 14 <= b.top || b.bottom + 14 <= a.top;
                  const intersect = !(a.right <= b.left || b.right <= a.left
                    || a.bottom <= b.top || b.bottom <= a.top);
                  pairs.push({ i, j, clear, intersect, a, b });
                }
              }
              return {
                orificeCount: orifices.length,
                labelCount: labels.length,
                orificeDist,
                pairs,
                drawn: window.__p2_debug.forkOrificesDrawn(),
                skips: window.__p2_debug.forkOrificesSkipCount(),
                opacity: window.__realism.opacity,
                labels,
              };
            }"""
        )
        assert report["orificeCount"] == 2, report
        assert report["labelCount"] == 2, report
        assert report["orificeDist"] is not None
        assert 3.0 <= report["orificeDist"] <= 12.0, report
        assert report["pairs"], report
        for pair in report["pairs"]:
            assert pair["intersect"] is False, pair
            assert pair["clear"] is True, pair
        assert report["drawn"] is False, report
        assert report["skips"] >= 1, report
        assert report["opacity"] > 0.5, report
        _shot(page, "realism-label-collision.png")
        cprint(
            f"  orificeDist={report['orificeDist']:.1f}px "
            f"skips={report['skips']} labels clear",
            "green",
        )
        assert not errors, errors
        browser.close()


def _angle_deg_from_centre(x: float, y: float, cx: float, cy: float) -> float:
    return math.degrees(math.atan2(x - cx, -(y - cy)))


def _clock_angle_deg(clock: int) -> float:
    return (int(clock) % 12) * 30.0


def _angle_delta_deg(a: float, b: float) -> float:
    return abs((a - b + 180.0) % 360.0 - 180.0)


def test_realism_unsnapped_edge_pills(server):
    """snapped:false → no outline; pill at HUD-clock circle edge; rolls with clock."""
    os.environ.pop("NODE_ENV", None)
    sync_playwright = _playwright_or_skip()
    base, _case_path, _tmp = server
    cprint("edge-pill mock: one snapped outline + one unsnapped on-route", "cyan")
    payload = _fetch_payload(base)
    hold_s, u, v, name = _on_route_uv(payload)
    orifices = [
        {
            "fork": 1,
            "edge": 2,
            "on_route": False,
            "name": "visible opening",
            "u": u,
            "v": v,
            "inside": True,
            "snapped": True,
        },
        {
            "fork": 1,
            "edge": 1,
            "on_route": True,
            "name": "unplaced branch",
            "u": u + 30.0,
            "v": v,
            "inside": True,
            "snapped": False,
        },
    ]
    mock = _RealismMock(
        hold_s=hold_s, u=u, v=v, name=name, mode="done", orifices=orifices,
    )
    errors: list[str] = []

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            args=["--disable-background-timer-throttling"],
        )
        page = browser.new_page(viewport=VIEWPORT)
        page.on("pageerror", lambda err: errors.append(str(err)))
        page.on(
            "console",
            lambda msg: errors.append(msg.text) if msg.type == "error" else None,
        )
        mock.install(page)
        _open(page, base)
        page.wait_for_function(
            "() => window.__realism && window.__realism.state === 'done'",
            timeout=10_000,
        )
        page.evaluate(
            """(s) => window.__p2_debug.setFly({ s, phi: 0, playing: false, pausedFork: null })""",
            hold_s,
        )
        page.evaluate("(s) => window.__p2_debug.setFlyS(s)", hold_s)
        _wait_shown(page, 0)
        page.wait_for_timeout(350)

        def _edge_report():
            return page.evaluate(
                """() => {
                  const geom = (() => {
                    const cv = document.getElementById('c');
                    const W = cv.width, H = cv.height;
                    const diameter = 0.94 * Math.min(W, H);
                    return { cx: W / 2, cy: H / 2, R: diameter / 2, W, H };
                  })();
                  const orifices = window.__realism.lastOrifices || [];
                  const labels = window.__realism.lastLabels || [];
                  const snapped = orifices.find(o => o.snapped === true);
                  const unsnapped = orifices.find(o => o.snapped === false);
                  const edgeLab = labels.find(l => l.edgePill)
                    || (unsnapped && labels.find(l => l.on_route));
                  const guidance = window.__p2_debug.rig();
                  return {
                    cx: geom.cx,
                    cy: geom.cy,
                    R: geom.R,
                    scopeClock: guidance && guidance.scopeClock,
                    snapped,
                    unsnapped,
                    edgeLab,
                    orificeCount: orifices.length,
                    labels,
                  };
                }"""
            )

        report0 = _edge_report()
        assert report0["orificeCount"] == 2, report0
        assert report0["snapped"] is not None, report0
        assert report0["unsnapped"] is not None, report0
        assert report0["snapped"]["outlined"] is True, report0
        assert report0["unsnapped"]["outlined"] is False, report0
        assert report0["edgeLab"] is not None, report0
        assert report0["unsnapped"]["clock"] == report0["scopeClock"], report0
        assert report0["edgeLab"]["clock"] == report0["scopeClock"], report0

        pill = report0["edgeLab"]
        cx, cy, R = report0["cx"], report0["cy"], report0["R"]
        r = math.hypot(pill["x"] - cx, pill["y"] - cy)
        half = math.hypot(pill["w"] / 2, pill["h"] / 2)
        # Rule places centre at R - halfDiag - 6; outer corner then sits ~6 px inside rim.
        expected_r = max(0.0, R - half - 6.0)
        edge_gap = abs(r - expected_r)
        assert edge_gap < 40.0, (edge_gap, r, expected_r, R, half, report0)
        assert (R - r) < 40.0 + half, (R - r, half, report0)
        clock0 = int(report0["scopeClock"])
        ang0 = _angle_deg_from_centre(pill["x"], pill["y"], cx, cy)
        assert _angle_delta_deg(ang0, _clock_angle_deg(clock0)) <= 20.0, (
            ang0, clock0, report0,
        )
        cprint(
            f"  roll0 clock={clock0} r={r:.1f} expected={expected_r:.1f} "
            f"R={R:.1f} gap={edge_gap:.1f} ang={ang0:.1f}°",
            "green",
        )
        _shot(page, "realism-edge-pill-roll0.png")

        cprint("roll 90°: edge pill moves with HUD clock", "cyan")
        page.evaluate(
            """(s) => {
              window.__p2_debug.setFly({
                s, playing: false, pausedFork: null, phi: Math.PI / 2,
              });
              return window.__p2_debug.setFlyS(s);
            }""",
            hold_s,
        )
        _wait_shown(page, 0)
        page.wait_for_timeout(350)
        report90 = _edge_report()
        assert report90["unsnapped"] is not None, report90
        assert report90["unsnapped"]["outlined"] is False, report90
        assert report90["edgeLab"] is not None, report90
        clock90 = report90["scopeClock"]
        assert clock90 is not None, report90
        assert report90["unsnapped"]["clock"] == clock90, report90
        assert clock90 != clock0, (clock0, clock90, report90)
        pill90 = report90["edgeLab"]
        r90 = math.hypot(pill90["x"] - cx, pill90["y"] - cy)
        half90 = math.hypot(pill90["w"] / 2, pill90["h"] / 2)
        expected_r90 = max(0.0, R - half90 - 6.0)
        assert abs(r90 - expected_r90) < 40.0, (r90, expected_r90, R, report90)
        ang90 = _angle_deg_from_centre(pill90["x"], pill90["y"], cx, cy)
        assert _angle_delta_deg(ang90, _clock_angle_deg(int(clock90))) <= 20.0, (
            ang90, clock90, report90,
        )
        moved = math.hypot(pill90["x"] - pill["x"], pill90["y"] - pill["y"])
        assert moved > 20.0, (moved, pill, pill90)
        cprint(
            f"  roll90 clock={clock90} ang={ang90:.1f}° moved={moved:.1f}px",
            "green",
        )
        _shot(page, "realism-edge-pill-roll90.png")
        assert not errors, errors
        browser.close()


def test_template_splice_still_holds():
    root = Path(__file__).resolve().parents[1] / "prototype"
    template = (root / "renderer.template.html").read_text()
    rendered = (root / "renderer.html").read_text()
    prefix, suffix = template.split("/*__AIRWAY_EMBED__*/")
    assert rendered.startswith(prefix)
    assert rendered.endswith(suffix)
