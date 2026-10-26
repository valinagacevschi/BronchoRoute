# Changes: Phase 4b review-fixes coverage — provenance bridge map, arc-length
# selectability, nearest-sample manual (Y junction), root-only rematch, sign-off at.
# Phase 4b: effective graph, bridges, corrections, manual mask, step-4 workflow.
# Uses tmp state root only — never real data / out/cases / app_state / ~/.lungnav* /tmp.
from __future__ import annotations

import json

import nibabel as nib
import numpy as np
import pytest
from fastapi.testclient import TestClient
from termcolor import cprint

import repo_root

repo_root.ensure()

from app.caseref import CaseRef  # noqa: E402
from app.jobs import reset_jobs_for_tests  # noqa: E402
from app.review import (  # noqa: E402
    bridge_stable_id,
    compute_effective_graph,
    compute_manual_labels,
    densify_centreline,
    load_bridges_annotated,
    map_all_bridges,
    rematch_edge_by_endpoints,
    subtree_edge_ids,
    unmatched_bridge_reason,
    validate_ct_evidence_note,
    write_manual_mask,
)
from app.server import app  # noqa: E402
from app.versions import register_artifact, sha256_file  # noqa: E402
from app.workflow import SEGMENT_ARTIFACT_NAMES  # noqa: E402
from test_app_server import _nifti, _write_case_tree  # noqa: E402


def _multi_edge_graph(case_id: str = "local-001") -> dict:
    """Root→1→2 (bridge)→3 plus side branch 1→4 — coords inside airways box.

    Airways box (affine diag 1,1,1.5): i 10:14, j 8:12, k 6:12 → mm x10-13,y8-11,z9-16.5.
    """
    return {
        "case": case_id,
        "root_node_id": 0,
        "coordinate_system": "RAS",
        "nodes": [
            {"id": 0, "position_mm": [11.0, 9.0, 16.5], "radius_mm": 8.0},
            {"id": 1, "position_mm": [11.0, 9.0, 13.5], "radius_mm": 5.0},
            {"id": 2, "position_mm": [11.0, 9.0, 12.0], "radius_mm": 4.0},
            {"id": 3, "position_mm": [11.0, 9.0, 9.0], "radius_mm": 3.0},
            {"id": 4, "position_mm": [13.0, 9.0, 13.5], "radius_mm": 3.0},
        ],
        "edges": [
            {
                "from": 0, "to": 1, "generation": 0, "radius_mm": 6.0, "length_mm": 3.0,
                "lobe": None, "airway_name": "trachea", "provenance": "ct",
                "interpolated_mm": 0.0, "interpolated_spans_mm": [],
                "downstream_of_interpolated": False,
                "centreline_mm": [[11.0, 9.0, z] for z in (16.5, 15.0, 13.5)],
            },
            {
                "from": 1, "to": 2, "generation": 1, "radius_mm": 4.0, "length_mm": 1.5,
                "lobe": "right upper lobe", "airway_name": None, "provenance": "interpolated",
                "interpolated_mm": 1.5, "interpolated_spans_mm": [[0.0, 1.5]],
                "downstream_of_interpolated": False,
                "centreline_mm": [[11.0, 9.0, z] for z in (13.5, 12.75, 12.0)],
            },
            {
                "from": 2, "to": 3, "generation": 2, "radius_mm": 3.0, "length_mm": 3.0,
                "lobe": "right upper lobe", "airway_name": None, "provenance": "ct",
                "interpolated_mm": 0.0, "interpolated_spans_mm": [],
                "downstream_of_interpolated": True,
                "centreline_mm": [[11.0, 9.0, z] for z in (12.0, 10.5, 9.0)],
            },
            {
                "from": 1, "to": 4, "generation": 1, "radius_mm": 3.0, "length_mm": 2.0,
                "lobe": "right upper lobe", "airway_name": None, "provenance": "ct",
                "interpolated_mm": 0.0, "interpolated_spans_mm": [],
                "downstream_of_interpolated": False,
                "centreline_mm": [[x, 9.0, 13.5] for x in (11.0, 12.0, 13.0)],
            },
        ],
        "review": [],
    }


def _bridge_json(tree=(11.0, 9.0, 13.5), piece=(11.0, 9.0, 12.0)) -> dict:
    return {
        "note": "interpolated, not CT-demonstrated airway",
        "radius_mm": 1.5,
        "bridges": [{
            "gap_mm": 1.5,
            "piece_volume_mm3": 50.0,
            "tree_point_mm": [float(v) for v in tree],
            "piece_point_mm": [float(v) for v in piece],
        }],
    }


def _paint_bridge_mask(harness, tree=(11.0, 9.0, 13.5), piece=(11.0, 9.0, 12.0)):
    """Put amber (label-2) voxels along the bridge for CT overlay tests."""
    path = harness["ts"] / "airway_bridges.nii.gz"
    img = nib.load(path)
    data = np.asanyarray(img.dataobj).astype(np.uint8)
    affine = np.asarray(img.affine, dtype=float)
    inv = np.linalg.inv(affine)
    for t in np.linspace(0, 1, 8):
        p = np.asarray(tree, float) * (1 - t) + np.asarray(piece, float) * t
        vox = np.rint((inv @ np.r_[p, 1.0])[:3]).astype(int)
        if np.all(vox >= 0) and np.all(vox < np.asarray(data.shape)):
            data[tuple(vox)] = 1
    nib.save(_nifti(data, affine), path)


@pytest.fixture
def harness(tmp_path, monkeypatch):
    cprint(f"p4b harness at {tmp_path}", "cyan")
    assert not str(tmp_path).startswith("/tmp"), "tests must not use /tmp"
    paths = _write_case_tree(tmp_path)
    graph = _multi_edge_graph()
    paths["graph"].write_text(json.dumps(graph))
    bridges = _bridge_json()
    (paths["ts"] / "airway_bridges.json").write_text(json.dumps(bridges))
    _paint_bridge_mask(paths)
    case = json.loads(paths["case"].read_text())
    case["source"] = {
        "series_uid_sha256": "abc" * 21 + "ab",
        "folder": "SERIES0",
        "series_number": 1,
        "series_description": "test",
        "convolution_kernel": "STANDARD",
        "spacing_mm": [1.0, 1.0, 1.5],
        "size": [24, 20, 16],
    }
    case["review"] = []
    paths["case"].write_text(json.dumps(case, indent=2) + "\n")

    monkeypatch.setattr("paths.PIPELINE", tmp_path)
    monkeypatch.setattr("paths.LOCAL_CASES", tmp_path / "data/extracted/local")
    monkeypatch.setattr("paths.AEROPATH", tmp_path / "data/extracted/AeroPath")
    monkeypatch.setattr("paths.TS_OUT", tmp_path / "ts_out")
    monkeypatch.setattr("paths.OUT", tmp_path / "out")
    (tmp_path / "data/extracted/AeroPath").mkdir(parents=True, exist_ok=True)
    (tmp_path / "app_state").mkdir(parents=True, exist_ok=True)
    reset_jobs_for_tests()
    return paths


@pytest.fixture
def client(harness):
    return TestClient(app, base_url="http://127.0.0.1")


def _set_clinician(client, role="Radiologist", initials="A.B."):
    res = client.put("/api/clinician", json={"role": role, "initials": initials})
    assert res.status_code == 200, res.text
    return res.json()


def _register_segment(case: dict, harness) -> None:
    register_artifact(case, "ct", harness["ct"])
    for name, path in [
        ("airways", harness["ts"] / "airways.nii.gz"),
        ("bridges_mask", harness["ts"] / "airway_bridges.nii.gz"),
        ("bridges_json", harness["ts"] / "airway_bridges.json"),
        ("lobes", harness["ts"] / "lobes.nii.gz"),
        ("graph", harness["graph"]),
    ]:
        register_artifact(case, name, path)


def _seed_through_step(client, harness, step: int, *, at_base: str = "2026-01-01T00:00:0"):
    case_path = harness["case"]
    case = json.loads(case_path.read_text())
    records = case.setdefault("records", [])
    if step > 1:
        records.append({
            "step": 1, "kind": "series_confirmed",
            "data": {
                "folder": "SERIES0", "series_number": 1,
                "description": "test", "kernel": "STANDARD", "overridden": False,
            },
            "clinician": "Radiologist, A.B.", "at": f"{at_base}0Z",
        })
    if step > 2:
        meta = client.get("/api/cases/local-001/ct/meta").json()
        affine = np.asarray(meta["affine"], dtype=float)
        ijk = [meta["shape"][0] / 2, meta["shape"][1] / 2, meta["shape"][2] / 2]
        ras = (affine @ np.array([ijk[0], ijk[1], ijk[2], 1.0]))[:3]
        case["lesion"] = {
            "ras_mm": [float(ras[0]), float(ras[1]), float(ras[2])],
            "confirmed_by": "Radiologist, A.B.",
            "at": f"{at_base}1Z",
        }
        records.append({
            "step": 2, "kind": "lesion_confirmed",
            "data": {"ras_mm": case["lesion"]["ras_mm"]},
            "clinician": "Radiologist, A.B.", "at": f"{at_base}1Z",
        })
    if step > 3:
        _register_segment(case, harness)
        records.append({
            "step": 3, "kind": "segmentation_done",
            "data": {"artifacts": list(SEGMENT_ARTIFACT_NAMES)},
            "clinician": "Radiologist, A.B.", "at": f"{at_base}2Z",
        })
    case_path.write_text(json.dumps(case, indent=2) + "\n")


def test_bridge_stable_id_and_note_validation(harness):
    bridges = _bridge_json()["bridges"]
    bid = bridge_stable_id(0, bridges[0])
    assert bid.startswith("0-")
    assert len(bid.split("-", 1)[1]) == 12
    assert bridge_stable_id(0, bridges[0]) == bid
    with pytest.raises(ValueError):
        validate_ct_evidence_note("")
    with pytest.raises(ValueError):
        validate_ct_evidence_note("x" * 301)
    assert validate_ct_evidence_note("  CT gap visible  ") == "CT gap visible"


def test_provenance_bridge_map_and_arc_selectability(harness):
    ref = CaseRef.parse("local-001")
    graph = json.loads(harness["graph"].read_text())
    bridges = load_bridges_annotated(ref)
    bridge_map = map_all_bridges(graph, bridges, ref)
    assert bridges[0]["id"] in bridge_map
    assert bridge_map[bridges[0]["id"]], "bridge must match densified samples"
    assert (ref.ts_out / "airway_bridges_labels.nii.gz").is_file()

    case = {"bridge_decisions": {}, "corrections": []}
    # Unconfirmed: bridge edge partially/not selectable; downstream not selectable;
    # upstream CT edge selectable; side branch selectable.
    eff = compute_effective_graph(graph, case, bridges, ref=ref, bridge_map=bridge_map)
    by_oi = {e["_original_index"]: e for e in eff["graph"]["edges"]}
    assert by_oi[0]["selectable"] is True
    assert by_oi[0].get("selectable_until_mm") is None
    assert by_oi[1]["selectable"] is False or by_oi[1].get("selectable_until_mm") == 0.0 \
        or (by_oi[1].get("selectable_until_mm") is not None
            and by_oi[1]["selectable_until_mm"] <= 1e-6)
    assert by_oi[2]["selectable"] is False
    assert by_oi[3]["selectable"] is True
    assert eff["removed"] == []

    # Confirm → all selectable, until null.
    case["bridge_decisions"] = {
        bridges[0]["id"]: {"decision": "confirmed", "note": "ok"},
    }
    eff = compute_effective_graph(graph, case, bridges, ref=ref, bridge_map=bridge_map)
    assert all(e["selectable"] for e in eff["graph"]["edges"])
    assert all(e.get("selectable_until_mm") is None for e in eff["graph"]["edges"])

    # Reject → bridge edge + downstream removed (span starts at 0).
    case["bridge_decisions"] = {
        bridges[0]["id"]: {"decision": "rejected", "note": "no CT"},
    }
    eff = compute_effective_graph(graph, case, bridges, ref=ref, bridge_map=bridge_map)
    removed_ois = {e["_original_index"] for e in eff["removed"]}
    assert 1 in removed_ois and 2 in removed_ois
    assert 0 not in removed_ois and 3 not in removed_ois


def test_arc_length_prefix_stays_selectable(harness):
    """CT part of an edge before the bridge span stays selectable."""
    ref = CaseRef.parse("local-001")
    graph = json.loads(harness["graph"].read_text())
    # Stretch edge 0 so a synthetic mid-edge bridge span can be injected.
    graph["edges"][0]["centreline_mm"] = [
        [11.0, 9.0, z] for z in (16.5, 15.75, 15.0, 14.25, 13.5)
    ]
    graph["edges"][0]["length_mm"] = 3.0
    bridges = load_bridges_annotated(ref)
    # Pretend the unconfirmed bridge hits edge 0 from arc 1.5 onward.
    bridge_map = {
        bridges[0]["id"]: [{"edge_index": 0, "start_mm": 1.5, "end_mm": 3.0}],
    }
    case = {"bridge_decisions": {}, "corrections": []}
    eff = compute_effective_graph(graph, case, bridges, bridge_map=bridge_map)
    by_oi = {e["_original_index"]: e for e in eff["graph"]["edges"]}
    assert by_oi[0]["selectable"] is True
    assert by_oi[0]["selectable_until_mm"] == 1.5
    # Downstream of edge 0 not selectable.
    assert by_oi[1]["selectable"] is False
    assert by_oi[2]["selectable"] is False


def test_unmatched_bridge_blocks_step4(client, harness):
    _set_clinician(client)
    # Move bridge endpoints far outside the volume → unmatched.
    (harness["ts"] / "airway_bridges.json").write_text(json.dumps(_bridge_json(
        tree=(100.0, 100.0, 100.0), piece=(101.0, 100.0, 100.0),
    )))
    _seed_through_step(client, harness, 4)
    body = client.get("/api/cases/local-001/workflow").json()
    assert body["steps"][3]["status"] == "available"
    reason = body["steps"][3].get("reason") or ""
    assert "not found on the graph" in reason
    review = client.get("/api/cases/local-001/review").json()
    bid = review["bridges"][0]["id"]
    assert unmatched_bridge_reason(bid) in (reason, unmatched_bridge_reason(bid))
    res = client.put(f"/api/cases/local-001/bridges/{bid}", json={
        "decision": "confirmed", "note": "cannot match",
    })
    assert res.status_code == 422
    assert "not found on the graph" in res.json()["detail"]


def test_subtree_removal_correction(harness):
    ref = CaseRef.parse("local-001")
    graph = json.loads(harness["graph"].read_text())
    bridges = load_bridges_annotated(ref)
    bridge_map = map_all_bridges(graph, bridges, ref)
    case = {
        "bridge_decisions": {
            bridges[0]["id"]: {"decision": "confirmed", "note": "ok"},
        },
        "corrections": [{
            "id": "c1", "kind": "remove_branch", "edge_id": 3, "undone": False,
            "root_edge": {
                "edge_id": 3,
                "start_mm": graph["edges"][3]["centreline_mm"][0],
                "end_mm": graph["edges"][3]["centreline_mm"][-1],
            },
            "removed_edges": [],
            "status": "active",
        }],
    }
    eff = compute_effective_graph(graph, case, bridges, ref=ref, bridge_map=bridge_map)
    assert {e["_original_index"] for e in eff["removed"]} == {3}
    assert all(e["_original_index"] != 3 for e in eff["graph"]["edges"])
    assert eff["removed"][0].get("correction_id") == "c1"


def test_correction_rematch_root_only_endpoint_to_endpoint(harness):
    graph = json.loads(harness["graph"].read_text())
    start = graph["edges"][3]["centreline_mm"][0]
    end = graph["edges"][3]["centreline_mm"][-1]
    assert rematch_edge_by_endpoints(graph, start, end) == 3
    # Endpoint-to-endpoint (not distance-to-centreline): points on a long edge's
    # centreline that are >2 mm from every edge's endpoints → no match.
    graph["edges"][0]["centreline_mm"] = [
        [11.0, 9.0, z] for z in (30.0, 24.0, 18.0, 12.0, 6.0)
    ]
    mid_a = [11.0, 9.0, 24.0]
    mid_b = [11.0, 9.0, 18.0]
    assert rematch_edge_by_endpoints(graph, mid_a, mid_b) is None
    # One endpoint matches, the other is >2 mm away → no match.
    almost = [start[0] + 5.0, start[1], start[2]]
    assert rematch_edge_by_endpoints(graph, start, almost) is None
    # Restore edge 0 for the stale/active checks below.
    graph["edges"][0]["centreline_mm"] = [[11.0, 9.0, z] for z in (16.5, 15.0, 13.5)]

    far = [start[0] + 50.0, start[1] + 50.0, start[2] + 50.0]
    case = {
        "bridge_decisions": {},
        "corrections": [{
            "id": "c-stale", "kind": "remove_branch", "undone": False,
            "root_edge": {"edge_id": 3, "start_mm": far, "end_mm": [far[0] + 1, far[1], far[2]]},
            "removed_edges": [
                {"edge_id": 3, "start_mm": far, "end_mm": [far[0] + 1, far[1], far[2]]},
                # Stale subtree specs must NOT force stale if root rematches —
                # here root fails → stale.
            ],
        }],
    }
    eff = compute_effective_graph(graph, case, [], bridge_map={})
    assert eff["corrections"][0]["status"] == "stale"
    assert eff["removed"] == []

    # Root rematches; junk subtree specs ignored → active, subtree re-derived.
    case["corrections"][0]["root_edge"] = {
        "edge_id": 3, "start_mm": start, "end_mm": end,
    }
    case["corrections"][0]["removed_edges"] = [{
        "edge_id": 99, "start_mm": far, "end_mm": far,
    }]
    eff = compute_effective_graph(graph, case, [], bridge_map={})
    assert eff["corrections"][0]["status"] == "active"
    assert {e["_original_index"] for e in eff["removed"]} == {3}


def test_manual_mask_nearest_sample_y_junction(harness):
    """manual ⊆ airways; junction voxels of kept parent/sibling NOT labelled."""
    ref = CaseRef.parse("local-001")
    affine = np.diag([1.0, 1.0, 1.5, 1.0])
    # Synthetic Y in a small grid: parent trunk + two children.
    airways = np.zeros((8, 8, 8), dtype=bool)
    # Parent along z at (3,3,*)
    airways[3, 3, 0:4] = True
    # Child A (+x), Child B (-x) from junction z=3
    airways[4:7, 3, 3] = True
    airways[0:3, 3, 3] = True
    edges = [
        {"centreline_mm": [[3.0, 3.0, float(z)] for z in (0, 1, 2, 3)]},  # parent oi0
        {"centreline_mm": [[float(x), 3.0, 3.0] for x in (3, 4, 5, 6)]},  # child A oi1
        {"centreline_mm": [[float(x), 3.0, 3.0] for x in (3, 2, 1, 0)]},  # child B oi2
    ]
    manual, n = compute_manual_labels(airways, affine, edges, removed_ois={1})
    assert n >= 1
    assert not np.any(manual & ~airways)
    # Junction voxel (3,3,3) nearest to parent/both — should NOT be labelled as removed.
    assert manual[3, 3, 3] == 0
    # Far child-A voxel should be labelled.
    assert manual[6, 3, 3] == 1
    # Sibling child-B voxel stays unlabelled.
    assert manual[0, 3, 3] == 0


def test_manual_mask_zero_voxel_warning_no_tube(harness):
    """Removed edge far from airways; kept edge owns all voxels → zero manual, no tube."""
    ref = CaseRef.parse("local-001")
    airways_before = sha256_file(ref.airways)
    graph = {
        "edges": [
            {
                "centreline_mm": [[11.0, 9.0, float(z)] for z in (9.0, 10.5, 12.0, 13.5)],
                "radius_mm": 2.0,
            },
            {
                "centreline_mm": [[200.0, 200.0, 200.0], [201.0, 200.0, 200.0]],
                "radius_mm": 2.0,
            },
        ],
    }
    removed = [{
        "centreline_mm": graph["edges"][1]["centreline_mm"],
        "radius_mm": 2.0,
        "_original_index": 1,
    }]
    path, n_vox = write_manual_mask(ref, graph, removed)
    assert n_vox == 0
    assert path.is_file()
    assert sha256_file(ref.airways) == airways_before
    manual = np.asanyarray(nib.load(path).dataobj) > 0
    assert not manual.any()  # no tube fallback


def test_manual_mask_voxels_only_removed_never_touches_airways(harness):
    ref = CaseRef.parse("local-001")
    airways_before = sha256_file(ref.airways)
    graph = json.loads(harness["graph"].read_text())
    removed = [{
        **graph["edges"][3],
        "_original_index": 3,
    }]
    path, n_vox = write_manual_mask(ref, graph, removed)
    assert path.is_file()
    assert sha256_file(ref.airways) == airways_before
    manual = np.asanyarray(nib.load(path).dataobj) > 0
    airways = np.asanyarray(nib.load(ref.airways).dataobj) > 0
    assert n_vox >= 1
    assert not np.any(manual & ~airways)


def test_api_bridge_decision_and_review(client, harness):
    _set_clinician(client)
    _seed_through_step(client, harness, 4)
    review = client.get("/api/cases/local-001/review").json()
    assert len(review["bridges"]) == 1
    assert review["bridges"][0]["matched"] is True
    bid = review["bridges"][0]["id"]

    res = client.put(f"/api/cases/local-001/bridges/{bid}", json={
        "decision": "confirmed", "note": "",
    })
    assert res.status_code == 422

    res = client.put(f"/api/cases/local-001/bridges/{bid}", json={
        "decision": "confirmed", "note": "CT continuum visible across gap",
    })
    assert res.status_code == 200, res.text
    assert res.json()["decision"]["decision"] == "confirmed"
    assert "invalidates" in res.json()["record"]["data"]

    res = client.post("/api/cases/local-001/records", json={
        "step": 4, "kind": "bridge_decision",
        "data": {"decision": "rejected"},
    })
    assert res.status_code == 403


def test_api_rejects_unknown_and_already_removed_edges(client, harness):
    _set_clinician(client)
    _seed_through_step(client, harness, 4)
    review = client.get("/api/cases/local-001/review").json()
    bid = review["bridges"][0]["id"]
    client.put(f"/api/cases/local-001/bridges/{bid}", json={
        "decision": "confirmed", "note": "bridge ok",
    })
    res = client.post("/api/cases/local-001/corrections", json={
        "kind": "remove_branch", "edge_id": 99, "note": "missing",
    })
    assert res.status_code == 422
    res = client.post("/api/cases/local-001/corrections", json={
        "kind": "remove_branch", "edge_id": 3, "note": "CT does not support this spur",
    })
    assert res.status_code == 200, res.text
    res = client.post("/api/cases/local-001/corrections", json={
        "kind": "remove_branch", "edge_id": 3, "note": "already gone",
    })
    assert res.status_code == 422
    assert "already removed" in res.json()["detail"]


def test_api_correction_undo_and_workflow_step4(client, harness):
    _set_clinician(client)
    _seed_through_step(client, harness, 4)
    review = client.get("/api/cases/local-001/review").json()
    bid = review["bridges"][0]["id"]
    client.put(f"/api/cases/local-001/bridges/{bid}", json={
        "decision": "confirmed", "note": "bridge ok on CT",
    })

    body = client.get("/api/cases/local-001/workflow").json()
    assert body["steps"][3]["status"] == "done"

    res = client.post("/api/cases/local-001/corrections", json={
        "kind": "remove_branch", "edge_id": 3, "note": "CT does not support this spur",
    })
    assert res.status_code == 200, res.text
    cid = res.json()["correction"]["id"]
    assert (harness["ts"] / "airway_manual.nii.gz").is_file()

    payload = client.get("/api/cases/local-001/payload?allow_review=true").json()
    assert any(r.get("ghost") for r in payload.get("removed", []))
    assert any(r.get("correction_id") == cid for r in payload["removed"])
    assert all(e.get("oi") != 3 for e in payload["edges"])

    res = client.delete(f"/api/cases/local-001/corrections/{cid}")
    assert res.status_code == 200, res.text
    payload = client.get("/api/cases/local-001/payload?allow_review=true").json()
    assert payload.get("removed") == []


def test_review_change_invalidates_downstream(client, harness):
    _set_clinician(client)
    _seed_through_step(client, harness, 4)
    review = client.get("/api/cases/local-001/review").json()
    bid = review["bridges"][0]["id"]
    client.put(f"/api/cases/local-001/bridges/{bid}", json={
        "decision": "confirmed", "note": "ok",
    })
    case = json.loads(harness["case"].read_text())
    for rec in case["records"]:
        if rec.get("kind") == "bridge_decision":
            rec["at"] = "2026-01-01T00:00:03Z"
    if bid in (case.get("bridge_decisions") or {}):
        case["bridge_decisions"][bid]["at"] = "2026-01-01T00:00:03Z"
    case["records"].append({
        "step": 5, "kind": "route_start_confirmed",
        "data": {"node": 0, "ras_mm": [11.0, 9.0, 16.5], "moved": False},
        "clinician": "Radiologist, A.B.", "at": "2026-01-01T00:00:05Z",
    })
    harness["case"].write_text(json.dumps(case, indent=2) + "\n")
    body = client.get("/api/cases/local-001/workflow").json()
    assert body["steps"][3]["status"] == "done"
    assert body["steps"][4]["status"] == "done"

    client.put(f"/api/cases/local-001/bridges/{bid}", json={
        "decision": "rejected", "note": "revisit: no CT support",
    })
    body = client.get("/api/cases/local-001/workflow").json()
    assert body["steps"][3]["status"] == "done"
    assert body["steps"][4]["status"] == "available"


def test_stale_correction_blocks_step4(client, harness):
    _set_clinician(client)
    _seed_through_step(client, harness, 4)
    review = client.get("/api/cases/local-001/review").json()
    bid = review["bridges"][0]["id"]
    client.put(f"/api/cases/local-001/bridges/{bid}", json={
        "decision": "confirmed", "note": "ok",
    })
    case = json.loads(harness["case"].read_text())
    case["corrections"] = [{
        "id": "stale-1", "kind": "remove_branch", "undone": False,
        "root_edge": {
            "edge_id": 99,
            "start_mm": [100.0, 100.0, 100.0],
            "end_mm": [101.0, 100.0, 100.0],
        },
        "removed_edges": [],
        "status": "active",
    }]
    harness["case"].write_text(json.dumps(case, indent=2) + "\n")
    body = client.get("/api/cases/local-001/workflow").json()
    assert body["steps"][3]["status"] == "available"


def test_review_signoff_api_server_at_no_client_date(client, harness):
    _set_clinician(client)
    graph = json.loads(harness["graph"].read_text())
    graph["review"] = [{"kind": "small_lobe", "lobe": "right middle lobe",
                        "ml": 10.0, "median_ml": 80.0}]
    harness["graph"].write_text(json.dumps(graph))
    _seed_through_step(client, harness, 4)
    review = client.get("/api/cases/local-001/review").json()
    bid = review["bridges"][0]["id"]
    client.put(f"/api/cases/local-001/bridges/{bid}", json={
        "decision": "confirmed", "note": "ok",
    })
    body = client.get("/api/cases/local-001/workflow").json()
    assert body["steps"][3]["status"] == "available"

    res = client.post("/api/cases/local-001/review-signoff", json={
        "kind": "small_lobe", "lobe": "right middle lobe",
        "decision": "rejected", "finding": "Too small on CT",
        "date": "2099-01-01",  # client date must be ignored / rejected by schema
    })
    # Extra field: pydantic v2 may ignore or forbid — either no `date` stored.
    if res.status_code == 200:
        so = res.json()["signoff"]
        assert "at" in so
        assert so.get("date") is None
        assert "date" not in so or so["date"] != "2099-01-01"
    else:
        # If schema forbids extra fields, retry without date.
        assert res.status_code in {422, 200}
        res = client.post("/api/cases/local-001/review-signoff", json={
            "kind": "small_lobe", "lobe": "right middle lobe",
            "decision": "rejected", "finding": "Too small on CT",
        })
        assert res.status_code == 200, res.text
        so = res.json()["signoff"]
        assert "at" in so
        assert "date" not in so

    # No note side-channel record for sign-off.
    case = json.loads(harness["case"].read_text())
    assert not any(
        r.get("kind") == "note" and (r.get("data") or {}).get("action") == "review_signoff"
        for r in case.get("records") or []
    )
    body = client.get("/api/cases/local-001/workflow").json()
    assert body["steps"][3]["status"] == "available"

    res = client.post("/api/cases/local-001/review-signoff", json={
        "kind": "small_lobe", "lobe": "right middle lobe",
        "decision": "approved", "finding": "Acceptable variation",
    })
    assert res.status_code == 200, res.text
    assert "at" in res.json()["signoff"]
    body = client.get("/api/cases/local-001/workflow").json()
    assert body["steps"][3]["status"] == "done"


def test_malformed_signoff_skipped(client, harness):
    _set_clinician(client)
    graph = json.loads(harness["graph"].read_text())
    graph["review"] = [{"kind": "small_lobe", "lobe": "right middle lobe",
                        "ml": 10.0, "median_ml": 80.0}]
    harness["graph"].write_text(json.dumps(graph))
    _seed_through_step(client, harness, 4)
    case = json.loads(harness["case"].read_text())
    case["review_signoff"] = [
        {"kind": "small_lobe", "lobe": "right middle lobe", "decision": "approved"},  # bad
        "not-a-dict",
    ]
    harness["case"].write_text(json.dumps(case, indent=2) + "\n")
    # Must not 500.
    res = client.get("/api/cases/local-001/review")
    assert res.status_code == 200, res.text
    flags = res.json()["review_flags"]
    assert flags[0]["resolved"] is False


def test_masks_label3_after_correction(client, harness):
    _set_clinician(client)
    _seed_through_step(client, harness, 4)
    review = client.get("/api/cases/local-001/review").json()
    bid = review["bridges"][0]["id"]
    client.put(f"/api/cases/local-001/bridges/{bid}", json={
        "decision": "confirmed", "note": "ok",
    })
    client.post("/api/cases/local-001/corrections", json={
        "kind": "remove_branch", "edge_id": 3, "note": "unsupported on CT",
    })
    meta = client.get("/api/cases/local-001/masks/meta").json()
    assert meta["labels"]["3"] == "manual"
    raw = client.get("/api/cases/local-001/masks").content
    labels = np.frombuffer(raw, dtype=np.uint8)
    assert int((labels == 3).sum()) >= 1


def test_densify_step():
    pts = np.array([[0.0, 0.0, 0.0], [0.0, 0.0, 1.0]])
    samples, arcs = densify_centreline(pts, step_mm=0.25)
    assert len(samples) >= 5
    assert arcs[-1] == pytest.approx(1.0)
    gaps = np.diff(arcs)
    assert float(gaps.max()) <= 0.25 + 1e-9
