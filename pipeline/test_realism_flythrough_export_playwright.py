# Changes: route export test serves 16-bit depth PNGs for anchor reprojection (#66).
"""Mocked route export: decoded colour blends, fork stills, fallback and visibility."""
import io
import json

import numpy as np
import pytest
from PIL import Image, ImageDraw

from test_realism_viewer_playwright import (
    _RealismMock, _fetch_payload, _on_route_uv, _open, server,
)
from test_viewer_v2_playwright import VIEWPORT, _playwright_or_skip


def png(colour, number):
    image = Image.new('RGB', (256, 256), colour)
    ImageDraw.Draw(image).text((15, 15), str(number), fill='white')
    out = io.BytesIO()
    image.save(out, format='PNG')
    return out.getvalue()


def depth_png(mm=50.0):
    # uint16 = round(depth_mm × 10); uniform so the identity dolly is exact.
    values = np.full((256, 256), int(round(mm * 10)), dtype=np.uint16)
    out = io.BytesIO()
    Image.fromarray(values).save(out, format='PNG')
    return out.getvalue()


@pytest.mark.parametrize('mode', ['done', 'cancel', 'failed'])
def test_route_export(server, mode):
    base, _, _ = server
    payload = _fetch_payload(base)
    hold, u, v, name = _on_route_uv(payload)
    mock = _RealismMock(hold_s=hold, u=170, v=145, name=name)
    end = payload['fly']['s'][-1]
    stations = list(range(0, int(end), 2)) + [end]
    frames = [dict(index=i, s=s, ready=True,
                   url=f'/api/cases/local-001/realism/route/key/kf-{i}.png',
                   depth_url=f'/api/cases/local-001/realism/route/key/kf-{i}.depth.png')
              for i, s in enumerate(stations)]
    requests = []
    with _playwright_or_skip()() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport=VIEWPORT)
        page.set_default_timeout(120000)
        mock.install(page)
        def route_api(route):
            requests.append(route.request.url)
            if route.request.url.endswith('.depth.png'):
                route.fulfill(content_type='image/png', body=depth_png())
            elif route.request.url.endswith('.png'):
                i = int(route.request.url.rsplit('kf-', 1)[1].split('.')[0])
                route.fulfill(content_type='image/png', body=png((240, 20, 20) if i % 2 == 0 else (20, 20, 240), i))
            else:
                status = 'done' if mode == 'done' else 'running' if mode == 'cancel' else 'failed'
                route.fulfill(content_type='application/json', body=json.dumps(dict(
                    status=status, reason=None, key='key', done=len(frames) if status == 'done' else 0,
                    total=len(frames), frames=frames)))
        page.route('**/realism/route**', route_api)
        _open(page, base)
        page.wait_for_function("() => !document.getElementById('flythrough-realism-option').hidden")
        page.check('#flythrough-realism')
        if mode == 'done':
            page.evaluate("window.dispatchEvent(new KeyboardEvent('keydown', {key:'v'}))")
            assert page.evaluate('window.__realism.on') is False
        result = page.evaluate("""async (mode) => {
          let travel = 0, fork = 0, decoded = 0;
          const bad = [];
          const original = Image.prototype.decode;
          Image.prototype.decode = async function() { await original.call(this); decoded++; };
          if (mode === 'cancel') {
            const timer = setInterval(() => {
              if (document.getElementById('flythrough-count').textContent.startsWith('Generating tissue')) {
                document.getElementById('flythrough-cancel').click(); clearInterval(timer);
              }
            }, 10);
          }
          try {
            const result = await window.__p2_debug.renderFlythrough({
              realistic: document.getElementById('flythrough-realism').checked,
              size: {width:640,height:360}, hud:false, lesion:false, yieldEvery:100,
              async sink(bytes, meta) {
                if (mode !== 'done') return;
                if (meta.s > 1.1 && meta.hold !== 'fork') return;
                const bmp = await createImageBitmap(new Blob([bytes], {type:'image/jpeg'}));
                const c = document.createElement('canvas'); c.width=bmp.width;c.height=bmp.height;
                const ctx=c.getContext('2d');ctx.drawImage(bmp,0,0);bmp.close();
                let x=c.width/2, y=c.height/2;
                if (meta.hold === 'fork') {
                  const R=.47*Math.min(c.width,c.height);
                  const dx=R*((170.5*2/256)-1), dy=R*((145.5*2/256)-1);
                  x+=dx*Math.cos(meta.phi)+dy*Math.sin(meta.phi);
                  y+=-dx*Math.sin(meta.phi)+dy*Math.cos(meta.phi);
                }
                const pixel=[...ctx.getImageData(Math.round(x),Math.round(y),1,1).data];
                if(meta.hold==='fork') {
                  if(Math.abs(meta.s-HOLD)>1.5)return;
                  fork++; if(pixel[0]<200 || pixel[2]>80)bad.push({pixel,meta});
                } else {
                  travel++;
                  const mix=meta.s/2;
                  const expected=[240-220*mix,20,20+220*mix];
                  if(expected.some((v,i)=>Math.abs(pixel[i]-v)>5))bad.push({pixel,expected,s:meta.s});
                }
              }
            });
            return {result,travel,fork,bad,decoded,status:document.getElementById('flythrough-status').textContent};
          } finally {Image.prototype.decode=original;}
        }""".replace('HOLD', str(hold)), mode)
        assert result['result']['cancelled'] is False
        if mode == 'done':
            assert result['travel'] >= 3
            assert result['fork'] >= 30
            assert result['bad'] == []
            assert result['decoded'] >= len(frames)
        else:
            assert 'exporting normal fly-through' in result['status']
            assert result['result']['frames'] > 0
            if mode == 'cancel':
                assert any(url.endswith('/cancel') for url in requests)
        browser.close()


def test_route_checkbox_hidden_when_unavailable(server):
    base, _, _ = server
    with _playwright_or_skip()() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport=VIEWPORT)
        _RealismMock(hold_s=1, u=128, v=128, name='branch', mode='unavailable').install(page)
        _open(page, base)
        page.wait_for_function("() => window.__realism.state === 'unavailable'")
        assert page.locator('#flythrough-realism-option').is_hidden()
        browser.close()
