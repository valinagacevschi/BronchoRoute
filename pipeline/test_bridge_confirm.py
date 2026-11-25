# Changes: rejected stored RouteTargets become stale and recover after reconfirmation.
# Changes: CT bridge API without workflow gates, and routes across multiple bridges.
import json
import itertools

import numpy as np
import pytest
from fastapi.testclient import TestClient

from test_p4b_review import harness, client, _set_clinician, _seed_through_step
from test_route_target_playwright import _seed
from app.caseref import CaseRef
from app.cases import case_payload
from app.server import app
from app.review import compute_effective_graph, effective_for_case, load_bridges_annotated, map_all_bridges
from app.route_target import (
    densify_centreline, rank_route_target_candidates, snap_to_selectable_ct,
    resolve_edge_sample, plan_route_target_for_payload,
    resolve_route_end_for_payload,
)


def seed_bridges(root, count=2):
    _seed(root)
    graph_path = root / "out/local-001/local-001_graph.json"
    graph = json.loads(graph_path.read_text())
    graph["edges"][0].update(interpolated_mm=0.0, interpolated_spans_mm=[])
    bridges = []
    for i in range(1, count + 1):
        edge = graph["edges"][i]
        edge.update(provenance="interpolated", interpolated_mm=2.0,
                    interpolated_spans_mm=[[4.0, 6.0]])
        line = edge["centreline_mm"]
        bridges.append({"tree_point_mm": line[4], "piece_point_mm": line[6], "gap_mm": 2.0})
    graph_path.write_text(json.dumps(graph))
    (root / "ts_out/local-001/airway_bridges.json").write_text(json.dumps({
        "radius_mm": 0.5, "bridges": bridges,
    }))
    return graph


@pytest.mark.parametrize("target_edge", [1, 2], ids=["blocked-sample", "removed-edge"])
def test_rejected_stored_target_recovers(tmp_path, monkeypatch, target_edge):
    monkeypatch.delenv("BRONCHOROUTE_LEGACY_REVIEW", raising=False)
    graph = seed_bridges(tmp_path, count=1)
    monkeypatch.setattr("paths.PIPELINE", tmp_path)
    monkeypatch.setattr("paths.LOCAL_CASES", tmp_path / "data/extracted/local")
    monkeypatch.setattr("paths.TS_OUT", tmp_path / "ts_out")
    monkeypatch.setattr("paths.OUT", tmp_path / "out")
    client = TestClient(app, base_url="http://127.0.0.1")
    _set_clinician(client)
    base = "/api/cases/local-001"
    bridge = client.get(f"{base}/bridges").json()["bridges"][0]
    url = f"{base}/bridges/{bridge['id']}"
    assert client.put(url, json={"decision": "confirmed"}).status_code == 200
    point = graph["edges"][target_edge]["centreline_mm"][-1]
    assert client.put(f"{base}/lesion", json={"ras_mm": point}).status_code == 200
    chosen = client.put(f"{base}/route-target", json={"ras_mm": point})
    assert chosen.status_code == 200, chosen.text
    stored = chosen.json()["route_target"]
    assert stored["edge_id"] == target_edge
    assert client.put(url, json={"decision": "rejected"}).status_code == 200
    response = client.get(f"{base}/payload?allow_review=1")
    assert response.status_code == 200, response.text
    payload = response.json()
    # The lost target is replaced by the next best reachable point on load.
    assert payload["route_target"]["stale"] is False
    if target_edge == 2:
        assert payload["route_target"]["edge_id"] != target_edge
    assert payload["endpoint"] is not None
    assert all(payload["edges"][i]["oi"] in (0, 1) for i in payload["route"])
    assert payload["fly"]["s"][-1] <= 15.25 + 0.01
    proposals = client.get(f"{base}/route-candidates")
    assert proposals.status_code == 200, proposals.text
    assert all(c["edge_id"] != 2 for c in proposals.json()["candidates"])
    effective = effective_for_case("local-001")["graph"]
    # The legacy centreline-index fallback must not resurrect a blocked sample.
    assert resolve_route_end_for_payload(effective, {**stored, "centreline_index": 12}) is None
    assert client.put(url, json={"decision": "confirmed"}).status_code == 200
    recovered = client.get(f"{base}/payload?allow_review=1")
    assert recovered.status_code == 200, recovered.text
    assert recovered.json()["route_target"]["stale"] is False
    assert recovered.json()["endpoint"] is not None


@pytest.mark.parametrize("states", list(itertools.product((None, "confirmed", "rejected"), repeat=2)))
def test_routes_never_cross_unconfirmed_bridges(tmp_path, monkeypatch, states):
    monkeypatch.delenv("BRONCHOROUTE_LEGACY_REVIEW", raising=False)
    graph = seed_bridges(tmp_path)
    monkeypatch.setattr("paths.PIPELINE", tmp_path)
    monkeypatch.setattr("paths.LOCAL_CASES", tmp_path / "data/extracted/local")
    monkeypatch.setattr("paths.TS_OUT", tmp_path / "ts_out")
    monkeypatch.setattr("paths.OUT", tmp_path / "out")
    harness = {"case": tmp_path / "data/extracted/local/local-001/case.json"}
    ref = CaseRef.parse("local-001")
    bridges = load_bridges_annotated(ref)
    mapping = map_all_bridges(graph, bridges, ref)
    assert all(mapping[b["id"]] for b in bridges)
    case = json.loads(harness["case"].read_text())
    case["bridge_decisions"] = {b["id"]: {"decision": state} for b, state in zip(bridges, states) if state}
    eff = compute_effective_graph(graph, case, bridges, ref=ref, bridge_map=mapping)
    effective = eff["graph"]
    edges = effective["edges"]

    def assert_safe(payload):
        travelled = float(payload["fly"]["s"][-1])
        prefix = 0.0
        for index in payload["route"]:
            oi = payload["edges"][index]["oi"]
            for bridge, state in zip(bridges, states):
                if state == "confirmed":
                    continue
                for span in mapping[bridge["id"]]:
                    if span["edge_index"] == oi:
                        assert travelled <= prefix + span["start_mm"] + 0.01
            pts = np.asarray(graph["edges"][oi]["centreline_mm"])
            prefix += float(np.linalg.norm(np.diff(pts, axis=0), axis=1).sum())

    # The default displayed route must obey the same rule before any target exists.
    harness["case"].write_text(json.dumps(case))
    assert_safe(case_payload("local-001", allow_review=True))
    candidates = []
    snaps = []
    for edge in graph["edges"]:
        points, _ = densify_centreline(np.asarray(edge["centreline_mm"]), 0.5)
        for point in points:
            candidates.extend(rank_route_target_candidates(effective, case, point.tolist(), top_n=99, dedup_mm=0))
            snap = snap_to_selectable_ct(effective, point.tolist())
            if snap:
                snaps.append(snap)
    assert candidates and snaps
    targets = {(t["edge_id"], t["sample_index"]): t for t in candidates + snaps}
    for target in targets.values():
        resolved = resolve_edge_sample(effective, target["edge_id"], target["sample_index"])
        case["route_target"] = {**target, "ras_mm": resolved["ras_mm"]}
        plan = plan_route_target_for_payload(effective, case, case["route_target"])
        assert plan["active"]
        harness["case"].write_text(json.dumps(case))
        assert_safe(case_payload("local-001", allow_review=True))
    beyond = snap_to_selectable_ct(effective, [24.5, 20.5, 20.5])
    if states == ("confirmed", "confirmed"):
        assert beyond and beyond["edge_id"] == 2
        case["route_target"] = beyond
        assert plan_route_target_for_payload(effective, case, beyond)["route"] == [0, 1, 2]
    else:
        assert beyond is None
    if "rejected" in states:
        rejected_edge = states.index("rejected") + 1
        assert not any(e["_original_index"] > rejected_edge for e in edges if e["from"] >= rejected_edge + 1)


def test_bridge_api(client, harness, monkeypatch):
    monkeypatch.delenv("BRONCHOROUTE_LEGACY_REVIEW", raising=False)
    _set_clinician(client)
    url = "/api/cases/local-001/bridges"
    response = client.get(url)
    assert response.status_code == 200, response.text
    row = response.json()["bridges"][0]
    assert row["decision"] is None and row["matched"]
    assert row["midpoint_ras_mm"] == [11, 9, 12.75]
    assert response.json()["unconfirmed"] == 1
    for decision in ("confirmed", "rejected"):
        res = client.put(f"{url}/{row['id']}", json={"decision": decision})
        assert res.status_code == 200, res.text
        case = json.loads(harness["case"].read_text())
        entry = case["bridge_decisions"][row["id"]]
        assert entry["decision"] == decision and entry["note"] == ""
        assert entry["clinician"] == "A.B."
        record = case["records"][-1]
        assert record["kind"] == "bridge_decision" and record["step"] == 4
        assert record["clinician"] == "A.B." and record["at"]
        got = client.get(url).json()["bridges"][0]
        assert got["decision"] == decision and got["decided_by"] == "A.B."
        assert got["decided_at"] == entry["at"]
    assert client.put(f"{url}/unknown", json={"decision": "confirmed"}).status_code == 422
    assert client.put(f"{url}/{row['id']}", json={"decision": "confirmed", "note": "\n"}).status_code == 422
    monkeypatch.setenv("LUNGNAV_VIEWER_ONLY", "1")
    assert client.get(url).status_code == 200
    assert client.put(f"{url}/{row['id']}", json={"decision": "confirmed"}).status_code == 403


def test_legacy_note_still_required(client, harness):
    _set_clinician(client)
    row = client.get("/api/cases/local-001/bridges").json()["bridges"][0]
    url = f"/api/cases/local-001/bridges/{row['id']}"
    assert client.put(url, json={"decision": "confirmed", "note": "CT continuity"}).status_code == 409
    _seed_through_step(client, harness, 4)
    row = client.get("/api/cases/local-001/review").json()["bridges"][0]
    url = f"/api/cases/local-001/bridges/{row['id']}"
    assert client.put(url, json={"decision": "confirmed"}).status_code == 422
    assert client.put(url, json={"decision": "confirmed", "note": ""}).status_code == 422
    assert client.put(url, json={"decision": "confirmed", "note": "CT continuity"}).status_code == 200


def test_unmatched_bridge_is_listed_without_a_decision(client, harness, monkeypatch):
    monkeypatch.delenv("BRONCHOROUTE_LEGACY_REVIEW", raising=False)
    _set_clinician(client)
    path = harness["ts"] / "airway_bridges.json"
    document = json.loads(path.read_text())
    document["bridges"].append({
        "tree_point_mm": [100, 100, 100], "piece_point_mm": [100, 100, 102], "gap_mm": 2,
    })
    path.write_text(json.dumps(document))
    response = client.get("/api/cases/local-001/bridges").json()
    assert response["unconfirmed"] == 1
    unmatched = response["bridges"][1]
    assert unmatched["matched"] is False and unmatched["decision"] is None
    assert client.put(f"/api/cases/local-001/bridges/{unmatched['id']}", json={"decision": "confirmed"}).status_code == 422
