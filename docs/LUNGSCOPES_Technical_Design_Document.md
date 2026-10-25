# LUNGSCOPES: Technical Design Document
## A CT-Derived Bronchial Navigation Trainer

**Version:** 1.1  
**Date:** 2026-08-16  
**Platform:** Mobile HTML5 Canvas  

---

## Table of Contents

1. [Executive Summary](#1-executive-summary)
2. [Core Concept & Narrative Frame](#2-core-concept--narrative-frame)
3. [World Model: The Bronchial Graph](#3-world-model-the-bronchial-graph)
4. [DICOM CT Processing Pipeline](#4-dicom-ct-processing-pipeline)
5. [Point-of-Interest Target Selection](#5-point-of-interest-target-selection)
6. [The Respiration Engine](#6-the-respiration-engine)
7. [Player Controller & Input](#7-player-controller--input)
8. [Cylindrical Renderer](#8-cylindrical-renderer)
9. [Recording & Playback System](#9-recording--playback-system)
10. [Neuroscience of Spatial Training](#10-neuroscience-of-spatial-training)
11. [Intersection Decision Protocol](#11-intersection-decision-protocol)
12. [Adaptive Difficulty Algorithm](#12-adaptive-difficulty-algorithm)
13. [Audio Architecture](#13-audio-architecture)
14. [Mobile Performance & Optimization](#14-mobile-performance--optimization)
15. [File Structure & Asset Pipeline](#15-file-structure--asset-pipeline)
16. [Training Schedule & Transfer Validation](#16-training-schedule--transfer-validation)
17. [Appendix A: JSON Schemas](#appendix-a-json-schemas)
18. [Appendix B: Physics & Math Reference](#appendix-b-physics--math-reference)

---

## 1. Executive Summary

LUNGSCOPES is a first-person corridor-navigation game set inside the bronchial tree of a sleeping giant. The world is derived from real DICOM CT scan data, processed into a directed graph where edges represent bronchi and nodes represent bifurcations. The game uses a cylindrical raycaster rendered on HTML5 Canvas for mobile compatibility.

The primary design objective is **cognitive transfer**: the player must develop robust spatial mapping skills that transfer to real endoscopic navigation. This is achieved through a neuroscience-informed training architecture combining dual-representation encoding, retrieval practice, variable practice, stress inoculation, and an adaptive difficulty algorithm that spaces repetitions based on per-node forgetting curves.

A key workflow addition allows a clinician to **browse the DICOM stack**, select a **point of interest** (e.g., tumor, lesion, biopsy target), and have the system automatically compute the **optimal bronchial path** from the trachea to the nearest reachable airway point. This target then becomes the mission objective for training sessions.

---

## 2. Core Concept & Narrative Frame

### 2.1 Setting
The player navigates the respiratory system of a giant lying on its back, asleep. The corridors (bronchi) rhythmically expand and contract with the giant's breathing. The CT scan used to generate the world was taken while the giant was awake and inspiring; the game occurs during sleep, when the anatomy is in a different physiological state.

### 2.2 The "Map Is a Liar" Mechanic
The in-game map (derived from CT) shows corridors in their **inspiratory** (wide) state. The live world operates in an **expiratory/sleep** state (narrower, more elastic). Some paths that appear passable on the map may be temporarily blocked during deep exhalation. The player must learn to use the map for **topology** while relying on the live view for **state**.

### 2.3 Navigation Paradigm
- **Vertical arrows (Up/Down):** Control speed along the current corridor
- **Horizontal arrows (Left/Right):** Orient the player at intersections
- **Tank-like controls:** Movement is constrained to the graph; no free-roaming

---

## 3. World Model: The Bronchial Graph

### 3.1 Graph Structure
The world is a **directed tree graph**:

- **Nodes** = Intersections (bifurcations), dead ends (alveoli), or the trachea entry
- **Edges** = Bronchial corridors connecting two nodes

```javascript
class Node {
  id: number;
  generation: number;      // 0 = trachea, increases with depth
  x, y, z: number;         // 3D position in meters
  edges: Edge[];
}

class Edge {
  id: string;
  from: Node;
  to: Node;
  length: number;          // meters
  angle: number;           // world-space radians
  generation: number;
  baseRadius: number;      // meters at rest (inspiratory CT)
  elasticity: number;    // how much radius changes with breath

  // CT-derived rendering data
  radiusProfile: number[];         // sampled radii along edge
  eccentricityProfile: number[];   // ellipse squashed-ness
  orientationProfile: number[];    // ellipse rotation angle
  wallTextureOffset: [number, number]; // into texture atlas
}
```

### 3.2 Generation Algorithm (Procedural Fallback)
If no CT data is available, a procedural tree is generated:

```
root (trachea, generation 0)
  └── splits into 2 branches at angle ±spread
      └── each branch splits recursively
          └── spread = 0.5-0.8 radians
          └── length *= 0.65-0.85 per generation
          └── baseRadius *= 0.85 per generation
          └── max depth = 5-7 generations
```

### 3.3 Anatomical Coordinate System
All navigation uses **anatomical directions**, not screen-relative left/right:
- **C** (Cranial) — toward the head
- **Ca** (Caudal) — toward the feet
- **D** (Dorsal) — toward the back (where the giant's weight presses)
- **V** (Ventral) — toward the front (heart side)

This prevents negative transfer to real endoscopy, where operators must think in anatomical terms.

---

## 4. DICOM CT Processing Pipeline

### 4.1 Input
A stack of DICOM slices from an inspiratory-phase chest CT scan.

### 4.2 Key DICOM Metadata
| Tag | Meaning | Usage |
|-----|---------|-------|
| `(0020,0032)` | Image Position Patient (x,y,z) | 3D reconstruction |
| `(0028,0030)` | Pixel Spacing (mm/pixel) | Real-world scale conversion |
| `(0018,0050)` | Slice Thickness | Z-resolution |
| `(0028,0100)` | Bits Allocated | HU conversion |

### 4.3 Hounsfield Unit Thresholding
```python
# Convert raw pixels to HU
hu_volume = pixel_array * rescale_slope + rescale_intercept

# Bronchial lumen is air-filled: ~ -1000 to -800 HU
air_mask = (hu_volume < -800).astype(np.uint8)
```

### 4.4 Trachea Seeding & Region Growing
```python
# Find trachea in top slice: largest circular dark region near center
dist = ndimage.distance_transform_edt(top_slice)
tracea_seed = np.unravel_index(np.argmax(dist), dist.shape)

# Keep only connected component containing the seed
labeled, _ = ndimage.label(air_mask)
tracea_label = labeled[0, seed_y, seed_x]
bronchial_mask = (labeled == trachea_label)
```

### 4.5 3D Skeletonization
Extract the medial axis (centerline) of the airway tree:
```python
from skimage.morphology import skeletonize_3d
skeleton = skeletonize_3d(bronchial_mask)
skeleton_points = np.argwhere(skeleton > 0)
```

### 4.6 Bifurcation Detection
Classify skeleton points by 26-connectivity neighbor count:
- **1 neighbor** = Endpoint (trachea top or alveolar dead end)
- **2 neighbors** = Segment point (corridor)
- **3+ neighbors** = Bifurcation (intersection node)

### 4.7 Per-Slice Ellipse Fitting
For each point along an edge's path, fit an ellipse to the airway lumen in the perpendicular CT slice:

```python
from skimage.measure import EllipseModel

ellipse = EllipseModel()
ellipse.estimate(contour_points)
xc, yc, a, b, theta = ellipse.params

# Outputs:
#   semi_major (a), semi_minor (b)
#   eccentricity = sqrt(1 - (b/a)^2)
#   equivalent_radius = sqrt(a * b)
#   orientation = theta
```

### 4.8 Wall Texture Extraction
Sample HU values just outside the lumen boundary to capture:
- Cartilage rings (bright, periodic)
- Smooth muscle (medium gray)
- Mucus patches (slightly brighter than air)

Store as a 2D texture atlas: X-axis = circumference samples, Y-axis = position along edge.

### 4.9 State Remapping (Inspire → Sleep)
The CT captures inspiratory anatomy. The game requires expiratory/sleep dimensions:

```python
def sleep_radius(inspiratory_radius, generation, breath_phase):
    # Base narrowing from inspire to sleep-rest
    base_narrowing = 0.15 + (generation * 0.04)
    sleep_rest_radius = inspiratory_radius * (1 - base_narrowing)

    # Breathing oscillation on top of sleep-rest
    elasticity = 0.10 + (1.0 / (generation + 1)) * 0.15
    current_radius = sleep_rest_radius * (1 + breath_phase * elasticity)

    return current_radius
```

**4D CT Enhancement:** If inspiratory + expiratory CT pairs are available, compute true per-edge elasticity directly from radius change.

### 4.10 Export Format
Pre-processed on server/desktop, exported as:
- `game_graph.json` (~64KB for 200 edges)
- `wall_atlas.png` (~32KB, 64×N texture)
- Total mobile payload: **< 100KB**

---

## 5. Point-of-Interest Target Selection

### 5.1 Overview
Before training begins, a clinician (or the player) browses the raw DICOM stack, identifies a lesion or target, and clicks on it. The system then:
1. Computes the 3D world coordinate of the click
2. Finds the nearest point on the bronchial graph to that target
3. Computes the optimal path from the trachea to that reachable airway point
4. Sets that path's terminal node as the **training mission objective**

### 5.2 DICOM Browser Interface
```
┌─────────────────────────────────────┐
│  [◀ Prev]  Slice 127/312  [Next ▶]  │
├─────────────────────────────────────┤
│                                     │
│      ┌───────────────┐              │
│      │               │              │
│      │    CT Slice   │  ← click     │
│      │      📍       │    here      │
│      │               │              │
│      └───────────────┘              │
│                                     │
│  Window: Lung    Zoom: 1.5x         │
│  HU: -640 at cursor                 │
├─────────────────────────────────────┤
│  [Set Target]  [Auto-Find Lesion]   │
└─────────────────────────────────────┘
```

**Controls:**
- **Scroll / drag slider:** Move through DICOM slices (Z-axis)
- **Click on slice:** Set POI at that (X, Y, Z) position
- **Window/Level presets:** Lung, Mediastinum, Bone
- **Auto-Find Lesion:** Optional ML-based nodule detection (see 5.5)

### 5.3 Coordinate Transformation
When the user clicks at pixel `(px, py)` on slice `z`:

```python
# DICOM Image Position Patient for this slice
origin_x, origin_y, origin_z = dicom_slice.ImagePositionPatient

# Pixel spacing (mm per pixel)
spacing_x, spacing_y = dicom_slice.PixelSpacing

# 3D world coordinate of the click
world_x = origin_x + px * spacing_x
world_y = origin_y + py * spacing_y
world_z = origin_z  # or use slice location

poi = np.array([world_x, world_y, world_z])
```

### 5.4 Nearest Bronchus Detection
The POI is unlikely to be exactly on an airway centerline. The system finds the **closest reachable airway point** by computing the minimum distance from the POI to every edge in the graph.

```python
def point_to_segment_distance(p, a, b):
    # Distance from point p to line segment ab
    ab = b - a
    ap = p - a
    t = np.clip(np.dot(ap, ab) / np.dot(ab, ab), 0, 1)
    closest = a + t * ab
    return np.linalg.norm(p - closest), closest, t

def find_nearest_airway_point(poi, graph):
    best_dist = float('inf')
    best_edge = None
    best_t = 0
    best_point = None

    for edge in graph.edges:
        a = edge.from_node.pos
        b = edge.to_node.pos
        dist, closest, t = point_to_segment_distance(poi, a, b)

        # Check if the closest point is inside the airway lumen
        # (i.e., within the radius at that position)
        radius_at_t = lerp(edge.radiusProfile[0], edge.radiusProfile[-1], t)

        if dist < best_dist and dist < radius_at_t * 1.5:  # allow slight overshoot
            best_dist = dist
            best_edge = edge
            best_t = t
            best_point = closest

    return {
        'edge': best_edge,
        't': best_t,                    # position along edge (0-1)
        'point': best_point,            # 3D coordinate
        'distance': best_dist,          # mm from POI
        'is_reachable': best_dist < radius_at_t
    }
```

**Edge cases:**
- If POI is in lung parenchyma but not near any airway: flag as "peripheral — may require transbronchial biopsy"
- If multiple edges are close: rank by distance, then by generation (shallower = easier access)
- If POI is outside the lung entirely: reject with error

### 5.5 Auto-Find Lesion (Optional ML)
Instead of manual clicking, the system can suggest candidate lesions:

```python
# Simple threshold-based nodule detection
# Nodules are typically soft-tissue density (~0 to +100 HU) surrounded by air
nodule_mask = (hu_volume > -100) & (hu_volume < 200) & (air_mask == 0)

# Find connected components
labels, num = ndimage.label(nodule_mask)

# Filter by size (3-30mm typical for nodules)
for i in range(1, num + 1):
    component = (labels == i)
    volume_mm3 = np.sum(component) * voxel_volume
    diameter_mm = (volume_mm3 * 6 / np.pi) ** (1/3)

    if 3 <= diameter_mm <= 30:
        centroid = ndimage.center_of_mass(component)
        candidates.append({
            'centroid': centroid,
            'diameter': diameter_mm,
            'volume': volume_mm3,
            'hu_mean': np.mean(hu_volume[component])
        })
```

Present candidates to the user as numbered markers on the slice viewer.

### 5.6 Path Planning
Once the nearest airway point is found, compute the **shortest path** from the trachea (root node) to that point:

```python
def find_path_to_target(graph, target_edge, target_t):
    # Returns the sequence of edges from trachea to target.
    # If target_t < 1.0, the final edge is partial (target is along the edge).

    # BFS/DFS from root to target_edge.to_node
    path = []
    current = graph.root

    # Since it is a tree, there is exactly one path
    # We can backtrack from target node to root, then reverse
    target_node = target_edge.to_node if target_t > 0.5 else target_edge.from_node

    node_path = []
    while target_node != graph.root:
        node_path.append(target_node)
        # Find parent edge (the edge that leads TO this node)
        parent_edge = find_parent_edge(graph, target_node)
        target_node = parent_edge.from_node

    node_path.append(graph.root)
    node_path.reverse()

    # Convert to edge path
    edge_path = []
    for i in range(len(node_path) - 1):
        edge = find_edge_between(node_path[i], node_path[i+1])
        edge_path.append(edge)

    return {
        'nodes': node_path,
        'edges': edge_path,
        'final_edge': target_edge,
        'final_t': target_t,
        'total_length': sum(e.length for e in edge_path) + target_t * target_edge.length,
        'max_generation': max(e.generation for e in edge_path)
    }
```

### 5.7 Target Assignment
The computed path becomes the **mission objective**:

```javascript
const mission = {
    targetPoi: { x: 45.2, y: 32.1, z: 78.5 },
    targetDescription: "Left upper lobe peripheral nodule, 8mm",
    path: ["e0", "e1", "e4", "e12", "e33"],
    pathLength: 142.5,          // mm
    maxGeneration: 4,
    finalEdge: "e33",
    finalEdgePosition: 0.65,    // 65% along the last edge
    estimatedDifficulty: 0.72,  // computed from depth + narrowness

    // For rendering
    targetWorldPos: [45.2, 32.1, 78.5],
    targetScreenIndicator: true  // show faint glow when near
};
```

### 5.8 Visual Feedback in Game
When the player is on the target path:
- The map highlights the target path in **gold**
- Other visited paths in **faint blue**
- Unvisited paths in **dotted gray**
- As the player approaches the final edge, a subtle **red glow** appears in the distance (the lesion)
- At the terminal point, the cylindrical renderer shows a **spherical alveolus** with the target visible as a dark mass on the wall

### 5.9 Multiple Path Ranking
If the POI is near multiple airways (e.g., at a bifurcation), present the clinician with ranked options:

| Rank | Path | Distance to POI | Generation | Approach Angle | Recommendation |
|------|------|-----------------|------------|----------------|----------------|
| 1 | LUL → segmental B1 | 2.1mm | 4 | 35° dorsal | **Optimal** |
| 2 | LUL → segmental B2 | 4.8mm | 4 | 12° ventral | Alternative |
| 3 | LLL → superior B6 | 11.2mm | 4 | 89° lateral | Longer route |

The clinician selects the preferred path. The game then trains the player on that specific route.

### 5.10 Integration with Training
The adaptive difficulty algorithm uses the target path to:
- Prioritize nodes along the target path in early sessions
- Use off-path nodes for challenge blocks ("you took a wrong turn")
- Compute transfer tests that end at the target POI
- Record whether the player can reach the target without the map

---

## 6. The Respiration Engine

### 6.1 Global Breath Cycle
```javascript
const PERIOD = 6.0; // seconds per breath (deep sleep)
const cycle = (now % PERIOD) / PERIOD; // 0.0 → 1.0
```

### 6.2 Breath Intensity Function
```javascript
function smoothstep(edge0, edge1, x) {
    const t = Math.max(0, Math.min(1, (x - edge0) / (edge1 - edge0)));
    return t * t * (3 - 2 * t);
}

const inhale = smoothstep(0.0, 0.45, cycle);
const exhale = smoothstep(0.55, 1.0, cycle);
const intensity = inhale - exhale; // -1.0 (full exhale) to +1.0 (full inhale)
```

### 6.3 Per-Edge Radius
```javascript
const currentRadius = edge.baseRadius * (1 + intensity * edge.elasticity);
```

### 6.4 Gameplay Consequences
| Phase | Visual | Mechanical |
|-------|--------|------------|
| **Inhale** | Corridors widen, brighten | Max speed +30%, easier turns |
| **Exhale** | Corridors narrow, darken, veins bulge | Max speed -20%, deep bronchioles may block |
| **Peak inhale** | Brief pause | "Sigh" audio cue |
| **Peak exhale** | Walls nearly touch | Heartbeat intensifies |

### 6.5 The Squeeze Mechanic
If `currentRadius < PLAYER_WIDTH` during deep exhale (`intensity < -0.7`):
- Player movement slows to a crawl
- Screen darkens progressively
- Heartbeat audio accelerates
- Player must backtrack to a wider corridor or wait for inhale

---

## 7. Player Controller & Input

### 7.1 State Machine
```
TRAVERSING ──position>=0.95──> CHOOSING
    ↑                              │
    └───commit to exit─────────────┘
         or go back
```

### 7.2 Keyboard Input
| Key | Action |
|-----|--------|
| ↑ / W | Accelerate forward |
| ↓ / S | Accelerate backward |
| ← / A | Select left exit (at intersection) |
| → / D | Select right exit (at intersection) |

### 7.3 Mobile Touch Zones (Production)
| Zone | Action |
|------|--------|
| Bottom 40% tap-and-hold | Accelerate forward |
| Top 20% tap | Brake / reverse |
| Left half swipe | Select left exit |
| Right half swipe | Select right exit |

### 7.4 Physics
```javascript
if (input.up) speed += acceleration * dt;
if (input.down) speed -= acceleration * dt;

speed *= (1 - friction * dt);
speed = clamp(speed, -maxSpeed * 0.5, maxSpeed * breathFactor);

// Move along edge
position += (speed * dt) / edge.length;
```

### 7.5 Haptic Feedback
```javascript
// Commit vibration pattern: "da-dum"
navigator.vibrate([30, 50, 30]);

// Wall contact
navigator.vibrate(20);

// Intersection arrival
navigator.vibrate([10, 30, 10]);
```

---

## 8. Cylindrical Renderer

### 8.1 Core Raycasting
For each vertical screen strip:

```javascript
const normX = (x / width) * 2 - 1; // -1 (left wall) to 1 (right wall)
const angleFromCenter = normX * fovHalf; // e.g. ±60°

// Distance to tube wall (cylindrical)
const dist = currentRadius / Math.cos(angleFromCenter);

// Wall strip height
const stripHeight = (screenHeight / dist) * focalLength;
const y1 = horizon - stripHeight / 2;
```

### 8.2 Non-Uniform Tubes (CT-Derived)
Interpolate radius from `edge.radiusProfile` based on `player.position`:

```javascript
const sampleIndex = Math.floor(player.position * edge.radiusProfile.length);
const baseRadius = lerp(
    edge.radiusProfile[sampleIndex],
    edge.radiusProfile[sampleIndex + 1],
    frac
);
```

### 8.3 Wall Texturing
- Sample from pre-computed texture atlas
- Scroll vertically based on `player.position`
- Modulate brightness by breath intensity and generation depth

### 8.4 Atmosphere
- **Ceiling gradient:** Dark red → black
- **Floor gradient:** Black → dark red
- **Vignette:** Radial gradient, 30% inner radius to 80% outer
- **Breath pulse overlay:** `rgba(255,50,50, pulseAlpha)` where `pulseAlpha = 0.05 + |intensity| * 0.08`
- **Fog:** Exponential, density increases with generation

### 8.5 Intersection Rendering
When `state === CHOOSING`:
- Freeze or slow corridor view
- Overlay compass rose with anatomical labels
- Highlight available exits
- Show decision timer (invisible to player, drives decay effects)

---

## 9. Recording & Playback System

### 9.1 State Snapshots (20 Hz)
```javascript
{
    t: elapsedSeconds,
    edgeId: player.currentEdge.id,
    position: player.position,      // 0.0 to 1.0
    heading: player.heading,
    breathPhase: globalBreath.cycle,
    state: player.state
}
```

### 9.2 File Size
- 5-minute run = 6000 snapshots × ~40 bytes = **240KB**
- Compress with LZ-string for storage

### 9.3 Playback
```javascript
function getGhostPosition(time) {
    const i = binarySearch(snapshots, s => s.t <= time);
    const before = snapshots[i];
    const after = snapshots[i + 1];
    const t = (time - before.t) / (after.t - before.t);

    return {
        edge: findEdgeById(before.edgeId),
        position: lerp(before.position, after.position, t)
    };
}
```

### 9.4 Ghost Rendering
- Translucent purple silhouette
- Scaled by distance from player
- Hidden if ghost is on a different edge (around a corner)

### 9.5 Optimal Ghost
Compute shortest path from start to target using graph traversal. Display as a faint dotted line along edges.

---

## 10. Neuroscience of Spatial Training

### 10.1 The Navigation Systems
| System | Brain Region | Function | Failure Mode in Endoscopy |
|--------|-------------|----------|---------------------------|
| Place Cells | Hippocampus CA1 | "I am here" | View rotates, no stable landmarks |
| Grid Cells | Entorhinal Cortex | Metric tracking | No optic flow reference |
| Boundary Vector | Subiculum | Distance to walls | Walls are uniform and curved |
| Head Direction | Thalamus | Compass bearing | Scope rotates independently of body |
| Landmark | Lateral Entorhinal | Unique feature ID | All bifurcations look identical |

### 10.2 Route vs. Survey Knowledge
- **Route Knowledge (Striatum):** "Third bifurcation, turn left." Fast, brittle.
- **Survey Knowledge (Hippocampus):** "Left upper lobe is anterior-superior to carina." Slow, robust.

**Endoscopy requires survey knowledge.** Most training builds only route knowledge.

### 10.3 Training Principles
| Principle | Mechanism | Brain Effect |
|-----------|-----------|--------------|
| Dual Representation | Simultaneous egocentric view + allocentric map | Strengthens cross-hippocampal binding via theta oscillations |
| Retrieval Practice | Blink test: brief map flashes, must reconstruct | Anterior hippocampus activation predicts retention |
| Variable Practice | Random breath phase, random targets | Extracts invariant structural features |
| Errorful Learning | Auto-commit to wrong path on hesitation | Anterior cingulate activation strengthens encoding |
| Stress Inoculation | Progressive degradation modes | Prevents choking under pressure |
| Spaced Repetition | Algorithm schedules by forgetting curve | Combats exponential decay |

---

## 11. Intersection Decision Protocol

### 11.1 The Blink-Commit Protocol

**APPROACHING** (last 2 meters):
- Pulsing ring at center of view
- Heartbeat audio increases tempo
- Map fades to 20% opacity

**AT_INTERSECTION** (position >= 0.95):
- Corridor view freezes
- Compass rose appears with anatomical labels
- Decision timer starts (invisible)

### 11.2 The Compass Rose
```
        Your heading: Caudal ↓

        Exit A:  +35°  Dorsal-Caudal  ←◉
        Exit B:  -28°  Ventral-Caudal  ◉→

        [◀ Choose]        [Choose ▶]
```

Labels use anatomical directions (C/Ca/D/V), not screen-relative left/right.

### 11.3 The 3-Second Decay Gradient
| Time | Effect |
|------|--------|
| T+0ms | Full brightness. Audio chime. |
| T+500ms | Compass rose fully visible. Player can select. |
| T+1500ms | Screen desaturates -10% if no choice. |
| T+2500ms | Desaturation to 50%. Heartbeat arrhythmic. |
| T+3000ms | **Auto-commit to WRONG path.** "Patient coughed. Scope drifted." |

### 11.4 Pre-Load Mechanic
At T-3 seconds (3m from intersection), a ghost preview flashes for 200ms. Player must predict direction before arrival.

- Correct prediction: +10% speed boost, "confirmed" audio
- Wrong prediction: screen shake, -10% speed, "recalibrating"

### 11.5 Snap Input Model
- **Tap left half** → commit to left exit
- **Tap right half** → commit to right exit
- **Swipe down** → go back
- **Hold 500ms** → pause (only in safe zones)

Mimics the ballistic motor program of real scope torque.

---

## 12. Adaptive Difficulty Algorithm

### 12.1 Decision Event Schema
```javascript
{
    eventId: string,
    edgeId: string,
    nodeId: number,
    generation: number,
    timestamp: number,
    approachTime: number,           // seconds from previous intersection
    decisionLatency: number,        // seconds from visible to commit
    blinkCount: number,             // map-checks during approach
    preLoadCorrect: boolean,
    chosenExit: string,
    correctExit: string,
    wasCorrect: boolean,
    hesitationScore: number,        // 0=decisive, 1=wavered
    backtrackCount: number,
    touchPressure: number,          // 0-1
    scrollVelocity: number          // px/ms
}
```

### 12.2 Forgetting Curve
```
memory_strength(node, t) = e^(-λ * Δt)
```

**λ modifiers:**
| Factor | λ Multiplier |
|--------|-------------|
| High generation (deep) | × 1.5 |
| High hesitation | × 1.3 |
| Correct pre-load | × 0.7 |
| Backtrack after error | × 0.5 |
| Consecutive successes | × 0.8 |

### 12.3 Priority Queue
```javascript
priority(node) = memory_strength(node, now) * difficulty_weight(node)

difficulty_weight = 
    base_weight 
    + generation * 0.2 
    + (1 - accuracy_rate) * 0.5
    + hesitation_rate * 0.3
```

### 12.4 Session Construction
| Block | Duration | Content |
|-------|----------|---------|
| Warm-up | 2 min | 3 nodes with priority > 0.8 |
| Training | 8 min | 5-8 nodes with priority 0.3-0.7 (desirable difficulty) |
| Challenge | 3 min | 2-3 nodes with priority < 0.3 |
| Novelty | 2 min | 1-2 never-seen nodes |

### 12.5 Interleaving Rules
```javascript
function selectNextNode(queue) {
    const candidates = queue.filter(n => n.priority > 0.2);

    // Don't repeat same generation
    candidates = candidates.filter(n => n.generation !== lastGeneration);

    // Don't repeat same anatomical region
    candidates = candidates.filter(n => n.region !== lastRegion);

    return candidates.sort((a, b) => b.priority - a.priority)[0];
}
```

### 12.6 Stress Inoculation
```javascript
stressLevel = 
    baseStress 
    + (sessionTime / 15) * 0.1
    + errorStreak * 0.15
    + generation * 0.05;

if (stressLevel > 0.7) {
    // Inject calm node to prevent learned helplessness
    nextNode = findEasiestAvailable();
}
```

### 12.7 Dynamic Difficulty Adjustment
| Condition | Action |
|-----------|--------|
| Accuracy > 90%, Latency < 0.5s | Increase generation depth, add rotation disruption, reduce map opacity |
| Accuracy < 60%, Latency > 2s | Decrease generation, increase map opacity, add pre-load hints |

---

## 13. Audio Architecture

### 13.1 Web Audio API Layers
```javascript
const audioCtx = new AudioContext();

// 1. Heartbeat (60 BPM, amplitude modulated by breath)
const heartbeat = audioCtx.createOscillator();
heartbeat.frequency.value = 1.0;
const beatGain = audioCtx.createGain();
// Envelope: "lub-dub" shape

// 2. Wind/Rush (pink noise + bandpass)
const wind = audioCtx.createBufferSource();
wind.buffer = pinkNoiseBuffer;
const windFilter = audioCtx.createBiquadFilter();
windFilter.type = "bandpass";
windFilter.frequency.value = 200 + Math.abs(breathIntensity) * 300;

// 3. Squelch Footsteps
// Triggered at position thresholds (0.1, 0.2, ...)

// 4. Spatial Panner (heartbeat behind player)
const panner = audioCtx.createPanner();
panner.positionZ.value = -2; // deeper in chest
```

### 13.2 Audio Cues
| Event | Sound |
|-------|-------|
| Intersection arrival | Chime |
| Decision committed | "Da-dum" haptic pattern |
| Deep exhale | Heartbeat intensifies, becomes arrhythmic |
| Corridor blocked | Low-frequency rumble, wind stops |
| Backtrack required | Wet squelch, reversed |

---

## 14. Mobile Performance & Optimization

| Technique | Implementation |
|-----------|---------------|
| Fixed timestep | Physics at 60Hz, render at display refresh |
| Strip count | `screenWidth / 4` (e.g., 270 strips on 1080p phone) |
| Offscreen canvas | Generate wall texture once, draw as `drawImage()` slices |
| No shadows / sprites | Atmosphere from fog and color |
| Battery-aware | If `battery.level < 0.2`, drop to 15Hz recording, reduce strips |
| Touch debouncing | 50ms tap threshold prevents accidental double-commits |

---

## 15. File Structure & Asset Pipeline

### 15.1 Source Structure
```
/src
  main.js          // Game loop, canvas setup
  world.js         // Graph generation, node/edge classes
  player.js        // Movement, state machine
  breath.js        // Global breath cycle
  renderer.js      // Cylindrical raycaster
  recorder.js      // Snapshot capture, playback, ghost
  audio.js         // Web Audio API soundscape
  input.js         // Touch zones, haptics
  protocol.js      // Intersection decision protocol
  adaptive.js      // Difficulty algorithm, scheduling
  transfer.js      // Transfer test generation
  poi.js           // DICOM browsing, target selection, path planning

/assets
  (none needed — everything procedural or pre-processed from CT)

/data (pre-processed)
  game_graph.json
  wall_atlas.png
  normal_atlas.png
```

### 15.2 Pre-Processing Pipeline (Server/Desktop)
```
DICOM Stack
    → HU Thresholding
    → Trachea Seeding (top slice, largest air circle)
    → 3D Skeletonization
    → Bifurcation Detection
    → Ellipse Fitting
    → Wall Texture Extraction
    → State Remapping
    → Export JSON + PNG
```

### 15.3 Target Selection Workflow
```
Clinician loads DICOM
    → Browses slices (scroll through stack)
    → Clicks on lesion / tumor
    → System computes 3D world coordinate
    → Finds nearest bronchus edge + position
    → Ranks alternative paths
    → Clinician selects preferred route
    → System computes path from trachea to target
    → Exports mission.json alongside game_graph.json
```

---

## 16. Training Schedule & Transfer Validation

### 16.1 7-Day Training Protocol
| Day | Duration | Focus | Mechanic |
|-----|----------|-------|----------|
| 1 | 15 min | Graph encoding | Split-screen map + corridor |
| 2 | 10 min | Retrieval | Blink test, same path |
| 3 | 15 min | Mental rotation | Rotation disruption |
| 4 | 10 min | Variable practice | Random breath phase, random target |
| 5 | 20 min | Stress inoculation | Mucus + cough modes |
| 7 | 10 min | Retention test | No map, no blinks, novel target |

### 16.2 Transfer Test Protocol
Every 5 sessions:
1. Start at familiar node
2. Target: POI-derived destination via untrained path
3. No map. No blinks. No pre-load.
4. Metrics: time to target, backtrack count, decision latency

**Pass criteria:** Accuracy > 80%, latency < 1.5s per intersection.

### 16.3 The One-Second Rule
Expert endoscopists commit to a turn within **800ms** of seeing a bifurcation. The game enforces this through the decay gradient. Hesitation beyond 3 seconds auto-commits to a wrong path, simulating scope drift.

---

## Appendix A: JSON Schemas

### A.1 Player Profile
```json
{
  "playerId": "anon_hash",
  "createdAt": "2026-08-16T15:40:00Z",
  "forgettingRateLambda": 0.28,
  "stressBaseline": 0.3,
  "mentalRotationSpeed": 1.2,
  "preferredReferenceFrame": "anatomical"
}
```

### A.2 Node Memory
```json
{
  "nodeId": 42,
  "generation": 3,
  "region": "left_upper_lobe",
  "lastVisited": "2026-08-16T14:20:00Z",
  "successfulRetrievals": 7,
  "totalVisits": 9,
  "avgDecisionLatency": 0.72,
  "avgHesitationScore": 0.25,
  "currentLambda": 0.22,
  "priorityScore": 0.58,
  "masteryLevel": "developing"
}
```

### A.3 Session Plan
```json
{
  "sessionId": "sess_047",
  "targetDuration": 900,
  "blocks": [
    { "type": "warmup", "nodes": [12, 15, 8], "duration": 120 },
    { "type": "training", "nodes": [42, 33, 19, 7, 51], "duration": 480 },
    { "type": "challenge", "nodes": [67, 71], "duration": 180 },
    { "type": "novelty", "nodes": [89], "duration": 120 }
  ],
  "stressTarget": 0.45
}
```

### A.4 Transfer Test
```json
{
  "testId": "tt_003",
  "path": [0, 1, 4, 12, 33, 67],
  "novelEdges": ["e12", "e33"],
  "result": {
    "time": 145.2,
    "backtracks": 1,
    "avgLatency": 0.89,
    "passed": true
  }
}
```

### A.5 Game Graph Export
```json
{
  "seed": "ct_patient_001",
  "voxelSpacing": [0.5, 0.5, 1.0],
  "nodes": [
    {
      "id": 0,
      "generation": 0,
      "x": 0.0, "y": 0.0, "z": 0.0,
      "region": "trachea"
    }
  ],
  "edges": [
    {
      "id": "e0",
      "from": 0, "to": 1,
      "length": 0.12,
      "angle": -1.57,
      "generation": 1,
      "ctRadiusProfile": [2.1, 2.0, 1.9, 1.8, 1.7],
      "ctEccentricityProfile": [0.2, 0.25, 0.3, 0.35, 0.4],
      "wallTextureOffset": [0, 64],
      "elasticity": 0.22
    }
  ],
  "textures": {
    "wallAtlas": "data:image/png;base64,...",
    "normalAtlas": "..."
  }
}
```

### A.6 Mission / Target Export
```json
{
  "missionId": "m_001",
  "patientId": "ct_patient_001",
  "targetPoi": {
    "x": 45.2,
    "y": 32.1,
    "z": 78.5,
    "sliceIndex": 127,
    "pixelX": 256,
    "pixelY": 198
  },
  "targetDescription": "Left upper lobe peripheral nodule, 8mm",
  "nearestAirway": {
    "edgeId": "e33",
    "edgePosition": 0.65,
    "distanceMm": 2.1,
    "isReachable": true
  },
  "path": {
    "edges": ["e0", "e1", "e4", "e12", "e33"],
    "nodes": [0, 1, 4, 12, 33, 67],
    "totalLengthMm": 142.5,
    "maxGeneration": 4,
    "approachAngle": 35.0
  },
  "alternativePaths": [
    {
      "rank": 2,
      "edges": ["e0", "e1", "e4", "e13", "e34"],
      "distanceToPoiMm": 4.8,
      "description": "LUL segmental B2 approach"
    }
  ],
  "estimatedDifficulty": 0.72,
  "createdAt": "2026-08-16T10:30:00Z"
}
```

---

## Appendix B: Physics & Math Reference

### B.1 Smoothstep
```
smoothstep(a, b, x):
    t = clamp((x - a) / (b - a), 0, 1)
    return t^2 * (3 - 2t)
```

### B.2 Breath Intensity
```
inhale = smoothstep(0.0, 0.45, cycle)
exhale = smoothstep(0.55, 1.0, cycle)
intensity = inhale - exhale  // [-1.0, 1.0]
```

### B.3 Current Radius
```
currentRadius = baseRadius * (1 + intensity * elasticity)
```

### B.4 Cylindrical Wall Distance
```
normX = (screenX / screenWidth) * 2 - 1
angleFromCenter = normX * fovHalf
dist = currentRadius / cos(angleFromCenter)
stripHeight = (screenHeight / dist) * focalLength
```

### B.5 Forgetting Curve
```
memoryStrength = e^(-λ * Δt)
```

### B.6 Priority Score
```
priority = memoryStrength * difficultyWeight
difficultyWeight = base + generation*0.2 + (1-accuracy)*0.5 + hesitation*0.3
```

### B.7 Linear Interpolation
```
lerp(a, b, t) = a + (b - a) * t
```

### B.8 Ellipse Equivalent Radius
```
r_eq = sqrt(a * b)
```

### B.9 Point-to-Segment Distance
```
point_to_segment_distance(p, a, b):
    ab = b - a
    ap = p - a
    t = clamp(dot(ap, ab) / dot(ab, ab), 0, 1)
    closest = a + t * ab
    return ||p - closest||, closest, t
```

---

*Document compiled from design discussions on CT-derived world generation, mobile cylindrical rendering, neuroscience-informed spatial training, adaptive difficulty systems, and DICOM-based point-of-interest target selection.*
