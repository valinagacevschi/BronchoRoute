---
title: GitHub mirror keeps its public snapshot history; GitLab work lands on a new branch
author: Vali
date: 2026-11-23
branch: endoluminal-split-panel
cost: { minutes: 1, tokens: 1302 }
id: l-a487d050
scope: repo
status: active
src: agent
---

## Decided
Publish GitLab history to GitHub only on a fresh branch. Never rewrite GitHub `main`, which holds the public 0.1.0 snapshot with no shared ancestry.

## Tried and ruled out
- Overwriting GitHub `main` with GitLab `main`: rewrites the public snapshot history. User ruled it out.
- Fast-forward to GitHub `main`: impossible, the snapshot commit is not an ancestor of any GitLab ref.

## Watch out
Publishing the current working branch means unmerged work goes public. Confirm which ref is meant: deployed `main` or branch head.

In the code:
- remotes: origin = GitLab lung-navigator, github = BronchoRoute
- GitHub main tip: 8884a5e "BronchoRoute 0.1.0 - public snapshot"
- ancestry check: merge-base --is-ancestor 8884a5e origin/main returns false
- landed branch endoluminal-split-panel at f4d6c3b on github
