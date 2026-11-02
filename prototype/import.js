// Changes: Series override reason and one processing-failure sentence.
// Changes: Case Library screen, library hit, and outdated-Case prompt.
// Changes: import initials use the shared Python-owned Settings API.
// Changes: Archive input, Study picker, clinician prompt and processing screens.
"use strict";

(() => {
  let session = null;
  let archiveSource = null;
  let activeCase = null;
  let busy = false;
  let viewerOnly = false;
  let outdatedDismissed = null;
  const screen = document.createElement("section");
  screen.id = "import-screen";
  screen.hidden = true;
  screen.innerHTML = `
    <div class="import-heading"><span>BronchoRoute</span><span class="import-actions">
      <button type="button" id="import-library">Library</button>
      <button type="button" id="archive-back" hidden>Back to Case</button></span></div>
    <main class="import-panel">
      <p class="import-eyebrow">LOCAL CT PROCESSING</p>
      <h1>Open an Archive</h1>
      <p>Choose a reconstruction, build its airway model, then open the Case.</p>
      <div id="archive-drop" tabindex="0" aria-label="Drop an Archive">
        <strong>Drop an Archive here</strong><p>Zip, folder or DICOMDIR · processed on this Mac</p>
        <button type="button" id="archive-choose">Choose…</button>
        <input id="archive-file" type="file" accept=".zip" hidden>
      </div>
      <p id="import-message" role="status" aria-live="polite"></p>
      <button type="button" id="try-another" hidden>Try another Series</button>
      <div id="series-picker" hidden><h2>Choose a Series</h2><div id="study-groups"></div>
        <form id="import-confirm"><div id="clinician-prompt" hidden>
          <label>Your initials <input id="clinician-initials" maxlength="12" autocomplete="off"></label>
          <p>Saved locally for Case records.</p></div>
          <label id="override-reason-field" hidden>Reason for choosing this Series
            <input id="override-reason" maxlength="200" autocomplete="off"></label>
          <button id="series-confirm" type="submit">Process Series</button>
        </form>
      </div>
      <div id="processing" hidden><h2>Building the Case</h2>
        <ol id="processing-steps"><li>Reading</li><li>Segmentation</li><li>Airway model</li><li>Graph</li></ol>
        <p><span id="processing-elapsed">0 s</span> elapsed · Estimated 6–7 min</p>
        <button type="button" id="processing-cancel">Cancel</button>
      </div>
    </main>`;
  document.body.appendChild(screen);
  const libraryScreen = document.createElement("section");
  libraryScreen.id = "library-screen";
  libraryScreen.hidden = true;
  libraryScreen.innerHTML = `
    <div class="import-heading"><span>BronchoRoute</span>
      <button type="button" id="library-back">Back</button></div>
    <main class="import-panel">
      <p class="import-eyebrow">CASE LIBRARY</p>
      <h1>Case Library</h1>
      <p id="library-warn" hidden>Library is larger than 10 GB.</p>
      <p id="library-total"></p>
      <p id="library-empty" hidden>No Cases stored.</p>
      <div id="library-table-wrap"><table id="library-table">
        <thead><tr>
          <th>Case</th><th>Study date</th><th>Series</th><th>Processed</th><th>Size</th><th>Outdated</th><th></th>
        </tr></thead>
        <tbody id="library-rows"></tbody>
      </table></div>
      <p id="library-message" role="alert"></p>
      <button type="button" id="library-delete-all">Delete all</button>
    </main>
    <dialog id="library-confirm" aria-labelledby="library-confirm-text">
      <p id="library-confirm-text"></p>
      <button type="button" id="library-confirm-yes">Delete</button>
      <button type="button" id="library-confirm-no">Cancel</button>
    </dialog>`;
  document.body.appendChild(libraryScreen);
  const outdated = document.createElement("div");
  outdated.id = "outdated-note";
  outdated.hidden = true;
  outdated.setAttribute("role", "dialog");
  outdated.setAttribute("aria-labelledby", "outdated-text");
  outdated.innerHTML = `
    <p id="outdated-text">Processed with an older version — Reprocess?</p>
    <p id="outdated-error" role="alert"></p>
    <button type="button" id="outdated-reprocess">Reprocess</button>
    <button type="button" id="outdated-open">Open anyway</button>`;
  document.body.appendChild(outdated);
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

  function libraryMessage(text) {
    get("library-message").textContent = text;
  }

  function formatBytes(value) {
    const size = Number(value) || 0;
    if (size < 1024) return `${size} B`;
    if (size < 1024 ** 2) return `${(size / 1024).toFixed(1)} KB`;
    if (size < 1024 ** 3) return `${(size / 1024 ** 2).toFixed(1)} MB`;
    return `${(size / 1024 ** 3).toFixed(1)} GB`;
  }

  function formatStudyDate(value) {
    const text = String(value || "");
    const match = /^(\d{4})(\d{2})(\d{2})$/.exec(text);
    if (match) return `${match[1]}-${match[2]}-${match[3]}`;
    return text || "—";
  }

  function formatProcessed(value) {
    if (!value) return "—";
    return String(value).slice(0, 10);
  }

  function showProcessing() {
    libraryScreen.hidden = true;
    outdated.hidden = true;
    screen.hidden = false;
    get("series-picker").hidden = true;
    get("archive-drop").hidden = true;
    get("processing").hidden = false;
    get("try-another").hidden = true;
    get("processing-cancel").disabled = true;
    message("");
  }

  async function cancel() {
    if (!activeCase) return;
    get("processing-cancel").disabled = true;
    await api(`/api/cases/${activeCase}/jobs/latest/cancel`, { method: "POST" });
    activeCase = null;
    get("processing").hidden = true;
    get("archive-drop").hidden = false;
    get("try-another").hidden = true;
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
    libraryScreen.hidden = true;
    screen.hidden = false;
    get("series-picker").hidden = true;
    get("processing").hidden = true;
    get("archive-drop").hidden = false;
    get("try-another").hidden = true;
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

  function actionButton(label, className, onClick) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = className;
    button.textContent = label;
    button.addEventListener("click", () => {
      onClick().catch(error => libraryMessage(error.message));
    });
    return button;
  }

  function confirmLibrary(text) {
    const dialog = get("library-confirm");
    get("library-confirm-text").textContent = text;
    return new Promise(resolve => {
      const yes = get("library-confirm-yes");
      const no = get("library-confirm-no");
      let settled = false;
      const finish = (value) => {
        if (settled) return;
        settled = true;
        yes.removeEventListener("click", onYes);
        no.removeEventListener("click", onNo);
        dialog.removeEventListener("close", onClose);
        if (dialog.open) dialog.close();
        resolve(value);
      };
      const onYes = () => finish(true);
      const onNo = () => finish(false);
      const onClose = () => finish(false);
      yes.addEventListener("click", onYes);
      no.addEventListener("click", onNo);
      dialog.addEventListener("close", onClose);
      dialog.showModal();
    });
  }

  async function releaseSession() {
    if (!session) return;
    const importId = session.import_id;
    session = null;
    await api(`/api/imports/${importId}`, { method: "DELETE" }).catch(() => {});
  }

  async function openCase(caseId) {
    await releaseSession();
    location.assign(`/?case=${encodeURIComponent(caseId)}`);
  }

  async function reprocess(caseId, source) {
    if (busy) return;
    busy = true;
    try {
      await releaseSession();
      showProcessing();
      await api(`/api/cases/${encodeURIComponent(caseId)}/reprocess`, { method: "POST" });
      activeCase = caseId;
      get("processing-cancel").disabled = false;
      poll(activeCase);
    } catch (error) {
      const text = error.status === 412
        ? "Set your initials in Settings, then try Reprocess again."
        : error.message;
      get("processing").hidden = true;
      if (source === "library") {
        screen.hidden = true;
        libraryScreen.hidden = false;
        libraryMessage(text);
      } else if (source === "picker") {
        screen.hidden = false;
        get("series-picker").hidden = false;
        get("archive-drop").hidden = true;
        message(text);
      } else {
        screen.hidden = true;
        showOutdated(caseId);
        get("outdated-error").textContent = text;
      }
    } finally {
      busy = false;
    }
  }

  function showOutdated(caseId) {
    outdated.hidden = false;
    outdated.dataset.caseId = caseId;
    get("outdated-error").textContent = "";
    get("outdated-reprocess").hidden = viewerOnly;
  }

  async function maybeOutdated() {
    const caseId = new URLSearchParams(location.search).get("case");
    if (!caseId || outdatedDismissed === caseId) return;
    try {
      const library = await api("/api/library");
      const row = library.cases.find(item => item.id === caseId);
      if (row && row.outdated) showOutdated(caseId);
    } catch (error) {
      // Opening still works when the library cannot be read.
    }
  }

  async function showLibrary() {
    libraryScreen.hidden = false;
    libraryMessage("");
    const body = await api("/api/library");
    get("library-warn").hidden = !body.warn;
    get("library-total").textContent = `Total size ${formatBytes(body.total_bytes)}`;
    const rows = get("library-rows");
    rows.replaceChildren();
    for (const item of body.cases) {
      const tr = document.createElement("tr");
      cell(tr, item.id);
      cell(tr, formatStudyDate(item.study_date));
      cell(tr, item.series_description || "—");
      cell(tr, formatProcessed(item.processed_at));
      cell(tr, formatBytes(item.size_bytes));
      cell(tr, item.outdated ? "Yes" : "No");
      const actions = cell(tr, "");
      actions.appendChild(actionButton("Open", "library-open", () => openCase(item.id)));
      if (!viewerOnly) {
        actions.append(
          actionButton("Reprocess", "library-reprocess", () => reprocess(item.id, "library")),
          actionButton("Delete", "library-delete", () => removeCase(item.id)),
        );
      }
      rows.appendChild(tr);
    }
    get("library-empty").hidden = body.cases.length > 0;
    const all = get("library-delete-all");
    all.hidden = viewerOnly;
    all.disabled = body.cases.length === 0;
    all.dataset.count = String(body.cases.length);
  }

  async function removeCase(caseId) {
    if (busy) return;
    const ok = await confirmLibrary(`Delete ${caseId}? This cannot be undone.`);
    if (!ok) return;
    busy = true;
    try {
      await api(`/api/cases/${encodeURIComponent(caseId)}`, { method: "DELETE" });
      await showLibrary();
    } finally {
      busy = false;
    }
  }

  async function removeAll() {
    if (busy) return;
    const count = get("library-delete-all").dataset.count || "0";
    const noun = count === "1" ? "Case" : "Cases";
    const ok = await confirmLibrary(`Delete all ${count} ${noun}? This cannot be undone.`);
    if (!ok) return;
    busy = true;
    try {
      await api("/api/library", { method: "DELETE" });
      await showLibrary();
    } finally {
      busy = false;
    }
  }

  function selectedSeries() {
    const choice = screen.querySelector('input[name="series-choice"]:checked');
    if (!session || !choice) return null;
    return session.series.find(item => item.series_key === choice.value) || null;
  }

  function overrideNeeded() {
    const row = selectedSeries();
    const recommended = Boolean(session && session.series.some(item => item.status === "recommended"));
    return Boolean(row && row.status === "usable" && recommended);
  }

  function syncProcessButton() {
    const input = get("override-reason");
    const needed = overrideNeeded();
    const field = get("override-reason-field");
    if (!needed) {
      field.hidden = true;
      input.value = "";
    } else {
      field.hidden = false;
    }
    const choice = screen.querySelector('input[name="series-choice"]:checked');
    const row = selectedSeries();
    const blank = needed && !input.value.trim();
    get("series-confirm").disabled = !choice || Boolean(row && row.in_library) || blank;
  }

  async function scan(path, file) {
    if (!await prepare()) return;
    if (path) archiveSource = { path, file: null };
    else if (file) archiveSource = { path: null, file };
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
          radio.addEventListener("change", syncProcessButton);
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
          if (series.in_library) {
            const hit = document.createElement("strong");
            hit.className = "series-badge in-library";
            hit.textContent = "In library";
            const openHit = document.createElement("button");
            openHit.type = "button";
            openHit.className = "series-open";
            openHit.textContent = "Open";
            openHit.addEventListener("click", () => {
              openCase(series.in_library).catch(error => message(error.message));
            });
            const again = document.createElement("button");
            again.type = "button";
            again.className = "series-reprocess";
            again.textContent = "Reprocess";
            again.addEventListener("click", () => reprocess(series.in_library, "picker"));
            suitability.append(document.createTextNode(" "), hit, openHit, again);
          }
          tbody.appendChild(tr);
        }
        table.appendChild(tbody);
        groups.appendChild(table);
      }
      const clinician = await api("/api/settings");
      get("clinician-prompt").hidden = Boolean(clinician.initials);
      get("series-picker").hidden = false;
      get("archive-drop").hidden = true;
      syncProcessButton();
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
          if (job.error === "cancelled") {
            get("try-another").hidden = true;
            message("Processing cancelled. Open an Archive to try again.");
            return;
          }
          get("try-another").hidden = false;
          message(`${job.error}.`);
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
    const chosen = session.series.find(item => item.series_key === choice.value);
    if (chosen && chosen.in_library) {
      openCase(chosen.in_library).catch(error => message(error.message));
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
        body: JSON.stringify({
          import_id: session.import_id,
          series_key: choice.value,
          override_reason: get("override-reason").value.trim(),
        }),
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
      syncProcessButton();
    }
  });
  async function tryAnother() {
    const source = archiveSource;
    get("try-another").hidden = true;
    if (!source || (!source.path && !source.file)) {
      await prepare();
      return;
    }
    if (source.path) await scan(source.path);
    else await scan(null, source.file);
  }
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
  get("import-library").addEventListener("click", () => {
    showLibrary().catch(error => message(error.message));
  });
  get("library-open").addEventListener("click", event => {
    event.preventDefault();
    showLibrary().catch(error => libraryMessage(error.message));
  });
  get("library-back").addEventListener("click", () => { libraryScreen.hidden = true; });
  get("library-delete-all").addEventListener("click", () => {
    removeAll().catch(error => libraryMessage(error.message));
  });
  get("outdated-open").addEventListener("click", () => {
    outdatedDismissed = outdated.dataset.caseId || outdatedDismissed;
    outdated.hidden = true;
  });
  get("outdated-reprocess").addEventListener("click", () => {
    reprocess(outdated.dataset.caseId, "outdated");
  });
  get("processing-cancel").addEventListener("click", () => cancel().catch(error => message(error.message)));
  get("try-another").addEventListener("click", () => tryAnother().catch(error => message(error.message)));
  get("override-reason").addEventListener("input", syncProcessButton);
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
    viewerOnly = Boolean(features.viewer_only);
    get("outdated-reprocess").hidden = viewerOnly;
    if (features.viewer_only) return;
    open.hidden = false;
    const cases = await api("/api/cases");
    if (!cases.length) await prepare();
  }).catch(() => {});
  maybeOutdated();
})();
