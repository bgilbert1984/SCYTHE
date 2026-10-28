/**
 * preload.js — The only bridge between the sandboxed page and the main process.
 *
 * Three calls, each taking and returning plain data. The page can ask whether a storefront
 * is present, hand over a save it wrote, and ask what to do at start-up. It cannot reach
 * Node, Electron, Steam, or the filesystem.
 */

const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('scythePlatform', {
  status: () => ipcRenderer.invoke('platform:status'),
  saved: (text) => ipcRenderer.invoke('platform:saved', String(text)),
  start: (localText) => ipcRenderer.invoke('platform:start', localText === null ? null : String(localText)),
});
