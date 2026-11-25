"""Printable Case report from live case data.

The version report (`app.versions.render_version_report`) renders a frozen
snapshot only. This report covers the current Case: the Lesion, Lymph-node
marks, and the active RouteTarget, so a clinician can save the plan to PDF
before any sign-off.
"""

from __future__ import annotations

from datetime import datetime, timezone

from app.caseref import CaseRef
from app.cases import (
    ReviewBlocked,
    UnknownCase,
    case_payload,
    lesion_entries,
    load_case_record,
)
from app.versions import LIMITATIONS_LIST, _esc, _fmt_mm, _fmt_ras


def _series_field(record: dict, key: str) -> object:
    source = record.get("source")
    if isinstance(source, dict) and source.get(key) is not None:
        return source.get(key)
    for rec in reversed(record.get("records") or []):
        if not isinstance(rec, dict) or rec.get("kind") != "series_confirmed":
            continue
        data = rec.get("data") if isinstance(rec.get("data"), dict) else {}
        if data.get(key) is not None:
            return data.get(key)
    return None


def _route_summary(case: dict, payload: dict) -> dict:
    target = payload.get("route_target")
    if not isinstance(target, dict):
        target = case.get("route_target") if isinstance(case.get("route_target"), dict) else {}
    gap = target.get("target_gap_mm")
    if gap is None:
        gap = payload.get("target_gap_mm")
    inside = target.get("target_inside_airway")
    if inside is None:
        inside = payload.get("target_inside_airway")
    return {
        "present": bool(target),
        "edge": target.get("edge_id"),
        "gap": gap,
        "inside": inside,
        "generation": target.get("generation"),
        "length": target.get("route_length_mm"),
        "lobe": target.get("lobe"),
        "airway": target.get("airway_name"),
        "poi_kind": target.get("poi_kind") or payload.get("poi_kind"),
        "stale": bool(target.get("stale")),
        "reason": target.get("reason"),
    }


def render_case_report(case_id: str, case: dict | None = None) -> str:
    ref = CaseRef.parse(case_id)
    case = case if isinstance(case, dict) else load_case_record(ref)
    try:
        payload = case_payload(case_id, allow_review=True)
    except (ReviewBlocked, UnknownCase, FileNotFoundError, ValueError):
        payload = {}

    lesion = case.get("lesion") if isinstance(case.get("lesion"), dict) else {}
    lesions = lesion_entries(case)
    lesion_rows = []
    for index, item in enumerate(lesions, 1):
        if not isinstance(item, dict):
            continue
        active = item is lesion or (
            item.get("id") is not None and item.get("id") == lesion.get("id")
        )
        lesion_rows.append(
            "<tr>"
            f"<td>{index}</td>"
            f"<td>{_esc(_fmt_ras(item.get('ras_mm')))}</td>"
            f"<td>{_esc(_fmt_mm(item.get('diameter_mm')))} mm</td>"
            f"<td>{'active' if active else '—'}</td>"
            "</tr>"
        )
    lesions_html = "".join(lesion_rows)
    lesions_table = (
        "<table><tr><th>#</th><th>RAS mm</th><th>Diameter</th><th>Route</th></tr>"
        + lesions_html
        + "</table>"
        if lesions_html else "<p>No Lesion.</p>"
    )
    marks = case.get("node_marks") if isinstance(case.get("node_marks"), list) else []
    route = _route_summary(case, payload)
    generated = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    spacing = _series_field(case, "spacing_mm")
    if isinstance(spacing, (list, tuple)):
        spacing_text = " × ".join(_fmt_mm(v) for v in spacing)
    else:
        spacing_text = _fmt_mm(spacing) if spacing is not None else "—"

    node_rows = []
    for mark in marks:
        if not isinstance(mark, dict):
            continue
        node_id = mark.get("id")
        active = route.get("poi_kind") == f"node:{node_id}"
        node_rows.append(
            "<tr>"
            f"<td>{_esc(mark.get('label') or f'Lymph node {node_id}')}</td>"
            f"<td>{_esc(_fmt_mm(mark.get('diameter_mm')))} mm</td>"
            f"<td>{_esc(_fmt_ras(mark.get('ras_mm')))}</td>"
            f"<td>{'active target' if active else '—'}</td>"
            "</tr>"
        )
    nodes_html = (
        "<table><tr><th>Mark</th><th>Diameter</th><th>RAS mm</th><th>Route</th></tr>"
        + "".join(node_rows)
        + "</table>"
        if node_rows else "<p>No Lymph-node marks.</p>"
    )

    review = payload.get("review") if isinstance(payload.get("review"), list) else []
    review_items = "".join(
        f"<li>{_esc(item.get('kind'))} · {_esc(item.get('lobe'))}</li>"
        for item in review
        if isinstance(item, dict)
    ) or "<li>None open</li>"

    limitations = "".join(f"<li>{_esc(line)}</li>" for line in LIMITATIONS_LIST)
    gap_text = _fmt_mm(route["gap"])
    gap_note = ""
    if route["gap"] is not None:
        gap_note = " · inside airway" if route["inside"] else " · beyond shown airway"

    poi_label = "Lesion"
    if isinstance(route.get("poi_kind"), str) and route["poi_kind"].startswith("node:"):
        node_id = route["poi_kind"].split(":", 1)[1]
        poi_label = f"Lymph node {node_id}"
        for mark in marks:
            if isinstance(mark, dict) and str(mark.get("id")) == node_id:
                poi_label = str(mark.get("label") or poi_label)
                break

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>BronchoRoute report — {_esc(case_id)}</title>
<style>
  @page {{ size: A4; margin: 18mm; }}
  body {{
    margin: 0 auto; padding: 24px; max-width: 720px; width: 720px;
    font: 13px/1.45 ui-sans-serif, system-ui, sans-serif; color: #17242B;
  }}
  h1 {{ font-size: 20px; margin: 0 0 4px; }}
  h2 {{ font-size: 14px; letter-spacing: .06em; text-transform: uppercase;
       color: #5D7280; margin: 22px 0 8px; border-bottom: 1px solid #C7D3DA;
       padding-bottom: 4px; }}
  table {{ width: 100%; border-collapse: collapse; font-size: 12px; }}
  th, td {{ border-bottom: 1px solid #C7D3DA; padding: 6px 8px; text-align: left;
            vertical-align: top; word-break: break-word; }}
  th {{ color: #5D7280; font-weight: 600; }}
  ul {{ margin: 0; padding-left: 1.2rem; }}
  .meta {{ color: #5D7280; font-size: 12px; margin-bottom: 16px; }}
  .foot {{ margin-top: 28px; color: #5D7280; font-size: 11px; }}
  @media print {{
    body {{ max-width: none; width: auto; padding: 0; }}
    a {{ color: inherit; text-decoration: none; }}
  }}
</style>
</head>
<body>
  <h1>BronchoRoute — case report</h1>
  <p class="meta">Case {_esc(case_id)} · generated {_esc(generated)}</p>

  <h2>Source series</h2>
  <table>
    <tr><th>Description</th><td>{_esc(_series_field(case, "description") or _series_field(case, "series_description") or "—")}</td></tr>
    <tr><th>Kernel</th><td>{_esc(_series_field(case, "kernel") or _series_field(case, "convolution_kernel") or "—")}</td></tr>
    <tr><th>Spacing mm</th><td>{_esc(spacing_text)}</td></tr>
  </table>

  <h2>Lesions</h2>
  {lesions_table}

  <h2>Lymph-node marks</h2>
  {nodes_html}

  <h2>Route</h2>
  <table>
    <tr><th>Target point</th><td>{_esc(poi_label)}</td></tr>
    <tr><th>RouteTarget edge</th><td>{_esc(route["edge"] if route["present"] else "—")}</td></tr>
    <tr><th>Target gap</th><td>{_esc(gap_text)} mm{_esc(gap_note)}</td></tr>
    <tr><th>Generation</th><td>{_esc(route["generation"] if route["generation"] is not None else "—")}</td></tr>
    <tr><th>Route length</th><td>{_esc(_fmt_mm(route["length"]))} mm</td></tr>
    <tr><th>Lobe</th><td>{_esc(route["lobe"] or "—")}</td></tr>
    <tr><th>Airway</th><td>{_esc(route["airway"] or "—")}</td></tr>
  </table>
  {"<p>" + _esc(route["reason"] or "RouteTarget is stale.") + "</p>" if route["stale"] else ""}

  <h2>Open review flags</h2>
  <ul>{review_items}</ul>

  <h2>Limitations</h2>
  <ul>{limitations}</ul>

  <p class="foot">Simulated · CT-derived · research use. Not a medical device.
  Not for diagnosis or treatment decisions.</p>
</body>
</html>
"""


__all__ = ["render_case_report"]
