# Brief: route camera frame, roll at forks, anterior arrow, fork clock positions

Repo: /Users/val/dev/dicom. Python env: `cd pipeline && env -u PYTHONPATH uv run ...`
(tests: `env -u PYTHONPATH uv run --with pytest pytest`). Do NOT git add/commit. Do NOT touch
docs/, data/, out/, ts_out/, cases/, or any file not listed below. No new runtime dependencies
(numpy is available in the pipeline; the renderer stays library-free plain JS/canvas).

Files you may change/create:
- `pipeline/route_frame.py` (new, pure numpy, no I/O)
- `pipeline/test_route_frame.py` (new, pytest)
- `pipeline/make_prototype_data.py`
- `pipeline/prototype_data.js` (regenerated output)
- `prototype/renderer.template.html`
- `prototype/renderer.html` (regenerated: template with `/*DATA*/` replaced by the contents of
  prototype_data.js — that is exactly how the current file was produced; add a small
  `--html` option or a function in make_prototype_data.py that does this splice so it is reproducible)

## Background (verified facts)
- Graph coordinates are NIfTI world = **RAS**: +x patient right, +y anterior, +z superior.
  Verified on case 1: right-lobe centrelines have mean x ≈ +45, left ≈ −60; right middle lobe
  mean y 185 vs right lower lobe 132. The renderer comment at `renderer.template.html` ~line 241
  ("x right, y posterior, z superior") is wrong: fix it to RAS. Do not change the anatomical-view
  projection maths otherwise.
- `make_prototype_data.py` centres coordinates on the root node, decimates centrelines
  (KEEP_EVERY=3), and computes `route` (list of edge indices root→deepest node) and `target`.
  Edge records in the payload: f, t, g, r, l, lobe, c. Source graph edges also carry
  `airway_name`, `lobe_name`, `centreline_mm` (full resolution) — use the full-resolution
  centrelines for all geometry below, not the decimated ones.
- Endoluminal camera today (`drawEndoluminal`, ~line 323) rebuilds `up` from world `[0,0,1]` every
  frame. In the trachea the view direction is nearly parallel to z, so the frame degenerates and
  the image can spin/flip. This is the bug to fix. Movement is edge index + t, ArrowUp/Down only;
  ←/→ branch picking advertised in the hint is not implemented (remove it from the hint).

## Design (implement exactly)

### A. `route_frame.py` — single source of truth for route geometry
Functions (names are suggestions; keep them small and pure):
1. `resample(polyline_mm, step_mm=1.0)` → points, cumulative arc length `s`. Drop consecutive
   duplicates (< 0.01 mm) first. Input = concatenated full-resolution centrelines of the route
   edges, root→target (drop the shared junction point between consecutive edges). Also return, per
   sample, the route edge index it lies on (so radius/generation can be looked up).
2. `tangents(points, step_mm, look_ahead_mm=6.0)` → unit tangents by central difference over
   ±round(look_ahead/step/2) samples, clamped at ends; if a difference is zero, reuse the previous
   tangent.
3. `transport_frame(tangents, initial_up=(0,1,0))` → `up` per sample. Initial up = patient
   anterior projected onto the plane ⟂ t0; if that projection is < 1e-3 use +z (superior)
   instead. Then parallel transport: rotate previous up by the rotation taking t[i-1]→t[i]
   (Rodrigues about t[i-1]×t[i], angle atan2(|cross|, dot)), re-orthogonalise against t[i],
   renormalise. Convention everywhere: `right = cross(t, up)`.
4. `clock_of(direction, t, up)` → (clock 1..12 or None, angle_deg). Project direction onto the
   plane ⟂ t; if projected length < 0.15 × |direction| return (None, None) ("straight ahead").
   angle θ = atan2(dot(d, right), dot(d, up)) in degrees, normalised to [0, 360);
   clock = round(θ/30) mod 12, with 0 → 12. So 12 = transported up, 3 = right, 6 = down, 9 = left.
   At the start of the trachea (caudal view, anterior up) this gives 12 = anterior, 3 = patient
   right, 9 = patient left, matching the BBT convention.
5. `forks(...)` — for every node on the route that has ≥ 2 child edges in the full graph
   (route nodes excluding the final target): its arc length `s` on the route; the parent
   direction = unit mean of tangents over the last 5 mm before the fork sample; for each child
   edge, the daughter direction = vector from the fork node to the child's centreline point
   at arc length min(5 mm, child length / 2). Compute clock + angle for every child in the
   *transported zero-roll frame at the fork sample* (with t = parent direction, up
   re-orthogonalised to it). Mark which child is on the route. Include child radius, generation,
   airway_name/lobe_name when present.
6. `roll_profile(s, forks, roll_start_mm=10.0, roll_end_mm=3.0)` → roll (radians) per sample.
   Roll starts at 0. At each fork whose chosen-child angle is not None: target = the chosen
   child's angle θ (radians); delta = shortest wrap of (target − locked) into (−π, π]; ease with
   smoothstep x²(3−2x) from s_fork − roll_start_mm to s_fork − roll_end_mm (clamped at ≥ 0);
   hold `locked + delta` afterwards (locked between forks). Applying roll φ means
   `up' = up·cosφ + right·sinφ` (Rodrigues about t), which brings a branch at angle θ to the top
   of the screen when φ = θ. Forks with None angle leave roll unchanged.
7. `pause_points(forks, pause_before_mm=5.0)` → arc lengths max(0, s_fork − 5).
All distances are engineering parameters; keep them as named module constants.

### B. `make_prototype_data.py`
Keep everything that exists. Add to the payload, all coordinates relative to root and rounded to
0.1 mm, unit vectors rounded to 3 decimals, angles to 0.1°:
```
"fly": { "step_mm": 1.0, "s": [...], "p": [[x,y,z]...], "t": [...], "up": [...],
         "roll": [...radians, 4 dp...], "edge": [...route position index...], "r": [...], "g": [...] },
"forks": [ { "n": <fork number from 1>, "node": id, "s": mm, "pause_s": mm,
             "chosen": <edge index into payload edges>, "clock": 1..12|null, "angle": deg|null,
             "name": airway_name or lobe_name or null,
             "children": [ { "edge": idx, "clock": .., "angle": .., "r": .., "on_route": bool, "name": .. } ] } ],
"axes": "RAS"
```
Print a route card to stdout after writing, one line per fork, e.g.
`fork 2 @ 46 mm: take 3 o'clock (right main bronchus); siblings 9 o'clock` — plus a final line
`target node N, route length X mm`. Keep the output file size reasonable (route is short;
1 mm step is fine).

### C. Renderer (`renderer.template.html`, endoluminal variant only)
- Replace edge/t movement with arc-length `fly.s` along `A.fly`. Interpolate p, t, up, roll
  linearly between samples (renormalise t, re-orthogonalise up). Camera basis:
  `up' = rotate(up, t, roll)`, `right = cross(t, up')` — exactly the convention in A.3/A.6, so the
  ring/turn rendering and `toView` use (right, up', t). Remove the world-z `up0` code.
- The forward-looking polyline (`ahead`) comes from `A.fly` samples ahead of `fly.s`
  (radius `r` and generation `g` from the fly arrays), not from edges.
- Movement: ArrowUp/ArrowDown ±0.6 mm per 16 ms tick (today's speed is fine, keep it roughly
  the same). Space toggles auto-play forward at the same speed. `T` resets to s = 0.
- Auto-pause: moving forward across a fork's `pause_s` stops motion (both auto-play and held
  ArrowUp) once per crossing; continuing needs a fresh ArrowUp press or Space. Moving backward
  past it re-arms it. While paused show "Paused before fork n — Space / ↑ to continue".
- HUD (canvas overlay, bottom-left, readable on the dark ground): a fixed dial with 12/3/6/9
  ticks that never rotates; a blue arrow from the dial centre showing patient **anterior**
  (0,1,0) projected into the current camera image plane (screen x = dot(ap, right),
  screen y = dot(ap, up')). If |ap projected| < 0.10, hide the arrow and show
  "Anterior undefined: view along AP axis". Also draw small markers on the dial at the clock
  positions of the next fork's children (chosen one highlighted, siblings dim).
  Text lines: next fork "Fork n in X mm — take C o'clock (name)" (or "straight ahead" when
  clock is null), and a permanent label
  "Simulated steering · CT-derived · not live tracking".
- Readouts (`gen`, `rad`, `togo`, `turns`) derive from `fly.s`: togo = route length − s,
  turns = forks remaining ahead.
- Keep the anatomical and topology variants working unchanged (they read `A.edges`, `A.route`,
  `A.target`, which must remain in the payload).

## Tests (`pipeline/test_route_frame.py`) — must pass
1. Straight vertical tube going caudally (−z): transported up stays anterior (0,1,0) within 1e-6
   for every sample; no NaNs.
2. Synthetic fork: trachea down −z for 50 mm, then two children, one going toward +x (patient
   right, with −z component) and one toward −x. Chosen child (+x) → clock 3; sibling → clock 9.
3. Roll sign: with the frame at that fork and φ = chosen angle, `rotate(up, t, φ)` has dot > 0.95
   with the chosen daughter direction projected onto the plane ⟂ t (branch appears at 12).
4. Helix/curved path: up stays unit length and ⟂ t (|dot| < 1e-6) along the whole path; total
   twist about the tangent between consecutive samples is near zero (no spin).
5. Roll profile: roll is 0 before the first ramp, eases monotonically to the target, stays
   locked until the next ramp; wrap takes the short way (target 350° from 10° → delta −20°).
6. Degenerate: daughter parallel to parent → clock None, roll unchanged across that fork.

## Verification you must do and paste
- `python -m py_compile` on changed .py files; the pytest run above.
- `cd pipeline && env -u PYTHONPATH uv run python make_prototype_data.py cases/1_graph.json prototype_data.js --html ../prototype/renderer.html`
  (or whatever CLI you add) — paste the route card output.
- Headless check of the renderer with Playwright:
  `env -u PYTHONPATH uv run -q --with playwright python <script>` (Chromium may need
  `playwright install chromium`; put any script under $TMPDIR, not in the repo). Load
  `prototype/renderer.html?variant=endoluminal`, assert no console errors/exceptions, hold
  ArrowUp until the first auto-pause and assert the pause text is shown, take screenshots
  at s = 0, at the first pause, and after continuing past it, save them under $TMPDIR and print
  their paths. Also load `?variant=anatomical` and `?variant=topology` and assert no errors.

Finish with a short summary: files changed, commands run, outputs, screenshot paths.
