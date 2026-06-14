const { app, BrowserWindow, desktopCapturer, dialog, ipcMain, screen } = require("electron");
const { spawn, spawnSync } = require("child_process");
const fs = require("fs");
const path = require("path");

const BACKEND_PORT = 8765;
const BACKEND_URL = `http://127.0.0.1:${BACKEND_PORT}`;
const HEALTH_URL = `${BACKEND_URL}/api/health`;

let mainWindow = null;
let assistantWindow = null;
let backendProcess = null;
let quitting = false;
let normalWindowState = null;

function resolveBackendPath() {
  const candidates = [
    path.join(process.resourcesPath, "backend", "amadeus-backend", "amadeus-backend.exe"),
    path.join(__dirname, "..", "release", "backend", "amadeus-backend", "amadeus-backend.exe"),
    path.join(__dirname, "..", "dist-backend", "amadeus-backend", "amadeus-backend.exe"),
  ];
  const found = candidates.find((candidate) => fs.existsSync(candidate));
  if (!found) {
    throw new Error(`Backend executable not found. Checked: ${candidates.join(", ")}`);
  }
  return found;
}

function ensureDir(dir) {
  fs.mkdirSync(dir, { recursive: true });
  return dir;
}

function startBackend() {
  const backendPath = resolveBackendPath();
  const userData = app.getPath("userData");
  const dataDir = ensureDir(path.join(userData, "data"));
  const runtimeDir = ensureDir(path.join(userData, "runtime"));
  ensureDir(path.join(userData, "generated_docs"));
  ensureDir(path.join(userData, "agent_uploads"));
  ensureDir(path.join(userData, "agent_state"));
  ensureDir(path.join(userData, "user_voice"));

  backendProcess = spawn(backendPath, [], {
    cwd: userData,
    windowsHide: true,
    env: {
      ...process.env,
      AMADEUS_DESKTOP_HOST: process.env.AMADEUS_DESKTOP_HOST || "0.0.0.0",
      AMADEUS_DESKTOP_PORT: String(BACKEND_PORT),
      AMADEUS_WORKSPACE_PATH: userData,
      AMADEUS_SQLITE_PATH: path.join(dataDir, "amadeus_web.sqlite3"),
      AMADEUS_DOC_WRITER_OUTPUT_DIR: "generated_docs",
      AMADEUS_UPLOAD_DIR: "agent_uploads",
      AMADEUS_TODO_TASK_STORE: "agent_state/todo_tasks.json",
      AMADEUS_AUDIO_DIR: path.join(runtimeDir, "audio"),
      AMADEUS_USER_VOICE_DIR: "user_voice",
    },
  });

  backendProcess.on("exit", (code) => {
    if (!quitting && mainWindow) {
      dialog.showErrorBox("Amadeus 后端已退出", `后端进程已退出，退出码：${code}`);
    }
  });
}

async function waitForBackend() {
  const deadline = Date.now() + 30000;
  let lastError = "";
  while (Date.now() < deadline) {
    try {
      const response = await fetch(HEALTH_URL);
      if (response.ok) {
        return;
      }
      lastError = `HTTP ${response.status}`;
    } catch (error) {
      lastError = error instanceof Error ? error.message : String(error);
    }
    await new Promise((resolve) => setTimeout(resolve, 500));
  }
  throw new Error(`Backend did not become ready: ${lastError}`);
}

async function createWindow() {
  mainWindow = new BrowserWindow({
    width: 1280,
    height: 820,
    minWidth: 960,
    minHeight: 640,
    backgroundColor: "#00000000",
    title: "Amadeus",
    frame: false,
    transparent: true,
    webPreferences: {
      preload: path.join(__dirname, "preload.cjs"),
      contextIsolation: true,
      nodeIntegration: false,
    },
  });

  await waitForBackend();
  await mainWindow.loadURL(BACKEND_URL);
}

async function createAssistantWindow() {
  if (assistantWindow && !assistantWindow.isDestroyed()) {
    assistantWindow.focus();
    return assistantWindow;
  }
  const anchorBounds = mainWindow?.getBounds() || screen.getPrimaryDisplay().workArea;
  const display = screen.getDisplayMatching(anchorBounds);
  const width = 460;
  const height = 680;
  const bounds = {
    width,
    height,
    x: Math.round(display.workArea.x + display.workArea.width - width - 36),
    y: Math.round(display.workArea.y + display.workArea.height - height - 36),
  };
  assistantWindow = new BrowserWindow({
    ...bounds,
    minWidth: 320,
    minHeight: 420,
    resizable: false,
    movable: true,
    frame: false,
    transparent: true,
    backgroundColor: "#00000000",
    titleBarStyle: "hidden",
    hasShadow: false,
    thickFrame: false,
    skipTaskbar: true,
    alwaysOnTop: true,
    webPreferences: {
      preload: path.join(__dirname, "preload.cjs"),
      contextIsolation: true,
      nodeIntegration: false,
    },
  });
  assistantWindow.setAlwaysOnTop(true, "floating");
  assistantWindow.setVisibleOnAllWorkspaces(true, { visibleOnFullScreen: true });
  assistantWindow.on("closed", () => {
    assistantWindow = null;
    if (!quitting && mainWindow && !mainWindow.isDestroyed()) {
      mainWindow.show();
      mainWindow.focus();
    }
  });
  await assistantWindow.loadURL(`${BACKEND_URL}/?desktopAssistant=1`);
  return assistantWindow;
}

function setDesktopAssistantMode(enabled) {
  if (!mainWindow) {
    return { ok: false, reason: "Window is not ready." };
  }

  if (enabled) {
    if (!normalWindowState) {
      normalWindowState = {
        bounds: mainWindow.getBounds(),
        minimumSize: mainWindow.getMinimumSize(),
        resizable: mainWindow.isResizable(),
        movable: mainWindow.isMovable(),
        alwaysOnTop: mainWindow.isAlwaysOnTop(),
      };
    }
    createAssistantWindow()
      .then(() => {
        if (mainWindow && !mainWindow.isDestroyed()) {
          mainWindow.hide();
        }
      })
      .catch((error) => {
        dialog.showErrorBox("桌面助手启动失败", error instanceof Error ? error.message : String(error));
      });
    return { ok: true, externalWindow: true };
  }

  const state = normalWindowState;
  normalWindowState = null;
  if (assistantWindow && !assistantWindow.isDestroyed()) {
    const windowToClose = assistantWindow;
    assistantWindow = null;
    windowToClose.close();
  }
  mainWindow.setAlwaysOnTop(Boolean(state?.alwaysOnTop));
  mainWindow.setResizable(state?.resizable ?? true);
  mainWindow.setMovable(state?.movable ?? true);
  mainWindow.setMinimumSize(...(state?.minimumSize ?? [960, 640]));
  mainWindow.setBackgroundColor("#05060a");
  if (state?.bounds) {
    mainWindow.setBounds(state.bounds, true);
  }
  mainWindow.show();
  mainWindow.focus();
  return { ok: true, bounds: mainWindow.getBounds() };
}

function moveAssistantWindow(delta, sourceWindow) {
  const targetWindow = sourceWindow || assistantWindow || mainWindow;
  if (!targetWindow || targetWindow.isDestroyed()) {
    return { ok: false, reason: "Window is not ready." };
  }
  const dx = Number(delta?.dx || 0);
  const dy = Number(delta?.dy || 0);
  if (!Number.isFinite(dx) || !Number.isFinite(dy)) {
    return { ok: false, reason: "Invalid window delta." };
  }
  const bounds = targetWindow.getBounds();
  const nextBounds = {
    ...bounds,
    x: Math.round(bounds.x + dx),
    y: Math.round(bounds.y + dy),
  };
  targetWindow.setBounds(nextBounds, false);
  return { ok: true, bounds: nextBounds };
}

async function getCameraAvailability() {
  return {
    available: true,
    reason: "",
    source: "electron",
  };
}

function runWindowCommand(command, sourceWindow) {
  const targetWindow = sourceWindow || mainWindow;
  if (!targetWindow || targetWindow.isDestroyed()) {
    return { ok: false, reason: "Window is not ready." };
  }
  if (command === "minimize") {
    targetWindow.minimize();
    return { ok: true };
  }
  if (command === "toggle-maximize") {
    if (targetWindow.isMaximized()) {
      targetWindow.unmaximize();
    } else {
      targetWindow.maximize();
    }
    return { ok: true, maximized: targetWindow.isMaximized() };
  }
  if (command === "close") {
    targetWindow.close();
    return { ok: true };
  }
  return { ok: false, reason: "Unknown window command." };
}

function stopBackend() {
  quitting = true;
  if (!backendProcess || backendProcess.killed) {
    return;
  }
  if (process.platform === "win32") {
    const result = spawnSync("taskkill", ["/PID", String(backendProcess.pid), "/T", "/F"], {
      windowsHide: true,
      stdio: "ignore",
    });
    if (result.error) {
      backendProcess.kill();
    }
    return;
  }
  backendProcess.kill();
}

async function capturePrimaryScreen() {
  const display = screen.getPrimaryDisplay();
  const scaleFactor = display.scaleFactor || 1;
  const thumbnailSize = {
    width: Math.min(Math.round(display.size.width * scaleFactor), 1920),
    height: Math.min(Math.round(display.size.height * scaleFactor), 1080),
  };
  const sources = await desktopCapturer.getSources({
    types: ["screen"],
    thumbnailSize,
  });
  if (!sources.length) {
    throw new Error("No screen source available.");
  }
  const displayId = String(display.id);
  const source = sources.find((item) => String(item.display_id || "") === displayId) || sources[0];
  if (source.thumbnail.isEmpty()) {
    throw new Error("Screen thumbnail is empty.");
  }
  return {
    dataUrl: source.thumbnail.toDataURL(),
    filename: `screen-${Date.now()}.png`,
  };
}

app.whenReady().then(async () => {
  try {
    ipcMain.handle("amadeus:select-folder", async () => {
      const result = await dialog.showOpenDialog(mainWindow || undefined, {
        title: "选择 OpenCode 工作目录",
        properties: ["openDirectory", "createDirectory"],
      });
      if (result.canceled || result.filePaths.length === 0) {
        return null;
      }
      return result.filePaths[0];
    });
    ipcMain.handle("amadeus:capture-screen", async () => capturePrimaryScreen());
    ipcMain.handle("amadeus:set-desktop-assistant-mode", async (_event, enabled) => setDesktopAssistantMode(Boolean(enabled)));
    ipcMain.handle("amadeus:move-assistant-window", async (event, delta) => moveAssistantWindow(delta, BrowserWindow.fromWebContents(event.sender)));
    ipcMain.handle("amadeus:get-camera-availability", async () => getCameraAvailability());
    ipcMain.handle("amadeus:window-command", async (event, command) => runWindowCommand(command, BrowserWindow.fromWebContents(event.sender)));
    startBackend();
    await createWindow();
  } catch (error) {
    dialog.showErrorBox("Amadeus 启动失败", error instanceof Error ? error.message : String(error));
    app.quit();
  }
});

app.on("activate", () => {
  if (BrowserWindow.getAllWindows().length === 0) {
    createWindow().catch((error) => {
      dialog.showErrorBox("Amadeus 启动失败", error instanceof Error ? error.message : String(error));
    });
  }
});

app.on("before-quit", stopBackend);

app.on("window-all-closed", () => {
  if (process.platform !== "darwin") {
    app.quit();
  }
});
