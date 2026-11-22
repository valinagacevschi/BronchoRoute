// Changes: load the axial nodule contour artifact and hand it to the candidate glyphs.
// Changes: CT estimated airway is a #D2B4F0 stroke over a 1 px halo, faded no lower than 0.5.
// Changes: estimated airway overlay uses the shared session toggle and the centreline slab.
// Changes: bridge decisions beside CT controls and slice-slab midpoint rings.
// Changes: drop RouteStart confirmation controls; draw the target gap dashed grey.
// Changes: place a Lesion in any pane, drag its diameter, draw the sphere slice, no overlay gate.
/* Phase 4c review: raw click ras_mm (server snaps); hover route highlight in panes.
 * Phase 4b: hatched label 3; setAirwayOverlay; CT airway-voxel → remove branch.
 */
"use strict";

window.CtPanes = (function () {
  const PANE_COLORS = {
    axial: "#c44",
    coronal: "#3a3",
    sagittal: "#c9a000",
  };
  const LABEL_CT = 1;
  const LABEL_INTERP = 2;
  const LABEL_MANUAL = 3;
  const PROV_COLORS = {
    1: { r: 46, g: 110, b: 136, a: 0.45 },
    2: { r: 217, g: 149, b: 32, a: 0.55 },
    3: { r: 180, g: 80, b: 80, a: 0.55 },
  };
  const PROV_STROKE = {
    ct: "rgb(46,110,136)",
    interpolated: "rgb(217,149,32)",
    manual: "rgb(180,80,80)",
  };

  function strokeRouteSeg(ctx, pts, prov, opts) {
    if (!pts || pts.length < 2) return;
    const highlight = opts && opts.highlight;
    const color = highlight
      ? "rgb(46,110,136)"
      : (PROV_STROKE[prov] || PROV_STROKE.ct);
    ctx.lineJoin = "round";
    ctx.lineCap = "round";
    ctx.beginPath();
    ctx.moveTo(pts[0][0], pts[0][1]);
    for (let i = 1; i < pts.length; i++) ctx.lineTo(pts[i][0], pts[i][1]);
    ctx.strokeStyle = "rgba(0,0,0,0.85)";
    ctx.lineWidth = highlight ? 7 : 5.5;
    ctx.stroke();
    ctx.beginPath();
    ctx.moveTo(pts[0][0], pts[0][1]);
    for (let i = 1; i < pts.length; i++) ctx.lineTo(pts[i][0], pts[i][1]);
    ctx.strokeStyle = color;
    ctx.lineWidth = highlight ? 4.5 : 3.25;
    ctx.stroke();
  }

  function createSharedPosition(initial) {
    let pos = initial ? [...initial] : null;
    const listeners = new Set();
    return {
      get() {
        return pos ? [...pos] : null;
      },
      set(next, source) {
        if (!next || next.length !== 3) return;
        pos = [next[0], next[1], next[2]];
        for (const fn of listeners) fn(pos, source || "unknown");
      },
      subscribe(fn) {
        listeners.add(fn);
        return () => listeners.delete(fn);
      },
    };
  }

  function mat4MulVec(m, v) {
    const x = v[0], y = v[1], z = v[2];
    return [
      m[0][0] * x + m[0][1] * y + m[0][2] * z + m[0][3],
      m[1][0] * x + m[1][1] * y + m[1][2] * z + m[1][3],
      m[2][0] * x + m[2][1] * y + m[2][2] * z + m[2][3],
    ];
  }

  function invertAffine(m) {
    const a = m.map(row => row.slice());
    const inv = [
      [1, 0, 0, 0],
      [0, 1, 0, 0],
      [0, 0, 1, 0],
      [0, 0, 0, 1],
    ];
    for (let col = 0; col < 4; col++) {
      let pivot = col;
      for (let r = col + 1; r < 4; r++) {
        if (Math.abs(a[r][col]) > Math.abs(a[pivot][col])) pivot = r;
      }
      [a[col], a[pivot]] = [a[pivot], a[col]];
      [inv[col], inv[pivot]] = [inv[pivot], inv[col]];
      const div = a[col][col];
      if (Math.abs(div) < 1e-12) throw new Error("singular affine");
      for (let c = 0; c < 4; c++) {
        a[col][c] /= div;
        inv[col][c] /= div;
      }
      for (let r = 0; r < 4; r++) {
        if (r === col) continue;
        const f = a[r][col];
        for (let c = 0; c < 4; c++) {
          a[r][c] -= f * a[col][c];
          inv[r][c] -= f * inv[col][c];
        }
      }
    }
    return inv;
  }

  function clamp(v, lo, hi) {
    return Math.max(lo, Math.min(hi, v));
  }

  function inCropIjk(ijk, shape) {
    return (
      ijk[0] >= -0.5 && ijk[0] < shape[0] - 0.5 &&
      ijk[1] >= -0.5 && ijk[1] < shape[1] - 0.5 &&
      ijk[2] >= -0.5 && ijk[2] < shape[2] - 0.5
    );
  }

  function nearestCentrelineWorld(airway, originMm, world) {
    let best = null;
    let bestD = Infinity;
    for (const e of airway.edges) {
      for (const c of e.c) {
        const w = [c[0] + originMm[0], c[1] + originMm[1], c[2] + originMm[2]];
        const d = Math.hypot(w[0] - world[0], w[1] - world[1], w[2] - world[2]);
        if (d < bestD) {
          bestD = d;
          best = { world: w, dist: d, edge: e };
        }
      }
    }
    return best;
  }

  /**
   * Fit a plane into a canvas at a given mm-per-pixel (letterboxed).
   * When mmPerPx is omitted, use the largest scale that fits this pane alone
   * (legacy single-pane behaviour / unit tests).
   */
  function fitPlane(canvasW, canvasH, planeSizeUV, mmPerPx) {
    const physW = planeSizeUV[0];
    const physH = planeSizeUV[1] || 1;
    let scale; // px per mm
    if (mmPerPx != null && mmPerPx > 0) {
      scale = 1 / mmPerPx;
    } else {
      scale = Math.min(canvasW / physW, canvasH / physH);
    }
    const drawW = physW * scale;
    const drawH = physH * scale;
    const ox = (canvasW - drawW) / 2;
    const oy = (canvasH - drawH) / 2;
    return {
      ox, oy, drawW, drawH, w: canvasW, h: canvasH,
      mmPerPx: scale > 0 ? 1 / scale : null,
    };
  }

  const LESION_DIAMETER_DEFAULT = 10;
  const LESION_DIAMETER_MIN = 2;
  const LESION_DIAMETER_MAX = 60;

  // Signed distance follows `increment` (one step of this pane's slice index).
  // On a RAS crop that step is +superior for axial, +anterior for coronal,
  // and +patient right for sagittal. In-plane radius is sqrt(r² − d²).
  function lesionSliceGeometry(centreRas, diameterMm, planePointRas, incrementRas) {
    const step = Math.hypot(incrementRas[0], incrementRas[1], incrementRas[2]);
    const radius = Number(diameterMm) / 2;
    if (!(step > 0) || !(radius >= 0)) {
      return { through: false, signedDistanceMm: 0, inPlaneRadiusMm: 0 };
    }
    const ux = incrementRas[0] / step;
    const uy = incrementRas[1] / step;
    const uz = incrementRas[2] / step;
    const d = (centreRas[0] - planePointRas[0]) * ux
      + (centreRas[1] - planePointRas[1]) * uy
      + (centreRas[2] - planePointRas[2]) * uz;
    const through = d * d <= radius * radius + 1e-6;
    return {
      through,
      signedDistanceMm: d,
      inPlaneRadiusMm: through ? Math.sqrt(Math.max(0, radius * radius - d * d)) : 0,
    };
  }

  function lesionDiameterOf(rec) {
    const d = rec && Number(rec.diameter_mm);
    if (Number.isFinite(d) && d >= LESION_DIAMETER_MIN && d <= LESION_DIAMETER_MAX) return d;
    return LESION_DIAMETER_DEFAULT;
  }

  // CT-seen samples only. An interpolated edge contributes samples that touch
  // a non-bridge segment; a fully interpolated edge contributes none.
  function ctSeenSampleIndices(edge) {
    const n = (edge.c || []).length;
    const idx = [];
    if (!n) return idx;
    if (edge.prov === "ct") {
      for (let i = 0; i < n; i++) idx.push(i);
      return idx;
    }
    if (edge.prov !== "interpolated") return idx;
    const interp = new Set(edge.ci || []);
    if (!interp.size) return idx;
    for (let i = 0; i < n; i++) {
      const touchesInterp = (i > 0 && interp.has(i - 1)) || (i < n - 1 && interp.has(i));
      const touchesCt = (i > 0 && !interp.has(i - 1)) || (i < n - 1 && !interp.has(i));
      if (touchesCt && !touchesInterp) idx.push(i);
    }
    return idx;
  }

  function nearestCtSeenSample(edges, originMm, lesionRas) {
    const origin = originMm || [0, 0, 0];
    let best = null;
    for (const edge of edges || []) {
      const samples = edge.c || [];
      for (const i of ctSeenSampleIndices(edge)) {
        const rel = samples[i];
        if (!rel) continue;
        const world = [rel[0] + origin[0], rel[1] + origin[1], rel[2] + origin[2]];
        const distMm = Math.hypot(
          world[0] - lesionRas[0],
          world[1] - lesionRas[1],
          world[2] - lesionRas[2],
        );
        if (!best || distMm < best.distMm) {
          best = {
            distMm,
            edge,
            index: i,
            frac: i / Math.max(1, samples.length - 1),
            world,
          };
        }
      }
    }
    return best;
  }

  /** Largest mm/px that still fits every pane (shared anatomical scale). */
  function sharedMmPerPx(paneSpecs) {
    let mmPerPx = 0;
    for (const spec of paneSpecs) {
      const physW = spec.planeSizeUV[0];
      const physH = spec.planeSizeUV[1] || 1;
      const scale = Math.min(spec.w / physW, spec.h / physH); // px/mm
      if (!(scale > 0)) continue;
      const paneMm = 1 / scale;
      if (paneMm > mmPerPx) mmPerPx = paneMm;
    }
    return mmPerPx || null;
  }

  function mount(opts) {
    const {
      rootEl,
      caseId,
      airway,
      sharedPos,
      getCtView,
      setCtView,
      ensureClinician,
      onLesionChange,
      onBridgeChange,
      debug,
      isViewActive,
      readOnly = false,
      allowBridgeDecisions = !readOnly,
      showEstimatedAirway: readEstimated = () => false,
    } = opts;

    const originMm = airway.origin_mm || [0, 0, 0];
    let ctMeta = null;
    let ctData = null;
    let maskData = null;
    let invAffine = null;
    let loaded = false;
    let bridges = [];
    let bridgeBusy = false;
    const bridgeDraw = {};
    let loadError = null;
    let windowW = 1500;
    let windowL = -600;
    let ctView = getCtView() === "head" ? "head" : "feet";
    let overlays = {
      airway: true,
      route: false,
      lesion: true,
      target: true,
      candidates: true,
    };
    const estimatedDrawn = { axial: 0, coronal: 0, sagittal: 0 };
    let marking = false;
    let provisionalDiameter = LESION_DIAMETER_DEFAULT;
    let lesionDrag = null;
    let highlightRouteOis = new Set();
    let provisional = null;
    let lesion = airway.lesion ? { ...airway.lesion, ras_mm: [...airway.lesion.ras_mm] } : null;
    let focusedPane = "axial";
    let hoverPane = null;
    let wlDrag = null;
    let crosshair = sharedPos.get();
    const lesionDraw = {
      axial: { kind: "none" },
      coronal: { kind: "none" },
      sagittal: { kind: "none" },
    };
    const routeTargetDraw = {
      axial: { marker: false, gapDashed: false },
      coronal: { marker: false, gapDashed: false },
      sagittal: { marker: false, gapDashed: false },
    };
    let routeHighlightStrokes = 0;
    const offscreen = document.createElement("canvas");
    const offCtx = offscreen.getContext("2d", { willReadFrequently: true });
    // Halo and tint are assembled here so the fade blends the tint with the CT, not with the halo.
    const strokeLayer = document.createElement("canvas");
    const strokeCtx = strokeLayer.getContext("2d");

    const state = {
      slices: { axial: 0, coronal: 0, sagittal: 0 },
    };

    rootEl.innerHTML = "";
    rootEl.classList.add("ct-panes-root");

    const toolbar = document.createElement("div");
    toolbar.className = "ct-toolbar";
    rootEl.appendChild(toolbar);

    const candidates = window.CtCandidates.create({
      caseId,
      readOnly,
      ensureClinician,
      statusMessage,
      diameterOf: lesionDiameterOf,
      geometry: { sliceFrame, lesionSliceGeometry, worldToVoxelCont },
      sharedPos,
      onConfirmed: (next) => {
        lesion = next;
        provisional = null;
        marking = false;
        lesionDrag = null;
        provisionalDiameter = lesionDiameterOf(next);
        airway.lesion = next;
        airway.candidates = [];
        if (onLesionChange) onLesionChange(next);
        updateLesionStatus();
        drawAll();
      },
    });

    function renderBridges() {
      const bridgeList = document.getElementById("bridge-list");
      const group = document.getElementById("g-bridges");
      if (!bridgeList) return;
      bridgeList.replaceChildren();
      const pending = bridges.filter(b => b.matched && !b.decision);
      if (group) group.hidden = pending.length === 0;
      if (pending.length === 0) return;
      pending.forEach((bridge) => {
        const i = bridges.indexOf(bridge);
        const row = document.createElement("div");
        row.className = "bridge-row";
        row.dataset.bridgeId = bridge.id;
        const label = document.createElement("span");
        const air = bridge.evidence && Number.isFinite(bridge.evidence.air_fraction)
          ? ` · CT air ${Math.round(bridge.evidence.air_fraction * 100)}%` : "";
        label.textContent = `Bridge ${i + 1} · `;
        const state = document.createElement("span");
        state.className = "bridge-state";
        state.textContent = "Unconfirmed";
        label.appendChild(state);
        label.appendChild(document.createTextNode(
          ` · gap ${Number(bridge.gap_mm).toFixed(1)} mm${air}`,
        ));
        row.appendChild(label);
        for (const [action, text, label] of [
          ["goto", "→", "Go to"], ["confirm", "✓", "Confirm"], ["reject", "✕", "Reject"],
        ]) {
          if (!bridge.matched) continue;
          if (!allowBridgeDecisions && action !== "goto") continue;
          const button = document.createElement("button");
          button.type = "button";
          button.className = `bridge-${action}`;
          button.textContent = text;
          button.title = label;
          button.setAttribute("aria-label", label);
          const decision = action === "confirm" ? "confirmed" : "rejected";
          button.disabled = action !== "goto" && (bridgeBusy || bridge.decision === decision);
          button.onclick = () => {
            if (action === "goto") sharedPos.set(bridge.midpoint_ras_mm, "bridge");
            else decideBridge(bridge, decision);
          };
          row.appendChild(button);
        }
        bridgeList.appendChild(row);
      });
    }

    async function loadBridges() {
      const res = await fetch(`/api/cases/${encodeURIComponent(caseId)}/bridges`);
      if (!res.ok) throw new Error(`bridges ${res.status}`);
      bridges = (await res.json()).bridges;
      renderBridges();
      drawAll();
    }

    async function decideBridge(bridge, decision) {
      if (readOnly || bridgeBusy) return;
      bridgeBusy = true;
      renderBridges();
      try {
        if (ensureClinician && !(await ensureClinician())) return;
        const res = await fetch(`/api/cases/${encodeURIComponent(caseId)}/bridges/${encodeURIComponent(bridge.id)}`, {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ decision }),
        });
        const body = await res.json();
        if (!res.ok) throw new Error(body.detail || `bridge ${res.status}`);
        await onBridgeChange();
        await loadBridges();
      } catch (err) {
        statusMessage(String(err.message || err));
      } finally {
        bridgeBusy = false;
        renderBridges();
      }
    }

    const grid = document.createElement("div");
    grid.className = "ct-grid";
    rootEl.appendChild(grid);

    const panes = {};
    for (const [key, title] of [
      ["axial", "Axial (red)"],
      ["coronal", "Coronal (green)"],
      ["sagittal", "Sagittal (yellow)"],
    ]) {
      const wrap = document.createElement("div");
      wrap.className = `ct-pane ct-pane-${key}`;
      wrap.dataset.pane = key;
      wrap.style.setProperty("--pane-color", PANE_COLORS[key]);
      const label = document.createElement("div");
      label.className = "ct-pane-label";
      label.textContent = title;
      const canvas = document.createElement("canvas");
      canvas.className = "ct-canvas";
      const meta = document.createElement("div");
      meta.className = "ct-pane-meta";
      wrap.appendChild(label);
      wrap.appendChild(canvas);
      wrap.appendChild(meta);
      grid.appendChild(wrap);
      panes[key] = { wrap, canvas, ctx: canvas.getContext("2d"), meta, label };
    }

    function lesionConfirmed() {
      return !!(lesion && lesion.ras_mm);
    }

    function overlayAvailable(key) {
      if (key === "airway") return !!maskData;
      if (key === "route") return !!(airway.route && airway.route.length);
      if (key === "lesion") return lesionConfirmed();
      if (key === "candidates") return !!(airway.candidates && airway.candidates.length);
      if (key === "target") return !!(airway.endpoint || airway.route_target);
      return true;
    }

    function syncOverlayEnablement() {
      // Hide a toggle that has no data on this case instead of offering a dead control.
      for (const el of document.querySelectorAll("#ct-overlay-keys [data-overlay-key]")) {
        const available = overlayAvailable(el.dataset.overlayKey);
        const item = el.closest(".ct-key-item") || el;
        item.hidden = !available;
        el.disabled = !available;
      }
      syncOverlaySeparators();
    }

    function syncConfirmEnabled() {
      const btn = toolbar.querySelector("#ct-confirm-lesion");
      if (btn) btn.disabled = !provisional;
      const cancel = toolbar.querySelector("#ct-cancel-mark");
      if (cancel) cancel.disabled = !marking;
    }

    const OVERLAY_KEYS = {
      airway: "A",
      route: "R",
      candidates: "C",
      lesion: "L",
      target: "T",
    };

    function syncOverlaySeparators() {
      const items = [...document.querySelectorAll("#ct-overlay-keys .ct-key-item")]
        .filter(item => !item.hidden);
      items.forEach((item, index) => {
        const sep = item.querySelector(".ct-key-sep");
        if (sep) sep.hidden = index === items.length - 1;
      });
    }

    function syncOverlayKeys() {
      for (const el of document.querySelectorAll("#ct-overlay-keys [data-overlay-key]")) {
        const on = !!overlays[el.dataset.overlayKey];
        el.classList.toggle("on", on);
        el.setAttribute("aria-pressed", on ? "true" : "false");
      }
    }

    function buildOverlayControls() {
      const host = document.getElementById("ct-overlay-keys");
      if (!host) return;
      host.replaceChildren();
      const entries = [
        ["airway", "Airway mask"],
        ["route", "Route"],
        ["candidates", "Candidates"],
        ["lesion", "Lesion"],
        ["target", "RouteTarget"],
      ];
      entries.forEach(([key, label]) => {
        const item = document.createElement("span");
        item.className = "ct-key-item";
        const b = document.createElement("button");
        b.type = "button";
        b.className = "ct-key";
        b.dataset.overlayKey = key;
        b.textContent = OVERLAY_KEYS[key];
        b.title = label;
        b.setAttribute("aria-label", label);
        b.onclick = () => {
          overlays[key] = !overlays[key];
          syncOverlayKeys();
          drawAll();
        };
        item.appendChild(b);
        item.appendChild(document.createTextNode(" " + label));
        const sep = document.createElement("span");
        sep.className = "ct-key-sep";
        sep.textContent = " · ";
        item.appendChild(sep);
        host.appendChild(item);
      });
      syncOverlayKeys();
      syncOverlaySeparators();
    }

    function buildToolbar() {
      toolbar.innerHTML = "";
      buildOverlayControls();

      const lesionBox = document.createElement("div");
      lesionBox.className = "ct-lesion-box";
      const markBtn = document.createElement("button");
      markBtn.type = "button";
      markBtn.id = "ct-mark-lesion";
      markBtn.textContent = "Mark Lesion";
      markBtn.onclick = () => {
        marking = true;
        provisional = null;
        provisionalDiameter = LESION_DIAMETER_DEFAULT;
        lesionDrag = null;
        statusMessage("Click a CT pane to place the Lesion");
        syncConfirmEnabled();
        drawAll();
      };
      const confirmBtn = document.createElement("button");
      confirmBtn.type = "button";
      confirmBtn.id = "ct-confirm-lesion";
      confirmBtn.textContent = "Confirm Lesion";
      confirmBtn.disabled = true;
      confirmBtn.onclick = () => {
        confirmLesion().catch(err => statusMessage(String(err.message || err)));
      };
      const cancelBtn = document.createElement("button");
      cancelBtn.type = "button";
      cancelBtn.id = "ct-cancel-mark";
      cancelBtn.className = "secondary";
      cancelBtn.textContent = "Cancel";
      cancelBtn.disabled = true;
      cancelBtn.onclick = () => cancelMark();
      const clearBtn = document.createElement("button");
      clearBtn.type = "button";
      clearBtn.className = "secondary";
      clearBtn.textContent = "Clear";
      clearBtn.onclick = () => {
        clearLesion().catch(err => statusMessage(String(err.message || err)));
      };
      const statusEl = document.createElement("span");
      statusEl.className = "ct-lesion-status";
      statusEl.id = "ct-lesion-status";
      lesionBox.appendChild(markBtn);
      lesionBox.appendChild(confirmBtn);
      lesionBox.appendChild(cancelBtn);
      lesionBox.appendChild(clearBtn);
      lesionBox.appendChild(statusEl);

      if (!readOnly) {
        toolbar.appendChild(lesionBox);
        candidates.mount(toolbar);
      }
      syncOverlayEnablement();
      syncConfirmEnabled();
      updateLesionStatus();
      updateWlReadout();
      updateCtViewReadout();
      candidates.setItems(airway.candidates);
      toolbar.hidden = toolbar.childElementCount === 0;
    }

    function updateWlReadout() {
      const el = document.getElementById("ct-wl-readout");
      if (el) el.textContent = `W ${Math.round(windowW)} / L ${Math.round(windowL)}`;
    }

    function updateCtViewReadout() {
      const el = document.getElementById("ct-view-readout");
      if (el) el.textContent = ctView === "head" ? "Bronchoscopist" : "Radiologist";
    }

    function updateLesionStatus() {
      const el = toolbar.querySelector("#ct-lesion-status");
      if (!el) return;
      if (marking && provisional) {
        el.textContent = `Ø ${provisionalDiameter.toFixed(1)} mm`;
      } else if (lesion && lesion.ras_mm) {
        const who = lesion.confirmed_by || "";
        const when = lesion.at || "";
        const dia = lesionDiameterOf(lesion).toFixed(1);
        el.textContent = `Lesion confirmed · Ø ${dia} mm${who ? " · " + who : ""}${when ? " · " + when : ""}`;
      } else if (marking) {
        el.textContent = "Click a CT pane to place the Lesion";
      } else {
        el.textContent = "";
      }
      syncOverlayEnablement();
      syncConfirmEnabled();
    }

    function cancelMark() {
      marking = false;
      provisional = null;
      provisionalDiameter = LESION_DIAMETER_DEFAULT;
      lesionDrag = null;
      updateLesionStatus();
      drawAll();
    }

    async function confirmLesion() {
      if (readOnly) return;
      if (!provisional) {
        statusMessage("Place a provisional marker first");
        return;
      }
      let ok;
      try {
        ok = await ensureClinician();
      } catch (err) {
        statusMessage(String(err.message || err));
        return;
      }
      if (!ok) return;
      let res;
      try {
        res = await fetch(`/api/cases/${encodeURIComponent(caseId)}/lesion`, {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
          ras_mm: provisional,
          diameter_mm: Math.round(provisionalDiameter * 10) / 10,
        }),
        });
      } catch (err) {
        statusMessage(String(err.message || err));
        return;
      }
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        statusMessage(body.detail || `Confirm failed (${res.status})`);
        return;
      }
      const body = await res.json();
      lesion = body.lesion;
      provisional = null;
      marking = false;
      lesionDrag = null;
      provisionalDiameter = lesionDiameterOf(lesion);
      airway.lesion = lesion;
      if (onLesionChange) onLesionChange(lesion);
      updateLesionStatus();
      drawAll();
    }

    async function clearLesion() {
      if (readOnly) return;
      let ok;
      try {
        ok = await ensureClinician();
      } catch (err) {
        statusMessage(String(err.message || err));
        return;
      }
      if (!ok) return;
      let res;
      try {
        res = await fetch(`/api/cases/${encodeURIComponent(caseId)}/lesion`, {
          method: "DELETE",
        });
      } catch (err) {
        statusMessage(String(err.message || err));
        return;
      }
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        statusMessage(body.detail || `Clear failed (${res.status})`);
        return;
      }
      lesion = null;
      provisional = null;
      marking = false;
      lesionDrag = null;
      provisionalDiameter = LESION_DIAMETER_DEFAULT;
      airway.lesion = null;
      if (onLesionChange) onLesionChange(null);
      buildToolbar();
      drawAll();
    }

    function statusMessage(msg) {
      const el = toolbar.querySelector("#ct-lesion-status");
      if (el) el.textContent = String(msg);
    }

    function worldToVoxelCont(world) {
      return mat4MulVec(invAffine, world);
    }

    function worldToVoxel(world) {
      const ijk = worldToVoxelCont(world);
      return [
        clamp(Math.floor(ijk[0] + 1e-9), 0, ctMeta.shape[0] - 1),
        clamp(Math.floor(ijk[1] + 1e-9), 0, ctMeta.shape[1] - 1),
        clamp(Math.floor(ijk[2] + 1e-9), 0, ctMeta.shape[2] - 1),
      ];
    }

    function voxelToWorld(i, j, k) {
      return mat4MulVec(ctMeta.affine, [i + 0.5, j + 0.5, k + 0.5]);
    }

    function sampleCt(i, j, k) {
      const nx = ctMeta.shape[0], ny = ctMeta.shape[1];
      return ctData[i + j * nx + k * nx * ny];
    }

    function sampleMask(i, j, k) {
      if (!maskData) return 0;
      const nx = ctMeta.shape[0], ny = ctMeta.shape[1];
      return maskData[i + j * nx + k * nx * ny];
    }

    function huToGray(hu) {
      const lo = windowL - windowW / 2;
      const hi = windowL + windowW / 2;
      const t = (hu - lo) / (hi - lo || 1);
      return clamp(Math.round(t * 255), 0, 255);
    }

    function setCrosshairWorld(world, source) {
      crosshair = [world[0], world[1], world[2]];
      const [i, j, k] = worldToVoxel(crosshair);
      state.slices.axial = k;
      state.slices.coronal = j;
      state.slices.sagittal = i;
      sharedPos.set(crosshair, source || "ct");
      // Wheel and keys never move the provisional centre. Only an explicit place does.
      if (marking && (source === "ct-lesion-place" || source === "ct-axial-place")) {
        provisional = [...crosshair];
        updateLesionStatus();
      }
      drawAll();
    }

    function syncSlicesFromShared(world) {
      if (!loaded || !world) return;
      crosshair = [world[0], world[1], world[2]];
      const [i, j, k] = worldToVoxel(crosshair);
      state.slices.axial = k;
      state.slices.coronal = j;
      state.slices.sagittal = i;
      drawAll();
    }

    function planeMap(pane) {
      const feet = ctView === "feet";
      const nx = ctMeta.shape[0], ny = ctMeta.shape[1], nz = ctMeta.shape[2];
      const sx = ctMeta.spacing_mm[0], sy = ctMeta.spacing_mm[1], sz = ctMeta.spacing_mm[2];
      if (pane === "axial") {
        return {
          ijkFromUV(u, v, fixed) {
            const iNorm = feet ? 1 - u : u;
            const jNorm = 1 - v;
            return [
              clamp(Math.floor(iNorm * nx), 0, nx - 1),
              clamp(Math.floor(jNorm * ny), 0, ny - 1),
              fixed,
            ];
          },
          // i,j,k are continuous corner coords (voxel centre = i+0.5 → (i+0.5)/n).
          uvFromIjk(i, j, _k) {
            const iNorm = i / nx;
            const jNorm = j / ny;
            return {
              u: feet ? 1 - iNorm : iNorm,
              v: 1 - jNorm,
            };
          },
          planeSizeUV: [nx * sx, ny * sy],
          letters: feet
            ? { left: "R", right: "L", top: "A", bottom: "P" }
            : { left: "L", right: "R", top: "A", bottom: "P" },
          fixedAxis: 2,
          sliceKey: "axial",
        };
      }
      if (pane === "coronal") {
        return {
          ijkFromUV(u, v, fixed) {
            const iNorm = feet ? 1 - u : u;
            const kNorm = 1 - v;
            return [
              clamp(Math.floor(iNorm * nx), 0, nx - 1),
              fixed,
              clamp(Math.floor(kNorm * nz), 0, nz - 1),
            ];
          },
          uvFromIjk(i, _j, k) {
            const iNorm = i / nx;
            const kNorm = k / nz;
            return {
              u: feet ? 1 - iNorm : iNorm,
              v: 1 - kNorm,
            };
          },
          planeSizeUV: [nx * sx, nz * sz],
          letters: feet
            ? { left: "R", right: "L", top: "S", bottom: "I" }
            : { left: "L", right: "R", top: "S", bottom: "I" },
          fixedAxis: 1,
          sliceKey: "coronal",
        };
      }
      return {
        ijkFromUV(u, v, fixed) {
          const jNorm = 1 - u;
          const kNorm = 1 - v;
          return [
            fixed,
            clamp(Math.floor(jNorm * ny), 0, ny - 1),
            clamp(Math.floor(kNorm * nz), 0, nz - 1),
          ];
        },
        uvFromIjk(_i, j, k) {
          const jNorm = j / ny;
          const kNorm = k / nz;
          return { u: 1 - jNorm, v: 1 - kNorm };
        },
        planeSizeUV: [ny * sy, nz * sz],
        letters: { left: "A", right: "P", top: "S", bottom: "I" },
        fixedAxis: 0,
        sliceKey: "sagittal",
      };
    }

    function paneRect(canvas) {
      return { w: canvas.clientWidth, h: canvas.clientHeight };
    }

    function currentSharedMmPerPx() {
      if (!ctMeta) return null;
      const specs = [];
      for (const key of Object.keys(panes)) {
        const { w, h } = paneRect(panes[key].canvas);
        if (w < 2 || h < 2) continue;
        specs.push({ w, h, planeSizeUV: planeMap(key).planeSizeUV });
      }
      return sharedMmPerPx(specs);
    }

    function fitFor(canvas, planeSizeUV) {
      const { w, h } = paneRect(canvas);
      return fitPlane(w, h, planeSizeUV, currentSharedMmPerPx());
    }

    function drawOutlinedText(ctx, text, x, y, align) {
      ctx.save();
      ctx.textAlign = align || "center";
      ctx.textBaseline = "middle";
      ctx.lineWidth = 3;
      ctx.strokeStyle = "rgba(0,0,0,0.85)";
      ctx.fillStyle = "#fff";
      ctx.strokeText(text, x, y);
      ctx.fillText(text, x, y);
      ctx.restore();
    }

    function sliceFrame(map, fixed) {
      const ijk = [0, 0, 0];
      ijk[map.fixedAxis] = fixed;
      const next = ijk.slice();
      next[map.fixedAxis] = fixed + 1;
      const p0 = voxelToWorld(ijk[0], ijk[1], ijk[2]);
      const p1 = voxelToWorld(next[0], next[1], next[2]);
      return {
        planePoint: p0,
        increment: [p1[0] - p0[0], p1[1] - p0[1], p1[2] - p0[2]],
      };
    }

    function lesionDistanceLabel(signedMm) {
      const n = Math.round(signedMm);
      const sign = n > 0 ? "+" : "";
      return `Lesion ${sign}${n} mm`;
    }

    function drawLesionGlyph(ctx, key, map, fit, fixed) {
      const world = provisional || (lesion && lesion.ras_mm);
      if (!overlays.lesion || !world) {
        lesionDraw[key] = { kind: "none" };
        return;
      }
      const diameter = provisional ? provisionalDiameter : lesionDiameterOf(lesion);
      const frame = sliceFrame(map, fixed);
      const geom = lesionSliceGeometry(world, diameter, frame.planePoint, frame.increment);
      const ijk = worldToVoxelCont(world);
      const uv = map.uvFromIjk(ijk[0], ijk[1], ijk[2]);
      const sx = fit.ox + uv.u * fit.drawW;
      const sy = fit.oy + uv.v * fit.drawH;
      const record = {
        kind: geom.through ? "circle" : "cross",
        cx: sx,
        cy: sy,
        signedDistanceMm: geom.signedDistanceMm,
        inPlaneRadiusMm: geom.inPlaneRadiusMm,
        diameterMm: diameter,
        label: geom.through ? "Lesion" : lesionDistanceLabel(geom.signedDistanceMm),
        radiusPx: 0,
      };
      ctx.save();
      ctx.strokeStyle = "#e85d4c";
      ctx.fillStyle = "#e85d4c";
      if (geom.through) {
        const radiusPx = geom.inPlaneRadiusMm / (fit.mmPerPx || 1);
        record.radiusPx = radiusPx;
        ctx.lineWidth = 2;
        ctx.beginPath();
        ctx.arc(sx, sy, Math.max(radiusPx, 0.5), 0, Math.PI * 2);
        ctx.stroke();
        if (marking && provisional) {
          ctx.font = "12px monospace";
          ctx.fillText(`Ø ${diameter.toFixed(1)} mm`, sx + radiusPx + 6, sy);
        }
      } else {
        // Off the sphere: faint cross and signed distance, not a faded circle.
        ctx.globalAlpha = 0.4;
        ctx.lineWidth = 1.25;
        const arm = 7;
        ctx.beginPath();
        ctx.moveTo(sx - arm, sy);
        ctx.lineTo(sx + arm, sy);
        ctx.moveTo(sx, sy - arm);
        ctx.lineTo(sx, sy + arm);
        ctx.stroke();
        ctx.globalAlpha = 0.85;
        ctx.font = "11px monospace";
        ctx.fillText(record.label, sx + 10, sy - 6);
      }
      ctx.restore();
      lesionDraw[key] = record;
    }

    function drawPane(key) {
      const pane = panes[key];
      const canvas = pane.canvas;
      const dpr = Math.min(2, devicePixelRatio || 1);
      const { w, h } = paneRect(canvas);
      if (w < 2 || h < 2) return;
      canvas.width = Math.round(w * dpr);
      canvas.height = Math.round(h * dpr);
      const ctx = pane.ctx;
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      ctx.fillStyle = "#111";
      ctx.fillRect(0, 0, w, h);

      if (!loaded) {
        ctx.fillStyle = "#888";
        ctx.font = "13px sans-serif";
        ctx.fillText(loadError || "Loading CT…", 12, 24);
        return;
      }

      const map = planeMap(key);
      const fit = fitFor(canvas, map.planeSizeUV);
      const fixed = state.slices[map.sliceKey];
      const imgW = Math.max(1, Math.round(fit.drawW * dpr));
      const imgH = Math.max(1, Math.round(fit.drawH * dpr));
      const image = ctx.createImageData(imgW, imgH);
      const px = image.data;

      for (let py = 0; py < imgH; py++) {
        for (let px_ = 0; px_ < imgW; px_++) {
          const u = (px_ + 0.5) / imgW;
          const v = (py + 0.5) / imgH;
          const [i, j, k] = map.ijkFromUV(u, v, fixed);
          const g = huToGray(sampleCt(i, j, k));
          const idx = (py * imgW + px_) * 4;
          px[idx] = g;
          px[idx + 1] = g;
          px[idx + 2] = g;
          px[idx + 3] = 255;
          if (overlays.airway) {
            const lab = sampleMask(i, j, k);
            const col = PROV_COLORS[lab];
            if (col) {
              // Hatched pattern for manual (removed) label 3.
              const hatch = lab === LABEL_MANUAL && ((px_ + py) % 6 < 3);
              const a = hatch ? Math.min(0.85, col.a + 0.25) : col.a;
              const r = hatch ? Math.min(255, col.r + 40) : col.r;
              const g = hatch ? Math.max(0, col.g - 20) : col.g;
              const b = hatch ? Math.max(0, col.b - 20) : col.b;
              px[idx] = Math.round(px[idx] * (1 - a) + r * a);
              px[idx + 1] = Math.round(px[idx + 1] * (1 - a) + g * a);
              px[idx + 2] = Math.round(px[idx + 2] * (1 - a) + b * a);
            }
          }
        }
      }
      offscreen.width = imgW;
      offscreen.height = imgH;
      offCtx.putImageData(image, 0, 0);
      ctx.drawImage(offscreen, fit.ox, fit.oy, fit.drawW, fit.drawH);

      function strokeEdgePolyline(e, highlight) {
        if (!e || !e.c) return;
        const pts = [];
        for (const c of e.c) {
          const world = [c[0] + originMm[0], c[1] + originMm[1], c[2] + originMm[2]];
          const ijk = worldToVoxelCont(world);
          if (!inCropIjk(ijk, ctMeta.shape) || Math.abs(ijk[map.fixedAxis] - fixed) > 1.0) {
            if (pts.length > 1) {
            if (highlight) routeHighlightStrokes += 1;
            strokeRouteSeg(ctx, pts, e.prov || "ct", { highlight });
          }
            pts.length = 0;
            continue;
          }
          const uv = map.uvFromIjk(ijk[0], ijk[1], ijk[2]);
          pts.push([fit.ox + uv.u * fit.drawW, fit.oy + uv.v * fit.drawH]);
        }
        if (pts.length > 1) {
          if (highlight) routeHighlightStrokes += 1;
          strokeRouteSeg(ctx, pts, e.prov || "ct", { highlight });
        }
      }

      if (overlays.route && airway.route) {
        for (const ei of airway.route || []) {
          strokeEdgePolyline(airway.edges[ei], false);
        }
      }
      // Candidate hover: draw that route in highlight colour in all three panes.
      if (highlightRouteOis.size && airway.edges) {
        for (const e of airway.edges) {
          if (e && e.oi != null && highlightRouteOis.has(Number(e.oi))) {
            strokeEdgePolyline(e, true);
          }
        }
      }

      // Same ±1 voxel slab as the centreline overlay. Strokes are not pick targets.
      // A translucent tint drawn over the halo turns the centre dark on lung grey.
      estimatedDrawn[key] = 0;
      if (readEstimated()) {
        if (strokeLayer.width !== canvas.width || strokeLayer.height !== canvas.height) {
          strokeLayer.width = canvas.width;
          strokeLayer.height = canvas.height;
        }
        const strokeEstimatedRun = (pts) => {
          if (!pts || pts.length < 2) return;
          let dashAlong = 0;
          for (let i = 1; i < pts.length; i++) {
            const dist = pts[i][2];
            const t = Number.isFinite(dist) ? Math.max(0, Math.min(1, dist / 15)) : 0;
            const alpha = 1 - 0.5 * t;
            const x0 = pts[i - 1][0];
            const y0 = pts[i - 1][1];
            const x1 = pts[i][0];
            const y1 = pts[i][1];
            const step = Math.hypot(x1 - x0, y1 - y0);
            const pad = 6;
            const x0d = Math.max(0, Math.floor((Math.min(x0, x1) - pad) * dpr));
            const y0d = Math.max(0, Math.floor((Math.min(y0, y1) - pad) * dpr));
            const x1d = Math.min(canvas.width, Math.ceil((Math.max(x0, x1) + pad) * dpr));
            const y1d = Math.min(canvas.height, Math.ceil((Math.max(y0, y1) + pad) * dpr));
            const sw = x1d - x0d;
            const sh = y1d - y0d;
            if (sw > 0 && sh > 0) {
              strokeCtx.setTransform(1, 0, 0, 1, 0, 0);
              strokeCtx.clearRect(x0d, y0d, sw, sh);
              strokeCtx.setTransform(dpr, 0, 0, dpr, 0, 0);
              strokeCtx.globalAlpha = 1;
              strokeCtx.globalCompositeOperation = "source-over";
              strokeCtx.lineCap = "butt";
              strokeCtx.lineJoin = "round";
              strokeCtx.setLineDash([5, 3]);
              strokeCtx.lineDashOffset = -dashAlong;
              strokeCtx.beginPath();
              strokeCtx.moveTo(x0, y0);
              strokeCtx.lineTo(x1, y1);
              strokeCtx.lineWidth = 4;
              strokeCtx.strokeStyle = "rgba(0,0,0,0.6)";
              strokeCtx.stroke();
              strokeCtx.lineWidth = 2;
              strokeCtx.strokeStyle = "#D2B4F0";
              strokeCtx.stroke();
              // Integer device pixels, no smoothing: a fractional blit shifts the dash off the polyline.
              ctx.save();
              ctx.setTransform(1, 0, 0, 1, 0, 0);
              ctx.imageSmoothingEnabled = false;
              ctx.globalAlpha = alpha;
              ctx.drawImage(strokeLayer, x0d, y0d, sw, sh, x0d, y0d, sw, sh);
              ctx.restore();
            }
            dashAlong += step;
            estimatedDrawn[key] += 2;
          }
        };
        ctx.save();
        for (const segment of (airway.estimated || [])) {
          const line = segment.c || [];
          const distances = segment.d || [];
          const pts = [];
          for (let i = 0; i < line.length; i++) {
            const c = line[i];
            const world = [c[0] + originMm[0], c[1] + originMm[1], c[2] + originMm[2]];
            const ijk = worldToVoxelCont(world);
            if (!inCropIjk(ijk, ctMeta.shape) || Math.abs(ijk[map.fixedAxis] - fixed) > 1.0) {
              strokeEstimatedRun(pts);
              pts.length = 0;
              continue;
            }
            const uv = map.uvFromIjk(ijk[0], ijk[1], ijk[2]);
            const dist = Number(distances[i] != null ? distances[i] : distances[i - 1]);
            pts.push([
              fit.ox + uv.u * fit.drawW,
              fit.oy + uv.v * fit.drawH,
              Number.isFinite(dist) ? dist : 0,
            ]);
          }
          strokeEstimatedRun(pts);
        }
        ctx.restore();
      }

      function drawMarker(world, color, label) {
        if (!world) return;
        const ijk = worldToVoxelCont(world);
        if (!inCropIjk(ijk, ctMeta.shape)) {
          // Edge arrow toward off-crop marker with distance.
          const uv = map.uvFromIjk(
            clamp(ijk[0], 0, ctMeta.shape[0] - 1),
            clamp(ijk[1], 0, ctMeta.shape[1] - 1),
            clamp(ijk[2], 0, ctMeta.shape[2] - 1),
          );
          const edgeU = clamp(uv.u, 0.02, 0.98);
          const edgeV = clamp(uv.v, 0.02, 0.98);
          const sx = fit.ox + edgeU * fit.drawW;
          const sy = fit.oy + edgeV * fit.drawH;
          const distMm = Math.hypot(
            (ijk[0] - clamp(ijk[0], -0.5, ctMeta.shape[0] - 0.5)) * ctMeta.spacing_mm[0],
            (ijk[1] - clamp(ijk[1], -0.5, ctMeta.shape[1] - 0.5)) * ctMeta.spacing_mm[1],
            (ijk[2] - clamp(ijk[2], -0.5, ctMeta.shape[2] - 0.5)) * ctMeta.spacing_mm[2],
          );
          ctx.fillStyle = color;
          ctx.beginPath();
          ctx.moveTo(sx, sy - 7);
          ctx.lineTo(sx + 6, sy + 5);
          ctx.lineTo(sx - 6, sy + 5);
          ctx.closePath();
          ctx.fill();
          ctx.font = "10px monospace";
          ctx.fillText(`${distMm.toFixed(1)} mm`, sx + 8, sy);
          if (label) ctx.fillText(label, sx + 8, sy + 12);
          return;
        }
        const axisVal = ijk[map.fixedAxis];
        const distSlices = axisVal - fixed;
        const distMm = distSlices * ctMeta.spacing_mm[map.fixedAxis];
        const uv = map.uvFromIjk(ijk[0], ijk[1], ijk[2]);
        const sx = fit.ox + uv.u * fit.drawW;
        const sy = fit.oy + uv.v * fit.drawH;
        const onSlice = Math.abs(distSlices) <= 0.5;
        ctx.globalAlpha = onSlice ? 1 : clamp(1 - Math.abs(distMm) / 20, 0.25, 0.7);
        ctx.strokeStyle = color;
        ctx.lineWidth = 2;
        ctx.beginPath();
        ctx.arc(sx, sy, onSlice ? 8 : 6, 0, Math.PI * 2);
        ctx.stroke();
        if (!onSlice) {
          ctx.fillStyle = color;
          ctx.font = "10px monospace";
          ctx.fillText(`${distMm >= 0 ? "+" : ""}${distMm.toFixed(1)} mm`, sx + 10, sy - 4);
        }
        if (label) {
          ctx.fillStyle = color;
          ctx.font = "10px sans-serif";
          ctx.fillText(label, sx + 10, sy + 12);
        }
        ctx.globalAlpha = 1;
      }

      drawLesionGlyph(ctx, key, map, fit, fixed);
      candidates.draw(ctx, {
        key, map, fit, fixed,
        visible: overlays.candidates,
        diameterOf: lesionDiameterOf,
      });
      bridgeDraw[key] = [];
      for (const bridge of bridges) {
        if (bridge.decision) continue;
        const ijk = worldToVoxelCont(bridge.midpoint_ras_mm);
        if (Math.abs(ijk[map.fixedAxis] - fixed) > 0.5) continue;
        const uv = map.uvFromIjk(...ijk);
        const x = fit.ox + uv.u * fit.drawW;
        const y = fit.oy + uv.v * fit.drawH;
        const color = "#d99520";
        ctx.save();
        ctx.strokeStyle = color;
        ctx.lineWidth = 3;
        ctx.beginPath();
        ctx.arc(x, y, 9, 0, 2 * Math.PI);
        ctx.stroke();
        ctx.restore();
        bridgeDraw[key].push({ id: bridge.id, color, x, y });
      }
      routeTargetDraw[key] = { marker: false, gapDashed: false };
      if (airway.route_start && airway.route_start.ras_mm) {
        drawMarker(airway.route_start.ras_mm, "#B4522F", "RouteStart");
      }
      if (overlays.target) {
        if (airway.endpoint) {
          const ep = [
            airway.endpoint[0] + originMm[0],
            airway.endpoint[1] + originMm[1],
            airway.endpoint[2] + originMm[2],
          ];
          drawMarker(ep, "#2E6E88", "RouteTarget");
          routeTargetDraw[key].marker = true;
          const L = lesion && lesion.ras_mm;
          if (L && overlays.lesion) {
            const ijkL = worldToVoxelCont(L);
            const ijkE = worldToVoxelCont(ep);
            const nearL = Math.abs(ijkL[map.fixedAxis] - fixed) <= 1;
            const nearE = Math.abs(ijkE[map.fixedAxis] - fixed) <= 1;
            if (inCropIjk(ijkL, ctMeta.shape) && inCropIjk(ijkE, ctMeta.shape) && (nearL || nearE)) {
              const uvL = map.uvFromIjk(ijkL[0], ijkL[1], ijkL[2]);
              const uvE = map.uvFromIjk(ijkE[0], ijkE[1], ijkE[2]);
              ctx.strokeStyle = "#727d84";
              ctx.lineWidth = 1;
              ctx.setLineDash([4, 4]);
              ctx.beginPath();
              ctx.moveTo(fit.ox + uvL.u * fit.drawW, fit.oy + uvL.v * fit.drawH);
              ctx.lineTo(fit.ox + uvE.u * fit.drawW, fit.oy + uvE.v * fit.drawH);
              ctx.stroke();
              ctx.setLineDash([]);
              routeTargetDraw[key].gapDashed = true;
            }
          }
        } else if (airway.nodes && airway.nodes[String(airway.target)]) {
          const p = airway.nodes[String(airway.target)].p;
          drawMarker(
            [p[0] + originMm[0], p[1] + originMm[1], p[2] + originMm[2]],
            "#2E6E88",
            "Target",
          );
        }
      }

      if (crosshair) {
        const ijk = worldToVoxelCont(crosshair);
        if (inCropIjk(ijk, ctMeta.shape)) {
          const uv = map.uvFromIjk(ijk[0], ijk[1], ijk[2]);
          const sx = fit.ox + uv.u * fit.drawW;
          const sy = fit.oy + uv.v * fit.drawH;
          const others = {
            axial: [PANE_COLORS.coronal, PANE_COLORS.sagittal],
            coronal: [PANE_COLORS.axial, PANE_COLORS.sagittal],
            sagittal: [PANE_COLORS.axial, PANE_COLORS.coronal],
          }[key];
          ctx.strokeStyle = others[0];
          ctx.lineWidth = 1;
          ctx.beginPath();
          ctx.moveTo(fit.ox, sy);
          ctx.lineTo(fit.ox + fit.drawW, sy);
          ctx.stroke();
          ctx.strokeStyle = others[1];
          ctx.beginPath();
          ctx.moveTo(sx, fit.oy);
          ctx.lineTo(sx, fit.oy + fit.drawH);
          ctx.stroke();
        }
      }

      // Orientation letters inset from corners (off crosshair mid-lines).
      const L = map.letters;
      const inset = 14;
      ctx.font = "600 12px sans-serif";
      drawOutlinedText(ctx, L.top, fit.ox + fit.drawW / 2, fit.oy + inset, "center");
      drawOutlinedText(ctx, L.bottom, fit.ox + fit.drawW / 2, fit.oy + fit.drawH - inset, "center");
      drawOutlinedText(ctx, L.left, fit.ox + inset, fit.oy + fit.drawH / 2, "center");
      drawOutlinedText(ctx, L.right, fit.ox + fit.drawW - inset, fit.oy + fit.drawH / 2, "center");
      ctx.font = "10px monospace";
      drawOutlinedText(
        ctx,
        ctView === "feet" ? "viewed from feet" : "viewed from head",
        10,
        h - 10,
        "left",
      );

      const spacing = ctMeta.spacing_mm[map.fixedAxis];
      const worldFixed = voxelToWorld(
        key === "sagittal" ? fixed : state.slices.sagittal,
        key === "coronal" ? fixed : state.slices.coronal,
        key === "axial" ? fixed : state.slices.axial,
      );
      pane.meta.textContent =
        `slice ${fixed} · ${worldFixed[map.fixedAxis].toFixed(1)} mm ` +
        `(${spacing.toFixed(2)} mm)`;
    }

    function drawAll() {
      updateWlReadout();
      routeHighlightStrokes = 0;
      estimatedDrawn.axial = 0;
      estimatedDrawn.coronal = 0;
      estimatedDrawn.sagittal = 0;
      for (const key of Object.keys(panes)) drawPane(key);
    }

    function eventToWorld(paneKey, clientX, clientY) {
      const pane = panes[paneKey];
      const rect = pane.canvas.getBoundingClientRect();
      const x = clientX - rect.left;
      const y = clientY - rect.top;
      const map = planeMap(paneKey);
      const fit = fitFor(pane.canvas, map.planeSizeUV);
      const u = (x - fit.ox) / fit.drawW;
      const v = (y - fit.oy) / fit.drawH;
      if (u < 0 || u > 1 || v < 0 || v > 1) return null;
      const fixed = state.slices[map.sliceKey];
      const [i, j, k] = map.ijkFromUV(u, v, fixed);
      return voxelToWorld(i, j, k);
    }

    function onPointerDown(e) {
      const wrap = e.target.closest(".ct-pane");
      if (!wrap) return;
      const key = wrap.dataset.pane;
      focusedPane = key;
      if (e.button === 2 || e.shiftKey) {
        wlDrag = { x: e.clientX, y: e.clientY, W: windowW, L: windowL };
        e.preventDefault();
        return;
      }
      if (e.button !== 0) return;
      const world = eventToWorld(key, e.clientX, e.clientY);
      if (!world) return;
      if (marking) {
        const near = provisional && pointerNearLesion(key, e.clientX, e.clientY);
        if (!provisional || !near) {
          provisionalDiameter = LESION_DIAMETER_DEFAULT;
          lesionDrag = { pane: key, x: e.clientX, y: e.clientY, sizing: false };
          setCrosshairWorld(world, "ct-lesion-place");
          return;
        }
        lesionDrag = { pane: key, x: e.clientX, y: e.clientY, sizing: true };
        return;
      }
      setCrosshairWorld(world, "ct");
      // Clicking an airway voxel selects the nearest graph edge for removal.
      if (!readOnly && maskData) {
        const ijk = worldToVoxelCont(world);
        const ii = Math.round(ijk[0]), jj = Math.round(ijk[1]), kk = Math.round(ijk[2]);
        const lab = sampleMask(ii, jj, kk);
        if (lab === LABEL_CT || lab === LABEL_INTERP || lab === LABEL_MANUAL) {
          const hit = nearestCentrelineWorld(airway, originMm, world);
          if (hit && hit.edge && hit.edge.oi != null && hit.edge.selectable !== false) {
            window.postMessage(
              { type: "ct-select-edge", oi: hit.edge.oi },
              location.origin,
            );
          }
        }
      }
    }

    function pointerNearLesion(paneKey, clientX, clientY) {
      if (!provisional) return false;
      const scr = screenOfWorld(paneKey, provisional);
      if (!scr) return false;
      const rect = panes[paneKey].canvas.getBoundingClientRect();
      const px = Math.hypot(clientX - rect.left - scr.x, clientY - rect.top - scr.y);
      const fit = fitFor(panes[paneKey].canvas, planeMap(paneKey).planeSizeUV);
      const radiusPx = (provisionalDiameter / 2) / (fit.mmPerPx || 1);
      return px <= Math.max(12, radiusPx + 4);
    }

    function diameterFromPointer(paneKey, clientX, clientY) {
      const scr = screenOfWorld(paneKey, provisional);
      if (!scr) return provisionalDiameter;
      const rect = panes[paneKey].canvas.getBoundingClientRect();
      const px = Math.hypot(clientX - rect.left - scr.x, clientY - rect.top - scr.y);
      const fit = fitFor(panes[paneKey].canvas, planeMap(paneKey).planeSizeUV);
      const radiusMm = px * (fit.mmPerPx || 0);
      return clamp(radiusMm * 2, LESION_DIAMETER_MIN, LESION_DIAMETER_MAX);
    }

    function onPointerMove(e) {
      const wrap = e.target.closest(".ct-pane");
      hoverPane = wrap ? wrap.dataset.pane : hoverPane;
      if (wlDrag) {
        const dx = e.clientX - wlDrag.x;
        const dy = e.clientY - wlDrag.y;
        windowW = clamp(wlDrag.W + dx * 2, 50, 4000);
        windowL = clamp(wlDrag.L - dy * 2, -1200, 1000);
        updateWlReadout();
        drawAll();
        return;
      }
      if (lesionDrag && marking && provisional) {
        const moved = Math.hypot(e.clientX - lesionDrag.x, e.clientY - lesionDrag.y);
        if (!lesionDrag.sizing && moved < 3) return;
        lesionDrag.sizing = true;
        provisionalDiameter = diameterFromPointer(lesionDrag.pane, e.clientX, e.clientY);
        updateLesionStatus();
        drawAll();
      }
    }

    function onPointerUp() {
      wlDrag = null;
      lesionDrag = null;
    }

    function onWheel(e) {
      const wrap = e.target.closest(".ct-pane");
      if (!wrap || !loaded) return;
      e.preventDefault();
      const key = wrap.dataset.pane;
      hoverPane = key;
      focusedPane = key;
      const step = e.shiftKey ? 5 : 1;
      const dir = Math.sign(e.deltaY) || 1;
      const map = planeMap(key);
      const max = ctMeta.shape[map.fixedAxis] - 1;
      state.slices[map.sliceKey] = clamp(
        state.slices[map.sliceKey] + dir * step,
        0,
        max,
      );
      const i = state.slices.sagittal;
      const j = state.slices.coronal;
      const k = state.slices.axial;
      // Wheel never moves the provisional marker.
      setCrosshairWorld(voxelToWorld(i, j, k), "ct-wheel");
    }

    function viewIsActive() {
      if (typeof isViewActive === "function") return !!isViewActive();
      return rootEl.classList.contains("active") ||
        !!(rootEl.closest && rootEl.closest("#ct-stage.active"));
    }

    function onKey(e) {
      if (e.key === "Escape" && marking) {
        const tag = (e.target.tagName || "").toLowerCase();
        if (tag === "input" || tag === "textarea" || tag === "select") return;
        e.preventDefault();
        cancelMark();
        return;
      }
      if (!loaded || !viewIsActive()) return;
      if (e.key !== "ArrowUp" && e.key !== "ArrowDown") return;
      const tag = (e.target.tagName || "").toLowerCase();
      if (tag === "input" || tag === "textarea" || tag === "select") return;
      e.preventDefault();
      const key = focusedPane || "axial";
      const map = planeMap(key);
      const max = ctMeta.shape[map.fixedAxis] - 1;
      const step = e.shiftKey ? 5 : 1;
      const dir = e.key === "ArrowUp" ? -1 : 1;
      state.slices[map.sliceKey] = clamp(
        state.slices[map.sliceKey] + dir * step,
        0,
        max,
      );
      setCrosshairWorld(
        voxelToWorld(state.slices.sagittal, state.slices.coronal, state.slices.axial),
        "ct-key",
      );
    }

    grid.addEventListener("pointerdown", onPointerDown);
    window.addEventListener("pointermove", onPointerMove);
    window.addEventListener("pointerup", onPointerUp);
    grid.addEventListener("wheel", onWheel, { passive: false });
    grid.addEventListener("contextmenu", e => e.preventDefault());
    window.addEventListener("keydown", onKey);
    window.addEventListener("resize", drawAll);

    const unsub = sharedPos.subscribe((pos, source) => {
      if (String(source).startsWith("ct")) return;
      syncSlicesFromShared(pos);
    });

    function affinesClose(a, b, atol) {
      if (!a || !b || a.length !== 4 || b.length !== 4) return false;
      for (let r = 0; r < 4; r++) {
        for (let c = 0; c < 4; c++) {
          if (Math.abs(a[r][c] - b[r][c]) > atol) return false;
        }
      }
      return true;
    }

    // The stage traces in the CT array frame; the browser shows a canonical
    // crop. Map contour points through the CT affine into crop voxels. Without
    // an affine, fall back to the crop offset when the grids share a shape.
    function contourMapPoint(artifact) {
      const affine = artifact && artifact.affine;
      if (Array.isArray(affine) && affine.length === 4) {
        return (x, y, k) => worldToVoxelCont(mat4MulVec(affine, [x, y, k, 1]));
      }
      const shape = artifact && artifact.shape;
      if (!Array.isArray(shape) || shape.length !== 3) return null;
      const crop = ctMeta.shape;
      if (shape[0] === crop[0] && shape[1] === crop[1] && shape[2] === crop[2]) {
        return (x, y, k) => [x, y, k];
      }
      const offset = ctMeta.crop_offset_voxels;
      if (!Array.isArray(offset) || offset.length !== 3) return null;
      return (x, y, k) => [x - offset[0], y - offset[1], k - offset[2]];
    }

    async function loadNoduleContours() {
      try {
        const res = await fetch(
          `/api/cases/${encodeURIComponent(caseId)}/nodule-contours`,
        );
        if (!res.ok) {
          candidates.setContours(null);
          return;
        }
        const artifact = await res.json();
        const mapPoint = contourMapPoint(artifact);
        // An ambiguous grid must not draw the outline in the wrong place.
        candidates.setContours(mapPoint ? artifact : null, mapPoint);
      } catch (err) {
        candidates.setContours(null);
      }
    }

    async function load() {
      buildToolbar();
      try {
        const metaRes = await fetch(`/api/cases/${encodeURIComponent(caseId)}/ct/meta`);
        if (!metaRes.ok) throw new Error(`ct/meta ${metaRes.status}`);
        ctMeta = await metaRes.json();
        if (ctMeta.memory_order !== "x fastest") {
          throw new Error(`unexpected memory_order ${ctMeta.memory_order}`);
        }
        invAffine = invertAffine(ctMeta.affine);
        const ctRes = await fetch(`/api/cases/${encodeURIComponent(caseId)}/ct`);
        if (!ctRes.ok) throw new Error(`ct ${ctRes.status}`);
        const buf = await ctRes.arrayBuffer();
        ctData = new Int16Array(buf);
        const n = ctMeta.shape[0] * ctMeta.shape[1] * ctMeta.shape[2];
        if (ctData.length !== n) throw new Error("CT byte length mismatch");

        const maskMetaRes = await fetch(`/api/cases/${encodeURIComponent(caseId)}/masks/meta`);
        if (maskMetaRes.ok) {
          const maskMeta = await maskMetaRes.json();
          const shapeOk =
            maskMeta.shape &&
            maskMeta.shape[0] === ctMeta.shape[0] &&
            maskMeta.shape[1] === ctMeta.shape[1] &&
            maskMeta.shape[2] === ctMeta.shape[2];
          const affineOk = affinesClose(maskMeta.affine, ctMeta.affine, 1e-3);
          if (!shapeOk || !affineOk) {
            throw new Error("masks/meta does not match ct/meta (shape/affine)");
          }
          const maskRes = await fetch(`/api/cases/${encodeURIComponent(caseId)}/masks`);
          if (maskRes.ok) {
            maskData = new Uint8Array(await maskRes.arrayBuffer());
            if (maskData.length !== n) {
              throw new Error("masks byte length does not match ct/meta shape");
            }
          }
        }

        await loadNoduleContours();

        const presets = ctMeta.window_presets || {};
        if (presets.lung) {
          windowW = presets.lung.W;
          windowL = presets.lung.L;
        }

        let start = sharedPos.get();
        if (lesion && lesion.ras_mm) start = lesion.ras_mm;
        if (!start) {
          start = voxelToWorld(
            Math.floor(ctMeta.shape[0] / 2),
            Math.floor(ctMeta.shape[1] / 2),
            Math.floor(ctMeta.shape[2] / 2),
          );
        }
        loaded = true;
        syncOverlayEnablement();
        setCrosshairWorld(start, "ct-init");
        await loadBridges();
      } catch (err) {
        loadError = String(err.message || err);
        console.error(err);
        drawAll();
      }
    }

    load();

    function screenOfWorld(paneKey, world) {
      if (!loaded || !world || !invAffine) return null;
      const map = planeMap(paneKey);
      const fit = fitFor(panes[paneKey].canvas, map.planeSizeUV);
      const ijk = worldToVoxelCont(world);
      const uv = map.uvFromIjk(ijk[0], ijk[1], ijk[2]);
      return {
        x: fit.ox + uv.u * fit.drawW,
        y: fit.oy + uv.v * fit.drawH,
        ijk,
        inCrop: inCropIjk(ijk, ctMeta.shape),
      };
    }

    function letterScreenPositions(paneKey) {
      if (!loaded) return null;
      const map = planeMap(paneKey);
      const fit = fitFor(panes[paneKey].canvas, map.planeSizeUV);
      const inset = 14;
      return {
        letters: map.letters,
        top: { x: fit.ox + fit.drawW / 2, y: fit.oy + inset },
        bottom: { x: fit.ox + fit.drawW / 2, y: fit.oy + fit.drawH - inset },
        left: { x: fit.ox + inset, y: fit.oy + fit.drawH / 2 },
        right: { x: fit.ox + fit.drawW - inset, y: fit.oy + fit.drawH / 2 },
        fit,
      };
    }

    function syncEstimatedAirway() {
      drawAll();
    }

    const api = {
      getBridgeDraw: () => structuredClone(bridgeDraw),
      drawAll,
      getSlices: () => ({ ...state.slices }),
      getCrosshair: () => (crosshair ? [...crosshair] : null),
      setWorld(world) {
        setCrosshairWorld(world, "debug");
      },
      getCtView: () => ctView,
      setCtViewLocal(v) {
        ctView = v === "head" ? "head" : "feet";
        updateCtViewReadout();
        drawAll();
      },
      setCtViewByName(v) {
        // Key presses are a view toggle, not a saved clinician preference.
        ctView = v === "head" ? "head" : "feet";
        updateCtViewReadout();
        drawAll();
        return ctView;
      },
      getLetters(pane) {
        return planeMap(pane).letters;
      },
      letterScreenPositions,
      screenOfWorld,
      worldToVoxel,
      worldToVoxelCont,
      sampleMask,
      sampleCt,
      isLoaded: () => loaded,
      getMeta: () => ctMeta,
      getLesion: () => (lesion ? { ...lesion, ras_mm: [...lesion.ras_mm] } : null),
      getProvisional: () => (provisional ? [...provisional] : null),
      isMarking: () => marking,
      armMarkLesion() {
        if (readOnly) return;
        marking = true;
        provisional = null;
        provisionalDiameter = LESION_DIAMETER_DEFAULT;
        lesionDrag = null;
        statusMessage("Click a CT pane to place the Lesion");
        syncConfirmEnabled();
        drawAll();
      },
      lesionDraws() {
        return JSON.parse(JSON.stringify(lesionDraw));
      },
      routeTargetDraws() {
        return JSON.parse(JSON.stringify(routeTargetDraw));
      },
      highlightRouteIds() {
        return [...highlightRouteOis];
      },
      highlightStrokes() {
        return routeHighlightStrokes;
      },
      setHighlightRoute(edgeIds) {
        highlightRouteOis = new Set(
          Array.isArray(edgeIds)
            ? edgeIds.map(Number).filter((n) => Number.isFinite(n))
            : [],
        );
        drawAll();
      },
      overlaysEnabled() {
        return {
          airwayDisabled: !overlayAvailable("airway"),
          routeDisabled: !overlayAvailable("route"),
          airway: overlays.airway,
          route: overlays.route,
          lesionConfirmed: lesionConfirmed(),
        };
      },
      overlayAvailable,
      toggleOverlay(key) {
        if (!overlayAvailable(key)) return null;
        overlays[key] = !overlays[key];
        syncOverlayKeys();
        drawAll();
        return overlays[key];
      },
      mountOverlayKeys: buildOverlayControls,
      renderBridges,
      setOverlay(key, on) {
        overlays[key] = !!on;
        syncOverlayKeys();
        drawAll();
      },
      syncEstimatedAirway,
      estimatedCounts() {
        return {
          axial: estimatedDrawn.axial,
          coronal: estimatedDrawn.coronal,
          sagittal: estimatedDrawn.sagittal,
        };
      },
      debugState() {
        return {
          slices: { ...state.slices },
          crosshair: crosshair ? [...crosshair] : null,
          provisional: provisional ? [...provisional] : null,
          lesion: lesion ? { ...lesion, ras_mm: [...(lesion.ras_mm || [])] } : null,
          marking,
          provisionalDiameter,
          overlays: { ...overlays },
        };
      },
      clickPane(paneKey, clientX, clientY) {
        const world = eventToWorld(paneKey, clientX, clientY);
        if (!world) return null;
        if (marking) {
          provisionalDiameter = LESION_DIAMETER_DEFAULT;
          lesionDrag = null;
          setCrosshairWorld(world, "ct-lesion-place");
          return { world, placed: true };
        }
        setCrosshairWorld(world, "ct");
        return { world, placed: !!provisional };
      },
      setMarking(v) {
        if (readOnly) return;
        marking = !!v;
        if (marking) provisional = null;
        syncConfirmEnabled();
        updateLesionStatus();
      },
      setProvisional(ras) {
        provisional = ras ? [...ras] : null;
        updateLesionStatus();
        drawAll();
      },
      confirmLesion,
      clearLesion,
      fitPlaneSample(planeSizeUV) {
        const canvas = panes.axial.canvas;
        const { w, h } = paneRect(canvas);
        return fitPlane(w, h, planeSizeUV);
      },
      /** S-I pixel extents (coronal/sagittal drawH) under the shared mm/px scale. */
      siPixelExtents() {
        const mm = currentSharedMmPerPx();
        const out = { mmPerPx: mm };
        for (const key of ["coronal", "sagittal"]) {
          const canvas = panes[key].canvas;
          const { w, h } = paneRect(canvas);
          const fit = fitPlane(w, h, planeMap(key).planeSizeUV, mm);
          out[key] = fit.drawH;
        }
        return out;
      },
      originMm: () => [...originMm],
      nearestRoute: world => nearestCentrelineWorld(airway, originMm, world),
      setAirwayOverlay(on) {
        overlays.airway = !!on;
        syncOverlayKeys();
        drawAll();
      },
      paneCanvasRect(paneKey) {
        return panes[paneKey].canvas.getBoundingClientRect();
      },
      destroy() {
        unsub();
        window.removeEventListener("pointermove", onPointerMove);
        window.removeEventListener("pointerup", onPointerUp);
        window.removeEventListener("keydown", onKey);
        window.removeEventListener("resize", drawAll);
        if (window.__p3_ct === api) window.__p3_ct = null;
      },
    };

    if (debug) {
      window.__p3_ct = api;
      if (window.__p3_debug) window.__p3_debug.ctApi = api;
    }
    return api;
  }

  return {
    createSharedPosition, mount, nearestCentrelineWorld, fitPlane, sharedMmPerPx,
    lesionSliceGeometry, nearestCtSeenSample,
  };
})();
