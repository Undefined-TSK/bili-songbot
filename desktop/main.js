const { app, BrowserWindow, ipcMain, shell } = require('electron');
const path = require('node:path');

const RENDERER = path.join(__dirname, '..', 'design', 'index.html');

/** @type {BrowserWindow | null} */
let win = null;

function createWindow() {
  win = new BrowserWindow({
    width: 1360,
    height: 860,
    minWidth: 1040,
    minHeight: 680,
    frame: false,
    backgroundColor: '#06080C',
    show: false,
    autoHideMenuBar: true,
    title: 'SongBot 点歌台',
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
      spellcheck: false,
    },
  });

  win.loadFile(RENDERER);
  win.once('ready-to-show', () => {
    win.show();
    win.webContents.send('window:state', { maximized: win.isMaximized() });
  });

  const syncState = () => {
    if (!win.isDestroyed()) {
      win.webContents.send('window:state', { maximized: win.isMaximized() });
    }
  };
  win.on('maximize', syncState);
  win.on('unmaximize', syncState);

  // 外链一律交给系统浏览器，不在应用内开新窗口
  win.webContents.setWindowOpenHandler(({ url }) => {
    if (/^https?:/.test(url)) shell.openExternal(url);
    return { action: 'deny' };
  });

  win.on('closed', () => {
    win = null;
  });
}

ipcMain.handle('window:minimize', () => win?.minimize());
ipcMain.handle('window:toggle-maximize', () => {
  if (!win) return false;
  if (win.isMaximized()) win.unmaximize();
  else win.maximize();
  return win.isMaximized();
});
ipcMain.handle('window:close', () => win?.close());
ipcMain.handle('window:is-maximized', () => Boolean(win?.isMaximized()));

app.whenReady().then(() => {
  createWindow();
  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow();
  });
});

app.on('window-all-closed', () => {
  // Windows/Linux 直接退出；macOS 保留 dock 常驻
  if (process.platform !== 'darwin') app.quit();
});
