// Changes: expose narrow Archive selection IPC and dropped-file paths.
// Changes: expose getPathForFile for a future Archive drop handler.
"use strict";

const { contextBridge, ipcRenderer, webUtils } = require("electron");

contextBridge.exposeInMainWorld("bronchoroute", {
  openArchive() {
    return ipcRenderer.invoke("bronchoroute:open-archive");
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
