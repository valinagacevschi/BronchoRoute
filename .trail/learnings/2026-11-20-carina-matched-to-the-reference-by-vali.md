---
title: "Carina matched to the reference by scaling mouths, softening folds, keeping a dark core"
author: Vali
date: 2026-11-20
branch: 74-viewer-demo-affordances
cost: { minutes: 15, tokens: 7003890 }
id: l-392c5e2d
scope: repo
status: active
src: agent
touches:
  - prototype/renderer.html
  - prototype/renderer.template.html
  - pipeline/test_flythrough_playwright.py
---

## Decided
Fork mouths fill most of the view near the carina: size cap is 36% of the parent face, fit to a 1.02 face budget.
Interiors are brown with four irregular, shadow-blurred folds, rolled outer rims, and a narrow carina spur with a central crease and fold lines.
The lumen core stays dark enough for the offline fly-through test: the dark blob detector only counts luminance <= 28, so lighten the rim, not the first third of the radius.
The parent face carries broad shadow-blurred folds plus fine seeded grain, clipped to the face.

## Tried and ruled out
Uniform concentric coil folds read as a mechanical target, not mucosa; four irregular partial arcs are closer.
Evenly bright spur looked like a painted stripe; narrowing it and adding a crease reads as tissue.
Lightening the whole lumen gradient broke the blob-angle/label test; keep a dark core.

## Watch out
The fly-through test measures dark blobs inside each orifice (>=12 pixels at luminance <= 28) and their angle; any future lightening must preserve the core.
Opening size and face radius depend on the close-range projection; cap by parent face fraction, not raw child radius.

In the code: prototype/renderer.template.html drawForkOrifices (preferred cap parentR * 0.36, hole gradient stops, folds array, rolled rims, spur crease), prototype/renderer.html regenerated.
