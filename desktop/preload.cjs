const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("amadeusDesktop", {
  selectFolder: () => ipcRenderer.invoke("amadeus:select-folder"),
});

window.addEventListener("DOMContentLoaded", () => {
  document.documentElement.dataset.desktop = "true";
});
