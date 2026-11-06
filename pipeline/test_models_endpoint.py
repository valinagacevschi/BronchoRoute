# Changes: bearer model downloads, native ranges, audit logs, and ingress isolation (#58).
import hashlib
import json
import logging
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import repo_root

repo_root.ensure()
from app.server import app

pytestmark = pytest.mark.server
DATA = bytes(range(256))
KEY = "synthetic-download-secret"
AUTH = {"Authorization": f"Bearer {KEY}"}


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr("paths.PIPELINE", tmp_path)
    monkeypatch.delenv("BRONCHOROUTE_TOKEN", raising=False)
    monkeypatch.setenv("LUNGNAV_ALLOWED_HOSTS", "127.0.0.1")
    monkeypatch.setenv("LUNGNAV_MODEL_KEYS", json.dumps({"alice": KEY, "bob": "other-secret"}))
    models = tmp_path / "models"
    models.mkdir()
    (models / "fake.onnx").write_bytes(DATA)
    (models / "unlisted.onnx").write_bytes(DATA)
    (models / "manifest.json").write_text(json.dumps({"files": [{
        "name": "fake.onnx", "sha256": hashlib.sha256(DATA).hexdigest(), "size": len(DATA),
    }]}))
    return TestClient(app, base_url="http://127.0.0.1")


@pytest.mark.parametrize("value", [None, "", "{}"])
def test_disabled(client, monkeypatch, value):
    if value is None:
        monkeypatch.delenv("LUNGNAV_MODEL_KEYS")
    else:
        monkeypatch.setenv("LUNGNAV_MODEL_KEYS", value)
    assert client.get("/models/fake.onnx", headers=AUTH).status_code == 404


@pytest.mark.parametrize("headers", [{}, {"Authorization": "Bearer wrong"}])
def test_unauthorized(client, headers):
    response = client.get("/models/fake.onnx", headers=headers)
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"


def test_download_and_head(client, monkeypatch):
    monkeypatch.setenv("LUNGNAV_VIEWER_ONLY", "1")
    response = client.get("/models/fake.onnx", headers=AUTH)
    assert response.status_code == 200
    assert response.content == DATA
    assert response.headers["x-model-sha256"] == hashlib.sha256(DATA).hexdigest()
    assert response.headers["cache-control"] == "no-store"
    head = client.head("/models/fake.onnx", headers=AUTH)
    assert head.status_code == 200
    assert head.content == b""
    assert head.headers["content-length"] == str(len(DATA))


@pytest.mark.parametrize("value,start,end", [("bytes=10-19", 10, 19), ("bytes=100-", 100, 255)])
def test_range(client, value, start, end):
    response = client.get("/models/fake.onnx", headers={**AUTH, "Range": value})
    assert response.status_code == 206
    assert response.content == DATA[start:end + 1]
    assert response.headers["content-range"] == f"bytes {start}-{end}/256"


@pytest.mark.parametrize("name", ["unknown", "../x", "%2E%2E%2Fx", "unlisted.onnx", "bad!name"])
def test_unknown(client, name):
    assert client.get(f"/models/{name}", headers=AUTH).status_code == 404


def test_audit(client, caplog):
    with caplog.at_level(logging.INFO, logger="uvicorn.error"):
        client.get("/models/fake.onnx", headers={**AUTH, "X-Forwarded-For": "192.0.2.1, 192.0.2.2", "Range": "bytes=10-19"})
    assert "model download key=alice ip=192.0.2.1 file=fake.onnx range=bytes=10-19" in caplog.text
    assert KEY not in caplog.text


def test_checks_every_key(client, monkeypatch):
    from app import models_dl

    compare = models_dl.hmac.compare_digest
    compared = []

    def record(left, right):
        compared.append(right)
        return compare(left, right)

    monkeypatch.setattr(models_dl.hmac, "compare_digest", record)
    assert client.get("/models/fake.onnx", headers=AUTH).status_code == 200
    assert compared == [KEY.encode(), b"other-secret"]


def test_second_key_and_peer_log(client, caplog):
    with caplog.at_level(logging.INFO, logger="uvicorn.error"):
        response = client.get("/models/fake.onnx", headers={"Authorization": "Bearer other-secret"})
    assert response.status_code == 200
    assert "key=bob ip=testclient file=fake.onnx range=-" in caplog.text
    assert "other-secret" not in caplog.text


def test_symlink_escape(client, tmp_path):
    outside = tmp_path / "outside.onnx"
    outside.write_bytes(b"outside")
    target = tmp_path / "models/fake.onnx"
    target.unlink()
    target.symlink_to(outside)
    assert client.get("/models/fake.onnx", headers=AUTH).status_code == 404


def test_ingress_isolation():
    deploy = Path(__file__).resolve().parents[1] / "deploy"
    main = (deploy / "ingress.yaml").read_text()
    models = (deploy / "ingress-models.yaml").read_text()
    assert "nginx.ingress.kubernetes.io/auth-type: basic" in main
    assert "nginx.ingress.kubernetes.io/auth-secret: lung-nav-basic-auth" in main
    assert "nginx.ingress.kubernetes.io/auth-" not in models
    assert 'nginx.ingress.kubernetes.io/limit-rpm: "5"' in models
