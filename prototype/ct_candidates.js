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
        const score = item.score == null ? "" : ` ${Number(item.score).toFixed(2)}`;
        outlinedText(ctx, `#${index + 1}${score}`, sx + radius + 6, sy - 4);
        ctx.restore();
      });
    }

    return { mount, setItems, draw };
  }

  return { create };
})();
