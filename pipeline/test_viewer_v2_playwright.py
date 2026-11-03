# Viewer v2: CT-panes default order, radiologist front preset, endoluminal
# fork orifices. Screenshots → pipeline/out/viewer_v2_shots/. Tmp: link files only.
from __future__ import annotations

import os
import shutil
import subprocess
import time
from pathlib import Path

import pytest
from termcolor import cprint

from test_p3_playwright import build_tmp_pipeline

ROOT = Path(__file__).resolve().parent
SHOTS = ROOT / "out" / "viewer_v2_shots"
PORT = 8772
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
    tmp = tmp_path_factory.mktemp("viewer_v2_pipeline")
    return build_tmp_pipeline(tmp)


@pytest.fixture(scope="module")
def server(tmp_pipeline):
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    fake_home = tmp_pipeline / "fake_home"
    fake_home.mkdir(exist_ok=True)
    env["HOME"] = str(fake_home)
    env["LUNGNAV_PIPELINE_ROOT"] = str(tmp_pipeline)
    cprint(f"viewer-v2 server on {PORT} root={tmp_pipeline}", "cyan")
    proc = subprocess.Popen(
        [
            "env", "-u", "PYTHONPATH",
            "uv", "run", "python", "app_server.py", "--port", str(PORT),
        ],
        cwd=str(ROOT),
        env={**env, "LUNGNAV_PIPELINE_ROOT": str(tmp_pipeline)},
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    try:
        _wait_server()
        yield {"pipeline": tmp_pipeline, "proc": proc}
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()


def test_viewer_v2_order_radiologist_orifices(server, tmp_path):
    sync_playwright = _playwright_or_skip()
    SHOTS.mkdir(parents=True, exist_ok=True)
    shots_tmp = tmp_path / "viewer_v2_shots"
    shots_tmp.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport=VIEWPORT)

        cprint("open viewer /?case=local-001", "cyan")
        page.goto(
            f"{BASE}/?case=local-001&allow_review=1&debug=1",
            wait_until="domcontentloaded",
            timeout=120_000,
        )
        page.wait_for_function(
            "() => window.__VIEWER_MODE__ === true && typeof window.setVariant === 'function'"
            " && window.__p2_debug",
            timeout=120_000,
        )
        page.wait_for_selector(".ct-pane-axial", timeout=120_000)
        page.wait_for_timeout(800)

        order = page.evaluate("() => window.__p2_debug.switcherKeys()")
        assert order == ["ct-panes", "anatomical", "topology", "endoluminal"], order
        default_key = page.evaluate("() => window.__p2_debug.getVariant()")
        assert default_key == "ct-panes", default_key

        # CT panes: one row of three equal cells.
        ct_layout = page.evaluate(
            """() => {
              const panes = [...document.querySelectorAll('.ct-pane')];
              if (panes.length < 3) return { n: panes.length };
              const rects = panes.map(el => {
                const r = el.getBoundingClientRect();
                return { x: r.x, y: r.y, w: r.width, h: r.height, key: el.dataset.pane };
              });
              const ys = rects.map(r => r.y);
              const hs = rects.map(r => r.h);
              const ws = rects.map(r => r.w);
              return {
                n: panes.length,
                sameRow: Math.max(...ys) - Math.min(...ys) < 8,
                equalW: Math.max(...ws) - Math.min(...ws) < 12,
                tall: Math.min(...hs) > 200,
                keys: rects.map(r => r.key),
              };
            }"""
        )
        assert ct_layout.get("n") == 3, ct_layout
        assert ct_layout.get("sameRow"), ct_layout
        assert ct_layout.get("equalW"), ct_layout
        assert ct_layout.get("keys") == ["axial", "coronal", "sagittal"], ct_layout

        # Shared mm/px: S-I pixel extent must match in coronal vs sagittal (±2%).
        page.wait_for_function(
            "() => window.__p3_ct && typeof window.__p3_ct.siPixelExtents === 'function'"
            " && window.__p3_ct.getMeta && window.__p3_ct.getMeta()",
            timeout=120_000,
        )
        si = page.evaluate("() => window.__p3_ct.siPixelExtents()")
        assert si and si.get("coronal") and si.get("sagittal"), si
        si_max = max(si["coronal"], si["sagittal"])
        si_err = abs(si["coronal"] - si["sagittal"]) / si_max
        assert si_err <= 0.02, f"S-I scale mismatch coronal={si['coronal']} sagittal={si['sagittal']} err={si_err:.3%}"

        # Viewer mode: no write prompts in the CT toolbar.
        write_prompts = page.evaluate(
            """() => {
              const tb = document.querySelector('.ct-toolbar');
              if (!tb) return ['missing toolbar'];
              const text = tb.innerText || '';
              const hits = [];
              for (const s of [
                'Mark and confirm the Lesion first',
                'Mark Lesion',
                'Confirm Lesion',
                'Confirm Start',
                'Click in the axial',
              ]) {
                if (text.includes(s)) hits.push(s);
              }
              return hits;
            }"""
        )
        assert write_prompts == [], write_prompts

        for label, name in [
            ("CT panes", "ct_panes"),
            ("Anatomical", "anatomical"),
            ("Topology", "topology"),
            ("Endoluminal", "endoluminal"),
        ]:
            page.locator("#switcher button", has_text=label).click()
            page.wait_for_timeout(900)
            page.screenshot(
                path=str(shots_tmp / f"local-001_{name}.png"),
                full_page=True,
            )

        # Radiologist (front): head at top, patient right on screen left.
        rad = page.evaluate("() => window.__p2_debug.radiologistCheck()")
        assert rad and rad.get("headOnTop"), rad
        assert rad.get("rightOnScreenLeft"), rad
        preset = page.evaluate("() => window.__p2_debug.getPreset()")
        assert preset == "radiologist", preset

        # Endoluminal orifices at carina, RUL fork, and bronchus intermedius fork.
        forks = page.evaluate(
            """() => (window.__p2_debug.forks() || []).map((f, i) => ({
              i,
              name: f.name || '',
              children: (f.children || []).map(c => ({
                name: (c.name || '').toLowerCase(),
                on_route: !!c.on_route,
              })),
            }))"""
        )
        assert len(forks) >= 1, forks

        def _fork_idx(pred, default=0):
            for f in forks:
                if pred(f):
                    return f["i"]
            return default

        carina_i = 0
        # RUL takeoff: child named right upper lobe (usually vs bronchus intermedius).
        rul_i = _fork_idx(
            lambda f: any("right upper" in c["name"] for c in f["children"]),
            1 if len(forks) > 1 else 0,
        )
        # Bronchus intermedius fork: BI splits onward (RML/RLL), not the RUL takeoff.
        bi_i = _fork_idx(
            lambda f: (
                f["i"] != rul_i
                and any("middle" in c["name"] or "lower" in c["name"] for c in f["children"])
                and (
                    "intermedius" in (f["name"] or "").lower()
                    or any("intermedius" in c["name"] for c in f["children"])
                    or any("middle" in c["name"] for c in f["children"])
                )
            ),
            2 if len(forks) > 2 else rul_i,
        )

        carina = page.evaluate(f"() => window.__p2_debug.goToFork({carina_i})")
        assert carina and carina.get("fork"), carina
        orifices = carina.get("orifices") or []
        assert len(orifices) >= 2, carina
        # Distinct non-overlapping openings (centres at least ~sum of radii apart).
        for i, a in enumerate(orifices):
            for b in orifices[i + 1 :]:
                dist = ((a["x"] - b["x"]) ** 2 + (a["y"] - b["y"]) ** 2) ** 0.5
                need = a["pixels"] + b["pixels"]
                assert dist + 1e-3 >= need * 0.85, (a, b, dist, need)
        child_names = {c["name"] for c in carina["fork"]["children"]}
        orifice_names = {o["name"] for o in orifices}
        assert orifice_names <= child_names or child_names <= orifice_names or orifice_names & child_names, (
            child_names, orifice_names
        )
        for o in orifices:
            match = next(
                (c for c in carina["fork"]["children"] if c["name"] == o["name"]),
                None,
            )
            if o["name"] != "branch":
                assert match is not None, (o, carina["fork"]["children"])
        route_hi = [o for o in orifices if o.get("on_route")]
        assert len(route_hi) == 1, orifices
        page.wait_for_timeout(400)
        page.screenshot(
            path=str(shots_tmp / "local-001_endoluminal_carina.png"),
            full_page=True,
        )

        for idx, shot in ((rul_i, "rul"), (bi_i, "bi")):
            got = page.evaluate(f"() => window.__p2_debug.goToFork({idx})")
            assert got and got.get("orifices") and len(got["orifices"]) >= 2, got
            page.wait_for_timeout(400)
            page.screenshot(
                path=str(shots_tmp / f"local-001_endoluminal_{shot}.png"),
                full_page=True,
            )

        browser.close()

    for name in (
        "local-001_ct_panes.png",
        "local-001_anatomical.png",
        "local-001_topology.png",
        "local-001_endoluminal.png",
        "local-001_endoluminal_carina.png",
        "local-001_endoluminal_rul.png",
        "local-001_endoluminal_bi.png",
    ):
        src = shots_tmp / name
        if src.is_file():
            shutil.copy2(src, SHOTS / name)
    cprint(f"viewer v2 shots in {SHOTS}", "green")
