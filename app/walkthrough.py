# Changes: Phase 4d review fixes — walk answers keyed by fork node id;
# route_hash includes ordered fork nodes; No stores return_to without popping
# route_target; uncertainty per bridge + open notes/corrections/stale;
# clinician-text checks on notes; pass record through (no disk re-read).
"""Phase 4d — Walk the route (workflow step 7).

Bifurcations are the payload forks for the active RouteStart→RouteTarget route.
Confirmations are stored under case.json["walkthrough"][route_hash][node_id]
so a changed route never reuses old Yes/No answers.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from atomic_json import atomic_write_json, json_file_lock
from termcolor import cprint

from app.cases import _load_or_init_record, _utc_now, case_payload, load_case_record
from app.caseref import CaseRef
from app.review import effective_for_case
from app.route_target import plan_route_target_for_payload
from app.versions import note_change, validate_clinician_text
from app.workflow import WorkflowBlocked, WorkflowStep, _block_reason, _ordered_done

RETURN_TO_STEPS = frozenset({
    int(WorkflowStep.REVIEW),
    int(WorkflowStep.ROUTE_TARGET),
})


def _edge_oi(edge: dict, idx: int) -> int:
    oi = edge.get("_original_index")
    return int(oi) if oi is not None else int(idx)


def compute_route_hash(
    edge_ids: list[int],
    sample_index: int,
    fork_node_ids: list[int] | None = None,
) -> str:
    """Stable fingerprint of the chosen route + target sample + fork nodes."""
    blob = json.dumps(
        {
            "edges": [int(e) for e in edge_ids],
            "sample": int(sample_index),
            "forks": [int(n) for n in (fork_node_ids or [])],
        },
        separators=(",", ":"),
    )
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def active_route_context(
    case_id: str,
    case: dict | None = None,
    *,
    payload: dict | None = None,
) -> dict[str, Any]:
    """Route plan + payload forks for the current confirmed RouteTarget.

    Raises ValueError when there is no active (non-stale) route or forks cannot
    be produced (fail closed — callers treat as step 7 not done).
    """
    ref = CaseRef.parse(case_id)
    record = case if case is not None else load_case_record(ref)
    eff = effective_for_case(case_id, record)
    plan = plan_route_target_for_payload(
        eff["graph"], record, record.get("route_target")
    )
    if not plan.get("active"):
        reason = plan.get("reason") or "no active route target"
        raise ValueError(reason)
    edges = list(eff["graph"].get("edges") or [])
    route_idx = list(plan["route"])
    edge_ids = [_edge_oi(edges[i], i) for i in route_idx]
    sample_index = int((record.get("route_target") or {}).get("sample_index") or 0)
    # Same forks the payload draws (allow_review so gated graphs still walk).
    pl = payload if payload is not None else case_payload(case_id, allow_review=True)
    if not isinstance(pl, dict):
        raise ValueError("route payload unavailable")
    forks = list(pl.get("forks") or [])
    fork_node_ids = [int(f["node"]) for f in forks if f.get("node") is not None]
    route_hash = compute_route_hash(edge_ids, sample_index, fork_node_ids)
    return {
        "route_hash": route_hash,
        "edge_ids": edge_ids,
        "route_indices": route_idx,
        "sample_index": sample_index,
        "fork_node_ids": fork_node_ids,
        "forks": forks,
        "payload": pl,
        "graph": eff["graph"],
        "plan": plan,
    }


def walkthrough_entries_for_hash(case: dict, route_hash: str) -> dict[str, dict]:
    store = case.get("walkthrough")
    if not isinstance(store, dict):
        return {}
    bucket = store.get(route_hash)
    if not isinstance(bucket, dict):
        return {}
    return {str(k): v for k, v in bucket.items() if isinstance(v, dict)}


def walk_complete(case_id: str, case: dict | None = None) -> bool:
    """True when every fork on the active route has confirmed=True (vacuous OK)."""
    ref = CaseRef.parse(case_id)
    record = case if case is not None else load_case_record(ref)
    if pending_return_step(record) is not None:
        return False
    try:
        ctx = active_route_context(case_id, record)
    except (ValueError, FileNotFoundError, KeyError):
        return False
    forks = ctx["forks"]
    if not forks:
        return True
    entries = walkthrough_entries_for_hash(record, ctx["route_hash"])
    for fork in forks:
        node_key = str(int(fork["node"]))
        entry = entries.get(node_key)
        if not entry or entry.get("confirmed") is not True:
            return False
    return True


def pending_return_step(case: dict) -> int | None:
    pending = case.get("pending_return")
    if not isinstance(pending, dict):
        return None
    step = pending.get("step")
    try:
        step_i = int(step)
    except (TypeError, ValueError):
        return None
    return step_i if step_i in RETURN_TO_STEPS else None


def clear_pending_return(case: dict) -> None:
    case.pop("pending_return", None)


def _fork_ras_mm(fork: dict, payload: dict) -> list[float]:
    """Absolute RAS mm at the fork (payload fly is relative to origin)."""
    origin = payload.get("origin_mm") or [0.0, 0.0, 0.0]
    s_vals = (payload.get("fly") or {}).get("s") or []
    pts = (payload.get("fly") or {}).get("p") or []
    if not s_vals or not pts:
        node = fork.get("node")
        nodes = payload.get("nodes") or {}
        rel = (nodes.get(str(node)) or {}).get("p")
        if rel is None:
            return [0.0, 0.0, 0.0]
        return [float(rel[i]) + float(origin[i]) for i in range(3)]
    target_s = float(fork.get("s") or 0.0)
    best = min(range(len(s_vals)), key=lambda i: abs(float(s_vals[i]) - target_s))
    rel = pts[best]
    return [float(rel[i]) + float(origin[i]) for i in range(3)]


def _branch_name(fork: dict) -> str | None:
    if fork.get("name"):
        return fork["name"]
    chosen = next((c for c in fork.get("children") or [] if c.get("on_route")), None)
    if chosen:
        return chosen.get("name")
    return None


def set_walkthrough_decision(
    case_id: str,
    fork_index: int,
    clinician: str,
    *,
    confirmed: bool,
    note: str | None = None,
    return_to: int | None = None,
) -> dict:
    """Record Yes/No at a fork; No requires return_to in {4, 6}. Does not pop state."""
    if fork_index < 0:
        raise ValueError("fork_index must be >= 0")
    if confirmed:
        if return_to is not None:
            raise ValueError("return_to only applies when confirmed is false")
    else:
        if return_to is None or int(return_to) not in RETURN_TO_STEPS:
            raise ValueError("No requires return_to of 4 (review) or 6 (route target)")
        return_to = int(return_to)

    note_text = (note or "").strip()
    if note_text:
        note_text = validate_clinician_text(note_text, field="note")
        if len(note_text) > 500:
            raise ValueError("note exceeds 500 characters")
    else:
        note_text = ""

    ref = CaseRef.parse(case_id)
    path = ref.case_json
    at = _utc_now()
    with json_file_lock(path):
        record = _load_or_init_record(ref, path)
        done, _ = _ordered_done(record, ref)
        if not all(done[: int(WorkflowStep.WALK) - 1]):
            raise WorkflowBlocked(_block_reason(int(WorkflowStep.WALK), done))

        ctx = active_route_context(case_id, record)
        forks = ctx["forks"]
        if fork_index >= len(forks):
            raise ValueError(
                f"fork_index {fork_index} out of range (route has {len(forks)} forks)"
            )
        fork = forks[fork_index]
        node_id = int(fork["node"])
        route_hash = ctx["route_hash"]
        entry = {
            "node": node_id,
            "ras_mm": _fork_ras_mm(fork, ctx["payload"]),
            "branch_name": _branch_name(fork),
            "clock": fork.get("clock"),
            "confirmed": bool(confirmed),
            "note": note_text or None,
            "clinician": clinician,
            "at": at,
            "s_mm": fork.get("s"),
            "fork": fork.get("fork"),
            "fork_index": fork_index,
        }
        if not confirmed:
            entry["return_to"] = return_to

        note_change(record, f"walkthrough:node:{node_id}")
        store = record.setdefault("walkthrough", {})
        bucket = store.setdefault(route_hash, {})
        bucket[str(node_id)] = entry

        rec_data: dict[str, Any] = {
            "node": entry["node"],
            "ras_mm": entry["ras_mm"],
            "branch_name": entry["branch_name"],
            "clock": entry["clock"],
            "confirmed": entry["confirmed"],
            "note": entry["note"],
            "route_hash": route_hash,
            "fork_index": fork_index,
        }
        if not confirmed:
            rec_data["return_to"] = return_to
            # Derived reopen: keep route_target; store return_to for workflow.
            record["pending_return"] = {
                "step": return_to,
                "at": at,
                "node": node_id,
                "fork_index": fork_index,
                "route_hash": route_hash,
            }

        record_entry = {
            "step": int(WorkflowStep.WALK),
            "kind": "bifurcation_confirmed",
            "data": rec_data,
            "clinician": clinician,
            "at": at,
        }
        record.setdefault("records", []).append(record_entry)
        atomic_write_json(path, record)
        cprint(
            f"walkthrough node={node_id} confirmed={confirmed} "
            f"hash={route_hash[:8]}…",
            "green",
        )
        return {
            "walkthrough": entry,
            "route_hash": route_hash,
            "record": record_entry,
            "pending_return": record.get("pending_return"),
        }


def tracing_sequence(case_id: str, case: dict | None = None) -> dict[str, Any]:
    """Ordered confirmed forks for the active route (+ plain-text card)."""
    ref = CaseRef.parse(case_id)
    record = case if case is not None else load_case_record(ref)
    try:
        ctx = active_route_context(case_id, record)
    except (ValueError, FileNotFoundError, KeyError) as exc:
        return {
            "route_hash": None,
            "forks": [],
            "text": "",
            "reason": str(exc),
        }
    entries = walkthrough_entries_for_hash(record, ctx["route_hash"])
    ordered: list[dict] = []
    lines: list[str] = []
    for i, fork in enumerate(ctx["forks"]):
        node_key = str(int(fork["node"]))
        stored = entries.get(node_key)
        if not stored or stored.get("confirmed") is not True:
            continue
        row = {
            "fork_index": i,
            "fork": fork.get("fork") or (i + 1),
            "node": stored.get("node", fork.get("node")),
            "s_mm": stored.get("s_mm", fork.get("s")),
            "branch_name": stored.get("branch_name") or _branch_name(fork),
            "clock": stored.get("clock", fork.get("clock")),
            "note": stored.get("note"),
            "clinician": stored.get("clinician"),
            "at": stored.get("at"),
            "ras_mm": stored.get("ras_mm"),
        }
        ordered.append(row)
        clock = row["clock"]
        clock_txt = (
            f"{clock} o'clock" if clock is not None else "straight ahead"
        )
        name = row["branch_name"] or "unnamed"
        s_mm = row["s_mm"]
        try:
            s_txt = f"{float(s_mm):.1f}"
        except (TypeError, ValueError):
            s_txt = "?"
        lines.append(f"{len(ordered)}. at {s_txt} mm: {name}, {clock_txt}")
    return {
        "route_hash": ctx["route_hash"],
        "forks": ordered,
        "text": "\n".join(lines),
    }


def _confirmed_bridges_on_route(
    case_id: str, record: dict, ctx: dict
) -> list[dict]:
    """Per confirmed bridge intersecting the route: bridge id + interpolated mm."""
    from app.review import load_bridges_annotated, map_all_bridges

    ref = CaseRef.parse(case_id)
    decisions = record.get("bridge_decisions") or {}
    if not isinstance(decisions, dict):
        return []
    try:
        bridges = load_bridges_annotated(ref)
    except (OSError, ValueError, FileNotFoundError):
        bridges = []
    if not bridges:
        return []
    try:
        bridge_map = map_all_bridges(ctx["graph"], bridges, ref)
    except (OSError, ValueError, FileNotFoundError, KeyError):
        bridge_map = {}

    edges = list(ctx["graph"].get("edges") or [])
    route = list(ctx["route_indices"])
    end_edge = int(ctx["plan"]["end_edge"])
    end_arc = float(ctx["plan"]["end_arc_mm"])
    route_ois = {_edge_oi(edges[ei], ei) for ei in route}

    items: list[dict] = []
    for bridge in bridges:
        bid = bridge.get("id")
        if not bid:
            continue
        dec = decisions.get(bid)
        if not isinstance(dec, dict) or dec.get("decision") != "confirmed":
            continue
        spans = bridge_map.get(bid) or []
        mm_total = 0.0
        on_route = False
        for span in spans:
            ei = int(span.get("edge_index", -1))
            if ei < 0 or ei >= len(edges):
                continue
            oi = _edge_oi(edges[ei], ei)
            # Map to effective route index if present.
            if oi not in route_ois and ei not in route:
                # Check via original index on route edges.
                matched = False
                for ri in route:
                    if _edge_oi(edges[ri], ri) == oi or ri == ei:
                        matched = True
                        ei = ri
                        break
                if not matched:
                    continue
            on_route = True
            start = float(span.get("start_mm") or 0.0)
            end = float(span.get("end_mm") or start)
            limit = end_arc if ei == end_edge else float("inf")
            if start >= limit:
                continue
            mm_total += max(0.0, min(end, limit) - start)
        if not on_route:
            continue
        mm_r = round(mm_total, 1)
        items.append({
            "kind": "interpolated_bridge",
            "bridge_id": bid,
            "mm": mm_r,
            "text": (
                f"Confirmed bridge {bid}: {mm_r:.1f} mm interpolated on route"
            ),
        })
    # Fallback: edge-level interpolated spans when no bridge map hits.
    if not items:
        total_interp = 0.0
        for ei in route:
            edge = edges[ei]
            spans = edge.get("interpolated_spans_mm") or []
            limit = end_arc if ei == end_edge else float("inf")
            edge_mm = 0.0
            for span in spans:
                if not isinstance(span, (list, tuple)) or len(span) < 2:
                    continue
                start, end = float(span[0]), float(span[1])
                if start >= limit:
                    continue
                edge_mm += max(0.0, min(end, limit) - start)
            if edge_mm > 0:
                total_interp += edge_mm
                mm_r = round(edge_mm, 1)
                oid = _edge_oi(edge, ei)
                items.append({
                    "kind": "interpolated_bridge",
                    "bridge_id": f"edge-{oid}",
                    "mm": mm_r,
                    "text": (
                        f"Confirmed bridge edge-{oid}: "
                        f"{mm_r:.1f} mm interpolated on route"
                    ),
                })
            if ei == end_edge:
                break
        if total_interp > 0 and len(items) > 1:
            # Keep per-bridge items; wording already matches count.
            pass
    return items


def compute_uncertainty(case_id: str, case: dict | None = None) -> list[dict]:
    """Server-computed uncertainty list shown before sign-off."""
    ref = CaseRef.parse(case_id)
    record = case if case is not None else load_case_record(ref)
    items: list[dict] = []

    rt = record.get("route_target")
    if isinstance(rt, dict) and rt.get("target_gap_mm") is not None:
        gap = round(float(rt["target_gap_mm"]), 1)
        inside = bool(rt.get("target_inside_airway"))
        items.append({
            "kind": "target_gap",
            "mm": gap,
            "inside_airway": inside,
            "text": (
                f"Target gap {gap:.1f} mm"
                + (" (inside airway)" if inside else " (beyond last CT-seen airway)")
            ),
        })

    try:
        ctx = active_route_context(case_id, record)
        items.extend(_confirmed_bridges_on_route(case_id, record, ctx))
        # Walk "No" notes + fork notes on the active route.
        entries = walkthrough_entries_for_hash(record, ctx["route_hash"])
        for node_key, entry in entries.items():
            note = entry.get("note")
            if not note:
                continue
            if entry.get("confirmed") is False:
                items.append({
                    "kind": "walk_no_note",
                    "node": entry.get("node", node_key),
                    "text": f"Walk-through No note (node {node_key}): {note}",
                    "at": entry.get("at"),
                    "clinician": entry.get("clinician"),
                })
            else:
                items.append({
                    "kind": "fork_note",
                    "node": entry.get("node", node_key),
                    "text": f"Fork note (node {node_key}): {note}",
                    "at": entry.get("at"),
                    "clinician": entry.get("clinician"),
                })
    except (ValueError, FileNotFoundError, KeyError):
        pass

    for rec in record.get("records") or []:
        if not isinstance(rec, dict) or rec.get("kind") != "note":
            continue
        data = rec.get("data") or {}
        text = data.get("text") or data.get("note") or ""
        if not text:
            continue
        items.append({
            "kind": "open_note",
            "text": str(text)[:500],
            "at": rec.get("at"),
            "clinician": rec.get("clinician"),
        })

    for corr in record.get("corrections") or []:
        if not isinstance(corr, dict) or corr.get("undone"):
            continue
        status = corr.get("status")
        note = corr.get("note") or corr.get("reason") or ""
        cid = corr.get("id") or corr.get("correction_id") or "?"
        if status == "stale":
            items.append({
                "kind": "stale_correction",
                "correction_id": cid,
                "text": f"Stale correction {cid}" + (f": {note}" if note else ""),
            })
        else:
            items.append({
                "kind": "manual_correction",
                "correction_id": cid,
                "text": f"Manual correction {cid}" + (f": {note}" if note else ""),
            })

    from review_signoff import iter_valid_signoffs

    for so in iter_valid_signoffs(record.get("review_signoff") or []):
        items.append({
            "kind": "review_signoff",
            "decision": so.get("decision"),
            "lobe": so.get("lobe"),
            "finding_kind": so.get("kind"),
            "text": (
                f"Review {so.get('decision')}: {so.get('kind')} / {so.get('lobe')}"
            ),
            "at": so.get("at") or so.get("date"),
            "reviewer": so.get("reviewer"),
        })

    return items


__all__ = [
    "RETURN_TO_STEPS",
    "active_route_context",
    "clear_pending_return",
    "compute_route_hash",
    "compute_uncertainty",
    "pending_return_step",
    "set_walkthrough_decision",
    "tracing_sequence",
    "walk_complete",
    "walkthrough_entries_for_hash",
]
