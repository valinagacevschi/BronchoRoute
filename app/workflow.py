# Changes: Phase 4b fixes — step-4 completion times from review_signoff.at only;
# unmatched bridge reason on the review step.
"""Derived 8-step workflow state from case.json records and artifacts.

Phase 4a: ordering-aware derivation, segment artifacts, completion kinds.
Phase 4b: step 4 also requires no stale corrections; bridge ids via app.review.
"""

from __future__ import annotations

import json
from collections import deque
from datetime import datetime, timezone
from enum import IntEnum
from pathlib import Path
from typing import Any

from app.caseref import CaseRef
from app.cases import load_case_record, qualifying_route_start_terminals
from app.versions import sha256_file


class WorkflowStep(IntEnum):
    IMPORT = 1
    LESION = 2
    SEGMENT = 3
    REVIEW = 4
    ROUTE_START = 5
    ROUTE_TARGET = 6
    WALK = 7
    SIGNOFF = 8


STEP_NAMES: dict[int, str] = {
    WorkflowStep.IMPORT: "Import and choose reconstruction",
    WorkflowStep.LESION: "Mark the Lesion",
    WorkflowStep.SEGMENT: "Segment automatically",
    WorkflowStep.REVIEW: "Review and correct",
    WorkflowStep.ROUTE_START: "Confirm RouteStart",
    WorkflowStep.ROUTE_TARGET: "Choose RouteTarget",
    WorkflowStep.WALK: "Walk the route",
    WorkflowStep.SIGNOFF: "Sign off and freeze",
}

SEGMENT_ARTIFACT_NAMES = (
    "airways",
    "bridges_mask",
    "bridges_json",
    "lobes",
    "graph",
)

# Completion kinds that must go through their step endpoints, not POST /records.
WORKFLOW_COMPLETION_KINDS = frozenset({
    "series_confirmed",
    "segmentation_done",
    "route_start_confirmed",
    "lesion_confirmed",
    "route_target_chosen",
    "bifurcation_confirmed",
    "signoff",
    "bridge_decision",
})

AEROPATH_STEP1_REASON = "AeroPath dataset — no DICOM series choice"

Status = str  # done | available | blocked

_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


class WorkflowBlocked(Exception):
    """Action is out of order; maps to HTTP 409."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


def segment_artifact_paths(ref: CaseRef) -> dict[str, Path]:
    """Shared map of segment artifact names → on-disk paths (jobs + workflow)."""
    graph = ref.graph_path()
    return {
        "airways": ref.airways,
        "bridges_mask": ref.bridges,
        "bridges_json": ref.ts_out / "airway_bridges.json",
        "lobes": ref.lobes,
        "graph": graph if graph is not None else ref.ts_out / "__missing_graph__",
    }


def load_graph(case: dict | None = None, *, ref: CaseRef | None = None) -> dict:
    """Load the case graph JSON; raises ValueError on missing/invalid graph."""
    if ref is None:
        if case is None or not case.get("id"):
            raise ValueError("load_graph requires case id or CaseRef")
        ref = CaseRef.parse(str(case["id"]))
    graph_path = ref.graph_path()
    if graph_path is None:
        raise ValueError(f"no graph for case {ref.case_id}")
    try:
        graph = json.loads(graph_path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid graph for case {ref.case_id}") from exc
    if not isinstance(graph, dict):
        raise ValueError(f"invalid graph for case {ref.case_id}")
    return graph


def _parse_at(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _latest_kind(records: list[dict], kind: str) -> dict | None:
    for entry in reversed(records):
        if entry.get("kind") == kind:
            return entry
    return None


def _current_ct_sha256(ref: CaseRef) -> str | None:
    if not ref.ct.is_file():
        return None
    try:
        return sha256_file(ref.ct)
    except OSError:
        return None


def _segment_registered_for_ct(case: dict, ref: CaseRef) -> bool:
    """True when all five segment artifacts are registered for the current CT."""
    ct_sha = _current_ct_sha256(ref)
    if ct_sha is None:
        return False
    for version in case.get("versions") or []:
        arts = version.get("artifacts") or {}
        ct_art = arts.get("ct")
        if not isinstance(ct_art, dict) or ct_art.get("sha256") != ct_sha:
            continue
        if all(name in arts for name in SEGMENT_ARTIFACT_NAMES):
            return True
    return False


def _series_confirmed_raw(case: dict, ref: CaseRef) -> bool:
    if ref.kind == "aeropath":
        return True
    return _latest_kind(case.get("records") or [], "series_confirmed") is not None


def _lesion_confirmed_raw(case: dict) -> bool:
    lesion = case.get("lesion")
    if not isinstance(lesion, dict) or lesion.get("ras_mm") is None:
        return False
    records = case.get("records") or []
    for entry in reversed(records):
        if entry.get("kind") == "lesion_cleared":
            return False
        if entry.get("kind") == "lesion_confirmed":
            return True
    return True


def _review_raw(case: dict, ref: CaseRef) -> bool:
    if not _segment_registered_for_ct(case, ref):
        return False
    # Lazy import: app.review imports workflow helpers.
    from app.review import bridges_complete, has_stale_correction, review_flags_approved

    if not bridges_complete(case, ref):
        return False
    if not review_flags_approved(case, ref):
        return False
    if has_stale_correction(case, ref=ref):
        return False
    return True


def _review_available_reason(case: dict, ref: CaseRef) -> str | None:
    """Surface unmatched-bridge (or similar) reason while step 4 is available."""
    from app.review import review_block_reason

    return review_block_reason(case, ref)


def _route_start_still_qualifies(case: dict, ref: CaseRef) -> bool:
    rec = _latest_kind(case.get("records") or [], "route_start_confirmed")
    if rec is None:
        return False
    node = rec.get("data", {}).get("node")
    if node is None:
        return False
    try:
        graph = load_graph(case, ref=ref)
        terminals = qualifying_route_start_terminals(graph)
    except ValueError:
        return False
    return any(int(row["node"]) == int(node) for row in terminals)


def _route_target_chosen_raw(case: dict) -> bool:
    return _latest_kind(case.get("records") or [], "route_target_chosen") is not None


def _bifurcation_nodes_on_route(ref: CaseRef, case: dict) -> set[int]:
    """Nodes on the chosen route (from confirmed RouteStart → target) that bifurcate."""
    target_rec = _latest_kind(case.get("records") or [], "route_target_chosen")
    start_rec = _latest_kind(case.get("records") or [], "route_start_confirmed")
    if target_rec is None or start_rec is None:
        return set()
    target_node = target_rec.get("data", {}).get("node")
    start_node = start_rec.get("data", {}).get("node")
    if target_node is None or start_node is None:
        return set()
    try:
        graph = load_graph(case, ref=ref)
    except ValueError:
        return set()
    edges = graph.get("edges") or []
    children: dict[int, list[int]] = {}
    for i, e in enumerate(edges):
        children.setdefault(int(e["from"]), []).append(i)
    parent_edge: dict[int, int] = {}
    root = int(start_node)
    seen = {root}
    q = deque([root])
    while q:
        n = q.popleft()
        for ei in children.get(n, []):
            nxt = int(edges[ei]["to"])
            if nxt not in seen:
                seen.add(nxt)
                parent_edge[nxt] = ei
                q.append(nxt)
    route_nodes = [int(target_node)]
    cur = int(target_node)
    if cur not in seen and cur != root:
        return set()
    while cur in parent_edge:
        ei = parent_edge[cur]
        cur = int(edges[ei]["from"])
        route_nodes.append(cur)
    route_nodes.reverse()
    return {n for n in route_nodes if len(children.get(n, [])) > 1}


def _walk_raw(case: dict, ref: CaseRef) -> bool:
    if not _route_target_chosen_raw(case):
        return False
    needed = _bifurcation_nodes_on_route(ref, case)
    if not needed:
        return True
    confirmed: set[int] = set()
    for entry in case.get("records") or []:
        if entry.get("kind") != "bifurcation_confirmed":
            continue
        node = entry.get("data", {}).get("node")
        if node is not None:
            confirmed.add(int(node))
    return needed <= confirmed


def _signoff_raw(case: dict) -> bool:
    versions = case.get("versions") or []
    if not versions:
        return False
    return bool(versions[-1].get("frozen"))


def _completion_at(step: int, case: dict, ref: CaseRef) -> datetime | None:
    """Timestamp of the completing event for `step`, or None if incomplete."""
    records = case.get("records") or []
    if step == int(WorkflowStep.IMPORT):
        if ref.kind == "aeropath":
            return _EPOCH
        rec = _latest_kind(records, "series_confirmed")
        return _parse_at(rec.get("at")) if rec else None
    if step == int(WorkflowStep.LESION):
        if not _lesion_confirmed_raw(case):
            return None
        for entry in reversed(records):
            if entry.get("kind") == "lesion_cleared":
                return None
            if entry.get("kind") == "lesion_confirmed":
                return _parse_at(entry.get("at")) or _parse_at(
                    (case.get("lesion") or {}).get("at")
                )
        return _parse_at((case.get("lesion") or {}).get("at"))
    if step == int(WorkflowStep.SEGMENT):
        if not _segment_registered_for_ct(case, ref):
            return None
        rec = _latest_kind(records, "segmentation_done")
        return _parse_at(rec.get("at")) if rec else None
    if step == int(WorkflowStep.REVIEW):
        if not _review_raw(case, ref):
            return None
        # Vacuous review (no bridges / flags / corrections): inherit segment time.
        # Completion times: bridge/correction records + review_signoff[*].at only
        # (no client date, no note side-channel).
        times: list[datetime] = []
        for entry in records:
            if entry.get("kind") in {"bridge_decision", "correction"}:
                t = _parse_at(entry.get("at"))
                if t:
                    times.append(t)
        from review_signoff import iter_valid_signoffs

        for entry in iter_valid_signoffs(case.get("review_signoff") or []):
            t = _parse_at(entry.get("at"))
            if t:
                times.append(t)
        if times:
            return max(times)
        seg = _completion_at(int(WorkflowStep.SEGMENT), case, ref)
        return seg
    if step == int(WorkflowStep.ROUTE_START):
        if not _route_start_still_qualifies(case, ref):
            return None
        rec = _latest_kind(records, "route_start_confirmed")
        return _parse_at(rec.get("at")) if rec else None
    if step == int(WorkflowStep.ROUTE_TARGET):
        rec = _latest_kind(records, "route_target_chosen")
        return _parse_at(rec.get("at")) if rec else None
    if step == int(WorkflowStep.WALK):
        if not _walk_raw(case, ref):
            return None
        needed = _bifurcation_nodes_on_route(ref, case)
        if not needed:
            return _completion_at(int(WorkflowStep.ROUTE_TARGET), case, ref)
        times = []
        for entry in records:
            if entry.get("kind") != "bifurcation_confirmed":
                continue
            node = entry.get("data", {}).get("node")
            if node is not None and int(node) in needed:
                t = _parse_at(entry.get("at"))
                if t:
                    times.append(t)
        return max(times) if times else None
    if step == int(WorkflowStep.SIGNOFF):
        if not _signoff_raw(case):
            return None
        rec = _latest_kind(records, "signoff")
        if rec:
            return _parse_at(rec.get("at"))
        versions = case.get("versions") or []
        frozen_at = versions[-1].get("frozen_at") if versions else None
        return _parse_at(frozen_at)
    return None


def _raw_criteria(case: dict, ref: CaseRef) -> list[bool]:
    return [
        _series_confirmed_raw(case, ref),
        _lesion_confirmed_raw(case),
        _segment_registered_for_ct(case, ref),
        _review_raw(case, ref),
        _route_start_still_qualifies(case, ref),
        _route_target_chosen_raw(case),
        _walk_raw(case, ref),
        _signoff_raw(case),
    ]


def _ordered_done(case: dict, ref: CaseRef) -> tuple[list[bool], list[datetime | None]]:
    """Apply ordering: completion only counts if newer than all earlier completions."""
    raw = _raw_criteria(case, ref)
    done = [False] * 8
    ats: list[datetime | None] = [None] * 8
    for i in range(8):
        step = i + 1
        if not all(done[:i]):
            continue
        if not raw[i]:
            continue
        at = _completion_at(step, case, ref)
        if at is None:
            continue
        prior_ats = [t for t in ats[:i] if t is not None]
        if prior_ats:
            latest_prior = max(prior_ats)
            # Vacuous review/walk may inherit a prior completion timestamp.
            vacuous_inherit = step in {
                int(WorkflowStep.REVIEW),
                int(WorkflowStep.WALK),
            }
            if vacuous_inherit:
                if at < latest_prior:
                    continue
            elif at <= latest_prior:
                continue
        done[i] = True
        ats[i] = at
    return done, ats


def _block_reason(step: int, done: list[bool]) -> str:
    for i, ok in enumerate(done[: step - 1], start=1):
        if not ok:
            return f"step {i} ({STEP_NAMES[i]}) is not done"
    return f"step {step} is blocked"


def derive_workflow(case_id: str) -> dict[str, Any]:
    """Return per-step status for GET /api/cases/{id}/workflow."""
    ref = CaseRef.parse(case_id)
    case = load_case_record(ref)
    done, _ats = _ordered_done(case, ref)
    steps = []
    for step in range(1, 9):
        prior_ok = all(done[: step - 1])
        own = done[step - 1]
        if own and prior_ok:
            status: Status = "done"
            reason = None
            if step == 1 and ref.kind == "aeropath":
                reason = AEROPATH_STEP1_REASON
        elif prior_ok:
            status = "available"
            reason = None
            if step == int(WorkflowStep.REVIEW):
                reason = _review_available_reason(case, ref)
        else:
            status = "blocked"
            reason = _block_reason(step, done)
        steps.append({
            "step": step,
            "name": STEP_NAMES[step],
            "status": status,
            "reason": reason,
        })
    return {"case": case_id, "steps": steps}


def require_prior_steps(case_id: str, step: int) -> dict:
    """Raise WorkflowBlocked unless steps 1..step-1 are done. Returns case dict."""
    ref = CaseRef.parse(case_id)
    case = load_case_record(ref)
    done, _ = _ordered_done(case, ref)
    if not all(done[: step - 1]):
        raise WorkflowBlocked(_block_reason(step, done))
    return case


def step_status(case_id: str, step: int) -> Status:
    body = derive_workflow(case_id)
    for entry in body["steps"]:
        if entry["step"] == step:
            return entry["status"]
    raise ValueError(f"unknown step {step}")


__all__ = [
    "AEROPATH_STEP1_REASON",
    "SEGMENT_ARTIFACT_NAMES",
    "STEP_NAMES",
    "WORKFLOW_COMPLETION_KINDS",
    "WorkflowBlocked",
    "WorkflowStep",
    "derive_workflow",
    "load_graph",
    "require_prior_steps",
    "segment_artifact_paths",
    "step_status",
]
