# Changes: anatomical orbit and topology spin offline render, cancel, and 720p encode (#45).
"""Offline anatomical 360° orbit and topology spin via the debug hook and Record encoder."""

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

import pytest
from termcolor import cprint

import repo_root

repo_root.ensure()

from test_app_server import _stamp_pipeline_version, _write_case_tree  # noqa: E402
from test_flythrough_playwright import _seed  # noqa: E402
from test_viewer_v2_playwright import VIEWPORT, _playwright_or_skip  # noqa: E402

ROOT = Path(__file__).resolve().parent
DESKTOP = ROOT.parent / "desktop"
ORBIT_FRAMES = 360
FOOTER = "Simulated · CT-derived · research use"
FORBIDDEN = ("ZZZ-PATIENT", "PatientName", "PatientID", "19990315", "StudyDate")


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
    cprint(f"orbit/spin server on {port}", "cyan")
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
                pytest.fail(f"orbit/spin server exited\n{log_path.read_text()[-2000:]}")
            try:
                with urllib.request.urlopen(f"{base}/api/cases", timeout=1):
                    break
            except OSError:
                time.sleep(0.1)
        else:
            pytest.fail("orbit/spin server did not start")
        yield base, case_path, tmp_path
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)
        log.close()


def _open_view(page, server: str, variant: str):
    # Export buttons only show with the desktop recorder bridge.
    page.add_init_script(
        """window.bronchoroute = {
             recordStart: async () => ({ recordingId: 'stub', saved: true }),
             recordFrame: async () => true,
             recordStop: async () => true,
             recordCancel: async () => true,
           };"""
    )
    page.goto(
        f"{server}/renderer.template.html?case=local-001&allow_review=1&debug=1&variant={variant}",
        wait_until="domcontentloaded",
    )
    page.wait_for_function(
        """() => window.__p2_debug
          && document.getElementById('c').clientWidth > 100
          && window.AIRWAY""",
    )
    page.wait_for_function("() => document.fonts.status === 'loaded'")
    index = {"anatomical": 0, "topology": 2, "endoluminal": 1}[variant]
    page.evaluate(f"() => window.__p2_debug.setVariant({index})")
    btn = {"anatomical": "orbit-btn", "topology": "spin-btn", "endoluminal": "flythrough-btn"}[variant]
    page.wait_for_function(
        f"""() => document.getElementById('{btn}')
          && !document.getElementById('{btn}').hidden
          && document.getElementById('c').clientWidth > 100""",
    )


def _encode_mp4(frames_dir: Path, out_mp4: Path, tmp: Path, expected: int, width: int, height: int):
    script = tmp / "encode_orbit_spin.js"
    script.write_text(
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
            "  const width = Number(process.argv[7]);",
            "  const height = Number(process.argv[8]);",
            '  const ffmpegPath = resolveFfmpeg({ packaged: false, env: process.env });',
            "  const enc = startEncoder({ ffmpegPath, width, height, fps: 30, outPath });",
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
            "node", str(script),
            str(DESKTOP / "lib.js"),
            str(DESKTOP / "encoder.js"),
            str(frames_dir),
            str(out_mp4),
            str(expected),
            str(width),
            str(height),
        ],
        cwd=str(DESKTOP),
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )
    if encode.returncode != 0:
        pytest.fail(f"encode failed\n{encode.stdout}\n{encode.stderr}")


def _ffprobe(path: Path) -> dict:
    probe = subprocess.run(
        [
            "ffprobe", "-v", "error",
            "-select_streams", "v:0",
            "-show_entries",
            "stream=codec_name,avg_frame_rate,r_frame_rate,width,height,nb_frames,duration,pix_fmt,color_range",
            "-show_entries", "format=duration:format_tags:stream_tags",
            "-of", "json",
            str(path),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(probe.stdout)


def _assert_mp4(info: dict, *, frames: int, width: int, height: int):
    stream = info["streams"][0]
    duration = float(stream.get("duration") or info["format"]["duration"])
    assert stream["codec_name"] == "h264"
    assert int(stream["width"]) == width
    assert int(stream["height"]) == height
    assert stream.get("pix_fmt") in (None, "yuv420p") or "yuv420p" in str(stream.get("pix_fmt"))
    # color_range may be "tv" or 1 depending on ffprobe
    cr = stream.get("color_range")
    if cr is not None:
        assert str(cr) in ("tv", "1")
    rate = stream.get("avg_frame_rate") or stream.get("r_frame_rate")
    num, den = [int(part) for part in str(rate).split("/")]
    assert abs((num / den) - 30.0) < 0.05
    nb = stream.get("nb_frames")
    if nb not in (None, "N/A"):
        assert int(nb) == frames
    assert abs(duration - frames / 30.0) < 0.35
    tags = {}
    tags.update(info.get("format", {}).get("tags") or {})
    tags.update(stream.get("tags") or {})
    for key in ("creation_time", "title", "comment"):
        val = tags.get(key)
        assert not val, f"unexpected metadata {key}={val!r}"


def _flush_jpegs(page, frames_dir: Path, count: int, key: str):
    batch = 8
    for start in range(0, count, batch):
        payload = page.evaluate(
            """({ start, count, key }) => {
              const frames = window[key];
              const out = [];
              for (let i = 0; i < count; i += 1) {
                const bytes = frames[start + i];
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
            {"start": start, "count": min(batch, count - start), "key": key},
        )
        for offset, b64 in enumerate(payload):
            (frames_dir / f"frame-{start + offset:05d}.jpg").write_bytes(base64.b64decode(b64))


def test_orbit_spin_offline_render_cancel_and_encode(server):
    sync_playwright = _playwright_or_skip()
    base, _case_path, tmp_pipeline = server
    artifacts = Path(os.environ.get("TMPDIR", "/tmp")) / "bronchoroute-45"
    if artifacts.exists():
        shutil.rmtree(artifacts)
    artifacts.mkdir(parents=True)
    orbit_dir = artifacts / "orbit-frames"
    spin_dir = artifacts / "spin-frames"
    orbit_dir.mkdir()
    spin_dir.mkdir()
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

        # --- Orbit ---
        cprint("open anatomical for orbit", "cyan")
        _open_view(page, base, "anatomical")
        ui = page.evaluate(
            """() => ({
              orbitHidden: document.getElementById('orbit-btn').hidden,
              spinHidden: document.getElementById('spin-btn').hidden,
              flyHidden: document.getElementById('flythrough-btn').hidden,
              orbitDisabled: document.getElementById('orbit-btn').disabled,
            })"""
        )
        assert ui["orbitHidden"] is False
        assert ui["spinHidden"] is True
        assert ui["flyHidden"] is True
        assert ui["orbitDisabled"] is False

        page.evaluate(
            """() => {
              window.__p2_debug.setCamPose({
                yaw: 12 * Math.PI / 180,
                pitch: -8 * Math.PI / 180,
                roll: Math.PI,
                dist: 220,
                panX: 5,
                panY: -3,
              });
            }"""
        )
        before_cam = page.evaluate("() => window.__p2_debug.cam()")
        assert abs(before_cam["yaw"] * 180 / math.pi - 12) < 0.05

        cprint("renderOrbit check @640x360", "cyan")
        orbit = page.evaluate(
            """async () => {
              const width = 640, height = 360;
              const start = window.__p2_debug.cam();
              const labels = [];
              const yawDeg = [];
              const pitches = [];
              const rolls = [];
              const dists = [];
              let burnMismatch = null;
              let pixelDiff = null;
              let firstBytes = null;
              let midBytes = null;
              const when = new Date(2026, 10, 3, 1, 40, 0);
              const result = await window.__p2_debug.renderOrbit({
                size: { width, height },
                hud: true,
                lesion: true,
                when,
                yieldEvery: 10,
                start: {
                  yaw: start.yaw, pitch: start.pitch, roll: start.roll,
                  dist: start.dist, panX: 5, panY: -3,
                },
                onProgress(done, total) {
                  labels.push(`Rendering ${done} / ${total} frames`);
                },
                async sink(bytes, meta) {
                  yawDeg.push(meta.yaw * 180 / Math.PI);
                  pitches.push(meta.pitch);
                  rolls.push(meta.roll);
                  dists.push(meta.dist);
                  const burn = meta.burn;
                  if (!burnMismatch) {
                    const lines = (burn && burn.lines) || [];
                    const joined = lines.join("\\n");
                    if (!burn
                      || burn.viewName !== "Anatomical 360° orbit"
                      || burn.caseId !== "local-001"
                      || burn.footer !== "Simulated · CT-derived · research use"
                      || !joined.includes("local-001")
                      || !joined.includes("Anatomical 360")
                      || /ZZZ-PATIENT|PatientName|PatientID|19990315|StudyDate/.test(joined)) {
                      burnMismatch = burn;
                    }
                  }
                  if (meta.index === 0) firstBytes = bytes;
                  if (meta.index === 90) midBytes = bytes;
                },
              });
              if (firstBytes && midBytes) {
                const decode = async (bytes) => {
                  const bmp = await createImageBitmap(new Blob([bytes], { type: "image/jpeg" }));
                  const c = document.createElement("canvas");
                  c.width = bmp.width; c.height = bmp.height;
                  const g = c.getContext("2d", { willReadFrequently: true });
                  g.drawImage(bmp, 0, 0); bmp.close();
                  return g.getImageData(0, 0, c.width, c.height).data;
                };
                const a = await decode(firstBytes);
                const b = await decode(midBytes);
                let differ = 0;
                for (let i = 0; i < a.length; i += 16) {
                  if (Math.abs(a[i] - b[i]) > 8 || Math.abs(a[i+1] - b[i+1]) > 8
                    || Math.abs(a[i+2] - b[i+2]) > 8) differ += 1;
                }
                pixelDiff = differ;
              }
              const after = window.__p2_debug.cam();
              return {
                result, labelsFirst: labels[0], labelsLast: labels[labels.length - 1],
                labelCount: labels.length, yawDeg, pitches, rolls, dists,
                burnMismatch, pixelDiff, after, startYaw: start.yaw,
                startPitch: start.pitch, startRoll: start.roll, startDist: start.dist,
              };
            }"""
        )
        assert orbit["result"]["cancelled"] is False
        assert orbit["result"]["total"] == ORBIT_FRAMES
        assert orbit["result"]["frames"] == ORBIT_FRAMES
        assert orbit["labelCount"] == ORBIT_FRAMES
        assert orbit["labelsFirst"] == f"Rendering 1 / {ORBIT_FRAMES} frames"
        assert orbit["labelsLast"] == f"Rendering {ORBIT_FRAMES} / {ORBIT_FRAMES} frames"
        assert orbit["burnMismatch"] is None, orbit["burnMismatch"]
        assert orbit["pixelDiff"] is not None and orbit["pixelDiff"] > 50, orbit["pixelDiff"]
        start_yaw_deg = orbit["startYaw"] * 180 / math.pi
        for k, yaw in enumerate(orbit["yawDeg"]):
            expected = start_yaw_deg + k * 360.0 / ORBIT_FRAMES
            assert abs(yaw - expected) < 0.1, (k, yaw, expected)
            assert abs(orbit["pitches"][k] - orbit["startPitch"]) < 1e-9
            assert abs(orbit["rolls"][k] - orbit["startRoll"]) < 1e-9
            assert abs(orbit["dists"][k] - orbit["startDist"]) < 1e-9
        assert abs(orbit["yawDeg"][0] - orbit["yawDeg"][-1]) > 1.0
        after = orbit["after"]
        assert abs(after["yaw"] - before_cam["yaw"]) < 1e-9
        assert abs(after["pitch"] - before_cam["pitch"]) < 1e-9
        assert abs(after["roll"] - before_cam["roll"]) < 1e-9
        assert abs(after["dist"] - before_cam["dist"]) < 1e-9

        cprint("orbit cancel mid-run", "cyan")
        orbit_cancel = page.evaluate(
            """async () => {
              const before = window.__p2_debug.cam();
              const controller = new AbortController();
              let calls = 0;
              const result = await window.__p2_debug.renderOrbit({
                size: { width: 640, height: 360 },
                signal: controller.signal,
                sink() {
                  calls += 1;
                  if (calls === 5) controller.abort();
                },
              });
              const after = window.__p2_debug.cam();
              return { result, calls, before, after };
            }"""
        )
        assert orbit_cancel["result"]["cancelled"] is True
        assert orbit_cancel["result"]["frames"] == 5
        assert orbit_cancel["calls"] == 5
        for key in ("yaw", "pitch", "roll", "dist"):
            assert abs(orbit_cancel["after"][key] - orbit_cancel["before"][key]) < 1e-9

        cprint("orbit encode pass @1280x720", "cyan")
        page.evaluate(
            """async () => {
              const start = window.__p2_debug.cam();
              const jpegFrames = [];
              const when = new Date(2026, 10, 3, 1, 40, 0);
              await window.__p2_debug.renderOrbit({
                size: { width: 1280, height: 720 },
                hud: true, lesion: true, when,
                yieldEvery: 30,
                start: {
                  yaw: start.yaw, pitch: start.pitch, roll: start.roll,
                  dist: start.dist, panX: start.panX || 0, panY: start.panY || 0,
                },
                async sink(bytes) { jpegFrames.push(bytes); },
              });
              window.__orbitJpegFrames = jpegFrames;
              return jpegFrames.length;
            }"""
        )
        _flush_jpegs(page, orbit_dir, ORBIT_FRAMES, "__orbitJpegFrames")
        page.evaluate("() => { window.__orbitJpegFrames = null; }")
        for index in (0, 90, 180):
            src = orbit_dir / f"frame-{index:05d}.jpg"
            dest = artifacts / f"orbit-frame{index:03d}.jpg"
            shutil.copyfile(src, dest)
            cprint(f"artifact {dest.name}", "green")
        orbit_mp4 = artifacts / "orbit-720p.mp4"
        _encode_mp4(orbit_dir, orbit_mp4, tmp_pipeline, ORBIT_FRAMES, 1280, 720)
        orbit_info = _ffprobe(orbit_mp4)
        (artifacts / "orbit-ffprobe.json").write_text(json.dumps(orbit_info, indent=2) + "\n")
        cprint(json.dumps(orbit_info, indent=2), "green")
        _assert_mp4(orbit_info, frames=ORBIT_FRAMES, width=1280, height=720)

        # Cancel with encoder partial delete
        cprint("orbit encoder cancel deletes partial", "cyan")
        partial = artifacts / "orbit-partial.mp4"
        cancel_script = tmp_pipeline / "cancel_orbit.js"
        cancel_script.write_text(
            "\n".join([
                '"use strict";',
                'const fs = require("node:fs");',
                'const path = require("node:path");',
                'const { resolveFfmpeg } = require(process.argv[2]);',
                'const { startEncoder } = require(process.argv[3]);',
                "async function main() {",
                "  const framesDir = process.argv[4];",
                "  const outPath = process.argv[5];",
                '  const ffmpegPath = resolveFfmpeg({ packaged: false, env: process.env });',
                "  const enc = startEncoder({",
                "    ffmpegPath, width: 1280, height: 720, fps: 30, outPath,",
                "  });",
                "  await enc.writeFrame(fs.readFileSync(path.join(framesDir, 'frame-00000.jpg')));",
                "  await enc.writeFrame(fs.readFileSync(path.join(framesDir, 'frame-00001.jpg')));",
                "  await enc.cancel();",
                '  process.stdout.write(fs.existsSync(outPath) ? "exists\\n" : "gone\\n");',
                "}",
                "main().catch((err) => { console.error(err); process.exit(1); });",
                "",
            ])
        )
        cancel_run = subprocess.run(
            [
                "node", str(cancel_script),
                str(DESKTOP / "lib.js"),
                str(DESKTOP / "encoder.js"),
                str(orbit_dir),
                str(partial),
            ],
            cwd=str(DESKTOP),
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        assert cancel_run.returncode == 0, cancel_run.stderr
        assert "gone" in cancel_run.stdout
        assert not partial.exists()

        # --- Spin ---
        cprint("open topology for spin", "cyan")
        _open_view(page, base, "topology")
        ui2 = page.evaluate(
            """() => ({
              orbitHidden: document.getElementById('orbit-btn').hidden,
              spinHidden: document.getElementById('spin-btn').hidden,
              flyHidden: document.getElementById('flythrough-btn').hidden,
            })"""
        )
        assert ui2["spinHidden"] is False
        assert ui2["orbitHidden"] is True
        assert ui2["flyHidden"] is True

        page.evaluate("() => window.__p2_debug.setTopoRot(25)")
        before_topo = page.evaluate("() => window.__p2_debug.topoState()")
        assert abs(before_topo["topoRot"] - 25) < 1e-9

        cprint("renderSpin check @640x360", "cyan")
        spin = page.evaluate(
            """async () => {
              const width = 640, height = 360;
              const startRot = window.__p2_debug.topoState().topoRot;
              const beforeStats = window.__p2_debug.topoState();
              const labels = [];
              const rots = [];
              let burnMismatch = null;
              let pixelDiff = null;
              let firstBytes = null;
              let midBytes = null;
              const when = new Date(2026, 10, 3, 1, 40, 0);
              const result = await window.__p2_debug.renderSpin({
                size: { width, height },
                hud: true, lesion: true, when,
                yieldEvery: 10,
                startRot,
                onProgress(done, total) {
                  labels.push(`Rendering ${done} / ${total} frames`);
                },
                async sink(bytes, meta) {
                  rots.push(meta.topoRot);
                  const burn = meta.burn;
                  if (!burnMismatch) {
                    const lines = (burn && burn.lines) || [];
                    const joined = lines.join("\\n");
                    if (!burn
                      || burn.viewName !== "Topology spin"
                      || burn.caseId !== "local-001"
                      || burn.footer !== "Simulated · CT-derived · research use"
                      || !joined.includes("Topology spin")
                      || /ZZZ-PATIENT|PatientName|PatientID|19990315|StudyDate/.test(joined)) {
                      burnMismatch = burn;
                    }
                  }
                  if (meta.index === 0) firstBytes = bytes;
                  if (meta.index === 90) midBytes = bytes;
                },
              });
              if (firstBytes && midBytes) {
                const decode = async (bytes) => {
                  const bmp = await createImageBitmap(new Blob([bytes], { type: "image/jpeg" }));
                  const c = document.createElement("canvas");
                  c.width = bmp.width; c.height = bmp.height;
                  const g = c.getContext("2d", { willReadFrequently: true });
                  g.drawImage(bmp, 0, 0); bmp.close();
                  return g.getImageData(0, 0, c.width, c.height).data;
                };
                const a = await decode(firstBytes);
                const b = await decode(midBytes);
                let differ = 0;
                for (let i = 0; i < a.length; i += 16) {
                  if (Math.abs(a[i] - b[i]) > 8 || Math.abs(a[i+1] - b[i+1]) > 8
                    || Math.abs(a[i+2] - b[i+2]) > 8) differ += 1;
                }
                pixelDiff = differ;
              }
              const after = window.__p2_debug.topoState();
              return {
                result, labelsFirst: labels[0], labelsLast: labels[labels.length - 1],
                labelCount: labels.length, rots, burnMismatch, pixelDiff,
                beforeStats, after, startRot,
              };
            }"""
        )
        assert spin["result"]["cancelled"] is False
        assert spin["result"]["total"] == ORBIT_FRAMES
        assert spin["result"]["frames"] == ORBIT_FRAMES
        assert spin["burnMismatch"] is None, spin["burnMismatch"]
        assert spin["pixelDiff"] is not None and spin["pixelDiff"] > 50, spin["pixelDiff"]
        for k, rot in enumerate(spin["rots"]):
            expected = spin["startRot"] + k * 360.0 / ORBIT_FRAMES
            assert abs(rot - expected) < 0.1, (k, rot, expected)
        assert abs(spin["rots"][0] - spin["rots"][-1]) > 1.0
        assert spin["after"]["topoRot"] == spin["beforeStats"]["topoRot"]
        assert spin["after"]["branches"] == spin["beforeStats"]["branches"]
        assert spin["after"]["gens"] == spin["beforeStats"]["gens"]
        assert abs(spin["after"]["lengthCm"] - spin["beforeStats"]["lengthCm"]) < 1e-9

        cprint("spin cancel mid-run", "cyan")
        spin_cancel = page.evaluate(
            """async () => {
              const before = window.__p2_debug.topoState();
              const controller = new AbortController();
              let calls = 0;
              const result = await window.__p2_debug.renderSpin({
                size: { width: 640, height: 360 },
                signal: controller.signal,
                sink() {
                  calls += 1;
                  if (calls === 5) controller.abort();
                },
              });
              const after = window.__p2_debug.topoState();
              const btn = document.getElementById('spin-btn');
              const cancel = document.getElementById('flythrough-cancel');
              return {
                result, calls, before, after,
                btnHidden: btn.hidden, btnDisabled: btn.disabled, cancelHidden: cancel.hidden,
              };
            }"""
        )
        assert spin_cancel["result"]["cancelled"] is True
        assert spin_cancel["result"]["frames"] == 5
        assert abs(spin_cancel["after"]["topoRot"] - spin_cancel["before"]["topoRot"]) < 1e-9
        assert spin_cancel["btnHidden"] is False
        assert spin_cancel["btnDisabled"] is False
        assert spin_cancel["cancelHidden"] is True

        cprint("spin encode pass @1280x720", "cyan")
        page.evaluate(
            """async () => {
              const startRot = window.__p2_debug.topoState().topoRot;
              const jpegFrames = [];
              const when = new Date(2026, 10, 3, 1, 40, 0);
              await window.__p2_debug.renderSpin({
                size: { width: 1280, height: 720 },
                hud: true, lesion: true, when, startRot,
                yieldEvery: 30,
                async sink(bytes) { jpegFrames.push(bytes); },
              });
              window.__spinJpegFrames = jpegFrames;
              return jpegFrames.length;
            }"""
        )
        _flush_jpegs(page, spin_dir, ORBIT_FRAMES, "__spinJpegFrames")
        page.evaluate("() => { window.__spinJpegFrames = null; }")
        for index in (0, 90, 180):
            src = spin_dir / f"frame-{index:05d}.jpg"
            dest = artifacts / f"spin-frame{index:03d}.jpg"
            shutil.copyfile(src, dest)
            cprint(f"artifact {dest.name}", "green")
        spin_mp4 = artifacts / "spin-720p.mp4"
        _encode_mp4(spin_dir, spin_mp4, tmp_pipeline, ORBIT_FRAMES, 1280, 720)
        spin_info = _ffprobe(spin_mp4)
        (artifacts / "spin-ffprobe.json").write_text(json.dumps(spin_info, indent=2) + "\n")
        cprint(json.dumps(spin_info, indent=2), "green")
        _assert_mp4(spin_info, frames=ORBIT_FRAMES, width=1280, height=720)

        # Metadata strings in mp4 should not contain identifiers
        for mp4 in (orbit_mp4, spin_mp4):
            raw = mp4.read_bytes()
            for token in FORBIDDEN:
                assert token.encode() not in raw, (mp4.name, token)

        browser.close()
        cprint("orbit/spin test done", "green")
