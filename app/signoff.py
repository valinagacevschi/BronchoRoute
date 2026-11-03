# Changes: Phase 4d review fixes — validate statement at submit; fail sign-off
# (409) if route/walk/tracing cannot be produced; write immutable v<n>/ snapshot
# files; freeze last under lock; VersionAlreadyFrozen / FreezeDirExists → 409;
# report from snapshot only.
"""Phase 4d — Sign off and freeze version N (workflow step 8)."""

from __future__ import annotations

import json

from atomic_json import atomic_write_json, json_file_lock
from termcolor import cprint

from app.cases import _load_or_init_record, _utc_now, load_case_record
from app.caseref import CaseRef
from app.versions import (
    VersionAlreadyFrozen,
    FreezeDirExists,
    build_snapshot_dict,
    collect_signoff_artifacts,
    freeze_open_version,
    register_artifact,
    relative_to_pipeline,
    render_version_report,
    sha256_file,
    validate_clinician_text,
    version_snapshot_dir,
)
from app.walkthrough import (
    active_route_context,
    compute_uncertainty,
    tracing_sequence,
    walk_complete,
    walkthrough_entries_for_hash,
)
from app.workflow import WorkflowBlocked, WorkflowStep, _block_reason, _ordered_done


class SignoffArtifactError(Exception):
    """Route / walkthrough / tracing could not be produced for freeze."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


def apply_signoff(case_id: str, clinician: str, statement: str) -> dict:
    """Require steps 1–7 done; write snapshot; freeze; append record."""
    statement_text = validate_clinician_text(statement, field="statement")
    if len(statement_text) > 2000:
        raise ValueError("statement exceeds 2000 characters")

    ref = CaseRef.parse(case_id)
    path = ref.case_json
    at = _utc_now()

    with json_file_lock(path):
        record = _load_or_init_record(ref, path)
        done, _ = _ordered_done(record, ref)
        if not all(done[: int(WorkflowStep.SIGNOFF) - 1]):
            raise WorkflowBlocked(_block_reason(int(WorkflowStep.SIGNOFF), done))
        if not walk_complete(case_id, record):
            raise WorkflowBlocked("step 7 (Walk the route) is not done")

        versions = record.get("versions") or []
        if versions and versions[-1].get("frozen"):
            raise VersionAlreadyFrozen(
                "no changes since last freeze — re-sign requires a new open version"
            )

        uncertainty = compute_uncertainty(case_id, record)

        # Validate freeze artifacts BEFORE any writes.
        try:
            ctx = active_route_context(case_id, record)
        except (ValueError, FileNotFoundError, KeyError) as exc:
            raise SignoffArtifactError(
                f"route artifact cannot be produced: {exc}"
            ) from exc

        tracing = tracing_sequence(case_id, record)
        if tracing.get("route_hash") is None and tracing.get("reason"):
            raise SignoffArtifactError(
                f"tracing artifact cannot be produced: {tracing['reason']}"
            )

        wt_entries = walkthrough_entries_for_hash(record, ctx["route_hash"])
        route_payload = {
            "edge_ids": ctx["edge_ids"],
            "sample_index": ctx["sample_index"],
            "fork_node_ids": ctx["fork_node_ids"],
            "route_hash": ctx["route_hash"],
            "route_target": record.get("route_target"),
            "route_start": record.get("route_start"),
        }
        walk_payload = {
            "route_hash": ctx["route_hash"],
            "entries": wt_entries,
        }

        # Fingerprint current disk artifacts into the open version.
        for name, art_path in collect_signoff_artifacts(case_id, record).items():
            register_artifact(record, name, art_path)

        # Determine version number for the snapshot directory (open version).
        open_ver = (record.get("versions") or [{"n": 1}])[-1]
        version_n = int(open_ver.get("n") or 1)

        # Snapshot dict uses post-register artifact hashes; freeze metadata filled after.
        # Pre-build clinical body; signed_by/statement/signed_at stamped on freeze.
        # Write files first, register them, then freeze last under the lock.
        prelim = {
            "n": version_n,
            "artifacts": dict(open_ver.get("artifacts") or {}),
            "signed_by": clinician,
            "signed_at": at,
            "statement": statement_text,
            "uncertainty": uncertainty,
        }
        snapshot = build_snapshot_dict(
            case_id, record, prelim, tracing=tracing, uncertainty=uncertainty,
        )

        # Write route/walkthrough/tracing first, register them, then write
        # snapshot once (no self-hash circularity).
        vdir = version_snapshot_dir(case_id, version_n)
        try:
            if vdir.exists():
                raise FreezeDirExists(
                    f"version directory v{version_n} already exists — freeze refused"
                )
            vdir.mkdir(parents=True, exist_ok=False)
            for name, payload in (
                ("route.json", route_payload),
                ("walkthrough.json", walk_payload),
                ("tracing.json", tracing),
            ):
                fpath = vdir / name
                fpath.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
                open_ver.setdefault("artifacts", {})[name.replace(".json", "")] = {
                    "path": relative_to_pipeline(fpath),
                    "sha256": sha256_file(fpath),
                }
            snapshot = build_snapshot_dict(
                case_id, record, {
                    **prelim,
                    "artifacts": dict(open_ver.get("artifacts") or {}),
                },
                tracing=tracing,
                uncertainty=uncertainty,
            )
            snap_path = vdir / "snapshot.json"
            snap_path.write_text(
                json.dumps(snapshot, indent=2) + "\n", encoding="utf-8"
            )
            open_ver.setdefault("artifacts", {})["snapshot"] = {
                "path": relative_to_pipeline(snap_path),
                "sha256": sha256_file(snap_path),
            }
        except FreezeDirExists:
            raise
        except OSError as exc:
            raise SignoffArtifactError(
                f"freeze snapshot write failed: {exc}"
            ) from exc

        ver = freeze_open_version(
            record,
            clinician=clinician,
            statement=statement_text,
            uncertainty=uncertainty,
            at=at,
        )
        entry = {
            "step": int(WorkflowStep.SIGNOFF),
            "kind": "signoff",
            "data": {
                "version": ver.get("n"),
                "statement": statement_text,
                "uncertainty": uncertainty,
            },
            "clinician": clinician,
            "at": at,
        }
        record.setdefault("records", []).append(entry)
        atomic_write_json(path, record)
        cprint(f"signed off case {case_id} version {ver.get('n')}", "green")
        return {
            "version": ver,
            "record": entry,
            "uncertainty": uncertainty,
        }


def version_report_html(case_id: str, version_n: int) -> str:
    ref = CaseRef.parse(case_id)
    case = load_case_record(ref)
    return render_version_report(case_id, case, version_n)


__all__ = [
    "SignoffArtifactError",
    "apply_signoff",
    "version_report_html",
]
