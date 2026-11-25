"""CT evidence for synthetic airway bridges.

A bridge is an interpolated join, so the only independent check is whether the
CT shows air along the join. No labelled bridge set exists yet, so this is a
rule with bands, not a calibrated probability. Clear cases are decided
automatically; the band between the thresholds stays unconfirmed for a human.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import numpy as np
from atomic_json import atomic_write_json, json_file_lock

from app.caseref import CaseRef
from app.cases import _load_or_init_record
from app.ct_crop import read_ct_bytes
from app.review import DECISIONS, load_bridges_annotated
from app.versions import note_change
from app.workflow import WorkflowStep

AIR_HU = -700.0
SAMPLE_MM = 0.25
CONFIRM_MIN_AIR = 0.75
REJECT_MAX_AIR = 0.10
AUTO_CLINICIAN = "auto"


def _ct_crop(case_id: str) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    raw, meta = read_ct_bytes(case_id)
    shape = np.asarray(meta["shape"], dtype=int)
    ct = np.frombuffer(raw, dtype="<i2").reshape(tuple(shape), order="F")
    inverse = np.linalg.inv(np.asarray(meta["affine"], dtype=float))
    return ct, inverse, shape


def _air_stats(
    ct: np.ndarray, inverse: np.ndarray, shape: np.ndarray, start, end
) -> dict[str, Any] | None:
    a = np.asarray(start, dtype=float)
    b = np.asarray(end, dtype=float)
    length = float(np.linalg.norm(b - a))
    count = max(2, int(np.ceil(length / SAMPLE_MM)) + 1)
    t = np.linspace(0.0, 1.0, count)
    points = a[None, :] + (b - a)[None, :] * t[:, None]
    voxels = np.rint((inverse @ np.c_[points, np.ones(count)].T)[:3].T).astype(int)
    inside = np.all((voxels >= 0) & (voxels < shape), axis=1)
    hu = ct[tuple(voxels[inside].T)].astype(float)
    if not hu.size:
        return None
    return {
        "samples": int(hu.size),
        "air_fraction": round(float(np.mean(hu < AIR_HU)), 3),
        "median_hu": int(round(float(np.median(hu)))),
        "min_hu": int(round(float(hu.min()))),
    }


def score_bridge(bridge: dict, ct: np.ndarray, inverse: np.ndarray, shape: np.ndarray) -> dict[str, Any]:
    stats = _air_stats(ct, inverse, shape, bridge["tree_point_mm"], bridge["piece_point_mm"])
    row: dict[str, Any] = {
        "id": bridge["id"],
        "gap_mm": bridge.get("gap_mm"),
        "piece_volume_mm3": bridge.get("piece_volume_mm3"),
        "air_fraction": None,
        "median_hu": None,
        "min_hu": None,
        "samples": 0,
        "proposed_decision": None,
        "reason": "bridge is outside the CT crop",
    }
    if stats is None:
        return row
    air = stats["air_fraction"]
    row.update(stats)
    if air >= CONFIRM_MIN_AIR:
        row["proposed_decision"] = "confirmed"
        row["reason"] = f"air along {air:.0%} of the bridge"
    elif air <= REJECT_MAX_AIR:
        row["proposed_decision"] = "rejected"
        row["reason"] = f"air along only {air:.0%} of the bridge"
    else:
        row["reason"] = f"air along {air:.0%} of the bridge, between auto thresholds"
    return row


def bridge_evidence(case_id: str) -> list[dict[str, Any]]:
    """Score every bridge in a case against its CT. Empty when there are none."""
    ref = CaseRef.parse(case_id)
    bridges = load_bridges_annotated(ref)
    if not bridges:
        return []
    ct, inverse, shape = _ct_crop(case_id)
    return [score_bridge(bridge, ct, inverse, shape) for bridge in bridges]


def auto_decide_bridges(case_id: str, clinician: str = AUTO_CLINICIAN) -> dict[str, Any]:
    """Store evidence for every bridge and decide the clear ones.

    A decision already present is never overwritten, so a human or an earlier
    run always wins. The band between the thresholds stays unconfirmed.
    """
    evidence = bridge_evidence(case_id)
    if not evidence:
        return {"case": case_id, "evidence": [], "written": []}

    ref = CaseRef.parse(case_id)
    path = ref.case_json
    at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    written: list[str] = []
    with json_file_lock(path):
        record = _load_or_init_record(ref, path)
        evidence_map = record.setdefault("bridge_evidence", {})
        decisions = record.setdefault("bridge_decisions", {})
        for row in evidence:
            evidence_map[row["id"]] = row
            proposed = row["proposed_decision"]
            if proposed is None:
                continue
            existing = decisions.get(row["id"])
            if isinstance(existing, dict) and existing.get("decision") in DECISIONS:
                continue
            data = {
                "bridge_id": row["id"],
                "decision": proposed,
                "note": "auto: " + row["reason"],
                "source": "auto",
            }
            decisions[row["id"]] = {**data, "clinician": clinician, "at": at}
            record.setdefault("records", []).append({
                "step": int(WorkflowStep.REVIEW),
                "kind": "bridge_decision",
                "data": data,
                "clinician": clinician,
                "at": at,
            })
            written.append(row["id"])
        if written:
            note_change(record, "review")
        atomic_write_json(path, record)
    return {"case": case_id, "evidence": evidence, "written": written}
