# Changes: Case Library clicks, outdated prompt, and instant library-hit open.
from __future__ import annotations

import os
import re
import socket
import subprocess
import time
import urllib.request
from pathlib import Path

import pytest

from test_app_server import _write_case_tree
from test_viewer_v2_playwright import _playwright_or_skip
from tracer_fixture import write_archive


@pytest.fixture
def library_server(tmp_path):
    root = tmp_path / "state"
    root.mkdir()
    _write_case_tree(root, "local-001")
    _write_case_tree(root, "local-002")
    archive = write_archive(tmp_path / "input.zip")
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    base = f"http://127.0.0.1:{port}"
    env = {
        **os.environ,
        "LUNGNAV_PIPELINE_ROOT": str(root),
        "BRONCHOROUTE_FAKE_SEGMENT": "1",
        "BRONCHOROUTE_TESTING": "1",
        "BRONCHOROUTE_FAKE_STEP_SECONDS": "0.05",
        "HOME": str(tmp_path),
    }
    env.pop("PYTHONPATH", None)
    proc = subprocess.Popen(
        ["uv", "run", "python", "app_server.py", "--port", str(port)],
        cwd=Path(__file__).resolve().parent,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        for _ in range(200):
            assert proc.poll() is None, "library server exited before readiness"
            try:
                with urllib.request.urlopen(f"{base}/healthz", timeout=1):
                    break
            except OSError:
                time.sleep(0.1)
        else:
            pytest.fail("library server did not start")
        yield base, root, archive
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=10)


def test_library_screen_and_hit(library_server):
    base, root, archive = library_server
    shots = Path(os.environ.get("TMPDIR", "/tmp")) / "bronchoroute-31"
    shots.mkdir(parents=True, exist_ok=True)
    posts = []
    armed = {"on": False}

    def watch(request):
        if armed["on"] and request.method == "POST" and request.url.rstrip("/").endswith("/api/imports"):
            posts.append(request.url)

    with _playwright_or_skip()() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.on("console", lambda message: errors.append(message.text) if message.type == "error" else None)
        page.on("request", watch)
        page.goto(f"{base}/?case=local-001")
        page.locator("#outdated-note").wait_for(state="visible")
        assert page.locator("#outdated-text").inner_text() == "Processed with an older version — Reprocess?"
        assert page.locator("#outdated-reprocess").is_visible()
        assert page.locator("#outdated-open").is_visible()
        page.locator("#switcher button").first.wait_for(timeout=120_000)
        assert page.locator("#library-open").evaluate("""link => {
            const rect = link.getBoundingClientRect();
            const hit = document.elementFromPoint(rect.left + rect.width / 2, rect.top + rect.height / 2);
            return hit === link;
        }""")
        page.screenshot(path=str(shots / "outdated.png"))
        page.locator("#outdated-open").click()
        page.locator("#outdated-note").wait_for(state="hidden")
        page.locator("#library-open").click()
        page.locator("#library-screen").wait_for(state="visible")
        page.locator("#library-rows tr").nth(1).wait_for()
        assert page.locator("#library-rows tr").count() == 2
        table = page.locator("#library-table").inner_text()
        for column in ("Case", "Study date", "Series", "Processed", "Size", "Outdated"):
            assert column in table
        assert "local-001" in table
        assert "local-002" in table
        assert "Yes" in table
        assert page.locator("#library-warn").is_hidden()
        assert page.locator("#library-total").inner_text().startswith("Total size")
        page.screenshot(path=str(shots / "library.png"))
        page.locator("#library-rows tr").filter(has_text="local-002").locator(".library-delete").click()
        page.locator("#library-confirm").wait_for(state="visible")
        assert "local-002" in page.locator("#library-confirm-text").inner_text()
        page.locator("#library-confirm-yes").click()
        page.locator("#library-rows tr").filter(has_text="local-002").wait_for(state="detached")
        assert page.locator("#library-rows tr").count() == 1
        assert not (root / "data/extracted/local/local-002").exists()
        assert not (root / "ts_out/local-002").exists()
        assert not (root / "out/local-002").exists()
        assert (root / "data/extracted/local/local-001/case.json").is_file()

        page.locator("#archive-open").click()
        page.locator("#archive-choose").wait_for(state="visible")
        with page.expect_file_chooser() as chooser:
            page.locator("#archive-choose").click()
        chooser.value.set_files(str(archive))
        page.locator("#series-picker").wait_for(state="visible")
        page.locator("#clinician-initials").fill("AB")
        page.locator("#series-confirm").click()
        page.locator("#processing").wait_for(state="visible")
        page.wait_for_url(re.compile(r"\?case=local-002"), timeout=60_000)
        page.locator("#switcher button").first.wait_for(timeout=120_000)
        assert page.locator("#outdated-note").is_hidden()

        page.locator("#archive-open").click()
        with page.expect_file_chooser() as chooser:
            page.locator("#archive-choose").click()
        chooser.value.set_files(str(archive))
        page.locator(".series-open").wait_for()
        assert "In library" in page.locator("#study-groups").inner_text()
        assert page.locator(".series-reprocess").is_visible()
        assert page.locator("#series-confirm").is_disabled()
        page.screenshot(path=str(shots / "in-library.png"))
        page.locator("#import-library").click()
        page.locator("#library-rows tr").filter(has_text="local-002").wait_for()
        listed = page.locator("#library-table").inner_text()
        assert "2026-01-01" in listed
        assert "AXIAL STANDARD" in listed
        page.locator("#library-back").click()
        page.locator(".series-open").wait_for()
        armed["on"] = True
        with page.expect_navigation(url=re.compile(r"\?case=local-002"), timeout=15_000):
            page.locator(".series-open").click()
            assert page.locator("#processing").is_hidden()
        assert page.locator("#processing").is_hidden()
        assert page.locator("#outdated-note").is_hidden()
        assert posts == []
        page.locator("#switcher button").first.wait_for(timeout=120_000)
        page.screenshot(path=str(shots / "opened.png"))
        assert not errors, errors
        browser.close()
