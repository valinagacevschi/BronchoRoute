---
title: "Endoluminal view renders the examples three.js scene in an iframe, debug builds keep the canvas"
author: Vali
date: 2026-11-20
branch: 74-viewer-demo-affordances
cost: { minutes: 1, tokens: 1229 }
id: l-8cbb54a1
scope: repo
status: active
src: agent
touches:
  - prototype/renderer.template.html
  - app/server.py
---

## Decided
The product Endoluminal variant shows the examples/airway_nav three.js renderer in an iframe, with three.js vendored under prototype/vendor for offline use.
Assets (scene.json, graph.json, airways.bin) are built per case from the airway mask by pipeline/build_endoluminal_web.py into <case>/web and served at /case-web/<case>/<file>.
The iframe is skipped when the URL has debug=1 so existing headless canvas checks stay deterministic; without assets the variant falls back to the canvas renderer.
The endoluminal page auto-routes to the farthest airway leaf when no nodule mesh exists, so a1 mode starts inside.

## Tried and ruled out
Painting the carina in 2D canvas: could not match the mesh render; replaced by the demo renderer.
Software-rasterised fork mesh inside the canvas: geometry and shading were worse than the three.js scene.
CDN import map from the examples: not offline-safe; vendored three r160 locally.

## Watch out
The examples prepare.py had a broken IsoGrid call; the port in pipeline/build_endoluminal_web.py uses the corrected arguments.
Case web assets live outside the repo (inside the case dir); regenerate per case after segmentation.
The demo route is built from its own skeleton graph; the product RouteTarget is a separate lesion-anchored model.

In the code: prototype/renderer.template.html syncEndo3d/iframe, prototype/endoluminal.html, prototype/vendor/{three.module.js,controls/OrbitControls.js}, app/server.py /case-web route, pipeline/build_endoluminal_web.py.
