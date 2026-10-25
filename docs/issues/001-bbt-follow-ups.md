# Issue 001 — BBT follow-ups outside the build phases

| Field | Value |
|---|---|
| Status | Open |
| Opened | 2026-10-25 |
| Owner | Val |
| Related | PRD §5, §7, §8.1, §15; `docs/Workflow and Build Plan.md` phase 4; BBT v1.0.0 (doi:10.5281/zenodo.23190857) |

Open items left after adapting T. Toma's BBT workflow. None of them belongs to a build phase
(0–4). Items 5 and 6 depend on phase 4.

## 1. Send the message to Toma

The draft is in the 2026-10-25 session. Before sending, decide whether the `local-001` kernel
result may be shared under the local case's governance; if not, drop that paragraph or describe it
in general terms.

- [ ] Message sent

## 2. Act on Toma's replies

Each answer may change code or the PRD.

- [ ] **Screen frame.** He confirms (or corrects) the bronchoscopist's frame: patient supine,
  operator at the head, anterior at the top of the screen, patient's right on screen right,
  ideally with a de-identified bronchoscope screenshot. If it differs, every view's frame
  changes (plan Principles; PRD §2, §8).
- [ ] **Fork-clock ground truth.** He sends hand-placed bifurcations with clock positions for one
  case (a route card or Slicer markups). Compare them with our automatic clocks from
  `pipeline/route_frame.py` on the same case and record the agreement in the PRD (§15
  "Fork clock validation").
- [ ] **Roll and pause timing.** Ours: roll from 10 mm to 6 mm before a fork, pause at 5 mm
  (`ROLL_START_MM`, `ROLL_END_MM`, `PAUSE_BEFORE_MM` in `route_frame.py`). His v7 script uses
  8 / 2 / 2 mm. Adopt whichever he prefers; update PRD EN-05.
- [ ] **Kernel experience.** Whether sharp kernels work better for him with TotalSegmentator.
  Feeds item 3.
- [ ] **Test run.** Once phase 4 works, he tries a case and reports where it misleads.

## 3. Re-check the kernel default on new cases

The importer prefers standard kernels (`STANDARD|SOFT|B30`). The evidence is one patient: on
`local-001` (both series at 0.76 × 0.76 × 1.25 mm), STANDARD named 8/8 structures and LUNG 6/8,
missing the right main and right middle lobe bronchi. BBT recommends lung/sharp kernels.

- [ ] For every new case that has both reconstructions, run both through the pipeline and record
  named structures, edges and maximum generation side by side.
- [ ] After 3 or more cases, decide: keep standard, switch, or offer both with reasons. Update
  PRD §5 and §15 and `import_dicom.py`.

## 4. Fix the two weak markings in the anatomical 3D view

Found reviewing case 6 (bridges present):

- The amber outline on branches reachable only through a bridge (`downstream_of_interpolated`)
  dominates the tree; most of the left lower lobe turns amber.
- The dashed Lesion→RouteTarget gap line and its "no airway shown" label are short and faint.

- [ ] Design pass (e.g. thinner outline or a hatch, a stronger gap line with an end marker),
  checked with screenshots of case 6 at 1280×800 and 1024×700.
- [ ] Do it after phase 3 lands, so it doesn't collide with the CT-panes work in
  `prototype/renderer.template.html`. Code via Cursor; review against PRD §8.

## 5. Replace the interim target routing (check during phase 4 review)

`make_prototype_data.py --target X,Y,Z` routes to the single nearest centreline sample. Phase 4
must replace it:

- [ ] Top-3 RouteTarget candidates ranked by `target_gap_mm`, then fewer generations, then
  shorter route (PRD RT-01).
- [ ] Clinician pick, or a click that must lie on a CT-seen centreline (RT-02).
- [ ] Routes cross only clinician-confirmed bridges; branches beyond unconfirmed bridges are shown
  but not selectable (RT-03).

## 6. Feed fork clocks into the walk-through records (check during phase 4 review)

- [ ] Phase 4's per-bifurcation record (node, position, branch name, clock, decision, note, who,
  when) uses the fork clocks from `route_frame.forks()`, not a second implementation.

## Done when

All boxes are ticked or explicitly dropped with a reason, and the PRD reflects the outcome.
