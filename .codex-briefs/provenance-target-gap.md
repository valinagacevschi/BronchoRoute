# Brief: provenance tagging, target gap, visible limitations

Repo /Users/val/dev/dicom. `env -u PYTHONPATH uv run ...` from `pipeline/`; tests with
`env -u PYTHONPATH uv run --with pytest pytest`. Do NOT git add/commit.
PATIENT DATA RULE from `.codex-briefs/import-and-review.md` applies (never print/write identifiers
or UIDs; you only need graph JSON and masks here).

**Another agent recently changed `make_prototype_data.py` (argparse, `review_gate`, `source`,
`--allow-review`, sign-offs) and `renderer.template.html` (review banner, `A.source`, reviewed
note), plus `route_frame.py`. Keep all of that intact.** Read the current files first.

Files you may change: `pipeline/run_totalsegmentator.py`, `pipeline/test_bridge.py`,
`pipeline/build_graph.py`, `pipeline/make_prototype_data.py`, `pipeline/route_frame.py` (only if
needed), new `pipeline/test_provenance.py`, `prototype/renderer.template.html`, and regenerated
`pipeline/prototype_data.js` + `prototype/renderer.html` (see Verify). New outputs only under
`ts_out/fallback_6/` (the two new bridge files) and `out/prov/`. Nothing else.

## Why (requirements now in docs/Product Requirements Document_ Lung Navigator.md §6.7, §7.1, §8.3)
- Bridges are interpolated, not CT-demonstrated airway; they must be stored separately and every
  graph edge must carry `provenance`.
- A target beyond the last visible airway: route ends at the last airway sample and the remaining
  distance is stored as `target_gap_mm` and shown as uncertainty; never draw airway across it.
- The endoluminal view must keep its limitations visible.

## Verified facts
- `run_totalsegmentator.py::bridge_airway_components(combined, spacing)` returns
  `(result, bridged, dropped)`; `bridged` = list of (piece volume mm3, gap mm). The tube voxels
  are OR-ed into `result` together with the attached CT pieces, so today the saved
  `airways.nii.gz` cannot tell bridge voxels from CT voxels. `make_airway` saves `result`.
  Tube voxels = `result & ~combined`.
- Measured bridges exist on AeroPath case 6 (several pieces at 1.6–3.6 mm). TotalSegmentator
  outputs for case 6 are cached in `ts_out/fallback_6/`, so rerunning
  `run_totalsegmentator.py data/extracted/AeroPath/6/6_CT_HR.nii.gz -o ts_out/fallback_6` only
  rebuilds the mask (check it does not rerun the model).
- `build_graph.py` has `to_voxel()`, takes `--airways` and `--lobes`, writes
  `<out>/<case>_graph.json` with edges carrying `centreline_mm`, and a `review` list.
- Graph coordinates are RAS patient mm. `make_prototype_data.py` centres on the root node
  (`rel()`), builds `route` to the deepest node and the `fly`/`forks` payload via `route_frame`.

## Task 1 — store bridges separately (run_totalsegmentator.py)
- `bridged` entries gain the bridge endpoints: tree voxel and piece voxel indices, plus radius.
  Keep the existing printout.
- `make_airway` additionally writes, next to the airway mask:
  `airway_bridges.nii.gz` (uint8 mask of tube voxels only = `result & ~combined`, same
  affine/header) and `airway_bridges.json`:
  `{"note": "interpolated, not CT-demonstrated airway", "radius_mm": .., "bridges": [
  {"gap_mm", "piece_volume_mm3", "tree_point_mm": [x,y,z], "piece_point_mm": [x,y,z]}]}`
  (points in world mm via the affine). Write both even when there are zero bridges.
- Update `test_bridge.py` for the new return shape; assert tube-only mask excludes all voxels of
  the input `combined`.

## Task 2 — provenance per edge (build_graph.py)
- New `--bridges PATH`; default: `airway_bridges.nii.gz` beside `--airways` if it exists.
- For each edge: `interpolated_mm` = arc length of the centreline portion whose samples fall in
  bridge voxels (a segment counts when both its end samples are inside; round 0.1).
  `provenance` = `"interpolated"` if `interpolated_mm > 0` else `"ct"`.
  `downstream_of_interpolated` = true when any ancestor edge is interpolated (the branch itself
  may be CT, but it is only reachable through a bridge).
- Graph level: `"provenance_source"`: `"bridge mask <filename>"` or `"no bridge mask"` (then all
  edges `ct`). Print counts: N interpolated edges, total interpolated mm, M downstream edges.
- Edges never get geometry changes from this.

## Task 3 — target gap (make_prototype_data.py)
- New `--target X,Y,Z` (patient mm, RAS, same frame as the graph — not root-relative).
  Without it, behaviour stays exactly as now and the payload has `"poi": null,
  "target_gap_mm": null`.
- With it: find the nearest centreline sample over all edges (full-resolution `centreline_mm`).
  Route = root → that edge; the fly path is truncated at that sample (the route still includes
  the edge; truncate only the resampled fly polyline and drop forks beyond the endpoint).
  `target_gap_mm` = distance POI → endpoint sample (round 0.1); `target_inside_airway` = gap ≤
  that edge's `radius_mm`. Payload adds `"poi": rel(target)`, `"endpoint": rel(sample)`,
  `"target_gap_mm"`, `"target_inside_airway"`. `target` (node id) keeps meaning "last route
  node" so the anatomical/topology variants keep working.
- Provenance in the payload: edges get `"prov"` (`ct` / `interpolated` / `unrecorded` when
  the source graph has no `provenance` field — never silently assume `ct`) and `"down_i"`
  (bool). Payload `"route_interpolated"`: true if any route edge is interpolated.
  `fly` gets `"prov"`: per-sample provenance of the edge it lies on.
- Route card: mark interpolated forks/edges, and final line, e.g.
  `route ends at last airway sample; target 14.2 mm beyond (no airway shown)` or
  `target inside airway (2.1 mm from centreline)`; plus `WARNING: route crosses N interpolated
  segment(s), X mm` when applicable; and `provenance not recorded in this graph` for old graphs.
- Keep pure logic testable: nearest-sample/truncation and provenance mapping as small functions.

## Task 4 — renderer (renderer.template.html)
- Anatomical view: interpolated edges drawn dashed in amber with a legend entry
  "interpolated — not CT airway"; downstream-of-interpolated edges keep their colour but get a
  thin amber outline or reduced opacity (pick one, add it to the legend). If `A.poi`: draw the POI
  marker at `A.poi` and, if `target_gap_mm` > 0 and not inside airway, a thin dashed grey line
  endpoint → POI labelled `<gap> mm · no airway shown`.
- Endoluminal: when the current fly sample is `interpolated`, tint the rings amber and show HUD
  line "Interpolated segment — not CT-demonstrated airway". Near the end of the fly path (last
  10 mm) with a gap: "Target <gap> mm beyond last visible airway". Fork text unchanged otherwise.
- Persistent limitations list in the rail (small text, always visible in all variants, styled with
  the template's existing tokens): CT-derived pre-procedural view, not live tracking; simulated
  steering (roll is not measured scope orientation); CT-to-body divergence; inspiratory CT vs
  procedural lung volume; respiratory motion; atelectasis; patient positioning; incomplete
  distal segmentation. If `A.prov` info says unrecorded, add "Provenance not recorded for this
  graph".
- Must not overlap the existing review banner, HUD, rail content or switcher at 1280×800 and
  1024×700.

## Tests (`test_provenance.py`, pytest, synthetic data only)
1. Edge whose middle samples lie in a bridge mask → `interpolated`, `interpolated_mm` ≈ the
   in-mask length; its child edge → `ct` with `downstream_of_interpolated` true; sibling → `ct`,
   false.
2. No bridge mask → all `ct`, `provenance_source == "no bridge mask"`.
3. Nearest sample/truncation: POI 12 mm lateral of a distal sample → gap ≈ 12, endpoint is that
   sample, fly polyline ends there, forks after it dropped; POI inside a 5 mm-radius edge 2 mm
   off the centreline → `target_inside_airway` true.
4. Graph without `provenance` → payload edges `unrecorded`, never `ct`.
All existing tests (`test_bridge.py test_route_frame.py test_review_gate.py`) must still pass.

## Verify and paste
- py_compile changed .py; full pytest run.
- `run_totalsegmentator.py data/extracted/AeroPath/6/6_CT_HR.nii.gz -o ts_out/fallback_6`
  (cached) → bridge printout + the two new files.
- `build_graph.py data/extracted/AeroPath/6 --airways ts_out/fallback_6/airways.nii.gz --lobes
  ts_out/fallback_6/lobes.nii.gz -o out/prov` → provenance counts. (If it has review flags, use
  `--allow-review` below; do NOT write sign-offs.)
- `make_prototype_data.py out/prov/6_graph.json out/prov/case6.js --html out/prov/case6.html
  [--allow-review] --target <x,y,z>` where the target is 15 mm beyond (along the outward
  tangent) the tip of a distal edge downstream of an interpolated edge if one exists, otherwise
  any distal tip — compute it with a few lines of Python and print the coordinates you chose.
  Paste the route card.
- Regenerate the demo so template and data are in sync:
  `make_prototype_data.py cases/1_graph.json prototype_data.js --html ../prototype/renderer.html`
  (expect `provenance not recorded` for this older graph).
- Playwright (script in $TMPDIR): load `out/prov/case6.html` in all 3 variants and
  `prototype/renderer.html` endoluminal; assert no console errors; screenshots at 1280×800 of
  anatomical (case 6, showing amber edge + POI line), endoluminal on an interpolated sample if the
  route has one, and the rail limitations block. Save under $TMPDIR and print paths.

Short summary at the end: files changed, commands, outputs, screenshot paths.
