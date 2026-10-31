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
  // 本地后端：请求经主进程代理（token 不暴露给页面）
  api: (path, method = 'GET', body) => ipcRenderer.invoke('api:request', { path, method, body }),
  getBackendState: () => ipcRenderer.invoke('backend:get-state'),
  onBackendState: (fn) => {
    const handler = (_event, state) => fn(state);
    ipcRenderer.on('backend:state', handler);
    return () => ipcRenderer.removeListener('backend:state', handler);
  },
});
