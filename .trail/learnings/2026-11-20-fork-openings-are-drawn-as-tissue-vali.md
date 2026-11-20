---
title: "Fork openings are drawn as tissue mouths, not black circles"
author: Vali
date: 2026-11-20
branch: 74-viewer-demo-affordances
cost: { minutes: 10, tokens: 5076927 }
id: l-9f6a572a
scope: repo
status: active
src: agent
touches:
  - pipeline/test_realism_exports_playwright.py
  - pipeline/test_realism_flythrough_export_playwright.py
  - pipeline/test_realism_fork_gate_playwright.py
  - pipeline/test_realism_viewer_playwright.py
  - prototype/renderer.html
  - prototype/renderer.template.html
---

## Decided
The endoluminal bifurcation is drawn as organic mucosa: a beige lobed parent face with radial wrinkles and fine grain, each branch mouth as a dark lumen with corrugated inner folds, curved rim wrinkles, a soft mucosal lip, and a wide carina spur with creases.
Opening size is capped against the parent face (30% early, now 20%) so close forks do not fill the screen with black. Tissue grain comes from a small seeded noise canvas drawn with overlay composite, clipped to face and mouths.

## Tried and ruled out
Flat parent disk plus crisp black circles with blue/white outlines read as a diagram, not anatomy.
Filled radial-gradient rings washed the tunnel flat; wide stroke bands keep folds.
Straight radial spokes inside the mouths looked like scratches; short curved wrinkles near the rim work better.

## Watch out
Scoping a text patch by the first occurrence of a marker can delete everything up to a later marker. Search inside the target function span and assert the replaced length.
Openings sized from child radius at close range become enormous; cap by parent face fraction.

In the code: prototype/renderer.template.html drawForkOrifices (lobedPath, partialFold, forkNoise, orifice cap parentR * 0.20, carina spur), prototype/renderer.html regenerated.
