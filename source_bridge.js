#!/usr/bin/env node
/**
 * source_bridge.js — lx-music 音源脚本桥接器
 *
 * 加载 .js 音源脚本，注入 lx 全局对象，通过 stdin/stdout JSON 协议与 Python 通信。
 *
 * 协议:
 *   Python → Node:  每行一个 JSON 请求 {"id":1,"action":"search","source":"wy","keyword":"...","limit":5}
 *   Node → Python:  每行一个 JSON 响应 {"id":1,"success":true,"data":[...]}
 *
 * 用法: node source_bridge.js <script_dir>
 */

const fs = require('fs');
const path = require('path');
const crypto = require('crypto');
const zlib = require('zlib');
const http = require('http');
const https = require('https');

// ======================== lx API 实现 ========================

function uint8ToBase64(buf) {
  return Buffer.from(buf).toString('base64');
}
function base64ToUint8(str) {
  return new Uint8Array(Buffer.from(str, 'base64'));
}
function hexToUint8(hex) {
  const buf = new Uint8Array(hex.length / 2);
  for (let i = 0; i < buf.length; i++) buf[i] = parseInt(hex.slice(i * 2, i * 2 + 2), 16);
  return buf;
}
function uint8ToHex(buf) {
  return Buffer.from(buf).toString('hex');
}
function toUint8(val) {
  if (val instanceof Uint8Array) return val;
  if (Array.isArray(val)) return new Uint8Array(val);
  if (typeof val === 'string') return new Uint8Array(Buffer.from(val, 'utf-8'));
  if (val instanceof ArrayBuffer) return new Uint8Array(val);
  return new Uint8Array();
}

function aesEncrypt(buf, mode, key, iv) {
  const keyBuf = Buffer.from(toUint8(key));
  const ivBuf = iv ? Buffer.from(toUint8(iv)) : null;
  const data = Buffer.from(toUint8(buf));
  const algo = mode.includes('aes-128') ? 'aes-128' : mode.includes('aes-256') ? 'aes-256' : 'aes-128';
  const cipherMode = mode.includes('ecb') ? `${algo}-ecb` : `${algo}-cbc`;
  const cipher = crypto.createCipheriv(cipherMode, keyBuf, ivBuf || '');
  const encrypted = Buffer.concat([cipher.update(data), cipher.final()]);
  return new Uint8Array(encrypted);
}

function rsaEncrypt(buf, publicKeyPem) {
  const data = Buffer.from(toUint8(buf));
  // 支持 PKCS#1 和 PKCS#8 格式
  let key = publicKeyPem;
  if (!key.includes('-----BEGIN')) {
    key = `-----BEGIN PUBLIC KEY-----\n${key}\n-----END PUBLIC KEY-----`;
  }
  const encrypted = crypto.publicEncrypt(
    { key, padding: crypto.constants.RSA_PKCS1_PADDING },
    data
  );
  return new Uint8Array(encrypted);
}

function createLxApi(scriptInfo) {
  let requestHandler = null;
  let initedResolve = null;
  const initedPromise = new Promise((resolve) => { initedResolve = resolve; });

  const lxObj = {
    EVENT_NAMES: { request: 'request', inited: 'inited', updateAlert: 'updateAlert' },
    version: '2.0.0',
    env: 'desktop',
    currentScriptInfo: scriptInfo,

    request(url, opts, callback) {
      const controller = new AbortController();
      const ms = opts.timeout && opts.timeout > 0 ? Math.min(opts.timeout, 60000) : 15000;
      const timer = setTimeout(() => controller.abort(), ms);

      const fetchOpts = {
        method: (opts.method || 'GET').toUpperCase(),
        headers: opts.headers || {},
        signal: controller.signal,
      };
      if (opts.body) fetchOpts.body = opts.body;
      if (opts.form) {
        const params = new URLSearchParams(opts.form);
        fetchOpts.body = params.toString();
        fetchOpts.headers['Content-Type'] = 'application/x-www-form-urlencoded';
      }

      fetch(url, fetchOpts)
        .then(async (res) => {
          clearTimeout(timer);
          const rawText = await res.text();
          if (res.status === 429 || res.status === 403 ||
              (rawText.length > 200 && /^\s*</.test(rawText) && /cloudflare|just a moment|attention required/i.test(rawText))) {
            callback(new Error(`HTTP ${res.status}: source API blocked (${new URL(url).hostname})`), null, null);
            return;
          }
          let body;
          try { body = JSON.parse(rawText); } catch { body = rawText; }
          const rawBytes = new TextEncoder().encode(rawText);
          const response = {
            statusCode: res.status,
            statusMessage: res.statusText,
            headers: Object.fromEntries(res.headers.entries()),
            body,
            raw: Object.assign(rawBytes, { toString: () => rawText }),
            bytes: rawBytes.length,
          };
          callback(null, response, body);
        })
        .catch((err) => {
          clearTimeout(timer);
          callback(err, null, null);
        });

      return () => { clearTimeout(timer); controller.abort(); };
    },

    on(eventName, handler) {
      if (eventName === 'request') {
        requestHandler = handler;
        return Promise.resolve();
      }
      return Promise.reject(new Error('Unsupported event: ' + eventName));
    },

    send(eventName, data) {
      if (eventName === 'inited') {
        // data 可能是 { openDevTools, sources: { wy: {...}, kw: {...} } }
        // 也可能直接是 { wy: {...}, kw: {...} }
        const sources = (data && data.sources) ? data.sources : (data || {});
        initedResolve(sources);
      }
      return Promise.resolve();
    },

    utils: {
      crypto: {
        md5(str) { return crypto.createHash('md5').update(str).digest('hex'); },
        aesEncrypt(buf, mode, key, iv) { return aesEncrypt(buf, mode, key, iv); },
        rsaEncrypt(buf, publicKeyPem) { return rsaEncrypt(buf, publicKeyPem); },
        randomBytes(n) { return new Uint8Array(crypto.randomBytes(n)); },
      },
      buffer: {
        from(val, encoding) {
          if (typeof val === 'string') {
            if (encoding === 'base64') return base64ToUint8(val);
            if (encoding === 'hex') return hexToUint8(val);
            return new Uint8Array(Buffer.from(val, encoding || 'utf-8'));
          }
          if (val instanceof ArrayBuffer) return new Uint8Array(val);
          return new Uint8Array(val);
        },
        bufToString(buf, encoding) {
          const u8 = toUint8(buf);
          if (encoding === 'base64') return uint8ToBase64(u8);
          if (encoding === 'hex') return uint8ToHex(u8);
          return Buffer.from(u8).toString('utf-8');
        },
      },
      zlib: {
        inflate(buf) {
          return Promise.resolve(new Uint8Array(zlib.inflateSync(Buffer.from(toUint8(buf)))));
        },
        deflate(data) {
          return Promise.resolve(new Uint8Array(zlib.deflateSync(Buffer.from(toUint8(data)))));
        },
      },
    },
  };

  return { lxObj, getHandler: () => requestHandler, initedPromise };
}

// ======================== 脚本加载 ========================

// 重定向 console.log 到 stderr，避免污染 stdout JSON 协议
const origLog = console.log;
console.log = (...args) => console.error('[script]', ...args);

function parseScriptMeta(rawScript) {
  const commentMatch = /^\/\*[\S\s]+?\*\//.exec(rawScript);
  const comment = commentMatch?.[0] || '';
  const get = (key) => {
    const m = new RegExp(`@${key}\\s+(.+)`).exec(comment);
    return m?.[1]?.trim().slice(0, 56) || '';
  };
  return {
    name: get('name') || 'Unknown',
    version: get('version') || '0.0.0',
    author: get('author') || '',
    rawScript,
  };
}

const loadedScripts = []; // { name, handler, sources }

async function loadScript(filePath) {
  const rawScript = fs.readFileSync(filePath, 'utf-8');
  if (!rawScript.trim() || /^\s*</.test(rawScript)) {
    throw new Error('Not a valid script');
  }
  const meta = parseScriptMeta(rawScript);
  const { lxObj, getHandler, initedPromise } = createLxApi(meta);

  globalThis.lx = lxObj;

  // 捕获 unhandledrejection
  let asyncError = null;
  const onUnhandled = (ev) => {
    asyncError = ev.reason instanceof Error ? ev.reason : new Error(String(ev.reason || 'init failed'));
    ev.preventDefault?.();
  };
  if (typeof process !== 'undefined') {
    process.on('unhandledRejection', onUnhandled);
  }

  try {
    const fn = new Function(rawScript);
    fn();
  } catch (err) {
    console.error(`[bridge] 脚本 ${meta.name} 执行失败: ${err.message}`);
    return null;
  }

  // 等待初始化（最多 10 秒）
  const timeout = new Promise((_, reject) =>
    setTimeout(() => reject(new Error(asyncError?.message || `${meta.name} 初始化超时`)), 10000)
  );

  try {
    const sources = await Promise.race([initedPromise, timeout]);
    const handler = getHandler();
    if (!handler) {
      console.error(`[bridge] ${meta.name} 未注册 request handler`);
      return null;
    }
    console.error(`[bridge] 已加载: ${meta.name} v${meta.version} (sources: ${Object.keys(sources || {}).join(',')})`);
    return { name: meta.name, handler, sources: sources || {} };
  } catch (err) {
    console.error(`[bridge] ${meta.name} 加载失败: ${err.message}`);
    return null;
  }
}

// ======================== 音频下载（Node.js 原生 http/https） ========================

/**
 * 使用 Node.js 原生 http/https 模块下载音频到文件（参考 lx-music-desktop request.ts）。
 * 某些代理 URL 对 Python aiohttp 返回 0 字节，但 Node.js http/https 可正常下载。
 */
function handleDownload(url, outputPath, headers = {}) {
  return new Promise((resolve, reject) => {
    const defaultHeaders = {
      'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
      ...headers,
    };

    let redirectCount = 0;
    const maxRedirects = 5;

    function doRequest(reqUrl) {
      const urlObj = new URL(reqUrl);
      const options = {
        hostname: urlObj.hostname,
        port: urlObj.port,
        path: urlObj.pathname + urlObj.search,
        method: 'GET',
        headers: defaultHeaders,
        timeout: 15000,
      };

      const client = urlObj.protocol === 'https:' ? https : http;
      const req = client.request(options, (res) => {
        // 处理重定向
        if ([301, 302, 303, 307, 308].includes(res.statusCode) && res.headers.location) {
          if (redirectCount >= maxRedirects) {
            reject(new Error(`重定向次数超过上限 (${maxRedirects})`));
            return;
          }
          redirectCount++;
          let newUrl = res.headers.location;
          if (newUrl.startsWith('/')) {
            newUrl = `${urlObj.protocol}//${urlObj.host}${newUrl}`;
          }
          res.resume(); // 丢弃当前响应
          doRequest(newUrl);
          return;
        }

        if (res.statusCode >= 400) {
          res.resume();
          reject(new Error(`HTTP ${res.statusCode}`));
          return;
        }

        // 流式写入文件
        const fileStream = fs.createWriteStream(outputPath);
        let totalSize = 0;

        res.on('data', (chunk) => {
          totalSize += chunk.length;
          fileStream.write(chunk);
        });

        res.on('end', () => {
          fileStream.end(() => {
            if (totalSize < 1024) {
              reject(new Error(`音频文件过小 (${totalSize} bytes)`));
            } else {
              console.error(`[bridge] 下载完成: ${totalSize} bytes → ${outputPath}`);
              resolve({ size: totalSize });
            }
          });
        });

        res.on('error', (err) => {
          fileStream.destroy();
          try { fs.unlinkSync(outputPath); } catch {}
          reject(err);
        });
      });

      req.on('timeout', () => {
        req.destroy(new Error('下载超时 (15s)'));
      });

      req.on('error', (err) => {
        reject(err);
      });

      req.end();
    }

    doRequest(url);
  });
}

// ======================== 请求处理 ========================

async function handleSearch(source, keyword, limit) {
  // 找到支持该平台搜索的脚本
  const matching = loadedScripts.filter(s =>
    s.sources && s.sources[source] && s.sources[source].actions?.includes('search')
  );
  console.error(`[bridge] 搜索 ${source} "${keyword}": ${matching.length} 个脚本支持`);

  if (matching.length === 0) {
    // 回退：尝试所有脚本（有些不声明 search 但实际支持）
    console.error(`[bridge] 没有脚本声明 ${source} search，尝试所有脚本`);
    matching.push(...loadedScripts);
  }

  const tasks = matching.map(s => ({
    name: s.name,
    promise: s.handler({
      action: 'search',
      source,
      info: { keyword, limit: limit || 20 },
    }).then(result => {
      console.error(`[bridge] 搜索结果来自: ${s.name}`);
      return result;
    }).catch(err => {
      console.error(`[bridge] ${s.name} 搜索失败: ${err.message}`);
      throw err;
    }),
  }));

  if (tasks.length === 0) throw new Error(`没有音源支持 ${source} 搜索`);

  // 并发竞速，返回第一个成功结果（8秒超时，避免长时间等待）
  try {
    const result = await Promise.race([
      Promise.any(tasks.map(t => t.promise)),
      new Promise((_, reject) => setTimeout(() => reject(new Error('搜索超时 (8s)')), 8000)),
    ]);
    return result;
  } catch (aggErr) {
    throw new Error(`所有音源搜索失败: ${source} (${aggErr.message})`);
  }
}

async function handleMusicUrl(source, info, quality) {
  const musicInfo = {
    name: info.name || '',
    singer: info.singer || '',
    source: source,
    songmid: info.song_id || info.songmid || info.id || '',
    albumId: info.album_id || '',
    albumName: info.album || info.albumName || '',
    interval: info.interval || '',
    img: info.img || null,
    lrc: null,
    otherSource: null,
    types: [],
    _types: {},
    typeUrl: {},
    hash: info.hash || '',
    strMediaMid: info.strMediaMid || '',
    copyrightId: info.copyright_id || '',
    meta: info.meta || {},
  };

  const request = {
    action: 'musicUrl',
    source,
    info: { type: quality || '128k', musicInfo },
  };

  // 优先：只尝试声明支持该平台 musicUrl 的脚本
  const matching = loadedScripts.filter(s =>
    s.sources && s.sources[source] && (
      !s.sources[source].actions ||
      s.sources[source].actions.includes('musicUrl')
    )
  );
  const candidates = matching.length > 0 ? matching : loadedScripts;
  console.error(`[bridge] musicUrl ${source} ${quality}: ${matching.length} 精确匹配, ${candidates.length} 候选`);

  // 判断 URL 是否为直接音频 CDN 链接（非代理）
  // 代理 URL 通常包含 .php、?id=、?type=mp3 等查询参数
  // 直接 CDN URL 通常路径中包含 hex token，如 /3a1e1d1317e4458e27ce398eb095c5af/
  function isDirectAudioUrl(url) {
    try {
      const u = new URL(url);
      // 已知的直接 CDN 域名
      const DIRECT_DOMAINS = [
        'kw-lw.kuwo.cn', 'kuwo.cn',
        'm701.music.126.net', 'm801.music.126.net', 'm7.music.126.net', 'm8.music.126.net', // netease
        'dl.stream.qqmusic.qq.com', 'ws.stream.qqmusic.qq.com', // tencent
        'trackercdn.kugou.com', 'fsandroid.kugou.com', // kugou
        'freetls.nsmusic.com', 'migu.cn', // migu
      ];
      if (DIRECT_DOMAINS.some(d => u.hostname === d || u.hostname.endsWith('.' + d))) return true;
      // 路径中包含长 hex token（签名路径）
      if (/[a-f0-9]{20,}/i.test(u.pathname)) return true;
      // 排除 .php 结尾的
      if (u.pathname.endsWith('.php')) return false;
      // 排除带 ?id= 查询参数的
      if (u.searchParams.has('id')) return false;
      return false;
    } catch {
      return false;
    }
  }

  // 收集所有结果，1秒窗口（参考Museek波次竞速，缩短等待时间）
  const COLLECT_WINDOW = 1000;
  const results = [];

  const collectPromise = Promise.allSettled(
    candidates.map(s =>
      s.handler(request).then(url => {
        if (typeof url === 'string' && /^https?:\/\//.test(url)) {
          const direct = isDirectAudioUrl(url);
          console.error(`[bridge] URL 来自: ${s.name} (direct=${direct}) -> ${url.slice(0, 70)}`);
          results.push({ url, direct, name: s.name });
        } else {
          throw new Error(`${s.name}: 无效URL`);
        }
      })
    )
  );

  // 等待收集窗口结束（不阻塞后续返回）
  await Promise.race([
    collectPromise,
    new Promise(resolve => setTimeout(resolve, COLLECT_WINDOW)),
  ]);

  // 如果1秒内没有结果，再等2秒
  if (results.length === 0) {
    console.error(`[bridge] 1秒内无结果，再等2秒...`);
    await Promise.race([
      collectPromise,
      new Promise(resolve => setTimeout(resolve, 2000)),
    ]);
  }

  if (results.length === 0) {
    throw new Error('所有音源获取失败');
  }

  // 优先返回直接 CDN URL
  const direct = results.filter(r => r.direct);
  if (direct.length > 0) {
    // 多个直接 URL 优先返回第一个
    console.error(`[bridge] 选择直接CDN URL: ${direct[0].name}`);
    return direct[0].url;
  }

  // 没有直接 URL，回退到代理 URL（参考Museek：不拒绝代理URL，由调用方HEAD验证）
  // 代理 URL 可能返回 0 字节，但也可能是唯一可用的音源（如咪咕平台）
  if (results.length > 0) {
    console.error(`[bridge] 无直接CDN URL，回退到代理URL: ${results[0].name} (共${results.length}个)`);
    return results[0].url;
  }

  throw new Error('所有音源获取失败');
}

// ======================== 主循环 ========================

async function main() {
  const scriptDir = process.argv[2];
  if (!scriptDir) {
    console.error('[bridge] 用法: node source_bridge.js <script_dir>');
    process.exit(1);
  }

  // 加载所有 .js 脚本
  const files = fs.readdirSync(scriptDir).filter(f => f.endsWith('.js'));
  console.error(`[bridge] 发现 ${files.length} 个脚本文件`);

  for (const file of files) {
    const filePath = path.join(scriptDir, file);
    const loaded = await loadScript(filePath);
    if (loaded) loadedScripts.push(loaded);
  }

  console.error(`[bridge] 成功加载 ${loadedScripts.length}/${files.length} 个音源`);

  if (loadedScripts.length === 0) {
    console.error('[bridge] 没有可用的音源脚本');
    process.exit(1);
  }

  // 通知 Python 桥接器已就绪
  process.stdout.write(JSON.stringify({ type: 'ready', scripts: loadedScripts.length }) + '\n');

  // 读取 stdin 处理请求
  const readline = require('readline');
  const rl = readline.createInterface({ input: process.stdin, terminal: false });

  let pendingRequests = 0;
  let stdinClosed = false;

  rl.on('line', async (line) => {
    pendingRequests++;
    try {
      const req = JSON.parse(line);
      let result;
      if (req.action === 'search') {
        const data = await handleSearch(req.source, req.keyword, req.limit);
        result = { id: req.id, success: true, data };
      } else if (req.action === 'musicUrl') {
        const url = await handleMusicUrl(req.source, req.info, req.type);
        result = { id: req.id, success: true, url };
      } else if (req.action === 'download') {
        const data = await handleDownload(req.url, req.outputPath, req.headers || {});
        result = { id: req.id, success: true, data };
      } else {
        result = { id: req.id, success: false, error: `未知 action: ${req.action}` };
      }
      process.stdout.write(JSON.stringify(result) + '\n');
    } catch (err) {
      process.stdout.write(JSON.stringify({ id: req?.id, success: false, error: err.message }) + '\n');
    } finally {
      pendingRequests--;
      if (stdinClosed && pendingRequests === 0) {
        process.exit(0);
      }
    }
  });

  rl.on('close', () => {
    console.error('[bridge] stdin 已关闭，等待 pending 请求完成...');
    stdinClosed = true;
    if (pendingRequests === 0) {
      process.exit(0);
    }
    // 最多等待30秒后强制退出
    setTimeout(() => {
      console.error('[bridge] 超时强制退出');
      process.exit(0);
    }, 30000);
  });
}

main().catch(err => {
  console.error(`[bridge] 致命错误: ${err.message}`);
  process.exit(1);
});
