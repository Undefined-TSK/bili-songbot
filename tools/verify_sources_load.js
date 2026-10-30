// 与 source_bridge.js 同构的加载校验器（并行加载 + 单源隔离 + 硬超时）
const fs = require('fs');
const path = require('path');

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
    utils: { crypto: {}, buffer: {}, zlib: {} },
  };
  return { lx, getHandler: () => handler, initedPromise };
}

function parseMeta(raw) {
  const m = /^\/\*[\S\s]+?\*\//.exec(raw);
  const c = m ? m[0] : '';
  const get = (k) => { const mm = new RegExp(`@${k}\\s+(.+)`).exec(c); return mm ? mm[1].trim() : ''; };
  return { name: get('name') || 'Unknown', version: get('version') || '0.0.0' };
}

async function loadFile(fp) {
  const raw = fs.readFileSync(fp, 'utf-8');
  const meta = parseMeta(raw);
  const { lx, getHandler, initedPromise } = makeLx(meta);
  globalThis.lx = lx;
  const scriptDir = path.dirname(fp);
  const fn = new Function('require', 'module', 'exports', '__dirname', '__filename', 'lx', raw);
  try { fn(require, module, exports, scriptDir, fp, lx); }
  catch (e) { return { file: path.basename(fp), name: meta.name, ok: false, err: 'exec: ' + e.message }; }
  const timeout = new Promise((_, rej) => setTimeout(() => rej(new Error('init timeout')), 20000));
  try {
    const sources = await Promise.race([initedPromise, timeout]);
    const handler = getHandler();
    if (!handler) return { file: path.basename(fp), name: meta.name, ok: false, err: 'no request handler' };
    return { file: path.basename(fp), name: meta.name, ok: true, platforms: Object.keys(sources || {}) };
  } catch (e) { return { file: path.basename(fp), name: meta.name, ok: false, err: e.message }; }
}

(async () => {
  const dir = process.argv[2] || 'sources';
  const files = fs.readdirSync(dir).filter((f) => f.endsWith('.js')).map((f) => path.join(dir, f));
  const results = await Promise.all(files.map((f) => loadFile(f)));
  let ok = 0, fail = 0;
  for (const r of results) {
    if (r.ok) { ok++; console.log(`OK   ${r.name}  [${r.platforms.join(',')}]`); }
    else { fail++; console.log(`FAIL ${r.name}  (${r.file})  -> ${r.err}`); }
  }
  console.log(`\n总计: ${ok} 成功 / ${fail} 失败 / ${files.length} 文件`);
  process.exit(fail > 0 ? 1 : 0);
})();
