/**
 * main.js — Desktop shell for SCYTHE: GAME.
 *
 * Loads the self-contained player Hub from disk and nothing else. The page runs
 * sandboxed with no Node access. Every network request, navigation, popup, and
 * permission request is refused: the game is offline by construction.
 */

const { app, BrowserWindow, Menu, session, ipcMain } = require('electron');
const fs = require('fs');
const path = require('path');

// Packaged, the game code sits in ./game; from the repository it is the parent directory.
const GAME = fs.existsSync(path.join(__dirname, 'game')) ? path.join(__dirname, 'game') : path.resolve(__dirname, '..');
const { createPlatform, NO_PROVIDER } = require(path.join(GAME, 'platform/service'));
const { DEFINITIONS } = require(path.join(GAME, 'platform/achievements'));
const caseData = require(path.join(GAME, 'cases/signal_ghost/case.json'));
const steam = require('./steam');

const PAGE = path.join(__dirname, 'index.html');
const PAGE_URL = require('url').pathToFileURL(PAGE).href;
const SMOKE = process.env.SIGNAL_GHOST_SMOKE === '1';

// Two instances would share one profile and corrupt saved investigations.
if (!app.requestSingleInstanceLock()) {
  app.quit();
} else {
  app.on('second-instance', () => {
    const [win] = BrowserWindow.getAllWindows();
    if (win) {
      if (win.isMinimized()) win.restore();
      win.focus();
    }
  });

  app.whenReady().then(async () => {
    Menu.setApplicationMenu(null);
    lockDown(session.defaultSession);

    const platform = connectPlatform();
    registerPlatform(platform);

    const win = new BrowserWindow({
      width: 1280,
      height: 860,
      minWidth: 800,
      minHeight: 600,
      title: 'SIGNAL GHOST',
      backgroundColor: '#14161a',
      show: !SMOKE,
      webPreferences: {
        contextIsolation: true,
        sandbox: true,
        nodeIntegration: false,
        webSecurity: true,
        devTools: !app.isPackaged,
        spellcheck: false,
        preload: path.join(__dirname, 'preload.js'),
      },
    });

    win.webContents.setWindowOpenHandler(() => ({ action: 'deny' }));
    win.webContents.on('will-navigate', (event, url) => {
      if (url !== PAGE_URL) event.preventDefault();
    });

    await win.loadFile(PAGE);
    if (SMOKE) await require('./smoke').run(win, session.defaultSession);
  });

  app.on('window-all-closed', () => app.quit());
}

/** Refuse everything except the local page, data: and blob: (used for save-file export). */
function lockDown(ses) {
  ses.webRequest.onBeforeRequest((details, callback) => {
    callback({ cancel: !/^(file|data|blob|devtools):/.test(details.url) });
  });
  ses.setPermissionRequestHandler((_wc, _permission, callback) => callback(false));
  ses.setPermissionCheckHandler(() => false);
}

/** Steam if configured and running; otherwise the game plays exactly as it does offline. */
function connectPlatform() {
  let appId = Number(process.env.SIGNAL_GHOST_STEAM_APPID);
  if (!appId) {
    try {
      appId = require('./steam.config.json').appId;
    } catch (e) {
      appId = 0;
    }
  }
  const { provider, reason } = steam.connect(appId, DEFINITIONS.map((d) => d.id));
  if (!provider) process.stderr.write(`Steam features off: ${reason}\n`);
  return createPlatform(provider || NO_PROVIDER, caseData);
}

/** The page may send save text and get plain data back; nothing else crosses. */
function registerPlatform(platform) {
  const only = (fn) => (e, ...args) => {
    if (e.senderFrame.url !== PAGE_URL) throw new Error('refused: not the game page');
    return fn(...args);
  };
  ipcMain.handle('platform:status', only(() => ({ name: platform.name, available: platform.available })));
  ipcMain.handle('platform:saved', only((text) => platform.onSave(text)));
  ipcMain.handle('platform:start', only((text) => platform.onStart(text)));
}
