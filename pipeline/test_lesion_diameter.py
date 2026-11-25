# Changes: lesion diameter default, range, replace, and payload stamp.
"""Lesion diameter on the case record and the viewer payload."""

from __future__ import annotations

import json
import re

import numpy as np
import pytest

from test_app_server import client, harness

pytestmark = pytest.mark.server

_WHEN = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z")


def _ready(client, harness):
    client.put("/api/clinician", json={"role": "Radiologist", "initials": "A.B."})
    case = json.loads(harness["case"].read_text())
    case.setdefault("source", {
        "folder": "SERIES0",
        "series_number": 1,
        "series_description": "test",
        "convolution_kernel": "STANDARD",
    })
    case.setdefault("records", []).append({
        "step": 1,
        "kind": "series_confirmed",
        "data": {
            "folder": "SERIES0",
            "series_number": 1,
            "description": "test",
            "kernel": "STANDARD",
            "overridden": False,
        },
        "clinician": "Radiologist, A.B.",
        "at": "2026-01-01T00:00:00Z",
    })
    harness["case"].write_text(json.dumps(case, indent=2) + "\n")
    meta = client.get("/api/cases/local-001/ct/meta").json()
    affine = np.asarray(meta["affine"])
    shape = meta["shape"]

    def ras_at(i, j, k):
        ijk = np.array([i, j, k, 1.0])
        return (affine @ ijk)[:3].tolist()

    return ras_at(shape[0] / 2, shape[1] / 2, shape[2] / 2), ras_at(
        shape[0] / 2 + 1, shape[1] / 2, shape[2] / 2,
    )


def test_lesion_diameter_default_bounds_replace_and_payload(client, harness):
    centre, shifted = _ready(client, harness)

    missing = client.put("/api/cases/local-001/lesion", json={"ras_mm": centre})
    assert missing.status_code == 200, missing.text
    body = missing.json()
    assert body["lesion"]["diameter_mm"] == 10
    assert "A.B." in body["lesion"]["confirmed_by"]
    assert body["record"]["clinician"] == body["lesion"]["confirmed_by"]
    assert _WHEN.fullmatch(body["lesion"]["at"])
    assert body["record"]["at"] == body["lesion"]["at"]
    assert body["record"]["data"]["diameter_mm"] == 10

    for bad in (1.9, 60.1, -1, True, "wide"):
        res = client.put(
            "/api/cases/local-001/lesion",
            json={"ras_mm": centre, "diameter_mm": bad},
        )
        assert res.status_code == 422, (bad, res.text)

    for edge in (2, 60):
        res = client.put(
            "/api/cases/local-001/lesion",
            json={"ras_mm": centre, "diameter_mm": edge},
        )
        assert res.status_code == 200, (edge, res.text)
        assert res.json()["lesion"]["diameter_mm"] == edge

    replaced = client.put(
        "/api/cases/local-001/lesion",
        json={"ras_mm": shifted, "diameter_mm": 20},
    )
    assert replaced.status_code == 200, replaced.text
    on_disk = json.loads(harness["case"].read_text())
    assert isinstance(on_disk["lesion"], dict)
    assert on_disk["lesion"]["diameter_mm"] == 20
    assert on_disk["lesion"]["ras_mm"] == pytest.approx(shifted)
    assert on_disk["lesion"]["confirmed_by"] == "Radiologist, A.B."
    confirmed = [rec for rec in on_disk["records"] if rec.get("kind") == "lesion_confirmed"]
    assert len(confirmed) >= 2
    assert confirmed[-1]["data"]["diameter_mm"] == 20
    assert "A.B." in confirmed[-1]["clinician"]
    assert _WHEN.fullmatch(confirmed[-1]["at"])

    payload = client.get("/api/cases/local-001/payload?allow_review=true").json()
    assert payload["lesion"]["diameter_mm"] == 20
    assert payload["lesion"]["ras_mm"] == pytest.approx(shifted)

    # A later PUT that omits diameter stores the default, not the previous value.
    reset = client.put("/api/cases/local-001/lesion", json={"ras_mm": centre})
    assert reset.status_code == 200
    assert reset.json()["lesion"]["diameter_mm"] == 10
    again = json.loads(harness["case"].read_text())
    assert "lesion2" not in again
    assert isinstance(again["lesion"], dict)


def test_two_manual_lesions_are_kept_and_switch_active(client, harness):
    """A second manual point adds a Lesion; the same point re-activates it."""
    centre, shifted = _ready(client, harness)

    first = client.put("/api/cases/local-001/lesion", json={"ras_mm": centre})
    assert first.status_code == 200, first.text
    assert len(first.json()["lesions"]) == 1

    second = client.put("/api/cases/local-001/lesion", json={"ras_mm": shifted})
    assert second.status_code == 200, second.text
    assert len(second.json()["lesions"]) == 2
    assert second.json()["lesion"]["ras_mm"] == pytest.approx(shifted)

    again = client.put("/api/cases/local-001/lesion", json={"ras_mm": centre})
    assert again.status_code == 200, again.text
    assert len(again.json()["lesions"]) == 2
    assert again.json()["lesion"]["ras_mm"] == pytest.approx(centre)

    payload = client.get("/api/cases/local-001/payload?allow_review=true").json()
    assert len(payload["lesions"]) == 2
    assert payload["lesion"]["ras_mm"] == pytest.approx(centre)

    cleared = client.delete("/api/cases/local-001/lesion")
    assert cleared.status_code == 200, cleared.text
    assert len(cleared.json()["lesions"]) == 1
    assert cleared.json()["lesion"]["ras_mm"] == pytest.approx(shifted)
