// 单源加载器：在独立 Node 进程中加载一个 LX 源脚本并报告结果（与 source_bridge.js 每源一进程同构）
// lx.utils 与落雪桌面官方 preload.js 实现一致（Buffer 版），保证验证结论与生产内核等价
const fs = require('fs');
const path = require('path');
const crypto = require('crypto');

// 与 source_bridge.js 同构：捕获脚本异步 init 错误（混淆源网络不可达时会抛），单源隔离不崩溃
process.on('unhandledRejection', () => {});

// 与 source_bridge.js 同构：将脚本的 console.log 重定向到 stderr，避免污染 stdout 的 JSON 协议
console.log = (...args) => console.error('[script]', ...args);

function makeLx(meta) {
  let handler = null;
  let initedResolve = null;
  const initedPromise = new Promise((r) => (initedResolve = r));
  const lx = {
    EVENT_NAMES: { request: 'request', inited: 'inited', updateAlert: 'updateAlert' },
    version: '2.10.0',
    env: 'desktop',
    currentScriptInfo: meta,
    request(url, opts, cb) {
      const ms = opts.timeout && opts.timeout > 0 ? Math.min(opts.timeout, 60000) : 15000;
      const ctrl = new AbortController();
      const t = setTimeout(() => ctrl.abort(), ms);
      const fo = { method: (opts.method || 'GET').toUpperCase(), headers: opts.headers || {}, signal: ctrl.signal };
      if (opts.body) fo.body = opts.body;
      fetch(url, fo).then(async (res) => {
        clearTimeout(t);
        const txt = await res.text();
        let body; try { body = JSON.parse(txt); } catch { body = txt; }
        cb(null, { statusCode: res.status, body, raw: txt }, body);
      }).catch((err) => { clearTimeout(t); cb(err, null, null); });
      return () => { clearTimeout(t); ctrl.abort(); };
    },
    on(ev, h) { if (ev === 'request') handler = h; return Promise.resolve(); },
    send(ev, data) {
      if (ev === 'inited') {
        const sources = data && data.sources ? data.sources : (data || {});
        initedResolve(sources);
      }
      return Promise.resolve();
    },
    utils: {
      crypto: {
        aesEncrypt(buffer, mode, key, iv) {
          const cipher = crypto.createCipheriv(mode, key, iv);
          return Buffer.concat([cipher.update(buffer), cipher.final()]);
        },
        rsaEncrypt(buffer, key) {
          buffer = Buffer.concat([Buffer.alloc(128 - buffer.length), buffer]);
          return crypto.publicEncrypt({ key, padding: crypto.constants.RSA_NO_PADDING }, buffer);
        },
        randomBytes(size) { return crypto.randomBytes(size); },
        md5(str) { return crypto.createHash('md5').update(str).digest('hex'); },
      },
      buffer: {
        from(...args) { return Buffer.from(...args); },
        bufToString(buf, format) { return Buffer.from(buf, 'binary').toString(format); },
      },
      zlib: {
        inflate(buf) {
          return new Promise((resolve, reject) => {
            require('zlib').inflate(buf, (err, data) => (err ? reject(new Error(err.message)) : resolve(data)));
          });
        },
        deflate(data) {
          return new Promise((resolve, reject) => {
            require('zlib').deflate(data, (err, b) => (err ? reject(new Error(err.message)) : resolve(b)));
          });
        },
      },
    },
  };
  return { lx, getHandler: () => handler, initedPromise };
}

function parseMeta(raw) {
  const m = /^\/\*[\S\s]+?\*\//.exec(raw);
  const c = m ? m[0] : '';
  const get = (k) => { const mm = new RegExp(`@${k}\\s+(.+)`).exec(c); return mm ? mm[1].trim().slice(0, 56) : ''; };
  // 与 source_bridge.js parseScriptMeta 对齐：name/description/version/author/homepage + rawScript（官方规范）
  return {
    name: get('name') || 'Unknown',
    description: get('description'),
    version: get('version') || '0.0.0',
    author: get('author'),
    homepage: get('homepage'),
    rawScript: raw,
  };
}

(async () => {
  const fp = process.argv[2];
  const raw = fs.readFileSync(fp, 'utf-8');
  const meta = parseMeta(raw);
  const { lx, getHandler, initedPromise } = makeLx(meta);
  globalThis.lx = lx;
  const scriptDir = path.dirname(fp);
  let result;
  try {
    const fn = new Function('require', 'module', 'exports', '__dirname', '__filename', 'lx', raw);
    fn(require, module, exports, scriptDir, fp, lx);
  } catch (e) {
    result = { file: path.basename(fp), name: meta.name, ok: false, err: 'exec: ' + e.message };
    process.stdout.write(JSON.stringify(result) + '\n');
    process.exit(0);
  }
  // init 超时可经环境变量调整（默认 20s；混淆源解混淆+联网可能超 20s，验证时可用 LOAD_TIMEOUT_MS=90000）
  const initMs = parseInt(process.env.LOAD_TIMEOUT_MS || '20000', 10);
  const timeout = new Promise((_, rej) => setTimeout(() => rej(new Error(`init timeout (${initMs}ms)`)), initMs));
  try {
    const sources = await Promise.race([initedPromise, timeout]);
    const handler = getHandler();
    if (!handler) result = { file: path.basename(fp), name: meta.name, ok: false, err: 'no request handler' };
    else result = { file: path.basename(fp), name: meta.name, ok: true, platforms: Object.keys(sources || {}) };
  } catch (e) {
    result = { file: path.basename(fp), name: meta.name, ok: false, err: e.message };
  }
  process.stdout.write(JSON.stringify(result) + '\n');
  process.exit(0);
})();
