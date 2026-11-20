"""Offline realism pixels, roll, decode barrier, and live capture parity (#61)."""
from __future__ import annotations

import pytest

from test_realism_viewer_playwright import (
    _RealismMock, _fetch_payload, _on_route_uv, _open, _wait_shown, server,
)
from test_viewer_v2_playwright import VIEWPORT, _playwright_or_skip


pytestmark = pytest.mark.skip(reason="realistic image support is disabled for now")


# Sample the encoded image at the rolled mock dot, away from the label pills.
SAMPLE = r"""
async (blob, phi, raw = false) => {
  const bmp = await createImageBitmap(blob);
  const c = document.createElement('canvas');
  c.width = bmp.width; c.height = bmp.height;
  const ctx = c.getContext('2d'); ctx.drawImage(bmp, 0, 0);
  const R = .47 * Math.min(c.width, c.height);
  const dx = R * ((170.5 * 2 / 256) - 1);
  const dy = R * ((145.5 * 2 / 256) - 1);
  const x = c.width / 2 + dx * Math.cos(phi) + dy * Math.sin(phi);
  const y = c.height / 2 - dx * Math.sin(phi) + dy * Math.cos(phi);
  const pixel = [...ctx.getImageData(Math.round(x), Math.round(y), 1, 1).data];
  bmp.close();
  if (raw) return pixel;
  return pixel[0] > 200 && pixel[1] < 80 && pixel[2] < 80;
}
"""


@pytest.mark.parametrize("ready", [True, False])
def test_realism_exports(server, ready):
    base, _, _ = server
    hold, u, v, name = _on_route_uv(_fetch_payload(base))
    mock = _RealismMock(hold_s=hold, u=170, v=145, name=name,
                        mode="done" if ready else "not_ready",
                        orifices=[dict(fork=1, edge=1, on_route=True, name=name,
                                       u=u, v=v, inside=True)])
    with _playwright_or_skip()() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport=VIEWPORT)
        page.set_default_timeout(120_000)
        mock.install(page)
        _open(page, base)
        page.wait_for_function("() => window.__realism.frames.length > 0")
        page.evaluate("() => { window.sampleRealism = " + SAMPLE + "; }")
        page.evaluate("""(s) => {
          window.__p2_debug.setFly({s, phi: Math.PI / 2, playing: false, pausedFork: null});
          window.__p2_debug.setFlyS(s);
        }""", hold)
        # Export immediately on entering the hold; do not wait for the live fade.
        still = page.evaluate("""async () => {
          const original = Image.prototype.decode;
          let decodes = 0;
          Image.prototype.decode = async function() {
            decodes++; await original.call(this);
          };
          try {
            const blob = await window.__p2_debug.exportStill({hud: false});
            return {red: await window.sampleRealism(blob, Math.PI / 2), decodes};
          } finally { Image.prototype.decode = original; }
        }""")
        assert still["red"] is ready
        assert (still["decodes"] > 0) is ready
        if ready:
            _wait_shown(page)
            # Record captures this live canvas; verify its paused-hold pixels.
            assert page.evaluate("""async () => {
              const blob = await new Promise(r => document.getElementById('c').toBlob(r));
              return window.sampleRealism(blob, Math.PI / 2);
            }""")
            page.keyboard.press("v")
            assert not page.evaluate("""async () => window.sampleRealism(
              await window.__p2_debug.exportStill({hud: false}), Math.PI / 2)""")
            page.keyboard.press("v")
            ramps = page.evaluate("""async (hold) => {
              const rows = [];
              for (const s of [hold - 1.75, hold + 1.75]) {
                window.__p2_debug.setFlyS(s);
                const grab = async () => window.sampleRealism(
                  await window.__p2_debug.exportStill({hud:false}), Math.PI / 2, true);
                const on = await grab();
                window.dispatchEvent(new KeyboardEvent('keydown', {key:'v'}));
                const off = await grab();
                window.dispatchEvent(new KeyboardEvent('keydown', {key:'v'}));
                rows.push({on, off});
              }
              return rows;
            }""", hold)
            for row in ramps:
                expected = [(255 + row["off"][0]) / 2,
                            row["off"][1] / 2, row["off"][2] / 2]
                assert all(abs(a - b) <= 3 for a, b in zip(row["on"][:3], expected)), row
        result = page.evaluate("""async (hold) => {
          let inside = 0, outside = 0;
          const bad = [];
          let labelsChecked = 0;
          const result = await window.__p2_debug.renderFlythrough({
            size: {width: 640, height: 360}, hud: false, yieldEvery: 100,
            async sink(bytes, meta) {
              const distance = Math.abs(meta.s - hold);
              if (distance > 1.5 && distance <= 2) return; // intermediate opacity, checked in stills
              if (meta.hold !== 'fork' && distance <= 2) return;
              if (READY && meta.hold === 'fork' && distance <= 1.5) {
                const label = window.__realism.lastLabels.find(l => l.on_route);
                const opening = window.__realism.lastOrifices.find(o => o.on_route);
                if (!label || !opening || label.clock !== opening.clock
                    || label.clock !== meta.guidance.scopeClock) {
                  bad.push({label, opening, guidance:meta.guidance});
                }
                labelsChecked++;
              }
              const red = await window.sampleRealism(new Blob([bytes], {type:'image/jpeg'}), meta.phi);
              const expected = READY && distance <= 1.5;
              if (red !== expected) bad.push({s:meta.s, hold:meta.hold, red});
              if (distance <= 1.5) inside++; else outside++;
            }
          });
          return {result, inside, outside, bad, labelsChecked};
        }""".replace("READY", "true" if ready else "false"), hold)
        assert result["result"]["cancelled"] is False
        assert result["inside"] > 0 and result["outside"] > 0
        assert result["bad"] == []
        assert (result["labelsChecked"] > 0) is ready
        browser.close()
