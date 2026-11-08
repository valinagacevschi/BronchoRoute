// Changes: shared ? help button and help sheet on every screen (#71).
// Changes: an opened Case keeps the current page, so the desktop stays in edit mode (#70).
// Changes: Archive open shows an in-page guard instead of window.confirm.
// Changes: drop Series initials prompt; settings auto-supply initials.
// Changes: selection summary uses textContent (no Archive description HTML).
// Changes: focus initials when Series prompt opens so Electron insertText lands.
// Changes: topbar archive-open, failure note placement, library meta/stats, weights error note.
// Changes: intake shell rail, tokenised screens, drag-over/reading/failed states per design draft.
// Changes: park #archive-open below/above Limitations so it clears fixed chrome.
// Changes: download models before processing; existing Cases stay open.
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
  let libraryMeta = null;

  function caseUrl(caseId) {
    const path = location.pathname === "" ? "/" : location.pathname;
    return `${path}?case=${encodeURIComponent(caseId)}`;
  }

  const CHECK_SVG = '<svg width="12" height="12" viewBox="0 0 12 12" fill="none" aria-hidden="true"><path d="M2.5 6.2 5 8.6l4.5-5" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/></svg>';
  const NOTE_ICON = '<svg width="16" height="16" viewBox="0 0 16 16" fill="none" aria-hidden="true"><circle cx="8" cy="8" r="7" stroke="currentColor" stroke-width="1.5"/><path d="M8 4.5v4.2M8 11v.5" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/></svg>';
  const DROP_ICON = '<div class="drop-icon"><svg width="26" height="26" viewBox="0 0 24 24" fill="none" aria-hidden="true"><path d="M12 15V4m0 0L7.5 8.5M12 4l4.5 4.5" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/><path d="M4 14v3.5A2.5 2.5 0 0 0 6.5 20h11a2.5 2.5 0 0 0 2.5-2.5V14" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/></svg></div>';

  function buildRail() {
    const rail = document.createElement("aside");
    rail.className = "intake-rail";
    rail.setAttribute("aria-label", "BronchoRoute");
    rail.innerHTML = `
      <div class="intake-rail-top"><div class="intake-brand"><span>BronchoRoute</span>Airway route viewer</div></div>
      <p class="intake-rail-label">Steps</p>
      <ol class="intake-steps">
        <li class="intake-step" data-step="models"><span class="mark">1</span>Models<span class="meta"></span></li>
        <li class="intake-step" data-step="archive"><span class="mark">2</span>Archive<span class="meta"></span></li>
        <li class="intake-step" data-step="series"><span class="mark">3</span>Series<span class="meta"></span></li>
        <li class="intake-step" data-step="build"><span class="mark">4</span>Build<span class="meta"></span></li>
      </ol>
      <p class="intake-rail-label">On this Mac</p>
      <nav class="intake-rail-nav">
        <button type="button" data-intake-nav="new" id="import-library-new">New Case</button>
        <button type="button" data-intake-nav="library" id="import-library">Case Library</button>
      </nav>
      <div class="intake-rail-spacer"></div>
      <div class="intake-rail-local"><span class="dot"></span><span>Processing stays on this Mac</span></div>`;
    return rail;
  }

  const sharedRail = buildRail();

  function crumbHtml(parts) {
    return parts.map((part, index) => {
      if (index === parts.length - 1) return `<b>${part}</b>`;
      return `${part}<i>/</i>`;
    }).join("");
  }

  function wrapScreen(section, options) {
    const main = document.createElement("div");
    main.className = "intake-main";
    const topbar = document.createElement("header");
    topbar.className = "intake-topbar";
    topbar.innerHTML = `<div class="intake-crumb">${crumbHtml(options.crumb)}</div>
      <div class="intake-topbar-actions"></div>`;
    const actions = topbar.querySelector(".intake-topbar-actions");
    for (const action of options.actions || []) actions.appendChild(action);
    const scroll = document.createElement("div");
    scroll.className = "intake-scroll";
    scroll.appendChild(options.panel);
    main.append(topbar, scroll);
    section.appendChild(main);
    return topbar;
  }

  const screen = document.createElement("section");
  screen.id = "import-screen";
  screen.hidden = true;
  const importPanel = document.createElement("main");
  importPanel.className = "import-panel";
  importPanel.innerHTML = `
      <div id="archive-view">
        <p class="import-eyebrow">Local CT processing</p>
        <h1>Open an Archive</h1>
        <p class="import-lede">Choose a reconstruction, build its airway model, then open the Case.</p>
        <div class="drop-grid">
          <div id="archive-drop" tabindex="0" aria-label="Drop an Archive">
            <div class="drop-idle" id="drop-idle">
              ${DROP_ICON}
              <strong>Drop an Archive here</strong>
              <p>Zip, folder or DICOMDIR · processed on this Mac</p>
              <button type="button" id="archive-choose">Choose…</button>
              <div class="drop-or" id="archive-hotkey" hidden><kbd>⌘</kbd> <kbd>O</kbd> also opens an Archive</div>
              <input id="archive-file" type="file" accept=".zip" hidden>
            </div>
            <div class="drop-busy" id="drop-busy" hidden>
              <div class="intake-spinner" aria-hidden="true"></div>
              <strong>Reading Archive…</strong>
            </div>
          </div>
          <ul class="drop-facts">
            <li><b>Stays on this Mac</b><span>The Archive is read locally. Nothing is uploaded.</span></li>
            <li><b>You choose the Series</b><span>Each reconstruction is checked for thickness, kernel and coverage.</span></li>
            <li><b>About 6–7 minutes</b><span>Segmentation, airway model and graph run in the background.</span></li>
          </ul>
        </div>
      </div>
      <div id="series-picker" hidden>
        <p class="import-eyebrow" id="series-eyebrow">Archive</p>
        <h1>Choose a Series</h1>
        <p class="import-lede">The recommended reconstruction is preselected. Thin, sharp-kernel Series with full chest coverage give the best airway model.</p>
        <div id="study-groups"></div>
        <form id="import-confirm">
          <div class="selection-summary" id="selection-summary"></div>
          <label class="field" id="override-reason-field" hidden>Reason for choosing this Series
            <input id="override-reason" maxlength="200" autocomplete="off"></label>
          <button id="series-confirm" type="submit">Process Series</button>
        </form>
      </div>
      <div id="processing" hidden>
        <p class="import-eyebrow">Local CT processing</p>
        <h1>Building the Case</h1>
        <p class="import-lede">You can leave this screen open. The Case opens by itself when the graph is ready.</p>
        <ol id="processing-steps">
          <li data-state="pending"><div class="track"><i></i></div><div class="n">01</div><div class="t">Reading</div><div class="s"></div></li>
          <li data-state="pending"><div class="track"><i></i></div><div class="n">02</div><div class="t">Segmentation</div><div class="s"></div></li>
          <li data-state="pending"><div class="track"><i></i></div><div class="n">03</div><div class="t">Airway model</div><div class="s"></div></li>
          <li data-state="pending"><div class="track"><i></i></div><div class="n">04</div><div class="t">Graph</div><div class="s"></div></li>
        </ol>
        <div class="processing-clock"><span><span id="processing-elapsed">0 s</span> elapsed</span><span>Estimated 6–7 min</span></div>
        <div class="processing-actions">
          <button type="button" id="processing-cancel" class="btn-ghost">Cancel</button>
          <button type="button" id="try-another" hidden>Try another Series</button>
        </div>
      </div>
      <p id="import-message" role="status" aria-live="polite"></p>`;
  const archiveBack = document.createElement("button");
  archiveBack.type = "button";
  archiveBack.id = "archive-back";
  archiveBack.className = "btn-ghost btn-sm";
  archiveBack.textContent = "Back to Case";
  archiveBack.hidden = true;
  wrapScreen(screen, {
    crumb: ["New Case", "Archive"],
    panel: importPanel,
    actions: [archiveBack],
  });
  document.body.appendChild(screen);

  const libraryScreen = document.createElement("section");
  libraryScreen.id = "library-screen";
  libraryScreen.hidden = true;
  const libraryPanel = document.createElement("main");
  libraryPanel.className = "import-panel";
  libraryPanel.innerHTML = `
      <div class="lib-head">
        <div>
          <p class="import-eyebrow">On this Mac</p>
          <h1>Case Library</h1>
        </div>
        <div class="lib-stats">
          <div><b id="library-count">0</b>Cases</div>
          <div id="library-total"></div>
        </div>
      </div>
      <p id="library-warn" hidden>Library is larger than 10 GB.</p>
      <p id="library-empty" hidden>No Cases stored.</p>
      <div id="library-table-wrap"><table id="library-table">
        <thead><tr>
          <th>Case</th><th>Study date</th><th>Series</th><th>Processed</th><th>Size</th><th>Outdated</th><th></th>
        </tr></thead>
        <tbody id="library-rows"></tbody>
      </table></div>
      <p id="library-message" role="alert"></p>
      <div class="lib-foot">
        <button type="button" id="library-delete-all" class="btn-danger btn-sm">Delete all</button>
      </div>
      <dialog id="library-confirm" aria-labelledby="library-confirm-text">
        <p id="library-confirm-text"></p>
        <button type="button" id="library-confirm-yes" class="btn-danger-solid">Delete</button>
        <button type="button" id="library-confirm-no" class="btn-ghost">Cancel</button>
      </dialog>`;
  const libraryBack = document.createElement("button");
  libraryBack.type = "button";
  libraryBack.id = "library-back";
  libraryBack.className = "btn-ghost btn-sm";
  libraryBack.textContent = "Back";
  wrapScreen(libraryScreen, {
    crumb: ["Case Library"],
    panel: libraryPanel,
    actions: [libraryBack],
  });
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
    <button type="button" id="outdated-open" class="btn-ghost">Open anyway</button>`;
  document.body.appendChild(outdated);

  const weightsScreen = document.createElement("section");
  weightsScreen.id = "weights-screen";
  weightsScreen.hidden = true;
  const weightsPanel = document.createElement("main");
  weightsPanel.className = "import-panel";
  weightsPanel.innerHTML = `
      <p class="import-eyebrow">One-time setup</p>
      <h1 id="weights-title">Download models</h1>
      <p class="import-lede">These models are required before an Archive can be processed. Cases already on this Mac still open.</p>
      <section class="weights-meter" aria-labelledby="weights-title">
        <div class="weights-meter-head">
          <span class="label">Segmentation models</span>
          <span class="pct" id="weights-pct">0%</span>
        </div>
        <div class="weights-bar" id="weights-bar"><i id="weights-bar-fill"></i></div>
        <div class="weights-meter-foot">
          <span id="weights-progress" role="status" aria-live="polite">0.0 MB / 0.0 MB · 0%</span>
        </div>
        <p id="weights-error" role="alert"></p>
      </section>
      <div class="weights-actions">
        <button type="button" id="weights-cancel" class="btn-ghost">Cancel</button>
        <button type="button" id="weights-retry" hidden>Retry</button>
      </div>`;
  wrapScreen(weightsScreen, {
    crumb: ["Setup", "Models"],
    panel: weightsPanel,
    actions: [],
  });
  document.body.appendChild(weightsScreen);
  screen.insertBefore(sharedRail, screen.firstChild);

  const get = id => document.getElementById(id);

  const helpButton = document.createElement("button");
  helpButton.type = "button";
  helpButton.id = "help-open";
  helpButton.className = "help-open";
  helpButton.setAttribute("aria-label", "Help");
  helpButton.title = "Help";
  helpButton.textContent = "?";
  document.body.appendChild(helpButton);

  const helpOverlay = document.createElement("section");
  helpOverlay.id = "help-overlay";
  helpOverlay.hidden = true;
  helpOverlay.setAttribute("role", "dialog");
  helpOverlay.setAttribute("aria-modal", "true");
  helpOverlay.setAttribute("aria-labelledby", "help-title");
  helpOverlay.innerHTML = `
    <div class="help-panel" role="document">
      <header class="help-header">
        <h1 id="help-title">Help</h1>
        <button type="button" id="help-close">Close</button>
      </header>
      <div class="help-body"></div>
    </div>`;
  const helpTemplate = document.getElementById("help-content");
  if (helpTemplate) {
    helpOverlay.querySelector(".help-body").appendChild(helpTemplate.content.cloneNode(true));
  }
  document.body.appendChild(helpOverlay);
  let helpReturnFocus = null;

  function syncHelpMount() {
    const host = !weightsScreen.hidden ? weightsScreen
      : !libraryScreen.hidden ? libraryScreen
      : !screen.hidden ? screen : null;
    const actions = host ? host.querySelector(".intake-topbar-actions") : null;
    if (actions) {
      helpButton.classList.add("btn-ghost", "btn-sm");
      if (helpButton.parentElement !== actions) actions.appendChild(helpButton);
      return;
    }
    helpButton.classList.remove("btn-ghost", "btn-sm");
    const row = document.querySelector(".rail .brand-row");
    if (row && helpButton.parentElement !== row) row.appendChild(helpButton);
  }

  function openHelp() {
    if (!helpOverlay.hidden) return;
    helpReturnFocus = document.activeElement;
    helpOverlay.hidden = false;
    get("help-close").focus();
  }

  function closeHelp() {
    if (helpOverlay.hidden) return;
    helpOverlay.hidden = true;
    if (helpReturnFocus && typeof helpReturnFocus.focus === "function") {
      helpReturnFocus.focus();
    }
    helpReturnFocus = null;
  }

  helpButton.addEventListener("click", openHelp);
  get("help-close").addEventListener("click", closeHelp);
  helpOverlay.addEventListener("click", event => {
    if (event.target === helpOverlay) closeHelp();
  });
  document.addEventListener("keydown", event => {
    if (helpOverlay.hidden) return;
    if (event.key === "Escape") closeHelp();
    event.preventDefault();
    event.stopPropagation();
  }, true);
  syncHelpMount();
  const open = document.createElement("button");
  open.id = "archive-open";
  open.textContent = "Open Archive…";
  open.hidden = true;
  document.body.appendChild(open);

  if (window.bronchoroute) get("archive-hotkey").hidden = false;

  function overlayOpen() {
    return !screen.hidden || !libraryScreen.hidden || !weightsScreen.hidden;
  }

  function mountArchiveOpenInTopbar(host) {
    const actions = host.querySelector(".intake-topbar-actions");
    if (!actions) return;
    open.classList.add("btn-ghost", "btn-sm");
    open.style.top = "";
    open.style.right = "";
    if (open.parentElement !== actions) actions.insertBefore(open, actions.firstChild);
  }

  function restoreArchiveOpenToBody() {
    open.classList.remove("btn-ghost", "btn-sm");
    if (open.parentElement !== document.body) document.body.appendChild(open);
  }

  function syncArchiveOpen() {
    if (viewerOnly) {
      open.hidden = true;
      restoreArchiveOpenToBody();
      return;
    }
    const onImportForm = !screen.hidden && get("processing").hidden && libraryScreen.hidden;
    open.hidden = onImportForm || !weightsScreen.hidden;
    if (open.hidden) {
      if (!overlayOpen()) restoreArchiveOpenToBody();
      return;
    }
    if (!libraryScreen.hidden) {
      mountArchiveOpenInTopbar(libraryScreen);
      return;
    }
    if (!screen.hidden && !get("processing").hidden) {
      mountArchiveOpenInTopbar(screen);
      return;
    }
    restoreArchiveOpenToBody();
    placeArchiveOpen();
  }

  function placeArchiveOpen() {
    if (open.hidden) return;
    if (open.parentElement !== document.body) return;
    let top = 14;
    let right = 18;
    const panel = document.getElementById("limitations-panel");
    if (panel) {
      const box = panel.getBoundingClientRect();
      if (box.width > 0 && box.height > 0) {
        const gap = 8;
        right = Math.max(18, Math.round(window.innerWidth - box.right));
        const btnH = open.offsetHeight || 40;
        if (box.top < window.innerHeight * 0.5) {
          top = Math.ceil(box.bottom + gap);
        } else {
          top = Math.max(14, Math.floor(box.top - gap - btnH));
        }
        const maxTop = Math.max(14, window.innerHeight - btnH - gap);
        if (top > maxTop) top = maxTop;
      }
    }
    open.style.top = `${top}px`;
    open.style.right = `${right}px`;
  }

  function setCrumb(host, parts) {
    const crumb = host.querySelector(".intake-crumb");
    if (crumb) crumb.innerHTML = crumbHtml(parts);
  }

  function syncLibraryNavMeta() {
    const button = get("import-library");
    if (!button) return;
    const existing = button.querySelector(".mono");
    if (existing) existing.remove();
    if (!libraryMeta || libraryMeta.count <= 0) return;
    const meta = document.createElement("span");
    meta.className = "mono";
    meta.textContent = `${libraryMeta.count} · ${formatBytes(libraryMeta.total_bytes)}`;
    button.appendChild(meta);
  }

  function mountRail(host, mode) {
    if (sharedRail.parentElement !== host) host.insertBefore(sharedRail, host.firstChild);
    const order = ["models", "archive", "series", "build"];
    const active = mode === "library" ? -1 : order.indexOf(mode);
    sharedRail.querySelectorAll(".intake-step").forEach((li, index) => {
      const state = active < 0 ? "pending" : index < active ? "done" : index === active ? "active" : "pending";
      li.dataset.state = state;
      const mark = li.querySelector(".mark");
      mark.innerHTML = state === "done" ? CHECK_SVG : String(index + 1);
    });
    const libBtn = get("import-library");
    const newBtn = get("import-library-new");
    if (libBtn) {
      if (mode === "library") libBtn.setAttribute("aria-current", "page");
      else libBtn.removeAttribute("aria-current");
    }
    if (newBtn) {
      if (mode === "archive" || mode === "series" || mode === "build") newBtn.setAttribute("aria-current", "page");
      else newBtn.removeAttribute("aria-current");
    }
    syncLibraryNavMeta();
  }

  function syncIntakeChrome() {
    syncHelpMount();
    importPanel.classList.toggle("wide", !get("series-picker").hidden && get("processing").hidden);
    if (!weightsScreen.hidden) {
      mountRail(weightsScreen, "models");
      setCrumb(weightsScreen, ["Setup", "Models"]);
      syncArchiveOpen();
      return;
    }
    if (!libraryScreen.hidden) {
      mountRail(libraryScreen, "library");
      setCrumb(libraryScreen, ["Case Library"]);
      syncArchiveOpen();
      return;
    }
    if (screen.hidden) {
      syncArchiveOpen();
      return;
    }
    if (!get("processing").hidden) {
      mountRail(screen, "build");
      setCrumb(screen, ["New Case", "Build"]);
      syncArchiveOpen();
      return;
    }
    if (!get("series-picker").hidden) {
      mountRail(screen, "series");
      setCrumb(screen, ["New Case", "Series"]);
      syncArchiveOpen();
      return;
    }
    mountRail(screen, "archive");
    setCrumb(screen, ["New Case", "Archive"]);
    syncArchiveOpen();
  }

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

  function message(text, kind) {
    const el = get("import-message");
    el.classList.remove("is-error", "is-progress", "intake-note", "intake-note-error");
    if (!text) {
      el.replaceChildren();
      return;
    }
    if (kind === "progress") {
      el.textContent = text;
      el.classList.add("is-progress");
      return;
    }
    el.replaceChildren();
    el.insertAdjacentHTML("beforeend", NOTE_ICON);
    const span = document.createElement("span");
    span.textContent = text;
    el.appendChild(span);
    el.classList.add("intake-note", "intake-note-error", "is-error");
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

  function setDropBusy(on) {
    const drop = get("archive-drop");
    drop.classList.toggle("busy", on);
    get("drop-idle").hidden = on;
    get("drop-busy").hidden = !on;
  }

  function processingLede() {
    return get("processing").querySelector(".import-lede");
  }

  function parkImportMessage(failed) {
    const msg = get("import-message");
    const actions = get("processing").querySelector(".processing-actions");
    if (failed && actions) {
      actions.parentElement.insertBefore(msg, actions);
    } else if (msg.parentElement !== importPanel) {
      importPanel.appendChild(msg);
    }
  }

  function setProcessingFailed(on) {
    const lede = processingLede();
    if (lede) lede.hidden = on;
    get("processing-cancel").hidden = on;
    get("try-another").hidden = !on;
    parkImportMessage(on);
  }

  function showProcessing() {
    libraryScreen.hidden = true;
    outdated.hidden = true;
    screen.hidden = false;
    get("series-picker").hidden = true;
    get("archive-view").hidden = true;
    get("archive-drop").hidden = true;
    get("processing").hidden = false;
    setProcessingFailed(false);
    get("processing-cancel").disabled = true;
    message("");
    Array.from(get("processing-steps").children).forEach(li => {
      li.dataset.state = "pending";
      const status = li.querySelector(".s");
      if (status) status.textContent = "";
    });
    syncIntakeChrome();
  }

  async function cancel() {
    if (!activeCase) return;
    get("processing-cancel").disabled = true;
    await api(`/api/cases/${activeCase}/jobs/latest/cancel`, { method: "POST" });
    activeCase = null;
    get("processing").hidden = true;
    get("archive-view").hidden = false;
    get("archive-drop").hidden = false;
    setProcessingFailed(false);
    get("processing-cancel").disabled = false;
    message("Processing cancelled. Open an Archive to try again.", "progress");
    syncIntakeChrome();
  }

  function processingGuard() {
    if (!busy && !activeCase) return false;
    message("Cancel current processing first.");
    return true;
  }

  async function prepare() {
    if (processingGuard()) return false;
    if (session) {
      await api(`/api/imports/${session.import_id}`, { method: "DELETE" });
      session = null;
    }
    libraryScreen.hidden = true;
    screen.hidden = false;
    get("series-picker").hidden = true;
    get("processing").hidden = true;
    get("archive-view").hidden = false;
    get("archive-drop").hidden = false;
    setDropBusy(false);
    setProcessingFailed(false);
    get("archive-back").hidden = !new URLSearchParams(location.search).get("case");
    message("");
    syncIntakeChrome();
    return true;
  }

  function cell(row, value, className) {
    const td = document.createElement("td");
    if (className) td.className = className;
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
    location.assign(caseUrl(caseId));
  }

  function formatMb(bytes) {
    return (Number(bytes) / (1024 * 1024)).toFixed(1);
  }

  function weightsFailureText(error) {
    const text = error && error.message ? error.message : "";
    const checksum = "Model download failed checksum — try again";
    if (text.includes(checksum)) return checksum;
    return text || "Model download failed.";
  }

  function updateWeightsMeter(received, total, pct) {
    get("weights-pct").textContent = `${pct}%`;
    get("weights-bar-fill").style.width = `${pct}%`;
    get("weights-progress").textContent = `${formatMb(received)} MB / ${formatMb(total)} MB · ${pct}%`;
  }

  function showModelDownload(status) {
    const total = status.missing.reduce((sum, file) => sum + file.bytes, 0);
    const screenEl = get("weights-screen");
    const progress = get("weights-progress");
    const error = get("weights-error");
    const retry = get("weights-retry");
    const cancel = get("weights-cancel");
    const bar = get("weights-bar");
    get("weights-title").textContent = `Download models (~${Math.max(1, Math.round(total / (1024 * 1024)))} MB)`;
    progress.textContent = `0.0 MB / ${formatMb(total)} MB · 0%`;
    get("weights-pct").textContent = "0%";
    get("weights-bar-fill").style.width = "0%";
    error.replaceChildren();
    bar.classList.remove("failed");
    retry.hidden = true;
    cancel.disabled = false;
    screenEl.hidden = false;
    syncIntakeChrome();
    let stop = () => {};
    let cancelled = false;
    let settled = false;
    return new Promise(resolve => {
      const finish = (ok) => {
        if (settled) return;
        settled = true;
        stop();
        cancel.removeEventListener("click", onCancel);
        retry.removeEventListener("click", onRetry);
        screenEl.hidden = true;
        syncIntakeChrome();
        resolve(ok);
      };
      const onProgress = (payload) => {
        const received = Number(payload.received) || 0;
        const all = Number(payload.total) || total;
        const pct = all > 0 && received >= all ? 100 : Math.min(99, Math.floor(all > 0 ? (received / all) * 100 : 0));
        updateWeightsMeter(received, all, pct);
      };
      const start = () => {
        stop();
        cancelled = false;
        error.replaceChildren();
        bar.classList.remove("failed");
        retry.hidden = true;
        cancel.disabled = false;
        stop = window.bronchoroute.onWeightsProgress(onProgress);
        window.bronchoroute.downloadWeights().then(() => finish(true)).catch(err => {
          stop();
          stop = () => {};
          if (cancelled) {
            finish(false);
            return;
          }
          const text = weightsFailureText(err);
          error.replaceChildren();
          error.insertAdjacentHTML("beforeend", NOTE_ICON);
          const span = document.createElement("span");
          span.textContent = text;
          error.appendChild(span);
          bar.classList.add("failed");
          retry.hidden = false;
        });
      };
      const onCancel = () => {
        cancelled = true;
        cancel.disabled = true;
        window.bronchoroute.cancelWeights().catch(() => {});
      };
      const onRetry = () => start();
      cancel.addEventListener("click", onCancel);
      retry.addEventListener("click", onRetry);
      start();
    });
  }

  async function ensureModels() {
    const bridge = window.bronchoroute;
    if (!bridge || typeof bridge.weightsRequired !== "function") return true;
    if (!await bridge.weightsRequired()) return true;
    const status = await bridge.weightsStatus();
    if (status.ready) return true;
    return showModelDownload(status);
  }

  function cancelledWeights(source) {
    const text = "Model download cancelled.";
    if (source === "library") libraryMessage(text);
    else if (source === "outdated") get("outdated-error").textContent = text;
    else message(text);
  }

  async function reprocess(caseId, source) {
    if (busy) {
      const text = "Cancel current processing first.";
      if (source === "library") libraryMessage(text);
      else if (source === "outdated") get("outdated-error").textContent = text;
      else message(text);
      return;
    }
    busy = true;
    try {
      if (!await ensureModels()) {
        cancelledWeights(source);
        return;
      }
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
        get("archive-view").hidden = true;
        get("archive-drop").hidden = true;
        message(text);
      } else {
        screen.hidden = true;
        showOutdated(caseId);
        get("outdated-error").textContent = text;
      }
      syncIntakeChrome();
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
    syncIntakeChrome();
    const body = await api("/api/library");
    libraryMeta = { count: body.cases.length, total_bytes: body.total_bytes };
    get("library-warn").hidden = !body.warn;
    get("library-total").textContent = `Total size ${formatBytes(body.total_bytes)}`;
    get("library-count").textContent = String(body.cases.length);
    const rows = get("library-rows");
    rows.replaceChildren();
    for (const item of body.cases) {
      const tr = document.createElement("tr");
      const idCell = cell(tr, "");
      const caseSpan = document.createElement("span");
      caseSpan.className = "case";
      caseSpan.textContent = item.id;
      idCell.appendChild(caseSpan);
      cell(tr, formatStudyDate(item.study_date), "num");
      cell(tr, item.series_description || "—");
      cell(tr, formatProcessed(item.processed_at), "num");
      cell(tr, formatBytes(item.size_bytes), "num");
      const status = cell(tr, "");
      const chip = document.createElement("span");
      chip.className = `series-badge ${item.outdated ? "chip-warn" : "chip-ok"}`;
      chip.textContent = item.outdated ? "Yes" : "No";
      status.appendChild(chip);
      const actions = cell(tr, "");
      const wrap = document.createElement("div");
      wrap.className = "row-actions";
      wrap.appendChild(actionButton("Open", "library-open btn-sm", () => openCase(item.id)));
      if (!viewerOnly) {
        wrap.append(
          actionButton("Reprocess", "library-reprocess btn-ghost btn-sm", () => reprocess(item.id, "library")),
          actionButton("Delete", "library-delete btn-quiet btn-sm", () => removeCase(item.id)),
        );
      }
      actions.appendChild(wrap);
      rows.appendChild(tr);
    }
    get("library-empty").hidden = body.cases.length > 0;
    get("library-table-wrap").hidden = body.cases.length === 0;
    const all = get("library-delete-all");
    all.hidden = viewerOnly;
    all.disabled = body.cases.length === 0;
    all.dataset.count = String(body.cases.length);
    syncIntakeChrome();
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

  function syncSelectionSummary() {
    const summary = get("selection-summary");
    const row = selectedSeries();
    if (!row) {
      summary.replaceChildren();
      return;
    }
    const bold = document.createElement("b");
    bold.textContent = row.description || "CT Series";
    const count = document.createElement("span");
    count.className = "mono";
    count.textContent = String(row.count);
    summary.replaceChildren("Selected ", bold, " · ", count, " slices");
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
    screen.querySelectorAll(".series-row").forEach(tr => {
      const radio = tr.querySelector('input[name="series-choice"]');
      tr.classList.toggle("selected", Boolean(radio && radio.checked));
    });
    syncSelectionSummary();
  }

  async function scan(path, file) {
    if (!await prepare()) return;
    if (path) archiveSource = { path, file: null };
    else if (file) archiveSource = { path: null, file };
    busy = true;
    get("archive-choose").disabled = true;
    setDropBusy(true);
    message("Reading Archive…", "progress");
    try {
      session = await api(path ? "/api/imports/scan" : "/api/imports/upload", {
        method: "POST",
        headers: { "Content-Type": path ? "application/json" : "application/zip" },
        body: path ? JSON.stringify({ path }) : file,
      });
      const groups = get("study-groups");
      groups.replaceChildren();
      for (const study of session.studies) {
        const section = document.createElement("section");
        section.className = "study";
        const head = document.createElement("div");
        head.className = "study-head";
        const title = document.createElement("h2");
        title.textContent = study.description || "Study";
        const date = document.createElement("span");
        date.className = "mono";
        date.textContent = formatStudyDate(study.date);
        head.append(title, date);
        section.appendChild(head);
        const wrap = document.createElement("div");
        wrap.className = "table-wrap";
        const table = document.createElement("table");
        const headRow = document.createElement("tr");
        for (const label of ["Series", "Thickness / spacing", "Kernel", "Contrast", "Coverage", "Slices", "Suitability"]) {
          const th = document.createElement("th");
          th.textContent = label;
          headRow.appendChild(th);
        }
        const thead = document.createElement("thead");
        thead.appendChild(headRow);
        table.appendChild(thead);
        const tbody = document.createElement("tbody");
        for (const series of study.series) {
          const tr = document.createElement("tr");
          tr.className = "series-row";
          if (series.status === "not_usable") tr.classList.add("disabled");
          if (series.status === "recommended") tr.classList.add("selected");
          const label = document.createElement("label");
          label.className = "series-name";
          const radio = document.createElement("input");
          radio.type = "radio";
          radio.name = "series-choice";
          radio.value = series.series_key;
          radio.checked = series.status === "recommended";
          radio.disabled = series.status === "not_usable";
          radio.addEventListener("change", syncProcessButton);
          label.append(radio, document.createTextNode(series.description || "CT Series"));
          const nameCell = cell(tr, "");
          nameCell.appendChild(label);
          if (series.in_library) {
            const hit = document.createElement("div");
            hit.className = "lib-hit";
            const badge = document.createElement("strong");
            badge.className = "series-badge in-library";
            badge.textContent = "In library";
            const openHit = document.createElement("button");
            openHit.type = "button";
            openHit.className = "series-open btn-ghost btn-sm";
            openHit.textContent = "Open";
            openHit.addEventListener("click", event => {
              event.stopPropagation();
              openCase(series.in_library).catch(error => message(error.message));
            });
            const again = document.createElement("button");
            again.type = "button";
            again.className = "series-reprocess btn-quiet btn-sm";
            again.textContent = "Reprocess";
            again.addEventListener("click", event => {
              event.stopPropagation();
              reprocess(series.in_library, "picker");
            });
            hit.append(badge, openHit, again);
            nameCell.appendChild(hit);
          }
          cell(tr, `${series.slice_thickness_mm} / ${series.spacing_mm} mm`, "num");
          cell(tr, series.kernel_class);
          cell(tr, series.contrast ? "Contrast" : "Non-contrast");
          cell(tr, `${Math.round(series.z_span_mm)} mm`, "num");
          cell(tr, series.count, "num");
          const suitability = cell(tr, "");
          const badge = document.createElement("strong");
          badge.className = `series-badge ${series.status}`;
          badge.textContent = { recommended: "Recommended", usable: "Usable", not_usable: "Not usable" }[series.status];
          suitability.appendChild(badge);
          if (series.reason) {
            const reason = document.createElement("span");
            reason.className = "reason";
            reason.textContent = series.reason;
            suitability.appendChild(reason);
          }
          tr.addEventListener("click", event => {
            if (event.target.closest("button")) return;
            if (radio.disabled) return;
            radio.checked = true;
            syncProcessButton();
          });
          tbody.appendChild(tr);
        }
        table.appendChild(tbody);
        wrap.appendChild(table);
        section.appendChild(wrap);
        groups.appendChild(section);
      }
      get("series-picker").hidden = false;
      get("archive-view").hidden = true;
      get("archive-drop").hidden = true;
      setDropBusy(false);
      syncProcessButton();
      message("");
      syncIntakeChrome();
    } catch (error) {
      setDropBusy(false);
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
          const status = li.querySelector(".s");
          if (i < index) {
            li.dataset.state = "done";
            if (status) status.textContent = "done";
          } else if (i === index) {
            li.dataset.state = "active";
            if (status) status.textContent = "running…";
          } else {
            li.dataset.state = "pending";
            if (status) status.textContent = "";
          }
        });
        if (job.status === "succeeded") {
          location.assign(caseUrl(caseId));
          return;
        }
        if (job.status === "failed") {
          activeCase = null;
          if (job.error === "cancelled") {
            get("processing").hidden = true;
            get("archive-view").hidden = false;
            get("archive-drop").hidden = false;
            setProcessingFailed(false);
            message("Processing cancelled. Open an Archive to try again.", "progress");
            syncIntakeChrome();
            return;
          }
          const kids = Array.from(get("processing-steps").children);
          const failed = kids.find(li => li.dataset.state === "active") || kids[Math.max(0, index)];
          if (failed) {
            failed.dataset.state = "failed";
            const status = failed.querySelector(".s");
            if (status) status.textContent = "stopped";
          }
          setProcessingFailed(true);
          message(`${job.error}.`);
          syncIntakeChrome();
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
      if (!await ensureModels()) {
        message("Model download cancelled.");
        return;
      }
      get("series-picker").hidden = true;
      get("archive-view").hidden = true;
      get("processing").hidden = false;
      setProcessingFailed(false);
      get("processing-cancel").disabled = true;
      message("Reading Series…", "progress");
      syncIntakeChrome();
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
      message(
        error.status === 412
          ? "Set your initials in Settings, then try again."
          : error.message,
      );
      syncIntakeChrome();
    } finally {
      busy = false;
      syncProcessButton();
    }
  });
  async function tryAnother() {
    const source = archiveSource;
    setProcessingFailed(false);
    get("processing").hidden = true;
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
  get("archive-choose").addEventListener("click", event => {
    event.stopPropagation();
    choose().catch(error => message(error.message));
  });
  get("archive-drop").addEventListener("click", event => {
    if (event.target.closest("button") || event.target.closest("input")) return;
    choose().catch(error => message(error.message));
  });
  get("archive-back").addEventListener("click", () => { screen.hidden = true; syncIntakeChrome(); });
  get("import-library").addEventListener("click", () => {
    showLibrary().catch(error => message(error.message));
  });
  get("import-library-new").addEventListener("click", () => {
    prepare().catch(error => message(error.message));
  });
  get("library-open").addEventListener("click", event => {
    event.preventDefault();
    showLibrary().catch(error => libraryMessage(error.message));
  });
  get("library-back").addEventListener("click", () => { libraryScreen.hidden = true; syncIntakeChrome(); });
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
    if (file) scan(null, file).catch(error => message(error.message));
  });
  const drop = get("archive-drop");
  drop.addEventListener("dragenter", event => {
    event.preventDefault();
    drop.classList.add("drag-over");
  });
  drop.addEventListener("dragover", event => {
    event.preventDefault();
    drop.classList.add("drag-over");
  });
  drop.addEventListener("dragleave", () => {
    drop.classList.remove("drag-over");
  });
  drop.addEventListener("drop", event => {
    event.preventDefault();
    drop.classList.remove("drag-over");
  });
  document.addEventListener("dragover", event => event.preventDefault());
  document.addEventListener("drop", event => {
    event.preventDefault();
    drop.classList.remove("drag-over");
    const file = event.dataTransfer.files[0];
    if (!file) return;
    const path = window.bronchoroute?.getPathForFile(file);
    scan(path, file).catch(error => message(error.message));
  });
  if (window.bronchoroute) {
    window.bronchoroute.onOpenArchive(path => scan(path).catch(error => message(error.message)));
  }
  window.addEventListener("resize", placeArchiveOpen);
  const placeWhenChromeMoves = new MutationObserver(placeArchiveOpen);
  placeWhenChromeMoves.observe(document.body, {
    attributes: true, attributeFilter: ["class"],
  });
  for (const el of [screen, libraryScreen, weightsScreen]) {
    placeWhenChromeMoves.observe(el, {
      attributes: true, attributeFilter: ["hidden"],
    });
  }
  const limitations = document.getElementById("limitations-panel");
  if (limitations) {
    placeWhenChromeMoves.observe(limitations, {
      attributes: true, attributeFilter: ["class"],
    });
    if (typeof ResizeObserver === "function") {
      new ResizeObserver(placeArchiveOpen).observe(limitations);
    }
  }
  api("/api/features").then(async features => {
    viewerOnly = Boolean(features.viewer_only);
    get("outdated-reprocess").hidden = viewerOnly;
    if (features.viewer_only) return;
    open.hidden = false;
    placeArchiveOpen();
    const cases = await api("/api/cases");
    if (!cases.length) await prepare();
    syncArchiveOpen();
    try {
      const library = await api("/api/library");
      libraryMeta = { count: library.cases.length, total_bytes: library.total_bytes };
      syncLibraryNavMeta();
    } catch (error) {
      libraryMeta = null;
    }
  }).catch(() => {});
  maybeOutdated();
})();
