# Follow-up: provenance / target gap review findings

Same rules and file list as `.codex-briefs/provenance-target-gap.md`; keep the other agent's
review-gate/sign-off/banner work intact. Fix all of these:

1. **Missed bridge (false negative — the exact failure §6.7 forbids).** On case 6, bridge 0
   in `ts_out/fallback_6/airway_bridges.json` (gap 1.6 mm, 109.3 mm3) has edge 24 passing 0.41 mm
   from its midpoint, yet edge 24 is `ct` with `interpolated_mm 0`. Cause: the "both end samples
   inside" rule plus rounding sparse skeleton samples to voxels. Fix in `add_edge_provenance`:
   densify each centreline segment to ≤ 0.25 mm sub-samples, test each sub-sample against the
   bridge mask (world→voxel with the inverse affine, `rint`), and sum the length of sub-segments
   whose midpoint is inside. Any inside sub-sample → `interpolated` (minimum reported length =
   the sub-sample length, never 0 for a tagged edge).
   Add a check you run and paste: for every bridge in the JSON, the edge nearest its midpoint
   (if within radius + 1 mm) is `interpolated`; print one line per bridge. All must pass on case 6.
   Add a unit test where a single sparse sample pair straddles a 1-voxel bridge (neither sample
   inside) → still tagged interpolated.
2. **Whole edges painted amber.** An edge with 1.1 mm interpolated out of ~30 mm is dashed/amber
   along its entire length (anatomical) and the whole fly segment is tinted (endoluminal),
   overstating what is interpolated and hiding where. `build_graph.py` must also export per edge
   `interpolated_spans_mm`: list of [start, end] arc-length intervals along that edge's centreline
   (merged, rounded 0.1). Edge-level `provenance` stays as is. In `make_prototype_data.py`, mark
   interpolation per decimated segment (`ci`: list of segment indices whose arc interval overlaps
   a span; keep the payload small) and per fly sample (`fly.prov` by the sample's arc position on
   its edge, not the whole edge). Renderer: dash/amber only those segments and tint only those
   rings; the HUD warning shows only while on such a sample. The downstream outline stays
   edge-level.
3. **Limitations not visible.** At 1280×800 with the review banner, the rail list is below the
   fold (needs scrolling). Requirement is always visible. Move it out of the rail: a compact,
   always-visible block in the stage's top-right corner (below the banner when shown), small text,
   semi-opaque panel using the template tokens; same items. Remove the rail copy; keep the
   provenance legend next to it (only when the graph has interpolated edges or provenance is
   unrecorded). Must not overlap the switcher or HUD at 1280×800 and 1024×700.
4. **HUD contrast and stacking.** On the light theme the HUD text is nearly invisible over the
   lumen (amber warning on an amber wall; the "Simulated steering" label unreadable). Draw a
   rounded semi-opaque dark panel behind the HUD dial + text block, sized to its content. Stack
   the HUD lines with a single layout (array of lines → y positions) instead of fixed offsets:
   fork line, paused line, interpolated warning, target-gap line, anterior-undefined, steering
   label. Today "Anterior undefined" (bottom − 70) and the interpolated warning (bottom − 70)
   collide.
5. **Cleanup.** `edge_route`: remove the semicolon one-liners and the dead
   `final_edge is not None` check. Graph `"source"` in build_graph says "AeroPath (MIT), expert
   airway mask" even when `--airways` overrides the mask — make it say which mask was used
   (`expert airway mask` vs `airway mask <filename>`), no other change.

Re-run and paste: py_compile, full pytest, the case 6 chain (run_totalsegmentator cached →
build_graph → make_prototype_data with the same `--target` and `--allow-review`), the per-bridge
check from (1), and regenerate the demo (`cases/1_graph.json` → prototype_data.js +
renderer.html). Playwright screenshots at 1280×800 (anatomical case 6 zoomed near an interpolated
span, endoluminal on an interpolated sample, endoluminal on a CT sample) and 1024×700, no console
errors; print paths.
