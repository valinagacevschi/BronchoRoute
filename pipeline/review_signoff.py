"""Record clinician decisions for graph review flags on a case."""

from __future__ import annotations

import argparse
import json
import re
from datetime import date

import repo_root
from atomic_json import atomic_write_json, json_file_lock

repo_root.ensure()

from app.caseref import CaseRef, UnknownCase  # noqa: E402

KNOWN_KINDS = {"small_lobe", "lobe_without_airways"}
REVIEWER_PATTERN = re.compile(r"^[^,\r\n]+, ([A-Z]\.){1,4}$")
FULL_NAME_ROLE_PATTERN = re.compile(
    r"^[A-Z][a-z]+(?:[-'][A-Z]?[a-z]+)?(?:\s+[A-Z][a-z]+)+$"
)


def valid_reviewer(value: str) -> bool:
    if not REVIEWER_PATTERN.fullmatch(value):
        return False
    role = value.split(",", 1)[0].strip()
    return not FULL_NAME_ROLE_PATTERN.fullmatch(role)


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
        finding = args.finding.strip()
        if not finding or "\n" in args.finding or "\r" in args.finding:
            parser.error("--finding must be non-empty and a single line")
        if not valid_reviewer(args.reviewer):
            parser.error(
                "--reviewer must be a role followed by comma and initials "
                "(e.g. Radiologist, A.B.)"
            )
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", args.date):
            parser.error("--date must be YYYY-MM-DD")
        try:
            date.fromisoformat(args.date)
        except ValueError:
            parser.error("--date must be YYYY-MM-DD")

        entry = {
            "kind": args.kind,
            "lobe": args.lobe,
            "decision": args.decision,
            "finding": finding,
            "reviewer": args.reviewer,
            "date": args.date,
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
