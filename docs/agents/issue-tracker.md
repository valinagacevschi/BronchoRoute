# Issue tracker

Issues live on GitLab: `vali/lung-navigator` on git.xelasys.ro (`glab`, run from the repo root so it resolves the host).

## Wayfinding operations

- Map: one issue labelled `wayfinder:map`. Tickets: issues labelled `wayfinder:research|prototype|grilling|task`, never triage labels.
- Parent: ticket body starts `Part of #<map>`, plus a `relates_to` link to the map (issue-under-issue hierarchy is unavailable).
- Blocking: native blocked-by links need a licence this instance lacks. Use a `Blocked by: #a, #b` body line plus `relates_to` links. A ticket is unblocked when every listed issue is closed.
- Claim: assign the issue to the dev driving the map before any work.
- Frontier: open `wayfinder:*` children of the map, unassigned, whose `Blocked by` issues are all closed.
- Resolve: post a `**Resolution**` note, close the issue, add a line to the map's Decisions so far.
- Create issues with `glab api projects/135/issues -X POST --input <json> -H 'Content-Type: application/json'`.
