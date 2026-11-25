// Changes: queue Archive IPC until import.js registers onOpenArchive (⌘O race).
// Changes: expose model-weight status, download, and cancel.
// Changes: expose Record start, frame, stop, and cancel.
// Changes: expose saveImage for a native PNG still dialog.
// Changes: expose narrow Archive selection IPC and dropped-file paths.
// Changes: expose getPathForFile for a future Archive drop handler.
"use strict";

const { contextBridge, ipcRenderer, webUtils } = require("electron");

// Open Archive can arrive (menu / ⌘O / smoke) before import.js calls onOpenArchive.
let pendingArchive = null;
let archiveListener = null;

ipcRenderer.on("bronchoroute:archive", (_event, archivePath) => {
  if (typeof archiveListener === "function") {
    archiveListener(archivePath);
    return;
  }
  pendingArchive = archivePath;
});

contextBridge.exposeInMainWorld("bronchoroute", {
  openArchive() {
    return ipcRenderer.invoke("bronchoroute:open-archive");
  },
  saveImage(payload) {
    return ipcRenderer.invoke("bronchoroute:save-image", payload);
  },
  saveReport(payload) {
    return ipcRenderer.invoke("bronchoroute:save-report", payload);
  },
  recordStart(payload) {
    return ipcRenderer.invoke("bronchoroute:record-start", payload);
  },
  recordFrame(payload) {
    return ipcRenderer.invoke("bronchoroute:record-frame", payload);
  },
  recordStop(payload) {
    return ipcRenderer.invoke("bronchoroute:record-stop", payload);
  },
  recordCancel(payload) {
    return ipcRenderer.invoke("bronchoroute:record-cancel", payload);
  },
  onOpenArchive(callback) {
    archiveListener = (archivePath) => callback(archivePath);
    if (pendingArchive !== null) {
      const queued = pendingArchive;
      pendingArchive = null;
      archiveListener(queued);
    }
    return () => {
      archiveListener = null;
    };
  },
  getPathForFile(file) {
    return webUtils.getPathForFile(file);
  },
  realismModelStatus: () => ipcRenderer.invoke("bronchoroute:realism-model-status"),
  realismModelSetKey: key => ipcRenderer.invoke("bronchoroute:realism-model-set-key", key),
  realismModelClearKey: () => ipcRenderer.invoke("bronchoroute:realism-model-clear-key"),
  realismModelDownload: () => ipcRenderer.invoke("bronchoroute:realism-model-download"),
  realismModelCancel: () => ipcRenderer.invoke("bronchoroute:realism-model-cancel"),
  realismModelRemove: () => ipcRenderer.invoke("bronchoroute:realism-model-remove"),
  onRealismModelProgress(callback) {
    const listener = (_event, progress) => callback(progress);
    ipcRenderer.on("bronchoroute:realism-model-progress", listener);
    return () => ipcRenderer.removeListener("bronchoroute:realism-model-progress", listener);
  },
  weightsRequired() {
    return ipcRenderer.invoke("bronchoroute:weights-required");
  },
  weightsStatus() {
    return ipcRenderer.invoke("bronchoroute:weights-status");
  },
  downloadWeights() {
    return ipcRenderer.invoke("bronchoroute:weights-download");
  },
  cancelWeights() {
    return ipcRenderer.invoke("bronchoroute:weights-cancel");
  },
  onWeightsProgress(callback) {
    const listener = (_event, progress) => callback(progress);
    ipcRenderer.on("bronchoroute:weights-progress", listener);
    return () => ipcRenderer.removeListener("bronchoroute:weights-progress", listener);
  },
});
