# Changes: Series summary must not execute Archive description HTML.
# Changes: follow-up tests for hidden, topbar archive-open, library meta, failure layout.
# Changes: intake redesign theme, row-select, drag-over, failed track, and overflow checks.
from __future__ import annotations

import os
import socket
import subprocess
import time
import urllib.request
from pathlib import Path

import pytest

from test_app_server import _write_case_tree
from test_viewer_v2_playwright import _playwright_or_skip
from tracer_fixture import write_archive, write_choice_archive


def _serve(tmp_path, archive: Path, *, fail: str | None = None, cases: tuple[str, ...] = ()):
    root = tmp_path / "state"
    root.mkdir()
    for case_id in cases:
        _write_case_tree(root, case_id)
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    base = f"http://127.0.0.1:{port}"
    env = {
        **os.environ,
        "LUNGNAV_PIPELINE_ROOT": str(root),
        "BRONCHOROUTE_FAKE_SEGMENT": "1",
        "BRONCHOROUTE_TESTING": "1",
        "BRONCHOROUTE_FAKE_STEP_SECONDS": "1",
        "HOME": str(tmp_path),
    }
    if fail:
        env["BRONCHOROUTE_FAKE_FAIL"] = fail
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
            assert proc.poll() is None
            try:
                with urllib.request.urlopen(f"{base}/healthz", timeout=1):
                    break
            except OSError:
                time.sleep(0.1)
        else:
            pytest.fail("intake theme server did not start")
        yield base, root, archive
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=10)


@pytest.fixture
def intake_server(tmp_path):
    yield from _serve(tmp_path, write_archive(tmp_path / "input.zip"))


@pytest.fixture
def choice_server(tmp_path):
    yield from _serve(tmp_path, write_choice_archive(tmp_path / "choice.zip"))


@pytest.fixture
def lungs_server(tmp_path):
    yield from _serve(tmp_path, write_archive(tmp_path / "input.zip"), fail="lungs")


@pytest.fixture
def library_server(tmp_path):
    yield from _serve(
        tmp_path,
        write_archive(tmp_path / "input.zip"),
        cases=("local-001", "local-002"),
    )


def _parse_rgb(value: str) -> tuple[float, float, float]:
    text = value.strip().lower()
    if text.startswith("rgba"):
        parts = text[text.index("(") + 1 : text.index(")")].split(",")
        return tuple(float(parts[i]) / 255.0 for i in range(3))
    if text.startswith("rgb"):
        parts = text[text.index("(") + 1 : text.index(")")].split(",")
        return tuple(float(parts[i]) / 255.0 for i in range(3))
    raise AssertionError(f"unsupported color: {value}")


def _rel_luminance(rgb: tuple[float, float, float]) -> float:
    def channel(c: float) -> float:
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4

    r, g, b = (channel(c) for c in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _contrast(fg: str, bg: str) -> float:
    l1 = _rel_luminance(_parse_rgb(fg))
    l2 = _rel_luminance(_parse_rgb(bg))
    lighter, darker = max(l1, l2), min(l1, l2)
    return (lighter + 0.05) / (darker + 0.05)


def _theme_colors(page):
    return page.evaluate(
        """() => {
          const h1 = document.querySelector('#archive-view h1');
          const footer = document.querySelector('.research-footer');
          const h1Style = getComputedStyle(h1);
          const footStyle = getComputedStyle(footer);
          let node = h1;
          let ground = h1Style.backgroundColor;
          while (node && (ground === 'rgba(0, 0, 0, 0)' || ground === 'transparent')) {
            node = node.parentElement;
            ground = getComputedStyle(node).backgroundColor;
          }
          return {
            h1: h1Style.color,
            ground,
            footerBg: footStyle.backgroundColor,
          };
        }"""
    )


def _open_archive(page, archive):
    page.locator("#archive-choose").wait_for(state="visible")
    with page.expect_file_chooser() as chooser:
        page.locator("#archive-choose").click()
    chooser.value.set_files(str(archive))
    page.locator("#series-picker").wait_for(state="visible")


def _no_horizontal_overflow(page) -> bool:
    return page.evaluate("() => document.documentElement.scrollWidth <= window.innerWidth")


def _confirm_hit(page) -> bool:
    return page.evaluate(
        """() => {
          const btn = document.querySelector('#series-confirm');
          btn.scrollIntoView({ block: 'center', inline: 'nearest' });
          const rect = btn.getBoundingClientRect();
          const x = rect.left + rect.width / 2;
          const y = rect.top + rect.height / 2;
          const hit = document.elementFromPoint(x, y);
          return hit === btn || (hit && btn.contains(hit));
        }"""
    )


@pytest.mark.parametrize("scheme", ["light", "dark"])
def test_import_theme_contrast(intake_server, scheme):
    base, _root, _archive = intake_server
    with _playwright_or_skip()() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page(
            viewport={"width": 1440, "height": 900},
            color_scheme=scheme,
        )
        page.goto(base)
        page.locator("#archive-view h1").wait_for(state="visible")
        colors = _theme_colors(page)
        assert _contrast(colors["h1"], colors["ground"]) >= 7
        browser.close()


def test_dark_theme_differs_from_light(intake_server):
    base, _root, _archive = intake_server
    samples = {}
    with _playwright_or_skip()() as playwright:
        browser = playwright.chromium.launch()
        for scheme in ("light", "dark"):
            page = browser.new_page(
                viewport={"width": 1440, "height": 900},
                color_scheme=scheme,
            )
            page.goto(base)
            page.locator("#archive-view h1").wait_for(state="visible")
            samples[scheme] = _theme_colors(page)
            page.close()
        browser.close()
    assert samples["dark"]["h1"] != samples["light"]["h1"]
    assert samples["dark"]["footerBg"] != samples["light"]["footerBg"]


def test_series_row_click_selects_radio(choice_server):
    base, _root, archive = choice_server
    with _playwright_or_skip()() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        page.goto(base)
        _open_archive(page, archive)
        radios = page.locator('input[name="series-choice"]')
        assert radios.nth(0).is_checked()
        row = page.locator(".series-row").nth(1)
        row.locator("td").nth(2).click()
        assert radios.nth(1).is_checked()
        assert radios.nth(1).is_checked()
        disabled = page.locator(".series-row.disabled").first
        disabled.locator("td").nth(2).click()
        assert radios.nth(2).is_disabled()
        assert not radios.nth(2).is_checked()
        assert radios.nth(1).is_checked()
        browser.close()


def test_drop_area_drag_over_class(intake_server):
    base, _root, _archive = intake_server
    with _playwright_or_skip()() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        page.goto(base)
        page.locator("#archive-drop").wait_for(state="visible")
        page.dispatch_event("#archive-drop", "dragenter")
        assert page.locator("#archive-drop").evaluate("el => el.classList.contains('drag-over')")
        page.dispatch_event("#archive-drop", "dragleave")
        assert page.locator("#archive-drop").evaluate("el => !el.classList.contains('drag-over')")
        browser.close()


def test_processing_failed_track(lungs_server):
    base, _root, archive = lungs_server
    with _playwright_or_skip()() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        page.goto(base)
        _open_archive(page, archive)
        page.locator("#clinician-initials").fill("AB")
        page.locator("#series-confirm").click()
        page.locator("#processing").wait_for(state="visible")
        page.locator('#processing-steps li[data-state="failed"]').wait_for(timeout=20000)
        color = page.locator('#processing-steps li[data-state="failed"] .track > i').evaluate(
            "el => getComputedStyle(el).backgroundColor"
        )
        assert _parse_rgb(color)[0] > _parse_rgb(color)[1]
        page.locator("#import-message").get_by_text(
            "segmentation step failed: no lungs found",
        ).wait_for()
        browser.close()


@pytest.mark.parametrize("size", [(1440, 900), (390, 844)])
def test_intake_screens_no_overflow(library_server, size):
    width, height = size
    base, _root, archive = library_server
    with _playwright_or_skip()() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page(viewport={"width": width, "height": height})
        page.goto(base)
        page.locator("#archive-open").click()
        page.locator("#archive-choose").wait_for(state="visible")
        assert _no_horizontal_overflow(page)
        _open_archive(page, archive)
        assert _no_horizontal_overflow(page)
        assert _confirm_hit(page)
        page.locator("#clinician-initials").fill("AB")
        page.locator("#series-confirm").click()
        page.locator("#processing").wait_for(state="visible")
        assert _no_horizontal_overflow(page)
        page.goto(f"{base}/?case=local-001")
        page.locator("#outdated-open").click()
        page.locator("#library-open").click()
        page.locator("#library-screen").wait_for(state="visible")
        assert _no_horizontal_overflow(page)
        browser.close()


def test_archive_back_hidden_with_no_case(intake_server):
    base, _root, _archive = intake_server
    with _playwright_or_skip()() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        page.goto(base)
        page.locator("#archive-drop").wait_for(state="visible")
        assert not page.locator("#archive-back").is_visible()
        browser.close()


def _archive_open_in_topbar(page) -> bool:
    return page.evaluate(
        """() => {
          const open = document.querySelector('#archive-open');
          const bar = open && open.closest('.intake-topbar');
          if (!open || !bar || open.hidden) return false;
          const o = open.getBoundingClientRect();
          const b = bar.getBoundingClientRect();
          const inside = o.top >= b.top - 1 && o.bottom <= b.bottom + 1
            && o.left >= b.left - 1 && o.right <= b.right + 1;
          const x = o.left + o.width / 2;
          const y = o.top + o.height / 2;
          const hit = document.elementFromPoint(x, y);
          return inside && (hit === open || open.contains(hit));
        }"""
    )


@pytest.mark.parametrize("size", [(1440, 900), (390, 844)])
def test_archive_open_in_topbar_on_build_and_library(library_server, size):
    width, height = size
    base, _root, archive = library_server
    with _playwright_or_skip()() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page(viewport={"width": width, "height": height})
        page.goto(base)
        page.locator("#archive-open").click()
        _open_archive(page, archive)
        if page.locator("#clinician-initials").is_visible():
            page.locator("#clinician-initials").fill("AB")
        page.locator("#series-confirm").click()
        page.locator("#processing").wait_for(state="visible")
        page.locator("#archive-open").wait_for(state="visible")
        assert _archive_open_in_topbar(page)
        page.locator("#import-library").click()
        page.locator("#library-screen").wait_for(state="visible")
        page.wait_for_function(
            "() => document.querySelector('#archive-open')?.closest('section')?.id === 'library-screen'"
        )
        assert _archive_open_in_topbar(page)
        browser.close()


def test_library_nav_meta_empty_omitted(intake_server):
    base, _root, _archive = intake_server
    with _playwright_or_skip()() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        page.goto(base)
        page.locator("#archive-drop").wait_for(state="visible")
        meta = page.locator("#import-library .mono")
        assert meta.count() == 0 or meta.inner_text().strip() == ""
        browser.close()


def test_library_nav_meta_with_cases(library_server):
    base, _root, _archive = library_server
    with _playwright_or_skip()() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        page.goto(base)
        page.locator("#library-open").click()
        page.locator("#library-screen").wait_for(state="visible")
        text = page.locator("#import-library .mono").inner_text()
        assert text.startswith("2 ·")
        browser.close()


def test_processing_failure_layout(lungs_server):
    base, _root, archive = lungs_server
    with _playwright_or_skip()() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        page.goto(base)
        _open_archive(page, archive)
        page.locator("#clinician-initials").fill("AB")
        page.locator("#series-confirm").click()
        page.locator("#try-another").wait_for(state="visible", timeout=20000)
        assert not page.locator("#processing-cancel").is_visible()
        assert page.locator("#try-another").is_visible()
        assert page.evaluate(
            """() => {
              const note = document.querySelector('#import-message');
              const btn = document.querySelector('#try-another');
              return note.getBoundingClientRect().top < btn.getBoundingClientRect().top;
            }"""
        )
        browser.close()


def test_series_summary_escapes_description_html(intake_server):
    base, _root, archive = intake_server
    payload = {
        "import_id": "xss-import",
        "unreadable_files": 0,
        "proposed": None,
        "series": [],
        "studies": [{
            "study_key": "study-xss",
            "date": "20240101",
            "description": "Study",
            "series": [{
                "series_key": "series-xss",
                "series_number": 1,
                "description": '<img src=x onerror="window.__xss=1">',
                "kernel": "STANDARD",
                "modality": "CT",
                "slice_thickness_mm": 1.0,
                "spacing_mm": 0.7,
                "axial": True,
                "size": [64, 64, 20],
                "count": 20,
                "status": "recommended",
                "reason": "",
                "kernel_class": "soft",
                "contrast": False,
                "z_span_mm": 140.0,
                "skip_reason": None,
                "proposed": True,
                "in_library": None,
            }],
        }],
    }
    payload["proposed"] = payload["studies"][0]["series"][0]
    payload["series"] = payload["studies"][0]["series"]
    with _playwright_or_skip()() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page(viewport={"width": 1440, "height": 900})

        def fulfill_upload(route):
            if route.request.method == "POST" and route.request.url.rstrip("/").endswith(
                "/api/imports/upload"
            ):
                route.fulfill(status=200, content_type="application/json", json=payload)
                return
            route.continue_()

        page.route("**/api/imports/upload", fulfill_upload)
        page.goto(base)
        _open_archive(page, archive)
        assert page.locator('input[name="series-choice"]').first.is_checked()
        summary = page.locator("#selection-summary")
        assert '<img src=x onerror="window.__xss=1">' in summary.inner_text()
        assert page.evaluate("() => window.__xss") is None
        assert summary.locator("img").count() == 0
        browser.close()
