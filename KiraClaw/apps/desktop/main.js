const { app, BrowserWindow, ipcMain } = require("electron");

const {
  APP_ROOT,
  CONFIG_DIR,
  CONFIG_FILE,
  DAEMON_BIN,
  DAEMON_URL,
  DESKTOP_ROOT,
  IS_PACKAGED,
  RUNTIME_ENV_DIR,
  RUNTIME_STATE_FILE,
} = require("./lib/constants");
const { createConfigStore } = require("./lib/config-store");
const { createDaemonController } = require("./lib/daemon-controller");
const { createMainWindow } = require("./lib/create-window");
const { registerIpcHandlers } = require("./lib/register-ipc");
const { setupAutoUpdater } = require("./lib/updater");

const configStore = createConfigStore({
  configDir: CONFIG_DIR,
  configFile: CONFIG_FILE,
});

app.setName("KiraClaw");

let mainWindow = null;
let updaterState = null;

const daemonController = createDaemonController({
  appRoot: APP_ROOT,
  configFile: CONFIG_FILE,
  daemonBin: DAEMON_BIN,
  daemonUrl: DAEMON_URL,
  runtimeEnvDir: RUNTIME_ENV_DIR,
  runtimeStateFile: RUNTIME_STATE_FILE,
  isPackaged: IS_PACKAGED,
  onLog(payload) {
    if (mainWindow && !mainWindow.isDestroyed()) {
      mainWindow.webContents.send("daemon-log", payload);
    }
  },
});

function openMainWindow() {
  mainWindow = createMainWindow({
    desktopRoot: DESKTOP_ROOT,
  });
  mainWindow.on("closed", () => {
    mainWindow = null;
  });
}

app.whenReady().then(() => {
  registerIpcHandlers({
    app,
    ipcMain,
    configStore,
    daemonController,
    getUpdaterState: () => updaterState,
  });
  openMainWindow();
  updaterState = setupAutoUpdater();

  app.on("activate", () => {
    if (BrowserWindow.getAllWindows().length === 0) {
      openMainWindow();
    }
  });
});

app.on("before-quit", async () => {
  if (updaterState && updaterState.isInstallingUpdate()) {
    return;
  }
  await daemonController.shutdownBeforeQuit();
});

app.on("window-all-closed", () => {
  if (process.platform !== "darwin") {
    app.quit();
  }
});
