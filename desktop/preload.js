const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('songbot', {
  platform: process.platform,
  minimize: () => ipcRenderer.invoke('window:minimize'),
  toggleMaximize: () => ipcRenderer.invoke('window:toggle-maximize'),
  close: () => ipcRenderer.invoke('window:close'),
  isMaximized: () => ipcRenderer.invoke('window:is-maximized'),
  onWindowState: (fn) => {
    const handler = (_event, state) => fn(state);
    ipcRenderer.on('window:state', handler);
    return () => ipcRenderer.removeListener('window:state', handler);
  },
});
