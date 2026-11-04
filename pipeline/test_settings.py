# Changes: viewer-only GET settings/clinician never writes on a read-only app_state.
# Changes: auto-generate 3-letter initials on fresh load and empty PUT.
# Changes: filevault_note_shown is a boolean on the shared settings API.
# Changes: ram_warning_shown round-trips on the settings allowlist.
# Changes: shared settings API validation, preservation, and record identity.
import json
import re

import pytest

from test_app_server import client, harness
from app.settings import settings_path

pytestmark = pytest.mark.server


def test_settings_defaults(client):
    first = client.get("/api/settings").json()
    assert re.fullmatch(r"[A-Z]{3}", first["initials"])
    assert first["role"] == ""
    assert first["updates_enabled"] is True
    assert first["disclaimer_ack"] is False
    assert first["filevault_note_shown"] is False
    assert first["ram_warning_shown"] is False
    second = client.get("/api/settings").json()
    assert second["initials"] == first["initials"]


def test_viewer_only_settings_never_write(client, monkeypatch):
    state = settings_path().parent
    state.mkdir(parents=True, exist_ok=True)
    assert not settings_path().exists()
    monkeypatch.setenv("LUNGNAV_VIEWER_ONLY", "1")
    state.chmod(0o555)
    try:
        assert client.get("/api/settings").status_code == 200
        assert client.get("/api/clinician").status_code == 200
        assert not settings_path().exists()
        assert not (state / "settings.json.lock").exists()
    finally:
        state.chmod(0o755)


def test_settings_empty_put_regenerates(client):
    first = client.get("/api/settings").json()["initials"]
    assert re.fullmatch(r"[A-Z]{3}", first)
    client.put("/api/settings", json={"initials": "AB"})
    assert client.get("/api/settings").json()["initials"] == "AB"
    regenerated = client.put("/api/settings", json={"initials": ""}).json()["initials"]
    assert re.fullmatch(r"[A-Z]{3}", regenerated)
    assert regenerated != "AB"
    assert client.get("/api/settings").json()["initials"] == regenerated


def test_settings_explicit_initials_preserved(client):
    assert client.put("/api/settings", json={"initials": "A.B."}).json()["initials"] == "A.B."
    assert client.get("/api/settings").json()["initials"] == "A.B."


@pytest.mark.parametrize("initials", ["A", "ABCD", "A.B.", "ab", "A.B"])
def test_settings_initials(client, initials):
    res = client.put("/api/settings", json={"initials": initials})
    assert res.status_code == 200
    assert res.json()["initials"] == initials
    assert client.get("/api/clinician").json()["initials"] == initials
    saved = client.post("/api/cases/local-001/records", json={"step": 1, "kind": "note", "data": {}})
    assert saved.status_code == 200
    assert saved.json()["clinician"] == "Clinician, " + "".join(c.upper() + "." for c in initials if c.isalpha())


@pytest.mark.parametrize("patch", [
    {"initials": "ABCDE"}, {"initials": "A1"},
    {"initials": ".AB"}, {"initials": "A..B"}, {"initials": "A B"},
    {"initials": None}, {"role": "x" * 41},
    *({"role": "Role" + c} for c in "<>'\""),
    {"updates_enabled": "false"}, {"disclaimer_ack": 1},
    {"filevault_note_shown": "yes"}, {"filevault_note_shown": 1},
    {"ram_warning_shown": 1}, {"ram_warning_shown": "true"},
])
def test_settings_invalid(client, patch):
    before = client.get("/api/settings").json()
    assert client.put("/api/settings", json=patch).status_code == 422
    assert client.get("/api/settings").json() == before


def test_settings_preserved_by_all_writers(client):
    path = settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"future": {"enabled": 7}}))
    patch = {
        "initials": "AB", "role": "", "updates_enabled": False, "disclaimer_ack": True,
        "ram_warning_shown": True,
    }
    expected = {**patch, "filevault_note_shown": False}
    assert client.put("/api/settings", json=patch).json() == expected
    assert client.get("/api/settings").json() == expected
    assert client.put("/api/clinician", json={"role": "Radiologist", "initials": "C.D."}).status_code == 200
    assert client.put("/api/clinician", json={"ct_view": "head"}).status_code == 200
    saved = json.loads(path.read_text())
    assert saved["future"] == {"enabled": 7}
    assert saved["updates_enabled"] is False
    assert saved["disclaimer_ack"] is True
    assert saved["ram_warning_shown"] is True


def test_filevault_note_flag(client):
    assert client.get("/api/settings").json()["filevault_note_shown"] is False
    saved = client.put("/api/settings", json={"filevault_note_shown": True}).json()
    assert saved["filevault_note_shown"] is True
    assert saved["updates_enabled"] is True
    assert saved["disclaimer_ack"] is False
    assert client.get("/api/settings").json()["filevault_note_shown"] is True


def test_settings_free_text_role(client):
    for role in ["", "Research fellow", "Team, research"]:
        assert client.put("/api/settings", json={"role": role, "initials": "AB"}).status_code == 200
        assert client.get("/api/settings").json()["role"] == role


def test_settings_lesion_stamp(client, harness):
    assert client.put("/api/settings", json={"initials": "XY", "role": ""}).status_code == 200
    case = json.loads(harness["case"].read_text())
    case["records"] = [{
        "step": 1, "kind": "series_confirmed", "data": {},
        "clinician": "Clinician, X.Y.", "at": "2026-01-01T00:00:00Z",
    }]
    harness["case"].write_text(json.dumps(case))
    res = client.put("/api/cases/local-001/lesion", json={"ras_mm": [12, 10, 12]})
    assert res.status_code == 200
    case = json.loads(harness["case"].read_text())
    assert case["lesion"]["confirmed_by"] == "Clinician, X.Y."
    assert case["records"][-1]["clinician"] == "Clinician, X.Y."


def test_settings_ram_warning_shown(client):
    assert client.get("/api/settings").json()["ram_warning_shown"] is False
    saved = client.put("/api/settings", json={"initials": "AB", "ram_warning_shown": True}).json()
    assert saved["initials"] == "AB"
    assert saved["ram_warning_shown"] is True
    assert saved["disclaimer_ack"] is False
    assert client.put("/api/settings", json={"disclaimer_ack": True}).json()["ram_warning_shown"] is True


def test_settings_identity_keeps_ct_preferences(client):
    client.put("/api/clinician", json={"role": "Research", "initials": "A.B.", "ct_view": "head"})
    client.put("/api/settings", json={"initials": "CD"})
    assert client.get("/api/clinician").json()["ct_view"] == "feet"
    client.put("/api/settings", json={"initials": "AB"})
    assert client.get("/api/clinician").json()["ct_view"] == "head"
