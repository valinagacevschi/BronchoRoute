# Changes: rejecting a chosen target shows a stale reason and refreshed proposals; reconfirm recovers.
# Changes: CT bridge controls, slab markers, live payload refresh and viewer-only UI.
import json
import os
import selectors
import subprocess
import time
import urllib.request
from pathlib import Path

import pytest

from test_bridge_confirm import seed_bridges
from test_viewer_v2_playwright import _playwright_or_skip


@pytest.fixture(params=[False, True], ids=["editable", "viewer-only"])
def bridge_server(tmp_path, request):
    seed_bridges(tmp_path, count=1)
    home = tmp_path / "fake_home"
    home.mkdir()
    env = {**os.environ, "HOME": str(home), "LUNGNAV_PIPELINE_ROOT": str(tmp_path),
           "LUNGNAV_VIEWER_ONLY": "1" if request.param else "0",
           "BRONCHOROUTE_LEGACY_REVIEW": "0"}
    env.pop("PYTHONPATH", None)
    env.pop("LUNGNAV_PORT", None)
    env.pop("LUNGNAV_HOST", None)
    proc = subprocess.Popen(
        ["uv", "run", "python", "app_server.py", "--port", "0"],
        cwd=Path(__file__).resolve().parent, env=env,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    try:
        with selectors.DefaultSelector() as selector:
            selector.register(proc.stdout, selectors.EVENT_READ)
            assert selector.select(timeout=20), "missing port announcement"
        line = proc.stdout.readline().strip()
        assert line.startswith("BRONCHOROUTE_PORT=")
        base = f"http://127.0.0.1:{int(line.split('=', 1)[1])}"
        for _ in range(200):
            assert proc.poll() is None, "bridge server exited"
            try:
                with urllib.request.urlopen(f"{base}/api/features", timeout=1):
                    break
            except OSError:
                time.sleep(0.1)
        else:
            pytest.fail("bridge server did not become ready")
        yield base, tmp_path, request.param
    finally:
        proc.terminate()
        proc.wait(timeout=10)


def test_ct_bridge_decisions(bridge_server):
    sync_playwright = _playwright_or_skip()
    base, root, viewer = bridge_server
    shots = Path(os.environ.get("TMPDIR", "/tmp")) / "bronchoroute-35"
    shots.mkdir(parents=True, exist_ok=True)
    case_path = root / "data/extracted/local/local-001/case.json"
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        if not viewer:
            assert page.request.put(f"{base}/api/settings", data={"initials": "V.T."}).ok
        page.goto(f"{base}/renderer.template.html?case=local-001&allow_review=1&debug=1&variant=ct-panes")
        row = page.locator(".bridge-row")
        row.wait_for()
        assert row.count() == 1
        assert row.locator(".bridge-state").inner_text() == "Unconfirmed"
        bridge_id = row.get_attribute("data-bridge-id")
        bridge = page.request.get(f"{base}/api/cases/local-001/bridges").json()["bridges"][0]
        row.locator(".bridge-goto").click()
        assert page.evaluate("window.__p3_debug.sharedPos()") == bridge["midpoint_ras_mm"]

        def marker(color):
            page.wait_for_function(
                "color => Object.values(window.__p3_debug.ct().getBridgeDraw()).flat().some(m => m.color === color)",
                arg=color,
            )
            # Check actual canvas pixels on the ring, not just the draw record.
            assert page.evaluate("""color => {
              const ct = window.__p3_debug.ct();
              const rgb = color === '#3a3' ? [51,170,51] : color === '#888' ? [136,136,136] : [217,149,32];
              return Object.entries(ct.getBridgeDraw()).some(([key, marks]) => marks.some(m => {
                const canvas = document.querySelector(`.ct-pane-${key} canvas`);
                const ratio = canvas.width / canvas.clientWidth;
                const ctx = canvas.getContext('2d');
                const pixels = ctx.getImageData(Math.round((m.x-12)*ratio), Math.round((m.y-12)*ratio), Math.round(24*ratio), Math.round(24*ratio)).data;
                for (let i=0; i<pixels.length; i+=4) if (rgb.every((v,j)=>Math.abs(pixels[i+j]-v)<4)) return true;
                return false;
              }));
            }""", color)

        marker("#d99520")
        if viewer:
            assert row.locator(".bridge-confirm, .bridge-reject").count() == 0
        else:
            payloads = []
            page.on("response", lambda response: payloads.append(response.url) if "/payload" in response.url else None)
            page.screenshot(path=str(shots / "bridges-unconfirmed.png"), full_page=True)
            # Downstream CT branch remains in the payload and cannot be picked.
            assert page.evaluate("window.__p3_debug.airway().edges.some(e => e.oi === 2 && e.selectable === false)")
            page.locator("#ct-mark-lesion").click()
            page.evaluate("window.__p3_debug.ct().setWorld([24.5,20.5,20.5])")
            screen = page.evaluate("window.__p3_debug.ct().screenOfWorld('coronal',[24.5,20.5,20.5])")
            box = page.locator(".ct-pane-coronal canvas").bounding_box()
            page.mouse.click(box["x"] + screen["x"], box["y"] + screen["y"])
            page.locator("#ct-confirm-lesion").click()
            page.wait_for_function("window.__p3_debug.airway().lesion !== null")
            page.locator("#route-target-pick").click()
            with page.expect_response(lambda response: "/route-target" in response.url and response.request.method == "PUT") as blocked:
                page.mouse.click(box["x"] + screen["x"], box["y"] + screen["y"])
            assert blocked.value.status == 422
            assert page.evaluate("window.__p3_debug.airway().route_target") is None
            row.locator(".bridge-goto").click()
            row.locator(".bridge-confirm").click()
            page.wait_for_function("document.querySelector('.bridge-state').textContent === 'Confirmed'")
            marker("#3a3")
            assert row.locator(".bridge-confirm").is_disabled()
            assert json.loads(case_path.read_text())["bridge_decisions"][bridge_id]["decision"] == "confirmed"
            assert page.evaluate("window.__p3_debug.airway().edges.some(e => e.oi === 2 && e.selectable !== false)")
            assert payloads
            page.screenshot(path=str(shots / "bridges-confirmed.png"), full_page=True)
            # Pick the now reachable downstream branch, then reject its bridge.
            page.evaluate("window.__p3_debug.ct().setWorld([24.5,20.5,20.5])")
            screen = page.evaluate("window.__p3_debug.ct().screenOfWorld('coronal',[24.5,20.5,20.5])")
            box = page.locator(".ct-pane-coronal canvas").bounding_box()
            page.mouse.click(box["x"] + screen["x"], box["y"] + screen["y"])
            page.wait_for_function("window.__p3_debug.airway().route_target?.edge_id === 2")
            row.locator(".bridge-goto").click()
            row.locator(".bridge-reject").click()
            page.wait_for_function("document.querySelector('.bridge-state').textContent === 'Rejected'")
            marker("#888")
            assert row.locator(".bridge-reject").is_disabled()
            assert json.loads(case_path.read_text())["bridge_decisions"][bridge_id]["decision"] == "rejected"
            assert page.evaluate("window.__p3_debug.airway().route_target.stale")
            reason = page.evaluate("window.__p3_debug.airway().route_target.reason")
            assert reason == "not reachable from RouteStart"
            assert page.locator("#route-target-status").inner_text() == reason
            assert "unknown edge_id" not in page.locator("#route-target-status").inner_text()
            assert "is-error" not in (page.locator("#route-target-status").get_attribute("class") or "")
            assert page.locator(".route-target-card[aria-pressed='true']").count() == 0
            assert "(confirmed)" not in page.locator("#route-target-cards").inner_text()
            proposals = page.request.get(f"{base}/api/cases/local-001/route-candidates")
            assert proposals.ok
            assert page.locator(".route-target-card").count() == len(proposals.json()["candidates"])
            assert page.evaluate("window.__p3_debug.airway().edges.every(e => e.oi !== 2)")
            page.screenshot(path=str(shots / "bridges-rejected.png"), full_page=True)
            page.locator("#switcher button", has_text="Topology").click()
            page.wait_for_function("window.__p3_debug.getVariant() === 'topology'")
            page.locator("#switcher button", has_text="Anatomical").click()
            page.wait_for_function("window.__p3_debug.getVariant() === 'anatomical'")
            page.locator("#switcher button", has_text="CT panes").click()
            row.locator(".bridge-confirm").click()
            page.wait_for_function("document.querySelector('.bridge-state').textContent === 'Confirmed'")
            assert page.evaluate("window.__p3_debug.airway().route_target.stale") is False
            assert page.locator("#route-target-status").inner_text() == ""
        assert not errors
        browser.close()
