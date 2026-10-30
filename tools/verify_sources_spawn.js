// 并发池：每个源用独立子进程加载（与 source_bridge.js 每源一进程同构），汇总结果
const { spawn } = require('child_process');
const fs = require('fs');
const path = require('path');

const dir = process.argv[2] || 'sources';
const concurrency = parseInt(process.argv[3] || '8', 10);
const files = fs.readdirSync(dir).filter((f) => f.endsWith('.js')).map((f) => path.join(dir, f));

function runOne(fp) {
  return new Promise((resolve) => {
    const p = spawn('node', [path.join(__dirname, 'load_one.js'), fp], { stdio: ['ignore', 'pipe', 'ignore'] });
    let out = '';
    p.stdout.on('data', (d) => (out += d));
    p.on('close', () => {
      try { resolve(JSON.parse(out.trim())); }
      catch { resolve({ file: path.basename(fp), name: path.basename(fp), ok: false, err: 'no json output' }); }
    });
    setTimeout(() => { p.kill('SIGKILL'); resolve({ file: path.basename(fp), name: path.basename(fp), ok: false, err: 'spawn timeout' }); }, 25000);
  });
}

(async () => {
  const results = [];
  let i = 0;
  const workers = Array.from({ length: concurrency }, async () => {
    while (i < files.length) {
      const idx = i++;
      results[idx] = await runOne(files[idx]);
    }
  });
  await Promise.all(workers);
  let ok = 0, fail = 0;
  for (const r of results) {
    if (r.ok) { ok++; console.log(`OK   ${r.name}  [${r.platforms.join(',')}]`); }
    else { fail++; console.log(`FAIL ${r.name}  (${r.file})  -> ${r.err}`); }
  }
  console.log(`\n总计: ${ok} 成功 / ${fail} 失败 / ${files.length} 文件`);
  process.exit(fail > 0 ? 1 : 0);
})();
