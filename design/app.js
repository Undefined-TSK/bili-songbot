/* ============================================================
   SongBot Desktop · Fluent 2 交互层 + 真实后端数据
   动效：100/150/250/280ms · standard / decelerate / accelerate
   数据：/api/snapshot 每秒轮询；日志/弹幕增量拉取
   ============================================================ */
(() => {
  'use strict';

  const ORDER = ['dashboard', 'request', 'queue', 'live', 'sources', 'logs'];
  const $ = (sel, root = document) => root.querySelector(sel);
  const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));
  const bridge = window.songbot || null;
  const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;

  const pages = $$('.page');
  const navItems = $$('.nav-item');
  const pageTitle = $('#pageTitle');
  const pageEyebrow = $('#pageEyebrow');

  let current = 'dashboard';

  /* ---------------------------------------------------------------- 基础工具 */

  const state = {
    snapshot: null,
    settings: null,
    sources: null,
    backend: { status: 'starting', port: null, external: false, error: '' },
    searchResults: [],
    searchPlatform: 'all',
    searchSuggestion: null,
    sourcesItems: [],
    healthyOnly: false,
    danmaku: [],
    lastDmId: 0,
    dmFilter: 'all',
    logs: [],
    lastLogId: 0,
    logLevel: 'all',
    logKeyword: '',
    logAuto: true,
    settingsLoaded: false,
    backendNotified: false,
  };

  let browserBase = null;

  async function ensureBrowserBase() {
    if (browserBase !== null) return browserBase;
    for (let port = 8790; port < 8800; port += 1) {
      try {
        const res = await fetch(`http://127.0.0.1:${port}/api/health`);
        if (res.ok) {
          const data = await res.json();
          if (data && data.ok) { browserBase = `http://127.0.0.1:${port}`; return browserBase; }
        }
      } catch { /* 继续探测 */ }
    }
    browserBase = '';
    return browserBase;
  }

  async function api(path, method = 'GET', body) {
    if (bridge && typeof bridge.api === 'function') {
      return bridge.api(path, method, body);
    }
    const base = await ensureBrowserBase();
    if (!base) return { ok: false, status: 503, data: { ok: false, error: '后端未连接' } };
    try {
      const res = await fetch(base + path, {
        method,
        headers: { 'Content-Type': 'application/json' },
        body: body === undefined ? undefined : JSON.stringify(body),
      });
      const data = await res.json().catch(() => ({}));
      return { ok: res.ok, status: res.status, data };
    } catch (err) {
      return { ok: false, status: 599, data: { ok: false, error: String(err && err.message || err) } };
    }
  }

  const fmt = n => (Number(n) >= 1000 ? Number(n).toLocaleString('en-US') : String(Number(n) || 0));
  const pad2 = n => String(n).padStart(2, '0');
  const fmtTime = value => {
    const total = Math.max(0, Math.floor(Number(value) || 0));
    return `${pad2(Math.floor(total / 60))}:${pad2(total % 60)}`;
  };
  const clockTime = ts => {
    const d = new Date(Number(ts) * 1000);
    return `${pad2(d.getHours())}:${pad2(d.getMinutes())}:${pad2(d.getSeconds())}.${String(d.getMilliseconds()).padStart(3, '0')}`;
  };
  const coverText = name => (String(name || '').trim().slice(0, 1) || '—');
  function coverClass(name) {
    const text = String(name || '');
    let hash = 0;
    for (let i = 0; i < text.length; i += 1) hash = (hash * 31 + text.charCodeAt(i)) % 8;
    return `c${hash + 1}`;
  }
  function setCover(el, name, extra = '') {
    if (!el) return;
    el.className = `cover ${coverClass(name)}${extra ? ` ${extra}` : ''}`;
    el.textContent = coverText(name);
  }
  function setMeta(el, title, sub) {
    if (!el) return;
    const t = $('.t', el); const s = $('.s', el);
    if (t) t.textContent = title || '—';
    if (s) s.textContent = sub || '—';
  }
  function setCounter(el, value) {
    if (!el) return;
    const n = Math.max(0, Math.round(Number(value) || 0));
    el.dataset.count = String(n);
    el.textContent = fmt(n);
  }
  function setProgress(bar, percent) {
    if (!bar) return;
    const p = Math.max(0, Math.min(100, Number(percent) || 0));
    bar.style.setProperty('--p', `${p.toFixed(1)}%`);
  }
  function desc(result) {
    const data = result && result.data;
    if (data && data.error) return data.error;
    return result && result.status ? `请求失败（${result.status}）` : '请求失败';
  }

  /* ---------------------------------------------------------------- Toast */

  const toastWrap = $('#toastWrap');
  function toast(msg) {
    if (!msg || !toastWrap) return;
    const el = document.createElement('div');
    el.className = 'toast';
    el.innerHTML = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M5 12.5 10 17.5 19 7"/></svg><span></span>';
    el.querySelector('span').textContent = msg;
    toastWrap.appendChild(el);
    setTimeout(() => {
      el.classList.add('out');
      setTimeout(() => el.remove(), 200);
    }, 2600);
  }

  /* ---------------------------------------------------------------- 页面切换（串行转场） */

  function staggerIn(page) {
    if (!page) return;
    Array.from(page.children).forEach((child, i) => {
      child.style.setProperty('--i', String(Math.min(i, 8)));
    });
    Array.from(page.querySelectorAll('.row')).forEach((row, i) => {
      row.style.setProperty('--i', String(Math.min(i, 8)));
    });
    page.classList.remove('stagger');
    void page.offsetWidth;
    page.classList.add('stagger');
    setTimeout(() => page.classList.remove('stagger'), 470);
  }

  const EXIT_MS = 140;
  const ENTER_MS = 280;
  let pendingEnter = null;

  function runCountUp(root) {
    $$('[data-count]', root).forEach(el => {
      const target = Number(el.dataset.count || 0);
      if (reducedMotion) { el.textContent = fmt(target); return; }
      const dur = 800;
      const t0 = performance.now();
      function frame(t) {
        const p = Math.min(1, (t - t0) / dur);
        const eased = 1 - Math.pow(1 - p, 3);
        el.textContent = fmt(Math.round(target * eased));
        if (p < 1) requestAnimationFrame(frame);
      }
      requestAnimationFrame(frame);
    });
  }

  function switchPage(id, opts = {}) {
    if (!ORDER.includes(id)) return;
    if (id === current && !opts.force) return;
    if (pendingEnter) { clearTimeout(pendingEnter); pendingEnter = null; }

    const from = ORDER.indexOf(current);
    const to = ORDER.indexOf(id);
    const forward = to > from;
    const oldPage = $(`#page-${current}`);
    const newPage = $(`#page-${id}`);
    if (!newPage) return;

    if (oldPage) {
      oldPage.classList.remove('active', 'entering', 'leaving');
      void oldPage.offsetWidth;
      oldPage.style.setProperty('--exit-x', forward ? '-24px' : '24px');
      oldPage.classList.add('leaving');
    }

    const enter = () => {
      pendingEnter = null;
      if (oldPage) oldPage.classList.remove('leaving');
      current = id;
      newPage.classList.remove('leaving');
      newPage.classList.add('active');
      if (!reducedMotion) {
        newPage.style.setProperty('--enter-x', forward ? '24px' : '-24px');
        newPage.classList.add('entering');
        setTimeout(() => newPage.classList.remove('entering'), ENTER_MS + 20);
      }
      staggerIn(newPage);
      newPage.scrollTop = 0;
      runCountUp(newPage);
      onPageShown(id);
    };

    if (reducedMotion || !oldPage) enter();
    else pendingEnter = setTimeout(enter, EXIT_MS);

    const item = navItems.find(i => i.dataset.page === id);
    if (item) {
      pageTitle.textContent = item.dataset.title || pageTitle.textContent;
      pageEyebrow.textContent = item.dataset.eyebrow || pageEyebrow.textContent;
      navItems.forEach(n => n.classList.toggle('active', n === item));
    }
  }

  function onPageShown(id) {
    if (id === 'sources') {
      if (!state.settingsLoaded) loadSettings();
      fetchSources();
    }
    if (id === 'logs') fetchLogs();
    if (id === 'request') renderSearch();
  }

  navItems.forEach(item => {
    item.addEventListener('click', () => switchPage(item.dataset.page));
  });

  /* ---------------------------------------------------------------- 后端状态 */

  function applyBackendState(next) {
    if (!next) return;
    state.backend = next;
    const dot = $('#connDot');
    const title = $('#connTitle');
    const sub = $('#connSub');
    const online = next.status === 'online';
    if (dot) dot.style.background = online ? 'var(--ok)' : (next.status === 'starting' ? 'var(--warn)' : 'var(--err)');
    if (title) title.textContent = online ? '服务运行中' : (next.status === 'starting' ? '后端启动中' : '后端未就绪');
    if (sub) {
      sub.textContent = online
        ? `127.0.0.1:${next.port} · ${next.external ? '外部后端' : '本地进程'}`
        : (next.error || '等待 Python 后端…');
    }
    if (!online && next.status === 'offline' && !state.backendNotified) {
      state.backendNotified = true;
      toast('后端未就绪：请检查 Python 环境与 backend.log');
    }
    if (online) state.backendNotified = false;
  }

  if (bridge) {
    document.body.dataset.platform = bridge.platform;
    $$('[data-win]').forEach(btn => {
      btn.addEventListener('click', async () => {
        const action = btn.dataset.win;
        if (action === 'minimize') bridge.minimize();
        else if (action === 'maximize') await bridge.toggleMaximize();
        else if (action === 'close') bridge.close();
      });
    });
    bridge.onWindowState(({ maximized }) => {
      const btn = $('[data-win="maximize"]');
      if (btn) btn.setAttribute('aria-label', maximized ? '还原' : '最大化');
    });
    if (typeof bridge.getBackendState === 'function') {
      bridge.getBackendState().then(applyBackendState).catch(() => {});
    }
    if (typeof bridge.onBackendState === 'function') bridge.onBackendState(applyBackendState);
  }

  /* ---------------------------------------------------------------- 概览 / 播放渲染 */

  function playbackPercent(pb) {
    const dur = Number(pb.duration || 0);
    if (!dur) return 0;
    return Math.max(0, Math.min(100, (Number(pb.position || 0) / dur) * 100));
  }

  function setPlayIcon(playing) {
    const href = playing ? '#i-pause' : '#i-play';
    ['#pbPlay use', '#npToggle use', '#qToggle use', '#flyoutPlay use'].forEach(sel => {
      const use = $(sel);
      if (use) use.setAttribute('href', href);
    });
  }

  function renderPlayback(pb) {
    if (!pb) return;
    const song = pb.song;
    const playing = Boolean(pb.playing && !pb.paused);
    const pct = playbackPercent(pb);
    const backend = pb.backend || '—';
    const pos = fmtTime(pb.position);
    const dur = fmtTime(pb.duration);

    // 概览「现在播放」
    $('#npTitle').textContent = song ? song.name : '暂无播放';
    $('#npArtist').textContent = song
      ? [song.singer, song.platform_name, pb.requester ? `@${pb.requester}` : '', `已播 ${pos} / ${dur}`].filter(Boolean).join(' · ')
      : '等待点歌入队';
    $('#npHint').textContent = song ? (pb.paused ? '已暂停' : '正在播放') : '等待队列';
    $('#npTag').textContent = song ? backend : '—';
    setCover($('#npCover'), song ? song.name : '—', 'flip-source');
    setProgress($('#npBar'), pct);

    // 队列页当前项
    $('#qTitle').textContent = song ? song.name : '暂无播放';
    $('#qSub').textContent = song
      ? [song.singer, pb.requester ? `@${pb.requester}` : '', `已播 ${pos} / ${dur}`].filter(Boolean).join(' · ')
      : '—';
    $('#queueCurrentTag').textContent = song ? backend : '—';
    setCover($('#qCover'), song ? song.name : '—', 'flip-source');

    // 播放条
    $('#pbTitle').textContent = song ? song.name : '暂无播放';
    $('#pbTag').textContent = song ? (song.platform || '—') : '—';
    $('#pbArtist').textContent = song
      ? [song.singer, backend].filter(Boolean).join(' · ')
      : '等待点歌入队';
    setCover($('#pbCover'), song ? song.name : '—', 'flip-source');
    $('#pbTimeCur').textContent = pos;
    $('#pbTimeDur').textContent = dur;
    setProgress($('#pbBar'), pct);

    // 浮层
    $('#flyTitle').textContent = song ? song.name : '暂无播放';
    $('#flyTag').textContent = song ? (song.platform_name || song.platform || '—') : '—';
    $('#flyArtist').textContent = song
      ? [song.singer, song.album, song.platform_name].filter(Boolean).join(' · ')
      : '—';
    $('#flyBackend').textContent = backend;
    $('#flyRequester').textContent = song && pb.requester ? `@${pb.requester}` : '—';
    setCover($('#flyoutCover'), song ? song.name : '—', 'xl');
    $('#flyCur').textContent = pos;
    $('#flyDur').textContent = dur;
    setProgress($('#flyBar'), pct);

    // 音量
    const volume = Math.max(0, Math.min(100, Number(pb.volume ?? 100)));
    const fill = $('#volFill');
    if (fill) fill.style.width = `${volume}%`;

    setPlayIcon(playing);
  }

  function renderQueue(queue) {
    if (!queue) return;
    const pending = queue.pending || [];
    const size = Number(queue.size || 0);

    setCounter($('#queueChip'), size);
    const badge = $('#navQueueBadge');
    if (badge) { badge.textContent = String(size); badge.style.display = size ? 'grid' : 'none'; }
    $('#dashQueueHint').textContent = `待播 ${pending.length} 首`;
    $('#queuePendingCount').textContent = String(pending.length);
    $('#queueMeta').textContent = `待播 ${pending.length} 首 · 共 ${size} 项 · 持久化到 queue.json`;
    $('#queueEmpty').style.display = pending.length ? 'none' : '';

    const dash = $('#dashQueue');
    dash.textContent = '';
    if (!pending.length) {
      const row = document.createElement('div');
      row.className = 'row';
      const meta = document.createElement('div');
      meta.className = 'meta';
      const t = document.createElement('div'); t.className = 't'; t.textContent = '队列为空';
      const s = document.createElement('div'); s.className = 's'; s.textContent = '弹幕发送 /play 歌名 即可点歌';
      meta.append(t, s);
      row.append(meta);
      dash.append(row);
    }
    pending.slice(0, 3).forEach((item, i) => {
      dash.append(queueRow(item, String(i + 1).padStart(2, '0'), false));
    });

    const list = $('#queueList');
    list.textContent = '';
    pending.forEach((item, i) => {
      list.append(queueRow(item, String(i + 1).padStart(2, '0'), true));
    });

    const fly = $('#flyNext');
    fly.textContent = '';
    if (!pending.length) {
      const row = document.createElement('div');
      row.className = 'row';
      const meta = document.createElement('div');
      meta.className = 'meta';
      const t = document.createElement('div'); t.className = 't'; t.textContent = '暂无待播歌曲';
      meta.append(t);
      row.append(meta);
      fly.append(row);
    }
    pending.slice(0, 3).forEach(item => {
      const row = document.createElement('div');
      row.className = 'row';
      row.append(coverNode(item.song));
      const meta = document.createElement('div');
      meta.className = 'meta';
      const t = document.createElement('div'); t.className = 't'; t.textContent = item.song.name;
      const s = document.createElement('div'); s.className = 's';
      s.textContent = [item.song.singer, item.requester ? `@${item.requester}` : '', item.song.interval].filter(Boolean).join(' · ');
      meta.append(t, s);
      row.append(meta);
      fly.append(row);
    });
  }

  function coverNode(song) {
    const el = document.createElement('div');
    el.className = `cover ${coverClass(song && song.name)}`;
    el.textContent = coverText(song && song.name);
    return el;
  }

  function queueRow(item, label, withActions) {
    const song = item.song || {};
    const row = document.createElement('div');
    row.className = 'row';
    const idx = document.createElement('span');
    idx.className = 'idx'; idx.textContent = label;
    row.append(idx, coverNode(song));

    const meta = document.createElement('div');
    meta.className = 'meta';
    const t = document.createElement('div'); t.className = 't'; t.textContent = song.name || '—';
    const s = document.createElement('div'); s.className = 's';
    s.textContent = [song.singer, item.requester ? `@${item.requester}` : '', song.interval].filter(Boolean).join(' · ');
    meta.append(t, s);
    row.append(meta);

    const tag = document.createElement('span');
    tag.className = 'tag';
    tag.textContent = song.platform_name || song.platform || '—';
    row.append(tag);

    if (withActions) {
      const actions = document.createElement('div');
      actions.className = 'actions';
      actions.append(
        iconBtn('#i-pin', '置顶', { pin: String(item.index) }),
        iconBtn('#i-trash', '移除', { remove: String(item.index), del: '1' }),
      );
      row.append(actions);
    }
    return row;
  }

  function iconBtn(icon, title, data) {
    const btn = document.createElement('button');
    btn.className = `icon-btn${data.del ? ' del' : ''}`;
    btn.title = title;
    btn.setAttribute('aria-label', title);
    Object.entries(data).forEach(([key, value]) => {
      if (key !== 'del') btn.dataset[key] = value;
    });
    btn.innerHTML = `<svg stroke-linecap="round" stroke-linejoin="round"><use href="${icon}"/></svg>`;
    return btn;
  }

  function renderStats(stats) {
    if (!stats) return;
    setCounter($('#kpiSongs'), stats.songs_today || 0);
    setCounter($('#liveSongs'), stats.songs_today || 0);
    setCounter($('#kpiRate'), stats.danmaku_rate || 0);
    setCounter($('#liveRate'), stats.danmaku_rate || 0);
    $('#dmRate').textContent = `${stats.danmaku_rate || 0} 条 / 分`;

    const uptime = Number(stats.uptime || 0);
    const hours = Math.floor(uptime / 3600);
    const minutes = Math.floor((uptime % 3600) / 60);
    setCounter($('#kpiUptime'), hours >= 1 ? hours : minutes);
    $('#kpiUptimeUnit').textContent = hours >= 1 ? 'h' : 'm';

    renderTop(stats.top || []);
  }

  function renderTop(top) {
    const bars = $('#topBars');
    bars.textContent = '';
    if (!top.length) {
      const hint = document.createElement('div');
      hint.className = 'empty-hint';
      hint.style.marginTop = '0';
      hint.textContent = '暂无点歌记录。';
      bars.append(hint);
    } else {
      const max = Math.max(1, ...top.map(it => Number(it.count) || 0));
      top.forEach((item, i) => {
        const row = document.createElement('div');
        row.className = 'bar-row';
        const rk = document.createElement('span');
        rk.className = 'rk'; rk.textContent = String(i + 1).padStart(2, '0');
        const track = document.createElement('div');
        track.className = 'bar-track';
        const fill = document.createElement('span');
        fill.className = 'bar-fill';
        fill.style.setProperty('--v', `${(Number(item.count) / max) * 100}%`);
        track.append(fill);
        const num = document.createElement('span');
        num.className = 'num'; num.textContent = fmt(item.count);
        row.append(rk, track, num);
        bars.append(row);
      });
    }

    const list = $('#reqTopList');
    list.textContent = '';
    if (!top.length) {
      const row = document.createElement('div');
      row.className = 'row';
      const meta = document.createElement('div');
      meta.className = 'meta';
      const t = document.createElement('div'); t.className = 't'; t.textContent = '暂无记录';
      meta.append(t);
      row.append(meta);
      list.append(row);
    }
    top.forEach((item, i) => {
      const row = document.createElement('div');
      row.className = 'row';
      const idx = document.createElement('span');
      idx.className = 'idx'; idx.textContent = String(i + 1).padStart(2, '0');
      const meta = document.createElement('div');
      meta.className = 'meta';
      const t = document.createElement('div'); t.className = 't'; t.textContent = item.name;
      const s = document.createElement('div'); s.className = 's'; s.textContent = `点歌 ${item.count} 次`;
      meta.append(t, s);
      row.append(idx, meta);
      list.append(row);
    });
  }

  function renderActivity(activity) {
    const values = Array.isArray(activity) ? activity : [];
    if (!values.length) return;
    const path = $('#activityPath');
    if (path) path.setAttribute('d', sparkPath(values, 600, 74, 8));
    const live = $('#livePath');
    if (live) live.setAttribute('d', sparkPath(values.slice(-10), 300, 60, 8));
    const peak = Math.max(0, ...values);
    const total = values.reduce((a, b) => a + b, 0);
    $('#activityPeak').textContent = `峰值 ${peak}/10s`;
    $('#activityMeta').textContent = `${total} 条`;
  }

  function sparkPath(values, width, height, pad) {
    const max = Math.max(1, ...values);
    const step = values.length > 1 ? width / (values.length - 1) : width;
    return values.map((v, i) => {
      const x = (i * step).toFixed(1);
      const y = (height - pad - (Number(v) / max) * (height - pad * 2)).toFixed(1);
      return `${i === 0 ? 'M' : 'L'}${x} ${y}`;
    }).join(' ');
  }

  function renderRoom(snap) {
    const room = snap.room || {};
    const accepting = Boolean(room.accepting);
    $('#tbRoomText').textContent = `直播间 ${room.id || '—'} · ${room.connected ? '已连接' : '未连接'}`;
    const dot = $('#tbDot');
    if (dot) dot.style.background = room.connected ? 'var(--ok)' : 'var(--err)';
    setCounter($('#viewers'), snap.viewers || 0);

    $('#liveRoom').textContent = room.id || '—';
    $('#liveConnected').textContent = room.connected ? '已连接' : '未连接';
    $('#liveAccepting').textContent = accepting ? '接单中' : '已暂停';
    $('#liveConnTag').textContent = room.connected ? '在线' : '离线';

    const btn = $('#botToggle');
    if (btn) {
      btn.dataset.accepting = accepting ? '1' : '0';
      const use = $('use', btn);
      if (use) use.setAttribute('href', accepting ? '#i-pause' : '#i-play');
      const label = $('span', btn);
      if (label) label.textContent = accepting ? '暂停接单' : '开始接单';
    }
  }

  function applySnapshot(snap) {
    state.snapshot = snap;
    renderRoom(snap);
    renderPlayback(snap.playback);
    renderQueue(snap.queue);
    renderStats(snap.stats);
    renderActivity(snap.activity);

    const sources = snap.sources || {};
    $('#reqSourcesTag').textContent = sources.ready === sources.total && sources.total ? '全部就绪' : '部分就绪';
    $('#reqSourcesTotal').textContent = String(sources.total || 0);
    $('#reqSourcesReady').textContent = String(sources.ready || 0);
    $('#reqSourcesPlatforms').textContent = (sources.platforms || []).length
      ? (sources.platforms || []).join(' / ')
      : '—';

    const rate = Number(snap.stats && snap.stats.danmaku_rate || 0);
    const liveBadge = $('#navLiveBadge');
    if (liveBadge) { liveBadge.textContent = String(rate); liveBadge.style.display = rate ? 'grid' : 'none'; }

    const recent = snap.danmaku || [];
    if (!state.lastDmId && recent.length) {
      state.danmaku = recent.slice(-200);
      state.lastDmId = state.danmaku[state.danmaku.length - 1].id;
      renderFeed();
    }

    if (!state.settingsLoaded && snap.room) {
      // 设置页首次展示时再拉取，避免启动瞬间的额外请求
    }
  }

  /* ---------------------------------------------------------------- 弹幕 */

  function renderFeed() {
    const feed = $('#dmFeed');
    if (!feed) return;
    const visible = state.danmaku.filter(item => item.kind === 'request' || state.dmFilter === 'all');
    feed.textContent = '';
    visible.slice(-60).forEach(item => {
      const row = document.createElement('div');
      row.className = `dm${item.kind === 'request' ? ' type-request' : ''}`;
      const who = document.createElement('span');
      who.className = 'who'; who.textContent = item.user;
      const what = document.createElement('span');
      what.className = 'what'; what.textContent = item.text;
      row.append(who, what);
      feed.append(row);
    });
    feed.scrollTop = feed.scrollHeight;
  }

  async function fetchDanmaku() {
    const res = await api(`/api/danmaku?after=${state.lastDmId}`);
    if (!res.ok || !res.data || !res.data.ok) return;
    const items = res.data.items || [];
    if (!items.length) return;
    state.lastDmId = items[items.length - 1].id;
    state.danmaku = state.danmaku.concat(items).slice(-200);
    renderFeed();
  }

  async function sendDanmaku() {
    const input = $('#dmInput');
    const text = input.value.trim();
    if (!text) { toast('请输入弹幕内容'); input.focus(); return; }
    const res = await api('/api/live', 'POST', { text });
    if (!res.ok || !res.data || !res.data.ok) { toast(desc(res)); return; }
    input.value = '';
    $('#dmCounter').textContent = '0 / 20';
    toast('弹幕已发送');
  }

  /* ---------------------------------------------------------------- 日志 */

  function logLevelClass(level) {
    const lv = String(level || '').toUpperCase();
    if (lv.startsWith('WARN')) return { cls: 'warn', text: 'WARN' };
    if (lv.startsWith('ERR') || lv.startsWith('CRIT')) return { cls: 'err', text: 'ERR' };
    if (lv === 'DEBUG') return { cls: 'dbg', text: 'DBG' };
    return { cls: 'info', text: 'INFO' };
  }

  function filteredLogs() {
    return state.logs.filter(item => {
      const lv = String(item.level || '').toUpperCase();
      if (state.logLevel === 'INFO' && lv !== 'INFO') return false;
      if (state.logLevel === 'WARN' && !lv.startsWith('WARN')) return false;
      if (state.logLevel === 'ERROR' && !(lv.startsWith('ERR') || lv.startsWith('CRIT'))) return false;
      if (state.logKeyword) {
        const hay = `${item.name || ''} ${item.msg || ''}`.toLowerCase();
        if (!hay.includes(state.logKeyword.toLowerCase())) return false;
      }
      return true;
    });
  }

  function renderLogs() {
    const panel = $('#logPanel');
    if (!panel) return;
    const list = filteredLogs();
    panel.textContent = '';
    if (!list.length) {
      const hint = document.createElement('div');
      hint.className = 'empty-hint';
      hint.style.marginTop = '0';
      hint.textContent = state.logs.length ? '没有匹配的日志。' : '等待后端日志…';
      panel.append(hint);
      return;
    }
    list.slice(-300).forEach(item => {
      const row = document.createElement('div');
      row.className = 'log-line';
      const ts = document.createElement('span');
      ts.className = 'ts'; ts.textContent = clockTime(item.ts);
      const lv = logLevelClass(item.level);
      const lvEl = document.createElement('span');
      lvEl.className = `lv ${lv.cls}`; lvEl.textContent = lv.text;
      const msg = document.createElement('span');
      msg.className = 'msg'; msg.textContent = `[${item.name || 'bot'}] ${item.msg || ''}`;
      row.append(ts, lvEl, msg);
      panel.append(row);
    });
    if (state.logAuto) panel.scrollTop = panel.scrollHeight;
  }

  async function fetchLogs() {
    const res = await api(`/api/logs?after=${state.lastLogId}&limit=200`);
    if (!res.ok || !res.data || !res.data.ok) return;
    const items = res.data.items || [];
    if (!items.length) return;
    state.lastLogId = items[items.length - 1].id;
    state.logs = state.logs.concat(items).slice(-300);
    renderLogs();
  }

  /* ---------------------------------------------------------------- 搜索 / 点歌 */

  function renderSearch() {
    const results = state.searchResults.filter(item =>
      state.searchPlatform === 'all' || item.platform === state.searchPlatform);
    const grid = $('#searchResults');
    grid.textContent = '';
    if (!results.length) {
      const hint = document.createElement('div');
      hint.className = 'empty-hint';
      hint.style.marginTop = '0';
      hint.textContent = state.searchSuggestion
        ? `未直接命中，建议尝试：${state.searchSuggestion.name} - ${state.searchSuggestion.singer}`
        : '输入歌名或歌手开始搜索。';
      grid.append(hint);
      $('#searchHint').textContent = state.searchSuggestion ? '未命中' : '输入歌名或歌手开始搜索';
      return;
    }
    $('#searchHint').textContent = `${results.length} 条命中 · 按置信度排序`;
    results.forEach(item => {
      const row = document.createElement('div');
      row.className = 'row';
      row.append(coverNode(item));
      const meta = document.createElement('div');
      meta.className = 'meta';
      const t = document.createElement('div'); t.className = 't'; t.textContent = item.name;
      const s = document.createElement('div'); s.className = 's';
      s.textContent = [item.singer, item.album, item.interval].filter(Boolean).join(' · ');
      meta.append(t, s);
      row.append(meta);

      const pTag = document.createElement('span');
      pTag.className = 'tag'; pTag.textContent = item.platform_name || item.platform || '—';
      const scoreTag = document.createElement('span');
      scoreTag.className = `tag${Number(item.score) < 80 ? ' amber' : ''}`;
      scoreTag.textContent = `置信 ${item.score ?? '—'}`;
      row.append(pTag, scoreTag);

      const actions = document.createElement('div');
      actions.className = 'actions';
      const btn = document.createElement('button');
      btn.className = 'btn primary sm';
      btn.innerHTML = '<svg stroke-linecap="round" stroke-linejoin="round"><use href="#i-plus"/></svg>点歌';
      btn.addEventListener('click', () => enqueueSong(item, btn));
      actions.append(btn);
      row.append(actions);
      grid.append(row);
    });
  }

  function renderPlatformChips() {
    const row = $('#platformChips');
    row.textContent = '';
    const all = document.createElement('button');
    all.className = `chip${state.searchPlatform === 'all' ? ' on' : ''}`;
    all.dataset.platform = 'all';
    all.textContent = '全部平台';
    row.append(all);

    const seen = new Map();
    state.searchResults.forEach(item => {
      if (item.platform) seen.set(item.platform, item.platform_name || item.platform);
    });
    seen.forEach((label, code) => {
      const chip = document.createElement('button');
      chip.className = `chip${state.searchPlatform === code ? ' on' : ''}`;
      chip.dataset.platform = code;
      chip.textContent = label;
      row.append(chip);
    });

    const spacer = document.createElement('span');
    spacer.className = 'spacer';
    row.append(spacer);
  }

  async function doSearch() {
    const input = $('#searchInput');
    const query = input.value.trim();
    if (!query) { toast('请输入歌名或歌手'); input.focus(); return; }
    $('#searchBtn').disabled = true;
    $('#searchHint').textContent = '搜索中…';
    state.searchSuggestion = null;
    try {
      const res = await api('/api/search', 'POST', { query });
      if (!res.ok || !res.data || !res.data.ok) {
        state.searchResults = [];
        renderSearch();
        toast(desc(res));
        return;
      }
      state.searchResults = res.data.results || [];
      state.searchSuggestion = res.data.suggestion || null;
      state.searchPlatform = 'all';
      renderPlatformChips();
      renderSearch();
    } finally {
      $('#searchBtn').disabled = false;
    }
  }

  async function enqueueSong(song, btn) {
    if (btn) btn.disabled = true;
    const res = await api('/api/play', 'POST', { song, requester: '主播' });
    if (btn) btn.disabled = false;
    if (!res.ok || !res.data || !res.data.ok) { toast(desc(res)); return; }
    toast(`已加入队列：${res.data.song ? res.data.song.name : (song.name || '')}`);
    fetchSnapshot(true);
  }

  /* ---------------------------------------------------------------- 队列 / 播放控制 */

  async function queueAction(path, body) {
    const res = await api(path, 'POST', body || {});
    if (!res.ok || !res.data || !res.data.ok) { toast(desc(res)); return null; }
    if (res.data.queue) renderQueue(res.data.queue);
    if (res.data.playback) renderPlayback(res.data.playback);
    fetchSnapshot(true);
    return res.data;
  }

  async function clearQueue() {
    const snap = state.snapshot;
    if (!snap || !snap.queue || !snap.queue.size) { toast('队列已经为空'); return; }
    const data = await queueAction('/api/queue/clear');
    if (data) toast('队列已清空（正在播放保留）');
  }

  async function skipCurrent() {
    const data = await queueAction('/api/queue/skip');
    if (data) toast('已切歌，正在播放下一首');
  }

  async function togglePlayback() {
    const pb = state.snapshot && state.snapshot.playback;
    if (!pb || !pb.song) { toast('当前没有播放的歌曲'); return; }
    const path = pb.paused ? '/api/player/resume' : '/api/player/pause';
    const res = await api(path, 'POST', {});
    if (!res.ok || !res.data || !res.data.ok) { toast(desc(res)); return; }
    if (res.data.playback) renderPlayback(res.data.playback);
    fetchSnapshot(true);
  }

  async function setVolume(volume) {
    const value = Math.max(0, Math.min(100, Math.round(volume)));
    const res = await api('/api/player/volume', 'POST', { volume: value });
    if (!res.ok || !res.data || !res.data.ok) { toast(desc(res)); return; }
    if (res.data.playback) renderPlayback(res.data.playback);
  }

  async function toggleAccepting() {
    const btn = $('#botToggle');
    const next = btn.dataset.accepting !== '1';
    const res = await api('/api/bot/accepting', 'POST', { accepting: next });
    if (!res.ok || !res.data || !res.data.ok) { toast(desc(res)); return; }
    toast(next ? '已恢复接单' : '已暂停接单，队列保留');
    fetchSnapshot(true);
  }

  /* ---------------------------------------------------------------- 音源 */

  function renderSources() {
    const grid = $('#sourceGrid');
    if (!grid) return;
    const data = state.sources || { total: 0, ready: 0, platforms: [], items: [] };
    const items = (data.items || []).filter(item => !state.healthyOnly || item.ready);
    $('#srcMeta').textContent = `${data.total || 0} 个音源进程 · 就绪 ${data.ready || 0} · 覆盖 ${(data.platforms || []).length} 平台`;
    grid.textContent = '';
    if (!items.length) {
      const hint = document.createElement('div');
      hint.className = 'empty-hint';
      hint.textContent = '没有符合条件的音源。';
      grid.append(hint);
      return;
    }
    items.forEach(item => {
      const card = document.createElement('div');
      card.className = 'src';
      const top = document.createElement('div');
      top.className = 'src-top';
      const logo = document.createElement('span');
      logo.className = `src-logo ${coverClass(item.name)}`;
      logo.textContent = coverText(item.name);
      const info = document.createElement('div');
      const name = document.createElement('div'); name.className = 'src-name'; name.textContent = item.name;
      const sub = document.createElement('div'); sub.className = 'src-sub';
      sub.textContent = `${(item.sources || []).length} 平台 · ${item.file}`;
      info.append(name, sub);
      const health = document.createElement('span');
      health.className = `health ${item.ready ? 'ok' : 'off'}`;
      health.style.marginLeft = 'auto';
      const dot = document.createElement('span'); dot.className = 'd';
      health.append(dot, document.createTextNode(item.ready ? '就绪' : '未就绪'));
      top.append(logo, info, health);

      const metrics = document.createElement('div');
      metrics.className = 'src-metrics';
      metrics.append(
        metric(item.ready ? 'ready' : '—', '状态'),
        metric(String((item.sources || []).length), '平台数'),
        metric((item.sources || []).slice(0, 3).join(' / ') || '—', '覆盖'),
      );
      card.append(top, metrics);
      grid.append(card);
    });
  }

  function metric(value, label) {
    const box = document.createElement('div');
    box.className = 'metric';
    const b = document.createElement('b'); b.textContent = value;
    box.append(b, document.createTextNode(label));
    return box;
  }

  async function fetchSources() {
    const res = await api('/api/sources');
    if (!res.ok || !res.data || !res.data.ok) return;
    state.sources = res.data;
    renderSources();
  }

  async function refreshSources() {
    await fetchSources();
    const data = state.sources;
    toast(data ? `音源已刷新：就绪 ${data.ready}/${data.total}` : '音源刷新失败');
  }

  /* ---------------------------------------------------------------- 设置 */

  const QUALITY_LABELS = {
    flac24bit: '无损 24bit',
    flac: '无损',
    '320k': '320k',
    '192k': '192k',
    '128k': '128k',
  };

  async function loadSettings() {
    const res = await api('/api/settings');
    if (!res.ok || !res.data || !res.data.ok) return;
    state.settings = res.data.settings;
    state.settingsLoaded = true;
    populateSettings(state.settings);
  }

  function populateSettings(s) {
    if (!s) return;
    $('#setSessdata').value = '';
    $('#setJct').value = '';
    $('#setBuvid3').value = '';
    $('#setSessdata').placeholder = s.has_sessdata ? `当前 ${s.sessdata_hint}（留空不修改）` : '未配置';
    $('#setJct').placeholder = s.jct_hint ? `当前 ${s.jct_hint}（留空不修改）` : '未配置';
    $('#setBuvid3').placeholder = s.buvid3_hint ? `当前 ${s.buvid3_hint}（留空不修改）` : '未配置';
    $('#sessHint').textContent = s.has_sessdata ? '保存后自动热重载，无需重启' : '尚未配置 SESSDATA，无法发送弹幕';
    $('#buvidHint').textContent = s.buvid3_hint ? '风控校验；保存后热更新' : '缺失时启动自动获取';
    const cookieTag = $('#cookieTag');
    cookieTag.textContent = s.cookie_valid ? '有效' : (s.has_sessdata ? '已失效' : '未配置');

    $('#setRoom').value = s.room_id || '';
    $('#setRoomCurrent').textContent = s.room_id ? `房间 ${s.room_id}` : '—';

    $$('#setBackend button').forEach(btn => {
      btn.classList.toggle('on', btn.dataset.backend === (s.player_backend || 'ffplay'));
    });

    const quality = $('#setQuality');
    quality.textContent = '';
    (s.qualities || []).forEach(code => {
      const option = document.createElement('option');
      option.value = code;
      option.textContent = QUALITY_LABELS[code] || code;
      quality.append(option);
    });
    quality.value = s.min_quality || 'flac24bit';

    $('#setLyrics').classList.toggle('on', Boolean(s.enable_lyrics_search));
    $('#setMaxMsg').value = String(s.max_msg_per_minute ?? 20);
    $('#setInterval').value = String(s.danmaku_interval ?? 5);
    $('#setApiPort').value = String(s.api_port ?? 8790);
    $('#apiPortHint').textContent = String(s.api_port ?? '—');
    $('#saveHint').textContent = '设置来自 config.json';
  }

  async function saveSettings() {
    const body = {
      bili_sessdata: $('#setSessdata').value.trim(),
      bili_jct: $('#setJct').value.trim(),
      bili_buvid3: $('#setBuvid3').value.trim(),
      bili_room_id: $('#setRoom').value.trim(),
      player_backend: $('#setBackend button.on') ? $('#setBackend button.on').dataset.backend : '',
      min_quality: $('#setQuality').value,
      enable_lyrics_search: $('#setLyrics').classList.contains('on'),
      max_msg_per_minute: Number($('#setMaxMsg').value),
      danmaku_interval: Number($('#setInterval').value),
      api_port: Number($('#setApiPort').value),
    };
    if (!body.bili_sessdata) delete body.bili_sessdata;
    if (!body.bili_jct) delete body.bili_jct;
    if (!body.bili_buvid3) delete body.bili_buvid3;
    if (!body.bili_room_id) delete body.bili_room_id;

    const res = await api('/api/settings', 'PUT', body);
    if (!res.ok || !res.data || !res.data.ok) { toast(desc(res)); return; }
    if (res.data.settings) {
      state.settings = res.data.settings;
      populateSettings(res.data.settings);
    }
    const restart = res.data.restart_required || [];
    toast(restart.length ? `已保存；需重启后端：${restart.join('、')}` : '设置已保存并热更新');
  }

  /* ---------------------------------------------------------------- 下载 / 复制 */

  function download(filename, text) {
    const blob = new Blob([text], { type: 'text/plain;charset=utf-8' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = filename;
    a.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }

  function exportQueue() {
    const queue = state.snapshot && state.snapshot.queue;
    const lines = ['# SongBot 歌单'];
    if (queue && queue.current && queue.current.song) {
      const s = queue.current.song;
      lines.push(`正在播放\t${s.name}\t${s.singer}\t${queue.current.requester ? '@' + queue.current.requester : ''}`);
    }
    ((queue && queue.pending) || []).forEach((item, i) => {
      const s = item.song;
      lines.push(`${i + 1}\t${s.name}\t${s.singer}\t${item.requester ? '@' + item.requester : ''}`);
    });
    if (lines.length === 1) { toast('队列为空，没有可导出的歌曲'); return; }
    const stamp = new Date().toISOString().slice(0, 10);
    download(`songbot-queue-${stamp}.txt`, lines.join('\n'));
    toast('歌单已导出');
  }

  function exportLogs() {
    const list = filteredLogs();
    if (!list.length) { toast('当前没有可导出的日志'); return; }
    const text = list.map(item =>
      `${clockTime(item.ts)} [${item.level}] [${item.name}] ${item.msg}`).join('\n');
    download(`songbot-logs-${new Date().toISOString().slice(0, 10)}.txt`, text);
    toast('日志已导出');
  }

  async function copyConn() {
    let port = state.backend.port;
    if (!port && bridge && typeof bridge.getBackendState === 'function') {
      const st = await bridge.getBackendState();
      port = st && st.port;
    }
    if (!port) { toast('后端端口尚不可用'); return; }
    const text = `SongBot 本地接口：http://127.0.0.1:${port}`;
    try {
      await navigator.clipboard.writeText(text);
      toast('连接信息已复制');
    } catch {
      toast(text);
    }
  }

  /* ---------------------------------------------------------------- 轮询 */

  let tick = 0;
  let polling = false;

  async function fetchSnapshot(force = false) {
    if (polling && !force) return;
    polling = true;
    try {
      const res = await api('/api/snapshot');
      if (res.ok && res.data && res.data.ok) {
        applySnapshot(res.data);
        if (!bridge && state.backend.status !== 'online') {
          applyBackendState({ status: 'online', port: Number((browserBase || '').split(':').pop()) || null, external: true });
        }
      }
    } finally {
      polling = false;
    }
    tick += 1;
    fetchDanmaku().catch(() => {});
    fetchLogs().catch(() => {});
    if (tick % 5 === 0) fetchSources().catch(() => {});
  }

  /* ---------------------------------------------------------------- 播放详情浮层（FLIP） */

  const playerOverlay = $('#playerOverlay');
  const flyoutCover = $('#flyoutCover');

  function openPlayer(sourceEl) {
    if (!playerOverlay || !sourceEl) return;
    const first = sourceEl.getBoundingClientRect();
    sourceEl.classList.add('flying');
    playerOverlay.classList.add('open');
    const last = flyoutCover.getBoundingClientRect();
    const dx = first.left - last.left;
    const dy = first.top - last.top;
    const sx = first.width / last.width;
    const sy = first.height / last.height;
    flyoutCover.style.transformOrigin = '0 0';
    flyoutCover.style.transform = `translate(${dx}px, ${dy}px) scale(${sx}, ${sy})`;
    flyoutCover.style.transition = 'none';
    void flyoutCover.offsetWidth;
    flyoutCover.style.transition = 'transform 300ms var(--ease-decelerate), border-radius 300ms var(--ease-decelerate)';
    flyoutCover.style.transform = 'translate(0,0) scale(1,1)';
    setTimeout(() => {
      flyoutCover.style.transition = '';
      flyoutCover.style.transform = '';
    }, 320);
  }

  function closePlayer() {
    if (!playerOverlay) return;
    playerOverlay.classList.remove('open');
    $$('.flip-source.flying').forEach(el => el.classList.remove('flying'));
  }

  /* ---------------------------------------------------------------- 音量拖动 */

  const volTrack = $('#volTrack');
  let dragging = false;

  function volumeFromEvent(ev) {
    const rect = volTrack.getBoundingClientRect();
    const ratio = (ev.clientX - rect.left) / rect.width;
    return Math.max(0, Math.min(100, ratio * 100));
  }

  if (volTrack) {
    volTrack.addEventListener('pointerdown', ev => {
      dragging = true;
      volTrack.setPointerCapture(ev.pointerId);
      const fill = $('#volFill');
      if (fill) fill.style.width = `${volumeFromEvent(ev)}%`;
    });
    volTrack.addEventListener('pointermove', ev => {
      if (!dragging) return;
      const fill = $('#volFill');
      if (fill) fill.style.width = `${volumeFromEvent(ev)}%`;
    });
    volTrack.addEventListener('pointerup', ev => {
      if (!dragging) return;
      dragging = false;
      const volume = volumeFromEvent(ev);
      setVolume(volume);
      try { volTrack.releasePointerCapture(ev.pointerId); } catch { /* 忽略 */ }
    });
    volTrack.addEventListener('pointercancel', () => { dragging = false; });
  }

  /* ---------------------------------------------------------------- 委托交互 */

  document.addEventListener('click', ev => {
    const target = ev.target.closest('button, .chip, .switch, .cmd, .nav-item, .icon-btn, [data-open-player]');
    if (!target) return;

    if (target.hasAttribute('data-open-player')) {
      const src = target.closest('.flip-source') || target;
      openPlayer(src);
      return;
    }

    if (target.dataset.nav) { switchPage(target.dataset.nav); return; }

    // 平台筛选
    if (target.dataset.platform) {
      state.searchPlatform = target.dataset.platform;
      renderPlatformChips();
      renderSearch();
      return;
    }

    // 弹幕过滤
    if (target.dataset.dmFilter) {
      state.dmFilter = target.dataset.dmFilter;
      $$('.chips-row .chip', $('#page-live')).forEach(chip => {
        chip.classList.toggle('on', chip.dataset.dmFilter === state.dmFilter);
      });
      renderFeed();
      return;
    }

    // 日志级别
    if (target.dataset.level) {
      state.logLevel = target.dataset.level;
      $$('#logLevels button').forEach(btn => btn.classList.toggle('on', btn === target));
      renderLogs();
      return;
    }

    // 设置：播放后端
    if (target.closest('#setBackend') && target.tagName === 'BUTTON') {
      $$('#setBackend button').forEach(btn => btn.classList.toggle('on', btn === target));
      return;
    }

    // 队列操作
    if (target.dataset.pin !== undefined) {
      queueAction('/api/queue/pin', { index: Number(target.dataset.pin) })
        .then(data => { if (data) toast('已置顶到队首'); });
      return;
    }
    if (target.dataset.remove !== undefined) {
      const row = target.closest('.row');
      if (row) {
        row.classList.add('removing');
        setTimeout(() => row.remove(), 240);
      }
      queueAction('/api/queue/remove', { index: Number(target.dataset.remove) })
        .then(data => { if (data) toast('已从队列移除'); });
      return;
    }

    const action = target.dataset.action;
    if (action === 'clear-queue') { clearQueue(); return; }
    if (action === 'export-queue') { exportQueue(); return; }
    if (action === 'export-logs') { exportLogs(); return; }
    if (action === 'refresh-sources') {
      refreshSources();
      if (target.dataset.toast) target.dataset.toast = '';
      return;
    }
    if (action === 'copy-conn') { copyConn(); return; }

    if (target.id === 'srcHealthyOnly') {
      state.healthyOnly = !state.healthyOnly;
      target.classList.toggle('on', state.healthyOnly);
      renderSources();
      return;
    }

    if (target.id === 'logAuto') {
      state.logAuto = !state.logAuto;
      target.classList.toggle('on', state.logAuto);
      if (state.logAuto) {
        const panel = $('#logPanel');
        panel.scrollTop = panel.scrollHeight;
      }
      return;
    }

    if (target.id === 'botToggle') { toggleAccepting(); return; }
    if (target.id === 'searchBtn') { doSearch(); return; }
    if (target.id === 'dmSend') { sendDanmaku(); return; }
    if (target.id === 'saveBtn') { saveSettings(); return; }
    if (target.id === 'saveReset') { loadSettings().then(() => toast('已恢复为已保存配置')); return; }

    if (target.id === 'pbPlay' || target.id === 'npToggle' || target.id === 'qToggle'
      || target.id === 'flyoutPlay') {
      togglePlayback();
      return;
    }
    if (target.id === 'pbSkip' || target.id === 'npSkip' || target.id === 'qSkip'
      || target.id === 'flyoutSkip') {
      skipCurrent();
      return;
    }

    if (target.classList.contains('switch')) {
      target.classList.toggle('on');
      return;
    }

    if (target.closest('.segmented') && target.tagName === 'BUTTON') {
      $$('button', target.closest('.segmented')).forEach(b => b.classList.toggle('on', b === target));
      return;
    }

    if (target.classList.contains('chip')) {
      target.classList.toggle('on');
    }
  });

  document.addEventListener('keydown', ev => {
    if (ev.key === 'Enter') {
      if (ev.target && ev.target.id === 'searchInput') { ev.preventDefault(); doSearch(); }
      if (ev.target && ev.target.id === 'dmInput') { ev.preventDefault(); sendDanmaku(); }
    }
    if (ev.key !== 'Escape') return;
    if (playerOverlay.classList.contains('open')) closePlayer();
  });

  if (playerOverlay) playerOverlay.addEventListener('click', ev => { if (ev.target === playerOverlay) closePlayer(); });

  $('#dmInput').addEventListener('input', ev => {
    $('#dmCounter').textContent = `${ev.target.value.length} / 20`;
  });

  $('#logFilter').addEventListener('input', ev => {
    state.logKeyword = ev.target.value.trim();
    renderLogs();
  });

  $('#searchInput').addEventListener('keydown', ev => {
    if (ev.key === 'Enter') { ev.preventDefault(); doSearch(); }
  });

  /* ---------------------------------------------------------------- 初始化 */

  requestAnimationFrame(() => {
    staggerIn($(`#page-${current}`));
    runCountUp($(`#page-${current}`));
  });

  fetchSnapshot(true).catch(() => {});
  setInterval(() => { fetchSnapshot(true).catch(() => {}); }, 1000);
})();
