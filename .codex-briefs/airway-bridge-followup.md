Review findings on make_airway bridging. Fix all, do not commit.

1. The tube is wrong. `dist2 += ((g - start[axis]) * spacing[axis]) ** 2` is the squared distance from the
   bounding-box corner `start`, not from the tree->piece segment. It paints a 1 mm ball at the box corner
   (often detached from everything) and no tube. Compute, for each voxel in the box, the mm distance to
   the segment [tree, piece] (project onto the segment with t clipped to [0,1], all in mm using spacing)
   and set voxels with distance <= BRIDGE_RADIUS_MM. Keep the rasterised centreline as a connectivity
   guarantee.
2. The final "keep largest component" fallback hides bugs like (1). Remove it. If the result is not a
   single 26-connected component after bridging, raise with the component sizes. It must pass on
   cases 1-6.
3. `nib.save` runs before the final check, so the saved mask can differ from the asserted one. Save
   only after the connectivity check.
4. Add a small unit test `pipeline/test_bridge.py` (pytest, run with
   `env -u PYTHONPATH uv run --with pytest pytest test_bridge.py`): synthetic volume with two blobs
   3 mm apart at anisotropic spacing (0.7,0.7,0.5) -> make_airway-style bridge gives one component and
   the tube voxels lie within radius of the segment; a blob 6 mm away is dropped; a blob under
   20 mm3 is dropped. Refactor make_airway so the bridging core is a pure function on arrays to make
   this testable.

Verify: pytest passes; rerun run_totalsegmentator.py for cases 1..6
(`-o ts_out/fallback_<n>`, all cached) and paste the bridged/dropped/retained lines per case.
Short summary.
