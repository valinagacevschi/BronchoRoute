# Lessons Learned

- Series selection: ranking by slice step before checking coverage can prefer a partial
  gated reconstruction. Classify eligibility first, use the first convolution-kernel
  value, and rank survivors independently of file discovery order. Keep the header
  inventory regression test to pin the recommendation and rejection reasons.
- Airway skeleton split (GitLab #14): kimimaro's soma handling (thresholds 0) treated the wide
  trachea as a cell body and cut the skeleton; keeping the largest component then dropped the
  left lung (44 % of vertices on the Bf37 test Case). Disable soma handling (thresholds `inf`),
  test graph connectivity and not only mask connectivity, and join any remaining skeleton hole
  only when every sample (≤ 0.25 mm apart) lies inside the airway mask; a real mask gap stays
  the bridge detector's job with `interpolated` provenance.
- Twig root (GitLab #14): the widest terminal can be a 0.8 mm twig when the tracheal cut face
  fans out. Root at the most superior vertex (world RAS z, full affine) with radius ≥ ½ max,
  and check root z above carina z on real data.
- Empty tree (GitLab #14): lung pruning can leave no branches. Stop before export with
  `graph: no airway tree in lung`; the job error names the graph step.
- Real-fixture graph tests (`test_graph_fixes.py`) skip unless `G14_FIXTURE_ROOT` points at a
  writable copy under `$TMPDIR` of the derived series-kernel fixtures (never the originals).
- Topology rotation: drawing and picking can drift if they transform coordinates
  separately. Rotate positions in their shared screen projection; draw glyphs
  without rotating the canvas. Use synthetic graphs and dynamic server ports in
  browser tests so local case availability and other worktrees cannot affect them.
