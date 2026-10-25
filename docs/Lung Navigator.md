# PRD: Lung Navigator

A First-Person Breathing Labyrinth for Airway Mental Mapping and Targeted Bronchial Navigation Training


| Field             | Value                                               |
| ----------------- | --------------------------------------------------- |
| Product Name      | Lung Navigator                                      |
| Version           | 1.0                                                 |
| Status            | Draft                                               |
| Owner             | Product / Engineering Lead                          |
| Last Updated      | 2026-08-22                                          |
| Classification    | Internal Product Spec                               |
| Regulatory Intent | Training / Simulation Only — Not for Diagnostic Use |


---



## Table of Contents

1. Executive Summary
2. Problem Statement and Opportunity
3. Product Vision
4. Goals, Non-Goals, and Success Metrics
5. Personas and Use Cases
6. Core Product Principles
7. Product Scope
8. High-Level User Journey
9. Functional Requirements
10. System Architecture
11. Deep Dive Implementation Plan
12. Data Model and Schemas
13. API and Service Design
14. UX/UI Specification
15. Game Engine Specification
16. DICOM and Medical Imaging Pipeline
17. Route Planning and Targeting Algorithm
18. Training and Neuroscience Module
19. Recording, Playback, and Assessment
20. Performance Budgets and Technical Constraints
21. Security, Privacy, and Compliance
22. Quality Assurance and Validation
23. Risks and Mitigations
24. Roadmap and Milestones
25. Innovation Backlog: “10x / Outrageous” Extensions
26. Open Questions
27. Appendix: Key Algorithms and Implementation Notes

---



# 1. Executive Summary

Lung Navigator is a first-person breathing labyrinth game and training simulator set inside the lungs of a sleeping giant. The player navigates branching bronchial corridors that expand and contract with respiration. The system can generate airway maps from real patient-specific DICOM CT scans, allow a clinician or trainee to select a target point of interest such as a tumor, compute the best bronchial route to that target, and then train the user to navigate that route from memory.

The product combines:

- A real-time ray-marched signed-distance-field renderer.
- A patient-specific airway graph extracted from CT imaging.
- A target-selection workflow using DICOM browsing.
- A route planner that finds the closest feasible airway path to a lesion.
- A neuroscience-informed training protocol for building allocentric airway mental maps.
- Optional advanced features such as breathing dynamics, trauma modeling, spatial audio, and AI-assisted tutoring.

The result is a bridge between:

- an atmospheric exploration game,
- a cognitive anatomy trainer,
- and a pre-procedural bronchoscopy planning sandbox.

---



# 2. Problem Statement and Opportunity



## 2.1 Problem

Bronchoscopy and endobronchial navigation require clinicians to build strong mental maps of branching airway anatomy. This is difficult because:

- Bronchial trees are highly branching and visually repetitive.
- Endoscopic views are narrow, local, and egocentric.
- Trainees often learn route knowledge but fail to build flexible map-like knowledge.
- Real procedural practice is expensive, invasive, and risk-bearing.
- Existing simulators are often expensive, rigid, or disconnected from patient-specific anatomy.



## 2.2 Opportunity

A low-cost, browser-based, game-like trainer can help users:

- internalize airway topology,
- practice target-oriented navigation,
- understand patient-specific anatomy from CT,
- rehearse routes before procedures,
- improve spatial memory through active navigation and retrieval practice.



## 2.3 Unique Angle

The “sleeping giant” metaphor transforms anatomical navigation into an embodied experience:

- the airway tree is alive,
- breathing changes corridor size,
- the body reacts to trauma,
- the target is not abstract but a mission objective.

This makes training more memorable than traditional anatomical diagrams.

---



# 3. Product Vision



## 3.1 One-Sentence Vision

Lung Navigator turns patient-specific airway anatomy into a living, navigable labyrinth that trains spatial mastery for bronchial navigation.

## 3.2 Long-Term Vision

The platform evolves into:

- a serious-game bronchoscopy trainer,
- a patient-specific pre-procedural rehearsal tool,
- a research platform for spatial cognition in medical navigation,
- a foundation for future 3D virtual bronchoscopy and haptic simulation.



## 3.3 Product Pillars

1. Anatomically Grounded
  - Built from real airway structure, not arbitrary mazes.
2. Cognitively Effective
  - Designed around active navigation, landmarks, spaced retrieval, and error-based learning.
3. Technically Efficient
  - Runs in the browser using modern graphics and imaging pipelines.
4. Clinically Meaningful
  - Supports target selection and route planning, not just exploration.
5. Experientially Memorable
  - The breathing giant metaphor creates emotional salience and retention.

---



# 4. Goals, Non-Goals, and Success Metrics



## 4.1 Product Goals



### Primary Goals

- Allow browsing of DICOM CT data and selection of a target point.
- Extract an airway graph using air-intensity segmentation from the trachea downward.
- Compute ranked routes from the trachea to the selected target.
- Render a playable first-person breathing labyrinth.
- Train spatial memory through mission-based navigation.
- Record and replay user paths for review and assessment.



### Secondary Goals

- Support procedural generation for cases without DICOM data.
- Provide multiple training modes.
- Support import/export of airway graphs.
- Create a foundation for future 3D rendering and haptics.



## 4.2 Non-Goals

The product is not:

- a diagnostic medical device,
- a replacement for clinical planning systems,
- a certified bronchoscopy simulator,
- a tool for autonomous diagnosis,
- a real-time intraoperative navigation system.

Unless future regulatory work is performed, the system is for education, training, and research only.

## 4.3 Success Metrics



### Product Metrics


| Metric                          | Target                                          |
| ------------------------------- | ----------------------------------------------- |
| DICOM series load success       | ≥ 95% of supported chest CT protocols           |
| Airway graph generation success | ≥ 90% of clean inspiratory chest CTs            |
| Route generation success        | ≥ 95% when graph exists and target reachable    |
| Game playable frame rate        | ≥ 45 FPS at 1080p on mid-range GPU              |
| Session completion rate         | ≥ 70% for guided tutorial                       |
| Training retention improvement  | ≥ 20% relative improvement over baseline recall |




### Training Metrics

- Time to target.
- Number of wrong turns.
- Number of wall collisions.
- Route deviation score.
- Landmark recognition score.
- Sketch recall accuracy.
- Reverse-navigation success rate.

---



# 5. Personas and Use Cases



## 5.1 Persona 1: Pulmonology Trainee

Name: Dr. Maya  
Goal: Learn bronchial anatomy and route planning.  
Needs: Patient-specific rehearsal, feedback, repeated practice.  
Pain Points: Textbook anatomy does not translate to endoscopic viewpoint.

## 5.2 Persona 2: Medical Educator

Name: Prof. Chen  
Goal: Teach airway navigation effectively.  
Needs: Assignable training missions, performance metrics, replay review.  
Pain Points: Lack of engaging low-cost simulation tools.

## 5.3 Persona 3: Researcher in Spatial Cognition

Name: Dr. Alvarez  
Goal: Study how humans learn branching anatomical networks.  
Needs: Event logs, path recording, controllable maze complexity.  
Pain Points: Limited experimental control in real procedural training.

## 5.4 Persona 4: Game Developer / Technical Artist

Name: Leo  
Goal: Build a compelling atmospheric experience.  
Needs: Strong rendering pipeline, procedural content tools.  
Pain Points: Medical data is hard to convert into playable form.

## 5.5 Core Use Cases



### UC-1: Patient-Specific Mission Creation

1. User loads chest CT DICOM series.
2. User scrolls through slices in MPR viewer.
3. User clicks a suspected lesion.
4. System segments airway tree.
5. System computes ranked routes.
6. System exports mission to game.



### UC-2: Training Navigation Run

1. Player starts at trachea.
2. Player navigates toward target beacon.
3. Breathing dynamically alters corridor size.
4. Player reaches terminal branch near target.
5. System compares path with optimal route.



### UC-3: Mental Map Recall Session

1. Player completes navigation.
2. Player sketches airway tree from memory.
3. System compares sketch topology.
4. System provides feedback.

---



# 6. Core Product Principles



## 6.1 Active Navigation Over Passive Watching

The user must make decisions at bifurcations. Passive fly-throughs are less effective for building hippocampal spatial maps.

## 6.2 Anatomy Must Drive the Maze

Procedural content is useful, but patient-specific graph structure is the differentiator.

## 6.3 The Environment Must Feel Alive

Breathing is not cosmetic. It changes traversal, timing, and cognitive load.

## 6.4 Training Must Be Measurable

Every run should produce measurable performance metrics.

## 6.5 Simplicity First, Fidelity Later

The MVP should be 2.5D or 2D raycasted. Full 3D endoscopic simulation is a later phase.

## 6.6 Medical Data Must Be Handled Carefully

Patient privacy and coordinate correctness are non-negotiable.

---



# 7. Product Scope



## 7.1 In Scope for MVP

- Local or server-based DICOM series loading.
- Axial/coronal/sagittal MPR browsing.
- Target point selection.
- Top-down airway segmentation from trachea.
- Airway graph extraction.
- Route planning to target.
- JSON mission export.
- First-person raycasted breathing labyrinth.
- Keyboard movement controls.
- Recording and playback.
- Basic training feedback.



## 7.2 In Scope for V1.1

- Landmark system.
- Ghost path playback.
- Route comparison overlay.
- Training session dashboard.
- Better breathing model.
- Audio cues.



## 7.3 Out of Scope for Initial Release

- Full haptic endoscope physics.
- Certified clinical planning.
- Real-time intraoperative guidance.
- Multiplayer.
- VR.
- Full volumetric 3D bronchoscopy.

---



# 8. High-Level User Journey

[Load CT]
   ↓
[Browse DICOM in MPR Viewer]
   ↓
[Select Target POI]
   ↓
[Segment Airways from Trachea Downward]
   ↓
[Build Airway Graph]
   ↓
[Compute Ranked Routes to POI]
   ↓
[Export Mission JSON]
   ↓
[Enter Game Labyrinth]
   ↓
[Navigate to Target]
   ↓
[Record Path and Performance]
   ↓
[Review Route, Errors, and Recall]

---



# 9. Functional Requirements



## 9.1 DICOM and Imaging


| ID          | Requirement                                                            | Priority |
| ----------- | ---------------------------------------------------------------------- | -------- |
| FR-DICOM-01 | System shall load a DICOM CT series from local files or secure server. | Must     |
| FR-DICOM-02 | System shall display axial, coronal, and sagittal MPR views.           | Must     |
| FR-DICOM-03 | System shall support lung window and soft-tissue window presets.       | Must     |
| FR-DICOM-04 | System shall allow click selection of a 3D point of interest.          | Must     |
| FR-DICOM-05 | System shall snap selected point to lesion centroid when possible.     | Should   |
| FR-DICOM-06 | System shall preserve DICOM patient coordinates.                       | Must     |
| FR-DICOM-07 | System shall detect and warn if slice orientation is ambiguous.        | Should   |




## 9.2 Airway Segmentation


| ID        | Requirement                                                                   | Priority |
| --------- | ----------------------------------------------------------------------------- | -------- |
| FR-SEG-01 | System shall automatically detect trachea seed near superior slices.          | Must     |
| FR-SEG-02 | System shall segment airway lumen using air-intensity region growing.         | Must     |
| FR-SEG-03 | System shall prevent leakage into lung parenchyma using growth monitoring.    | Must     |
| FR-SEG-04 | System shall extract centerline/skeleton from airway mask.                    | Must     |
| FR-SEG-05 | System shall estimate branch radii from distance transform.                   | Must     |
| FR-SEG-06 | System shall produce a tree graph with nodes, edges, parent links, and radii. | Must     |
| FR-SEG-07 | System shall allow manual correction of seed or mask.                         | Should   |
| FR-SEG-08 | System shall support fallback to ML airway segmentation if available.         | Could    |




## 9.3 Route Planning


| ID          | Requirement                                                     | Priority |
| ----------- | --------------------------------------------------------------- | -------- |
| FR-ROUTE-01 | System shall find candidate airway nodes/edges near target POI. | Must     |
| FR-ROUTE-02 | System shall compute path from trachea root to each candidate.  | Must     |
| FR-ROUTE-03 | System shall rank routes by feasibility and distance.           | Must     |
| FR-ROUTE-04 | System shall return top-K routes.                               | Must     |
| FR-ROUTE-05 | System shall compute last-leg distance from airway to target.   | Must     |
| FR-ROUTE-07 | System shall estimate turn angles and bifurcation count.        | Should   |
| FR-ROUTE-08 | System shall mark unreachable targets.                          | Must     |




## 9.4 Game Engine


| ID         | Requirement                                                        | Priority |
| ---------- | ------------------------------------------------------------------ | -------- |
| FR-GAME-01 | System shall render first-person labyrinth from airway graph.      | Must     |
| FR-GAME-02 | System shall support forward/backward movement and yaw rotation.   | Must     |
| FR-GAME-03 | System shall animate corridor radius with breathing.               | Must     |
| FR-GAME-04 | System shall prevent player from passing through walls.            | Must     |
| FR-GAME-05 | System shall display target beacon direction or glow.              | Must     |
| FR-GAME-06 | System shall record player path.                                   | Must     |
| FR-GAME-07 | System shall replay recorded path.                                 | Must     |
| FR-GAME-08 | System shall display ghost optimal path.                           | Should   |
| FR-GAME-09 | System shall support procedural airway tree if no DICOM is loaded. | Should   |




## 9.5 Training and Assessment


| ID          | Requirement                                      | Priority |
| ----------- | ------------------------------------------------ | -------- |
| FR-TRAIN-01 | System shall support guided exploration mode.    | Must     |
| FR-TRAIN-02 | System shall support target navigation mode.     | Must     |
| FR-TRAIN-03 | System shall support reverse navigation mode.    | Should   |
| FR-TRAIN-04 | System shall support random start/target mode.   | Should   |
| FR-TRAIN-05 | System shall collect performance metrics.        | Must     |
| FR-TRAIN-06 | System shall compare user path to optimal path.  | Should   |
| FR-TRAIN-07 | System shall support landmark naming or tagging. | Could    |


---



# 10. System Architecture



## 10.1 Recommended High-Level Architecture

┌─────────────────────────────────────────────────────────────┐
│                        Frontend Web App                     │
│                                                             │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────────┐  │
│  │ DICOM Viewer │  │ Mission UI   │  │ Game Renderer    │  │
│  │ Cornerstone3D│  │ React/Next   │  │ WebGPU/WebGL     │  │
│  └──────────────┘  └──────────────┘  └──────────────────┘  │
│                                                             │
│  ┌──────────────────────────────────────────────────────┐   │
│  │ State Manager: mission, graph, routes, player, logs │   │
│  └──────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────┘
                            ↑
                            │ JSON / REST / WebSocket
                            ↓
┌─────────────────────────────────────────────────────────────┐
│                    Backend Processing API                   │
│                                                             │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────────┐  │
│  │ DICOM Ingest │  │ Segmentation │  │ Route Solver     │  │
│  │ SimpleITK    │  │ SciPy/VMTK   │  │ Python/NumPy     │  │
│  └──────────────┘  └──────────────┘  └──────────────────┘  │
│                                                             │
│  ┌──────────────────────────────────────────────────────┐   │
│  │ Task Queue: Celery/RQ + Redis for long-running jobs │   │
│  └──────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────┘
                            ↑
                            ↓
┌─────────────────────────────────────────────────────────────┐
│                      Storage Layer                          │
│                                                             │
│  - DICOM files                                              │
│  - Derived masks                                            │
│  - Airway graph JSON                                        │
│  - Route JSON                                               │
│  - Session recordings                                       │
│  - Metrics logs                                             │
└─────────────────────────────────────────────────────────────┘

## 10.2 Recommended Technology Stack



### Frontend


| Component     | Recommendation                | Reason                                     |
| ------------- | ----------------------------- | ------------------------------------------ |
| App framework | Next.js / React               | UI structure, routing, state               |
| DICOM viewing | Cornerstone3D                 | Robust MPR and patient coordinate handling |
| 3D graphics   | WebGPU first, WebGL2 fallback | High-performance raymarching               |
| State         | Zustand or Redux Toolkit      | Mission and player state                   |
| Audio         | Web Audio API                 | Spatial audio and breathing cues           |
| File access   | File System Access API        | Local DICOM loading                        |
| Optional gaze | TensorFlow.js / MediaPipe     | Experimental gaze analytics                |




### Backend


| Component   | Recommendation                   | Reason                        |
| ----------- | -------------------------------- | ----------------------------- |
| API         | FastAPI                          | Async, typed, easy deployment |
| Imaging     | SimpleITK, NumPy, SciPy          | DICOM and segmentation        |
| Centerlines | VMTK or custom skeleton pipeline | Robust airway centerlines     |
| Optional ML | nnU-Net / MONAI                  | Advanced airway segmentation  |
| Task queue  | Celery + Redis                   | Long-running processing       |
| Storage     | S3-compatible object storage     | DICOM and derived assets      |
| Metadata DB | PostgreSQL                       | Studies, missions, sessions   |


---



# 11. Deep Dive Implementation Plan

This section contains the most important implementation insights.

---



## 11.1 Coordinate Systems: The Hidden Core Problem

A large number of medical imaging failures come from coordinate mismatches. The system must define coordinate systems explicitly.

### Coordinate Systems Used

1. Voxel Index Space
  - (i, j, k)
  - integer indices into volume array.
2. Patient Image Space
  - millimeter coordinates derived from DICOM metadata.
  - often LPS-like depending on toolkit.
3. Game Space
  - simplified coordinate system used by the renderer.
  - may be projected or layout-transformed.



### Implementation Rule

Store canonical data in patient image space. Only convert to game space at the last step.

### Required Metadata

- ImagePositionPatient
- ImageOrientationPatient
- PixelSpacing
- SliceThickness
- SpacingBetweenSlices
- Rows, Columns
- RescaleIntercept, RescaleSlope



### Best Practice

Use SimpleITK to load the series and preserve direction matrix. Do not assume axial orientation.
image = reader.Execute()
spacing = image.GetSpacing()
origin = image.GetOrigin()
direction = image.GetDirection()

All downstream transforms must respect these.

---



## 11.2 DICOM Browsing and Target Selection



### Recommended Viewer

Use Cornerstone3D instead of writing a custom DICOM renderer from scratch.

Why:

- correct image orientation handling,
- MPR support,
- windowing,
- click-to-world coordinate conversion,
- extensible tools.



### Target Selection Flow

1. User opens MPR viewer.
2. System displays axial/coronal/sagittal planes.
3. User clicks on suspected lesion.
4. Frontend calls canvasToWorld().
5. Point is stored in patient coordinate space.
6. Optional lesion snap refines point.



### Lesion Snap Assist

A click is often imprecise. Use a local ROI search:

- Define ROI radius: 10–20 mm.
- Identify connected components with soft-tissue HU range.
- Select component most likely to be nodule.
- Snap target to component centroid.
- Estimate radius from volume or equivalent sphere.

Suggested heuristic:
target_snap_criteria:

- HU > -400
- surrounded by lower-density lung parenchyma
- compactness above threshold
- volume within expected nodule/mass range



### Manual Override

Always allow manual correction. Clinicians must be able to reject automatic snapping.

---



## 11.3 Airway Segmentation Strategy

The user insight is correct: start from the top, use air intensity, and grow down the tracheobronchial tree.

### Core Algorithm

1. Load CT volume in Hounsfield Units.
2. Identify trachea seed in superior slices.
3. Create strict air mask: HU < -950.
4. Flood fill from trachea seed inside strict air mask.
5. Expand distally using looser threshold: HU < -870 to -800.
6. Monitor growth to prevent leakage into parenchyma.
7. Post-process mask.
8. Extract skeleton/centerlines.
9. Build graph.
10. Estimate radii.

---



## 11.4 Trachea Seed Detection



### Why Start at the Trachea?

The trachea is:

- large,
- air-filled,
- centrally located,
- usually present in superior slices,- less likely to be confused with lung parenchyma.



### Seed Detection Method

Scan first N superior slices, for example z = 0..40.

For each slice:

1. Threshold air: slice < -950.
2. Label connected components.
3. Remove components touching image border.
4. Keep components with plausible tracheal area.
5. Select largest plausible enclosed component.
6. Use centroid as seed.



### Edge Cases

- Neck slices above trachea.
- Patient anatomy variation.
- Motion artifact.
- Intubation or medical devices.
- Tracheal compression.

Fallback:

- ask user to click trachea manually.

---



## 11.5 Region Growing and Leak Prevention



### The Leakage Problem

Distal airways have partial-volume averaging. At looser thresholds, segmentation can suddenly leak into aerated lung parenchyma.

### Two-Stage Region Growing

Use:

- strict threshold for trachea/main bronchi,
- looser threshold for distal airways,
- growth-rate monitoring.



### Leak Detection Rule

If the number of newly added voxels in one iteration exceeds a multiple of previous growth, stop.

Example:
if new_voxel_count > leak_ratio * previous_voxel_count:
    stop_and_rollback()

Recommended initial values:
strict_threshold = -950 HU
loose_threshold = -870 HU
leak_ratio = 3.0 to 5.0
max_iterations = 200

### Additional Guardrails

- maximum allowed airway radius,
- anatomical volume cap,
- connectivity to trachea required,
- avoid expansion into known lung mask boundary,
- morphological cleanup after growth.



### Optional ML Upgrade

If available, use a trained airway segmentation model as primary segmentation, with region growing as fallback or refinement.

Potential models:

- nnU-Net trained on airway segmentation.
- MONAI lung/airway pipelines.
- VMTK-based methods.

However, for MVP, threshold region growing is simpler and transparent.

---



## 11.6 Skeletonization and Graph Extraction



### Goal

Convert binary airway mask into:
nodes:

- id
- position
- radius
- parent

edges:

- parent-child relationship
- length
- angle



### Method Options



#### Option A: VMTK Centerlines

Best quality.

Pros:

- robust centerlines,
- radius estimation,
- branch handling.

Cons:

- heavier dependency,
- more complex deployment.



#### Option B: Skeletonization + Graph Parsing

Use:

- skimage.morphology.skeletonize
- voxel graph extraction
- pruning of spurious branches

Pros:

- simpler dependency footprint.
- easier to customize.

Cons:

- requires careful cleanup.



### Recommended MVP Approach

Use skeletonization plus graph extraction. Use VMTK in V1.1 if needed.

### Graph Cleanup Rules

- Remove branches shorter than minimum length.
- Merge nodes with degree 2 unless angle change is high.
- Preserve bifurcation nodes.
- Preserve terminal nodes.
- Ensure tree structure has one root.
- Remove cycles if present.
- Smooth centerline points using spline fitting.



### Radius Estimation

Use Euclidean distance transform on airway mask:
radius_at_centerline_point = distance_transform_edt(mask)[point]

Then smooth radii along branches.

---



## 11.7 From 3D Airway Graph to Playable 2D/2.5D Labyrinth

This is one of the most important implementation decisions.

### The Problem

The original document proposes projecting the 3D graph to a 2D plane. This can cause:

- overlapping branches,
- confusing crossings,
- unrealistic navigation,
- impossible turn geometry.



### Recommended Approach

Do not use raw anatomical projection as the primary playable map.

Instead, use a topology-preserving game layout.

### Proposed Game Map Strategy

1. Preserve the airway graph topology.
2. Preserve branch radii and lengths approximately.
3. Compute a 2D layout for gameplay:
  - root trachea at bottom or center,
  - branches radiating outward,
  - child branches separated angularly,
  - avoid overlaps.
4. Convert each edge into a 2D capsule corridor.
5. Maintain anatomical metadata for minimap and review.



### Why This Is Better

It creates a playable labyrinth while preserving:

- branch hierarchy,
- bifurcation decisions,
- relative sizes,
- route complexity.### Clinical Realism Preserved Separately

Display a separate anatomical 3D minimap using the original 3D graph.

Thus:

- gameplay map = cognitively navigable,
- anatomical map = clinically grounded.



### Layout Algorithms

Possible methods:

1. Radial tree layout
  - trachea at center or bottom,
  - children spread radially.
2. Force-directed layout with constraints
  - avoid overlap,
  - preserve branch lengths.
3. Anatomical sector layout
  - left/right lung separation,
  - lobar grouping,
  - segmental branching.



### Recommended MVP Layout

Use a constrained radial or orthogonal tree layout:
root: trachea
first split: left/right main bronchus
next splits: lobar branches
next splits: segmental/subsegmental branches

Add curvature to make corridors feel organic.

---



## 11.8 SDF Rendering Implementation



### Representation

Each airway segment becomes a capsule SDF:
capsule(start, end, radius)

The global SDF is the minimum distance over relevant capsules.

### Performance Strategy

Do not iterate over all branches for every ray if branch count is large.

Use:

- spatial hash grid,
- bounding volume hierarchy,
- or baked distance field.



### MVP Rendering Option

For 2D raycasting:

- Divide world into uniform grid cells.
- Each cell stores nearby capsule segment IDs.
- Ray marching queries only nearby cells.



### V1 Rendering Option

Use WebGPU shader:

- branch data in storage buffer,
- BVH or grid acceleration,
- raymarch per fragment,
- breathing radius modulation in shader.



### Breathing Deformation

For each branch:
effective_radius = base_radius * breathing_scale(time, depth, region)

Breathing scale:
breathing_scale = 1 + amplitude * sin(2π * t / period - phase_offset)

Phase offset can depend on:

- generation depth,
- lobe,
- regional compliance.



### Collision Rule

Player is inside airway if:
sdf(player_position) <= -player_radius

If breathing shrinks wall into player:

- compute normal,
- push player toward centerline,
- reduce speed if compressed.

---



## 11.9 Player Movement Model



### Controls


| Input       | Action                   |
| ----------- | ------------------------ |
| Arrow Up    | Accelerate forward       |
| Arrow Down  | Accelerate backward      |
| Arrow Left  | Rotate left              |
| Arrow Right | Rotate right             |
| Shift       | Boost, optional          |
| Space       | Brake or brace, optional |




### Movement Parameters

rotation_speed = 2.0 rad/s
acceleration = 8 units/s²
max_forward_speed = 4 units/s
max_backward_speed = -2 units/s
friction = exponential decay
player_radius = 0.25 to 0.5 branch-radius relative

### Wall Collision

If proposed movement violates SDF constraint:

1. Reject movement.
2. Optionally slide along wall using tangent projection.
3. Increase trauma metric if collision velocity is high.



### Centerline Assist

Optional assist mode:

- gently steer player toward centerline,
- reduce frustrating wall scraping,
- useful for beginner training.

---



## 11.10 Breathing Simulation Deep Dive



### Basic Model

Global sine wave:
scale(t) = 1 + A * sin(2πt/T)

### Better Model

Regional compliance:
scale(branch, t) =
  1 + A(branch) * sin(2πt/T - phase(branch))

Where:

- A(branch) depends on generation or pathology,
- phase(branch) delays distal motion,
- diseased regions have reduced compliance.



### Pathology Modeling

If CT indicates emphysema, fibrosis, or obstruction:

- emphysematous regions: hyperinflated, less elastic recoil.
- fibrotic regions: stiff, low expansion.
- obstructed regions: delayed or reduced expansion.

This can be approximated from HU statistics if available.

### Gameplay Events

- Normal breathing.
- Deep sigh: large expansion.
- Apnea: gradual narrowing.
- Cough: high-velocity expulsion.

For MVP, keep normal breathing only.

---



# 12. Data Model and Schemas



## 12.1 Study Metadata

{
  "study_id": "study_0001",
  "patient_id": "deidentified_0001",
  "modality": "CT",
  "acquisition_phase": "inspiratory",
  "slice_count": 342,
  "spacing": [0.75, 0.75, 1.0],
  "coordinate_system": "DICOM_Patient",
  "created_at": "2026-08-22T10:00:00Z"
}

## 12.2 Point of Interest

{
  "poi_id": "poi_0001",
  "label": "suspected_lesion","x": 112.4,
  "y": -38.1,
  "z": 214.7,
  "radius_mm": 9.5,
  "source": "manual_click_with_snap",
  "confidence": 0.82
}

## 12.3 Airway Graph Node

{
  "id": 17,
  "x": 101.2,
  "y": -25.4,
  "z": 180.3,
  "radius": 2.6,
  "parent": 12,
  "generation": 4,
  "lobe": "right_upper",
  "is_terminal": false
}

## 12.4 Airway Graph Edge

{
  "edge_id": "e_12_17",
  "from": 12,
  "to": 17,
  "length_mm": 14.2,
  "min_radius_mm": 2.4,
  "turn_angle_deg": 23.5
}

## 12.5 Route

{
  "route_id": "route_001",
  "target_id": "poi_0001",
  "rank": 1,
  "cost": 341.2,
  "path_node_ids": [0, 1, 4, 9, 22],
  "length_mm": 145.7,
  "bifurcation_count": 4,
  "min_radius_mm": 2.1,
  "max_turn_angle_deg": 46.0,
  "last_leg_mm": 6.8,
  "reachability_score": 0.87
}

## 12.6 Recording Frame

{
  "t": 12.333,
  "x": 45.2,
  "y": 18.7,
  "angle": 1.23,
  "speed": 2.1,
  "mode": "live",
  "breath_phase": 0.62
}

## 12.7 Session Metrics

{
  "session_id": "sess_001",
  "mission_id": "mission_001",
  "user_id": "user_001",
  "time_to_target_sec": 184.5,
  "wrong_turns": 3,
  "wall_collisions": 7,
  "route_deviation_score": 0.24,
  "completion": true,
  "training_mode": "target_navigation"
}

---



# 13. API and Service Design



## 13.1 REST API Endpoints



### Studies

POST /api/studies
GET /api/studies/{study_id}
GET /api/studies/{study_id}/status

### Targets

POST /api/studies/{study_id}/targets
GET /api/studies/{study_id}/targets

### Segmentation

POST /api/studies/{study_id}/airway-graph
GET /api/studies/{study_id}/airway-graph

### Routes

POST /api/studies/{study_id}/targets/{poi_id}/routes
GET /api/studies/{study_id}/targets/{poi_id}/routes

### Missions

POST /api/missions
GET /api/missions/{mission_id}

### Sessions

POST /api/sessions
POST /api/sessions/{session_id}/recording
GET /api/sessions/{session_id}/metrics

## 13.2 Processing Job Pattern

Because segmentation can be slow, use asynchronous jobs:
POST /api/studies/{study_id}/airway-graph
→ 202 Accepted
→ { "job_id": "job_123" }

GET /api/jobs/job_123
→ { "status": "processing", "progress": 0.42 }

---



# 14. UX/UI Specification



## 14.1 Main Screens



### Screen 1: Study Loader

Features:

- drag-and-drop DICOM folder,
- select from server,
- study metadata preview,
- privacy notice.



### Screen 2: DICOM Target Selection

Features:

- MPR viewer,
- window presets,
- crosshair,
- target marker,
- lesion snap toggle,
- route generation button.



### Screen 3: Route Review

Features:

- 3D airway graph display,
- target marker,
- top routes color-coded,
- route metrics table,
- select mission route.



### Screen 4: Game Mission

Features:

- first-person view,
- breathing indicator,
- target distance HUD,
- bifurcations remaining,
- pause menu,
- recording indicator.



### Screen 5: Session Review

Features:

- path overlay,
- optimal route comparison,
- wrong-turn markers,
- collision heatmap,
- time metrics,
- replay player.



## 14.2 Critical UX Rules

1. Always show processing status.
2. Never block user without explaining why.
3. Allow manual correction of segmentation and target.
4. Make route ranking transparent.
5. Provide a clear “not for diagnosis” disclaimer.

---



# 15. Game Engine Specification



## 15.1 Rendering Modes



### Mode A: MVP 2D Raycasted Labyrinth

- 2D top-down SDF.
- First-person ray columns.
- Capsule corridors.
- Fast and reliable.



### Mode B: V1.5 Enhanced 2.5D

- Height/depth cues.
- Anatomical z encoded as fog or color.
- Smooth curved corridors.
- Dynamic breathing.



### Mode C: Future Full 3D

- WebGPU volumetric raymarching.
- True 3D SDF.
- Endoscope-like camera.
- Optional VR.



## 15.2 Scene Representation

For each playable edge:
segment {
  start_2d
  end_2d
  base_radius
  depth
  generation
  landmark_id
  breath_phase
}

## 15.3 Landmark System

Each bifurcation should have distinguishable visual features:

- color hue,
- cartilage ring pattern,
- mucus patch,
- pulsation frequency,
- wall texture,
- audio cue.

Landmarks should be placed at decision points, not randomly.

## 15.4 Target Beacon

Beacon behavior:

- invisible when far,- faint glow when within 40 units,
- stronger glow through walls when within 15 units,
- audio ping rate increases near target.



## 15.5 HUD Elements

Target Distance: 42 mm
Next Bifurcation: 2
Min Radius Ahead: 2.3 mm
Breath Phase: Inhale
Trauma: Low

---



# 16. DICOM and Medical Imaging Pipeline



## 16.1 Recommended Python Pipeline

DICOM series
  → SimpleITK load
  → HU conversion
  → orientation normalization
  → optional resampling
  → trachea seed detection
  → airway region growing
  → mask cleanup
  → skeleton/centerline
  → graph extraction
  → radius smoothing
  → route planning
  → mission JSON

## 16.2 Preprocessing Details



### HU Conversion

Use rescale slope/intercept.
hu = raw * slope + intercept

### Resampling

Optional but recommended:

- isotropic spacing around 1 mm for segmentation.
- preserve original coordinates.



### Lung Mask

Optional but useful:

- threshold at around -400 HU,
- select largest connected components,
- separate left/right lungs.

This can constrain leakage and assist visualization.

## 16.3 Segmentation QA

Automatic segmentation should produce quality metrics:

- trachea seed confidence,
- total airway volume,
- number of generations,
- terminal branch count,
- leak suspicion score.

If quality is low, ask user to confirm or correct.

---



# 17. Route Planning and Targeting Algorithm



## 17.1 Objective

Find the best airway route from trachea to the airway location closest to the target.

## 17.2 Candidate Selection

For each node or edge:
distance_to_target = min_distance(target, node_or_edge)

Candidate if:
distance_to_target <= max_last_leg

Recommended default:
max_last_leg = 20 to 30 mm

## 17.3 Route Cost Function

For tree path P from root to candidate:
cost(P) =
    w_length * path_length(P)

- w_turn * sum(turn_angles(P))
- w_radius * narrowness_penalty(P)
- w_bif * bifurcation_count(P)
- w_last * last_leg_distance

Example weights:
w_length = 1.0
w_turn = 0.1 per degree
w_radius = 25.0
w_bif = 8.0
w_last = 3.0

## 17.4 Narrowness Penalty

If scope radius is known:
narrowness_penalty = max(0, scope_radius / min_branch_radius - 1)

If unknown:
narrowness_penalty = 2.0 / min_branch_radius

## 17.5 Reachability Score

Normalize cost into a 0–1 score:
reachability = exp(-normalized_cost)

Or use heuristic:
reachability =
  0.4 * (1 - last_leg / max_last_leg)

- 0.3 * radius_score
- 0.3 * turn_score



## 17.6 Output

Return top-K routes:

- Route 1: best overall.
- Route 2: wider alternative.
- Route 3: shorter but more difficult.

This gives users strategic options.

---



# 18. Training and Neuroscience Module



## 18.1 Training Objective

Build flexible allocentric knowledge of the airway tree, not just rote route memory.

## 18.2 Core Training Principles

1. Active Decision-Making
  - user chooses at every bifurcation.
2. Landmark Binding
  - decision points have unique sensory cues.
3. Spaced Retrieval
  - sessions distributed over days.
4. Error-Based Learning
  - show correct path after mistakes.
5. Progressive Difficulty
  - start shallow, increase depth and breathing amplitude.
6. Interleaving
  - vary targets and starting points.



## 18.3 Training Modes



### Mode 1: Guided Exploration

- ghost path visible,
- landmarks highlighted,
- no time pressure.



### Mode 2: Target Navigation

- target beacon active,
- ghost path hidden,
- metrics recorded.



### Mode 3: Reverse Navigation

- start near target,
- return to trachea.



### Mode 4: Random Start/Target

- prevents route memorization.



### Mode 5: Blind Recall

- minimal HUD,
- beacon disabled or delayed,
- user must rely on memory.



## 18.4 Assessment Tasks



### Sketch Recall

User draws tree from memory. System scores:

- node count,
- edge correctness,
- left/right correctness,
- branch order.



### Verbal Prediction

Before bifurcation:

- prompt user to predict available branches.
- compare with actual graph.



### Route Reproduction

User repeats route after delay.

---



# 19. Recording, Playback, and Assessment



## 19.1 Recording Requirements

Record at fixed interval:RECORD_INTERVAL = 1/30 second

Record:

- timestamp,
- position,
- angle,
- speed,
- breathing phase,
- event flags.



## 19.2 Event Flags

Record important events:

- collision,
- wrong turn,
- target visible,
- breath phase extreme,
- pause,
- user hesitation.



## 19.3 Playback

Use interpolation between frames.

Handle angle wrapping:
angle_delta = ((a2 - a1 + π) % 2π) - π
angle_interp = a1 + alpha * angle_delta

## 19.4 Path Comparison Metrics



### Route Deviation Score

Use Fréchet distance or sampled path distance between user path and optimal path.

### Wrong Turn Detection

At bifurcation:

- detect node approach,
- detect chosen child edge,
- compare to optimal edge.



### Collision Score

Weight collisions by:

- impact speed,
- wall trauma,
- repeated contacts.

---



# 20. Performance Budgets and Technical Constraints



## 20.1 Frontend Performance


| Item                     | Target                    |
| ------------------------ | ------------------------- |
| Initial app load         | < 5 s                     |
| DICOM viewer interaction | 30+ FPS                   |
| Game rendering           | 45–60 FPS                 |
| Raymarch steps           | 64–128 per ray            |
| Branch count MVP         | ≤ 500 playable capsules   |
| Branch count optimized   | ≤ 5,000 with acceleration |




## 20.2 Backend Processing


| Task             | Target                      |
| ---------------- | --------------------------- |
| DICOM load       | < 20 s for typical chest CT |
| Trachea seed     | < 5 s                       |
| Region growing   | < 30 s                      |
| Graph extraction | < 60 s                      |
| Route planning   | < 2 s                       |




## 20.3 Memory

- DICOM volume can be large.
- Use streaming or downsampling where possible.
- Do not load unnecessary frames into GPU.



## 20.4 Browser Constraints

- WebGPU not universally available.
- Provide WebGL2 fallback.
- Large DICOM uploads may fail; support local processing if possible.

---



# 21. Security, Privacy, and Compliance



## 21.1 Data Sensitivity

DICOM CT scans are sensitive medical data.

## 21.2 Privacy Requirements



### MVP Recommendation

Prefer local processing or de-identified upload.

If browser-based local processing is feasible:

- do not upload PHI.
- keep data in browser or local server.

If server upload is required:

- encrypt in transit with TLS,
- encrypt at rest,
- enforce access controls,
- maintain audit logs,
- support data deletion.



## 21.3 De-Identification

Remove or anonymize:

- patient name,
- patient ID,
- institution name,
- dates,
- access numbers,
- other DICOM PHI tags.



## 21.4 Regulatory Positioning

Initial product should state:

> Lung Navigator is an educational and training simulation tool. It is not intended for diagnosis, treatment planning, or intraoperative guidance unless cleared/approved under applicable medical device regulations.

If clinical use is intended later:

- define intended use precisely,
- implement risk management,
- validate segmentation and routing,
- consider IEC 62304 software lifecycle,
- conduct usability testing.

---



# 22. Quality Assurance and Validation



## 22.1 Unit Tests

Test:

- coordinate transforms,
- DICOM metadata parsing,
- trachea seed detection,
- region growing leak guard,
- graph extraction,
- route cost calculation,
- angle wrapping,
- playback interpolation.



## 22.2 Integration Tests

Test:

- DICOM load → graph export.
- target selection → route generation.
- mission JSON → game load.
- recording → playback.



## 22.3 Synthetic Test Data

Create synthetic airway phantoms:

- straight tube,
- simple bifurcation,
- multi-generation tree,
- target near terminal branch,
- target unreachable.



## 22.4 Clinical Validation Sample

Use a small set of representative CTs:

- normal inspiratory CT,
- motion artifact case,
- large lesion case,
- narrow airway case,
- partial obstruction case.



## 22.5 Expert Review

Have clinicians review:

- airway extraction correctness,
- route plausibility,
- target distance reporting,
- anatomical labeling.

---



# 23. Risks and Mitigations


| Risk                             | Impact   | Mitigation                                      |
| -------------------------------- | -------- | ----------------------------------------------- |
| DICOM orientation errors         | High     | Strict coordinate tests; use SimpleITK metadata |
| Segmentation leakage             | High     | Leak detector, rollback, manual correction      |
| Trachea not detected             | Medium   | Manual seed fallback                            |
| Browser performance poor         | High     | WebGL fallback; reduce resolution; bake SDF     |
| Patient privacy violation        | Critical | Local processing/de-id; encryption; audit       |
| User misinterprets as diagnostic | High     | Clear disclaimer; training-only labeling        |
| Game map distorts anatomy        | Medium   | Separate anatomical minimap; preserve topology  |
| Large CT upload fails            | Medium   | Local file processing; chunked upload           |
| Breathing makes game frustrating | Medium   | Adjustable amplitude; accessibility options     |


---



# 24. Roadmap and Milestones



## Phase 0: Discovery and Prototyping

Duration: 2–4 weeks

Deliverables:

- validate DICOM loading,
- validate trachea seed detection,
- validate region growing on sample scans,
- prototype 2D raycast corridor.



## Phase 1: MVP

Duration: 8–12 weeks

Deliverables:

- DICOM viewer with POI selection.
- airway segmentation pipeline.
- graph extraction.
- route solver.
- playable breathing labyrinth.
- recording/playback.
- basic metrics.



## Phase 2: Training Enhancement

Duration: 6–8 weeks

Deliverables:

- ghost path,
- landmark system,
- route comparison,
- training dashboard,
- session analytics.



## Phase 3: Clinical Robustness

Duration: 8–12 weeks

Deliverables:

- improved segmentation fallback,
- VMTK integration,
- more robust route scoring,
- expert validation,
- report export.



## Phase 4: Advanced Simulation

Duration: ongoing

Deliverables:

- 3D rendering,
- spatial audio,
- trauma model,
- cough/apnea events,
- haptic research prototype.

---



# 25. Innovation Backlog: “10x / Outrageous” Extensions

These are not MVP requirements but strategic extensions.

## 25.1 Autonomic Biofeedback

Use EEG/HRV to make the airway constrict when the player is stressed.

Implementation:

- browser-compatible BCI SDK,
- stress classifier,
- breathing amplitude modulation.



## 25.2 Vocal-Cord Sonar

Use sound pulses from the sleeping giant to reveal geometry.

Implementation:

- audio trigger,
- SDF wavefront visualization,
- temporary wireframe overlay.



## 25.3 Generative Disease Time Slider

Use generative models to predict past/future anatomical states.

Implementation:

- longitudinal model,
- interpolation of airway graph,
- tumor growth simulation.



## 25.4 Non-Euclidean REM Warping

Dream state distorts maze geometry.

Implementation:

- coordinate warping in shader,
- topology-preserving collision fallback.



## 25.5 Immune System NPCs

Macrophages respond to trauma.

Implementation:

- boid simulation,
- trauma event system,
- stealth mechanics.



## 25.6 Mucociliary Surfing

Player rides mucus flow.

Implementation:

- vector field along airway surface,
- traversal mode switch.



## 25.7 Asymmetric Multiplayer

One player navigates; another controls host responses.

Implementation:

- shared mission state,
- host control panel,
- real-time events.



## 25.8 AI Ghost Doctor

LLM companion gives hints and warnings.

Implementation:

- LLM grounded in mission graph,
- spatial audio narration,
- tutor mode.



## 25.9 Microbiome Crafting

Deploy bacteria to clear blockages.

Implementation:

- cellular automata,
- resource system,
- biological risk/reward.



## 25.10 Real-Time Histology Mini-Game

Biopsy triggers microscopic identification challenge.

Implementation:

- procedural histology images,
- classification quiz,
- treatment selection.

---



# 26. Open Questions

1. Should the MVP be fully client-side or use a backend for segmentation?
2. What CT protocols are supported? Inspiratory only? Expiratory later?
3. Do we need anatomical lobe labeling in MVP?
4. What is the preferred game projection: radial layout or coronal projection?
5. Should recordings be stored locally or server-side?
6. Do we need user accounts for training progress?
7. What is the maximum acceptable branch depth for gameplay?
8. Do we support pediatric scans?
9. Is there a target scope radius for route feasibility?
10. Should the system export to standard formats like GLTF or OBJ for 3D review?

---



# 27.Appendix: Key Algorithms and Implementation Notes



## 27.1 Pseudocode: Trachea Seed Detection

```
def find_trachea_seed(volume_hu):
    for z in range(0, MAX_SUPERIOR_SLICES):
        slice_air = volume_hu[z] < TRACHEA_AIR_HU
        labels = connected_components(slice_air)

    candidates = []
    for label in labels:
        if touches_image_border(label):
            continue
        area = label.area
        if MIN_TRACHEA_AREA < area < MAX_TRACHEA_AREA:
            candidates.append(label)

    if candidates:
        best = max(candidates, key=lambda c: c.area)
        return centroid(best)

return ask_user_for_seed()
```



## 27.2 Pseudocode: Region Growing with Leak Guard

```
def grow_airways(volume_hu, seed):
    strict_mask = volume_hu < STRICT_AIR_HU
    mask = flood_fill(strict_mask, seed)

loose_mask = volume_hu < LOOSE_AIR_HU
previous_growth = mask.volume()

for iteration in range(MAX_ITERATIONS):
    frontier = dilate(mask) & loose_mask & ~mask

    if frontier.volume() == 0:
        break

    if frontier.volume() > LEAK_RATIO * previous_growth:
        break

    mask |= frontier
    previous_growth = frontier.volume()

return mask
```



## 27.3 Pseudocode: Route Ranking

```
def rank_routes(graph, target, top_k=3):
    candidates = []

for node in graph.nodes:
    last_leg = distance(node.position, target)

    if last_leg > MAX_LAST_LEG:
        continue

    path = path_from_root(graph, node)
    cost = compute_route_cost(path, last_leg)

    candidates.append(Route(path, cost, last_leg))

candidates.sort(key=lambda r: r.cost)
return candidates[:top_k]
```



## 27.4 Pseudocode: SDF Capsule Distance

```
function capsuleDistance2D(px, py, ax, ay, bx, by, radius) {
  const abx = bx - ax;
  const aby = by - ay;
  const apx = px - ax;
  const apy = py - ay;

  const ab2 = abx * abx + aby * aby || 1;
  let t = (apx * abx + apy * aby) / ab2;
  t = Math.max(0, Math.min(1, t));

  const cx = ax + abx * t;
  const cy = ay + aby * t;

  const dx = px - cx;
  const dy = py - cy;

  return Math.hypot(dx, dy) - radius;
}
```



## 27.5 Pseudocode: Breathing Scale

```
function breathingScale(time, depth) {
  const phase = (time / BREATH_PERIOD) * Math.PI * 2;
  const delay = depth * 0.3;
  const wave = Math.sin(phase - delay);
  return 1 + BREATH_AMPLITUDE * wave;
}
```

---



# Final Recommendation

The best implementation strategy is:

1. Start with a robust DICOM-to-airway-graph pipeline.
2. Use top-down trachea seeding and leak-controlled region growing.
3. Convert the graph into a playable topology-preserving 2D labyrinth.
4. Add target selection and ranked route planning.
5. Build the breathing raycast game around that mission structure.
6. Layer neuroscience-based training and analytics on top.
7. Reserve full 3D, haptics, and advanced AI features for later phases.

This approach keeps the project technically feasible while preserving the most powerful idea:

> A patient-specific, living airway labyrinth that trains the brain to navigate the lung.

