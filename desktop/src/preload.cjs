const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("qwenrTray", {
  getState: () => ipcRenderer.invoke("qwenr:get-state"),
  save: (patch) => ipcRenderer.invoke("qwenr:save", patch),
  start: () => ipcRenderer.invoke("qwenr:start"),
  stop: () => ipcRenderer.invoke("qwenr:stop"),
  restart: () => ipcRenderer.invoke("qwenr:restart"),
  setLogin: (openAtLogin) => ipcRenderer.invoke("qwenr:login", openAtLogin),
});
