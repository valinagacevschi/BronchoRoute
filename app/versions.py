# Changes: Phase 4d review fixes — note_change opens N+1 inheriting hashes;
# freeze writes version-scoped snapshot under out/<case>/versions/v<n>/;
# report renders only from snapshot.json; clinician-text checks at submit;
# VersionAlreadyFrozen / FreezeDirExists → 409.
"""Artifact version chain stored on case.json.

register_artifact / note_change open n+1 after freeze (inheriting hashes).
freeze_open_version seals the open version; write_version_snapshot persists
immutable files under out/<case>/versions/v<n>/.
"""

from __future__ import annotations

import hashlib
import html
import json
import re
from pathlib import Path
from typing import Any

import paths

# Absolute paths and common DICOM identifier patterns must not appear in text.
_ABS_PATH_RE = re.compile(r"(?:/Users/|/home/|/tmp/|[A-Za-z]:\\)")
_UID_LIKE_RE = re.compile(r"\d+(?:\.\d+){4,}")
_FORBIDDEN_SUBSTR = (
    "patientname",
    "patient_name",
    "patientid",
    "patient_id",
    "patientbirth",
    "accession",
    "studyinstanceuid",
    "seriesinstanceuid",
    "sopinstanceuid",
)

REPORT_CSP = "default-src 'none'; style-src 'unsafe-inline'"


class VersionAlreadyFrozen(Exception):
    """No open version to freeze (already frozen / no changes since last freeze)."""

    def __init__(self, reason: str = "open version is already frozen"):
        super().__init__(reason)
        self.reason = reason


class FreezeDirExists(Exception):
    """Version snapshot directory already exists — never overwrite."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


class ReportNotFrozen(Exception):
    """Report requested for an unfrozen version."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def relative_to_pipeline(path: Path) -> str:
    """Return path relative to PIPELINE.

    Prefer absolute() (does not follow symlinks) so tmp test trees that symlink
    input files into PIPELINE stay addressable; fall back to resolve().
    """
    artifact = Path(path)
    pipe = Path(paths.PIPELINE)
    try:
        return str(artifact.absolute().relative_to(pipe.absolute()))
    except ValueError:
        return str(artifact.resolve().relative_to(pipe.resolve()))


def version_snapshot_dir(case_id: str, version_n: int) -> Path:
    return paths.PIPELINE / "out" / case_id / "versions" / f"v{int(version_n)}"


def note_change(case: dict, what: str) -> dict:
    """Ensure an open version exists after any post-freeze mutation.

    If the last version is frozen, open N+1 inheriting N's artifact hashes.
    Records ``what`` on the open version's ``changes`` list.
    """
    versions = case.setdefault("versions", [])
    label = str(what or "change")
    if not versions:
        versions.append({
            "n": 1,
            "artifacts": {},
            "frozen": False,
            "changes": [label],
        })
        return versions[-1]
    ver = versions[-1]
    if ver.get("frozen"):
        inherited = {
            k: dict(v) if isinstance(v, dict) else v
            for k, v in (ver.get("artifacts") or {}).items()
        }
        n = int(ver["n"]) + 1
        versions.append({
            "n": n,
            "artifacts": inherited,
            "frozen": False,
            "changes": [label],
            "inherits_from": int(ver["n"]),
        })
        return versions[-1]
    ver.setdefault("changes", []).append(label)
    return ver


def register_artifact(
    case: dict, name: str, path: Path | str, *, what: str | None = None
) -> dict:
    """Append/update artifact on the open version (opens N+1 after freeze)."""
    note_change(case, what or f"artifact:{name}")
    artifact_path = Path(path)
    versions = case.setdefault("versions", [])
    entry = {
        "path": relative_to_pipeline(artifact_path),
        "sha256": sha256_file(artifact_path),
    }
    versions[-1].setdefault("artifacts", {})[name] = entry
    return case


def register_bytes_artifact(
    case: dict, name: str, relative_path: str, data: bytes, *, what: str | None = None
) -> dict:
    """Write bytes under PIPELINE/relative_path and register the artifact."""
    path = paths.PIPELINE / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_bytes(data)
    tmp.replace(path)
    return register_artifact(case, name, path, what=what)


def current_version(case: dict) -> int | None:
    versions = case.get("versions") or []
    if not versions:
        return None
    return versions[-1].get("n")


def freeze_open_version(
    case: dict,
    *,
    clinician: str,
    statement: str,
    uncertainty: list[dict],
    at: str,
) -> dict:
    """Freeze the open version. Raises VersionAlreadyFrozen if none open."""
    versions = case.setdefault("versions", [])
    if not versions:
        versions.append({"n": 1, "artifacts": {}, "frozen": False})
    ver = versions[-1]
    if ver.get("frozen"):
        raise VersionAlreadyFrozen(
            "no changes since last freeze — re-sign requires a new open version"
        )
    ver["frozen"] = True
    ver["frozen_at"] = at
    ver["signed_by"] = clinician
    ver["signed_at"] = at
    ver["statement"] = statement
    ver["uncertainty"] = list(uncertainty)
    return ver


def validate_clinician_text(text: str, *, field: str = "text") -> str:
    """Reject UID-like strings, absolute paths, and forbidden identifier words."""
    cleaned = (text or "").strip()
    if not cleaned:
        raise ValueError(f"{field} is required")
    lower = cleaned.lower()
    for needle in _FORBIDDEN_SUBSTR:
        if needle in lower:
            raise ValueError(f"{field} contains forbidden identifier token: {needle}")
    if _ABS_PATH_RE.search(cleaned):
        raise ValueError(f"{field} must not contain an absolute path")
    if _UID_LIKE_RE.search(cleaned):
        raise ValueError(f"{field} must not contain a UID-looking string")
    return cleaned


def _fmt_mm(value: object) -> str:
    if value is None:
        return "—"
    try:
        return f"{float(value):.1f}"
    except (TypeError, ValueError):
        return str(value)


def _fmt_ras(ras: object) -> str:
    if not isinstance(ras, (list, tuple)) or len(ras) < 3:
        return "—" if ras is None else str(ras)
    try:
        return f"[{float(ras[0]):.1f}, {float(ras[1]):.1f}, {float(ras[2]):.1f}]"
    except (TypeError, ValueError):
        return str(ras)


LIMITATIONS_LIST = [
    "CT-derived pre-procedural view, not live tracking",
    "Simulated steering; roll is not measured scope orientation",
    "CT-to-body divergence",
    "Inspiratory CT vs procedural lung volume",
    "Respiratory motion",
    "Atelectasis",
    "Patient positioning",
    "Incomplete distal segmentation",
]


def _esc(value: object) -> str:
    return html.escape("" if value is None else str(value), quote=True)


def build_snapshot_dict(
    case_id: str,
    case: dict,
    ver: dict,
    *,
    tracing: dict,
    uncertainty: list[dict],
) -> dict[str, Any]:
    """Series summary + clinical fields for the frozen version snapshot."""
    source = case.get("source") if isinstance(case.get("source"), dict) else {}
    lesion = case.get("lesion") if isinstance(case.get("lesion"), dict) else {}
    route_start = (
        case.get("route_start") if isinstance(case.get("route_start"), dict) else {}
    )
    route_target = (
        case.get("route_target") if isinstance(case.get("route_target"), dict) else {}
    )
    arts = ver.get("artifacts") or {}
    # Exclude snapshot itself — embedding its own hash is circular.
    arts_for_snap = {
        name: entry for name, entry in arts.items()
        if name != "snapshot" and isinstance(entry, dict)
    }
    artifact_hashes = {
        name: entry.get("sha256") for name, entry in sorted(arts_for_snap.items())
    }
    # Freeze the version chain as of this sign-off (immutable in the report).
    chain = []
    for v in case.get("versions") or []:
        if not isinstance(v, dict):
            continue
        n = v.get("n")
        if n is None:
            continue
        if int(n) > int(ver["n"]):
            break
        chain.append({
            "n": int(n),
            "signed_at": v.get("signed_at") or v.get("frozen_at"),
            "reviewer": v.get("signed_by"),
            "frozen": bool(v.get("frozen")) or int(n) == int(ver["n"]),
        })
    # Ensure current version appears as frozen in the chain preview.
    if chain and int(chain[-1]["n"]) == int(ver["n"]):
        chain[-1]["signed_at"] = ver.get("signed_at") or ver.get("frozen_at")
        chain[-1]["reviewer"] = ver.get("signed_by")
        chain[-1]["frozen"] = True
    elif not any(int(c["n"]) == int(ver["n"]) for c in chain):
        chain.append({
            "n": int(ver["n"]),
            "signed_at": ver.get("signed_at") or ver.get("frozen_at"),
            "reviewer": ver.get("signed_by"),
            "frozen": True,
        })
    return {
        "case_id": case_id,
        "version": int(ver["n"]),
        "series": {
            "description": source.get("series_description") or source.get("description"),
            "kernel": source.get("convolution_kernel") or source.get("kernel"),
            "spacing_mm": source.get("spacing_mm"),
            "size": source.get("size"),
            "series_number": source.get("series_number"),
        },
        "lesion": {
            "ras_mm": lesion.get("ras_mm"),
        } if lesion else None,
        "route_start": {
            "node": route_start.get("node"),
            "ras_mm": route_start.get("ras_mm"),
        } if route_start else None,
        "route_target": {
            "edge_id": route_target.get("edge_id"),
            "sample_index": route_target.get("sample_index"),
            "ras_mm": route_target.get("ras_mm"),
            "target_gap_mm": route_target.get("target_gap_mm"),
            "target_inside_airway": route_target.get("target_inside_airway"),
        } if route_target else None,
        "tracing": tracing,
        "uncertainty": list(uncertainty),
        "limitations": list(LIMITATIONS_LIST),
        "reviewer": ver.get("signed_by"),
        "statement": ver.get("statement"),
        "signed_at": ver.get("signed_at") or ver.get("frozen_at"),
        "version_chain": chain,
        "artifact_hashes": artifact_hashes,
        "artifacts": {
            name: {
                "path": entry.get("path"),
                "sha256": entry.get("sha256"),
            }
            for name, entry in sorted(arts_for_snap.items())
        },
    }


def write_version_snapshot(
    case_id: str,
    version_n: int,
    *,
    route: dict,
    walkthrough: dict,
    tracing: dict,
    snapshot: dict,
) -> dict[str, Path]:
    """Write immutable freeze files under out/<case>/versions/v<n>/. Never overwrite."""
    vdir = version_snapshot_dir(case_id, version_n)
    if vdir.exists():
        raise FreezeDirExists(
            f"version directory v{version_n} already exists — freeze refused"
        )
    vdir.mkdir(parents=True, exist_ok=False)
    written: dict[str, Path] = {}
    for name, payload in (
        ("route.json", route),
        ("walkthrough.json", walkthrough),
        ("tracing.json", tracing),
        ("snapshot.json", snapshot),
    ):
        path = vdir / name
        path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        written[name] = path
    return written


def load_version_snapshot(case_id: str, version_n: int) -> dict:
    path = version_snapshot_dir(case_id, version_n) / "snapshot.json"
    if not path.is_file():
        raise ValueError(f"snapshot for version {version_n} not found")
    return json.loads(path.read_text(encoding="utf-8"))


def render_version_report(
    case_id: str,
    case: dict,
    version_n: int,
) -> str:
    """Printable HTML summary from frozen snapshot.json only (no live case fields)."""
    versions = case.get("versions") or []
    ver = next(
        (v for v in versions if isinstance(v, dict) and v.get("n") == version_n),
        None,
    )
    if ver is None:
        raise ValueError(f"version {version_n} not found")
    if not ver.get("frozen"):
        raise ReportNotFrozen(f"version {version_n} is not frozen")

    snap = load_version_snapshot(case_id, version_n)
    series = snap.get("series") or {}
    lesion = snap.get("lesion") or {}
    route_start = snap.get("route_start") or {}
    route_target = snap.get("route_target") or {}
    uncertainty = snap.get("uncertainty") if isinstance(snap.get("uncertainty"), list) else []
    tracing = snap.get("tracing") if isinstance(snap.get("tracing"), dict) else {}
    tracing_text = tracing.get("text") or ""
    limitations = snap.get("limitations") or LIMITATIONS_LIST
    arts = snap.get("artifacts") or {}
    # Also surface the snapshot file hash from case.json if registered.
    snap_art = (ver.get("artifacts") or {}).get("snapshot")
    if isinstance(snap_art, dict) and snap_art.get("sha256"):
        arts = dict(arts)
        arts.setdefault("snapshot", {
            "path": snap_art.get("path"),
            "sha256": snap_art.get("sha256"),
        })

    art_rows = []
    for name in sorted(arts.keys()):
        entry = arts[name] if isinstance(arts[name], dict) else {}
        path = entry.get("path") or ""
        if isinstance(path, str) and path.startswith("/"):
            path = Path(path).name
        art_rows.append(
            f"<tr><td>{_esc(name)}</td><td><code>{_esc(path)}</code></td>"
            f"<td><code>{_esc(entry.get('sha256'))}</code></td></tr>"
        )

    unc_items = "".join(
        f"<li>{_esc(item.get('text') if isinstance(item, dict) else item)}</li>"
        for item in uncertainty
    ) or "<li>None listed</li>"

    lim_items = "".join(f"<li>{_esc(line)}</li>" for line in limitations)
    trace_items = "".join(
        f"<li>{_esc(line)}</li>"
        for line in (tracing_text.splitlines() if tracing_text else [])
    ) or "<li>No confirmed bifurcations</li>"

    chain = snap.get("version_chain")
    if not isinstance(chain, list):
        chain = []
    chain_rows = []
    for v in chain:
        if not isinstance(v, dict):
            continue
        chain_rows.append(
            f"<tr><td>{_esc(v.get('n'))}</td>"
            f"<td>{_esc(v.get('signed_at') or '—')}</td>"
            f"<td>{_esc(v.get('reviewer') or '—')}</td>"
            f"<td>{'frozen' if v.get('frozen') else 'open'}</td></tr>"
        )

    gap = route_target.get("target_gap_mm")
    inside = route_target.get("target_inside_airway")
    spacing = series.get("spacing_mm")
    if isinstance(spacing, (list, tuple)):
        spacing_txt = ", ".join(_fmt_mm(v) for v in spacing)
    else:
        spacing_txt = _fmt_mm(spacing) if spacing is not None else "—"

    body = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Lung Navigator report — {_esc(case_id)} v{_esc(version_n)}</title>
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
  code {{ font: 11px/1.35 ui-monospace, SFMono-Regular, Menlo, monospace; }}
  ul {{ margin: 0; padding-left: 1.2rem; }}
  .meta {{ color: #5D7280; font-size: 12px; margin-bottom: 16px; }}
  .box {{ border: 1px solid #C7D3DA; border-radius: 4px; padding: 10px 12px;
          background: #F7FAFB; }}
  @media print {{
    body {{ max-width: none; width: auto; padding: 0; }}
    a {{ color: inherit; text-decoration: none; }}
  }}
</style>
</head>
<body>
  <h1>Lung Navigator — case report</h1>
  <p class="meta">Case {_esc(case_id)} · version {_esc(version_n)} · frozen</p>

  <h2>Source series</h2>
  <table>
    <tr><th>Description</th><td>{_esc(series.get("description") or "—")}</td></tr>
    <tr><th>Kernel</th><td>{_esc(series.get("kernel") or "—")}</td></tr>
    <tr><th>Spacing mm</th><td>{_esc(spacing_txt)}</td></tr>
  </table>

  <h2>Version chain</h2>
  <table>
    <tr><th>n</th><th>Signed at</th><th>Reviewer</th><th>State</th></tr>
    {"".join(chain_rows) or "<tr><td colspan='4'>None</td></tr>"}
  </table>

  <h2>Versions and hashes</h2>
  <table>
    <tr><th>Artifact</th><th>Path</th><th>SHA-256</th></tr>
    {"".join(art_rows) or "<tr><td colspan='3'>No artifacts</td></tr>"}
  </table>

  <h2>Lesion</h2>
  <p>{_esc(_fmt_ras(lesion.get("ras_mm") if lesion else None))}</p>

  <h2>RouteStart</h2>
  <p>node {_esc(route_start.get("node") if route_start else "—")}
     · {_esc(_fmt_ras(route_start.get("ras_mm") if route_start else None))}</p>

  <h2>RouteTarget and gap</h2>
  <p>edge {_esc(route_target.get("edge_id") if route_target else "—")}
     sample {_esc(route_target.get("sample_index") if route_target else "—")}
     · ras {_esc(_fmt_ras(route_target.get("ras_mm") if route_target else None))}<br>
     gap {_esc(_fmt_mm(gap))} mm
     {"· inside airway" if inside else "· beyond airway" if gap is not None else ""}</p>

  <h2>Branch-tracing sequence</h2>
  <ul>{trace_items}</ul>

  <h2>Uncertainty</h2>
  <ul>{unc_items}</ul>

  <h2>Limitations</h2>
  <ul>{lim_items}</ul>

  <h2>Sign-off</h2>
  <div class="box">
    <p><strong>Signed by</strong> {_esc(snap.get("reviewer") or "—")}<br>
       <strong>Signed at</strong> {_esc(snap.get("signed_at") or "—")}</p>
    <p>{_esc(snap.get("statement") or "")}</p>
  </div>
</body>
</html>
"""
    return body


def collect_signoff_artifacts(case_id: str, case: dict) -> dict[str, Path]:
    """Map artifact name → path for everything that should be fingerprinted at freeze."""
    from app.caseref import CaseRef
    from app.workflow import SEGMENT_ARTIFACT_NAMES, segment_artifact_paths

    ref = CaseRef.parse(case_id)
    out: dict[str, Path] = {}
    if ref.ct.is_file():
        out["ct"] = ref.ct
    paths_map = segment_artifact_paths(ref)
    for name in SEGMENT_ARTIFACT_NAMES:
        p = paths_map[name]
        if p.is_file():
            out[name] = p
    versions = case.get("versions") or []
    arts = (versions[-1].get("artifacts") if versions else {}) or {}
    for name in ("effective_graph", "manual_mask", "bridges_mask", "bridges_json"):
        entry = arts.get(name)
        if isinstance(entry, dict) and entry.get("path"):
            p = paths.PIPELINE / entry["path"]
            if p.is_file():
                out[name] = p
    for name, rel in (
        ("effective_graph", f"out/{case_id}/{case_id}_effective_graph.json"),
        ("manual_mask", f"ts_out/{case_id}/manual_airways.nii.gz"),
    ):
        if name in out:
            continue
        p = paths.PIPELINE / rel
        if p.is_file():
            out[name] = p
    return out


__all__ = [
    "FreezeDirExists",
    "LIMITATIONS_LIST",
    "REPORT_CSP",
    "ReportNotFrozen",
    "VersionAlreadyFrozen",
    "build_snapshot_dict",
    "collect_signoff_artifacts",
    "current_version",
    "freeze_open_version",
    "load_version_snapshot",
    "note_change",
    "register_artifact",
    "register_bytes_artifact",
    "relative_to_pipeline",
    "render_version_report",
    "sha256_bytes",
    "sha256_file",
    "validate_clinician_text",
    "version_snapshot_dir",
    "write_version_snapshot",
]
