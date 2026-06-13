const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("amadeusDesktop", {
  selectFolder: () => ipcRenderer.invoke("amadeus:select-folder"),
  captureScreen: () => ipcRenderer.invoke("amadeus:capture-screen"),
  setDesktopAssistantMode: (enabled) => ipcRenderer.invoke("amadeus:set-desktop-assistant-mode", Boolean(enabled)),
  moveAssistantWindow: (delta) => ipcRenderer.invoke("amadeus:move-assistant-window", delta),
  getCameraAvailability: () => ipcRenderer.invoke("amadeus:get-camera-availability"),
  windowCommand: (command) => ipcRenderer.invoke("amadeus:window-command", command),
});

window.addEventListener("DOMContentLoaded", () => {
  document.documentElement.dataset.desktop = "true";
});
