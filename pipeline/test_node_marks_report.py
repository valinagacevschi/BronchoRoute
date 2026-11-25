"""Lymph-node marks, routing to a node, and the live Case report."""

from __future__ import annotations

import json

from termcolor import cprint

from test_p4a_workflow import _seed_through_step
from test_p4c_route_target import (  # noqa: F401 - pytest fixtures
    _set_clinician,
    client,
    fake_home,
    harness,
)


def _lesion_ras(harness) -> list[float]:
    case = json.loads(harness["case"].read_text())
    return [float(v) for v in case["lesion"]["ras_mm"]]


def _add_node(client, ras: list[float], diameter: float = 10.0, label: str | None = None):
    body = {"ras_mm": ras, "diameter_mm": diameter}
    if label is not None:
        body["label"] = label
    return client.put("/api/cases/local-001/nodes", json=body)


def test_node_mark_crud_routing_and_payload(client, harness, fake_home):
    cprint("node marks: add, list in payload, route, delete", "cyan")
    _set_clinician(client)
    _seed_through_step(client, harness, 6)
    node_ras = _lesion_ras(harness)

    added = _add_node(client, node_ras, label="Station 11R")
    assert added.status_code == 200, added.text
    mark = added.json()["node_mark"]
    assert mark["id"] == 1
    assert mark["label"] == "Station 11R"
    assert mark["diameter_mm"] == 10.0

    payload = client.get("/api/cases/local-001/payload?allow_review=1").json()
    marks = payload["node_marks"]
    assert len(marks) == 1
    assert marks[0]["id"] == 1
    assert marks[0]["label"] == "Station 11R"
    assert len(marks[0]["p"]) == 3

    routed = client.post("/api/cases/local-001/nodes/1/route")
    assert routed.status_code == 200, routed.text
    target = routed.json()["route_target"]
    assert target["poi_kind"] == "node:1"
    assert target["poi_ras_mm"] == node_ras
    assert target["target_gap_mm"] is not None
    assert target["target_gap_mm"] >= 0

    payload = client.get("/api/cases/local-001/payload?allow_review=1").json()
    assert payload["poi_kind"] == "node:1"
    assert payload["poi"] is not None
    assert payload["route_target"]["poi_kind"] == "node:1"

    removed = client.delete("/api/cases/local-001/nodes/1")
    assert removed.status_code == 200, removed.text
    payload = client.get("/api/cases/local-001/payload?allow_review=1").json()
    assert payload["node_marks"] == []


def test_node_mark_validation_and_limit(client, harness, fake_home):
    cprint("node marks: bounds and label validation", "cyan")
    _set_clinician(client)
    _seed_through_step(client, harness, 6)
    node_ras = _lesion_ras(harness)

    outside = _add_node(client, [900.0, 900.0, 900.0])
    assert outside.status_code == 422
    assert "outside the CT crop" in outside.text

    long_label = _add_node(client, node_ras, label="x" * 41)
    assert long_label.status_code == 422

    missing = client.delete("/api/cases/local-001/nodes/99")
    assert missing.status_code == 422


def test_route_to_node_without_lesion(client, harness, fake_home):
    cprint("node routing: a node can be the selected destination", "cyan")
    _set_clinician(client)
    _seed_through_step(client, harness, 6)
    added = _add_node(client, _lesion_ras(harness))
    assert added.status_code == 200, added.text

    case = json.loads(harness["case"].read_text())
    case.pop("lesion", None)
    harness["case"].write_text(json.dumps(case, indent=2) + "\n")

    routed = client.post("/api/cases/local-001/nodes/1/route")
    assert routed.status_code == 200, routed.text
    target = routed.json()["route_target"]
    assert target["poi_kind"] == "node:1"
    payload = client.get("/api/cases/local-001/payload?allow_review=1").json()
    assert payload["lesion"] is None
    assert payload["poi_kind"] == "node:1"
    assert payload["target_gap_mm"] is not None


def test_clear_route_target_returns_to_the_lesion(client, harness, fake_home):
    cprint("target selection: node then back to the Lesion", "cyan")
    _set_clinician(client)
    _seed_through_step(client, harness, 6)
    added = _add_node(client, _lesion_ras(harness))
    assert added.status_code == 200, added.text
    routed = client.post("/api/cases/local-001/nodes/1/route")
    assert routed.status_code == 200, routed.text
    assert client.get("/api/cases/local-001/payload?allow_review=1").json()[
        "poi_kind"
    ] == "node:1"

    cleared = client.delete("/api/cases/local-001/route-target")
    assert cleared.status_code == 200, cleared.text
    payload = client.get("/api/cases/local-001/payload?allow_review=1").json()
    assert payload["poi_kind"] in (None, "lesion")
    assert payload["target_gap_mm"] is not None


def test_case_report_html(client, harness, fake_home):
    cprint("report: lesion, node marks, route, limitations", "cyan")
    _set_clinician(client)
    _seed_through_step(client, harness, 6)
    added = _add_node(client, _lesion_ras(harness), label="Station 11R")
    assert added.status_code == 200, added.text
    routed = client.post("/api/cases/local-001/nodes/1/route")
    assert routed.status_code == 200, routed.text

    res = client.get("/api/cases/local-001/report")
    assert res.status_code == 200, res.text
    assert res.headers["content-security-policy"] == (
        "default-src 'none'; style-src 'unsafe-inline'"
    )
    body = res.text
    assert "BronchoRoute — case report" in body
    assert body.count("Station 11R") >= 2  # node table row + active target
    assert "Target gap" in body
    assert "Simulated · CT-derived · research use" in body
    assert "Not a medical device" in body
    case = json.loads(harness["case"].read_text())
    assert case["source"]["series_uid_sha256"] not in body
    assert "Accession" not in body
