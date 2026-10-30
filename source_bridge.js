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
    // 上报现代 lx 真实版本号：部分音源脚本按协议检查 lx.version，
    // 版本过低会被通用版本门禁拒绝初始化（与具体音源无关，属协议合规）。
    version: '2.10.0',
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
      if (opts.formData) {
        // 官方规范支持 multipart/form-data（formData: {name: value | {value, options}})
        const fd = new FormData();
        for (const [k, v] of Object.entries(opts.formData)) {
          if (v && typeof v === 'object' && 'value' in v) {
            fd.append(k, v.value, v.options || {});
          } else {
            fd.append(k, v);
          }
        }
        fetchOpts.body = fd;
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
      // request 是宿主->脚本的唯一常规事件；updateAlert 等注册请求宽容接受（不 reject，
      // 避免脚本无 catch 的注册触发 unhandledRejection 被误判为初始化失败）
      if (eventName === 'request') {
        requestHandler = handler;
      }
      return Promise.resolve();
    },

    send(eventName, data) {
      if (eventName === 'inited') {
        // data 可能是 { openDevTools, sources: { wy: {...}, kw: {...} } }
        // 也可能直接是 { wy: {...}, kw: {...} }
        const sources = (data && data.sources) ? data.sources : (data || {});
        initedResolve(sources);
      } else if (eventName === 'updateAlert') {
        // 源更新提示：无 UI，记日志（官方规范：{ log, updateUrl }，每次运行最多一次）
        console.error(`[bridge] 源更新提示: ${typeof data === 'object' && data ? (data.log || '').slice(0, 200) : data}`);
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
  // 官方规范字段：name/description/version/author/homepage
  return {
    name: get('name') || 'Unknown',
    description: get('description'),
    version: get('version') || '0.0.0',
    author: get('author') || '',
    homepage: get('homepage') || '',
    rawScript,
  };
}

const loadedScripts = []; // { name, handler, sources }

// 脚本沙箱 require：补全 Node CommonJS 标准全局，使脚本可像在 Node 文件里访问内置模块
// （如 crypto、buffer、zlib、path、fs 等）。安全基线：进程执行能力（child_process）在 lx
// 桌面脚本沙箱中本就不暴露，故默认降级，避免脚本 spawn 任意系统命令导致挂起/越权，同时
// 与落雪沙箱行为对齐。所有标准 lx 脚本不依赖此能力，该降级对它们无副作用。
const SANDBOX_BLOCKED_MODULES = new Set(['child_process']);
function sandboxRequire(name) {
  if (SANDBOX_BLOCKED_MODULES.has(name)) {
    throw new Error(`模块 "${name}" 在脚本沙箱中不可用（进程执行能力已禁用）`);
  }
  return require(name);
}

// 阶段A: 执行脚本并返回其 inited promise（不等待初始化完成）
function executeScript(filePath) {
  const rawScript = fs.readFileSync(filePath, 'utf-8');
  if (!rawScript.trim() || /^\s*</.test(rawScript)) {
    throw new Error('Not a valid script');
  }
  const meta = parseScriptMeta(rawScript);
  const { lxObj, getHandler, initedPromise } = createLxApi(meta);

  globalThis.lx = lxObj;

  let asyncError = null;
  const onUnhandled = (ev) => {
    asyncError = ev.reason instanceof Error ? ev.reason : new Error(String(ev.reason || 'init failed'));
    ev.preventDefault?.();
  };
  if (typeof process !== 'undefined') {
    process.on('unhandledRejection', onUnhandled);
  }

  // 通用脚本执行环境：把 Node CommonJS 标准全局（require/module/exports/__dirname/__filename）
  // 作为函数参数注入，使脚本能像在 Node 文件里一样访问内置模块（如 crypto、buffer）。这是
  // 协议级通用健壮性，不针对任何具体音源——标准 lx 脚本不引用这些参数，零副作用。
  // require 走 sandboxRequire（内置模块放行、child_process 降级，见上）。
  const scriptDir = path.dirname(filePath);
  const fn = new Function(
    'require', 'module', 'exports', '__dirname', '__filename',
    'lx',
    rawScript
  );
  fn(
    sandboxRequire, module, exports, scriptDir, filePath,
    lxObj
  );
  return { meta, initedPromise, getHandler, getAsyncError: () => asyncError };
}

// 阶段B: 并行等待全部脚本的 inited（网络 init 互不阻塞，总耗时≈最慢者而非求和）
async function loadAllScripts(files) {
  const executed = []; // { meta, initedPromise, getHandler, filePath }
  for (const filePath of files) {
    try {
      executed.push({ ...executeScript(filePath), filePath });
    } catch (err) {
      console.error(`[bridge] 脚本执行失败 ${path.basename(filePath)}: ${err.message}`);
    }
  }
  const results = await Promise.all(executed.map(async (e) => {
    // 初始化超时可经环境变量配置（秒）：混淆源解混淆+联网较慢，默认放宽到 30s
    const initMs = (parseInt(process.env.LX_INIT_TIMEOUT || '30', 10) || 30) * 1000;
    const timeout = new Promise((_, reject) =>
      setTimeout(() => reject(new Error(e.getAsyncError()?.message || `初始化超时 (${initMs / 1000}s)`)), initMs));
    try {
      const sources = await Promise.race([e.initedPromise, timeout]);
      const handler = e.getHandler();
      if (!handler) {
        console.error(`[bridge] ${e.meta.name} 未注册 request handler`);
        return null;
      }
      console.error(`[bridge] 已加载: ${e.meta.name} v${e.meta.version} (sources: ${Object.keys(sources || {}).join(',')})`);
      return { name: e.meta.name, handler, sources: sources || {} };
    } catch (err) {
      console.error(`[bridge] ${e.meta.name} 加载失败: ${err.message}`);
      return null;
    }
  }));
  return results.filter(Boolean);
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

// ======================== 内置搜索（lx 官方 musicSdk） ========================

// lx-music-desktop v2.12.6 的 musicSdk（Apache 2.0）经 esbuild 打包的单文件 bundle。
// 官方音源脚本协议只有 musicUrl 动作，搜索是 lx 应用内置引擎——本 bundle 即该引擎。
// 作用：脚本不支持 search（官方标准脚本）或脚本搜索失败时，点歌机仍能与 lx 搜到同样的歌。
let musicSdkCache = null;
function getMusicSdk() {
  if (musicSdkCache === null) {
    try {
      musicSdkCache = require('./musicSdk_bundle.cjs').default;
    } catch (err) {
      console.error('[bridge] 内置搜索引擎加载失败（将仅依赖音源脚本搜索）:', err.message);
      musicSdkCache = false;
    }
  }
  return musicSdkCache || null;
}

async function handleSearchBuiltin(source, keyword, limit) {
  const sdk = getMusicSdk();
  if (!sdk || !sdk[source] || !sdk[source].musicSearch) {
    throw new Error(`内置引擎不支持 ${source}`);
  }
  const result = await sdk[source].musicSearch.search(keyword, 1, limit || 20);
  const list = (result && result.list) || [];
  console.error(`[bridge] 内置引擎搜索 ${source} "${keyword}": ${list.length}/${result.total || '?'}`);
  return list;
}

// ======================== musicInfo 保真缓存 ========================

// 对齐落雪行为：搜索到播放之间传递完整 musicInfo 对象（kg 的 hash、tx 的 strMediaMid、
// 脚本可能读取的 types/_types/meta 等）。搜索结果在此缓存，musicUrl 请求时优先匹配
// 完整对象而非 Python 侧重建的瘦身对象。
const musicInfoCache = new Map(); // source -> Map(idKey -> musicInfo)
const MUSIC_INFO_CACHE_MAX = 500; // 每个 source 的缓存上限（防止长期驻留膨胀）

function cacheSearchResult(source, list) {
  if (!Array.isArray(list)) return;
  let m = musicInfoCache.get(source);
  if (!m) {
    m = new Map();
    musicInfoCache.set(source, m);
  }
  for (const item of list) {
    if (!item || typeof item !== 'object') continue;
    for (const key of [item.songmid, item.hash, item.copyrightId, item.id, item.songId]) {
      if (key !== undefined && key !== null && key !== '') {
        if (m.size >= MUSIC_INFO_CACHE_MAX && !m.has(String(key))) {
          m.delete(m.keys().next().value); // FIFO 淘汰
        }
        m.set(String(key), item);
      }
    }
  }
}

function findCachedMusicInfo(source, idKey) {
  const m = musicInfoCache.get(source);
  if (!m || idKey === undefined || idKey === null || idKey === '') return null;
  return m.get(String(idKey)) || null;
}

// ======================== 请求处理 ========================

async function handleSearch(source, keyword, limit) {
  // 找到支持该平台搜索的脚本
  const matching = loadedScripts.filter(s =>
    s.sources && s.sources[source] && s.sources[source].actions?.includes('search')
  );
  console.error(`[bridge] 搜索 ${source} "${keyword}": ${matching.length} 个脚本支持`);

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

  // 1. 声明支持 search 的脚本并发竞速（8秒超时）
  let scriptResult = null;
  if (tasks.length > 0) {
    try {
      scriptResult = await Promise.race([
        Promise.any(tasks.map(t => t.promise)),
        new Promise((_, reject) => setTimeout(() => reject(new Error('搜索超时 (8s)')), 8000)),
      ]);
    } catch (aggErr) {
      console.error(`[bridge] 脚本搜索失败: ${aggErr.message}，尝试内置引擎`);
    }
  }

  // 脚本成功且非空 -> 缓存完整对象并返回
  const nonEmpty = r => r && (!Array.isArray(r) || r.length > 0);
  if (nonEmpty(scriptResult)) {
    if (Array.isArray(scriptResult)) cacheSearchResult(source, scriptResult);
    return scriptResult;
  }

  // 2. 回退：lx 内置搜索引擎（与 lx 播放器搜到同样的歌）
  try {
    const builtinResult = await handleSearchBuiltin(source, keyword, limit);
    if (Array.isArray(builtinResult)) cacheSearchResult(source, builtinResult);
    return builtinResult;
  } catch (builtinErr) {
    console.error(`[bridge] 内置引擎搜索失败: ${builtinErr.message}`);
  }

  // 3. 最后回退：盲试所有脚本（兼容声明不全但实际支持 search 的魔改脚本）
  if (loadedScripts.length > matching.length) {
    console.error(`[bridge] 盲试全部脚本 (${loadedScripts.length})`);
    try {
      const result = await Promise.race([
        Promise.any(loadedScripts.map(s =>
          s.handler({ action: 'search', source, info: { keyword, limit: limit || 20 } })
        )),
        new Promise((_, reject) => setTimeout(() => reject(new Error('搜索超时 (8s)')), 8000)),
      ]);
      if (nonEmpty(result)) return result;
    } catch (aggErr) {
      /* 全失败，落到下面 */
    }
  }

  throw new Error(`所有搜索通道失败（脚本/内置引擎）: ${source}`);
}

async function handleMusicUrl(source, info, quality) {
  // 对齐落雪：优先使用搜索阶段缓存的完整 musicInfo（保真 hash/strMediaMid/types/meta 等
  // 脚本可能依赖的字段），Python 侧传入的 song_id 用于匹配
  const idKey = info.song_id || info.songmid || info.id || info.hash || '';
  const cached = findCachedMusicInfo(source, idKey);
  let musicInfo;
  if (cached) {
    musicInfo = cached;
    console.error(`[bridge] musicInfo 命中缓存 (${idKey})`);
  } else {
    console.error(`[bridge] musicInfo 缓存未命中 (${idKey})，使用重建对象`);
    musicInfo = {
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
  }

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

// 内置引擎模式请求循环（search/musicUrl 直走 musicSdk，无脚本）
function handleBuiltinLoop() {
  const readline = require('readline');
  const rl = readline.createInterface({ input: process.stdin, terminal: false });
  rl.on('line', async (line) => {
    let req = {};
    try {
      req = JSON.parse(line);
      let result;
      if (req.action === 'search') {
        const data = await handleSearchBuiltin(req.source, req.keyword, req.limit);
        result = { id: req.id, success: true, data };
      } else if (req.action === 'musicUrl') {
        const url = await handleBuiltinMusicUrl(req.source, req.info, req.type);
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
    }
  });
}

async function handleBuiltinMusicUrl(source, info, quality) {
  const sdk = getMusicSdk();
  if (!sdk || !sdk[source] || !sdk[source].getMusicUrl) {
    throw new Error(`内置引擎不支持 ${source} 的 URL 获取`);
  }
  const idKey = info.song_id || info.songmid || info.id || info.hash || '';
  const cached = findCachedMusicInfo(source, idKey) || {
    songmid: info.song_id || info.songmid || '',
    hash: info.hash || '',
    name: info.name || '', singer: info.singer || '',
    albumId: '', albumName: info.album || '', interval: '',
    img: null, types: [], _types: {}, typeUrl: {},
  };
  const r = sdk[source].getMusicUrl(cached, quality || '128k');
  const url = (r && r.promise) ? await r.promise : await r;
  if (typeof url !== 'string' || !/^https?:\/\//.test(url)) throw new Error('内置引擎返回无效 URL');
  console.error(`[bridge] 内置引擎 URL (${source} ${quality}) -> ${url.slice(0, 70)}`);
  return url;
}

async function main() {
  const args = process.argv.slice(2);
  if (args.length === 0) {
    console.error('[bridge] 用法: node source_bridge.js <script_dir|script.js> [...] | --builtin');
    process.exit(1);
  }

  // --builtin 模式：不加载脚本，仅提供内置引擎（lx musicSdk）的搜索与 URL
  if (args[0] === '--builtin') {
    const sdk = getMusicSdk();
    if (!sdk) {
      console.error('[bridge] 内置引擎加载失败');
      process.exit(1);
    }
    const builtinSources = {};
    for (const s of ['kw', 'kg', 'tx', 'wy', 'mg']) {
      if (sdk[s] && sdk[s].musicSearch) builtinSources[s] = { actions: ['search', 'musicUrl'] };
    }
    loadedScripts.push({ name: '内置引擎', builtin: true, sources: builtinSources });
    process.stdout.write(JSON.stringify({ type: 'ready', scripts: 1, sources: builtinSources }) + '\n');
    handleBuiltinLoop();
    return;
  }

  // 脚本模式：每个参数可为目录（加载其中全部 .js）或单个脚本文件（每脚本一进程的隔离模式）
  const files = [];
  for (const p of args) {
    if (fs.existsSync(p) && fs.statSync(p).isFile() && p.endsWith('.js')) {
      files.push(p);
    } else if (fs.existsSync(p)) {
      files.push(...fs.readdirSync(p).filter(f => f.endsWith('.js')).map(f => path.join(p, f)));
    }
  }
  console.error(`[bridge] 发现 ${files.length} 个脚本文件`);

  const loaded = await loadAllScripts(files);
  loadedScripts.push(...loaded);

  const sourcesAgg = {};
  for (const s of loadedScripts) {
    for (const [k, v] of Object.entries(s.sources || {})) {
      if (!sourcesAgg[k]) sourcesAgg[k] = { actions: [...(v.actions || [])] };
      else for (const a of (v.actions || [])) if (!sourcesAgg[k].actions.includes(a)) sourcesAgg[k].actions.push(a);
    }
  }

  console.error(`[bridge] 成功加载 ${loadedScripts.length}/${files.length} 个音源`);

  if (loadedScripts.length === 0) {
    console.error('[bridge] 没有可用的音源脚本');
    process.exit(1);
  }

  // 通知 Python 桥接器已就绪（携带 sources 聚合声明，供多进程路由）
  // 多进程模型下每个 Node 进程只加载单个脚本文件，故 scriptName/scriptFile 唯一
  const first = loadedScripts[0] || {};
  process.stdout.write(JSON.stringify({
    type: 'ready', scripts: loadedScripts.length, sources: sourcesAgg,
    scriptName: first.name || 'Unknown',
    scriptFile: (args.find(a => a.endsWith('.js')) || ''),
  }) + '\n');

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
