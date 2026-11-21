---
title: "Candidate diameter is a measurement, not a Lesion value"
author: Vali
date: 2026-11-21
branch: 68-test-fixes
id: l-715c9ac9
scope: repo
status: active
src: agent
touches:
  - "app/cases.py#confirm_candidate"
  - prototype/ct_candidates.js
---

A candidate's diameter_mm comes from the mask and can exceed the Lesion range (local-001: 64.5 mm). Render any finite measured value in the candidate list; never reuse lesionDiameterOf there because it clamps to 2-60 and falls back to 10. At confirm, clamp only the confirmed Lesion to the nearest size inside 2-60 and leave candidate storage unchanged. The CT pane circle may keep the Lesion clamp.
