const { app, BrowserWindow, ipcMain, shell } = require('electron');
const path = require('node:path');
const fs = require('node:fs');
const net = require('node:net');
const crypto = require('node:crypto');
const { spawn } = require('node:child_process');

const ROOT = path.join(__dirname, '..');
const RENDERER = path.join(ROOT, 'design', 'index.html');
const BACKEND_ROOT = app.isPackaged
  ? path.join(process.resourcesPath, 'app.asar.unpacked')
  : ROOT;

const BASE_PORT = Number(process.env.SONGBOT_API_PORT || 8790);
const PORT_RANGE = 10;
const TOKEN = process.env.SONGBOT_API_TOKEN || crypto.randomBytes(16).toString('hex');

/** 后端状态：starting / online / offline；external 表示复用了已在运行的后端 */
const backend = {
  status: 'starting',
  port: null,
  external: false,
  token: TOKEN,
  error: '',
  child: null,
};

/** @type {BrowserWindow | null} */
let win = null;
let quitting = false;

function broadcastState() {
  if (!win || win.isDestroyed()) return;
  win.webContents.send('backend:state', publicState());
}

function publicState() {
  return {
    status: backend.status,
    port: backend.port,
    external: backend.external,
    error: backend.error,
  };
}

function setBackend(patch) {
  Object.assign(backend, patch);
  broadcastState();
}

/* ---------------------------------------------------------------- HTTP */

async function requestJson(port, urlPath, method = 'GET', body, token = '') {
  const headers = { 'Content-Type': 'application/json' };
  if (token) headers['X-SongBot-Token'] = token;
  const res = await fetch(`http://127.0.0.1:${port}${urlPath}`, {
    method,
    headers,
    body: body === undefined ? undefined : JSON.stringify(body),
    signal: AbortSignal.timeout(20000),
  });
  const text = await res.text();
  let data = null;
  try { data = text ? JSON.parse(text) : null; } catch { data = { raw: text }; }
  return { ok: res.ok, status: res.status, data };
}

async function probePort(port, timeout = 800) {
  try {
    const res = await fetch(`http://127.0.0.1:${port}/api/health`, {
      signal: AbortSignal.timeout(timeout),
    });
    if (!res.ok) return false;
    const data = await res.json();
    return Boolean(data && data.ok);
  } catch {
    return false;
  }
}

async function findRunningBackend() {
  for (let i = 0; i < PORT_RANGE; i += 1) {
    const port = BASE_PORT + i;
    if (await probePort(port)) return port;
  }
  return null;
}

function findFreePort(start) {
  return new Promise((resolve) => {
    let port = start;
    const tryPort = () => {
      if (port >= start + PORT_RANGE) { resolve(null); return; }
      const srv = net.createServer();
      srv.once('error', () => { port += 1; tryPort(); });
      srv.once('listening', () => {
        const p = srv.address().port;
        srv.close(() => resolve(p));
      });
      srv.listen(port, '127.0.0.1');
    };
    tryPort();
  });
}

/* ---------------------------------------------------------------- Python 后端 */

function pythonCommand() {
  if (process.env.SONGBOT_PYTHON) return { cmd: process.env.SONGBOT_PYTHON, args: [] };
  if (process.platform === 'win32') return { cmd: 'python', args: [] };
  return { cmd: 'python3', args: [] };
}

function logFile() {
  const dir = app.getPath('userData');
  try { fs.mkdirSync(dir, { recursive: true }); } catch { /* 忽略 */ }
  return path.join(dir, 'backend.log');
}

/**
 * 后端配置文件路径：开发时用项目根 config.json；
 * 打包后放在用户可写目录，首次启动从 config.example.json 播种。
 */
function backendConfigPath() {
  if (!app.isPackaged) return path.join(ROOT, 'config.json');
  const target = path.join(app.getPath('userData'), 'config.json');
  try {
    if (!fs.existsSync(target)) {
      const seed = path.join(BACKEND_ROOT, 'config.example.json');
      if (fs.existsSync(seed)) fs.copyFileSync(seed, target);
    }
  } catch (err) {
    return path.join(BACKEND_ROOT, 'config.json');
  }
  return fs.existsSync(target) ? target : path.join(BACKEND_ROOT, 'config.json');
}

function spawnBackend(port) {
  const py = pythonCommand();
  const out = fs.createWriteStream(logFile(), { flags: 'a' });
  const child = spawn(py.cmd, [...py.args, 'main.py'], {
    cwd: BACKEND_ROOT,
    env: {
      ...process.env,
      SONGBOT_API_PORT: String(port),
      SONGBOT_API_TOKEN: TOKEN,
      SONGBOT_CONFIG: backendConfigPath(),
      PYTHONUNBUFFERED: '1',
    },
    windowsHide: true,
    stdio: ['ignore', 'pipe', 'pipe'],
  });
  child.stdout.on('data', (chunk) => {
    out.write(chunk);
    process.stdout.write(chunk);
  });
  child.stderr.on('data', (chunk) => {
    out.write(chunk);
    process.stderr.write(chunk);
  });
  child.on('error', (err) => {
    setBackend({ status: 'offline', error: `无法启动 Python 后端: ${err.message}` });
  });
  child.on('exit', (code, signal) => {
    if (quitting) return;
    setBackend({
      status: 'offline',
      error: `后端进程已退出（code=${code ?? '?'} signal=${signal || '-'}）`,
      child: null,
    });
  });
  backend.child = child;
  return child;
}

async function waitForBackend(port, timeoutMs = 25000) {
  const started = Date.now();
  while (Date.now() - started < timeoutMs) {
    if (await probePort(port, 1200)) return true;
    // 端口占用时后端会自动顺延，按区间探测实际端口
    const found = await findRunningBackend();
    if (found) {
      backend.port = found;
      return true;
    }
    await new Promise((r) => setTimeout(r, 500));
  }
  return false;
}

async function startBackend() {
  const running = await findRunningBackend();
  if (running) {
    backend.status = 'online';
    backend.port = running;
    backend.external = true;
    backend.token = ''; // 外部后端未使用本进程注入的 token
    broadcastState();
    return;
  }

  if (app.isPackaged && !fs.existsSync(path.join(BACKEND_ROOT, 'main.py'))) {
    setBackend({ status: 'offline', error: '未找到后端文件（asar 解包失败）' });
    return;
  }

  const port = await findFreePort(BASE_PORT);
  if (!port) {
    setBackend({ status: 'offline', error: `本地端口 ${BASE_PORT}-${BASE_PORT + PORT_RANGE - 1} 不可用` });
    return;
  }

  backend.port = port;
  backend.external = false;
  backend.token = TOKEN;
  backend.status = 'starting';
  broadcastState();
  spawnBackend(port);

  const ok = await waitForBackend(port);
  if (ok) {
    setBackend({ status: 'online', port: backend.port });
  } else {
    setBackend({ status: 'offline', error: '后端启动超时，请查看 backend.log' });
  }
}

function killBackend() {
  const child = backend.child;
  if (!child || child.killed) return;
  try {
    if (process.platform === 'win32') {
      spawn('taskkill', ['/pid', String(child.pid), '/T', '/F'], { windowsHide: true });
    } else {
      child.kill('SIGTERM');
    }
  } catch { /* 忽略 */ }
}

/* ---------------------------------------------------------------- 窗口 */

function createWindow() {
  win = new BrowserWindow({
    width: 1360,
    height: 860,
    minWidth: 1040,
    minHeight: 680,
    frame: false,
    backgroundColor: '#202020',
    // Windows 11 用系统 Mica 材质；其他平台保持纯色，避免渲染异常
    ...(process.platform === 'win32' ? { backgroundMaterial: 'mica' } : {}),
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
    win.webContents.send('backend:state', publicState());
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

ipcMain.handle('backend:get-state', () => publicState());

/**
 * 渲染层通过主进程代理访问本地 API：避免 file:// 的 CORS 限制，
 * token 只存在于主进程，不下发到页面。
 */
ipcMain.handle('api:request', async (_event, payload) => {
  const { path: urlPath, method = 'GET', body } = payload || {};
  if (typeof urlPath !== 'string' || !urlPath.startsWith('/api/')) {
    return { ok: false, status: 400, data: { ok: false, error: '非法 API 路径' } };
  }
  if (backend.status !== 'online' || !backend.port) {
    return { ok: false, status: 503, data: { ok: false, error: '后端尚未就绪' } };
  }
  try {
    return await requestJson(backend.port, urlPath, method, body, backend.token);
  } catch (err) {
    return { ok: false, status: 599, data: { ok: false, error: String(err && err.message || err) } };
  }
});

app.whenReady().then(() => {
  createWindow();
  startBackend().catch((err) => {
    setBackend({ status: 'offline', error: String(err && err.message || err) });
  });
  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow();
  });
});

app.on('before-quit', () => {
  quitting = true;
  killBackend();
});

app.on('window-all-closed', () => {
  // Windows/Linux 直接退出；macOS 保留 dock 常驻
  if (process.platform !== 'darwin') app.quit();
});
