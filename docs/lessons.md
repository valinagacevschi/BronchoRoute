# Lessons Learned

- Topology rotation: drawing and picking can drift if they transform coordinates
  separately. Rotate positions in their shared screen projection; draw glyphs
  without rotating the canvas. Use synthetic graphs and dynamic server ports in
  browser tests so local case availability and other worktrees cannot affect them.
