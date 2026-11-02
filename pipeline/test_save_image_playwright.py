# Changes: PNG stills at 2× CSS size, burn-in strings, chunk list, and browser download name.
"""Save-image stills for anatomical, topology, and endoluminal views."""

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
ALLOWED_CHUNKS = {"IHDR", "IDAT", "IEND", "sRGB", "pHYs", "gAMA"}
BANNED_CHUNKS = {"tEXt", "iTXt", "zTXt", "eXIf", "tIME"}
VIEWS = (
    ("anatomical", 0, "Anatomical 3D", True),
    ("topology", 2, "Topology map", False),
    ("endoluminal", 1, "Endoluminal", False),
)

GRAB = r"""
async (spec) => {
  window.__p2_debug.setVariant(spec.index);
  const cv = document.getElementById("c");
  const cssW = cv.clientWidth;
  const cssH = cv.clientHeight;
  const when = new Date(2026, 10, 2, 21, 40, 30);
  const blob = await window.__p2_debug.exportStill({ hud: true, lesion: true, when });
  const bitmap = await createImageBitmap(blob);
  const bytes = new Uint8Array(await blob.arrayBuffer());
  const chunk = 0x2000;
  let binary = "";
  for (let i = 0; i < bytes.length; i += chunk) {
    binary += String.fromCharCode.apply(null, bytes.subarray(i, Math.min(bytes.length, i + chunk)));
  }
  const burn = window.__p2_debug.burnIn();
  let hudDiffers = null;
  let burnOff = null;
  if (spec.compareHud) {
    const rig = window.__p2_debug.rig();
    const offBlob = await window.__p2_debug.exportStill({ hud: false, lesion: true, when });
    burnOff = window.__p2_debug.burnIn();
    const differ = async (rect) => {
      if (!rect || !(rect.w > 4) || !(rect.h > 4)) return false;
      const [bmpA, bmpB] = await Promise.all([
        createImageBitmap(blob), createImageBitmap(offBlob),
      ]);
      const canvas = document.createElement("canvas");
      canvas.width = bmpA.width;
      canvas.height = bmpA.height;
      const ctx = canvas.getContext("2d", { willReadFrequently: true });
      const x = Math.max(0, Math.min(canvas.width - 1, Math.floor(rect.x * 2)));
      const y = Math.max(0, Math.min(canvas.height - 1, Math.floor(rect.y * 2)));
      const w = Math.max(1, Math.min(canvas.width - x, Math.floor(rect.w * 2)));
      const h = Math.max(1, Math.min(canvas.height - y, Math.floor(rect.h * 2)));
      ctx.drawImage(bmpA, 0, 0);
      const pa = ctx.getImageData(x, y, w, h).data;
      ctx.drawImage(bmpB, 0, 0);
      const pb = ctx.getImageData(x, y, w, h).data;
      for (let i = 0; i < pa.length; i += 1) {
        if (pa[i] !== pb[i]) return true;
      }
      return false;
    };
    const dial = rig && rig.scopeDial;
    const dialRect = dial && Number.isFinite(dial.x) ? {
      x: dial.x - dial.radius,
      y: dial.y - dial.radius,
      w: dial.radius * 2,
      h: dial.radius * 2,
    } : null;
    const panel = rig && rig.panel;
    const panelRect = panel ? {
      x: panel.l,
      y: panel.t,
      w: panel.r - panel.l,
      h: panel.b - panel.t,
    } : null;
    hudDiffers = await differ(dialRect);
    if (!hudDiffers) hudDiffers = await differ(panelRect);
  }
  return {
    cssW, cssH,
    width: bitmap.width,
    height: bitmap.height,
    b64: btoa(binary),
    burn,
    hudDiffers,
    burnOff,
  };
}
"""


def _png_chunks(data: bytes) -> list[str]:
    assert data.startswith(b"\x89PNG\r\n\x1a\n")
    chunks = []
    index = 8
    while index < len(data):
        assert index + 12 <= len(data)
        length = int.from_bytes(data[index:index + 4], "big")
        ctype = data[index + 4:index + 8].decode("ascii")
        assert index + 12 + length <= len(data)
        chunks.append(ctype)
        index += 12 + length
        if ctype == "IEND":
            break
    assert chunks and chunks[0] == "IHDR" and chunks[-1] == "IEND"
    return chunks


def _png_size(data: bytes) -> tuple[int, int]:
    assert data[12:16] == b"IHDR"
    return (
        int.from_bytes(data[16:20], "big"),
        int.from_bytes(data[20:24], "big"),
    )


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
    cprint(f"save-image server on {port}", "cyan")
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
                pytest.fail(f"save-image server exited\n{log_path.read_text()[-2000:]}")
            try:
                with urllib.request.urlopen(f"{base}/api/cases", timeout=1):
                    break
            except OSError:
                time.sleep(0.1)
        else:
            pytest.fail("save-image server did not start")
        yield base
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)
        log.close()


def _assert_burn(burn: dict, view_name: str, scale: bool) -> None:
    assert burn["viewName"] == view_name
    assert burn["caseId"] == "local-001"
    assert burn["date"] == "2026-11-02 21:40"
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}", burn["date"])
    assert burn["footer"] == FOOTER
    assert burn["lines"][0] == f"{view_name} · local-001 · {burn['date']}"
    assert burn["review"] == [REVIEW_LINE]
    assert burn["lines"][1:2] == [REVIEW_LINE]
    assert burn["lines"][-1] == FOOTER
    if scale:
        assert re.fullmatch(r"(10|20|50) mm", burn["scaleBar"])
        assert burn["scaleBar"] in burn["lines"]
    else:
        assert burn["scaleBar"] is None
        assert all(not re.fullmatch(r"(10|20|50) mm", line) for line in burn["lines"])
    blob = "\n".join(burn["lines"])
    for secret in (
        PATIENT_NAME, PATIENT_ID, STUDY_DATE, "1999-03-15",
        "Study date", "patient_name", "patient_id", "study_date",
    ):
        assert secret not in blob


def test_export_stills(server: str):
    sync_playwright = _playwright_or_skip()
    shots = Path(os.environ.get("TMPDIR", "/tmp")) / "bronchoroute-42"
    shots.mkdir(parents=True, exist_ok=True)
    cprint(f"artifacts in {shots}", "cyan")
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(args=["--disable-background-timer-throttling"])
        context = browser.new_context(viewport=VIEWPORT, accept_downloads=True)
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
        for key, index, view_name, scale in VIEWS:
            cprint(f"export {key}", "cyan")
            info = page.evaluate(GRAB, {"index": index, "compareHud": key == "endoluminal"})
            raw = base64.b64decode(info["b64"])
            assert info["width"] == info["cssW"] * 2
            assert info["height"] == info["cssH"] * 2
            assert info["cssW"] > 100 and info["cssH"] > 100
            assert _png_size(raw) == (info["width"], info["height"])
            chunks = _png_chunks(raw)
            assert "IDAT" in chunks
            assert set(chunks) <= ALLOWED_CHUNKS, chunks
            assert BANNED_CHUNKS.isdisjoint(chunks)
            _assert_burn(info["burn"], view_name, scale)
            if key == "endoluminal":
                assert info["hudDiffers"] is True
                for field in ("lines", "viewName", "caseId", "date", "footer", "scaleBar", "review"):
                    assert info["burnOff"][field] == info["burn"][field]
            still = shots / f"still-{key}.png"
            still.write_bytes(raw)
            view = shots / f"view-{key}.png"
            page.screenshot(path=str(view))
            cprint(f"{still} {info['width']}x{info['height']}", "green")
        cprint("hide Save image on CT panes and walk-through", "cyan")
        flags = page.evaluate(
            """() => {
              const hidden = () => document.getElementById("save-image").hidden;
              window.__p2_debug.setVariant(3);
              const ct = hidden();
              window.__p2_debug.setVariant(4);
              const walk = hidden();
              window.__p2_debug.setVariant(0);
              return { ct, walk, anatomical: hidden() };
            }"""
        )
        assert flags == {"ct": True, "walk": True, "anatomical": False}
        cprint("browser download", "cyan")
        page.locator("#save-image-btn").scroll_into_view_if_needed()
        with page.expect_download(timeout=60_000) as downloaded:
            page.locator("#save-image-btn").click()
        name = downloaded.value.suggested_filename
        assert re.fullmatch(r"local-001_anatomical_\d{8}-\d{4}\.png", name)
        cprint(name, "green")
        context.close()
        browser.close()
