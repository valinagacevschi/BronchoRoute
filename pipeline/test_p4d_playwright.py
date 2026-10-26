# Changes: Phase 4d review fixes — UI happy path + post-freeze RouteTarget change
# shows N+1 workflow reason; v1 report unchanged. Screenshots → out/p4d_shots/.
from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path

import pytest
from termcolor import cprint

from test_p3_playwright import build_tmp_pipeline

ROOT = Path(__file__).resolve().parent
SHOTS_REPO = ROOT / "out" / "p4d_shots"
PORT = 8770
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
    tmp = tmp_path_factory.mktemp("p4d_pipeline")
    assert not str(tmp).startswith("/tmp")
    root = build_tmp_pipeline(tmp)
    # Ensure bridges_json exists as a real writable file (not only symlink).
    bridges = root / "ts_out/local-001/airway_bridges.json"
    if bridges.is_symlink() or not bridges.is_file():
        real = ROOT / "ts_out/local-001/airway_bridges.json"
        if real.is_file():
            if bridges.exists() or bridges.is_symlink():
                bridges.unlink()
            bridges.write_text(real.read_text())
    case_path = root / "data/extracted/local/local-001/case.json"
    case = json.loads(case_path.read_text())
    case["records"] = []
    case["versions"] = []
    case["bridge_decisions"] = {}
    case["corrections"] = []
    case.pop("lesion", None)
    case.pop("route_start", None)
    case.pop("route_target", None)
    case.pop("walkthrough", None)
    case_path.write_text(json.dumps(case, indent=2) + "\n")
    (root / "app_state").mkdir(parents=True, exist_ok=True)
    fake_home = root / "fake_home"
    fake_home.mkdir(exist_ok=True)
    return {"root": root, "home": fake_home}


@pytest.fixture(scope="module")
def server(tmp_pipeline):
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    env["HOME"] = str(tmp_pipeline["home"])
    env["LUNGNAV_PIPELINE_ROOT"] = str(tmp_pipeline["root"])
    cprint(f"p4d server on {PORT} root={tmp_pipeline['root']}", "cyan")
    proc = subprocess.Popen(
        [
            "env", "-u", "PYTHONPATH",
            "uv", "run", "python", "app_server.py", "--port", str(PORT),
        ],
        cwd=str(ROOT),
        env=env,
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


def _attach_console(page) -> None:
    page._p4d_errors = []

    def on_msg(msg):
        if msg.type == "error":
            page._p4d_errors.append(msg.text)

    def on_page_error(err):
        page._p4d_errors.append(str(err))

    page.on("console", on_msg)
    page.on("pageerror", on_page_error)


def _api(page, method: str, path: str, data=None):
    kwargs = {"headers": {"Content-Type": "application/json"}}
    if data is not None:
        kwargs["data"] = json.dumps(data)
    if method == "GET":
        return page.request.get(f"{BASE}{path}")
    if method == "PUT":
        return page.request.put(f"{BASE}{path}", **kwargs)
    if method == "POST":
        return page.request.post(f"{BASE}{path}", **kwargs)
    raise ValueError(method)


def test_p4d_happy_path_local001(server):
    sync_playwright = _playwright_or_skip()
    SHOTS_REPO.mkdir(parents=True, exist_ok=True)
    pipeline = server["pipeline"]["root"]

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport=VIEWPORT)
        _attach_console(page)

        cprint("clinician + series confirm", "cyan")
        assert _api(page, "PUT", "/api/clinician", {
            "role": "Radiologist", "initials": "A.B.",
        }).ok
        assert _api(page, "POST", "/api/cases/local-001/series/confirm", {
            "overridden": False,
        }).ok
        # Ordering-aware workflow needs strictly later timestamps per step.
        time.sleep(1.1)

        # Lesion near a distal airway (use CT meta centre as safe default).
        meta = _api(page, "GET", "/api/cases/local-001/ct/meta").json()
        affine = meta["affine"]
        shape = meta["shape"]
        # Voxel centre → RAS via affine.
        i, j, k = shape[0] / 2, shape[1] / 2, shape[2] / 2
        ras = [
            affine[0][0] * i + affine[0][1] * j + affine[0][2] * k + affine[0][3],
            affine[1][0] * i + affine[1][1] * j + affine[1][2] * k + affine[1][3],
            affine[2][0] * i + affine[2][1] * j + affine[2][2] * k + affine[2][3],
        ]
        # Prefer a point near RLL tip from graph if available.
        graph_path = pipeline / "out/local-001/local-001_graph.json"
        if graph_path.is_file():
            graph = json.loads(graph_path.read_text())
            rll = [
                e for e in graph.get("edges") or []
                if "right lower" in str(e.get("lobe") or "").lower()
            ]
            if rll:
                tip = rll[0]["centreline_mm"][-1]
                ras = [float(tip[0]), float(tip[1]), float(tip[2])]
        cprint("mark lesion", "cyan")
        les = _api(page, "PUT", "/api/cases/local-001/lesion", {"ras_mm": ras})
        assert les.ok, les.text()
        time.sleep(1.1)

        cprint("register-existing segment (fake job / existing outputs)", "cyan")
        # Stamp run.json markers matching CT so register-existing accepts.
        from run_totalsegmentator import ct_identity

        ct = pipeline / "data/extracted/local/local-001/local-001_CT.nii.gz"
        ident = ct_identity(ct)
        for sub in ("airways_source", "lobes"):
            d = pipeline / "ts_out/local-001" / sub
            d.mkdir(parents=True, exist_ok=True)
            marker = d / "run.json"
            if marker.is_symlink():
                marker.unlink()
            marker.write_text(json.dumps({
                "ct": ident, "device": "mps", "fast": False,
            }) + "\n")
        reg = _api(page, "POST", "/api/cases/local-001/segment/register-existing")
        assert reg.ok, reg.text()
        time.sleep(1.1)

        cprint("review bridges", "cyan")
        review = _api(page, "GET", "/api/cases/local-001/review").json()
        for bridge in review.get("bridges") or []:
            bid = bridge.get("id")
            if not bid:
                continue
            decision = "confirmed" if bridge.get("mappable", True) else "rejected"
            note = "ct evidence ok" if decision == "confirmed" else "not airway"
            r = _api(page, "PUT", f"/api/cases/local-001/bridges/{bid}", {
                "decision": decision, "note": note,
            })
            assert r.ok, r.text()
            time.sleep(1.05)
        for flag in review.get("review_flags") or []:
            if flag.get("resolved"):
                continue
            r = _api(page, "POST", "/api/cases/local-001/review-signoff", {
                "kind": flag["kind"],
                "lobe": flag["lobe"],
                "decision": "approved",
                "finding": flag.get("finding") or "ok",
            })
            assert r.ok, r.text()
            time.sleep(1.05)
        time.sleep(1.1)

        cprint("RouteStart + RouteTarget candidate 1", "cyan")
        cands_rs = _api(page, "GET", "/api/cases/local-001/route-start/candidates").json()
        node = (cands_rs.get("candidates") or [{"node": 0}])[0]["node"]
        rs = _api(page, "PUT", "/api/cases/local-001/route-start", {"node": node})
        assert rs.ok, rs.text()
        time.sleep(1.1)
        rtc = _api(page, "GET", "/api/cases/local-001/route-candidates").json()
        candidates = rtc.get("candidates") or []
        assert candidates, "expected RouteTarget candidates"
        top = candidates[0]
        rt = _api(page, "PUT", "/api/cases/local-001/route-target", {
            "edge_id": top["edge_id"], "sample_index": top["sample_index"],
        })
        assert rt.ok, rt.text()
        time.sleep(1.1)

        cprint("walk all forks Yes", "cyan")
        payload = _api(page, "GET", "/api/cases/local-001/payload?allow_review=1").json()
        forks = payload.get("forks") or []
        for i in range(len(forks)):
            r = _api(page, "PUT", f"/api/cases/local-001/walkthrough/{i}", {
                "confirmed": True, "note": "branch confirmed on CT",
            })
            assert r.ok, r.text()
            time.sleep(1.05)

        cprint("open shell + walk/signoff panels", "cyan")
        page.goto(
            f"{BASE}/?case=local-001&allow_review=1",
            wait_until="domcontentloaded",
            timeout=120_000,
        )
        page.wait_for_selector("#workflow .step", timeout=60_000)
        page.screenshot(path=str(SHOTS_REPO / "01_workflow.png"), full_page=True)

        page.click("#toggle-walk")
        page.wait_for_selector("#walk-panel.visible")
        page.screenshot(path=str(SHOTS_REPO / "02_walk_panel.png"), full_page=True)

        page.click("#toggle-signoff")
        page.wait_for_selector("#signoff-panel.visible")
        page.wait_for_selector("#signoff-uncertainty li")
        page.screenshot(path=str(SHOTS_REPO / "03_signoff_uncertainty.png"), full_page=True)

        page.click("#start-walk-btn")
        page.screenshot(path=str(SHOTS_REPO / "02b_start_walk.png"), full_page=True)

        cprint("sign & freeze + report", "cyan")
        page.fill("#signoff-statement", "Happy-path sign-off for local-001.")
        page.click("#signoff-btn")
        page.wait_for_selector("#signoff-report-link:not([hidden])", timeout=30_000)
        href = page.get_attribute("#signoff-report-link", "href")
        assert href and "/versions/" in href and "/report" in href, href
        report = page.request.get(f"{BASE}{href}" if href.startswith("/") else href)
        assert report.ok, report.text()
        body = report.text()
        assert "PatientName" not in body
        assert "SHA-256" in body
        assert report.headers.get("content-security-policy", "").startswith("default-src")
        # Open report in the same page for a screenshot.
        page.goto(f"{BASE}{href}" if href.startswith("/") else href,
                  wait_until="domcontentloaded")
        page.screenshot(path=str(SHOTS_REPO / "04_report.png"), full_page=True)
        v1_body = body

        cprint("change RouteTarget after freeze → N+1; v1 report unchanged", "cyan")
        time.sleep(1.1)
        rtc2 = _api(page, "GET", "/api/cases/local-001/route-candidates").json()
        cands2 = rtc2.get("candidates") or []
        assert cands2
        alt = cands2[0]
        new_sample = max(0, int(alt["sample_index"]) - 3)
        if new_sample == int(alt["sample_index"]) and len(cands2) > 1:
            alt = cands2[1]
            new_sample = int(alt["sample_index"])
        rt2 = _api(page, "PUT", "/api/cases/local-001/route-target", {
            "edge_id": alt["edge_id"], "sample_index": new_sample,
        })
        assert rt2.ok, rt2.text()
        wf = _api(page, "GET", "/api/cases/local-001/workflow").json()
        s7 = next(s for s in wf["steps"] if s["step"] == 7)
        s8 = next(s for s in wf["steps"] if s["step"] == 8)
        # Retarget reopens walk; step 8 stays blocked until walk is redone,
        # but still carries the frozen → N+1 sign-off reason.
        assert s7["status"] == "available", s7
        assert "frozen" in (s8.get("reason") or "").lower()
        assert "needs sign-off" in (s8.get("reason") or "")
        report_v1 = page.request.get(f"{BASE}{href}" if href.startswith("/") else href)
        assert report_v1.ok
        assert report_v1.text() == v1_body

        page.goto(
            f"{BASE}/?case=local-001&allow_review=1",
            wait_until="domcontentloaded",
            timeout=60_000,
        )
        page.wait_for_selector("#workflow .step", timeout=60_000)
        page.screenshot(path=str(SHOTS_REPO / "05_after_signoff.png"), full_page=True)
        errors = list(getattr(page, "_p4d_errors", []))
        assert errors == [], errors
        browser.close()
    cprint(f"shots in {SHOTS_REPO}", "green")
