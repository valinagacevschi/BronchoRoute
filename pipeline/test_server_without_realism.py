# Changes: verify the server works without optional realism dependencies.
"""Exercise imports and realism routes in an isolated server process."""

import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.server


def test_server_without_realism():
    result = subprocess.run(
        [sys.executable, "-c", """
import importlib.abc
import sys

class BlockRealism(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] in {'cv2', 'PIL', 'onnxruntime'}:
            raise ImportError(f'blocked optional dependency: {fullname}')

sys.meta_path.insert(0, BlockRealism())
import repo_root
repo_root.ensure()
import app.models_dl
import app.server
from fastapi.testclient import TestClient
from unittest.mock import patch

client = TestClient(app.server.app, base_url='http://127.0.0.1')
expected = {
    'status': 'unavailable', 'reason': 'realism not installed',
    'key': None, 'frames': [],
}
response = client.get('/api/cases/local-001/realism')
assert response.status_code == 200, response.text
assert response.json() == expected, response.text
response = client.post('/api/cases/local-001/realism/start')
assert response.status_code == 200, response.text
assert response.json() == expected, response.text
for method, suffix in [('get', ''), ('post', '/start'), ('post', '/cancel')]:
    response = getattr(client, method)('/api/cases/local-001/realism/route' + suffix)
    assert response.status_code == 200, response.text
    assert response.json() == {**expected, 'done': 0, 'total': 0}, response.text
response = client.get('/api/cases/local-001/realism/route/key/kf-0.png')
assert response.status_code == 404, response.text
response = client.get('/api/cases/local-001/realism/key/frame.png')
assert response.status_code == 404, response.text
with patch.object(app.server, '_require_clinician', return_value={'clinician': 'test'}), \
     patch.object(app.server, 'set_route_target', return_value={'confirmed': True}) as confirm:
    response = client.put(
        '/api/cases/local-001/route-target', json={'ras_mm': [1, 2, 3]},
    )
    assert response.status_code == 200, response.text
    assert response.json() == {'confirmed': True}, response.text
    confirm.assert_called_once()
"""],
        cwd=Path(__file__).resolve().parent,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
