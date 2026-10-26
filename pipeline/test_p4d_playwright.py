# Changes: Phase 4d happy path drives steps 2–8 via UI (CT lesion, Segment with
# fake job launcher, Review, RouteStart, top candidate, walk Yes, Sign & freeze).
# API only for setup + read-only assertions; keep post-freeze retarget check.
# Screenshots → out/p4d_shots/. Fake runner stamps run.json; never TotalSegmentator.
from __future__ import annotations

import json
import os
import subprocess
import time
import urllib.request
from pathlib import Path

import pytest
from termcolor import cprint

from test_p3_playwright import build_tmp_pipeline

ROOT = Path(__file__).resolve().parent
SHOTS_REPO = ROOT / "out" / "p4d_shots"
PORT = 8770
BASE = f"http://127.0.0.1:{PORT}"
VIEWPORT = {"width": 1400, "height": 900}

# Ordering-aware workflow uses second-resolution timestamps.
_STEP_GAP_S = 1.1

_FAKE_SERVER_BOOT = r'''
# Changes: p4d playwright server bootstrap — inject fake segment job launcher.
"""Stamp run.json / keep tmp ts_out artifacts; never invoke TotalSegmentator."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

# Popen cwd is pipeline/; script may live under fake_home (not on default path).
_cwd = Path.cwd()
if str(_cwd) not in sys.path:
    sys.path.insert(0, str(_cwd))
_repo = _cwd.parent
if str(_repo) not in sys.path:
    sys.path.insert(0, str(_repo))

import repo_root
from termcolor import cprint

repo_root.ensure()

from app.jobs import set_job_launcher  # noqa: E402
from run_totalsegmentator import ct_identity  # noqa: E402


class _OkProc:
    def __init__(self, lines: list[str] | None = None):
        self.pid = 424242
        self.stdout = iter(lines or ["fake segment ok\n"])
        self._code = 0

    def wait(self, timeout=None):
        return self._code


def _stamp_run_json(ts_out: Path, ct: Path) -> None:
    ident = ct_identity(ct)
    payload = json.dumps({"ct": ident, "device": "mps", "fast": False}) + "\n"
    for sub in ("airways_source", "lobes"):
        d = ts_out / sub
        d.mkdir(parents=True, exist_ok=True)
        marker = d / "run.json"
        if marker.is_symlink() or marker.exists():
            marker.unlink()
        marker.write_text(payload)
        cprint(f"fake segment stamped {marker}", "cyan")


def fake_launcher(cmd: list[str], cwd: Path):
    joined = " ".join(str(c) for c in cmd)
    if "run_totalsegmentator.py" in joined:
        # cmd: uv run python run_totalsegmentator.py <ct> -o <ts_out> ...
        try:
            idx = next(i for i, c in enumerate(cmd) if str(c).endswith("run_totalsegmentator.py"))
        except StopIteration:
            idx = cmd.index("run_totalsegmentator.py")
        ct = Path(cmd[idx + 1])
        out = None
        for i, a in enumerate(cmd):
            if a in ("-o", "--out") and i + 1 < len(cmd):
                out = Path(cmd[i + 1])
                break
        if out is None:
            raise RuntimeError(f"fake launcher: no -o in {cmd!r}")
        # Artifacts already linked into the tmp tree; only stamp markers.
        _stamp_run_json(out, ct)
        return _OkProc(["fake totalsegmentator (existing ts_out)\n"])
    if "build_graph.py" in joined:
        # Graph already present via per-file symlink in tmp pipeline.
        return _OkProc(["fake build_graph (existing graph)\n"])
    return subprocess.Popen(
        cmd,
        cwd=str(cwd),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        start_new_session=True,
        env={k: v for k, v in os.environ.items() if k != "PYTHONPATH"},
    )


cprint("p4d fake segment launcher armed", "yellow")
set_job_launcher(fake_launcher)

from app.server import main  # noqa: E402

if __name__ == "__main__":
    main(sys.argv[1:])
'''


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
    boot = fake_home / "p4d_fake_server.py"
    boot.write_text(_FAKE_SERVER_BOOT.lstrip("\n"))
    return {"root": root, "home": fake_home, "boot": boot}


@pytest.fixture(scope="module")
def server(tmp_pipeline):
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    env["HOME"] = str(tmp_pipeline["home"])
    env["LUNGNAV_PIPELINE_ROOT"] = str(tmp_pipeline["root"])
    cprint(f"p4d server on {PORT} root={tmp_pipeline['root']}", "cyan")
    # Drain to a file — PIPE without a reader eventually blocks the server.
    log_path = tmp_pipeline["home"] / "p4d_server.log"
    log_f = open(log_path, "w", encoding="utf-8")
    proc = subprocess.Popen(
        [
            "env", "-u", "PYTHONPATH",
            "uv", "run", "python", str(tmp_pipeline["boot"]),
            "--port", str(PORT),
        ],
        cwd=str(ROOT),
        env=env,
        stdout=log_f,
        stderr=subprocess.STDOUT,
    )
    try:
        _wait_server()
        yield {"proc": proc, "pipeline": tmp_pipeline, "log": log_path}
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
        log_f.close()


def _attach_console(page) -> None:
    page._p4d_errors = []

    def on_msg(msg):
        if msg.type == "error":
            page._p4d_errors.append(msg.text)

    def on_page_error(err):
        page._p4d_errors.append(str(err))

    page.on("console", on_msg)
    page.on("pageerror", on_page_error)


def _api_get(page, path: str):
    """Read-only HTTP helper for assertions / coordinate setup reads."""
    return page.request.get(f"{BASE}{path}")


def _iframe_frame(page):
    for frame in page.frames:
        if frame != page.main_frame and "renderer" in (frame.url or ""):
            return frame
    for frame in page.frames:
        if frame != page.main_frame:
            return frame
    return page.main_frame


def _wait_step(page, step: int, status: str, timeout: float = 60_000) -> None:
    page.wait_for_function(
        """([step, status]) => {
          const nodes = [...document.querySelectorAll('#workflow .step')];
          const el = nodes.find(n => (n.textContent || '').includes(`Step ${step}`));
          return !!(el && el.classList.contains(status));
        }""",
        arg=[step, status],
        timeout=timeout,
    )


def _lesion_ras_from_graph(pipeline: Path, page) -> list[float]:
    """Prefer RLL tip from graph; fall back to CT centre (read-only)."""
    graph_path = pipeline / "out/local-001/local-001_graph.json"
    if graph_path.is_file():
        graph = json.loads(graph_path.read_text())
        rll = [
            e for e in graph.get("edges") or []
            if "right lower" in str(e.get("lobe") or "").lower()
        ]
        if rll:
            tip = rll[0]["centreline_mm"][-1]
            return [float(tip[0]), float(tip[1]), float(tip[2])]
    meta = _api_get(page, "/api/cases/local-001/ct/meta").json()
    affine = meta["affine"]
    shape = meta["shape"]
    i, j, k = shape[0] / 2, shape[1] / 2, shape[2] / 2
    return [
        affine[0][0] * i + affine[0][1] * j + affine[0][2] * k + affine[0][3],
        affine[1][0] * i + affine[1][1] * j + affine[1][2] * k + affine[1][3],
        affine[2][0] * i + affine[2][1] * j + affine[2][2] * k + affine[2][3],
    ]


def test_p4d_happy_path_local001(server):
    sync_playwright = _playwright_or_skip()
    SHOTS_REPO.mkdir(parents=True, exist_ok=True)
    pipeline = server["pipeline"]["root"]

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport=VIEWPORT)
        _attach_console(page)

        cprint("setup: clinician + series confirm (API)", "cyan")
        assert page.request.put(
            f"{BASE}/api/clinician",
            data=json.dumps({"role": "Radiologist", "initials": "A.B."}),
            headers={"Content-Type": "application/json"},
        ).ok
        assert page.request.post(
            f"{BASE}/api/cases/local-001/series/confirm",
            data=json.dumps({"overridden": False}),
            headers={"Content-Type": "application/json"},
        ).ok
        time.sleep(_STEP_GAP_S)

        ras = _lesion_ras_from_graph(pipeline, page)

        cprint("open shell + CT panes; Mark Lesion via axial click", "cyan")
        page.goto(
            f"{BASE}/?case=local-001&allow_review=1&debug=1",
            wait_until="domcontentloaded",
            timeout=120_000,
        )
        page.wait_for_selector("#workflow .step", timeout=60_000)
        _wait_step(page, 2, "available")
        page.wait_for_selector("#stage iframe", timeout=60_000)
        page.wait_for_function(
            """() => {
              const iframe = document.querySelector('#stage iframe');
              if (!iframe || !iframe.contentWindow) return false;
              try {
                return typeof iframe.contentWindow.setVariant === 'function';
              } catch (_) { return false; }
            }""",
            timeout=120_000,
        )
        frame = _iframe_frame(page)
        # Prefer the visible "CT panes" switcher button (variant index 3).
        frame.locator("button", has_text="CT panes").click()
        frame.wait_for_selector("#ct-mark-lesion", timeout=120_000)
        # debug=1 exposes window.__p3_ct after mount; wait until CT bytes loaded.
        frame.wait_for_function(
            """() => {
              const api = window.__p3_ct;
              return !!(api && api.isLoaded && api.isLoaded());
            }""",
            timeout=180_000,
        )
        frame.wait_for_function(
            """() => {
              const c = document.querySelector(
                '.ct-pane-axial canvas, .ct-pane[data-pane="axial"] canvas'
              );
              if (!c) return false;
              const r = c.getBoundingClientRect();
              return r.width > 40 && r.height > 40;
            }""",
            timeout=30_000,
        )

        frame.click("#ct-mark-lesion")
        placed = frame.evaluate(
            """(ras) => {
              const api = window.__p3_ct;
              if (!api) return { ok: false, reason: 'no __p3_ct' };
              if (!api.isLoaded()) return { ok: false, reason: 'not loaded' };
              api.setWorld(ras);
              const scr = api.screenOfWorld('axial', ras);
              const canvas = document.querySelector(
                '.ct-pane-axial canvas, .ct-pane[data-pane="axial"] canvas'
              );
              if (!scr) return { ok: false, reason: 'no screen', loaded: api.isLoaded() };
              if (!canvas) return { ok: false, reason: 'no canvas', scr };
              const rect = canvas.getBoundingClientRect();
              canvas.dispatchEvent(new PointerEvent('pointerdown', {
                bubbles: true,
                clientX: rect.left + scr.x,
                clientY: rect.top + scr.y,
                button: 0,
                pointerId: 11,
              }));
              const prov = api.getProvisional();
              return { ok: !!prov, provisional: prov };
            }""",
            ras,
        )
        assert placed.get("ok"), placed
        frame.click("#ct-confirm-lesion")
        frame.wait_for_function(
            """() => {
              const api = window.__p3_ct;
              const L = api && api.getLesion && api.getLesion();
              return !!(L && L.ras_mm);
            }""",
            timeout=30_000,
        )
        page.screenshot(path=str(SHOTS_REPO / "01_lesion.png"), full_page=True)
        _wait_step(page, 3, "available", timeout=30_000)
        time.sleep(_STEP_GAP_S)

        cprint("Segment via UI (fake job runner)", "cyan")
        assert page.is_enabled("#segment-btn")
        page.click("#segment-btn")
        page.wait_for_selector("#job-panel.visible", timeout=15_000)
        page.wait_for_function(
            """() => {
              const t = (document.querySelector('#job-status') || {}).textContent || '';
              return /succeeded/i.test(t) || /failed/i.test(t);
            }""",
            timeout=120_000,
        )
        job_txt = page.locator("#job-status").inner_text()
        assert "succeeded" in job_txt.lower(), job_txt
        page.screenshot(path=str(SHOTS_REPO / "02_segment.png"), full_page=True)
        time.sleep(_STEP_GAP_S)

        cprint("Review sign-off via Review panel", "cyan")
        page.click("#toggle-review")
        page.wait_for_selector("#review-panel.visible")
        # Read-only: decide confirm vs reject from review payload.
        review = _api_get(page, "/api/cases/local-001/review").json()
        for bridge in review.get("bridges") or []:
            bid = bridge.get("id")
            if not bid:
                continue
            note = "ct evidence ok" if bridge.get("mappable", True) else "not airway"
            page.fill(f'#review-bridges input[data-bridge-note="{bid}"]', note)
            if bridge.get("mappable", True):
                page.click(f'#review-bridges button[data-bridge-confirm="{bid}"]')
            else:
                page.click(f'#review-bridges button[data-bridge-reject="{bid}"]')
            time.sleep(_STEP_GAP_S)
        for flag in review.get("review_flags") or []:
            if flag.get("resolved"):
                continue
            key = f"{flag['kind']}|{flag['lobe']}"
            page.fill(
                f'#review-flags input[data-flag-finding="{key}"]',
                flag.get("finding") or "ok",
            )
            page.click(f'#review-flags button[data-flag-approve="{key}"]')
            time.sleep(_STEP_GAP_S)
        page.screenshot(path=str(SHOTS_REPO / "03_review.png"), full_page=True)
        _wait_step(page, 5, "available", timeout=30_000)
        time.sleep(_STEP_GAP_S)

        cprint("Confirm RouteStart via shell", "cyan")
        page.click("#confirm-route-start-btn")
        _wait_step(page, 6, "available", timeout=30_000)
        page.screenshot(path=str(SHOTS_REPO / "04_route_start.png"), full_page=True)
        time.sleep(_STEP_GAP_S)

        cprint("Choose top RouteTarget candidate card", "cyan")
        page.click("#toggle-route-target")
        page.wait_for_selector("#route-target-panel.visible")
        page.wait_for_selector("#route-target-cards .rt-card", timeout=30_000)
        page.click('#route-target-cards .rt-card[data-rt-rank="1"]')
        _wait_step(page, 7, "available", timeout=30_000)
        page.screenshot(path=str(SHOTS_REPO / "05_route_target.png"), full_page=True)
        time.sleep(_STEP_GAP_S)

        cprint("Start walk-through; Yes at every fork", "cyan")
        page.click("#toggle-walk")
        page.wait_for_selector("#walk-panel.visible")
        page.wait_for_function(
            """() => {
              const m = document.querySelector('#walk-msg');
              return !!(m && m.textContent && !/Loading forks/i.test(m.textContent));
            }""",
            timeout=60_000,
        )
        n_forks = page.locator("#walk-forks .row").count()
        cprint(f"walk forks: {n_forks}", "cyan")
        page.click("#start-walk-btn")
        page.wait_for_timeout(800)
        for i in range(n_forks):
            time.sleep(_STEP_GAP_S)
            row = page.locator("#walk-forks .row").nth(i)
            row.locator(f"#walk-note-{i}").fill("branch confirmed on CT")
            with page.expect_response(
                lambda r: "/walkthrough/" in r.url and r.request.method == "PUT",
                timeout=30_000,
            ) as resp_info:
                row.get_by_role("button", name="Yes", exact=True).click()
            resp = resp_info.value
            assert resp.ok, f"walk PUT failed: {resp.status} {resp.text()}"
            page.wait_for_function(
                """() => {
                  const m = document.querySelector('#walk-msg');
                  return !!(m && m.textContent
                    && !/Loading forks/i.test(m.textContent)
                    && !/Walk failed/i.test(m.textContent));
                }""",
                timeout=30_000,
            )
        page.screenshot(path=str(SHOTS_REPO / "06_walk.png"), full_page=True)
        case = json.loads(
            (pipeline / "data/extracted/local/local-001/case.json").read_text()
        )
        bif = [
            r for r in case.get("records") or []
            if r.get("kind") == "bifurcation_confirmed"
        ]
        assert len(bif) >= n_forks, (bif, case.get("walkthrough"))
        with urllib.request.urlopen(
            f"{BASE}/api/cases/local-001/workflow", timeout=60
        ) as res:
            wf = json.loads(res.read())
        s7 = next(s for s in wf["steps"] if s["step"] == 7)
        s8 = next(s for s in wf["steps"] if s["step"] == 8)
        assert s7["status"] == "done", (s7, s8, case.get("walkthrough"), bif)
        assert s8["status"] == "available", (s7, s8)

        cprint("Sign & freeze + open report", "cyan")
        # Fresh shell load — avoids a busy walkthrough iframe stalling fetch.
        page.goto(
            f"{BASE}/?case=local-001&allow_review=1",
            wait_until="domcontentloaded",
            timeout=120_000,
        )
        page.wait_for_selector("#workflow .step", timeout=60_000)
        _wait_step(page, 8, "available", timeout=30_000)
        page.wait_for_function(
            "() => { const b = document.querySelector('#toggle-signoff'); return b && !b.disabled; }",
            timeout=15_000,
        )
        page.click("#toggle-signoff")
        page.wait_for_selector("#signoff-panel.visible")
        page.wait_for_selector("#signoff-uncertainty li", timeout=60_000)
        page.fill("#signoff-statement", "Happy-path sign-off for local-001.")
        page.click("#signoff-btn")
        page.wait_for_selector("#signoff-report-link:not([hidden])", timeout=60_000)
        href = page.get_attribute("#signoff-report-link", "href")
        assert href and "/versions/" in href and "/report" in href, href
        report = page.request.get(f"{BASE}{href}" if href.startswith("/") else href)
        assert report.ok, report.text()
        body = report.text()
        assert "PatientName" not in body
        assert "SHA-256" in body
        assert report.headers.get("content-security-policy", "").startswith("default-src")
        page.goto(
            f"{BASE}{href}" if href.startswith("/") else href,
            wait_until="domcontentloaded",
        )
        page.screenshot(path=str(SHOTS_REPO / "07_report.png"), full_page=True)
        v1_body = body
        page.screenshot(path=str(SHOTS_REPO / "08_after_signoff.png"), full_page=True)

        cprint("change RouteTarget after freeze → N+1; v1 report unchanged", "cyan")
        time.sleep(_STEP_GAP_S)
        rtc2 = _api_get(page, "/api/cases/local-001/route-candidates").json()
        cands2 = rtc2.get("candidates") or []
        assert cands2
        alt = cands2[0]
        new_sample = max(0, int(alt["sample_index"]) - 3)
        if new_sample == int(alt["sample_index"]) and len(cands2) > 1:
            alt = cands2[1]
            new_sample = int(alt["sample_index"])
        rt2 = page.request.put(
            f"{BASE}/api/cases/local-001/route-target",
            data=json.dumps({
                "edge_id": alt["edge_id"], "sample_index": new_sample,
            }),
            headers={"Content-Type": "application/json"},
        )
        assert rt2.ok, rt2.text()
        wf = _api_get(page, "/api/cases/local-001/workflow").json()
        s7 = next(s for s in wf["steps"] if s["step"] == 7)
        s8 = next(s for s in wf["steps"] if s["step"] == 8)
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
        page.screenshot(path=str(SHOTS_REPO / "09_after_retarget.png"), full_page=True)
        errors = list(getattr(page, "_p4d_errors", []))
        assert errors == [], errors
        browser.close()
    cprint(f"shots in {SHOTS_REPO}", "green")
