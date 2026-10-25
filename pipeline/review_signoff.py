"""Record clinician decisions for graph review flags on a local case."""

import argparse
from datetime import date
import json
import re
from pathlib import Path

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

    case_path = Path("data/extracted/local") / args.case_id / "case.json"
    if not case_path.is_file():
        parser.error(f"case.json not found: {case_path}")
    record = json.loads(case_path.read_text())
    entries = record.setdefault("review_signoff", [])
    if args.list:
        print(json.dumps(entries, indent=2))
        return

    required = (args.kind, args.lobe, args.decision, args.finding, args.reviewer)
    if any(value is None for value in required):
        parser.error("--kind, --lobe, --decision, --finding, and --reviewer are required")
    finding = args.finding.strip()
    if not finding or "\n" in args.finding or "\r" in args.finding:
        parser.error("--finding must be non-empty and a single line")
    if not valid_reviewer(args.reviewer):
        parser.error("--reviewer must be a role followed by comma and initials (e.g. Radiologist, A.B.)")
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", args.date):
        parser.error("--date must be YYYY-MM-DD")
    try:
        date.fromisoformat(args.date)
    except ValueError:
        parser.error("--date must be YYYY-MM-DD")

    entry = {"kind": args.kind, "lobe": args.lobe, "decision": args.decision,
             "finding": finding, "reviewer": args.reviewer, "date": args.date}
    entries[:] = [old for old in entries
                  if (old.get("kind"), old.get("lobe")) != (args.kind, args.lobe)]
    entries.append(entry)
    case_path.write_text(json.dumps(record, indent=2) + "\n")
    print(f"recorded {args.decision}: {args.kind} {args.lobe}")


if __name__ == "__main__":
    main()
