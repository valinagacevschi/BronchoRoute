# Brief: clinical sign-off for review flags + demo source label/banner

Repo /Users/val/dev/dicom. Work in `pipeline/` and `prototype/renderer.template.html` only.
`env -u PYTHONPATH uv run ...` from `pipeline/`. Do NOT git add/commit. Same PATIENT DATA RULE as
`.codex-briefs/import-and-review.md`. Do not touch `pipeline/prototype_data.js` or
`prototype/renderer.html` (AeroPath demo outputs).

## Context
`build_graph.py` writes `"review": [...]` (kinds `small_lobe` {lobe, ml, median_ml} and
`lobe_without_airways` {lobe}). `make_prototype_data.py` refuses a graph with a non-empty review
list unless `--allow-review`. Graph JSON has a `case` field (e.g. "local-001"; AeroPath "3").
Local cases have `data/extracted/local/<case>/case.json`.

## Task 1 — sign-off record (decisions approved)
- New `pipeline/review_signoff.py <case-id> --kind small_lobe --lobe "right middle lobe"
  --decision approved|rejected --finding "<one line>" --reviewer "<role, initials>"
  [--date YYYY-MM-DD, default today]`. Appends to `case.json` key `review_signoff` (list) an entry
  {kind, lobe, decision, finding, reviewer, date}. Validate: case.json exists, kind is a known
  kind, reviewer matches `^[^,]+, ([A-Z]\.){1,4}$` (role + initials only — reject anything that
  looks like a full name), finding non-empty and single line. Replaces an earlier entry with the
  same (kind, lobe). `--list` prints current sign-offs.
- `make_prototype_data.py`: before refusing, load `data/extracted/local/<case>/case.json` if it
  exists and match each review entry to a sign-off by (kind, lobe).
  approved -> resolved; rejected -> refuse even with `--allow-review`? No: rejected means refuse,
  and `--allow-review` still overrides with the loud warning (dev viewing). Unmatched -> refuse
  as now. Print each resolved entry: `reviewed: <kind> <lobe> — approved by <reviewer> on <date>`.
  Payload: `"review"` = unresolved entries only; `"signoff"` = matched approved entries
  (kind, lobe, finding, reviewer, date). `build_graph.py` is NOT changed: flags always recomputed,
  so a new/changed flag needs a fresh sign-off.
- Tests `pipeline/test_review_gate.py` (pytest, tmp_path, no real data): unresolved refused;
  approved matched passes; rejected refused; signoff for a different lobe does not resolve;
  `--allow-review` overrides; reviewer validation rejects "John Smith" and "Dr John Smith, J.S."?
  — the regex above accepts the latter's shape only if role part has no name; keep it simple:
  reject when the role part contains 2+ capitalised words. Refactor gate logic into a pure
  function so it is testable without running the whole export.

## Task 2 — renderer template
- `make_prototype_data.py` adds `"source"` to the payload: AeroPath numeric case -> "AeroPath <n>",
  local case -> the case id (e.g. "local-001"). Template line ~148
  `<dd>AeroPath 1</dd>` -> filled from `A.source` (fallback "unknown").
- Banner: when `A.review` is non-empty, show a fixed, clearly visible amber banner at the top:
  "Clinical review pending — not for training use" + one line per entry. When `A.signoff` is
  non-empty, show a small neutral note in the sidebar readout: "Reviewed: <lobe> <kind> —
  <reviewer>, <date>". Match the template's existing style/CSS variables.
- Regenerate ONLY to scratch: `make_prototype_data.py out/fallback/3_graph.json out/check_3.js
  --html out/check_3.html` and confirm "AeroPath 3" source, no banner. Check with headless
  playwright: `env -u PYTHONPATH uv run -q --with playwright python <script>`; screenshot to `out/`,
  report console errors (must be none).

## Do NOT write a real sign-off for local-001
I will record it with real clinician details. For verification use tmp copies only.

## Verify
py_compile; `--with pytest pytest test_bridge.py test_route_frame.py test_review_gate.py` pass;
make_prototype_data on out/local-001/local-001_graph.json still refused (no sign-off yet), with
`--allow-review` the html shows the banner (screenshot). Short summary.
