function setupAutoUpdater() {
  let autoUpdater;
  let log;
  let updateLifecycle = null;
  let pendingCheckPrompt = false;
  let promptingForDownload = false;
  let updateState = {
    supported: false,
    status: "unsupported",
    version: "",
    progress: 0,
    message: "",
  };

  try {
    ({ autoUpdater } = require("electron-updater"));
    log = require("electron-log");
  } catch {
    return;
  }

  const path = require("path");
  const { app, dialog, BrowserWindow } = require("electron");
  const {
    clearIncompatiblePendingUpdate,
    getAppBundlePath,
    isInApplicationsFolder,
    readUpdaterCacheDirName,
  } = require("./updater-helpers");
  if (!app.isPackaged) {
    return;
  }

  const updateConfigPath = path.join(process.resourcesPath, "app-update.yml");
  const fs = require("fs");
  if (!fs.existsSync(updateConfigPath)) {
    return;
  }
  const updaterCacheDir = path.join(
    app.getPath("cache"),
    readUpdaterCacheDirName(updateConfigPath, app.getName()),
  );

  const exePath = app.getPath("exe");
  const bundlePath = getAppBundlePath(exePath);
  log.info("Packaged app startup", {
    version: app.getVersion(),
    exePath,
    bundlePath,
    updateConfigPath,
    updaterCacheDir,
  });

  autoUpdater.logger = log;
  autoUpdater.logger.transports.file.level = "info";
  autoUpdater.logger.info(`Using packaged app-update.yml: ${updateConfigPath}`);
  updateState = {
    supported: true,
    status: "idle",
    version: app.getVersion(),
    progress: 0,
    message: "",
  };
  clearIncompatiblePendingUpdate(updaterCacheDir, app.getName(), autoUpdater.logger);
  autoUpdater.autoDownload = false;
  autoUpdater.autoInstallOnAppQuit = true;
  app.on("before-quit-for-update", () => {
    updateLifecycle = "installing";
    log.info("Auto-update: before-quit-for-update");
  });

  function setUpdateState(patch) {
    updateState = {
      ...updateState,
      ...patch,
    };
  }

  async function promptForDownload(info) {
    if (promptingForDownload) {
      return false;
    }

    promptingForDownload = true;
    try {
      const focusedWindow = BrowserWindow.getFocusedWindow() || BrowserWindow.getAllWindows()[0] || null;
      const result = await dialog.showMessageBox(focusedWindow, {
        type: "info",
        buttons: ["Download Now", "Later"],
        defaultId: 0,
        cancelId: 1,
        title: "Update Available",
        message: `Version ${info.version || updateState.version} is available.`,
        detail: "Download the latest KiraClaw update in the background?",
      });
      if (result.response !== 0) {
        return false;
      }

      setUpdateState({
        supported: true,
        status: "downloading",
        progress: 0,
        message: `Downloading version ${info.version || updateState.version}...`,
      });
      await autoUpdater.downloadUpdate();
      return true;
    } catch (error) {
      log.error("Auto-update available dialog failed:", error);
      setUpdateState({
        supported: true,
        status: "error",
        progress: 0,
        message: String(error?.message || error || "Update download failed."),
      });
      return false;
    } finally {
      promptingForDownload = false;
    }
  }

  if (!isInApplicationsFolder(bundlePath, app.getPath("home"))) {
    log.warn("Auto-update may be unreliable because the app is not running from Applications.", {
      bundlePath,
    });
    const focusedWindow = BrowserWindow.getFocusedWindow() || BrowserWindow.getAllWindows()[0] || null;
    void dialog.showMessageBox(focusedWindow, {
      type: "warning",
      buttons: ["OK"],
      defaultId: 0,
      title: "Install Location",
      message: "KiraClaw is not running from the Applications folder.",
      detail: "Automatic updates are most reliable when KiraClaw.app is installed in /Applications. If a newly downloaded app still shows an older version, make sure you are reopening the app from /Applications and not another copy in Downloads or a mounted DMG.",
    }).catch((error) => {
      log.error("Install location warning dialog failed:", error);
    });
  }

  autoUpdater.on("checking-for-update", () => {
    log.info("Auto-update: checking for update");
    setUpdateState({
      supported: true,
      status: "checking",
      version: updateState.version || app.getVersion(),
      progress: 0,
      message: "Checking for updates...",
    });
  });

  autoUpdater.on("update-available", (info) => {
    log.info("Auto-update: update available", info);
    setUpdateState({
      supported: true,
      status: "available",
      version: info.version || updateState.version,
      progress: 0,
      message: `Version ${info.version} is available.`,
    });
    if (pendingCheckPrompt) {
      pendingCheckPrompt = false;
      void promptForDownload(info);
    }
  });

  autoUpdater.on("update-not-available", (info) => {
    log.info("Auto-update: no update available", info);
    pendingCheckPrompt = false;
    setUpdateState({
      supported: true,
      status: "current",
      version: app.getVersion(),
      progress: 0,
      message: "KiraClaw is up to date.",
    });
  });

  autoUpdater.on("download-progress", (progress) => {
    log.info("Auto-update: download progress", progress);
    setUpdateState({
      supported: true,
      status: "downloading",
      progress: Number(progress?.percent || 0),
      message: `Downloading update… ${Math.round(Number(progress?.percent || 0))}%`,
    });
  });

  autoUpdater.on("update-downloaded", (info) => {
    log.info("Auto-update: update downloaded", info);
    setUpdateState({
      supported: true,
      status: "downloaded",
      version: info.version || updateState.version,
      progress: 100,
      message: `Version ${info.version} has been downloaded.`,
    });
  });

  autoUpdater.on("error", (error) => {
    log.error("Auto-update error:", error);
    pendingCheckPrompt = false;
    setUpdateState({
      supported: true,
      status: "error",
      progress: 0,
      message: String(error?.message || error || "Update failed."),
    });
  });

  pendingCheckPrompt = true;
  setUpdateState({
    supported: true,
    status: "checking",
    version: updateState.version || app.getVersion(),
    progress: 0,
    message: "Checking for updates...",
  });
  autoUpdater.checkForUpdates().catch((error) => {
    log.error("Auto-update check failed:", error);
    pendingCheckPrompt = false;
    setUpdateState({
      supported: true,
      status: "error",
      progress: 0,
      message: String(error?.message || error || "Update check failed."),
    });
  });

  return {
    getState() {
      return { ...updateState };
    },
    isInstallingUpdate() {
      return updateLifecycle === "installing";
    },
    async checkForUpdates() {
      try {
        pendingCheckPrompt = false;
        await autoUpdater.checkForUpdates();
        return { ...updateState };
      } catch (error) {
        log.error("Manual auto-update check failed:", error);
        pendingCheckPrompt = false;
        setUpdateState({
          supported: true,
          status: "error",
          progress: 0,
          message: String(error?.message || error || "Update check failed."),
        });
        return { ...updateState };
      }
    },
    async downloadUpdate() {
      if (updateState.status !== "available") {
        return { ...updateState };
      }
      try {
        await promptForDownload({ version: updateState.version });
        return { ...updateState };
      } catch (error) {
        log.error("Manual update download failed:", error);
        setUpdateState({
          supported: true,
          status: "error",
          progress: 0,
          message: String(error?.message || error || "Update download failed."),
        });
        return { ...updateState };
      }
    },
    async installUpdate() {
      if (updateState.status !== "downloaded") {
        return { ...updateState };
      }
      setImmediate(() => autoUpdater.quitAndInstall(false, true));
      return { ...updateState };
    },
  };
}

module.exports = {
  setupAutoUpdater,
};
