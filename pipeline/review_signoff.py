# Changes: extract validate_finding / validate_signoff_entry for app reuse;
# CLI behaviour unchanged (still requires --date).
"""Record clinician decisions for graph review flags on a case."""

from __future__ import annotations

import argparse
import json
import re
from datetime import date

import repo_root
from atomic_json import atomic_write_json, json_file_lock
from termcolor import cprint

repo_root.ensure()

from app.caseref import CaseRef, UnknownCase  # noqa: E402

KNOWN_KINDS = {"small_lobe", "lobe_without_airways"}
SIGNOFF_DECISIONS = frozenset({"approved", "rejected"})
FINDING_MAX_CHARS = 300
REVIEWER_PATTERN = re.compile(r"^[^,\r\n]+, ([A-Z]\.){1,4}$")
FULL_NAME_ROLE_PATTERN = re.compile(
    r"^[A-Z][a-z]+(?:[-'][A-Z]?[a-z]+)?(?:\s+[A-Z][a-z]+)+$"
)


def valid_reviewer(value: str) -> bool:
    if not isinstance(value, str) or not REVIEWER_PATTERN.fullmatch(value):
        return False
    role = value.split(",", 1)[0].strip()
    return not FULL_NAME_ROLE_PATTERN.fullmatch(role)


def validate_finding(finding: object) -> str:
    """Non-empty single-line finding ≤ FINDING_MAX_CHARS."""
    if not isinstance(finding, str):
        raise ValueError("finding must be a string")
    text = finding.strip()
    if not text:
        raise ValueError("finding must be non-empty and a single line")
    if "\n" in finding or "\r" in finding:
        raise ValueError("finding must be non-empty and a single line")
    if len(text) > FINDING_MAX_CHARS:
        raise ValueError(f"finding must be ≤ {FINDING_MAX_CHARS} characters")
    return text


def validate_signoff_date(day: object) -> str:
    if not isinstance(day, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", day):
        raise ValueError("date must be YYYY-MM-DD")
    try:
        date.fromisoformat(day)
    except ValueError as exc:
        raise ValueError("date must be YYYY-MM-DD") from exc
    return day


def validate_signoff_entry(entry: object) -> dict | None:
    """Return a normalised entry or None if malformed (caller may warn)."""
    if not isinstance(entry, dict):
        return None
    kind = entry.get("kind")
    lobe = entry.get("lobe")
    decision = entry.get("decision")
    reviewer = entry.get("reviewer")
    if kind not in KNOWN_KINDS:
        return None
    if not isinstance(lobe, str) or not lobe.strip():
        return None
    if decision not in SIGNOFF_DECISIONS:
        return None
    if not valid_reviewer(reviewer if isinstance(reviewer, str) else ""):
        return None
    try:
        finding = validate_finding(entry.get("finding"))
    except ValueError:
        return None
    out = {
        "kind": kind,
        "lobe": lobe.strip(),
        "decision": decision,
        "finding": finding,
        "reviewer": reviewer,
    }
    if isinstance(entry.get("date"), str):
        try:
            out["date"] = validate_signoff_date(entry["date"])
        except ValueError:
            pass
    if isinstance(entry.get("at"), str) and entry["at"].strip():
        out["at"] = entry["at"].strip()
    # App entries have `at`; CLI entries have `date`. Need at least one timestamp.
    if "at" not in out and "date" not in out:
        return None
    return out


def iter_valid_signoffs(entries: object) -> list[dict]:
    """Skip malformed sign-off rows with a warning; never raise."""
    if not isinstance(entries, list):
        return []
    out: list[dict] = []
    for i, raw in enumerate(entries):
        parsed = validate_signoff_entry(raw)
        if parsed is None:
            cprint(f"skipping malformed review_signoff[{i}]", "yellow")
            continue
        out.append(parsed)
    return out


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("case_id")
    parser.add_argument("--kind", choices=sorted(KNOWN_KINDS))
    parser.add_argument("--lobe")
    parser.add_argument("--decision", choices=("approved", "rejected"))
    parser.add_argument("--finding")
    parser.add_argument("--reviewer")
    parser.add_argument("--date", default=date.today().isoformat())
    parser.add_argument("--list", action="store_true")
    args = parser.parse_args()

    try:
        ref = CaseRef.parse(args.case_id)
    except UnknownCase as exc:
        parser.error(str(exc))

    case_path = ref.case_json
    with json_file_lock(case_path):
        if case_path.is_file():
            record = json.loads(case_path.read_text())
        elif ref.kind == "aeropath":
            record = {
                "id": ref.case_id,
                "source": ref.source_label,
                "records": [],
                "versions": [],
                "review_signoff": [],
            }
        else:
            parser.error(f"case.json not found: {case_path}")

        entries = record.setdefault("review_signoff", [])
        if args.list:
            print(json.dumps(entries, indent=2))
            return

        required = (args.kind, args.lobe, args.decision, args.finding, args.reviewer)
        if any(value is None for value in required):
            parser.error(
                "--kind, --lobe, --decision, --finding, and --reviewer are required"
            )
        try:
            finding = validate_finding(args.finding)
        except ValueError as exc:
            parser.error(str(exc))
        if not valid_reviewer(args.reviewer):
            parser.error(
                "--reviewer must be a role followed by comma and initials "
                "(e.g. Radiologist, A.B.)"
            )
        try:
            day = validate_signoff_date(args.date)
        except ValueError as exc:
            parser.error(str(exc))

        entry = {
            "kind": args.kind,
            "lobe": args.lobe,
            "decision": args.decision,
            "finding": finding,
            "reviewer": args.reviewer,
            "date": day,
        }
        entries[:] = [
            old for old in entries
            if (old.get("kind"), old.get("lobe")) != (args.kind, args.lobe)
        ]
        entries.append(entry)
        atomic_write_json(case_path, record)
    print(f"recorded {args.decision}: {args.kind} {args.lobe}")


if __name__ == "__main__":
    main()
