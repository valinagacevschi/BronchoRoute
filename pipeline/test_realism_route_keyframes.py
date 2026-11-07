"""Route stations, shared seed, fork priority, caching and cancellation."""
import json
import threading
import time

import numpy as np
import pytest

from test_realism_worker import local001_client, _reset_worker
from app.realism import worker as w
from app.realism import route_keyframes as r

pytestmark = pytest.mark.server


@pytest.fixture(autouse=True)
def reset_routes():
    r._states.clear()
    yield
    w.reset_realism_state_for_tests()
    r._states.clear()


def test_station_spacing_and_interpolation():
    payload = dict(fly=dict(s=[0, 5], p=[[0, 0, 0], [5, 0, 0]],
                            t=[[1, 0, 0]] * 2, up=[[0, 1, 0]] * 2),
                   origin_mm=[10, 20, 30], route=[1])
    key, frames, poses = r.plan_route('local-001', payload, 'stub')
    assert [f['s'] for f in frames] == [0, 2, 4, 5]
    assert poses[1]['position_ras_mm'] == [12, 20, 30]
    assert key == r.plan_route('local-001', payload, 'stub')[0]
    payload['fly']['s'][-1] = 4
    assert [f['s'] for f in r.plan_route('local-001', payload, 'stub')[1]] == [0, 2, 4]


def wait(client, status):
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline:
        body = client.get('/api/cases/local-001/realism/route').json()
        if body['status'] == status:
            return body
        assert body['status'] != 'failed', body
        time.sleep(.02)
    raise AssertionError(body)


def test_route_cache_seed_and_fork_priority(local001_client, monkeypatch):
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
    assert stations[0] == 0
    assert np.allclose(np.diff(stations)[:-1], 2)
    assert 0 < stations[-1] - stations[-2] <= 2
    route_seed = w.group_seed('local-001', 'route')
    assert seeds[-done['total']:] == [route_seed] * done['total']
    assert all(seed != route_seed for seed in seeds[:-done['total']])
    assert client.get(done['frames'][0]['url']).status_code == 200
    assert client.get(url + '/' + done['key'] + '/kf-99999.png').status_code == 404
    directory = root / 'ts_out/local-001/realism' / done['key'] / 'route'
    manifest = json.loads((directory / 'manifest.json').read_text())
    assert manifest == [{k: f[k] for k in ('index', 's', 'url')} for f in done['frames']]
    before = {p.name: p.stat().st_mtime_ns for p in directory.iterdir()}
    count = len(seeds)
    w._delete_other_keys('local-001', w.realism_status('local-001')['key'])
    assert (directory / 'manifest.json').is_file()
    w.reset_realism_state_for_tests()
    assert client.post(url + '/start').json()['status'] == 'done'
    assert w.realism_status('local-001')['status'] == 'done'
    assert len(seeds) == count
    assert before == {p.name: p.stat().st_mtime_ns for p in directory.iterdir()}


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
    started = client.post(url + '/start').json()
    assert entered.wait(60)
    cancelled = client.post(url + '/cancel').json()
    assert cancelled['status'] == 'idle'
    assert cancelled['reason'] == 'cancelled'
    w._active_thread.join(10)
    assert not w._active_thread.is_alive()
    assert client.get(url).json()['done'] == 0
