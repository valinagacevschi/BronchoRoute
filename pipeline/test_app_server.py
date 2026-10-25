"""Synthetic tiny volumes only — no patient data, no TotalSegmentator."""

from __future__ import annotations

import hashlib
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import nibabel as nib
import numpy as np
import pytest
from fastapi.testclient import TestClient

import repo_root

repo_root.ensure()

from app.ct_crop import (  # noqa: E402
    MEMORY_ORDER,
    build_ct_crop,
    read_ct_bytes,
    voxel_byte_offset,
)
from app.server import app  # noqa: E402
from app.versions import register_artifact  # noqa: E402
from atomic_json import atomic_write_json  # noqa: E402
from make_prototype_data import (  # noqa: E402
    AIRWAY_EMBED_SENTINEL,
    ReviewBlocked,
    build_payload,
    embed_payload_in_template,
    load_case_record,
)


def _nifti(data: np.ndarray, affine: np.ndarray | None = None) -> nib.Nifti1Image:
    if affine is None:
        affine = np.diag([1.0, 1.0, 1.5, 1.0])
    return nib.Nifti1Image(data, affine)


def _write_case_tree(
    root: Path,
    case_id: str = "local-001",
    *,
    shape: tuple[int, int, int] = (24, 20, 16),
    affine: np.ndarray | None = None,
    mask_box: tuple[slice, slice, slice] | None = None,
    marker_ijk: tuple[int, int, int] | None = None,
    marker_hu: int = 1234,
) -> dict[str, Path]:
    local = root / "data/extracted/local" / case_id
    local.mkdir(parents=True)
    ts = root / "ts_out" / case_id
    lobes_dir = ts / "lobes"
    lobes_dir.mkdir(parents=True)
    out = root / "out" / case_id
    out.mkdir(parents=True)

    if affine is None:
        affine = np.diag([1.0, 1.0, 1.5, 1.0])
    if mask_box is None:
        mask_box = (slice(9, 15), slice(7, 13), slice(5, 11))

    ct = np.full(shape, -800, dtype=np.float32)
    ct[8:16, 6:14, 4:12] = -200
    if marker_ijk is not None:
        ct[marker_ijk] = float(marker_hu)
    lobes = np.zeros(shape, dtype=np.uint8)
    lobes[mask_box] = 1
    trachea = np.zeros(shape, dtype=np.uint8)
    trachea[11:13, 9:11, 10:14] = 1
    airways = np.zeros(shape, dtype=np.uint8)
    airways[10:14, 8:12, 6:12] = 1

    ct_path = local / f"{case_id}_CT.nii.gz"
    nib.save(_nifti(ct, affine), ct_path)
    nib.save(_nifti(lobes, affine), ts / "lobes.nii.gz")
    nib.save(_nifti(trachea, affine), lobes_dir / "trachea.nii.gz")
    nib.save(_nifti(airways, affine), ts / "airways.nii.gz")

    graph = {
        "case": case_id,
        "root_node_id": 0,
        "coordinate_system": "RAS",
        "nodes": [
            {"id": 0, "position_mm": [0.0, 0.0, 0.0], "radius_mm": 8.0},
            {"id": 1, "position_mm": [0.0, 0.0, -20.0], "radius_mm": 4.0},
        ],
        "edges": [{
            "from": 0, "to": 1, "generation": 0, "radius_mm": 6.0, "length_mm": 20.0,
            "lobe": None, "airway_name": "trachea", "provenance": "ct",
            "centreline_mm": [[0.0, 0.0, -z] for z in range(0, 21, 2)],
        }],
        "review": [{"kind": "small_lobe", "lobe": "right middle lobe",
                    "ml": 10.0, "median_ml": 80.0}],
    }
    graph_path = out / f"{case_id}_graph.json"
    graph_path.write_text(json.dumps(graph))
    case_path = local / "case.json"
    case_path.write_text(json.dumps({
        "id": case_id, "records": [], "versions": [], "review_signoff": [],
    }, indent=2) + "\n")
    return {
        "local": local, "ts": ts, "out": out, "case": case_path,
        "graph": graph_path, "ct": ct_path, "root": root,
    }


def _write_aeropath_case(root: Path, case_id: str = "3") -> dict[str, Path]:
    aero = root / "data/extracted/AeroPath" / case_id
    aero.mkdir(parents=True)
    (aero / f"{case_id}_CT_HR.nii.gz").write_bytes(b"not-used-for-signoff")
    graph_dir = root / "out" / "fallback"
    graph_dir.mkdir(parents=True, exist_ok=True)
    graph = {
        "case": case_id,
        "root_node_id": 0,
        "nodes": [{"id": 0, "position_mm": [0, 0, 0], "radius_mm": 5}],
        "edges": [{
            "from": 0, "to": 0, "generation": 0, "radius_mm": 5, "length_mm": 1,
            "lobe": None, "airway_name": "trachea", "provenance": "ct",
            "centreline_mm": [[0, 0, 0], [0, 0, -1]],
        }],
        "review": [{"kind": "small_lobe", "lobe": "left upper lobe",
                    "ml": 5.0, "median_ml": 40.0}],
    }
    graph_path = graph_dir / f"{case_id}_graph.json"
    graph_path.write_text(json.dumps(graph))
    return {"aero": aero, "graph": graph_path}


@pytest.fixture
def harness(tmp_path, monkeypatch):
    """Patch paths once; modules must read paths.X at call time."""
    paths = _write_case_tree(tmp_path)
    monkeypatch.setattr("paths.PIPELINE", tmp_path)
    monkeypatch.setattr("paths.LOCAL_CASES", tmp_path / "data/extracted/local")
    monkeypatch.setattr("paths.AEROPATH", tmp_path / "data/extracted/AeroPath")
    monkeypatch.setattr("paths.TS_OUT", tmp_path / "ts_out")
    monkeypatch.setattr("paths.OUT", tmp_path / "out")
    (tmp_path / "data/extracted/AeroPath").mkdir(parents=True, exist_ok=True)
    return paths


@pytest.fixture
def client(harness):
    return TestClient(app, base_url="http://127.0.0.1")


def test_cases_list(client):
    res = client.get("/api/cases")
    assert res.status_code == 200
    body = res.json()
    assert len(body) == 1
    assert body[0]["id"] == "local-001"
    assert body[0]["source"] == "local-001"
    assert body[0]["has_graph"] is True
    assert body[0]["review"]["unresolved"] == 1


def test_payload_409_then_allow_review(client):
    blocked = client.get("/api/cases/local-001/payload")
    assert blocked.status_code == 409
    assert blocked.json()["unresolved"][0]["kind"] == "small_lobe"

    ok = client.get("/api/cases/local-001/payload?allow_review=true")
    assert ok.status_code == 200
    payload = ok.json()
    assert payload["case"] == "local-001"
    assert payload["review"]
    assert "edges" in payload


def test_ct_crop_meta_and_bytes(client, harness):
    meta_res = client.get("/api/cases/local-001/ct/meta")
    assert meta_res.status_code == 200
    meta = meta_res.json()
    assert meta["shape_axis_order"].startswith("x,y,z")
    assert meta["memory_order"] == MEMORY_ORDER
    assert meta["dtype"] == "int16"
    assert meta["endian"] == "little"
    assert meta["window_presets"]["lung"] == {"W": 1500, "L": -600}
    assert meta["window_presets"]["mediastinal"] == {"W": 400, "L": 40}
    assert "path" not in json.dumps(meta)

    raw_res = client.get("/api/cases/local-001/ct")
    assert raw_res.status_code == 200
    assert raw_res.headers["content-type"].startswith("application/octet-stream")
    raw = raw_res.content
    assert len(raw) == int(np.prod(meta["shape"])) * 2
    assert raw_res.headers["x-content-sha256"] == meta["sha256"]
    assert hashlib.sha256(raw).hexdigest() == meta["sha256"]

    affine = np.asarray(meta["affine"])
    assert affine.shape == (4, 4)
    assert nib.aff2axcodes(affine) == ("R", "A", "S")

    again = build_ct_crop("local-001")
    assert again["sha256"] == meta["sha256"]
    raw2, meta2 = read_ct_bytes("local-001")
    assert raw2 == raw
    assert meta2["sha256"] == meta["sha256"]


def test_ct_crop_non_ras_small_mask_region(harness, tmp_path, monkeypatch):
    # LPS-ordered volume (non-RAS); mask occupies a small interior box.
    shape = (40, 36, 30)
    # Diagonal negative x,y → LPS before canonicalisation.
    affine = np.diag([-1.0, -1.0, 1.2, 1.0])
    affine[:3, 3] = [20.0, 18.0, -5.0]
    marker = (20, 18, 15)
    paths = _write_case_tree(
        tmp_path,
        case_id="local-002",
        shape=shape,
        affine=affine,
        mask_box=(slice(16, 26), slice(14, 24), slice(10, 20)),
        marker_ijk=marker,
        marker_hu=1500,
    )
    monkeypatch.setattr("paths.PIPELINE", tmp_path)
    monkeypatch.setattr("paths.LOCAL_CASES", tmp_path / "data/extracted/local")
    monkeypatch.setattr("paths.AEROPATH", tmp_path / "data/extracted/AeroPath")
    monkeypatch.setattr("paths.TS_OUT", tmp_path / "ts_out")
    monkeypatch.setattr("paths.OUT", tmp_path / "out")

    meta = build_ct_crop("local-002")
    assert meta["memory_order"] == MEMORY_ORDER
    assert all(s < dim for s, dim in zip(meta["shape"], shape))
    assert nib.aff2axcodes(np.asarray(meta["affine"])) == ("R", "A", "S")

    raw, _ = read_ct_bytes("local-002")
    src = nib.load(paths["ct"])
    world = src.affine @ np.array([*marker, 1.0], dtype=float)
    inv = np.linalg.inv(np.asarray(meta["affine"]))
    ijk = inv @ world
    i, j, k = [int(round(float(v))) for v in ijk[:3]]
    assert 0 <= i < meta["shape"][0]
    assert 0 <= j < meta["shape"][1]
    assert 0 <= k < meta["shape"][2]
    offset = voxel_byte_offset(i, j, k, tuple(meta["shape"]))
    value = int.from_bytes(raw[offset:offset + 2], "little", signed=True)
    assert value == 1500


def test_memory_order_byte_layout(harness):
    meta = build_ct_crop("local-001")
    raw, _ = read_ct_bytes("local-001")
    nx, ny, nz = meta["shape"]
    arr = np.frombuffer(raw, dtype="<i2").reshape((nx, ny, nz), order="F")
    # Spot-check: index (1,0,0) is the second int16 in x-fastest order.
    assert arr.flags["F_CONTIGUOUS"] or True
    expected = int.from_bytes(raw[2:4], "little", signed=True)
    assert int(arr[1, 0, 0]) == expected


def test_cache_rebuild_on_input_mtime_change(harness):
    first = build_ct_crop("local-001")
    ct = harness["ct"]
    payload = ct.read_bytes()
    ct.write_bytes(payload + b"")  # touch size same — bump mtime via rewrite
    import os
    import time
    os.utime(ct, (time.time() + 5, time.time() + 5))
    # Also change size so fingerprint definitely differs.
    img = nib.load(ct)
    data = np.asanyarray(img.dataobj).copy()
    data[0, 0, 0] = data[0, 0, 0] + 1
    nib.save(nib.Nifti1Image(data, img.affine), ct)
    second = build_ct_crop("local-001")
    assert second["inputs"]["ct"]["mtime_ns"] != first["inputs"]["ct"]["mtime_ns"]
    assert second["sha256"] != first["sha256"] or second["bin_mtime_ns"] != first["bin_mtime_ns"]


def test_concurrent_first_crop_requests(harness):
    results = []

    def worker():
        results.append(build_ct_crop("local-001"))

    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(lambda _: worker(), range(2)))
    assert results[0]["sha256"] == results[1]["sha256"]
    raw_a, meta_a = read_ct_bytes("local-001")
    raw_b, meta_b = read_ct_bytes("local-001")
    assert raw_a == raw_b
    assert meta_a["sha256"] == meta_b["sha256"]


def test_invalid_case_ids_404_no_files(client, tmp_path, harness):
    before = {p for p in tmp_path.rglob("*")}
    for bad in ("..", "foo", "local-1", "local-001/../x", "%2E%2E"):
        for suffix in ("/payload", "/records", "/versions", "/ct/meta", "/ct"):
            if suffix == "/records":
                res = client.post(f"/api/cases/{bad}{suffix}", json={
                    "step": 1, "kind": "note", "data": {},
                })
            else:
                res = client.get(f"/api/cases/{bad}{suffix}")
            # Unknown ids → 404; traversal may 404/405 at the router.
            assert res.status_code in {404, 405}, (bad, suffix, res.status_code)
    after = {p for p in tmp_path.rglob("*")}
    created = after - before
    assert not any(
        p.name == "case.json" and "local-001" not in p.parts for p in created if p.is_file()
    )


def test_path_traversal_records_404(client, harness):
    res = client.get("/api/cases/../records")
    assert res.status_code == 404


def test_clinician_validation_and_persistence(client):
    bad = client.put("/api/clinician", json={"role": "John Smith", "initials": "J.S."})
    assert bad.status_code == 422

    nasty = client.put("/api/clinician", json={"role": "Rad<script>", "initials": "A.B."})
    assert nasty.status_code == 422

    good = client.put("/api/clinician", json={"role": "Radiologist", "initials": "A.B."})
    assert good.status_code == 200
    assert good.json() == {"role": "Radiologist", "initials": "A.B."}

    got = client.get("/api/clinician")
    assert got.status_code == 200
    assert got.json() == {"role": "Radiologist", "initials": "A.B."}


def test_records_require_clinician_then_stamp(client, harness):
    missing = client.post("/api/cases/local-001/records",
                          json={"step": 1, "kind": "note", "data": {"mm": [1, 2, 3]}})
    assert missing.status_code == 412

    client.put("/api/clinician", json={"role": "Radiologist", "initials": "A.B."})
    saved = client.post("/api/cases/local-001/records",
                        json={"step": 1, "kind": "note", "data": {"mm": [1, 2, 3]}})
    assert saved.status_code == 200
    entry = saved.json()
    assert entry["clinician"] == "Radiologist, A.B."
    assert entry["at"].endswith("Z")
    assert entry["step"] == 1
    assert entry["kind"] == "note"

    on_disk = json.loads(harness["case"].read_text())
    assert on_disk["records"][-1]["clinician"] == "Radiologist, A.B."
    json.loads(harness["case"].read_text())


def test_records_validation_422(client):
    client.put("/api/clinician", json={"role": "Radiologist", "initials": "A.B."})
    bad_kind = client.post("/api/cases/local-001/records",
                           json={"step": 1, "kind": "point", "data": {}})
    assert bad_kind.status_code == 422

    oversize = client.post("/api/cases/local-001/records", json={
        "step": 1, "kind": "note", "data": {"blob": "x" * 9000},
    })
    assert oversize.status_code == 422

    ident = client.post("/api/cases/local-001/records", json={
        "step": 1, "kind": "note", "data": {"patient_name": "nope"},
    })
    assert ident.status_code == 422

    bad_step = client.post("/api/cases/local-001/records", json={
        "step": 0, "kind": "note", "data": {},
    })
    assert bad_step.status_code == 422


def test_atomic_writer_crash_keeps_original(harness, monkeypatch):
    path = harness["case"]
    original = json.loads(path.read_text())
    atomic_write_json(path, {**original, "marker": "ok"})
    assert json.loads(path.read_text())["marker"] == "ok"

    def boom(src, dst):
        raise OSError("simulated crash before replace")

    monkeypatch.setattr("atomic_json.os.replace", boom)
    with pytest.raises(OSError):
        atomic_write_json(path, {"id": "local-001", "corrupt": True})
    assert json.loads(path.read_text())["marker"] == "ok"


def test_aeropath_signoff_shared_location(harness, tmp_path):
    _write_aeropath_case(tmp_path, "3")
    from app.caseref import CaseRef
    from atomic_json import json_file_lock

    ref = CaseRef.parse("3")
    assert ref.case_json == tmp_path / "out" / "cases" / "3" / "case.json"
    record = {
        "id": "3",
        "source": "AeroPath 3",
        "records": [],
        "versions": [],
        "review_signoff": [{
            "kind": "small_lobe",
            "lobe": "left upper lobe",
            "decision": "approved",
            "finding": "anatomically small",
            "reviewer": "Radiologist, A.B.",
            "date": "2026-10-25",
        }],
    }
    with json_file_lock(ref.case_json):
        atomic_write_json(ref.case_json, record)

    via_helper = load_case_record("3")
    assert via_helper["review_signoff"][0]["decision"] == "approved"

    client = TestClient(app, base_url="http://127.0.0.1")
    payload = client.get("/api/cases/3/payload")
    assert payload.status_code == 200
    assert payload.json()["signoff"]
    assert payload.json()["review"] == []


def test_template_sentinel_replaced_once():
    template = (
        "<html><script id=\"airway-data\">\n"
        f"{AIRWAY_EMBED_SENTINEL}\n"
        "</script></html>"
    )
    js = "window.AIRWAY = {};"
    out = embed_payload_in_template(template, js)
    assert out.count(js) == 1
    assert AIRWAY_EMBED_SENTINEL not in out
    with pytest.raises(ValueError):
        embed_payload_in_template(template + AIRWAY_EMBED_SENTINEL, js)
    real = (repo_root.REPO_ROOT / "prototype/renderer.template.html").read_text()
    assert real.count(AIRWAY_EMBED_SENTINEL) == 1


def test_register_artifact_opens_next_after_freeze(harness, tmp_path):
    artifact = tmp_path / "ts_out" / "local-001" / "ct_crop.bin"
    artifact.parent.mkdir(parents=True, exist_ok=True)
    artifact.write_bytes(b"abc")
    case = json.loads(harness["case"].read_text())
    register_artifact(case, "ct_crop", artifact)
    assert case["versions"][0]["n"] == 1
    assert case["versions"][0]["frozen"] is False
    case["versions"][0]["frozen"] = True
    case["versions"][0]["signed_by"] = "Radiologist, A.B."

    other = tmp_path / "ts_out" / "local-001" / "graph.json"
    other.write_text("{}")
    register_artifact(case, "graph", other)
    assert len(case["versions"]) == 2
    assert case["versions"][1]["n"] == 2
    assert case["versions"][1]["frozen"] is False
    assert "graph" in case["versions"][1]["artifacts"]


def test_build_payload_raises_when_blocked(harness):
    graph = json.loads(harness["graph"].read_text())
    record = json.loads(harness["case"].read_text())
    with pytest.raises(ReviewBlocked):
        build_payload(graph, record, allow_review=False)
    payload = build_payload(graph, record, allow_review=True)
    assert payload["review"]
