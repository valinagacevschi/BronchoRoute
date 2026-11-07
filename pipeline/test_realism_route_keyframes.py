# Changes: anchors at fork holds with ≤80 mm gaps; depth PNG contract, fork PNG
# reuse, old-cache regeneration (#66).
"""Route anchors, shared seed, fork priority, caching and cancellation."""
import json
import threading
import time

import numpy as np
import pytest
from PIL import Image

from test_realism_worker import local001_client
from app.realism import worker as w
from app.realism import route_keyframes as r

pytestmark = pytest.mark.server


@pytest.fixture(autouse=True)
def reset_routes():
    r._states.clear()
    yield
    w.reset_realism_state_for_tests()
    r._states.clear()


def _payload(forks):
    return dict(
        fly=dict(
            s=[0, 100, 200],
            p=[[0, 0, 0], [100, 0, 0], [200, 0, 0]],
            t=[[1, 0, 0]] * 3,
            up=[[0, 1, 0]] * 3,
        ),
        origin_mm=[10, 20, 30],
        route=[1],
        forks=forks,
    )


def test_anchor_plan_fork_holds_and_max_gap():
    forks = [
        {"fork": 1, "s": 40.0, "node": 11, "children": []},
        {"fork": 2, "s": 150.0, "node": 12, "children": []},
    ]
    key, frames, poses, anchors = r.plan_route(
        "local-001", _payload(forks), "stub",
    )
    stations = [frame["s"] for frame in frames]
    assert stations == [0, 28, 83, 138, 200]
    assert [frame["group"] for frame in frames] == [None, 0, None, 1, None]
    assert len(poses) == len(frames) == len(anchors)
    assert all(b - a <= r.MAX_GAP_MM for a, b in zip(stations, stations[1:]))
    assert key == r.plan_route("local-001", _payload(forks), "stub")[0]
    assert frames[1]["depth_url"].endswith("kf-1.depth.png")


def wait(client, status):
    deadline = time.monotonic() + 180
    while time.monotonic() < deadline:
        body = client.get('/api/cases/local-001/realism/route').json()
        if body['status'] == status:
            return body
        assert body['status'] != 'failed', body
        time.sleep(.02)
    raise AssertionError(body)


def _depth_values(path):
    image = Image.open(path)
    assert image.mode == "I;16", image.mode
    values = np.asarray(image)
    assert values.dtype == np.uint16
    assert values.shape == (256, 256)
    return values


def test_route_reuses_fork_pngs_depths_and_cache(local001_client, monkeypatch):
    client, root = local001_client
    seeds = []
    original = w._run_euler

    def record(encoded, seed, cancel):
        if seed == w.group_seed('local-001', 'route'):
            assert w.realism_status('local-001')['status'] == 'done'
        seeds.append(seed)
        return original(encoded, seed, cancel)

    monkeypatch.setattr(w, '_run_euler', record)
    url = '/api/cases/local-001/realism/route'
    assert client.get(url).json()['status'] == 'idle'
    started = client.post(url + '/start').json()
    assert started['status'] in {'running', 'done'}
    assert client.post(url + '/start').json()['key'] == started['key']
    done = wait(client, 'done')
    assert done['done'] == done['total'] > 1
    stations = [f['s'] for f in done['frames']]
    assert stations == sorted(stations)
    assert all(b - a <= r.MAX_GAP_MM for a, b in zip(stations, stations[1:]))

    fork_state = w.realism_status('local-001')
    fork_groups = {f['group'] for f in fork_state['frames']}
    anchor_groups = {f['group'] for f in done['frames'] if f['group'] is not None}
    assert anchor_groups == fork_groups
    straight = len([f for f in done['frames'] if f['group'] is None])
    route_seed = w.group_seed('local-001', 'route')
    assert seeds.count(route_seed) == straight
    assert all(seed != route_seed for seed in seeds if seed != route_seed)

    directory = root / 'ts_out/local-001/realism' / done['key'] / 'route'
    fork_dir = w._cache_dir('local-001', fork_state['key'])
    for frame in done['frames']:
        if frame['group'] is None:
            continue
        assert (directory / f"kf-{frame['index']}.png").read_bytes() == (
            fork_dir / f"group-{frame['group']}.png"
        ).read_bytes()
    first = done['frames'][0]
    values = _depth_values(directory / f"kf-{first['index']}.depth.png")
    assert values.max() <= 65535
    assert (values == 65535).any()
    assert (values != 65535).any()
    assert client.get(first['depth_url']).status_code == 200
    assert client.get(f"{url}/{done['key']}/kf-99999.depth.png").status_code == 404

    manifest = json.loads((directory / 'manifest.json').read_text())
    assert manifest['stations'] == stations
    assert [entry['index'] for entry in manifest['frames']] == list(range(len(done['frames'])))
    assert all(entry['depth_url'].endswith('.depth.png') for entry in manifest['frames'])

    before = {p.name: p.stat().st_mtime_ns for p in directory.iterdir()}
    count = len(seeds)
    w._delete_other_keys('local-001', w.realism_status('local-001')['key'])
    w.reset_realism_state_for_tests()
    assert client.post(url + '/start').json()['status'] == 'done'
    assert w.realism_status('local-001')['status'] == 'done'
    assert len(seeds) == count
    assert before == {p.name: p.stat().st_mtime_ns for p in directory.iterdir()}


def test_old_style_manifest_regenerates(local001_client, monkeypatch):
    client, root = local001_client
    url = '/api/cases/local-001/realism/route'
    started = client.post(url + '/start').json()
    done = wait(client, 'done')
    directory = root / 'ts_out/local-001/realism' / done['key'] / 'route'
    (directory / 'manifest.json').write_text(json.dumps([
        {"index": f['index'], "s": f['s'], "url": f['url']} for f in done['frames']
    ]))
    w.reset_realism_state_for_tests()
    again = client.post(url + '/start').json()
    assert again['status'] in {'running', 'done'}
    done_again = wait(client, 'done')
    assert done_again['done'] == done_again['total']
    manifest = json.loads((directory / 'manifest.json').read_text())
    assert isinstance(manifest, dict)
    assert manifest['stations'] == [f['s'] for f in done_again['frames']]


def test_cancel_route(local001_client, monkeypatch):
    client, root = local001_client
    entered = threading.Event()
    original = w._run_euler

    def pause(encoded, seed, cancel):
        if seed == w.group_seed('local-001', 'route'):
            entered.set()
            cancel.wait(10)
        return original(encoded, seed, cancel)

    monkeypatch.setattr(w, '_run_euler', pause)
    url = '/api/cases/local-001/realism/route'
    client.post(url + '/start')
    assert entered.wait(60)
    cancelled = client.post(url + '/cancel').json()
    assert cancelled['status'] == 'idle'
    assert cancelled['reason'] == 'cancelled'
    w._active_thread.join(10)
    assert not w._active_thread.is_alive()
    assert client.get(url).json()['done'] == 0
