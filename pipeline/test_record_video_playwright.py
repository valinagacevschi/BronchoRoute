# Changes: video frame size, burn-in match, letterbox background, and the 30 Hz scheduler.
"""Live Record frames for anatomical, topology, and endoluminal views."""

from __future__ import annotations

import base64
import json
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

from test_viewer_v2_playwright import VIEWPORT, _playwright_or_skip

REVIEW_LINE = "Left upper lobe: no airways found in this lobe"
PATIENT_NAME = "ZZZ-PATIENT-NAME"
PATIENT_ID = "ZZZ-PATIENT-ID"
STUDY_DATE = "19990315"
FOOTER = "Simulated · CT-derived · research use"
VIEWS = (
    ("anatomical", 0, "Anatomical 3D", True),
    ("topology", 2, "Topology map", False),
    ("endoluminal", 1, "Endoluminal", False),
)

GRAB = r"""
async (spec) => {
  window.__p2_debug.setVariant(spec.index);
  const when = new Date(2026, 10, 2, 21, 40, 30);
  await window.__p2_debug.exportStill({ hud: true, lesion: true, when });
  const stillBurn = window.__p2_debug.burnIn();
  const ground = getComputedStyle(document.documentElement).getPropertyValue("--ground").trim();
  const parse = (value) => {
    const text = String(value || "").trim();
    const hex = text.match(/^#([0-9a-fA-F]{6})$/);
    if (hex) {
      const n = parseInt(hex[1], 16);
      return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
    }
    const rgb = text.match(/^rgba?\(\s*([\d.]+)[,\s]+([\d.]+)[,\s]+([\d.]+)/);
    return rgb ? [Number(rgb[1]), Number(rgb[2]), Number(rgb[3])] : null;
  };
  const groundRgb = parse(ground);
  async function grab(width, height, keepBytes) {
    const source = window.__p2_debug.createVideoFrameSource({
      view: spec.view, width, height, hud: true, lesion: true,
    });
    const bytes = await source.frame(when);
    const burn = window.__p2_debug.burnIn();
    const bitmap = await createImageBitmap(new Blob([bytes], { type: "image/jpeg" }));
    const canvas = document.createElement("canvas");
    canvas.width = bitmap.width;
    canvas.height = bitmap.height;
    const ctx = canvas.getContext("2d", { willReadFrequently: true });
    ctx.drawImage(bitmap, 0, 0);
    const px = ctx.getImageData(0, 0, 1, 1).data;
    let b64 = null;
    if (keepBytes) {
      let binary = "";
      const chunk = 0x2000;
      for (let i = 0; i < bytes.length; i += chunk) {
        binary += String.fromCharCode.apply(
          null, bytes.subarray(i, Math.min(bytes.length, i + chunk)),
        );
      }
      b64 = btoa(binary);
    }
    const corner = [px[0], px[1], px[2]];
    const delta = (rgb) => Math.max(
      Math.abs(corner[0] - rgb[0]),
      Math.abs(corner[1] - rgb[1]),
      Math.abs(corner[2] - rgb[2]),
    );
    return {
      width: bitmap.width,
      height: bitmap.height,
      byteLength: bytes.length,
      signature: [bytes[0], bytes[1], bytes[2]],
      burn,
      corner,
      deltaGround: groundRgb ? delta(groundRgb) : null,
      deltaBlack: Math.max(corner[0], corner[1], corner[2]),
      b64,
    };
  }
  return {
    stillBurn,
    ground,
    hd: await grab(1920, 1080, true),
    sd: await grab(1280, 720, false),
  };
}
"""

SCHEDULER = r"""
async () => {
  async function flush() {
    await Promise.resolve();
    await Promise.resolve();
    await Promise.resolve();
  }
  const frames = [];
  let resolveDraw = null;
  let draws = 0;
  let auto = null;
  const scheduler = window.__p2_debug.createRecordScheduler({
    manual: true,
    fps: 30,
    draw() {
      draws += 1;
      const id = draws;
      return new Promise((resolve) => { resolveDraw = () => resolve({ id }); });
    },
    onFrame(frame, info) {
      frames.push({ id: frame.id, duplicate: info.duplicate, index: info.index });
    },
    onAutoStop(message) { auto = message; },
  });
  scheduler.start();
  await flush();
  resolveDraw();
  await flush();
  const first = scheduler.advance(1000);
  await flush();
  const midDraws = draws;
  const second = scheduler.advance(1000);
  const slow = {
    emitted: frames.length,
    duplicates: frames.filter((frame) => frame.duplicate).length,
    ids: [...new Set(frames.map((frame) => frame.id))],
    firstDuplicate: frames[0] ? frames[0].duplicate : null,
    laterDuplicate: frames[1] ? frames[1].duplicate : null,
    indicesOk: frames.every((frame, index) => frame.index === index),
    draws,
    midDraws,
    firstStopped: first.stopped,
    secondStopped: second.stopped,
    secondEmitted: second.emitted,
    auto,
  };

  const limited = [];
  let release = null;
  let limitMessage = null;
  const limit = window.__p2_debug.createRecordScheduler({
    manual: true,
    fps: 30,
    draw() {
      return new Promise((resolve) => { release = () => resolve({ id: 1 }); });
    },
    onFrame(_frame, info) { limited.push(info.index); },
    onAutoStop(message) { limitMessage = message; },
  });
  limit.start();
  await flush();
  release();
  await flush();
  const atEnd = limit.advance(10 * 60 * 1000);
  const after = limit.advance(1000);
  return {
    slow,
    limit: {
      emitted: limited.length,
      first: limited[0],
      last: limited[limited.length - 1],
      message: limitMessage,
      stopped: atEnd.stopped,
      after: after.emitted,
    },
  };
}
"""


def _seed(tmp: Path) -> None:
    root = Path(__file__).resolve().parent
    case_src = root / "data/extracted/local/local-001/case.json"
    graph_src = root / "out/local-001/local-001_graph.json"
    if not case_src.is_file() or not graph_src.is_file():
        pytest.skip("local-001 graph is not in this checkout")
    case_dst = tmp / "data/extracted/local/local-001/case.json"
    graph_dst = tmp / "out/local-001/local-001_graph.json"
    case_dst.parent.mkdir(parents=True)
    graph_dst.parent.mkdir(parents=True)
    shutil.copyfile(case_src, case_dst)
    shutil.copyfile(graph_src, graph_dst)
    case = json.loads(case_dst.read_text())
    case["patient_name"] = PATIENT_NAME
    case["patient_id"] = PATIENT_ID
    case["study_date"] = STUDY_DATE
    case_dst.write_text(json.dumps(case, indent=2) + "\n")
    graph = json.loads(graph_dst.read_text())
    graph["review"] = [{"kind": "lobe_without_airways", "lobe": "left upper lobe"}]
    graph_dst.write_text(json.dumps(graph))
    (tmp / "ts_out").mkdir()
    (tmp / "app_state").mkdir()
    cprint(f"seeded tmp case at {tmp}", "cyan")


@pytest.fixture
def server(tmp_path: Path):
    _seed(tmp_path)
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
    cprint(f"record server on {port}", "cyan")
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
                pytest.fail(f"record server exited\n{log_path.read_text()[-2000:]}")
            try:
                with urllib.request.urlopen(f"{base}/api/cases", timeout=1):
                    break
            except OSError:
                time.sleep(0.1)
        else:
            pytest.fail("record server did not start")
        yield base
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)
        log.close()


def _assert_same_burn(still: dict, video: dict, view_name: str, scale: bool) -> None:
    assert video["lines"] == still["lines"]
    assert video["viewName"] == still["viewName"] == view_name
    assert video["caseId"] == still["caseId"] == "local-001"
    assert video["date"] == still["date"] == "2026-11-02 21:40"
    assert video["footer"] == still["footer"] == FOOTER
    assert video["review"] == still["review"] == [REVIEW_LINE]
    assert video["scaleBar"] == still["scaleBar"]
    if scale:
        assert re.fullmatch(r"(10|20|50) mm", video["scaleBar"])
    else:
        assert video["scaleBar"] is None
    blob = "\n".join(video["lines"])
    for secret in (PATIENT_NAME, PATIENT_ID, STUDY_DATE, "1999-03-15"):
        assert secret not in blob


def test_record_frames_and_scheduler(server: str):
    sync_playwright = _playwright_or_skip()
    shots = Path(os.environ.get("TMPDIR", "/tmp")) / "bronchoroute-43"
    shots.mkdir(parents=True, exist_ok=True)
    cprint(f"artifacts in {shots}", "cyan")
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(args=["--disable-background-timer-throttling"])
        context = browser.new_context(viewport=VIEWPORT)
        page = context.new_page()
        page.set_default_timeout(120_000)
        page.on("pageerror", lambda err: cprint(f"pageerror {err}", "red"))
        cprint("open anatomical local-001", "cyan")
        page.goto(
            f"{server}/renderer.template.html?case=local-001&allow_review=1&debug=1&variant=anatomical",
            wait_until="domcontentloaded",
        )
        try:
            page.wait_for_function(
                """() => window.__p2_debug
                  && document.getElementById('c').clientWidth > 100
                  && [...document.querySelectorAll('#review-warnings-list li')]
                    .some(li => li.textContent === 'Left upper lobe: no airways found in this lobe')""",
            )
        except Exception:
            cprint(page.locator("body").inner_text()[:800], "red")
            raise
        page.wait_for_function("() => document.fonts.status === 'loaded'")
        hidden = page.evaluate(
            """() => ({
              record: document.getElementById("record-btn").hidden,
              stop: document.getElementById("record-stop").hidden,
              save: document.getElementById("save-image").hidden,
            })"""
        )
        cprint(f"browser record chrome {hidden}", "cyan")
        assert hidden == {
            "record": True,
            "stop": True,
            "save": False,
        }
        for key, index, view_name, scale in VIEWS:
            cprint(f"frame {key}", "cyan")
            info = page.evaluate(GRAB, {"index": index, "view": key})
            for label, frame, width, height in (
                ("hd", info["hd"], 1920, 1080),
                ("sd", info["sd"], 1280, 720),
            ):
                assert frame["width"] == width and frame["height"] == height, label
                assert frame["signature"] == [0xFF, 0xD8, 0xFF]
                assert 0 < frame["byteLength"] <= 5 * 1024 * 1024
                assert frame["deltaGround"] is not None
                assert frame["deltaGround"] <= 20, frame
                assert frame["deltaGround"] < frame["deltaBlack"] or frame["deltaBlack"] < 8
                _assert_same_burn(info["stillBurn"], frame["burn"], view_name, scale)
            raw = base64.b64decode(info["hd"]["b64"])
            assert raw[:3] == b"\xff\xd8\xff"
            dest = shots / f"frame-{key}.jpg"
            dest.write_bytes(raw)
            cprint(f"{dest} {info['hd']['width']}x{info['hd']['height']}", "green")
        cprint("hide Record on CT panes", "cyan")
        flags = page.evaluate(
            """() => {
              const hidden = (id) => document.getElementById(id).hidden;
              window.__p2_debug.setVariant(3);
              const ct = { save: hidden("save-image"), record: hidden("record-btn") };
              window.__p2_debug.setVariant(0);
              return { ct, anatomical: { save: hidden("save-image"), record: hidden("record-btn") } };
            }"""
        )
        assert flags == {
            "ct": {"save": True, "record": True},
            "anatomical": {"save": False, "record": True},
        }
        cprint("30 Hz scheduler with a slow draw", "cyan")
        clock = page.evaluate(SCHEDULER)
        slow = clock["slow"]
        cprint(
            f"slow emitted={slow['emitted']} duplicates={slow['duplicates']} draws={slow['draws']}",
            "cyan",
        )
        assert slow["emitted"] == 60
        assert slow["secondEmitted"] == 60
        assert slow["duplicates"] == 59
        assert slow["ids"] == [1]
        assert slow["firstDuplicate"] is False
        assert slow["laterDuplicate"] is True
        assert slow["indicesOk"] is True
        assert slow["draws"] == 2
        assert slow["midDraws"] == 2
        assert slow["firstStopped"] is False
        assert slow["secondStopped"] is False
        assert slow["auto"] is None
        limit = clock["limit"]
        cprint(f"auto-stop {limit}", "cyan")
        assert limit["emitted"] == 18000
        assert limit["first"] == 0
        assert limit["last"] == 17999
        assert limit["message"] == "Recording stopped at 10 minutes."
        assert limit["stopped"] is True
        assert limit["after"] == 18000
        context.close()
        browser.close()
