# Product Requirements Document: Lung Navigator
## CT-derived airway route planning and bronchial branch tracing

> **Desktop app:** `docs/Desktop App Spec.md` (BronchoRoute) supersedes this document where they conflict: EN-02 auto-roll, GR-05 export gate, Lesion-first gating, sign-off/frozen versions, and the no-extension rule (Estimated airway exception). See its §13.

| Document field | Value |
|---|---|
| **Version** | 2.0 (2026-10-25). Replaces v1.0, which described a cloud learning platform built on DICOMweb. |
| **Status** | Current. `docs/Workflow and Build Plan.md` is the build spec that code reviews check against; this PRD explains the requirements behind it. Where the two differ, the plan wins and this document is corrected. |
| **Primary scope** | Research, education and pre-procedural planning by clinicians, on a local machine |
| **Out of scope** | Real-time bronchoscope localisation, electromagnetic or robotic navigation, diagnosis, treatment decisions, any medical-device claim |
| **Origin** | Re-implements, as one app, the 3D Slicer workflow *CT-based Bronchial Branch Tracing (BBT)* by T. Toma [7] |

---

## 1. Summary

Lung Navigator takes a chest CT and helps a clinician produce a reviewed airway route towards a
peripheral lesion, together with a branch-by-branch tracing sequence for bronchoscopy planning.
Every step happens in one local app. 3D Slicer is not used.

The clinician imports the CT and marks the lesion. The app segments the airways automatically. The
clinician reviews the result against the CT, chooses where the CT-supported route ends, walks the
route in linked CT, 3D, endoluminal and topology views, confirms each bifurcation, and signs off a
frozen version. Every step writes its own record.

> **Purpose boundary.** Output is a CT-derived pre-procedural representation. It must never be
> described as real-time localisation, as a navigation instruction for a live procedure, or as
> evidence of improved diagnostic yield or safety.

Learner-facing game and training modes (the "Explore / Learn" tiers of v1.0) are deferred
(§16).

---

## 2. Principles

These are product rules, not preferences. Each traces to the build plan's Principles section and
to BBT's safety rules [7].

| Principle | Requirement |
|---|---|
| **The CT is the anatomical authority** | Masks, graphs, routes and rendered views are derivatives. None may show more anatomy than the CT demonstrates. A smooth 3D model is not evidence of completeness. |
| **Lesion ≠ RouteTarget** | The Lesion is the clinical target, marked on the CT. RouteTarget is the end of the airway route that the CT demonstrates. The distance between them is `target_gap_mm`, always shown as uncertainty; no airway is ever drawn across it. |
| **Provenance everywhere** | Each piece of airway is `ct`, `interpolated` (a bridge across a gap of 4 mm or less) or `manual` (a clinician correction). Interpolated and manual layers are stored separately from the CT mask and are always visible as such. |
| **Records as you go** | Every step writes its record to `case.json` (who, when, version, content fingerprint). Nothing waits for a final save. |
| **Patient data stays out** | Source DICOM never enters the repository. Identifiers are never printed, logged or written (§11). |
| **One frame for every view** | Bronchoscopist's frame: patient supine, viewer at the head looking caudally, anterior at the top of the screen, patient's right on screen right (to be confirmed against clinicians' bronchoscope screens, §15). |
| **Clinician decides** | Automation proposes. The clinician confirms the series, Lesion, corrections, bridges, RouteStart, RouteTarget, each bifurcation and the final sign-off. |

---

## 3. Users

| User | Job | Notes |
|---|---|---|
| **Clinician (bronchoscopist / respiratory physician)** | Prepares, reviews and signs off a case | Identified by role + initials only (e.g. `Consultant, T.T.`), entered once and saved locally; stamped on every confirmation, correction and sign-off. No login in the MVP. |
| **Developer / research operator** | Runs the pipeline, evaluates segmentation, maintains the app | Works on public data (AeroPath) and de-identified local cases |
| **Learner** | Deferred | See §16 |

---

## 4. Workflow

Eight steps, from the build plan. The *Phase* column refers to the build plan's phases (§14).

| # | Step | System | Clinician | Recorded | Phase |
|---|---|---|---|---|---|
| 1 | **Import and choose the reconstruction** | `import_dicom.py` proposes a series and lists every skipped series with its reason | Confirms or overrides | Chosen series (folder + SeriesNumber, description, kernel, spacing, size, SHA-256 of the SeriesInstanceUID); skipped series with reasons | Import done; in-app step: 4 |
| 2 | **Mark the Lesion**, before any airway is shown | Linked axial (red), coronal (green) and sagittal (yellow) panes | Clicks the lesion centre, adjusts it in the other panes, confirms | One point, patient RAS mm | 3 |
| 3 | **Segment automatically** | TotalSegmentator airways + trachea + five lobes; bridges detached pieces (§6.2) | — | Tool versions, options, outputs with fingerprints | Pipeline done; in-app step: 4 |
| 4 | **Review and correct** | Shows the mask by provenance on the CT, plus review flags | Confirms or rejects each bridge; removes branches the CT does not support (v1 corrections are removal-only, each with a CT-evidence note); signs off review flags | Bridge decisions, `manual` removal layer, sign-offs (role + initials, finding, date) | Sign-off CLI done; in-app step: 4 |
| 5 | **Build model and route** | Builds the graph; places RouteStart at the trachea automatically | Confirms RouteStart | Graph version and fingerprint, RouteStart | Graph done; confirmation: 4 |
| 6 | **Choose RouteTarget** | Proposes the 3 CT-seen endpoints nearest the Lesion (§7) | Picks one, or clicks another point on a CT-seen centreline | RouteTarget, `target_gap_mm`, candidates shown | 4 |
| 7 | **Walk the route** | Endoluminal fly-through with all views linked; pauses before each bifurcation | Confirms the branch taken at each bifurcation | Per bifurcation: node, position mm, branch name + clock position, yes/no, note, who, when. A "no" returns the case to step 4 or 6 and the record says which. | Fly-through done; records: 4 |
| 8 | **Sign off and freeze version N** | Lists the remaining uncertainty: target gap, interpolated spans on the route, open notes | Signs off | Frozen version chain. Any later change starts N+1 and needs a new sign-off. | 4 |

---

## 5. CT import and series choice

| ID | Requirement |
|---|---|
| CT-01 | Import reads DICOM headers without exporting identifiers. Output is NIfTI in canonical RAS with empty descriptive header fields and a neutral case id (`local-NNN`). |
| CT-02 | A series is proposed by rule: thinnest axial, non-contrast, preferred kernel. Scouts, localisers, secondary captures, structured reports, reformats, contrast phases and thick series are skipped with a stated reason. Non-CT DICOM objects are reported by SOP class name, separately from truly unreadable files. |
| CT-03 | Duplicate import is detected by SHA-256 of the SeriesInstanceUID. The UID itself is never stored or printed. |
| CT-04 | The orientation of the volume comes from the DICOM geometry, never from slice order or "start at the top slice". |
| CT-05 | The clinician confirms or overrides the proposal in the app. The record keeps both the choice and the skipped list. |

**Kernel: decided for now, on one patient (§15).** The importer prefers standard/soft kernels
(`STANDARD|SOFT|B30`) and keeps lung/sharp series for comparison only. This was decided on
`local-001`, which has both reconstructions at the same 0.76 × 0.76 × 1.25 mm spacing (the
`LUNG` series covers the thorax only, 291 vs 485 slices). Run through the same pipeline, the
`STANDARD` series found all 8 named structures. The `LUNG` series found 6 and missed the right
main and right middle lobe bronchi. BBT's published guidance recommends the opposite: "a lung or
relatively sharp reconstruction kernel, provided noise does not make the airway lumen difficult to
follow" [7], and its series ranker scores sharp kernels above standard ones. One patient is not
enough to overrule that, so the default should be re-checked as more dual-reconstruction cases
arrive.

---

## 6. Segmentation and airway graph

### 6.1 Segmentation

| ID | Requirement |
|---|---|
| SEG-01 | Airways: TotalSegmentator [8] task `lung_vessels` → `lung_airways`, unioned with `total` → `trachea`. Five lobes from `total` with `--roi_subset`. The `--legacy` option (`lung_vessels_LEGACY` → `lung_trachea_bronchia`) remains a fallback. |
| SEG-02 | Full resolution by default. `--fast` is opt-in only, because distal airway detail is the priority. |
| SEG-03 | `--higher_order_resampling` is not used. The phase 0 A/B run on AeroPath 1–6 improved only 1 of 6 cases and dropped case 6 to 6/8 named structures. Smoother output is not evidence of better anatomy. |
| SEG-04 | Outputs are cached by options, so reruns rebuild only the derived masks. |
| SEG-05 | Global low-HU thresholding and Hessian/vesselness methods are not used for the whole tree. BBT found that thresholding leaks into parenchyma and that Hessian/vesselness methods were not reliable for airways [7]. |

### 6.2 Bridges (interpolation)

| ID | Requirement |
|---|---|
| BR-01 | The largest 26-connected component is the tree. Detached pieces of at least 20 mm³ whose gap to the tree is 4 mm or less are joined by a 1 mm-radius straight tube, greedily and nearest first. Everything else is dropped. The final mask must be one component, or the run fails. |
| BR-02 | Tube voxels are written separately as `airway_bridges.nii.gz`, with `airway_bridges.json` recording each bridge's endpoints (world mm), gap, piece volume and radius. The CT-derived pieces stay `ct`. |
| BR-03 | Bridges are proposals. In step 4 the clinician confirms or rejects each one on the CT. A rejected bridge removes the piece beyond it from routing. |
| BR-04 | A bridge is never described or displayed as a demonstrated bronchus. BBT allows bridges of up to 15 mm with explicit authorisation [7]; this product is stricter (4 mm automatic proposal, clinician confirmation). |

### 6.3 Graph

| ID | Requirement |
|---|---|
| GR-01 | Mask → kimimaro skeleton (loop-free, radius per vertex) → tree graph. Coordinates stay in patient RAS mm throughout. Any conversion to screen space is the renderer's job. |
| GR-02 | Root = widest terminal vertex (the cut end of the trachea). Position heuristics are not used: the mask may not reach the top of the trachea. |
| GR-03 | Branches carry generation, length, radius, full-resolution centreline, lobe (from the lobe masks, or inferred when a branch's whole subtree drains one lobe) and names. Named structures come from topology and lobe drainage: trachea, main bronchi, bronchus intermedius and the five lobar bronchi. SNOMED CT codes for lobes and trachea are added in phase 0. |
| GR-04 | Provenance per edge: `provenance` (`ct` / `interpolated`), `interpolated_mm`, `interpolated_spans_mm` (arc-length intervals along the edge) and `downstream_of_interpolated`. Detection samples the centreline every 0.25 mm or less against the bridge mask. Graph-level `provenance_source` names the bridge mask or states `no bridge mask`. A graph written before provenance existed is shown as `unrecorded`, never assumed `ct`. |
| GR-05 | Review flags: `small_lobe` and `lobe_without_airways`. Learner-facing export is refused while flags are unresolved, unless a clinician sign-off (role + initials, finding, date) resolves them. Flags are recomputed on every build, so a changed flag needs a new sign-off. |
| GR-06 | Display smoothing is display-only. It is never used for centreline extraction, routing or measurement. |

Graph JSON (current fields):

```json
{
  "case": "6", "coordinate_system": "patient_physical_mm", "source": "airway mask airways.nii.gz",
  "voxel_spacing_mm": [0.684, 0.684, 0.5], "root_node_id": 0,
  "provenance_source": "bridge mask airway_bridges.nii.gz", "review": [],
  "nodes": [{"id": 0, "position_mm": [0, 0, 0], "radius_mm": 9.9}],
  "edges": [{"from": 0, "to": 1, "generation": 0, "length_mm": 46.6, "radius_mm": 9.9,
             "lobe": null, "lobe_name": null, "lobe_source": null, "lobe_confidence": 0.0,
             "airway_name": "trachea", "centreline_mm": [[0, 0, 0]],
             "provenance": "ct", "interpolated_mm": 0.0, "interpolated_spans_mm": [],
             "downstream_of_interpolated": false}]
}
```

---

## 7. RouteTarget and route

| ID | Requirement |
|---|---|
| RT-01 | Candidates are the 3 endpoints on CT-seen airway nearest the Lesion. They are ranked by `target_gap_mm`, then fewer generations, then shorter route. Each shows its gap, generation and route length. |
| RT-02 | The clinician picks a candidate or clicks any other point, which must lie on a CT-seen centreline. |
| RT-03 | A route may cross an interpolated bridge only if that bridge was confirmed in step 4. Branches reachable only through unconfirmed bridges are shown but cannot be selected. |
| RT-04 | The route runs from RouteStart to RouteTarget along the tree, truncated at the exact RouteTarget sample. `target_gap_mm` is the distance from Lesion to RouteTarget. If the Lesion lies within the local airway radius, it is recorded as "inside airway". |
| RT-05 | Output language never says "best" or "optimal" route. The ranking order is explained by its three criteria. |

Interim implementation: `make_prototype_data.py --target X,Y,Z` routes to the single nearest
centreline sample and reports `target_gap_mm`. Phase 4 replaces it with RT-01 to RT-03.

---

## 8. Views

All four views share one current position: picking a branch in 3D, moving the fly-through camera
or clicking a topology node moves the CT crosshair, and vice versa.

| View | Requirement |
|---|---|
| **CT panes** | Red axial, green coronal, yellow sagittal. Shared crosshair (a click in any pane moves the others); the wheel scrolls the pane under the cursor; lung and mediastinal window presets; toggleable overlays: airway mask by provenance, route, Lesion, RouteTarget. |
| **Anatomical 3D** | Default camera at the head end, about 45° elevated, looking caudally onto the chest; presets *operator* (0°) and *top-down* (90°); free orbit; orientation gizmo. Interpolated spans are dashed amber; branches reachable only through a bridge are marked; the Lesion→RouteTarget gap is a dashed line labelled "no airway shown". |
| **Endoluminal** | See §8.1. |
| **Topology map** | Trachea at the top, branches downward, patient's right on screen right; legend notes that anterior/posterior depth is not shown. |

### 8.1 Endoluminal orientation aids

| ID | Requirement |
|---|---|
| EN-01 | **Camera frame.** The camera follows the route centreline with a parallel-transported (minimal-twist) frame that starts with patient anterior as "up". Curvature alone never rolls the image. Route geometry is computed once in the pipeline (`route_frame.py`) and the renderer only reads it. |
| EN-02 | **Simulated steering.** Approaching a bifurcation, the camera rolls so the chosen daughter branch reaches 12 o'clock, pauses, then advances (roll → deflect → advance). Roll is locked between bifurcations; closely spaced forks chain their rolls without jumps. The roll is labelled *simulated*: CT cannot determine shaft torsion, hand rotation, looping or real camera orientation. |
| EN-03 | **Clock positions** are measured in the transported frame at each fork: 12 = frame up (anterior at the trachea), 3 = right, 6 = down, 9 = left. They are derived from the segmented bifurcation, never from the route curve alone. A daughter almost parallel to its parent is "straight ahead". |
| EN-04 | **HUD.** A fixed dial that never rotates, with markers for the next fork's daughters at their current on-screen angle; an anterior arrow, hidden with a warning when the view runs along the anterior–posterior axis; next-fork text; a pause notice; an "Interpolated segment" warning on bridged spans; and the target-gap notice near the end. The HUD sits on a dark panel that stays readable on both themes. |
| EN-05 | **Engineering parameters.** Look-ahead 6 mm, roll start 10 mm and roll end 6 mm before a fork, pause 5 mm before. BBT's published script uses 8 / 2 / 2 mm [7]. None of these are validated clinical values, and none may be presented as such. |
| EN-06 | **Limitations** stay visible in every view: CT-derived pre-procedural view, not live tracking; simulated steering; CT-to-body divergence; inspiratory CT vs procedural lung volume; respiratory motion; atelectasis; patient positioning; incomplete distal segmentation; and "provenance not recorded" for older graphs. |
| EN-07 | Any future recording or video export uses the same camera function as interactive navigation. |

---

## 9. Walk-through, branch tracing and sign-off

| ID | Requirement |
|---|---|
| WK-01 | At each bifurcation the fly-through pauses and the CT panes show the same point. The clinician confirms the branch taken (yes/no + note). |
| WK-02 | The confirmations, in order, are the branch-tracing sequence: node, position, branch name, clock position, decision, note, who, when. |
| WK-03 | A "no" returns the case to step 4 (segmentation) or step 6 (RouteTarget), and the record says which. |
| WK-04 | Sign-off lists the remaining uncertainty (target gap, interpolated spans on the route, open notes) and freezes version N. Any later change starts N+1. |

---

## 10. App

| ID | Requirement |
|---|---|
| APP-01 | Local app on the clinician's Mac. A Python server runs the pipeline steps and owns case records; the browser holds the four views and the workflow steps. This replaces the per-case single-file HTML prototypes. |
| APP-02 | CT to the browser: thorax crop (lungs + trachea + 10 mm), native resolution, int16, canonical RAS, raw binary plus a JSON header (shape, spacing, affine), loaded once per case. |
| APP-03 | Identity: role + initials, saved in a local gitignored settings file, with a "change clinician" control. No login in the MVP. |
| APP-04 | Versions: every artifact (CT crop, segmentation, bridges, corrections, graph, route, walk-through) gets a version number and a SHA-256 fingerprint in `case.json`. Sign-off freezes the chain. |
| APP-05 | Pipeline paths resolve independently of the working directory (phase 0). |

---

## 11. Patient data and governance

| ID | Requirement |
|---|---|
| PD-01 | Never print, log, write or embed PatientName, PatientID, PatientBirthDate, InstitutionName, AccessionNumber, ReferringPhysicianName, StudyDate, or any UID, in any file, stdout or test fixture. Allowed series facts: SeriesNumber, SeriesDescription, Modality, SOP class name, ConvolutionKernel, SliceThickness, spacing, size, plane, file count. |
| PD-02 | Cases are NIfTI with neutral ids. Source DICOM, extracted data and pipeline outputs are gitignored (`pipeline/data/`, `pipeline/ts_out/`, `pipeline/out/`). |
| PD-03 | Reviewer identity is role + initials only. Anything that looks like a full name is rejected. |
| PD-04 | Screenshots shared outside the machine must have no burned-in identifiers. |
| PD-05 | Use of any clinical case follows the applicable research, information-governance and ethics approvals. This document does not replace them. |

---

## 12. Quality and acceptance

### 12.1 Development data and measures

- **AeroPath** [9] cases 1–6 (MIT): CT plus expert airway masks; used for regression and
  segmentation comparison. TotalSegmentator `lung_airways` on case 1: Dice 0.858 against the expert
  mask before trachea union and bridging; the upper trachea was missing until the `trachea` class
  was added.
- **Naming completeness gate:** a case passes when all 8 named structures are found: trachea,
  right/left main bronchus, right upper/middle/lower lobe bronchus, left upper/lower lobe
  bronchus. Failing cases rerun with `--legacy`, then go to manual review. There is no
  region-growing fallback.
- **Local cases:** de-identified, imported with `import_dicom.py`; review flags must be signed off
  before learner-facing export.

### 12.2 Automated checks

Unit tests (pytest, synthetic data only) cover bridging geometry and connectivity, the review
gate and sign-off validation, route frame and fork clock maths (including roll sign and wrap), and
provenance detection (including bridges missed between sparse samples), target-gap truncation and
`unrecorded` provenance. Renderer changes are checked headlessly (no console errors; layout at
1280×800 and 1024×700) with screenshots reviewed by a person.

### 12.3 Acceptance per step

| Step | Acceptance |
|---|---|
| Import | Proposed series and every skipped series carry a reason; no identifier appears in any output |
| Lesion | One point reproduces in all three panes within a voxel |
| Segmentation | Single connected mask; bridges listed separately; 8/8 names on AeroPath regression |
| Review | Every bridge has a decision; every removal has a note; every flag is resolved or blocks export |
| Route | RouteTarget lies on CT-seen centreline; `target_gap_mm` recorded; no unconfirmed bridge on the route |
| Walk-through | Every bifurcation on the route has a decision record |
| Sign-off | Version chain complete with fingerprints; later edits create N+1 |

---

## 13. Non-functional requirements

| Area | Requirement |
|---|---|
| Reproducibility | The same CT, tool versions, options and clinician inputs yield the same graph, route and fork clocks. |
| Performance | Segmentation runs in the background with visible status; CT panes and the fly-through stay interactive. Cached TotalSegmentator outputs avoid model reruns. |
| Accessibility | Keyboard control for flying (↑/↓, Space, T), readable contrast on both themes, and status cues that never rely on colour alone. |
| Versioning | Never mutate a signed-off artifact; supersede it. |
| Hardware | Apple Silicon Mac (TotalSegmentator on `--device mps`). |

---

## 14. Build plan and status

From `docs/Workflow and Build Plan.md`. Each phase: brief → coding agent → code review against the
plan → fixes → its own commit.

| Phase | Scope | Status (2026-10-25, late) |
|---|---|---|
| — | Pipeline: fallback segmentation, bridging, graph, naming, review gate and sign-off, provenance, route frame, prototype renderer | Done (committed) |
| 0 | Path resolution, higher-order-resampling A/B (not adopted), SNOMED codes | Done |
| 1 | Local server, browser shell, case list, CT crop endpoint, clinician identity, `case.json` version chain | Done |
| 2 | Bronchoscopist frame in 3D / endoluminal / topology, camera presets, anterior tick | Done |
| 3 | CT panes, crosshair, windows, overlays, cross-view linking, Lesion marking | In progress |
| 4 | Workflow steps in the app: import, segmentation, review (removal-only corrections, bridge confirmation), RouteTarget candidates, walk-through records, sign-off | Briefed (4a: workflow jobs) |

---

## 15. Open decisions

| Decision | Why it matters | Owner | Current position |
|---|---|---|---|
| **Reconstruction kernel** | Standard chosen on one patient (8/8 vs 6/8 named structures); BBT recommends lung/sharp (§5) | Clinicians | Keep standard; repeat the comparison on every new case that has both reconstructions, and tell Toma about the result |
| **Screen frame** | All views assume anterior up and patient right on screen right | Clinicians | Confirm against a real bronchoscope screen |
| **Roll and pause parameters** | Ours 10 / 6 / 5 mm vs BBT 8 / 2 / 2 mm | Clinicians | Keep ours until clinicians try both |
| **Fork clock validation** | Automatic clock positions have no clinical ground truth yet | Clinicians + developer | Compare with a clinician's hand-marked bifurcation clocks on one case |
| **Corrections beyond removal** | v1 corrections are removal-only | Clinicians | Revisit after v1 use |
| **Bridge limits** | 4 mm / 20 mm³ are engineering limits | Clinicians | Keep; review against confirmation decisions |
| **Learner modes** | Deferred tiers (§16) | Product | After the planning workflow is validated |

---

## 16. Deferred from v1.0

Dropped from the MVP, with reasons:

- **Cloud platform with a DICOMweb archive, OHIF/Cornerstone viewer, job queue and multi-role
  access control.** Replaced by a local app with role + initials identity. Revisit only if
  multi-site or shared use is needed.
- **Explore / Learn / Validate tiers, game modes, learner telemetry and route-recall assessment.**
  Not started. Any future learner use must keep §2 and §8.1 intact and use only signed-off cases.
- **Weighted route-ranking score.** Replaced by the three-criterion ordering in §7.
- **Tracheal seed proposal.** Not needed: TotalSegmentator plus widest-terminal rooting replaced
  it.

---

## 17. References

[4] [Reynisson, P. J., et al. *Airway Segmentation and Centerline Extraction from Thoracic CT.*](https://pmc.ncbi.nlm.nih.gov/articles/PMC4676651/)

[5] [Epstein, R. A., Patai, E. Z., Julian, J. B., & Spiers, H. J. *The cognitive map in humans: Spatial navigation and beyond.*](https://pmc.ncbi.nlm.nih.gov/articles/PMC6028313/)

[6] [Vieira, L. M. N., Camargos, P. A. M., & Ibiapina, C. da C. *Bronchoscopy simulation training in the post-pandemic world.*](https://pmc.ncbi.nlm.nih.gov/articles/PMC9064612/)

[7] Toma T. *CT-based Bronchial Branch Tracing (BBT): CT-derived virtual bronchoscopy and bronchial branch tracing workflow.* Version 1.0.0. 2026. Zenodo. [doi:10.5281/zenodo.23190857](https://doi.org/10.5281/zenodo.23190857). Repository: <https://github.com/ttoma1068/CT-Bronchial-Branch-Tracing> (Apache-2.0).

[8] [TotalSegmentator](https://github.com/wasserth/TotalSegmentator): Wasserthal J., et al. *TotalSegmentator: Robust Segmentation of 104 Anatomic Structures in CT Images.* Radiology: Artificial Intelligence, 2023.

[9] [AeroPath](https://github.com/raidionics/AeroPath): airway segmentation benchmark dataset with challenging pathology (MIT licence).

References [1]–[3] of v1.0 (DICOMweb, OHIF, Cornerstone) were removed with the cloud architecture. The
remaining numbers are kept so that existing citations elsewhere stay valid.

---

## Appendix A — Terms

| Term | Definition |
|---|---|
| **Lesion** | Clinical target point marked by the clinician on the CT, in patient RAS mm |
| **RouteStart** | Route origin in the trachea, placed automatically at the widest terminal and confirmed by the clinician |
| **RouteTarget** | End of the airway route that the CT demonstrates; lies on a CT-seen centreline |
| **target_gap_mm** | Distance from Lesion to RouteTarget; shown as uncertainty, never bridged by drawn airway |
| **Bridge** | Straight 1 mm tube joining a detached airway piece across a gap of 4 mm or less; `interpolated`, stored separately |
| **Provenance** | Origin of a piece of airway: `ct`, `interpolated`, `manual`, or `unrecorded` for older graphs |
| **Fork clock** | Direction of a daughter branch in the transported camera frame at a bifurcation (12 = anterior at the trachea) |
| **Branch-tracing sequence** | Ordered bifurcation confirmations from the walk-through |
| **Version N** | Frozen, signed-off chain of artifacts for a case |
