---
title: Transparent black gradient stops darken the whole canvas; use the wall hue instead
author: Vali
date: 2026-11-20
branch: 74-viewer-demo-affordances
cost: { minutes: 21, tokens: 1616512 }
id: l-a63ec8c1
scope: repo
status: active
src: agent
touches:
  - prototype/renderer.template.html
  - prototype/renderer.html
---

## Decided
Endoluminal now matches the a1 reference: beige ground, dark far lumen, wall folds drawn as wide translucent stroke bands, green 6 mm rings, minimap top-right, green mono readout bottom-left.
Generated realistic images are disabled for now: realism starts off, V shows "Realistic images are off", the RouteTarget realism option stays hidden, and boot no longer starts generation. Renderer realism Playwright suites are skipped with a reason; the template-splice test still runs.
Minimap top-right must clear fixed DOM panels by measuring their rectangles; offsetParent is null for fixed elements.

## Tried and ruled out
Radial gradient with addColorStop(0, "rgba(0,0,0,0)"): premultiplied interpolation darkened the whole canvas to about half brightness. Use a transparent stop in the wall hue instead.
Filled wall-ring gradients: overlapping fills wash the tunnel flat. Wide stroke bands keep folds readable.
Placing minimap from offsetParent checks: fixed panels report null, so the map landed under them.

## Watch out
Canvas covers the whole window; overlays must avoid the rail and DOM panels by measured rects.
Skipping a whole realism test module also skipped the template-splice check; use a collection hook that keeps that one test.

In the code: prototype/renderer.template.html (ENDO_GROUND, mucosa, lumen gradient, wall band loop, drawMinimap top-right, drawEndoReadout, realism.on false, syncTargetRealismOption, targetRealismWanted), pipeline/test_realism_*_playwright.py skip markers, prototype/renderer.html regenerated.
