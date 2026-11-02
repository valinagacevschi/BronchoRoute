// Changes: expose Record start, frame, stop, and cancel.
// Changes: expose saveImage for a native PNG still dialog.
// Changes: expose narrow Archive selection IPC and dropped-file paths.
// Changes: expose getPathForFile for a future Archive drop handler.
"use strict";

const { contextBridge, ipcRenderer, webUtils } = require("electron");

contextBridge.exposeInMainWorld("bronchoroute", {
  openArchive() {
    return ipcRenderer.invoke("bronchoroute:open-archive");
  },
  saveImage(payload) {
    return ipcRenderer.invoke("bronchoroute:save-image", payload);
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
    const listener = (_event, archivePath) => callback(archivePath);
    ipcRenderer.on("bronchoroute:archive", listener);
    return () => ipcRenderer.removeListener("bronchoroute:archive", listener);
  },
  getPathForFile(file) {
    return webUtils.getPathForFile(file);
  },
});
