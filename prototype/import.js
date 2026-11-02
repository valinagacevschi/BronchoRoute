// Changes: import initials use the shared Python-owned Settings API.
// Changes: Archive input, Study picker, clinician prompt and processing screens.
"use strict";

(() => {
  let session = null;
  let activeCase = null;
  let busy = false;
  const screen = document.createElement("section");
  screen.id = "import-screen";
  screen.hidden = true;
  screen.innerHTML = `
    <div class="import-heading"><span>BronchoRoute</span><button id="archive-back" hidden>Back to Case</button></div>
    <main class="import-panel">
      <p class="import-eyebrow">LOCAL CT PROCESSING</p>
      <h1>Open an Archive</h1>
      <p>Choose a reconstruction, build its airway model, then open the Case.</p>
      <div id="archive-drop" tabindex="0" aria-label="Drop an Archive">
        <strong>Drop an Archive here</strong><p>Zip, folder or DICOMDIR · processed on this Mac</p>
        <button id="archive-choose">Choose…</button>
        <input id="archive-file" type="file" accept=".zip" hidden>
      </div>
      <p id="import-message" role="status" aria-live="polite"></p>
      <div id="series-picker" hidden><h2>Choose a Series</h2><div id="study-groups"></div>
        <form id="import-confirm"><div id="clinician-prompt" hidden>
          <label>Your initials <input id="clinician-initials" maxlength="12" autocomplete="off"></label>
          <p>Saved locally for Case records.</p></div>
          <button id="series-confirm" type="submit">Process Series</button>
        </form>
      </div>
      <div id="processing" hidden><h2>Building the Case</h2>
        <ol id="processing-steps"><li>Reading</li><li>Segmentation</li><li>Airway model</li><li>Graph</li></ol>
        <p><span id="processing-elapsed">0 s</span> elapsed · Estimated 6–7 min</p>
        <button id="processing-cancel">Cancel</button>
      </div>
    </main>`;
  document.body.appendChild(screen);
  const get = id => document.getElementById(id);
  const open = document.createElement("button");
  open.id = "archive-open";
  open.textContent = "Open Archive…";
  open.hidden = true;
  document.body.appendChild(open);

  async function api(url, options) {
    const response = await fetch(url, options);
    const data = await response.json();
    if (!response.ok) {
      const error = new Error(typeof data.detail === "string" ? data.detail : "Could not complete this action");
      error.status = response.status;
      throw error;
    }
    return data;
  }

  function message(text) {
    get("import-message").textContent = text;
  }

  async function cancel() {
    if (!activeCase) return;
    get("processing-cancel").disabled = true;
    await api(`/api/cases/${activeCase}/jobs/latest/cancel`, { method: "POST" });
    activeCase = null;
    get("processing").hidden = true;
    get("archive-drop").hidden = false;
    get("processing-cancel").disabled = false;
    message("Processing cancelled. Open an Archive to try again.");
  }

  async function prepare() {
    if (busy) return false;
    if (activeCase) {
      if (!window.confirm("Cancel current processing?")) return false;
      await cancel();
    }
    if (session) {
      await api(`/api/imports/${session.import_id}`, { method: "DELETE" });
      session = null;
    }
    screen.hidden = false;
    get("series-picker").hidden = true;
    get("archive-drop").hidden = false;
    get("archive-back").hidden = !new URLSearchParams(location.search).get("case");
    message("");
    return true;
  }

  function cell(row, value) {
    const td = document.createElement("td");
    td.textContent = value;
    row.appendChild(td);
    return td;
  }

  async function scan(path, file) {
    if (!await prepare()) return;
    busy = true;
    get("archive-choose").disabled = true;
    message("Reading Archive…");
    try {
      session = await api(path ? "/api/imports/scan" : "/api/imports/upload", {
        method: "POST",
        headers: { "Content-Type": path ? "application/json" : "application/zip" },
        body: path ? JSON.stringify({ path }) : file,
      });
      const groups = get("study-groups");
      groups.replaceChildren();
      for (const study of session.studies) {
        const heading = document.createElement("h3");
        heading.textContent = [study.date, study.description].filter(Boolean).join(" · ") || "Study";
        groups.appendChild(heading);
        const table = document.createElement("table");
        const head = document.createElement("tr");
        for (const title of ["Series", "Thickness / spacing", "Kernel", "Contrast", "Coverage", "Slices", "Suitability"]) {
          const th = document.createElement("th");
          th.textContent = title;
          head.appendChild(th);
        }
        const thead = document.createElement("thead");
        thead.appendChild(head);
        table.appendChild(thead);
        const tbody = document.createElement("tbody");
        for (const series of study.series) {
          const tr = document.createElement("tr");
          const label = document.createElement("label");
          const radio = document.createElement("input");
          radio.type = "radio";
          radio.name = "series-choice";
          radio.value = series.series_key;
          radio.checked = series.status === "recommended";
          radio.disabled = series.status === "not_usable";
          label.append(radio, document.createTextNode(series.description || "CT Series"));
          cell(tr, "").appendChild(label);
          cell(tr, `${series.slice_thickness_mm} / ${series.spacing_mm} mm`);
          cell(tr, series.kernel_class);
          cell(tr, series.contrast ? "Contrast" : "Non-contrast");
          cell(tr, `${Math.round(series.z_span_mm)} mm`);
          cell(tr, series.count);
          const suitability = cell(tr, "");
          const badge = document.createElement("strong");
          badge.className = `series-badge ${series.status}`;
          badge.textContent = { recommended: "Recommended", usable: "Usable", not_usable: "Not usable" }[series.status];
          suitability.append(badge, document.createTextNode(` ${series.reason || ""}`));
          tbody.appendChild(tr);
        }
        table.appendChild(tbody);
        groups.appendChild(table);
      }
      const clinician = await api("/api/settings");
      get("clinician-prompt").hidden = Boolean(clinician.initials);
      get("series-picker").hidden = false;
      get("archive-drop").hidden = true;
      message("");
    } catch (error) {
      message(error.message);
    } finally {
      busy = false;
      get("archive-choose").disabled = false;
    }
  }

  async function poll(caseId) {
    try {
      while (activeCase === caseId) {
        const job = await api(`/api/cases/${caseId}/jobs/latest`);
        if (activeCase !== caseId) return;
        get("processing-elapsed").textContent = `${Math.floor(job.elapsed_s)} s`;
        const steps = ["reading", "segmentation", "airway model", "graph"];
        const index = steps.indexOf(job.step);
        Array.from(get("processing-steps").children).forEach((li, i) => {
          li.dataset.state = i < index ? "done" : i === index ? "active" : "pending";
        });
        if (job.status === "succeeded") {
          location.assign(`/?case=${encodeURIComponent(caseId)}`);
          return;
        }
        if (job.status === "failed") {
          activeCase = null;
          get("processing").hidden = true;
          get("archive-drop").hidden = false;
          message(`${job.step}: ${job.error || "Processing failed"}. Try another Series.`);
          return;
        }
        await new Promise(resolve => setTimeout(resolve, 300));
      }
    } catch (error) {
      message(error.message);
    }
  }

  get("import-confirm").addEventListener("submit", async event => {
    event.preventDefault();
    if (busy) return;
    const choice = screen.querySelector('input[name="series-choice"]:checked');
    if (!choice) {
      message("Choose a usable Series.");
      return;
    }
    busy = true;
    get("series-confirm").disabled = true;
    try {
      if (!get("clinician-prompt").hidden) {
        await api("/api/settings", {
          method: "PUT", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ initials: get("clinician-initials").value.trim() }),
        });
      }
      get("series-picker").hidden = true;
      get("processing").hidden = false;
      get("processing-cancel").disabled = true;
      message("Reading Series…");
      const result = await api("/api/imports", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ import_id: session.import_id, series_key: choice.value }),
      });
      session = null;
      activeCase = result.case_id;
      get("processing-cancel").disabled = false;
      message("");
      poll(activeCase);
    } catch (error) {
      get("processing").hidden = true;
      get("series-picker").hidden = false;
      if (error.status === 412) get("clinician-prompt").hidden = false;
      message(error.message);
    } finally {
      busy = false;
      get("series-confirm").disabled = false;
    }
  });
  async function choose() {
    if (!await prepare()) return;
    if (window.bronchoroute) {
      await window.bronchoroute.openArchive();
    } else {
      get("archive-file").click();
    }
  }
  open.addEventListener("click", () => prepare().catch(error => message(error.message)));
  get("archive-choose").addEventListener("click", () => choose().catch(error => message(error.message)));
  get("archive-back").addEventListener("click", () => { screen.hidden = true; });
  get("processing-cancel").addEventListener("click", () => cancel().catch(error => message(error.message)));
  get("archive-file").addEventListener("change", event => {
    const file = event.target.files[0];
    event.target.value = "";
    if (file) scan(null, file);
  });
  document.addEventListener("dragover", event => event.preventDefault());
  document.addEventListener("drop", event => {
    event.preventDefault();
    const file = event.dataTransfer.files[0];
    if (!file) return;
    const path = window.bronchoroute?.getPathForFile(file);
    scan(path, file);
  });
  if (window.bronchoroute) window.bronchoroute.onOpenArchive(path => scan(path));
  api("/api/features").then(async features => {
    if (features.viewer_only) return;
    open.hidden = false;
    const cases = await api("/api/cases");
    if (!cases.length) await prepare();
  }).catch(() => {});
})();
