# Changes: playwright gate on fork-still readiness before the fly-through export (#66).
"""Fly button waits for every fork group still while the model is installed."""
import pytest

from test_realism_viewer_playwright import (
    _RealismMock, _fetch_payload, _on_route_uv, _open, server,
)
from test_viewer_v2_playwright import VIEWPORT, _playwright_or_skip


class _GateMock(_RealismMock):
    """Fork worker status with a controllable ready count."""

    def __init__(self, *, total=4, ready=1, **kwargs):
        super().__init__(**kwargs)
        self.total = total
        self.ready = ready
        self.status = "running"

    def body(self):
        frames = [{
            "group": index,
            "forks": [index + 1],
            "hold_s": self.hold_s,
            "ready": index < self.ready,
            "url": (
                f"/api/cases/local-001/realism/{self.key}/group-{index}.png"
                if index < self.ready else None
            ),
            "orifices": [{
                "fork": index + 1,
                "edge": index + 1,
                "on_route": True,
                "name": self.name,
                "u": self.u,
                "v": self.v,
                "inside": True,
            }],
        } for index in range(self.total)]
        return {
            "status": self.status,
            "reason": None,
            "key": self.key,
            "frames": frames,
        }


def _open_with_mock(page, base, mock):
    mock.install(page)
    _open(page, base)


def test_fly_waits_for_all_fork_textures(server):
    base, _, _ = server
    payload = _fetch_payload(base)
    hold, u, v, name = _on_route_uv(payload)
    mock = _GateMock(hold_s=hold, u=u, v=v, name=name, total=4, ready=1)
    with _playwright_or_skip()() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport=VIEWPORT)
        page.set_default_timeout(120_000)
        _open_with_mock(page, base, mock)
        button = page.locator("#flythrough-btn")
        page.wait_for_function(
            """() => document.getElementById('flythrough-btn').textContent
              === 'Fork textures 1/4'""",
        )
        assert button.is_visible()
        assert button.is_disabled()
        assert button.get_attribute("title") == "Waiting for fork textures"

        mock.ready = 3
        page.wait_for_function(
            """() => document.getElementById('flythrough-btn').textContent
              === 'Fork textures 3/4'""",
        )
        assert button.is_disabled()

        mock.ready = 4
        mock.status = "done"
        page.wait_for_function(
            """() => {
              const btn = document.getElementById('flythrough-btn');
              return !btn.disabled && btn.textContent === 'Export fly-through';
            }""",
            timeout=15_000,
        )
        assert button.is_enabled()
        button.click()
        page.wait_for_function(
            """() => document.getElementById('flythrough-status').textContent
              === 'Video export needs the desktop app'""",
        )
        browser.close()


def test_fly_is_not_gated_without_the_model(server):
    base, _, _ = server
    payload = _fetch_payload(base)
    hold, u, v, name = _on_route_uv(payload)
    mock = _RealismMock(hold_s=hold, u=u, v=v, name=name, mode="unavailable")
    with _playwright_or_skip()() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport=VIEWPORT)
        page.set_default_timeout(120_000)
        _open_with_mock(page, base, mock)
        page.wait_for_function("() => window.__realism.state === 'unavailable'")
        button = page.locator("#flythrough-btn")
        assert button.is_visible()
        assert button.is_enabled()
        assert button.text_content() == "Export fly-through"
        browser.close()
