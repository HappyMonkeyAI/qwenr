import { app, BrowserWindow, Menu, Tray, dialog, ipcMain, nativeImage } from "electron";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { loadOperatorView, readRouterApiKey, saveOperatorSettings } from "./config-store.js";
import { RouterProcess } from "./router-process.js";

const desktopDir = path.dirname(fileURLToPath(import.meta.url));
const root = path.resolve(desktopDir, "..", "..");
const prefsPath = path.join(app.getPath("userData"), "qwenr-tray.json");
const router = new RouterProcess(root);

let tray;
let windowRef;
let openAtLogin = false;

function icon() {
  const size = 16;
  const buffer = Buffer.alloc(size * size * 4);
  for (let y = 0; y < size; y += 1) {
    for (let x = 0; x < size; x += 1) {
      const offset = (y * size + x) * 4;
      const inside = x > 1 && x < 14 && y > 1 && y < 14;
      buffer[offset] = 72;
      buffer[offset + 1] = 168;
      buffer[offset + 2] = 112;
      buffer[offset + 3] = inside ? 255 : 0;
    }
  }
  return nativeImage.createFromBitmap(buffer, { width: size, height: size });
}

function readPrefs() {
  try {
    return JSON.parse(fs.readFileSync(prefsPath, "utf8"));
  } catch {
    return {};
  }
}

function writePrefs(patch) {
  const next = { ...readPrefs(), ...patch };
  fs.mkdirSync(path.dirname(prefsPath), { recursive: true });
  fs.writeFileSync(prefsPath, JSON.stringify(next, null, 2));
  return next;
}

function probeHost(settings) {
  return settings.listenHost === "0.0.0.0" ? "127.0.0.1" : settings.listenHost;
}

async function liveStatus() {
  const settings = loadOperatorView(root);
  const base = `http://${probeHost(settings)}:${settings.listenPort}`;
  const key = readRouterApiKey(root);
  const headers = key ? { authorization: `Bearer ${key}` } : {};
  try {
    const healthResponse = await fetch(`${base}/health`);
    const health = await healthResponse.json();
    const statusResponse = await fetch(`${base}/status`, { headers });
    if (!statusResponse.ok) return { health, error: `status ${statusResponse.status}` };
    const status = await statusResponse.json();
    return { health, status };
  } catch (error) {
    return { error: error.message };
  }
}

async function snapshot() {
  const settings = loadOperatorView(root);
  const live = await liveStatus();
  return {
    settings,
    openAtLogin,
    router: router.owned ? "owned" : live.health ? "external" : "stopped",
    live,
    lastError: router.lastError,
  };
}

function showWindow() {
  if (windowRef && !windowRef.isDestroyed()) {
    windowRef.show();
    windowRef.focus();
    return;
  }
  windowRef = new BrowserWindow({
    width: 460,
    height: 560,
    show: false,
    autoHideMenuBar: true,
    backgroundColor: "#14181c",
    webPreferences: {
      preload: path.join(desktopDir, "preload.cjs"),
      contextIsolation: true,
      nodeIntegration: false,
    },
  });
  windowRef.once("ready-to-show", () => windowRef.show());
  windowRef.on("close", (event) => {
    if (!app.isQuitting) {
      event.preventDefault();
      windowRef.hide();
    }
  });
  windowRef.loadFile(path.join(desktopDir, "settings.html"));
}

function rebuildMenu() {
  const menu = Menu.buildFromTemplate([
    { label: "Settings", click: showWindow },
    { label: "Start router", click: () => router.start() },
    { label: "Stop router", click: () => router.stop() },
    { label: "Restart router", click: () => { router.stop(); setTimeout(() => router.start(), 600); } },
    { type: "separator" },
    { label: "Quit", click: () => { app.isQuitting = true; router.stop(); app.quit(); } },
  ]);
  tray.setContextMenu(menu);
}

function trusted(event) {
  if (!windowRef || event.sender !== windowRef.webContents) throw new Error("untrusted renderer");
}

app.whenReady().then(async () => {
  const prefs = readPrefs();
  openAtLogin = Boolean(prefs.openAtLogin);
  app.setLoginItemSettings({ openAtLogin });
  tray = new Tray(icon());
  tray.setToolTip("qwenr");
  tray.on("double-click", showWindow);
  rebuildMenu();
  const live = await liveStatus();
  if (!live.health && prefs.autostartRouter !== false) router.start();
  showWindow();
});

ipcMain.handle("qwenr:get-state", () => snapshot());
ipcMain.handle("qwenr:start", () => router.start());
ipcMain.handle("qwenr:stop", () => router.stop());
ipcMain.handle("qwenr:restart", async () => {
  router.stop();
  await new Promise((resolve) => setTimeout(resolve, 700));
  return router.start();
});
ipcMain.handle("qwenr:login", (_event, enabled) => {
  openAtLogin = Boolean(enabled);
  app.setLoginItemSettings({ openAtLogin });
  writePrefs({ openAtLogin });
  return { openAtLogin };
});
ipcMain.handle("qwenr:save", async (event, patch) => {
  trusted(event);
  const current = loadOperatorView(root);
  const turningPaidOn = Boolean(patch.paygAllowPaid) && !current.paygAllowPaid;
  if (turningPaidOn) {
    const answer = await dialog.showMessageBox(windowRef, {
      type: "warning",
      buttons: ["Keep paid PAYG off", "Allow paid PAYG"],
      defaultId: 0,
      cancelId: 0,
      title: "Paid PAYG",
      message: "Allowing paid PAYG can spend money after free quota is gone.",
    });
    if (answer.response !== 1) patch = { ...patch, paygAllowPaid: false };
  }
  const host = String(patch.listenHost || "");
  if (host === "0.0.0.0") {
    const answer = await dialog.showMessageBox(windowRef, {
      type: "warning",
      buttons: ["Stay on localhost", "Bind all interfaces"],
      defaultId: 0,
      cancelId: 0,
      title: "Network",
      message: "Binding 0.0.0.0 exposes the router beyond this PC.",
      detail: "The router will refuse to start unless QWENR_API_KEY is set in .env; callers must then send 'Authorization: Bearer <key>'.",
    });
    if (answer.response !== 1) return { error: "Kept listen on localhost." };
    patch = { ...patch, allowLan: true };
  }
  try {
    saveOperatorSettings(root, patch);
    return { ok: true };
  } catch (error) {
    return { error: error.message };
  }
});
