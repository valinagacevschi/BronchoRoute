---
title: Merge conflicts with main resolve to the branch side when both sides ported the same commits
author: Vali
date: 2026-11-21
branch: 74-viewer-demo-affordances
cost: { minutes: 3, tokens: 268585 }
id: l-27a8726a
scope: repo
status: active
src: agent
touches:
  - app/server.py
  - pipeline/build_endoluminal_web.py
  - pipeline/run_totalsegmentator.py
  - prototype/endoluminal.html
---

## Decided

When the branch and main carry the same changes under different commit hashes
(each side ported or cherry-picked the other), resolve conflicts in favour of
the branch side. The branch file is the superset: main's content plus the
branch-only work the MR adds.

Check each side's commit list per file before trusting this. If main has a
commit the branch lacks, do not blanket-take ours.

## Tried and ruled out

Hand-merging the hunks one by one. For these files the branch side already
contained every main line plus additions, so the merge produced nothing new.

## Watch out

Main tracks `.trail/learnings`; the branch removed it with "Stop tracking local
agent memory". A merge from main re-adds those files and silently undoes that
commit. Decide which policy wins and say so in the merge message.

`trail check` flags existing branch content on files the merge never touched
(`renderer.html`, `renderer.template.html`). That is not a merge regression.

In the code:
- Conflicted: `app/server.py`, `pipeline/build_endoluminal_web.py`,
  `pipeline/run_totalsegmentator.py`, `prototype/endoluminal.html`.
- Resolution: `git checkout --ours <files> && git add`.
- Evidence: `git log --oneline HEAD..origin/main -- <file>` vs
  `git log --oneline origin/main..HEAD -- <file>` showed matching titles with
  different hashes, branch list a superset.
- Checks after: `uv run --with pytest python -m pytest -q -m server
  test_app_server.py test_server_settings.py` (63 passed) and
  `test_endoluminal_layers.py` (3 passed).
