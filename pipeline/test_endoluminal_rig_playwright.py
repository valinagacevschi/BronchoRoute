# Changes: manual endoluminal roll, two clocks, and fork-label clearance.
from __future__ import annotations

import math
import os
import re
import shutil
import socket
import subprocess
import time
import urllib.request
from pathlib import Path

import pytest
from termcolor import cprint

from conftest import symlink_input_file
from route_frame import (
    apply_roll,
    clock_hour,
    patient_angle,
    patient_frame,
    rotate_hint,
    scope_angle,
    wrap_deg,
)
from test_viewer_v2_playwright import VIEWPORT, _playwright_or_skip

FORK_CLOCK = re.compile(
    r"^Fork \d+ · \d+ mm — patient \d+ o'clock · scope \d+ o'clock(?: · interp)?$"
)


def _child(fork, needle):
    for child in fork["children"]:
        if needle in (child["name"] or ""):
            return child
    return None


@pytest.fixture(scope="module")
def tmp_pipeline(tmp_path_factory):
    root = Path(__file__).resolve().parent
    case_src = root / "data/extracted/local/local-001/case.json"
    graph_src = root / "out/local-001/local-001_graph.json"
    if not case_src.is_file() or not graph_src.is_file():
        pytest.skip("local-001 graph is not in this checkout")
    tmp = tmp_path_factory.mktemp("endo_rig_pipeline")
    case_dst = tmp / "data/extracted/local/local-001/case.json"
    case_dst.parent.mkdir(parents=True)
    shutil.copyfile(case_src, case_dst)
    symlink_input_file(graph_src, tmp / "out/local-001/local-001_graph.json")
    (tmp / "ts_out").mkdir()
    (tmp / "app_state").mkdir()
    return tmp


@pytest.fixture(scope="module")
def server(tmp_pipeline):
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    base = f"http://127.0.0.1:{port}"
    home = tmp_pipeline / "fake_home"
    home.mkdir()
    env = {**os.environ, "HOME": str(home), "LUNGNAV_PIPELINE_ROOT": str(tmp_pipeline)}
    env.pop("PYTHONPATH", None)
    log_path = tmp_pipeline / "server.err"
    log = log_path.open("w")
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
                pytest.fail("endoluminal server exited before readiness")
            try:
                with urllib.request.urlopen(f"{base}/api/cases", timeout=1):
                    break
            except OSError:
                time.sleep(0.1)
        else:
            pytest.fail("endoluminal server did not start")
        yield base
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)
        log.close()


def _press(page, key, times):
    for _ in range(times):
        page.keyboard.press(key)


def _shot(page, shots, name):
    path = shots / name
    page.screenshot(path=str(path))
    cprint(str(path), "cyan")
    assert path.stat().st_size > 10_000
    return path


def test_manual_roll_and_clocks(server):
    sync_playwright = _playwright_or_skip()
    shots = Path(os.environ.get("TMPDIR", "/tmp")) / "bronchoroute-37"
    shots.mkdir(parents=True, exist_ok=True)
    cprint("open endoluminal local-001 on a free port", "cyan")
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            args=["--disable-background-timer-throttling"],
        )
        page = browser.new_page(viewport=VIEWPORT)
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.on(
            "console",
            lambda message: errors.append(message.text) if message.type == "error" else None,
        )
        page.goto(
            f"{server}/?case=local-001&variant=endoluminal&debug=1&allow_review=1",
            wait_until="domcontentloaded",
        )
        page.wait_for_function(
            """() => {
              const failed = document.body
                && document.body.innerText.startsWith("Could not load");
              const rig = window.__p2_debug && window.__p2_debug.rig
                && window.__p2_debug.rig();
              return failed || (rig && rig.rollText && rig.forkText);
            }""",
            timeout=120_000,
        )
        failed = page.evaluate(
            "() => document.body.innerText.startsWith('Could not load')"
        )
        assert not failed, "endoluminal payload did not load"
        assert not errors, errors
        _assert_formula_parity(page)
        _assert_local_survey(page)
        page.locator("canvas").click(position={"x": 800, "y": 160})
        _shot(page, shots, "roll_0.png")
        opened = page.evaluate("() => window.__p2_debug.rig()")
        assert opened["rollText"] == "Scope roll: 0°"
        assert abs(opened["phiDeg"]) < 0.5
        assert FORK_CLOCK.match(opened["forkText"])
        assert opened["patientClock"] == 3
        _assert_tick_clear(page, expect_visible=True)
        cprint("roll +15° and advance", "cyan")
        _press(page, "ArrowRight", 3)
        page.wait_for_function(
            """() => {
              const rig = window.__p2_debug.rig();
              return rig.rollText === "Scope roll: 15°";
            }"""
        )
        rolled = page.evaluate("() => window.__p2_debug.rig()")
        assert abs(rolled["phiDeg"] - 15) < 0.5
        page.keyboard.down("ArrowUp")
        page.wait_for_function(
            "() => window.__p2_debug.rig().s >= 6",
            timeout=10_000,
        )
        page.keyboard.up("ArrowUp")
        advanced = page.evaluate("() => window.__p2_debug.rig()")
        assert advanced["s"] < 25
        assert abs(advanced["phiDeg"] - 15) < 0.5
        assert advanced["rollText"] == "Scope roll: 15°"
        cprint("roll 90° in the trachea", "cyan")
        _press(page, "ArrowRight", 15)
        page.wait_for_function(
            """() => Math.abs(window.__p2_debug.rig().phiDeg - 90) < 0.5"""
        )
        _shot(page, shots, "roll_90.png")
        at_ninety = page.evaluate("() => window.__p2_debug.rig()")
        assert at_ninety["rollText"] == "Scope roll: 90°"
        assert at_ninety["s"] < 25
        _assert_tick_clear(page, expect_visible=False)
        _press(page, "ArrowLeft", 18)
        page.wait_for_function(
            """() => Math.abs(window.__p2_debug.rig().phiDeg) < 0.5"""
        )
        cprint("advance to the carina without auto-roll", "cyan")
        page.keyboard.down("ArrowUp")
        page.wait_for_function(
            """() => {
              const rig = window.__p2_debug.rig();
              return rig.nextPause != null && rig.s >= rig.nextPause - 1;
            }""",
            timeout=20_000,
        )
        page.keyboard.up("ArrowUp")
        # Manual ↑ does not take the 1s auto-play pause, so stop just short of the junction.
        arrived = page.evaluate("() => window.__p2_debug.rig()")
        assert arrived["forkText"].startswith("Fork 1 ")
        assert abs(arrived["phiDeg"]) < 0.5
        assert arrived["patientClock"] == 3
        assert arrived["scopeClock"] in (2, 3, 4)
        assert arrived["scopeClock"] != 12
        assert abs(arrived["routeRollDeg"] - arrived["phiDeg"]) > 40
        assert FORK_CLOCK.match(arrived["forkText"])
        assert "patient 3 o'clock" in arrived["forkText"]
        labels = _label_clearance(page)
        assert labels["n"] >= 2
        assert labels["hits"] == []
        cprint("roll the scope clock onto the branch", "cyan")
        aligned = arrived
        for _ in range(40):
            if aligned["aligned"]:
                break
            assert aligned["delta"] is not None
            key = "ArrowRight" if aligned["delta"] > 0 else "ArrowLeft"
            page.keyboard.press(key)
            page.wait_for_timeout(30)
            aligned = page.evaluate("() => window.__p2_debug.rig()")
        else:
            raise AssertionError(aligned["hint"])
        _shot(page, shots, "aligned_fork.png")
        assert aligned["hint"].startswith("aligned")
        assert aligned["forkText"].startswith("Fork 1 ")
        assert aligned["patientClock"] == 3
        assert aligned["scopeClock"] in (11, 12, 1)
        assert FORK_CLOCK.match(aligned["forkText"])
        aligned_labels = _label_clearance(page)
        assert aligned_labels["n"] >= 2
        assert aligned_labels["hits"] == []
        # Drag sets φ from the pointer, after the screenshots.
        dial = page.evaluate(
            """() => {
              const dial = window.__p2_debug.rig().scopeDial;
              const rect = document.querySelector("canvas").getBoundingClientRect();
              return {
                x: rect.left + dial.x,
                y: rect.top + dial.y,
                radius: dial.radius,
              };
            }"""
        )
        page.mouse.move(dial["x"], dial["y"] - dial["radius"] * 0.55)
        page.mouse.down()
        page.mouse.move(dial["x"], dial["y"] - dial["radius"] * 0.8, steps=6)
        page.mouse.up()
        page.wait_for_function(
            """() => Math.abs(window.__p2_debug.rig().phiDeg) < 8"""
        )
        dragged = page.evaluate("() => window.__p2_debug.rig()")
        assert abs(dragged["phiDeg"]) < 8
        assert not errors, errors
        browser.close()


def _assert_formula_parity(page):
    cprint("compare clock formulas with the page", "cyan")
    tangent = [0, 0, -1]
    anterior = [0, 1, 0]
    right = [1, 0, 0]
    rolled = apply_roll(anterior, right, math.pi / 2)
    psi = scope_angle(tangent, rolled)
    theta_anterior = patient_angle(tangent, anterior)
    theta_right = patient_angle(tangent, right)
    assert clock_hour(theta_anterior) == 12
    assert clock_hour(theta_right) == 3
    assert clock_hour(psi) == 3
    shift = wrap_deg(math.degrees((theta_anterior - psi) - (theta_anterior - scope_angle(tangent, anterior))))
    assert abs(shift + 90) < 1e-6
    report = page.evaluate(
        """() => {
          const api = window.__p2_debug;
          const tangent = [0, 0, -1];
          const anterior = [0, 1, 0];
          const right = [1, 0, 0];
          const rolled = api.applyRoll(anterior, right, Math.PI / 2);
          const psi = api.scopeAngle(tangent, rolled);
          const thetaAnterior = api.patientAngle(tangent, anterior);
          const thetaRight = api.patientAngle(tangent, right);
          const psi0 = api.scopeAngle(tangent, anterior);
          const shift = api.wrapDeg((thetaAnterior - psi) * 180 / Math.PI
            - (thetaAnterior - psi0) * 180 / Math.PI);
          const deltas = [15, -15, 16, -16, 180, -180, 181, -181];
          const wraps = [180, -180, 181, -181, 540, -540, 0];
          return {
            clockAnterior: api.clockHour(thetaAnterior),
            clockRight: api.clockHour(thetaRight),
            clockLeft: api.clockHour(api.patientAngle(tangent, [-1, 0, 0])),
            clockPosterior: api.clockHour(api.patientAngle(tangent, [0, -1, 0])),
            clockFifteen: api.clockHour(15 * Math.PI / 180),
            needle: api.clockHour(psi),
            shift,
            rolled,
            hints: deltas.map(delta => api.rotateHint(delta)),
            wraps: wraps.map(value => api.wrapDeg(value)),
            ahead: api.rotateHint(null, true),
            degenerateHint: api.rotateHint(null, false),
            straight: api.patientFrame([0, 1, 0]).degenerate,
            nearly: api.patientFrame([0, 1, 0.02]).degenerate,
            onEdge: api.patientFrame([0, Math.sqrt(1 - 0.01), 0.1]).degenerate,
            inside: api.patientFrame([0, Math.sqrt(1 - 0.099 * 0.099), 0.099]).degenerate,
          };
        }"""
    )
    assert report["clockAnterior"] == 12
    assert report["clockRight"] == 3
    assert report["clockLeft"] == 9
    assert report["clockPosterior"] == 6
    assert report["clockFifteen"] == 1
    assert report["needle"] == 3
    assert abs(report["shift"] + 90) < 1e-4
    for index, value in enumerate(rolled):
        assert abs(report["rolled"][index] - float(value)) < 1e-9
    deltas = [15, -15, 16, -16, 180, -180, 181, -181]
    assert report["hints"] == [rotate_hint(delta) for delta in deltas]
    wraps = [180, -180, 181, -181, 540, -540, 0]
    assert report["wraps"] == [wrap_deg(value) for value in wraps]
    assert report["ahead"] == "straight ahead"
    assert report["degenerateHint"] == "degenerate AP axis"
    assert report["straight"] is True
    assert report["nearly"] is True
    assert report["onEdge"] is False
    assert report["inside"] is True
    assert patient_frame([0.0, 1.0, 0.02])["degenerate"] is True
    edge = [0.0, math.sqrt(1.0 - 0.01), 0.1]
    assert patient_frame(edge)["degenerate"] is False


def _assert_local_survey(page):
    survey = page.evaluate("() => window.__p2_debug.forkSurvey()")
    carina = next(
        fork for fork in survey
        if _child(fork, "right main") and _child(fork, "left main")
    )
    right = _child(carina, "right main")
    left = _child(carina, "left main")
    assert right["clock"] == 3
    assert left["clock"] == 9
    assert right["ahead"] is False
    assert left["ahead"] is False
    cprint(
        f"local-001 fork {carina['fork']}: right main patient {right['clock']}, "
        f"left main patient {left['clock']}",
        "cyan",
    )
    takeoff = next(
        fork for fork in survey
        if any("intermedius" in (child["name"] or "") for child in fork["children"])
        and any("upper" in (child["name"] or "") for child in fork["children"])
    )
    intermedius = _child(takeoff, "intermedius")
    upper = _child(takeoff, "upper")
    assert intermedius["ahead"] is True
    assert intermedius["clock"] is None
    assert intermedius["inPlane"] < 0.15
    assert upper["ahead"] is False
    assert upper["clock"] in (2, 3, 4)
    assert upper["inPlane"] > 0.5
    cprint(
        "local-001 RUL takeoff: bronchus intermedius straight ahead "
        f"(in-plane {intermedius['inPlane']:.3f}); "
        f"right upper lobe bronchus patient {upper['clock']} "
        f"(in-plane {upper['inPlane']:.3f})",
        "cyan",
    )


def _assert_tick_clear(page, expect_visible):
    state = page.evaluate(
        """() => {
          const tick = window.__p2_debug.anteriorTick();
          const rig = window.__p2_debug.rig();
          return {tick, railRight: rig.railRight, panel: rig.panel};
        }"""
    )
    tick = state["tick"]
    panel = state["panel"]
    if expect_visible:
        assert tick["visible"] is True
    if not tick["visible"]:
        return
    assert tick["x"] > state["railRight"] + 4
    assert not _inside(tick["x"], tick["y"], panel)
    assert not _inside(tick["cx"], tick["cy"], panel)


def _inside(x, y, panel):
    if x is None or y is None:
        return False
    return panel["l"] < x < panel["r"] and panel["t"] < y < panel["b"]


def _label_clearance(page):
    return page.evaluate(
        """() => {
          const rig = window.__p2_debug.rig();
          const panel = rig.panel;
          const hits = [];
          for (const label of window.__p2_debug.forkLabels()) {
            const left = label.x - label.half;
            const right = label.x + label.half;
            const top = label.y - 8;
            const bottom = label.y + 8;
            const overlaps = right > panel.l && left < panel.r
              && bottom > panel.t && top < panel.b;
            const underRail = left < rig.railRight;
            if (overlaps || underRail) hits.push(label.text);
          }
          return {n: window.__p2_debug.forkLabels().length, hits};
        }"""
    )
