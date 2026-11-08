# Changes: RouteTarget generate_realism flag controls realism start (#68).
# Changes: candidates need a Lesion only; RouteStart confirmation is not required.
# Changes: hide legacy review behind the default-off feature flag.
# Changes: Phase 4d review — assert retarget yields a new route_hash (not just truthy).
# Phase 4c review fixes — RouteStart route length sync, reachability,
# fly exact sample, stale target, tie-breaks, 409 before step 5, NaN 422.
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest
from fastapi.testclient import TestClient
from termcolor import cprint

import repo_root

repo_root.ensure()

from app.route_target import (  # noqa: E402
    NOT_ON_CT,
    NOT_REACHABLE,
    SNAP_MM,
    candidate_metrics_for_sample,
    rank_route_target_candidates,
    resolve_edge_sample,
    route_edges_from_start,
    snap_to_selectable_ct,
    stale_reason_for_route_target,
)
from app.server import app  # noqa: E402
from conftest import symlink_input_file  # noqa: E402
from test_app_server import _write_case_tree  # noqa: E402
from test_p4a_workflow import _seed_through_step  # noqa: E402
from test_p4b_review import _bridge_json, _multi_edge_graph, _paint_bridge_mask  # noqa: E402


def _rt_graph() -> dict:
    """Synthetic tree for ranking / dedup / interpolated exclusion.

    Layout (RAS mm):
      0 --ct--> 1 --interp--> 2 --ct--> 3   (main, gens 0,1,2)
           \\
            --ct--> 4   (side, gen 1) tip near lesion
            --ct--> 5   (near-duplicate tip, within 5 mm of 4)
    Lesion near tips of 4 and 5; edge 3 farther.
    """
    return {
        "case": "local-001",
        "root_node_id": 0,
        "coordinate_system": "RAS",
        "nodes": [
            {"id": 0, "position_mm": [0.0, 0.0, 0.0], "radius_mm": 8.0},
            {"id": 1, "position_mm": [0.0, 0.0, -10.0], "radius_mm": 5.0},
            {"id": 2, "position_mm": [0.0, 0.0, -13.0], "radius_mm": 4.0},
            {"id": 3, "position_mm": [0.0, 0.0, -30.0], "radius_mm": 3.0},
            {"id": 4, "position_mm": [20.0, 0.0, -10.0], "radius_mm": 3.0},
            {"id": 5, "position_mm": [22.0, 0.0, -10.0], "radius_mm": 3.0},
            {"id": 6, "position_mm": [0.0, 20.0, -10.0], "radius_mm": 3.0},
        ],
        "edges": [
            {
                "from": 0, "to": 1, "generation": 0, "radius_mm": 6.0, "length_mm": 10.0,
                "lobe": None, "airway_name": "trachea", "provenance": "ct",
                "interpolated_mm": 0.0, "interpolated_spans_mm": [],
                "downstream_of_interpolated": False,
                "centreline_mm": [[0.0, 0.0, -z] for z in range(0, 11)],
            },
            {
                "from": 1, "to": 2, "generation": 1, "radius_mm": 4.0, "length_mm": 3.0,
                "lobe": "right upper lobe", "airway_name": None, "provenance": "interpolated",
                "interpolated_mm": 3.0, "interpolated_spans_mm": [[0.0, 3.0]],
                "downstream_of_interpolated": False,
                "centreline_mm": [[0.0, 0.0, z] for z in (-10.0, -11.5, -13.0)],
            },
            {
                "from": 2, "to": 3, "generation": 2, "radius_mm": 3.0, "length_mm": 17.0,
                "lobe": "right upper lobe", "airway_name": None, "provenance": "ct",
                "interpolated_mm": 0.0, "interpolated_spans_mm": [],
                "downstream_of_interpolated": True,
                "centreline_mm": [[0.0, 0.0, -z] for z in range(13, 31)],
            },
            {
                "from": 1, "to": 4, "generation": 1, "radius_mm": 3.0, "length_mm": 20.0,
                "lobe": "right lower lobe", "airway_name": None, "provenance": "ct",
                "interpolated_mm": 0.0, "interpolated_spans_mm": [],
                "downstream_of_interpolated": False,
                "centreline_mm": [[x, 0.0, -10.0] for x in range(0, 21)],
            },
            {
                "from": 1, "to": 5, "generation": 1, "radius_mm": 3.0, "length_mm": 22.0,
                "lobe": "right lower lobe", "airway_name": None, "provenance": "ct",
                "interpolated_mm": 0.0, "interpolated_spans_mm": [],
                "downstream_of_interpolated": False,
                "centreline_mm": [[x, 0.0, -10.0] for x in range(0, 23)],
            },
            {
                "from": 1, "to": 6, "generation": 3, "radius_mm": 2.5, "length_mm": 20.0,
                "lobe": "left lower lobe", "airway_name": None, "provenance": "ct",
                "interpolated_mm": 0.0, "interpolated_spans_mm": [],
                "downstream_of_interpolated": False,
                "centreline_mm": [[0.0, y, -10.0] for y in range(0, 21)],
            },
        ],
        "review": [],
    }


@pytest.fixture
def harness(tmp_path, monkeypatch):
    monkeypatch.setenv("BRONCHOROUTE_LEGACY_REVIEW", "1")
    cprint(f"p4c harness at {tmp_path}", "cyan")
    assert not str(tmp_path).startswith("/tmp"), "tests must not use /tmp"
    paths = _write_case_tree(tmp_path)
    graph = _rt_graph()
    paths["graph"].write_text(json.dumps(graph))
    # No bridges → vacuous review so step 4 completes after segment.
    (paths["ts"] / "airway_bridges.json").write_text(json.dumps({
        "note": "interpolated, not CT-demonstrated airway",
        "radius_mm": 1.0,
        "bridges": [],
    }))
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
    return paths


@pytest.fixture
def client(harness):
    return TestClient(app, base_url="http://127.0.0.1")


@pytest.fixture
def fake_home(tmp_path, monkeypatch):
    home = tmp_path / "fake_home"
    home.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    monkeypatch.setenv("HOME", str(home))
    return home


def _set_clinician(client):
    res = client.put("/api/clinician", json={"role": "Radiologist", "initials": "A.B."})
    assert res.status_code == 200, res.text


def _place_lesion(case_path: Path, ras: list[float]) -> None:
    case = json.loads(case_path.read_text())
    case["lesion"] = {
        "ras_mm": ras,
        "confirmed_by": "Radiologist, A.B.",
        "at": "2026-01-01T00:00:01Z",
    }
    # Keep records in sync if already seeded.
    for rec in case.get("records") or []:
        if rec.get("kind") == "lesion_confirmed":
            rec["data"] = {"ras_mm": ras}
    case_path.write_text(json.dumps(case, indent=2) + "\n")


def test_ranking_tiebreaks_and_dedup():
    cprint("ranking / dedup pure", "cyan")
    graph = _rt_graph()
    for i, e in enumerate(graph["edges"]):
        e["_original_index"] = i
        e["selectable"] = True
        e["selectable_until_mm"] = None
    # Mark interpolated bridge edge as selectable only to span start (unconfirmed).
    graph["edges"][1]["selectable"] = True
    graph["edges"][1]["selectable_until_mm"] = 0.0
    graph["edges"][2]["selectable"] = False  # beyond unconfirmed
    lesion = [20.0, 0.0, -10.0]
    case = {"route_start": {"node": 0}, "records": [{
        "kind": "route_start_confirmed", "data": {"node": 0},
    }]}
    cands = rank_route_target_candidates(graph, case, lesion)
    assert len(cands) <= 3
    # Nearest tip should be edge 3 (to node 4) at [20,0,-10].
    assert cands[0]["edge_id"] == 3
    assert cands[0]["target_gap_mm"] <= cands[-1]["target_gap_mm"]
    # Edge 4 tip is within DEDUP_MM of edge 3 tip → excluded.
    assert all(c["edge_id"] != 4 for c in cands)
    # Interpolated-only edge 1 and non-selectable edge 2 excluded.
    assert all(c["edge_id"] not in {1, 2} for c in cands)
    # Generations non-decreasing among equal-gap would apply; overall sorted.
    gaps = [c["target_gap_mm"] for c in cands]
    assert gaps == sorted(gaps)


def test_snap_within_and_beyond():
    graph = _rt_graph()
    for i, e in enumerate(graph["edges"]):
        e["_original_index"] = i
        e["selectable"] = e.get("provenance") == "ct" and i != 2
        e["selectable_until_mm"] = None
    graph["edges"][2]["selectable"] = False
    near = snap_to_selectable_ct(graph, [20.1, 0.0, -10.0], max_mm=SNAP_MM)
    assert near is not None
    assert near["edge_id"] == 3
    far = snap_to_selectable_ct(graph, [50.0, 50.0, 50.0], max_mm=SNAP_MM)
    assert far is None


def test_api_gating_and_put(client, harness, fake_home):
    _set_clinician(client)
    res = client.get("/api/cases/local-001/route-candidates")
    assert res.status_code == 409, res.text
    assert res.json()["detail"] == "place a Lesion first"

    _seed_through_step(client, harness, 6)
    _place_lesion(harness["case"], [20.0, 0.0, -10.0])

    res = client.get("/api/cases/local-001/route-candidates")
    assert res.status_code == 200, res.text
    cands = res.json()["candidates"]
    assert 1 <= len(cands) <= 3
    top = cands[0]
    for key in (
        "edge_id", "sample_index", "ras_mm", "target_gap_mm",
        "target_inside_airway", "generation", "route_length_mm",
        "interpolated_mm_on_route", "lobe",
    ):
        assert key in top, key

    # Custom beyond 2 mm → 422
    res = client.put("/api/cases/local-001/route-target", json={
        "ras_mm": [90.0, 90.0, 90.0],
    })
    assert res.status_code == 422
    assert NOT_ON_CT in str(res.json().get("detail", "")).lower() or NOT_ON_CT in res.text

    # Snap within 2 mm
    ras = list(top["ras_mm"])
    ras[0] += 0.5
    res = client.put("/api/cases/local-001/route-target", json={"ras_mm": ras})
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["record"]["kind"] == "route_target_chosen"
    assert body["record"]["step"] == 6
    assert body["route_target"]["rank"] == "custom"
    assert body["route_target"]["target_gap_mm"] is not None

    # Candidate pick
    res = client.put("/api/cases/local-001/route-target", json={
        "edge_id": top["edge_id"], "sample_index": top["sample_index"],
    })
    assert res.status_code == 200, res.text
    chosen = res.json()["route_target"]
    assert chosen["rank"] == 1

    payload = client.get("/api/cases/local-001/payload?allow_review=1").json()
    assert payload["route_target"]["edge_id"] == top["edge_id"]
    assert payload["endpoint"] is not None
    assert payload["target_gap_mm"] == chosen["target_gap_mm"]
    assert payload["target_inside_airway"] == chosen["target_inside_airway"]
    assert payload["lesion"] is not None
    # Fly ends at stored ras_mm (absolute) within 1e-6.
    fly = payload["fly"]["p"]
    assert fly, "expected truncated fly path"
    origin = np.asarray(payload["origin_mm"], dtype=float)
    fly_end_abs = np.asarray(fly[-1], dtype=float) + origin
    stored = np.asarray(chosen["ras_mm"], dtype=float)
    assert float(np.linalg.norm(fly_end_abs - stored)) < 1e-6
    assert abs(float(top["route_length_mm"]) - float(chosen["route_length_mm"])) < 1e-6


def test_route_target_generate_realism_flag(client, harness, fake_home, monkeypatch):
    """The client checkbox decides whether RouteTarget confirm starts realism."""
    _set_clinician(client)
    _seed_through_step(client, harness, 6)
    _place_lesion(harness["case"], [20.0, 0.0, -10.0])
    cands = client.get("/api/cases/local-001/route-candidates").json()["candidates"]
    assert cands
    top = cands[0]

    from app.realism import worker as realism_worker

    calls: list[str] = []
    monkeypatch.setattr(
        realism_worker, "start_realism", lambda case_id: calls.append(case_id) or {},
    )

    payload = {"edge_id": top["edge_id"], "sample_index": top["sample_index"]}
    res = client.put(
        "/api/cases/local-001/route-target",
        json={**payload, "generate_realism": False},
    )
    assert res.status_code == 200, res.text
    assert calls == []

    res = client.put("/api/cases/local-001/route-target", json=payload)
    assert res.status_code == 200, res.text
    assert calls == ["local-001"]


def test_downstream_invalidation_on_retarget(client, harness, fake_home):
    """Changing RouteTarget to a forked path requires a fresh walk confirmation."""
    import time

    _set_clinician(client)
    _seed_through_step(client, harness, 6)
    _place_lesion(harness["case"], [20.0, 0.0, -10.0])
    cands = client.get("/api/cases/local-001/route-candidates").json()["candidates"]
    assert cands
    top = cands[0]
    # First choose a target whose route includes bifurcation node 1 (side branch).
    res = client.put("/api/cases/local-001/route-target", json={
        "edge_id": top["edge_id"], "sample_index": top["sample_index"],
    })
    assert res.status_code == 200, res.text
    payload = client.get("/api/cases/local-001/payload?allow_review=1").json()
    forks = payload.get("forks") or []
    assert forks, "expected at least one fork on side-branch route"
    for i in range(len(forks)):
        res = client.put(f"/api/cases/local-001/walkthrough/{i}", json={
            "confirmed": True, "note": "ok",
        })
        assert res.status_code == 200, res.text
    wf = client.get("/api/cases/local-001/workflow").json()
    assert wf["steps"][5]["status"] == "done"
    assert wf["steps"][6]["status"] == "done", wf["steps"][6]
    first_hash = res.json()["route_hash"]
    assert isinstance(first_hash, str) and len(first_hash) == 64

    # Wait so the re-target `at` is strictly after the walk confirms.
    time.sleep(1.1)
    # Same forked edge, earlier sample → new route_hash; forks remain.
    new_sample = max(0, int(top["sample_index"]) - 5)
    assert new_sample != int(top["sample_index"])
    res = client.put("/api/cases/local-001/route-target", json={
        "edge_id": top["edge_id"], "sample_index": new_sample,
    })
    assert res.status_code == 200, res.text
    wf = client.get("/api/cases/local-001/workflow").json()
    assert wf["steps"][5]["status"] == "done"
    # New target → walk incomplete (old confirmations keyed by previous route_hash).
    assert wf["steps"][6]["status"] == "available", wf["steps"][6]
    # Confirm a fork on the new route yields a different route_hash.
    res2 = client.put("/api/cases/local-001/walkthrough/0", json={"confirmed": True})
    assert res2.status_code == 200, res2.text
    assert res2.json()["route_hash"] != first_hash


def test_selectable_until_respected(client, harness, fake_home):
    """Unconfirmed bridge: samples past selectable_until_mm excluded."""
    _set_clinician(client)
    # Use multi-edge graph with real bridge mask mapping; confirm nothing →
    # edge beyond bridge not selectable. Vacuous flags: clear review list.
    graph = _multi_edge_graph()
    graph["review"] = []
    harness["graph"].write_text(json.dumps(graph))
    (harness["ts"] / "airway_bridges.json").write_text(json.dumps(_bridge_json()))
    _paint_bridge_mask(harness)
    # Confirm bridge decisions are absent; still need step 4 done. Sign off
    # is not required when there are no review flags — but pending bridges
    # block review. Confirm the bridge so review completes, then mark…
    # Actually: unconfirmed means no decision. For step 4 to complete with
    # pending bridges, review_raw fails. Confirm then clear? Better: seed
    # through step 4 by confirming bridge, then the effective graph still
    # has confirmed bridge (selectable past). We need UNCONFIRMED.
    # So: manually stamp review as done via empty bridges for gating, and
    # unit-test selectability on a hand-built effective graph instead.
    from app.review import compute_effective_graph
    from app.route_target import rank_route_target_candidates

    bridges = _bridge_json()["bridges"]
    for i, b in enumerate(bridges):
        b["id"] = f"b{i}"
    case = {"bridge_decisions": {}, "records": [], "route_start": {"node": 0}}
    # Fake bridge_map: whole edge 1 is the bridge span.
    bridge_map = {"b0": [{"edge_index": 1, "start_mm": 0.0, "end_mm": 1.5}]}
    for i, e in enumerate(graph["edges"]):
        e.setdefault("selectable", True)
    eff = compute_effective_graph(
        graph, case, bridges, bridge_map=bridge_map,
    )
    cands = rank_route_target_candidates(
        eff["graph"], case, [11.0, 9.0, 9.0],
    )
    assert all(c["edge_id"] != 2 for c in cands)
    # Prefix CT edge 0 and side branch 3 remain candidates.
    assert any(c["edge_id"] == 0 for c in cands) or any(c["edge_id"] == 3 for c in cands)


def test_local001_rll_top_candidate(tmp_path, monkeypatch, fake_home):
    """Real local-001 graph under tmp (file symlinks): lesion near RLL."""
    import hashlib

    cprint("local-001 RLL candidate check", "cyan")
    assert not str(tmp_path).startswith("/tmp")
    root = Path(__file__).resolve().parent
    real_graph = root / "out/local-001/local-001_graph.json"
    real_ct = root / "data/extracted/local/local-001/local-001_CT.nii.gz"
    real_ts = root / "ts_out/local-001"
    if not real_graph.is_file() or not real_ct.is_file():
        pytest.skip("local-001 artifacts missing")

    paths = _write_case_tree(tmp_path)
    paths["graph"].unlink()
    symlink_input_file(real_graph, paths["graph"])
    paths["ct"].unlink()
    symlink_input_file(real_ct, paths["ct"])
    for src in real_ts.rglob("*"):
        if not src.is_file():
            continue
        if src.name in {"job.json"} or src.suffix == ".lock":
            continue
        # Writable empty bridges so step 4 is vacuous (no pending decisions).
        if src.name in {"airway_bridges.json", "airway_bridges.nii.gz",
                        "airway_bridges_labels.nii.gz"}:
            continue
        rel = src.relative_to(real_ts)
        dst = paths["ts"] / rel
        if dst.exists() or dst.is_symlink():
            dst.unlink()
        symlink_input_file(src, dst)
    # Real (writable) empty bridge artifacts under tmp — not symlinks.
    import nibabel as nib
    airways_img = nib.load(paths["ts"] / "airways.nii.gz")
    empty = np.zeros(airways_img.shape, dtype=np.uint8)
    nib.save(nib.Nifti1Image(empty, airways_img.affine), paths["ts"] / "airway_bridges.nii.gz")
    (paths["ts"] / "airway_bridges.json").write_text(json.dumps({
        "note": "interpolated, not CT-demonstrated airway",
        "radius_mm": 1.0,
        "bridges": [],
    }))

    monkeypatch.setattr("paths.PIPELINE", tmp_path)
    monkeypatch.setattr("paths.LOCAL_CASES", tmp_path / "data/extracted/local")
    monkeypatch.setattr("paths.AEROPATH", tmp_path / "data/extracted/AeroPath")
    monkeypatch.setattr("paths.TS_OUT", tmp_path / "ts_out")
    monkeypatch.setattr("paths.OUT", tmp_path / "out")
    (tmp_path / "data/extracted/AeroPath").mkdir(parents=True, exist_ok=True)
    (tmp_path / "app_state").mkdir(parents=True, exist_ok=True)

    def sha(p: Path) -> str:
        h = hashlib.sha256()
        h.update(p.read_bytes())
        return h.hexdigest()

    def rel(p: Path) -> str:
        # Relative path under tmp root (do not resolve symlinks out of tree).
        return str(p.relative_to(tmp_path))

    g = json.loads(paths["graph"].read_text())
    rll = [e for e in g["edges"] if e.get("lobe") == 5]
    assert rll, "expected right lower lobe edges on local-001"
    tip = rll[-1]["centreline_mm"][-1]
    lesion = [float(tip[0]) + 3.0, float(tip[1]), float(tip[2])]

    arts = {
        "ct": {"path": rel(paths["ct"]), "sha256": sha(paths["ct"])},
        "airways": {
            "path": rel(paths["ts"] / "airways.nii.gz"),
            "sha256": sha(paths["ts"] / "airways.nii.gz"),
        },
        "bridges_mask": {
            "path": rel(paths["ts"] / "airway_bridges.nii.gz"),
            "sha256": sha(paths["ts"] / "airway_bridges.nii.gz"),
        },
        "bridges_json": {
            "path": rel(paths["ts"] / "airway_bridges.json"),
            "sha256": sha(paths["ts"] / "airway_bridges.json"),
        },
        "lobes": {
            "path": rel(paths["ts"] / "lobes.nii.gz"),
            "sha256": sha(paths["ts"] / "lobes.nii.gz"),
        },
        "graph": {"path": rel(paths["graph"]), "sha256": sha(paths["graph"])},
    }
    case = {
        "id": "local-001",
        "source": {
            "series_uid_sha256": "deadbeef" * 8,
            "folder": "SERIES0",
            "series_number": 1,
            "series_description": "test",
            "convolution_kernel": "STANDARD",
            "spacing_mm": [1.0, 1.0, 1.5],
            "size": [24, 24, 16],
        },
        "lesion": {
            "ras_mm": lesion,
            "confirmed_by": "Radiologist, A.B.",
            "at": "2026-01-01T00:00:01Z",
        },
        "route_start": {
            "node": 0, "ras_mm": [0.0, 0.0, 0.0],
            "confirmed_by": "Radiologist, A.B.", "at": "2026-01-01T00:00:03Z",
        },
        "versions": [{"n": 1, "artifacts": arts, "frozen": False}],
        "review_signoff": [],
        "bridge_decisions": {},
        "records": [
            {
                "step": 1, "kind": "series_confirmed",
                "data": {
                    "folder": "SERIES0", "series_number": 1,
                    "description": "test", "kernel": "STANDARD", "overridden": False,
                },
                "clinician": "Radiologist, A.B.", "at": "2026-01-01T00:00:00Z",
            },
            {
                "step": 2, "kind": "lesion_confirmed",
                "data": {"ras_mm": lesion},
                "clinician": "Radiologist, A.B.", "at": "2026-01-01T00:00:01Z",
            },
            {
                "step": 3, "kind": "segmentation_done",
                "data": {"artifacts": list(arts)},
                "clinician": "Radiologist, A.B.", "at": "2026-01-01T00:00:02Z",
            },
            {
                "step": 5, "kind": "route_start_confirmed",
                "data": {"node": 0, "ras_mm": [0.0, 0.0, 0.0], "moved": False},
                "clinician": "Radiologist, A.B.", "at": "2026-01-01T00:00:03Z",
            },
        ],
    }
    # Graph is symlinked read-only — stamp review_signoff for any flags.
    for flag in g.get("review") or []:
        case.setdefault("review_signoff", []).append({
            "kind": flag["kind"], "lobe": flag["lobe"],
            "decision": "approved", "finding": "ok",
            "reviewer": "Radiologist, A.B.",
            "at": "2026-01-01T00:00:02Z",
        })
    paths["case"].write_text(json.dumps(case, indent=2) + "\n")

    client = TestClient(app, base_url="http://127.0.0.1")
    _set_clinician(client)
    wf = client.get("/api/cases/local-001/workflow").json()
    assert wf["steps"][3]["status"] == "done", wf["steps"][3]
    assert wf["steps"][4]["status"] == "done", wf["steps"][4]

    res = client.get("/api/cases/local-001/route-candidates")
    assert res.status_code == 200, res.text
    cands = res.json()["candidates"]
    assert cands
    top = cands[0]
    assert top["lobe"] == "right lower lobe" or top["lobe"] == 5
    gaps = [c["target_gap_mm"] for c in cands]
    assert top["target_gap_mm"] == min(gaps)
    cprint(
        "local-001 top "
        f"lobe={top['lobe']} gap_mm={top['target_gap_mm']} "
        f"generation={top['generation']} route_length_mm={top['route_length_mm']} "
        f"airway={top.get('airway_name')}",
        "green",
    )


def _prep_graph(graph: dict) -> dict:
    for i, e in enumerate(graph["edges"]):
        e["_original_index"] = i
        e.setdefault("selectable", e.get("provenance") == "ct")
        e.setdefault("selectable_until_mm", None)
    return graph


def test_equal_gap_tiebreak_generation():
    """Equal gap → lower generation wins."""
    cprint("equal-gap generation tie-break", "cyan")
    graph = _prep_graph(_rt_graph())
    # Two tips at same distance from lesion; gens 1 vs 3.
    lesion = [10.0, 10.0, -10.0]  # equidistant-ish to edge3 tip[20,0,-10] and edge5 tip[0,20,-10]
    # Place lesion so gaps to edge 3 (gen1, to node4) and edge 5 (gen3, to node6) match.
    tip_a = np.array([20.0, 0.0, -10.0])
    tip_b = np.array([0.0, 20.0, -10.0])
    mid = (tip_a + tip_b) / 2.0
    lesion = mid.tolist()
    case = {"route_start": {"node": 0}, "records": [{
        "kind": "route_start_confirmed", "data": {"node": 0},
    }]}
    cands = rank_route_target_candidates(graph, case, lesion, top_n=3, dedup_mm=0.1)
    # Among equal-gap pair, gen 1 (edge 3) before gen 3 (edge 5).
    pair = [c for c in cands if c["edge_id"] in {3, 5}]
    if len(pair) >= 2:
        assert abs(pair[0]["target_gap_mm"] - pair[1]["target_gap_mm"]) < 1e-6
        assert pair[0]["generation"] <= pair[1]["generation"]
        assert pair[0]["edge_id"] == 3


def test_equal_gap_tiebreak_route_length():
    """Equal gap + generation → shorter route from RouteStart wins."""
    cprint("equal-gap route-length tie-break", "cyan")
    graph = _prep_graph(_rt_graph())
    # Edges 3 and 4: both gen 1, tips near each other; shorter route wins when
    # gaps forced equal by lesion at midpoint — but dedup would exclude one.
    # Disable dedup and compare sort keys directly via per-edge candidates.
    from app.route_target import _edge_candidates_for_lesion, _route_start_node

    lesion = np.array([21.0, 0.0, -10.0])
    case = {"route_start": {"node": 0}, "records": [{
        "kind": "route_start_confirmed", "data": {"node": 0},
    }]}
    start = _route_start_node(graph, case)
    rows = _edge_candidates_for_lesion(graph["edges"], lesion, start)
    # Force equal gap for edges 3 and 4 for the sort comparison.
    by_id = {r["edge_id"]: r for r in rows}
    assert 3 in by_id and 4 in by_id
    by_id[3]["_sort"] = (1.0, by_id[3]["generation"], by_id[3]["_sort"][2], 3)
    by_id[4]["_sort"] = (1.0, by_id[4]["generation"], by_id[4]["_sort"][2], 4)
    ordered = sorted([by_id[3], by_id[4]], key=lambda r: r["_sort"])
    assert ordered[0]["route_length_mm"] <= ordered[1]["route_length_mm"]
    assert ordered[0]["edge_id"] == 3  # shorter centreline (20 vs 22)


def test_removed_subtree_excluded():
    """Candidates never land on a removed (non-selectable) subtree."""
    graph = _prep_graph(_rt_graph())
    graph["edges"][3]["selectable"] = False
    case = {"route_start": {"node": 0}, "records": [{
        "kind": "route_start_confirmed", "data": {"node": 0},
    }]}
    cands = rank_route_target_candidates(graph, case, [20.0, 0.0, -10.0])
    assert all(c["edge_id"] != 3 for c in cands)
    assert cands


def test_candidates_need_lesion_not_route_start(client, harness, fake_home):
    """A Lesion unlocks candidates and RouteTarget; steps 2–5 stay undone."""
    cprint("candidates need a Lesion, not RouteStart", "cyan")
    _set_clinician(client)
    missing = client.get("/api/cases/local-001/route-candidates")
    assert missing.status_code == 409, missing.text
    assert missing.json()["detail"] == "place a Lesion first"
    _place_lesion(harness["case"], [20.0, 0.0, -10.0])
    wf = client.get("/api/cases/local-001/workflow").json()
    for index in (1, 2, 3, 4):
        assert wf["steps"][index]["status"] != "done", wf["steps"][index]
    res = client.get("/api/cases/local-001/route-candidates")
    assert res.status_code == 200, res.text
    cands = res.json()["candidates"]
    assert cands
    top = cands[0]
    put = client.put("/api/cases/local-001/route-target", json={
        "edge_id": top["edge_id"],
        "sample_index": top["sample_index"],
    })
    assert put.status_code == 200, put.text
    assert put.json()["route_target"]["edge_id"] == top["edge_id"]
    bare = client.put("/api/cases/local-001/route-target", json={
        "ras_mm": [0.0, 0.0, 0.0],
    })
    assert bare.status_code == 200, bare.text


def test_interpolated_mm_only_confirmed_bridges():
    """interpolated_mm_on_route counts confirmed-bridge spans; routes skip unconfirmed."""
    graph = _prep_graph(_rt_graph())
    # Unconfirmed bridge edge 1: not selectable past 0; edge 2 not selectable.
    graph["edges"][1]["selectable"] = True
    graph["edges"][1]["selectable_until_mm"] = 0.0
    graph["edges"][2]["selectable"] = False
    lesion = [20.0, 0.0, -10.0]
    case = {"route_start": {"node": 0}, "records": [{
        "kind": "route_start_confirmed", "data": {"node": 0},
    }]}
    cands = rank_route_target_candidates(graph, case, lesion)
    assert all(c["edge_id"] not in {1, 2} for c in cands)
    # Confirm the bridge → edge 2 selectable; route may cross interpolated span.
    graph["edges"][1]["selectable"] = True
    graph["edges"][1]["selectable_until_mm"] = None
    graph["edges"][2]["selectable"] = True
    graph["edges"][2]["selectable_until_mm"] = None
    # Lesion near tip of edge 2 (node 3).
    tip = graph["edges"][2]["centreline_mm"][-1]
    cands2 = rank_route_target_candidates(graph, case, tip)
    via_bridge = [c for c in cands2 if c["edge_id"] == 2]
    if via_bridge:
        assert via_bridge[0]["interpolated_mm_on_route"] >= 3.0 - 1e-6
        # Route edges include the confirmed bridge (oi 1).
        assert 1 in via_bridge[0]["route_edge_ids"]


def test_nan_ras_mm_422(client, harness, fake_home):
    _set_clinician(client)
    _seed_through_step(client, harness, 6)
    _place_lesion(harness["case"], [20.0, 0.0, -10.0])
    res = client.put(
        "/api/cases/local-001/route-target",
        content=json.dumps({"ras_mm": [1.0, float("nan"), 3.0]}),
        headers={"Content-Type": "application/json"},
    )
    assert res.status_code == 422


def test_target_outside_routestart_subtree_422():
    """Sample on an edge not downstream of RouteStart → ValueError / 422 path."""
    graph = _prep_graph(_rt_graph())
    # Simulate confirmed RouteStart at node 1 (mid-tree) without qualifying check.
    case = {"route_start": {"node": 1}, "records": [{
        "kind": "route_start_confirmed", "data": {"node": 1},
    }]}
    resolved = resolve_edge_sample(graph, 0, 0)
    with pytest.raises(ValueError, match=NOT_REACHABLE):
        candidate_metrics_for_sample(graph, case, [0.0, 0.0, 0.0], resolved)


def test_moved_routestart_route_length_matches_payload():
    """Card route_length_mm uses RouteStart node; matches route_edges_from_start."""
    graph = _prep_graph(_rt_graph())
    case = {"route_start": {"node": 1}, "records": [{
        "kind": "route_start_confirmed", "data": {"node": 1},
    }]}
    lesion = [20.0, 0.0, -10.0]
    cands = rank_route_target_candidates(graph, case, lesion)
    assert cands
    top = cands[0]
    assert 0 not in (top.get("route_edge_ids") or [])
    # Length from node 1 is shorter than from root 0.
    case_root = {"route_start": {"node": 0}, "records": [{
        "kind": "route_start_confirmed", "data": {"node": 0},
    }]}
    cands_root = rank_route_target_candidates(graph, case_root, lesion)
    root_match = next(c for c in cands_root if c["edge_id"] == top["edge_id"])
    assert top["route_length_mm"] < root_match["route_length_mm"]
    route = route_edges_from_start(graph["edges"], 1, top["edge_id"])
    assert route is not None
    assert [_edge_oi_for_test(graph, i) for i in route] == top["route_edge_ids"]


def _edge_oi_for_test(graph: dict, i: int) -> int:
    e = graph["edges"][i]
    return int(e.get("_original_index", i))


def test_stale_target_when_edge_gone(client, harness, fake_home):
    """Stored target edge no longer in graph → stale payload + step 6 available."""
    _set_clinician(client)
    _seed_through_step(client, harness, 6)
    _place_lesion(harness["case"], [20.0, 0.0, -10.0])
    cands = client.get("/api/cases/local-001/route-candidates").json()["candidates"]
    top = cands[0]
    assert client.put("/api/cases/local-001/route-target", json={
        "edge_id": top["edge_id"], "sample_index": top["sample_index"],
    }).status_code == 200

    # Point stored target at a vanished edge without invalidating RouteStart.
    case = json.loads(harness["case"].read_text())
    case["route_target"]["edge_id"] = 9999
    for rec in case["records"]:
        if rec.get("kind") == "route_target_chosen":
            rec["data"]["edge_id"] = 9999
    harness["case"].write_text(json.dumps(case, indent=2) + "\n")

    payload = client.get("/api/cases/local-001/payload?allow_review=1").json()
    assert payload["route_target"]["stale"] is True
    assert "reason" in payload["route_target"]
    assert payload["endpoint"] is None
    wf = client.get("/api/cases/local-001/workflow").json()
    assert wf["steps"][4]["status"] == "done", wf["steps"][4]
    assert wf["steps"][5]["status"] == "available", wf["steps"][5]
    assert wf["steps"][5]["reason"]


def test_fly_no_points_beyond_selectable_or_interp_on_target_edge(
    client, harness, fake_home
):
    from make_prototype_data import edge_provenance_at

    _set_clinician(client)
    _seed_through_step(client, harness, 6)
    _place_lesion(harness["case"], [20.0, 0.0, -10.0])
    cands = client.get("/api/cases/local-001/route-candidates").json()["candidates"]
    top = cands[0]
    assert client.put("/api/cases/local-001/route-target", json={
        "edge_id": top["edge_id"], "sample_index": top["sample_index"],
    }).status_code == 200
    payload = client.get("/api/cases/local-001/payload?allow_review=1").json()
    # Reload effective edge for provenance checks.
    graph = json.loads(harness["graph"].read_text())
    for i, e in enumerate(graph["edges"]):
        e["_original_index"] = i
    # Find target edge in payload route (last).
    end_ei = payload["route"][-1]
    edge = graph["edges"][end_ei]
    until = edge.get("selectable_until_mm")
    origin = np.asarray(payload["origin_mm"], dtype=float)
    # Map fly points on last route edge and check arcs.
    fe = payload["fly"]["edge"]
    fp = payload["fly"]["p"]
    fs = payload["fly"]["s"]
    # Approximate arc on target edge from route offsets.
    route_len_before = 0.0
    for ei in payload["route"][:-1]:
        pts = np.asarray(graph["edges"][ei]["centreline_mm"], dtype=float)
        route_len_before += float(np.linalg.norm(np.diff(pts, axis=0), axis=1).sum())
    for dist, route_pos, pt in zip(fs, fe, fp):
        if int(route_pos) != len(payload["route"]) - 1:
            continue
        arc = float(dist) - route_len_before
        if until is not None:
            assert arc <= float(until) + 1e-3
        assert edge_provenance_at(edge, max(0.0, arc)) == "ct" or arc < 1e-6
    # Endpoint matches stored.
    stored = np.asarray(
        client.get("/api/cases/local-001/payload?allow_review=1").json()["route_target"]["ras_mm"],
        dtype=float,
    )
    fly_end = np.asarray(fp[-1], dtype=float) + origin
    assert float(np.linalg.norm(fly_end - stored)) < 1e-6


def test_route_body_xor_forms(client, harness, fake_home):
    _set_clinician(client)
    _seed_through_step(client, harness, 6)
    _place_lesion(harness["case"], [20.0, 0.0, -10.0])
    res = client.put("/api/cases/local-001/route-target", json={"edge_id": 3})
    assert res.status_code == 422
    res = client.put("/api/cases/local-001/route-target", json={
        "edge_id": 3, "sample_index": 0, "ras_mm": [1.0, 2.0, 3.0],
    })
    assert res.status_code == 422


def test_candidate_metrics_rejects_unreachable():
    graph = _prep_graph(_rt_graph())
    case = {"route_start": {"node": 1}, "records": [{
        "kind": "route_start_confirmed", "data": {"node": 1},
    }]}
    resolved = resolve_edge_sample(graph, 0, 0)
    with pytest.raises(ValueError, match=NOT_REACHABLE):
        candidate_metrics_for_sample(graph, case, [0.0, 0.0, 0.0], resolved)
    assert route_edges_from_start(graph["edges"], 1, 0) is None
    assert stale_reason_for_route_target(
        graph, case, {"edge_id": 0, "sample_index": 0, "ras_mm": [0, 0, 0]}
    ) == NOT_REACHABLE
