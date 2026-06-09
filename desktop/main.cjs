const { app, BrowserWindow, dialog, ipcMain } = require("electron");
const { spawn, spawnSync } = require("child_process");
const fs = require("fs");
const path = require("path");

const BACKEND_PORT = 8765;
const BACKEND_URL = `http://127.0.0.1:${BACKEND_PORT}`;
const HEALTH_URL = `${BACKEND_URL}/api/health`;

let mainWindow = null;
let backendProcess = null;
let quitting = false;

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
    backgroundColor: "#05060a",
    title: "Amadeus",
    webPreferences: {
      preload: path.join(__dirname, "preload.cjs"),
      contextIsolation: true,
      nodeIntegration: false,
    },
  });

  await waitForBackend();
  await mainWindow.loadURL(BACKEND_URL);
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
