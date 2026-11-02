# Changes: wait until processing-cancel is enabled before Archive-open during an import.
# Changes: override-reason and lungs-failure browser flows.
# Changes: settings assertion includes the FileVault note flag.
# Changes: settings payload includes ram_warning_shown.
# Changes: import initials share Settings and leave the other preferences intact.
# Changes: real-click Archive import and cancellation on a free port and isolated state.
from __future__ import annotations

import json
import os
import socket
import subprocess
import time
import urllib.request
from pathlib import Path

import pytest

from test_viewer_v2_playwright import _playwright_or_skip
from tracer_fixture import write_archive, write_choice_archive


def _shots() -> Path:
    path = Path(os.environ.get("TMPDIR", "/tmp")) / "bronchoroute-29-30"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _serve(tmp_path, archive: Path, *, fail: str | None = None):
    root = tmp_path / "state"
    root.mkdir()
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    base = f"http://127.0.0.1:{port}"
    env = {**os.environ, "LUNGNAV_PIPELINE_ROOT": str(root),
           "BRONCHOROUTE_FAKE_SEGMENT": "1", "BRONCHOROUTE_TESTING": "1",
           "BRONCHOROUTE_FAKE_STEP_SECONDS": "1", "HOME": str(tmp_path)}
    if fail:
        env["BRONCHOROUTE_FAKE_FAIL"] = fail
    env.pop("PYTHONPATH", None)
    proc = subprocess.Popen(
        ["uv", "run", "python", "app_server.py", "--port", str(port)],
        cwd=Path(__file__).resolve().parent, env=env,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        for _ in range(200):
            assert proc.poll() is None
            try:
                with urllib.request.urlopen(f"{base}/healthz", timeout=1):
                    break
            except OSError:
                time.sleep(.1)
        else:
            pytest.fail("import server did not start")
        yield base, root, archive
    finally:
        proc.terminate()
        proc.wait(timeout=10)


@pytest.fixture
def tracer_server(tmp_path):
    yield from _serve(tmp_path, write_archive(tmp_path / "input.zip"))


@pytest.fixture
def choice_server(tmp_path):
    yield from _serve(tmp_path, write_choice_archive(tmp_path / "choice.zip"))


@pytest.fixture
def lungs_server(tmp_path):
    yield from _serve(tmp_path, write_archive(tmp_path / "input.zip"), fail="lungs")


@pytest.mark.parametrize("cancel", [False, True])
def test_archive_import(tracer_server, cancel):
    base, root, archive = tracer_server
    shots = Path(os.environ.get("TMPDIR", "/tmp")) / "bronchoroute-28"
    shots.mkdir(parents=True, exist_ok=True)
    with _playwright_or_skip()() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        errors = []
        settings_writes = []
        page.on("request", lambda request: settings_writes.append(request.post_data_json)
                if request.method == "PUT" and request.url.endswith("/api/settings") else None)
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto(base)
        page.locator("#archive-choose").wait_for(state="visible")
        page.locator("#settings-open").click()
        page.locator("#settings-dialog").wait_for(state="visible")
        page.locator("#settings-cancel").click()
        with page.expect_file_chooser() as chooser:
            page.locator("#archive-choose").click()
        chooser.value.set_files(archive)
        page.locator("#series-picker").wait_for(state="visible")
        choices = page.locator('input[name="series-choice"]')
        assert choices.nth(0).is_checked()
        assert choices.nth(1).is_disabled()
        assert "Recommended" in page.locator("#study-groups").inner_text()
        assert "Synthetic thorax" in page.locator("#study-groups").inner_text()
        page.screenshot(path=str(shots / "picker.png"))
        page.locator("#clinician-initials").fill("AB")
        page.locator("#series-confirm").click()
        page.locator("#processing").wait_for(state="visible")
        assert page.locator("#processing-steps li").count() == 4
        page.screenshot(path=str(shots / "processing.png"))
        if cancel:
            # The cancel button is enabled only once the import has a job to cancel.
            dialogs = []
            def keep_processing(dialog):
                dialogs.append(dialog.message)
                dialog.dismiss()
            page.once("dialog", keep_processing)
            page.wait_for_function("() => !document.querySelector('#processing-cancel').disabled")
            page.locator("#archive-open").click()
            assert dialogs == ["Cancel current processing?"]
            assert page.locator("#processing").is_visible()
            page.locator("#processing-cancel").click()
            page.get_by_text("Processing cancelled. Open an Archive to try again.").wait_for()
            with urllib.request.urlopen(f"{base}/api/cases") as response:
                assert json.load(response) == []
            assert not list((root / "tmp").iterdir())
            page.screenshot(path=str(shots / "cancelled.png"))
        else:
            page.wait_for_url("**/?case=local-001", timeout=30000)
            page.locator("#switcher button").first.wait_for()
            page.wait_for_function("() => document.querySelector('#r-branches').textContent === '1'")
            assert not list((root / "tmp").iterdir())
            page.screenshot(path=str(shots / "case.png"))
        assert settings_writes == [{"initials": "AB"}]
        with urllib.request.urlopen(f"{base}/api/settings") as response:
            settings = json.load(response)
        assert settings == {
            "initials": "AB", "role": "", "updates_enabled": True,
            "disclaimer_ack": False, "filevault_note_shown": False,
            "ram_warning_shown": False,
        }
        assert not errors, errors
        browser.close()


def _open_archive(page, archive):
    page.locator("#archive-choose").wait_for(state="visible")
    with page.expect_file_chooser() as chooser:
        page.locator("#archive-choose").click()
    chooser.value.set_files(archive)
    page.locator("#series-picker").wait_for(state="visible")


def test_override_reason_flow(choice_server):
    base, _root, archive = choice_server
    shots = _shots()
    with _playwright_or_skip()() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        errors = []
        posted = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.on("request", lambda request: posted.append(request.post_data_json)
                if request.method == "POST" and request.url.rstrip("/").endswith("/api/imports") else None)
        page.goto(base)
        _open_archive(page, archive)
        radios = page.locator('input[name="series-choice"]')
        assert radios.nth(0).is_checked()
        assert radios.nth(2).is_disabled()
        radios.nth(1).click()
        page.locator("#override-reason").wait_for(state="visible")
        assert page.locator("#series-confirm").is_disabled()
        page.screenshot(path=str(shots / "override-reason.png"))
        page.locator("#override-reason").fill("sharper edges")
        assert page.locator("#series-confirm").is_enabled()
        page.locator("#clinician-initials").fill("AB")
        page.locator("#series-confirm").click()
        page.locator("#processing").wait_for(state="visible")
        page.wait_for_function("() => !document.querySelector('#processing-cancel').disabled")
        assert posted and posted[-1]["override_reason"] == "sharper edges"
        page.screenshot(path=str(shots / "override-processing.png"))
        page.locator("#processing-cancel").click()
        page.get_by_text("Processing cancelled. Open an Archive to try again.").wait_for()
        assert not errors, errors
        browser.close()


def test_lungs_failure_offers_the_same_archive(lungs_server):
    base, _root, archive = lungs_server
    shots = _shots()
    with _playwright_or_skip()() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto(base)
        _open_archive(page, archive)
        page.locator("#clinician-initials").fill("AB")
        page.locator("#series-confirm").click()
        page.locator("#import-message").get_by_text(
            "segmentation step failed: no lungs found",
        ).wait_for(timeout=20000)
        text = page.locator("#import-message").inner_text()
        assert "segmentation step failed: no lungs found" in text
        assert text.count("Try another Series") == 1
        page.screenshot(path=str(shots / "failure.png"))
        page.locator("#try-another").click()
        page.locator("#series-picker").wait_for(state="visible", timeout=20000)
        assert "Recommended" in page.locator("#study-groups").inner_text()
        assert "Synthetic thorax" in page.locator("#study-groups").inner_text()
        page.screenshot(path=str(shots / "try-another.png"))
        assert not errors, errors
        browser.close()
