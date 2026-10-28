/**
 * main.js — Desktop shell for SCYTHE: GAME.
 *
 * Loads the self-contained player Hub from disk and nothing else. The page runs
 * sandboxed with no Node access. Every network request, navigation, popup, and
 * permission request is refused: the game is offline by construction.
 */

const { app, BrowserWindow, Menu, session } = require('electron');
const path = require('path');

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
