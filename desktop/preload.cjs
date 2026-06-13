const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("amadeusDesktop", {
  selectFolder: () => ipcRenderer.invoke("amadeus:select-folder"),
  captureScreen: () => ipcRenderer.invoke("amadeus:capture-screen"),
});

window.addEventListener("DOMContentLoaded", () => {
  document.documentElement.dataset.desktop = "true";
});
