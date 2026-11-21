// Changes: draw traced axial contours; keep the dashed circle for box-only candidates.
// Detector candidates: suggestions until a clinician confirms one.
// Owns its toolbar buttons, the confirm/clear requests, and the CT glyphs.
window.CtCandidates = (function () {
  const COLOR = "#b8860b";
  const LABEL = "#e0b53a";

  function outlinedText(ctx, text, x, y) {
    ctx.save();
    ctx.textAlign = "left";
    ctx.textBaseline = "middle";
    ctx.lineWidth = 3;
    ctx.strokeStyle = "rgba(0,0,0,0.85)";
    ctx.fillStyle = LABEL;
    ctx.strokeText(text, x, y);
    ctx.fillText(text, x, y);
    ctx.restore();
  }

  function create(opts) {
    const {
      caseId,
      readOnly,
      ensureClinician,
      statusMessage,
      diameterOf,
      geometry,
      onConfirmed,
    } = opts;
    let items = [];
    let box = null;
    let contours = null;
    let contourMapPoint = null;

    // mapPoint turns one contour point on an axial slice into crop voxel
    // coordinates, so the viewer does not assume the artifact grid is its crop.
    function setContours(artifact, mapPoint) {
      contourMapPoint = typeof mapPoint === "function" ? mapPoint : null;
      contours = new Map();
      if (!contourMapPoint) return;
      const nodules = artifact && Array.isArray(artifact.nodules)
        ? artifact.nodules
        : [];
      for (const nodule of nodules) {
        if (!nodule || nodule.id == null) continue;
        const slices = new Map();
        for (const entry of Array.isArray(nodule.slices) ? nodule.slices : []) {
          if (!entry || !Array.isArray(entry.polygons)) continue;
          const sliceIndex = Number(entry.slice_index);
          const plane = contourMapPoint(0, 0, sliceIndex);
          if (!plane) continue;
          slices.set(Math.round(plane[2]), {
            sliceIndex,
            polygons: entry.polygons,
          });
        }
        contours.set(Number(nodule.id), slices);
      }
    }

    function drawContour(ctx, entry, map, fit, label) {
      ctx.save();
      // The polygon may run past the CT image; clip it to the pane viewport.
      ctx.beginPath();
      ctx.rect(fit.ox, fit.oy, fit.drawW, fit.drawH);
      ctx.clip();
      ctx.lineJoin = "round";
      ctx.lineCap = "round";
      let topY = Infinity;
      let leftX = Infinity;
      for (const polygon of entry.polygons) {
        if (!Array.isArray(polygon) || polygon.length < 3) continue;
        ctx.beginPath();
        polygon.forEach((point, index) => {
          if (!Array.isArray(point) || point.length < 2) return;
          const mapped = contourMapPoint(
            Number(point[0]), Number(point[1]), entry.sliceIndex,
          );
          if (!mapped) return;
          // Contour points sit on voxel centres; uvFromIjk takes the
          // half-voxel corner coordinate.
          const uv = map.uvFromIjk(
            mapped[0] + 0.5, mapped[1] + 0.5, mapped[2] + 0.5,
          );
          const x = fit.ox + uv.u * fit.drawW;
          const y = fit.oy + uv.v * fit.drawH;
          topY = Math.min(topY, y);
          leftX = Math.min(leftX, x);
          if (index === 0) ctx.moveTo(x, y);
          else ctx.lineTo(x, y);
        });
        ctx.closePath();
        ctx.lineWidth = 3.5;
        ctx.strokeStyle = "rgba(0,0,0,0.65)";
        ctx.stroke();
        ctx.lineWidth = 2;
        ctx.strokeStyle = COLOR;
        ctx.stroke();
      }
      ctx.restore();
      if (label && Number.isFinite(topY) && Number.isFinite(leftX)) {
        ctx.save();
        ctx.font = "600 10px monospace";
        outlinedText(ctx, label, leftX, topY - 10);
        ctx.restore();
      }
    }

    async function run(path, method, after) {
      let ok = true;
      try {
        ok = await ensureClinician();
      } catch (err) {
        statusMessage(String(err.message || err));
        return;
      }
      if (!ok) return;
      let res;
      try {
        res = await fetch(`/api/cases/${encodeURIComponent(caseId)}${path}`, {
          method,
        });
      } catch (err) {
        statusMessage(String(err.message || err));
        return;
      }
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        statusMessage(body.detail || `${method} candidates failed (${res.status})`);
        return;
      }
      after(await res.json());
    }

    function render() {
      if (!box) return;
      box.replaceChildren();
      if (!items.length) {
        box.hidden = true;
        return;
      }
      box.hidden = false;
      items.forEach((item, index) => {
        const btn = document.createElement("button");
        btn.type = "button";
        btn.className = "secondary";
        const score = item && item.score != null
          ? ` · ${Number(item.score).toFixed(2)}`
          : "";
        const dia = `Ø ${diameterOf(item || {}).toFixed(1)} mm`;
        btn.textContent = `Candidate ${index + 1}${score} · ${dia}`;
        btn.onclick = () => confirm(index);
        box.appendChild(btn);
      });
      const clearBtn = document.createElement("button");
      clearBtn.type = "button";
      clearBtn.className = "secondary";
      clearBtn.textContent = "Clear candidates";
      clearBtn.onclick = clearAll;
      box.appendChild(clearBtn);
    }

    function setItems(next) {
      items = Array.isArray(next) ? next : [];
      render();
    }

    function mount(parent) {
      if (readOnly) return;
      box = document.createElement("div");
      box.className = "ct-lesion-box";
      box.id = "ct-candidates";
      box.hidden = true;
      parent.appendChild(box);
      render();
    }

    function confirm(index) {
      run(`/candidates/${index}/confirm`, "POST", (body) => {
        setItems([]);
        onConfirmed(body.lesion);
      }).catch((err) => statusMessage(String(err.message || err)));
    }

    function clearAll() {
      run("/candidates", "DELETE", () => setItems([]))
        .catch((err) => statusMessage(String(err.message || err)));
    }

    function draw(ctx, view) {
      if (!view.visible || !items.length) return;
      const frame = geometry.sliceFrame(view.map, view.fixed);
      items.forEach((item, index) => {
        if (!item || !item.ras_mm) return;
        const score = item.score == null ? "" : ` ${Number(item.score).toFixed(2)}`;
        const label = `#${index + 1}${score}`;
        const contourId = item.id != null ? Number(item.id) : index + 1;
        const noduleSlices = view.key === "axial" && contours
          ? contours.get(contourId)
          : null;
        if (noduleSlices) {
          // A mask-backed candidate uses its traced outline on the axial pane;
          // other panes keep the circle because the artifact is axial only.
          const entry = noduleSlices.get(view.fixed);
          if (entry) drawContour(ctx, entry, view.map, view.fit, label);
          return;
        }
        const diameter = view.diameterOf(item);
        const geom = geometry.lesionSliceGeometry(
          item.ras_mm, diameter, frame.planePoint, frame.increment,
        );
        if (!geom.through) return;
        const ijk = geometry.worldToVoxelCont(item.ras_mm);
        const uv = view.map.uvFromIjk(ijk[0], ijk[1], ijk[2]);
        const sx = view.fit.ox + uv.u * view.fit.drawW;
        const sy = view.fit.oy + uv.v * view.fit.drawH;
        const radius = Math.max(
          geom.inPlaneRadiusMm / (view.fit.mmPerPx || 1), 4,
        );
        ctx.save();
        ctx.setLineDash([4, 3]);
        ctx.beginPath();
        ctx.arc(sx, sy, radius, 0, Math.PI * 2);
        ctx.lineWidth = 3.5;
        ctx.strokeStyle = "rgba(0,0,0,0.65)";
        ctx.stroke();
        ctx.lineWidth = 2;
        ctx.strokeStyle = COLOR;
        ctx.stroke();
        ctx.setLineDash([]);
        ctx.beginPath();
        ctx.arc(sx, sy, 1.6, 0, Math.PI * 2);
        ctx.fillStyle = COLOR;
        ctx.fill();
        ctx.font = "600 10px monospace";
        outlinedText(ctx, label, sx + radius + 6, sy - 4);
        ctx.restore();
      });
    }

    return { mount, setItems, setContours, draw };
  }

  return { create };
})();
