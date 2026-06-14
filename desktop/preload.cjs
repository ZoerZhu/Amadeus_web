const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("amadeusDesktop", {
  selectFolder: () => ipcRenderer.invoke("amadeus:select-folder"),
  captureScreen: () => ipcRenderer.invoke("amadeus:capture-screen"),
  setDesktopAssistantMode: (enabled) => ipcRenderer.invoke("amadeus:set-desktop-assistant-mode", Boolean(enabled)),
  moveAssistantWindow: (delta) => ipcRenderer.invoke("amadeus:move-assistant-window", delta),
  setAssistantMousePassthrough: (ignore) => ipcRenderer.invoke("amadeus:set-assistant-mouse-passthrough", Boolean(ignore)),
  onDesktopAssistantModeChanged: (callback) => {
    if (typeof callback !== "function") {
      return () => undefined;
    }
    const listener = (_event, enabled) => callback(Boolean(enabled));
    ipcRenderer.on("amadeus:desktop-assistant-mode-changed", listener);
    return () => ipcRenderer.removeListener("amadeus:desktop-assistant-mode-changed", listener);
  },
  getCameraAvailability: () => ipcRenderer.invoke("amadeus:get-camera-availability"),
  windowCommand: (command) => ipcRenderer.invoke("amadeus:window-command", command),
});

window.addEventListener("DOMContentLoaded", () => {
  document.documentElement.dataset.desktop = "true";
});
