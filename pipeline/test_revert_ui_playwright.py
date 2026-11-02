# Changes: hide legacy review behind the default-off feature flag.
# Revert UI: GET / is the read-only renderer; /workflow is the app shell.
# Radial topology orientation/laterality; viewer mode issues no writes.
# Screenshots → pipeline/out/revert_shots/. Tmp pipeline: link files only.
from __future__ import annotations

import os
import subprocess
import tempfile
import time
from pathlib import Path

import pytest
from termcolor import cprint

from test_p3_playwright import build_tmp_pipeline

ROOT = Path(__file__).resolve().parent
SHOTS = ROOT / "out" / "revert_shots"
PORT = 8771
BASE = f"http://127.0.0.1:{PORT}"
VIEWPORT = {"width": 1400, "height": 900}
WRITE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


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
    tmp = tmp_path_factory.mktemp("revert_ui_pipeline")
    return build_tmp_pipeline(tmp)


@pytest.fixture(scope="module")
def server(tmp_pipeline):
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    env.pop("BRONCHOROUTE_LEGACY_REVIEW", None)
    fake_home = tmp_pipeline / "fake_home"
    fake_home.mkdir(exist_ok=True)
    env["HOME"] = str(fake_home)
    env["LUNGNAV_PIPELINE_ROOT"] = str(tmp_pipeline)
    cprint(f"revert-ui server on {PORT} root={tmp_pipeline}", "cyan")
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


def test_root_is_renderer_not_shell(server):
    import urllib.request

    with urllib.request.urlopen(f"{BASE}/", timeout=10) as res:
        html = res.read().decode("utf-8", errors="replace")
    assert "Bronchial Tree Renderer" in html
    assert 'id="switcher"' in html
    assert 'id="workflow"' not in html

    import urllib.error

    with pytest.raises(urllib.error.HTTPError) as error:
        urllib.request.urlopen(f"{BASE}/workflow", timeout=10)
    assert error.value.code == 404


def test_viewer_mode_no_writes_and_topology_layout(server, tmp_path):
    sync_playwright = _playwright_or_skip()
    SHOTS.mkdir(parents=True, exist_ok=True)
    shots_tmp = tmp_path / "revert_shots"
    shots_tmp.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport=VIEWPORT)
        writes: list[str] = []

        def on_request(req):
            if req.method.upper() in WRITE_METHODS:
                writes.append(f"{req.method} {req.url}")

        page.on("request", on_request)

        cprint("open viewer /?case=local-001", "cyan")
        page.goto(
            f"{BASE}/?case=local-001&allow_review=1&debug=1",
            wait_until="domcontentloaded",
            timeout=120_000,
        )
        page.wait_for_function(
            "() => window.__VIEWER_MODE__ === true && typeof window.setVariant === 'function'",
            timeout=120_000,
        )
        page.wait_for_selector("#case-picker", state="visible", timeout=60_000)
        assert page.locator("#remove-branch-panel").count() == 0
        assert page.locator("#ct-mark-lesion").count() == 0

        # Default view is CT panes (switcher order 1).
        page.wait_for_function(
            "() => window.__p2_debug && window.__p2_debug.getVariant() === 'ct-panes'",
            timeout=30_000,
        )
        switcher = page.evaluate("() => window.__p2_debug.switcherKeys()")
        assert switcher == ["ct-panes", "anatomical", "topology", "endoluminal"], switcher

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

        # Topology orientation + laterality (local-001).
        page.locator("#switcher button", has_text="Topology").click()
        page.wait_for_timeout(500)
        layout = page.evaluate(
            """() => {
              const d = window.__p2_debug;
              if (!d) return null;
              d.setVariant(2);
              const mains = d.mainBronchi();
              const root = d.topoScreen(mains.trachea);
              const carina = d.topoScreen(mains.carina);
              // Leaves under each main bronchus.
              const A = window.AIRWAY;
              const kids = new Map();
              for (const e of A.edges) {
                if (!kids.has(e.f)) kids.set(e.f, []);
                kids.get(e.f).push(e.t);
              }
              const leafIds = (n) => {
                const k = kids.get(n) || [];
                if (!k.length) return [n];
                return k.flatMap(leafIds);
              };
              const meanX = (ids) => {
                let s = 0, n = 0;
                for (const id of ids) {
                  const p = d.topoScreen(id);
                  if (!p) continue;
                  s += p.x; n += 1;
                }
                return n ? s / n : null;
              };
              const leftLeaves = leafIds(mains.left);
              const rightLeaves = leafIds(mains.right);
              return {
                root, carina,
                dx: carina && root ? carina.x - root.x : null,
                dy: carina && root ? carina.y - root.y : null,
                leftMeanX: meanX(leftLeaves),
                rightMeanX: meanX(rightLeaves),
                centreX: root && root.x,
              };
            }"""
        )
        assert layout and layout.get("root") and layout.get("carina"), layout
        assert layout["dy"] > 0, f"trachea edge should point down, got {layout}"
        assert abs(layout["dx"]) < abs(layout["dy"]) * 0.35 + 8, (
            f"trachea edge should be mostly vertical, got {layout}"
        )
        assert layout["rightMeanX"] is not None and layout["leftMeanX"] is not None
        assert layout["rightMeanX"] > layout["centreX"] > layout["leftMeanX"], layout

        # Case picker change should only GET (navigation).
        options = page.locator("#case-picker option")
        n_opts = options.count()
        if n_opts >= 2:
            other = None
            for i in range(n_opts):
                val = options.nth(i).get_attribute("value")
                if val and val != "local-001":
                    other = val
                    break
            if other:
                page.select_option("#case-picker", other)
                page.wait_for_function(
                    "() => window.__VIEWER_MODE__ === true && typeof window.setVariant === 'function'",
                    timeout=120_000,
                )
                page.wait_for_timeout(400)
                # Switch back so screenshots stay on local-001 already taken.
                page.select_option("#case-picker", "local-001")
                page.wait_for_function(
                    """() => {
                      const s = document.querySelector('#case-picker');
                      return s && s.value === 'local-001'
                        && typeof window.setVariant === 'function';
                    }""",
                    timeout=120_000,
                )

        browser.close()

    assert writes == [], f"viewer mode issued writes: {writes}"

    import shutil

    for name in (
        "local-001_anatomical.png",
        "local-001_endoluminal.png",
        "local-001_topology.png",
        "local-001_ct_panes.png",
    ):
        src = shots_tmp / name
        if src.is_file():
            shutil.copy2(src, SHOTS / name)
    cprint(f"revert shots in {SHOTS}", "green")


@pytest.fixture
def synthetic_server(tmp_path):
    import urllib.request

    from test_app_server import _write_case_tree

    _write_case_tree(tmp_path)
    home = tmp_path / "home"
    home.mkdir()
    env = {
        **os.environ,
        "HOME": str(home),
        "LUNGNAV_DATA_ROOT": str(tmp_path),
        "LUNGNAV_PIPELINE_ROOT": str(tmp_path),
        "BRONCHOROUTE_LEGACY_REVIEW": "0",
        "LUNGNAV_VIEWER_ONLY": "0",
    }
    env.pop("PYTHONPATH", None)
    base = "http://127.0.0.1:8773"
    proc = subprocess.Popen(
        ["env", "-u", "PYTHONPATH", "uv", "run", "python",
         "app_server.py", "--port", "8773"],
        cwd=str(ROOT),
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        deadline = time.monotonic() + 30
        while True:
            try:
                with urllib.request.urlopen(f"{base}/healthz", timeout=1):
                    break
            except OSError:
                if time.monotonic() >= deadline:
                    raise RuntimeError("synthetic server did not start")
                time.sleep(0.1)
        yield base
    finally:
        proc.terminate()
        proc.wait(timeout=10)


def test_legacy_off_renderer_switcher(synthetic_server, tmp_path):
    sync_playwright = _playwright_or_skip()
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport=VIEWPORT)
        page.goto(
            f"{synthetic_server}/renderer.template.html?case=local-001&allow_review=1"
            "&debug=1&variant=walkthrough",
            wait_until="domcontentloaded",
        )
        page.wait_for_function("() => typeof window.setVariant === 'function'")
        assert page.locator("#switcher button").count() == 4
        assert page.locator("#switcher button", has_text="Walk-through").count() == 0
        for label in ("CT panes", "Anatomical", "Topology", "Endoluminal"):
            page.locator("#switcher button", has_text=label).click()
        shots = Path(tempfile.mkdtemp(prefix="bronchoroute-25-"))
        screenshot = shots / "legacy-off-renderer.png"
        page.screenshot(path=str(screenshot), full_page=True)
        cprint(f"legacy-off screenshot: {screenshot}", "cyan")
        browser.close()
