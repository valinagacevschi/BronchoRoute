# Changes: lesion placement is any CT pane and overlays are not gated on it.
# Changes: keep AeroPath renderer coverage with legacy review explicitly enabled.
# Changes: tmp ts_out trees symlink individual input files (never directories) so
# review writes like airway_bridges_labels.nii.gz land in the tmp tree.
# Phase 3 review: playwright against a tmp PIPELINE root (symlinked read-only
# inputs; writable app_state / case.json copies). Never mutate real data.
from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from pathlib import Path

import pytest
from playwright.sync_api import sync_playwright
from termcolor import cprint

from conftest import symlink_input_file, symlink_input_tree

ROOT = Path(__file__).resolve().parent
REPO = ROOT.parent
SHOTS = ROOT / "out" / "p3_shots"
PORT = 8766
BASE = f"http://127.0.0.1:{PORT}"
VIEWPORT = {"width": 1280, "height": 800}


def _copy_file(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)


def build_tmp_pipeline(tmp: Path) -> Path:
    """Tmp PIPELINE with per-file symlinked inputs and copied case.json."""
    if not (ROOT / "out/local-001/local-001_graph.json").is_file():
        pytest.skip("renderer integration requires generated local Case graph")
    if not any(path.is_file() for path in (
        ROOT / "out/fallback/3_graph.json", ROOT / "out/3_graph.json",
    )):
        pytest.skip("renderer integration requires generated AeroPath graph")
    cprint(f"building tmp pipeline at {tmp}", "cyan")
    (tmp / "app_state").mkdir(parents=True)
    (tmp / "out" / "cases").mkdir(parents=True)
    (tmp / "out" / "fallback").mkdir(parents=True)
    (tmp / "out" / "local-001").mkdir(parents=True)
    (tmp / "data" / "extracted" / "local" / "local-001").mkdir(parents=True)
    (tmp / "data" / "extracted" / "AeroPath" / "3").mkdir(parents=True)
    (tmp / "ts_out").mkdir(parents=True)

    # local-001 — real dirs under ts_out so new writes stay in tmp.
    local_src = ROOT / "data/extracted/local/local-001"
    local_dst = tmp / "data/extracted/local/local-001"
    symlink_input_file(local_src / "local-001_CT.nii.gz", local_dst / "local-001_CT.nii.gz")
    _copy_file(local_src / "case.json", local_dst / "case.json")
    symlink_input_tree(ROOT / "ts_out/local-001", tmp / "ts_out/local-001")
    graph_local = ROOT / "out/local-001/local-001_graph.json"
    if graph_local.is_file():
        symlink_input_file(graph_local, tmp / "out/local-001/local-001_graph.json")

    # AeroPath 3
    aero_src = ROOT / "data/extracted/AeroPath/3"
    aero_dst = tmp / "data/extracted/AeroPath/3"
    for name in ("3_CT_HR.nii.gz", "3_CT_HR_label_airways.nii.gz", "3_CT_HR_label_lungs.nii.gz"):
        src = aero_src / name
        if src.is_file():
            symlink_input_file(src, aero_dst / name)
    symlink_input_tree(ROOT / "ts_out/fallback_3", tmp / "ts_out/fallback_3")
    symlink_input_tree(ROOT / "ts_out/3", tmp / "ts_out/3")
    for graph_name in ("3_graph.json",):
        for candidate in (
            ROOT / "out/fallback" / graph_name,
            ROOT / "out" / graph_name,
        ):
            if candidate.is_file():
                symlink_input_file(candidate, tmp / "out/fallback" / graph_name)
                break
    # Writable AeroPath case.json (may not exist yet on real tree).
    case3 = tmp / "out/cases/3/case.json"
    case3.parent.mkdir(parents=True, exist_ok=True)
    real_case3 = ROOT / "out/cases/3/case.json"
    if real_case3.is_file():
        data = json.loads(real_case3.read_text())
        data.pop("lesion", None)
        case3.write_text(json.dumps(data, indent=2) + "\n")
    else:
        case3.write_text(json.dumps({
            "id": "3",
            "source": "AeroPath 3",
            "records": [],
            "versions": [],
        }, indent=2) + "\n")

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
    tmp = tmp_path_factory.mktemp("p3_pipeline")
    return build_tmp_pipeline(tmp)


@pytest.fixture(scope="module")
def server(tmp_pipeline):
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    env["BRONCHOROUTE_LEGACY_REVIEW"] = "1"
    env["LUNGNAV_PIPELINE_ROOT"] = str(tmp_pipeline)
    cprint(f"starting app server on {PORT} with LUNGNAV_PIPELINE_ROOT={tmp_pipeline}", "cyan")
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
        yield {"proc": proc, "pipeline": tmp_pipeline}
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


def _console_errors(page) -> list[str]:
    return list(getattr(page, "_p3_errors", []))


def _attach_console(page) -> None:
    page._p3_errors = []

    def on_msg(msg):
        if msg.type == "error":
            page._p3_errors.append(msg.text)

    def on_page_error(err):
        page._p3_errors.append(str(err))

    page.on("console", on_msg)
    page.on("pageerror", on_page_error)


def _open_ct(page, case_id: str) -> None:
    url = (
        f"{BASE}/renderer.template.html?case={case_id}"
        f"&allow_review=1&debug=1&variant=ct-panes"
    )
    page.goto(url, wait_until="domcontentloaded", timeout=120_000)
    page.wait_for_function(
        """() => {
          const d = window.__p3_debug;
          if (!d) return false;
          const ct = (d.ct && d.ct()) || window.__p3_ct;
          return !!(ct && ct.isLoaded && ct.isLoaded());
        }""",
        timeout=180_000,
    )


def _set_clinician(page, role="Radiologist", initials="A.B.", ct_view="feet") -> None:
    page.request.put(
        f"{BASE}/api/clinician",
        data=json.dumps({"role": role, "initials": initials, "ct_view": ct_view}),
        headers={"Content-Type": "application/json"},
    )


def _ensure_step1(page, case_id: str) -> None:
    """Local cases need series_confirmed before lesion (Phase 4a)."""
    if not str(case_id).startswith("local-"):
        return
    page.request.post(
        f"{BASE}/api/cases/{case_id}/series/confirm",
        data=json.dumps({"overridden": False}),
        headers={"Content-Type": "application/json"},
    )


@pytest.mark.parametrize("case_id", ["3", "local-001"])
def test_p3_ct_panes(server, case_id):
    SHOTS.mkdir(parents=True, exist_ok=True)
    cprint(f"playwright CT panes for case {case_id}", "cyan")
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport=VIEWPORT)
        _attach_console(page)
        _set_clinician(page)
        _ensure_step1(page, case_id)
        _open_ct(page, case_id)

        # Lesion is optional: overlays stay enabled before one is placed.
        overlays_before = page.evaluate(
            """() => {
              const ct = window.__p3_debug.ct();
              return ct.overlaysEnabled();
            }"""
        )
        assert overlays_before["airwayDisabled"] is False
        assert overlays_before["routeDisabled"] is False
        assert overlays_before["lesionConfirmed"] is False

        results = page.evaluate(
            """async () => {
              const d = window.__p3_debug;
              const ct = d.ct();
              const meta = ct.getMeta();
              const origin = d.originMm();
              const A = d.airway();
              const out = { checks: {}, metaShape: meta.shape };

              // fitPlane aspect uses physical plane size
              const nx = meta.shape[0], ny = meta.shape[1];
              const sx = meta.spacing_mm[0], sy = meta.spacing_mm[1];
              const fit = window.CtPanes.fitPlane(400, 300, [nx*sx, ny*sy]);
              const aspect = (nx*sx) / (ny*sy);
              const gotAspect = fit.drawW / fit.drawH;
              out.checks.fitPlane = {
                aspect, gotAspect,
                ok: Math.abs(aspect - gotAspect) < 1e-6,
              };

              // (a) real axial mouse click at known world point
              const i0 = Math.floor(meta.shape[0] * 0.4);
              const j0 = Math.floor(meta.shape[1] * 0.45);
              const k0 = Math.floor(meta.shape[2] * 0.55);
              const aff = meta.affine;
              const world = [
                aff[0][0]*(i0+0.5) + aff[0][1]*(j0+0.5) + aff[0][2]*(k0+0.5) + aff[0][3],
                aff[1][0]*(i0+0.5) + aff[1][1]*(j0+0.5) + aff[1][2]*(k0+0.5) + aff[1][3],
                aff[2][0]*(i0+0.5) + aff[2][1]*(j0+0.5) + aff[2][2]*(k0+0.5) + aff[2][3],
              ];
              ct.setWorld([
                aff[0][0]*(i0+0.5) + aff[0][1]*(j0+0.5) + aff[0][2]*(0.5) + aff[0][3],
                aff[1][0]*(i0+0.5) + aff[1][1]*(j0+0.5) + aff[1][2]*(0.5) + aff[1][3],
                aff[2][0]*(i0+0.5) + aff[2][1]*(j0+0.5) + aff[2][2]*(0.5) + aff[2][3],
              ]);
              // Put axial on k0 first so the click lands on that slice.
              ct.setWorld(world);
              const scr = ct.screenOfWorld('axial', world);
              const canvas = document.querySelector('.ct-pane-axial canvas');
              const rect = canvas.getBoundingClientRect();
              const cx = rect.left + scr.x;
              const cy = rect.top + scr.y;
              canvas.dispatchEvent(new PointerEvent('pointerdown', {
                bubbles: true, clientX: cx, clientY: cy, button: 0, pointerId: 1,
              }));
              const slices = ct.getSlices();
              out.checks.crosshair = {
                want: [i0, j0, k0],
                got: [slices.sagittal, slices.coronal, slices.axial],
                ok: slices.sagittal === i0 && slices.coronal === j0 && slices.axial === k0,
              };

              // (b) wheel changes only hovered pane
              const before = { ...ct.getSlices() };
              const axial = document.querySelector('.ct-pane-axial canvas');
              axial.dispatchEvent(new WheelEvent('wheel', { deltaY: 100, bubbles: true }));
              const after = ct.getSlices();
              out.checks.wheel = {
                before, after,
                ok: after.axial !== before.axial
                  && after.coronal === before.coronal
                  && after.sagittal === before.sagittal,
              };

              // (c) radiological letters + rendered screen positions
              ct.setCtViewLocal('feet');
              const feet = ct.getLetters('axial');
              const feetPos = ct.letterScreenPositions('axial');
              ct.setCtViewLocal('head');
              const head = ct.getLetters('axial');
              const headPos = ct.letterScreenPositions('axial');
              ct.setCtViewLocal('feet');
              const coronalPos = ct.letterScreenPositions('coronal');
              out.checks.letters = {
                feet, head, feetPos, headPos, coronalPos,
                ok: feet.left === 'R' && head.left === 'L' && head.right === 'R'
                  && feetPos.left.x < feetPos.right.x
                  && headPos.left.x < headPos.right.x
                  && coronalPos.top.y < coronalPos.bottom.y
                  && Math.abs(coronalPos.left.y - (coronalPos.fit.oy + coronalPos.fit.drawH/2)) < 1,
              };

              // (c2) V toggles the view; A/R/C toggle overlays and light the key
              const press = k => dispatchEvent(new KeyboardEvent('keydown', { key: k, bubbles: true }));
              const lungWl = document.getElementById('ct-wl-readout').textContent;
              press('v');
              const headView = ct.getCtView();
              const headLabel = document.getElementById('ct-view-readout').textContent;
              press('v');
              const feetView = ct.getCtView();
              const feetLabel = document.getElementById('ct-view-readout').textContent;
              const airwayStart = ct.overlaysEnabled().airway;
              press('a');
              const airwayOff = ct.overlaysEnabled().airway;
              press('a');
              const airwayOn = ct.overlaysEnabled().airway;
              press('r');
              const states = ct.overlaysEnabled();
              const airwayKey = document.querySelector('[data-overlay-key="airway"]');
              const airwayStyle = getComputedStyle(airwayKey);
              out.checks.keys = {
                lungWl, headView, headLabel, feetView, feetLabel,
                airway: { start: airwayStart, off: airwayOff, on: airwayOn },
                overlays: { airway: states.airway, route: states.route },
                keyOn: airwayKey.classList.contains('on'),
                keyWeight: airwayStyle.fontWeight, keyColor: airwayStyle.color,
                ok: lungWl === 'W 1500 / L -600'
                  && headView === 'head' && headLabel === 'Bronchoscopist'
                  && feetView === 'feet' && feetLabel === 'Radiologist'
                  && airwayStart === true && airwayOff === false && airwayOn === true
                  && states.route === true
                  && airwayKey.classList.contains('on')
                  && airwayStyle.fontWeight === '700'
                  && airwayStyle.color === 'rgb(255, 255, 255)',
              };

              // (d) ALL 10 sampled route points within 1 voxel of label 1 or 2
              let routeOk = 0, routeN = 0;
              const routeEdges = A.route || [];
              const samples = [];
              for (const ei of routeEdges) {
                for (const c of (A.edges[ei] || {}).c || []) {
                  samples.push([c[0]+origin[0], c[1]+origin[1], c[2]+origin[2]]);
                }
              }
              const step = Math.max(1, Math.floor(samples.length / 10));
              for (let i = 0; i < samples.length && routeN < 10; i += step) {
                const w = samples[i];
                const ijk = ct.worldToVoxel(w);
                let found = false;
                for (let di = -1; di <= 1 && !found; di++) {
                  for (let dj = -1; dj <= 1 && !found; dj++) {
                    for (let dk = -1; dk <= 1 && !found; dk++) {
                      const ii = ijk[0]+di, jj = ijk[1]+dj, kk = ijk[2]+dk;
                      if (ii<0||jj<0||kk<0||ii>=meta.shape[0]||jj>=meta.shape[1]||kk>=meta.shape[2]) continue;
                      const lab = ct.sampleMask(ii,jj,kk);
                      if (lab === 1 || lab === 2) found = true;
                    }
                  }
                }
                routeN++;
                if (found) routeOk++;
              }
              out.checks.route = { routeOk, routeN, ok: routeN === 10 && routeOk === 10 };

              // (e) trachea superior to carina in coronal
              const mains = d.mainBronchi();
              const trachP = A.nodes[String(mains.trachea)].p;
              const carinaP = A.nodes[String(mains.carina)].p;
              const trachW = [trachP[0]+origin[0], trachP[1]+origin[1], trachP[2]+origin[2]];
              const carinaW = [carinaP[0]+origin[0], carinaP[1]+origin[1], carinaP[2]+origin[2]];
              ct.setWorld([
                (trachW[0]+carinaW[0])/2,
                (trachW[1]+carinaW[1])/2,
                (trachW[2]+carinaW[2])/2,
              ]);
              const st = ct.screenOfWorld('coronal', trachW);
              const sc = ct.screenOfWorld('coronal', carinaW);
              out.checks.coronal = {
                trachY: st && st.y, carinaY: sc && sc.y,
                ok: !!(st && sc && st.y < sc.y),
              };

              // Arrow keys outside CT view navigate the shared position
              const slicesBefore = { ...ct.getSlices() };
              d.setVariant(0);
              await new Promise(r => setTimeout(r, 50));
              window.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowDown', bubbles: true }));
              const slicesMid = { ...ct.getSlices() };
              d.setVariant(3);
              await new Promise(r => setTimeout(r, 50));
              out.checks.arrowsOutside = {
                before: slicesBefore, mid: slicesMid,
                // Arrows outside CT now navigate the shared airway position;
                // CT panes follow that position, so slices track the move.
                ok: slicesMid.axial !== slicesBefore.axial
                  || slicesMid.coronal !== slicesBefore.coronal
                  || slicesMid.sagittal !== slicesBefore.sagittal,
              };

              // (f) Mark Lesion in the coronal pane → Confirm → panes → DELETE
              const lesionI = Math.floor(meta.shape[0] / 2);
              const lesionJ = Math.floor(meta.shape[1] / 2);
              const lesionK = Math.floor(meta.shape[2] / 2);
              const lesionWorld = [
                aff[0][0]*(lesionI+0.5) + aff[0][1]*(lesionJ+0.5) + aff[0][2]*(lesionK+0.5) + aff[0][3],
                aff[1][0]*(lesionI+0.5) + aff[1][1]*(lesionJ+0.5) + aff[1][2]*(lesionK+0.5) + aff[1][3],
                aff[2][0]*(lesionI+0.5) + aff[2][1]*(lesionJ+0.5) + aff[2][2]*(lesionK+0.5) + aff[2][3],
              ];
              ct.armMarkLesion();
              const ovArm = ct.overlaysEnabled();
              ct.setWorld(lesionWorld);
              const corScr = ct.screenOfWorld('coronal', lesionWorld);
              const corCanvas = document.querySelector('.ct-pane-coronal canvas');
              const corRect = corCanvas.getBoundingClientRect();
              corCanvas.dispatchEvent(new PointerEvent('pointerdown', {
                bubbles: true,
                clientX: corRect.left + corScr.x,
                clientY: corRect.top + corScr.y,
                button: 0, pointerId: 2,
              }));
              const placed = ct.getProvisional();
              await ct.confirmLesion();
              const lesion = ct.getLesion();
              const perPane = {
                axial: ct.screenOfWorld('axial', lesion && lesion.ras_mm),
                coronal: ct.screenOfWorld('coronal', lesion && lesion.ras_mm),
                sagittal: ct.screenOfWorld('sagittal', lesion && lesion.ras_mm),
              };
              const dbg = ct.debugState();
              const ovAfter = ct.overlaysEnabled();
              ct.setOverlay('airway', true);
              ct.setOverlay('route', true);
              await ct.clearLesion();
              const afterDelete = ct.getLesion();
              out.checks.lesion = {
                placed, lesion, perPane, dbg, ovArm, ovAfter, afterDelete,
                ok: !!placed
                  && !!lesion && !!lesion.ras_mm
                  && Math.hypot(lesion.ras_mm[0]-placed[0],
                                lesion.ras_mm[1]-placed[1],
                                lesion.ras_mm[2]-placed[2]) < 2.0
                  && !!(perPane.axial && perPane.coronal && perPane.sagittal)
                  && ovArm.airwayDisabled === false
                  && ovAfter.airwayDisabled === false
                  && afterDelete == null,
              };

              return out;
            }"""
        )

        page.screenshot(path=str(SHOTS / f"{case_id}_ct_panes.png"), full_page=True)
        # Overlays ON screenshot (re-confirm briefly for shot with masks+route)
        page.evaluate(
            """async () => {
              const ct = window.__p3_debug.ct();
              const meta = ct.getMeta();
              const aff = meta.affine;
              const i = Math.floor(meta.shape[0]/2);
              const j = Math.floor(meta.shape[1]/2);
              const k = Math.floor(meta.shape[2]/2);
              const w = [
                aff[0][0]*(i+0.5)+aff[0][1]*(j+0.5)+aff[0][2]*(k+0.5)+aff[0][3],
                aff[1][0]*(i+0.5)+aff[1][1]*(j+0.5)+aff[1][2]*(k+0.5)+aff[1][3],
                aff[2][0]*(i+0.5)+aff[2][1]*(j+0.5)+aff[2][2]*(k+0.5)+aff[2][3],
              ];
              ct.armMarkLesion();
              ct.setProvisional(w);
              await ct.confirmLesion();
              ct.setOverlay('airway', true);
              ct.setOverlay('route', true);
              ct.drawAll();
            }"""
        )
        page.screenshot(path=str(SHOTS / f"{case_id}_ct_overlays.png"), full_page=True)
        page.evaluate("() => window.__p3_debug.ct().setCtViewLocal('head')")
        page.screenshot(path=str(SHOTS / f"{case_id}_ct_head.png"), full_page=True)
        page.evaluate("async () => { await window.__p3_debug.ct().clearLesion(); }")

        errors = _console_errors(page)
        browser.close()

    cprint(json.dumps(results["checks"], indent=2, default=str), "yellow")
    assert results["checks"]["fitPlane"]["ok"], results["checks"]["fitPlane"]
    assert results["checks"]["crosshair"]["ok"], results["checks"]["crosshair"]
    assert results["checks"]["wheel"]["ok"], results["checks"]["wheel"]
    assert results["checks"]["letters"]["ok"], results["checks"]["letters"]
    assert results["checks"]["route"]["ok"], results["checks"]["route"]
    assert results["checks"]["coronal"]["ok"], results["checks"]["coronal"]
    assert results["checks"]["arrowsOutside"]["ok"], results["checks"]["arrowsOutside"]
    assert results["checks"]["lesion"]["ok"], results["checks"]["lesion"]
    assert not errors, errors

    # Writable copies under tmp may have lesion history; real tree must not.
    real_local = ROOT / "data/extracted/local/local-001/case.json"
    real_data = json.loads(real_local.read_text())
    assert "lesion" not in real_data
    assert not (ROOT / "app_state" / "settings.json").is_file()


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q", "--tb=short"]))
