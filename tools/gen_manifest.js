// 生成 sources/manifest.json：每源加载状态 + 平台 + 官方归属（复用 load_one 并发校验）
const { spawn } = require('child_process');
const fs = require('fs');
const path = require('path');

const dir = process.argv[2] || 'sources';
const out = process.argv[3] || path.join(dir, 'manifest.json');
const concurrency = 8;
const files = fs.readdirSync(dir).filter((f) => f.endsWith('.js')).map((f) => path.join(dir, f));

function runOne(fp) {
  return new Promise((resolve) => {
    const p = spawn('node', [path.join(__dirname, 'load_one.js'), fp], { stdio: ['ignore', 'pipe', 'ignore'] });
    let s = '';
    p.stdout.on('data', (d) => (s += d));
    p.on('close', () => {
      try { resolve(JSON.parse(s.trim())); } catch { resolve({ file: path.basename(fp), ok: false, err: 'no json' }); }
    });
    setTimeout(() => { p.kill('SIGKILL'); resolve({ file: path.basename(fp), ok: false, err: 'timeout' }); }, 25000);
  });
}

// 解析头部元数据（与 source_bridge.js parseScriptMeta 对齐）
function metaOf(raw) {
  const m = /^\/\*[\S\s]+?\*\//.exec(raw);
  const c = m ? m[0] : '';
  const get = (k) => { const mm = new RegExp(`@${k}\\s+(.+)`).exec(c); return mm ? mm[1].trim().slice(0, 56) : ''; };
  return { name: get('name'), description: get('description'), version: get('version'), author: get('author') };
}

const PDONE = /^(pdone-|NEW_|lx-玉宁熙)/;

(async () => {
  const results = [];
  let i = 0;
  const workers = Array.from({ length: concurrency }, async () => {
    while (i < files.length) {
      const idx = i++;
      const fp = files[idx];
      const raw = fs.readFileSync(fp, 'utf-8');
      const meta = metaOf(raw);
      const load = await runOne(fp);
      results[idx] = {
        file: path.basename(fp),
        lx_name: load.name || meta.name || path.basename(fp, '.js'),
        display_name: meta.name || '',
        description: meta.description || '',
        version: meta.version || '',
        author: meta.author || '',
        official_pdone: PDONE.test(path.basename(fp)),
        status: load.ok ? 'ok' : 'fail',
        error: load.ok ? '' : (load.err || ''),
        platforms: load.platforms || [],
      };
    }
  });
  await Promise.all(workers);
  const okCount = results.filter((r) => r.status === 'ok').length;
  const manifest = {
    generated_at: new Date().toISOString(),
    total: results.length,
    loaded: okCount,
    failed: results.length - okCount,
    note: 'LX 标准音源清单；official_pdone=true 的音源同时在 config.json 的 source_urls 写入官方 GitHub 链接，开机自动更新。',
    sources: results,
  };
  fs.writeFileSync(out, JSON.stringify(manifest, null, 2), 'utf-8');
  console.log(`manifest 已生成: ${out} (${okCount}/${results.length} 加载成功)`);
})();
