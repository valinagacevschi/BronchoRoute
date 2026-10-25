# Lung Navigator — Clinical Workflow and Build Plan

Agreed in the 2026-10-25 grilling session (decisions Q1–Q57). This file is the spec that
code reviews check against. No 3D Slicer: every step happens in our app.

## Principles

- **Lesion ≠ RouteTarget.** The Lesion is the clinical target, marked by the clinician on the CT.
  RouteTarget is the endpoint of the airway route that the CT demonstrates. The distance between
  them is `target_gap_mm` and is shown as uncertainty; no airway is ever drawn across it.
- **Provenance everywhere.** Each piece of airway is `ct` (segmented from the CT),
  `interpolated` (a bridge across a ≤ 4 mm gap) or `manual` (a clinician correction). Interpolated
  spans and manual corrections are stored separately from the CT mask.
- **Records as you go.** Every step writes its own record to `case.json` (who, when, version,
  content fingerprint). Nothing waits for a final save.
- **Patient data.** Source DICOM never enters the repo. Cases are NIfTI with empty descriptive
  header fields, a neutral id (`local-NNN`) and a SHA-256 of the SeriesInstanceUID for duplicate
  detection only. Identifiers are never printed, logged or written.
- **One frame for every view.** Bronchoscopist's frame: patient supine, face up, viewer at the
  head looking caudally, anterior at the top of the screen, patient's right on screen right
  (Q43; to be confirmed against the clinicians' bronchoscope screens).

## Workflow (8 steps)

1. **Import and choose the reconstruction.** `import_dicom.py` proposes the thinnest axial,
   non-contrast, standard-kernel CT series (lung/bone kernels, contrast phases, thick slices,
   reformats, scouts and captures are skipped with a reason). The clinician confirms or overrides.
   Recorded: chosen series (folder + SeriesNumber), skipped series with reasons.
2. **Mark the Lesion** — before any airway is shown. Click the lesion centre in the red (axial)
   pane, check and adjust it in the green (coronal) and yellow (sagittal) panes, confirm. Stored as
   one point in patient RAS mm.
3. **Segment automatically.** TotalSegmentator `lung_vessels` → `lung_airways`, union with
   `total` → `trachea`, bridge detached pieces (≤ 4 mm gap, ≥ 20 mm³, 1 mm tube; bridges stored
   in `airway_bridges.nii.gz/.json`), five lobes. No manual RouteStart placement needed later.
4. **Review and correct** in the linked CT panes:
   - confirm or reject each interpolated bridge on the CT;
   - remove branches the CT does not support (v1 corrections are removal-only, each with a note
     on the CT evidence); stored as a separate `manual` layer, never merged silently;
   - resolve review flags (`small_lobe`, `lobe_without_airways`) with a sign-off
     (role + initials, finding, date).
5. **Build model and route.** Graph (centrelines, radii, generations, lobes, names, provenance).
   RouteStart is placed automatically at the trachea (widest terminal); the clinician confirms.
6. **Choose RouteTarget.** The app proposes the top 3 endpoints on CT-seen airway nearest the
   Lesion, ranked by target gap, then fewer generations, then shorter route; each shows gap,
   generation and route length. The clinician picks one or clicks another point, which must lie on
   a CT-seen centreline. A route may cross an interpolated bridge only if that bridge was confirmed
   in step 4; branches reachable only through unconfirmed bridges are shown but not selectable.
7. **Walk the route.** Endoluminal fly-through with all views linked. At each bifurcation the CT
   panes show the same point; the clinician confirms the branch taken. Recorded per bifurcation:
   node, position mm, branch (name + clock position), confirmed yes/no, note, who, when. These
   confirmations are the branch-tracing sequence. A "no" returns the case to step 4 or 6 and the
   record says which.
8. **Sign off and freeze version N.** Remaining uncertainty is listed: target gap, interpolated
   spans on the route, open notes. Any later change starts version N+1 and needs a new sign-off.

## Views

| View | Orientation / behaviour |
|---|---|
| Anatomical 3D | Default camera at the head end, ~45° elevated, looking caudally onto the chest; presets *operator* (0°) and *top-down* (90°); free orbit. |
| Endoluminal | Starts in the trachea with anterior at 12 o'clock; parallel-transport roll as now; an "A" tick always marks anterior (roll is simulated, not measured). |
| Topology map | Trachea at the top, branches downward, patient's right on screen right; legend notes that anterior/posterior depth is not shown. |
| CT panes (new) | Red axial, green coronal, yellow sagittal. Shared crosshair (click in any pane moves the others), wheel scrolls the pane under the cursor, lung and mediastinal window presets, toggleable overlays: airway mask by provenance, route, Lesion, RouteTarget. |

All four views share one current position: picking a branch in 3D, the fly-through camera or a
topology node moves the CT crosshair, and vice versa.

## App

- Local app on this Mac: a Python server runs pipeline steps and owns case records; the browser
  holds the four views and the workflow steps. Replaces per-case single-file HTML.
- CT to the browser: thorax crop (lungs + trachea + 10 mm), native resolution int16, canonical RAS,
  raw binary plus a JSON header (shape, spacing, affine); loaded once per case.
- Identity: the clinician enters role + initials once; saved in a local, gitignored settings file
  and reused across sessions, with a "change clinician" control. Every confirmation, correction and
  sign-off is stamped with it. No login in the MVP.
- Versions: every artifact (CT crop, segmentation, bridges, corrections, graph, route,
  walk-through) gets a version number and SHA-256 fingerprint in `case.json`; sign-off freezes the
  chain.

## Build plan

| Phase | Scope | Decisions |
|---|---|---|
| 0 | `make_prototype_data` resolves `case.json` independent of cwd; A/B `--higher_order_resampling` on AeroPath 1–6, adopt only if branches/generations improve with 8/8 names kept; SNOMED CT codes (from SlicerTotalSegmentator's mapping) on lobes and trachea | Q40 |
| 1 | Local server + browser shell, case list, CT crop endpoint, saved clinician identity, `case.json` records with version chain | Q41, Q49, Q56, Q57 |
| 2 | Supine bronchoscopist frame in the 3D, endoluminal and topology views; 3D camera presets; anterior tick | Q43, Q50–Q52 |
| 3 | CT panes view: panes, crosshair, scroll, windows, overlays, cross-view linking; Lesion marking | Q44–Q46 |
| 4 | Workflow steps in the app: import, segmentation, review with removal-only corrections and bridge confirmation, RouteTarget candidates, walk-through records, sign-off | Q33, Q35–Q39, Q47, Q53–Q55 |

Each phase: Codex brief → code review (standards + spec against this file) → fixes → commit.
Phases 0 and 2 may run beside 1; 3 needs 1's CT endpoint; 4 needs 3.
