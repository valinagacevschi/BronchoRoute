---
title: "The viewer canvas spans the whole window, so new overlays must offset the rail width"
author: Vali
date: 2026-11-20
branch: 74-viewer-demo-affordances
cost: { minutes: 5, tokens: 1624830 }
id: l-e0c3840a
scope: repo
status: active
src: agent
touches:
  - prototype/renderer.html
  - prototype/renderer.template.html
  - pipeline/test_viewer_affordances_playwright.py
---

## Decided
Any canvas overlay drawn at canvas top-left is hidden behind the DOM rail, which floats on top of a full-window canvas. Place new readouts past the rail width (measured from .rail) instead of using canvas insets.
The route-end reveal readout now sits at rail width + 18 px, 14 px from the top, and stayed visible in the running app.

## Tried and ruled out
Using the endoluminal chrome visible rectangle: it returned the full canvas (0..W, 0..H), so it does not account for the rail.
Placing the block at the bottom-left: collided with the clock HUD and rail footer.

## Watch out
Canvas-pixel probes and screenshots can disagree while the rail occludes the drawing area; check the element rectangle, not only canvas coordinates.
Debug counters alone proved the reveal ran but not that it was visible; driving the real app found the occlusion.

In the code: prototype/renderer.template.html drawEndoluminalReveal text block, document.querySelector(".rail").getBoundingClientRect().width, prototype/renderer.html regenerated, pipeline/test_viewer_affordances_playwright.py.
