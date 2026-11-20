---
title: Demo viewer affordances port stays inside the existing lesion-anchored route model
author: Vali
date: 2026-11-20
branch: main
cost: { minutes: 9, tokens: 492264 }
id: l-6f99ce49
scope: repo
status: active
src: agent
touches:
  - prototype/renderer.template.html
  - app/route_target.py
  - app/server.py
  - prototype/ct_panes.js
  - pipeline/route_frame.py
  - pipeline/make_prototype_data.py
---

## Decided
Port only the standalone demo viewer affordances into the product app, using data a case already has: no public dataset download, no DICOM-SEG import, no pipeline changes, no vessel or heart surfaces.
Behaviours: end-of-route reveal inside the Endoluminal variant at automatic route end; jump key between Endoluminal and Anatomical 3D; corner airway minimap in those two variants; optional green 6 mm distance rings sized by local radius, mucosa rings kept; clickable lesion sphere opens the RouteTarget candidate panel; clickable airway node sets the route endpoint near the lesion through the existing route-target call; aids hidden by one key; with no confirmed lesion, clicks and reveal show a hint and are disabled.
Planning stays server-side. Product quality: tests plus provenance, one branch with four commits and one PR.

## Tried and ruled out
Client-side Dijkstra / instant replan: would duplicate route logic; local round trip is acceptable.
Free-destination routing from a clicked airway point: needs a new server concept and breaks the lesion-anchored metrics model.
Rendering existing artery, vein and heart masks as surfaces: extra pipeline work, not a viewer port.

## Watch out
Route targets require a confirmed lesion and are scored against lesion position; an airway click is an endpoint choice, not a free target. Do not present it as "route anywhere".
Existing keys are taken: V realistic forks, R reset, F/H/O/T presets, Space play. New keys must not collide.

In the code: prototype/renderer.template.html (served viewer, regenerated renderer.html), app/route_target.py set_route_target and route_target_candidates, app/server.py endpoint for target picking, prototype/ct_panes.js click path, pipeline/route_frame.py and pipeline/make_prototype_data.py payload fields, Playwright tests under pipeline/test_*_playwright.py.
