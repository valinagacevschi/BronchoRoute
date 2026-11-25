import json

import numpy as np
import pytest

from test_app_server import _write_case_tree
from app.bridge_evidence import auto_decide_bridges, bridge_evidence
from app.review import load_bridges_annotated
from app.caseref import CaseRef


def _seed(tmp_path, monkeypatch):
    fixture = _write_case_tree(tmp_path, shape=(24, 20, 16))
    monkeypatch.setattr("paths.PIPELINE", tmp_path)
    monkeypatch.setattr("paths.LOCAL_CASES", tmp_path / "data/extracted/local")
    monkeypatch.setattr("paths.TS_OUT", tmp_path / "ts_out")
    monkeypatch.setattr("paths.OUT", tmp_path / "out")
    # CT: -800 everywhere except a -200 block over voxels x 8:16, y 6:14, z 4:12.
    bridges = [
        # full air
        {"gap_mm": 1.0, "piece_volume_mm3": 25.0,
         "tree_point_mm": [1.0, 1.0, 1.5], "piece_point_mm": [2.0, 1.0, 1.5]},
        # full soft tissue (inside the -200 block)
        {"gap_mm": 1.0, "piece_volume_mm3": 25.0,
         "tree_point_mm": [10.0, 10.0, 15.0], "piece_point_mm": [11.0, 10.0, 15.0]},
        # crosses the block edge
        {"gap_mm": 1.0, "piece_volume_mm3": 25.0,
         "tree_point_mm": [7.0, 10.0, 15.0], "piece_point_mm": [9.0, 10.0, 15.0]},
    ]
    (fixture["ts"] / "airway_bridges.json").write_text(json.dumps({
        "note": "interpolated, not CT-demonstrated airway",
        "radius_mm": 1.0,
        "bridges": bridges,
    }))
    return fixture


def test_scores_split_air_tissue_and_ambiguous(tmp_path, monkeypatch):
    fixture = _seed(tmp_path, monkeypatch)
    ref = CaseRef.parse("local-001")
    ids = [b["id"] for b in load_bridges_annotated(ref)]

    rows = {row["id"]: row for row in bridge_evidence("local-001")}

    assert rows[ids[0]]["air_fraction"] == 1.0
    assert rows[ids[0]]["proposed_decision"] == "confirmed"
    assert rows[ids[1]]["air_fraction"] == 0.0
    assert rows[ids[1]]["proposed_decision"] == "rejected"
    ambiguous = rows[ids[2]]
    assert 0.10 < ambiguous["air_fraction"] < 0.75
    assert ambiguous["proposed_decision"] is None


def test_auto_decides_clear_bridges_and_leaves_the_middle(tmp_path, monkeypatch):
    fixture = _seed(tmp_path, monkeypatch)
    ref = CaseRef.parse("local-001")
    ids = [b["id"] for b in load_bridges_annotated(ref)]

    result = auto_decide_bridges("local-001")

    record = json.loads(fixture["case"].read_text())
    assert record["bridge_decisions"][ids[0]]["decision"] == "confirmed"
    assert record["bridge_decisions"][ids[0]]["clinician"] == "auto"
    assert record["bridge_decisions"][ids[1]]["decision"] == "rejected"
    assert ids[2] not in record["bridge_decisions"]
    assert record["bridge_evidence"][ids[2]]["proposed_decision"] is None
    assert set(result["written"]) == {ids[0], ids[1]}


def test_auto_never_overwrites_a_human_decision(tmp_path, monkeypatch):
    fixture = _seed(tmp_path, monkeypatch)
    ref = CaseRef.parse("local-001")
    ids = [b["id"] for b in load_bridges_annotated(ref)]
    record = json.loads(fixture["case"].read_text())
    record["bridge_decisions"] = {
        ids[0]: {"decision": "rejected", "clinician": "Radiologist, A.B.",
                 "at": "2026-01-01T00:00:00Z"},
    }
    fixture["case"].write_text(json.dumps(record))

    auto_decide_bridges("local-001")

    record = json.loads(fixture["case"].read_text())
    assert record["bridge_decisions"][ids[0]]["decision"] == "rejected"
    assert record["bridge_decisions"][ids[0]]["clinician"] == "Radiologist, A.B."
    assert ids[0] not in [r["data"]["bridge_id"] for r in record["records"]
                          if r.get("kind") == "bridge_decision"]
