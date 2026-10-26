/* Phase 4b fixes: hatched label 3; setAirwayOverlay; CT airway-voxel → remove branch.
 * RouteStart move flow from 4a retained.
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

  function strokeRouteSeg(ctx, pts, prov) {
    if (!pts || pts.length < 2) return;
    const color = PROV_STROKE[prov] || PROV_STROKE.ct;
    ctx.lineJoin = "round";
    ctx.lineCap = "round";
    ctx.beginPath();
    ctx.moveTo(pts[0][0], pts[0][1]);
    for (let i = 1; i < pts.length; i++) ctx.lineTo(pts[i][0], pts[i][1]);
    ctx.strokeStyle = "rgba(0,0,0,0.85)";
    ctx.lineWidth = 5.5;
    ctx.stroke();
    ctx.beginPath();
    ctx.moveTo(pts[0][0], pts[0][1]);
    for (let i = 1; i < pts.length; i++) ctx.lineTo(pts[i][0], pts[i][1]);
    ctx.strokeStyle = color;
    ctx.lineWidth = 3.25;
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

  /** Physical plane aspect: n_u·sp_u : n_v·sp_v (not per-voxel spacing). */
  function fitPlane(canvasW, canvasH, planeSizeUV) {
    const physW = planeSizeUV[0];
    const physH = planeSizeUV[1];
    const aspect = physW / (physH || 1);
    let drawW = canvasW, drawH = canvasW / aspect;
    if (drawH > canvasH) {
      drawH = canvasH;
      drawW = canvasH * aspect;
    }
    const ox = (canvasW - drawW) / 2;
    const oy = (canvasH - drawH) / 2;
    return { ox, oy, drawW, drawH, w: canvasW, h: canvasH };
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
      debug,
      isViewActive,
    } = opts;

    const originMm = airway.origin_mm || [0, 0, 0];
    let ctMeta = null;
    let ctData = null;
    let maskData = null;
    let invAffine = null;
    let loaded = false;
    let loadError = null;
    let windowW = 1500;
    let windowL = -600;
    let ctView = getCtView() === "head" ? "head" : "feet";
    let overlays = {
      airway: false,
      route: false,
      lesion: true,
      target: true,
    };
    let marking = false;
    let provisional = null;
    let lesion = airway.lesion ? { ...airway.lesion, ras_mm: [...airway.lesion.ras_mm] } : null;
    let focusedPane = "axial";
    let hoverPane = null;
    let wlDrag = null;
    let crosshair = sharedPos.get();
    const offscreen = document.createElement("canvas");
    const offCtx = offscreen.getContext("2d", { willReadFrequently: true });

    const state = {
      slices: { axial: 0, coronal: 0, sagittal: 0 },
    };

    rootEl.innerHTML = "";
    rootEl.classList.add("ct-panes-root");

    const toolbar = document.createElement("div");
    toolbar.className = "ct-toolbar";
    rootEl.appendChild(toolbar);

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

    function syncOverlayEnablement() {
      const ok = lesionConfirmed();
      for (const el of toolbar.querySelectorAll("[data-overlay]")) {
        el.disabled = !ok && el.dataset.overlay !== "lesion";
      }
      const hint = toolbar.querySelector(".ct-overlay-hint");
      if (hint) hint.hidden = ok;
    }

    function syncConfirmEnabled() {
      const btn = toolbar.querySelector("#ct-confirm-lesion");
      if (btn) btn.disabled = !provisional;
      const cancel = toolbar.querySelector("#ct-cancel-mark");
      if (cancel) cancel.disabled = !marking;
    }

    function buildToolbar() {
      toolbar.innerHTML = "";
      const presets = document.createElement("div");
      presets.className = "ct-presets";
      for (const [name, W, L] of [
        ["Lung", 1500, -600],
        ["Mediastinal", 400, 40],
      ]) {
        const b = document.createElement("button");
        b.type = "button";
        b.textContent = name;
        b.onclick = () => {
          windowW = W;
          windowL = L;
          drawAll();
        };
        presets.appendChild(b);
      }
      const wl = document.createElement("span");
      wl.className = "ct-wl";
      wl.id = "ct-wl-readout";
      presets.appendChild(wl);

      const view = document.createElement("label");
      view.className = "ct-view-toggle";
      const sel = document.createElement("select");
      sel.innerHTML =
        '<option value="feet">View from feet (radiological)</option>' +
        '<option value="head">View from head (bronchoscopist)</option>';
      sel.value = ctView;
      sel.onchange = async () => {
        ctView = sel.value === "head" ? "head" : "feet";
        try {
          await setCtView(ctView);
        } catch (err) {
          statusMessage(String(err.message || err));
        }
        drawAll();
      };
      view.appendChild(sel);

      const ov = document.createElement("div");
      ov.className = "ct-overlays";
      for (const [key, label] of [
        ["airway", "Airway mask"],
        ["route", "Route"],
        ["lesion", "Lesion"],
        ["target", "RouteTarget"],
      ]) {
        const lab = document.createElement("label");
        const cb = document.createElement("input");
        cb.type = "checkbox";
        cb.dataset.overlay = key;
        cb.checked = overlays[key];
        cb.onchange = () => {
          overlays[key] = cb.checked;
          drawAll();
        };
        lab.appendChild(cb);
        lab.appendChild(document.createTextNode(" " + label));
        ov.appendChild(lab);
      }
      const hint = document.createElement("span");
      hint.className = "ct-overlay-hint";
      hint.textContent = "Mark and confirm the Lesion first";
      ov.appendChild(hint);

      const lesionBox = document.createElement("div");
      lesionBox.className = "ct-lesion-box";
      const markBtn = document.createElement("button");
      markBtn.type = "button";
      markBtn.id = "ct-mark-lesion";
      markBtn.textContent = "Mark Lesion";
      markBtn.onclick = () => {
        marking = true;
        provisional = null;
        statusMessage("Click in the axial (red) pane");
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
      cancelBtn.onclick = () => {
        marking = false;
        provisional = null;
        statusMessage("");
        syncConfirmEnabled();
        updateLesionStatus();
        drawAll();
      };
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

      const routeStartBox = document.createElement("div");
      routeStartBox.className = "ct-route-start-box";
      routeStartBox.id = "ct-route-start-box";
      const rsLabel = document.createElement("span");
      rsLabel.textContent = "Start";
      const rsSelect = document.createElement("select");
      rsSelect.id = "ct-route-start-select";
      rsSelect.title = "RouteStart: qualifying terminal (≥ 80% of root radius)";
      rsSelect.style.minWidth = "7.5rem";
      const rsConfirm = document.createElement("button");
      rsConfirm.type = "button";
      rsConfirm.id = "ct-confirm-route-start";
      rsConfirm.textContent = "Confirm Start";
      rsConfirm.onclick = () => {
        confirmRouteStart().catch(err => statusMessage(String(err.message || err)));
      };
      const rsMove = document.createElement("button");
      rsMove.type = "button";
      rsMove.id = "ct-move-route-start";
      rsMove.textContent = "Move RouteStart";
      rsMove.hidden = true;
      rsMove.onclick = () => {
        routeStartMoving = true;
        syncRouteStartConfirmEnabled();
        statusMessage("Select another qualifying terminal, then Confirm Start");
      };
      const rsStatus = document.createElement("span");
      rsStatus.className = "ct-lesion-status";
      rsStatus.id = "ct-route-start-status";
      routeStartBox.appendChild(rsLabel);
      routeStartBox.appendChild(rsSelect);
      routeStartBox.appendChild(rsConfirm);
      routeStartBox.appendChild(rsMove);
      routeStartBox.appendChild(rsStatus);

      // Populate RouteStart from cache (filled before first layout) so async
      // fetches never grow the toolbar mid-interaction.
      fillRouteStartSelect(rsSelect, rsStatus);
      toolbar.appendChild(presets);
      toolbar.appendChild(view);
      toolbar.appendChild(ov);
      toolbar.appendChild(lesionBox);
      toolbar.appendChild(routeStartBox);
      syncOverlayEnablement();
      syncConfirmEnabled();
      updateLesionStatus();
      updateWlReadout();
    }

    let routeStartCandidates = [];
    let routeStartMoving = false;

    function syncRouteStartConfirmEnabled() {
      const sel = toolbar.querySelector("#ct-route-start-select");
      const btn = toolbar.querySelector("#ct-confirm-route-start");
      const move = toolbar.querySelector("#ct-move-route-start");
      const rs = airway.route_start;
      if (!btn) return;
      const confirmed = !!(rs && rs.confirmed);
      const selected = sel && sel.value !== "" ? Number(sel.value) : (rs && rs.node);
      const changed = confirmed && selected != null && Number(selected) !== Number(rs.node);
      if (move) {
        move.hidden = !confirmed;
        move.disabled = !confirmed;
      }
      if (confirmed && !routeStartMoving && !changed) {
        btn.disabled = true;
        btn.textContent = "Confirm Start";
      } else {
        btn.disabled = false;
        btn.textContent = (confirmed || routeStartMoving) ? "Confirm new Start" : "Confirm Start";
      }
      if (sel) sel.disabled = confirmed && !routeStartMoving && !changed;
      if (confirmed && routeStartMoving) {
        if (sel) sel.disabled = false;
      }
    }

    function fillRouteStartSelect(sel, status) {
      if (!sel) {
        sel = toolbar.querySelector("#ct-route-start-select");
        status = toolbar.querySelector("#ct-route-start-status");
      }
      if (!sel) return;
      const current = (airway.route_start && airway.route_start.node != null)
        ? airway.route_start.node
        : (routeStartCandidates.find(c => c.is_default) || routeStartCandidates[0] || {}).node;
      sel.innerHTML = "";
      if (!routeStartCandidates.length) {
        const opt = document.createElement("option");
        opt.value = "";
        opt.textContent = "—";
        sel.appendChild(opt);
      } else {
        for (const c of routeStartCandidates) {
          const opt = document.createElement("option");
          opt.value = String(c.node);
          opt.textContent = `#${c.node} r${c.radius_mm.toFixed(1)}`
            + (c.is_default ? "*" : "");
          sel.appendChild(opt);
        }
        if (current != null) sel.value = String(current);
      }
      sel.onchange = () => {
        const node = Number(sel.value);
        const cand = routeStartCandidates.find(c => c.node === node);
        if (cand && cand.ras_mm) {
          airway.route_start = {
            ...(airway.route_start || {}),
            node: cand.node,
            ras_mm: cand.ras_mm,
            confirmed: !!(airway.route_start && airway.route_start.confirmed),
          };
          sharedPos.set(cand.ras_mm, "ct-route-start");
          syncRouteStartConfirmEnabled();
          drawAll();
        }
      };
      if (status) {
        const rs = airway.route_start;
        status.textContent = rs && rs.confirmed ? `ok #${rs.node}` : (current != null ? `to confirm #${current}` : "");
      }
      syncRouteStartConfirmEnabled();
    }

    async function loadRouteStartCandidates() {
      try {
        const res = await fetch(`/api/cases/${encodeURIComponent(caseId)}/route-start/candidates`);
        if (!res.ok) return;
        const body = await res.json();
        routeStartCandidates = body.candidates || [];
      } catch (_) {
        routeStartCandidates = [];
      }
    }

    async function confirmRouteStart() {
      if (!(await ensureClinician())) return;
      const sel = toolbar.querySelector("#ct-route-start-select");
      const node = sel ? Number(sel.value) : (airway.route_start && airway.route_start.node);
      if (node == null || Number.isNaN(node)) {
        statusMessage("No RouteStart candidate");
        return;
      }
      const res = await fetch(`/api/cases/${encodeURIComponent(caseId)}/route-start`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ node }),
      });
      const body = await res.json().catch(() => ({}));
      if (!res.ok) {
        throw new Error(body.detail || `RouteStart failed (${res.status})`);
      }
      airway.route_start = {
        node: body.route_start.node,
        ras_mm: body.route_start.ras_mm,
        confirmed: true,
        default_node: airway.route_start && airway.route_start.default_node,
      };
      routeStartMoving = false;
      statusMessage(`RouteStart confirmed (node ${node})`);
      updateLesionStatus();
      fillRouteStartSelect();
      drawAll();
    }

    function updateWlReadout() {
      const el = toolbar.querySelector("#ct-wl-readout");
      if (el) el.textContent = `W ${Math.round(windowW)} / L ${Math.round(windowL)}`;
    }

    function updateLesionStatus() {
      const el = toolbar.querySelector("#ct-lesion-status");
      if (!el) return;
      if (lesion && lesion.ras_mm) {
        const who = lesion.confirmed_by || "";
        const when = lesion.at || "";
        el.textContent = `Lesion confirmed${who ? " · " + who : ""}${when ? " · " + when : ""}`;
      } else if (provisional) {
        el.textContent = "Provisional marker — Confirm Lesion to save";
      } else if (marking) {
        el.textContent = "Click in the axial (red) pane";
      } else {
        el.textContent = "";
      }
      syncOverlayEnablement();
      syncConfirmEnabled();
    }

    async function confirmLesion() {
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
          body: JSON.stringify({ ras_mm: provisional }),
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
      airway.lesion = lesion;
      if (onLesionChange) onLesionChange(lesion);
      updateLesionStatus();
      drawAll();
    }

    async function clearLesion() {
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
      airway.lesion = null;
      overlays.airway = false;
      overlays.route = false;
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
      // Wheel/keys never move the provisional marker; only axial-place and pane clicks.
      if (marking) {
        if (source === "ct-axial-place") {
          provisional = [...crosshair];
          updateLesionStatus();
        } else if (provisional && source === "ct") {
          provisional = [...crosshair];
          updateLesionStatus();
        }
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

    function fitFor(canvas, planeSizeUV) {
      const { w, h } = paneRect(canvas);
      return fitPlane(w, h, planeSizeUV);
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
          if (overlays.airway && lesionConfirmed()) {
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

      if (overlays.route && lesionConfirmed() && airway.route) {
        const routeEdges = airway.route || [];
        for (const ei of routeEdges) {
          const e = airway.edges[ei];
          if (!e) continue;
          const pts = [];
          for (const c of e.c) {
            const world = [c[0] + originMm[0], c[1] + originMm[1], c[2] + originMm[2]];
            const ijk = worldToVoxelCont(world);
            if (!inCropIjk(ijk, ctMeta.shape) || Math.abs(ijk[map.fixedAxis] - fixed) > 1.0) {
              if (pts.length > 1) strokeRouteSeg(ctx, pts, e.prov || "ct");
              pts.length = 0;
              continue;
            }
            const uv = map.uvFromIjk(ijk[0], ijk[1], ijk[2]);
            pts.push([fit.ox + uv.u * fit.drawW, fit.oy + uv.v * fit.drawH]);
          }
          if (pts.length > 1) strokeRouteSeg(ctx, pts, e.prov || "ct");
        }
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

      if (overlays.lesion) {
        const L = provisional || (lesion && lesion.ras_mm);
        if (L) drawMarker(L, "#e85d4c", "Lesion");
      }
      if (airway.route_start && airway.route_start.ras_mm) {
        drawMarker(airway.route_start.ras_mm, "#B4522F", "RouteStart");
      }
      if (overlays.target && lesionConfirmed()) {
        if (airway.endpoint) {
          const ep = [
            airway.endpoint[0] + originMm[0],
            airway.endpoint[1] + originMm[1],
            airway.endpoint[2] + originMm[2],
          ];
          drawMarker(ep, "#2E6E88", "Target");
          const L = lesion && lesion.ras_mm;
          if (L && overlays.lesion) {
            const ijkL = worldToVoxelCont(L);
            const ijkE = worldToVoxelCont(ep);
            if (
              inCropIjk(ijkL, ctMeta.shape) &&
              inCropIjk(ijkE, ctMeta.shape) &&
              Math.abs(ijkL[map.fixedAxis] - fixed) <= 1 &&
              Math.abs(ijkE[map.fixedAxis] - fixed) <= 1
            ) {
              const uvL = map.uvFromIjk(ijkL[0], ijkL[1], ijkL[2]);
              const uvE = map.uvFromIjk(ijkE[0], ijkE[1], ijkE[2]);
              ctx.strokeStyle = "#888";
              ctx.setLineDash([4, 4]);
              ctx.beginPath();
              ctx.moveTo(fit.ox + uvL.u * fit.drawW, fit.oy + uvL.v * fit.drawH);
              ctx.lineTo(fit.ox + uvE.u * fit.drawW, fit.oy + uvE.v * fit.drawH);
              ctx.stroke();
              ctx.setLineDash([]);
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
      if (marking && !provisional) {
        if (key !== "axial") {
          statusMessage("Click in the axial (red) pane");
          setCrosshairWorld(world, "ct");
          return;
        }
        setCrosshairWorld(world, "ct-axial-place");
        return;
      }
      if (marking && provisional) {
        setCrosshairWorld(world, "ct");
        return;
      }
      setCrosshairWorld(world, "ct");
      // Clicking an airway voxel selects the nearest graph edge for removal.
      if (maskData && lesionConfirmed()) {
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

    function onPointerMove(e) {
      const wrap = e.target.closest(".ct-pane");
      hoverPane = wrap ? wrap.dataset.pane : hoverPane;
      if (wlDrag) {
        const dx = e.clientX - wlDrag.x;
        const dy = e.clientY - wlDrag.y;
        windowW = clamp(wlDrag.W + dx * 2, 50, 4000);
        windowL = clamp(wlDrag.L - dy * 2, -1200, 1000);
        drawAll();
      }
    }

    function onPointerUp() {
      wlDrag = null;
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

    async function load() {
      // Fetch RouteStart candidates before first toolbar layout so later
      // rebuilds (e.g. clearLesion → buildToolbar) keep a stable size.
      await loadRouteStartCandidates();
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
        setCrosshairWorld(start, "ct-init");
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

    const api = {
      drawAll,
      getSlices: () => ({ ...state.slices }),
      getCrosshair: () => (crosshair ? [...crosshair] : null),
      setWorld(world) {
        setCrosshairWorld(world, "debug");
      },
      getCtView: () => ctView,
      setCtViewLocal(v) {
        ctView = v === "head" ? "head" : "feet";
        const sel = toolbar.querySelector("select");
        if (sel) sel.value = ctView;
        drawAll();
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
      overlaysEnabled() {
        const airwayEl = toolbar.querySelector('[data-overlay="airway"]');
        const routeEl = toolbar.querySelector('[data-overlay="route"]');
        return {
          airwayDisabled: !!(airwayEl && airwayEl.disabled),
          routeDisabled: !!(routeEl && routeEl.disabled),
          airway: overlays.airway,
          route: overlays.route,
          lesionConfirmed: lesionConfirmed(),
        };
      },
      setOverlay(key, on) {
        overlays[key] = !!on;
        const el = toolbar.querySelector(`[data-overlay="${key}"]`);
        if (el) el.checked = overlays[key];
        drawAll();
      },
      debugState() {
        return {
          slices: { ...state.slices },
          crosshair: crosshair ? [...crosshair] : null,
          provisional: provisional ? [...provisional] : null,
          lesion: lesion ? { ...lesion, ras_mm: [...(lesion.ras_mm || [])] } : null,
          marking,
          overlays: { ...overlays },
        };
      },
      clickPane(paneKey, clientX, clientY) {
        const world = eventToWorld(paneKey, clientX, clientY);
        if (!world) return null;
        if (marking && !provisional) {
          if (paneKey !== "axial") {
            statusMessage("Click in the axial (red) pane");
            return { world, placed: false };
          }
          setCrosshairWorld(world, "ct-axial-place");
          return { world, placed: true };
        }
        setCrosshairWorld(world, "ct");
        return { world, placed: !!provisional };
      },
      setMarking(v) {
        marking = !!v;
        if (marking) provisional = null;
        syncConfirmEnabled();
        updateLesionStatus();
      },
      armMarkLesion() {
        marking = true;
        provisional = null;
        statusMessage("Click in the axial (red) pane");
        syncConfirmEnabled();
        drawAll();
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
      originMm: () => [...originMm],
      nearestRoute: world => nearestCentrelineWorld(airway, originMm, world),
      setAirwayOverlay(on) {
        overlays.airway = !!on;
        const cb = rootEl.querySelector('input[data-overlay="airway"]');
        if (cb) cb.checked = overlays.airway;
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

  return { createSharedPosition, mount, nearestCentrelineWorld, fitPlane };
})();
