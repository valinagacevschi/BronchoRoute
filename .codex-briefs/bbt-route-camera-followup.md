# Follow-up: review findings on the route camera change

Same rules as the original brief (no commits, only the same files). Fix all of these:

1. **HUD hidden behind the left rail.** Screenshots `route_s0.png` / `route_pause.png` show the dial
   (cx=78) and the text lines starting at x=12/120 sit underneath `<aside class="rail">`, so the
   dial is invisible and the fork text is cut ("…ke 3 o'clock"). Position the HUD inside the
   visible stage: compute the rail's right edge from `document.querySelector(".rail")
   .getBoundingClientRect()` (canvas CSS pixels) and lay the dial + text out to the right of it,
   above the bottom switcher (`#switcher`) — or put it bottom-right; just make it fully visible at
   1280×800 and at 1024×700, never overlapping the rail or the switcher.
2. **Blue starburst in the lumen.** The dashed guide ring is stroked with whatever `lineWidth`
   the last cartilage ring left (px*0.05 → huge), so it renders as a starburst. Set
   `ctx.lineWidth = 1.25` (as the original code did) before the dashed ring. Also place the guide
   ring at the next fork's chosen branch — the fly sample at `next.s + 5` mm when it lies within
   the ahead window — instead of the fixed index 20; fall back to index 20 if there's no fork ahead.
3. **Auto-pause is skipped by key auto-repeat.** Holding ArrowUp fires repeated `keydown`
   events, and any ArrowUp keydown clears `pausedFork`, so a held key passes the pause after
   the OS repeat delay. Only a non-repeat keydown (`!e.repeat`) may clear the pause.
4. **Dial markers must be in screen frame.** The dial is screen-fixed and the camera is rolled, so
   draw each child marker at screen angle = (child.angle − current roll in degrees) — then as the
   roll eases the chosen marker visibly moves to 12. Keep the text as the route-card clock but
   phrase it "Fork n in X mm — branch at C o'clock (name), rolling to 12" (or "straight ahead").
5. **Roll ramp overlap discontinuity** (`route_frame.roll_profile`). Case 1 has forks 3–4 mm
   apart (153/157/161, 169/172/175/178, 194/196 mm). With roll_start 10 mm and roll_end 3 mm the
   next fork's ramp starts before the previous one ends, overwrites it, and the roll jumps at the
   ramp start. Rule: each fork's ramp starts at max(s_fork − roll_start_mm, previous fork's ramp
   end) and ends at max(that start, s_fork − roll_end_mm); if start == end the roll steps at that
   sample. Add a test: forks at s=20 (angle 90) and s=24 (angle 180), step 1 mm → roll at s=17
   equals radians(90) (first target reached), the second ramp runs from 17 to 21, and
   no consecutive-sample change exceeds the second fork's delta.
6. **Style.** Both `route_frame.py` and the renderer changes are written as dense one-liners with
   semicolons. Rewrite them in the style of the surrounding code (`build_graph.py`, the original
   renderer): one statement per line, short docstrings/comments explaining the why (frame
   convention, roll sign, pause re-arming), no behaviour change beyond the fixes above.
   In `make_prototype_data.py`: drop the odd `ft, fu = tangents(...), None` line, use
   `pause_points()`/`PAUSE_BEFORE_MM` instead of the hard-coded `5.`; in `forks()` remove the unused
   `sample` variable and the unused `edge_ids` parameter.

Then rerun and paste: py_compile, pytest, the make_prototype_data command with `--html`, and the
Playwright check (also hold ArrowUp for 2 s past the first pause and assert it is still paused;
release and press again to continue). Save fresh screenshots (s=0, first pause, mid-roll before
fork 1, after continuing) under $TMPDIR at 1280×800 and print the paths.
