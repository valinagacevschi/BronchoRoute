// Changes: expose getPathForFile for a future Archive drop handler.
"use strict";

const { contextBridge, webUtils } = require("electron");

contextBridge.exposeInMainWorld("bronchoroute", {
  getPathForFile(file) {
    return webUtils.getPathForFile(file);
  },
});
