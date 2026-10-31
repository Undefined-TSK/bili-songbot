/* ============================================================
   SongBot Desktop · Fluent 2 交互层
   动效：100/150/250/280ms · standard / decelerate / accelerate
   ============================================================ */
(() => {
  'use strict';

  const ORDER = ['dashboard', 'request', 'queue', 'live', 'sources', 'logs'];
  const $ = (sel, root = document) => root.querySelector(sel);
  const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

  const pages = $$('.page');
  const navItems = $$('.nav-item');
  const pageTitle = $('#pageTitle');
  const pageEyebrow = $('#pageEyebrow');
  const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;

  let current = 'dashboard';
  let isPlaying = true;

  /* 页内 stagger：仅首次进入该页，20ms/项，序号上限 6（总时长 ≤400ms） */
  const visited = new Set();
  function staggerIn(page) {
    if (!page || visited.has(page.id)) return;
    visited.add(page.id);
    Array.from(page.children).forEach((child, i) => {
      child.style.setProperty('--i', String(Math.min(i, 6)));
    });
    Array.from(page.querySelectorAll('.row')).forEach((row, i) => {
      row.style.setProperty('--i', String(Math.min(i, 6)));
    });
    page.classList.add('stagger');
    setTimeout(() => page.classList.remove('stagger'), 420);
  }

  /* ---------------- 页面切换（Frame.Navigate 心智） ---------------- */
  function switchPage(id, opts = {}) {
    if (!ORDER.includes(id)) return;
    if (id === current && !opts.force) return;

    const from = ORDER.indexOf(current);
    const to = ORDER.indexOf(id);
    const forward = to > from;
    const oldPage = $(`#page-${current}`);
    const newPage = $(`#page-${id}`);
    if (!newPage) return;

    current = id;

    if (oldPage) {
      oldPage.classList.remove('active', 'entering');
      if (!reducedMotion) {
        oldPage.style.setProperty('--exit-x', forward ? '-24px' : '24px');
        oldPage.classList.add('leaving');
        const leaving = oldPage;
        setTimeout(() => leaving.classList.remove('leaving'), 300);
      }
    }

    newPage.classList.remove('leaving');
    newPage.classList.add('active');
    if (!reducedMotion) {
      newPage.style.setProperty('--enter-x', forward ? '24px' : '-24px');
      newPage.classList.add('entering');
      setTimeout(() => newPage.classList.remove('entering'), 300);
    }
    staggerIn(newPage);
    newPage.scrollTop = 0;

    const item = navItems.find(i => i.dataset.page === id);
    if (item) {
      pageTitle.textContent = item.dataset.title || pageTitle.textContent;
      pageEyebrow.textContent = item.dataset.eyebrow || pageEyebrow.textContent;
      navItems.forEach(n => n.classList.toggle('active', n === item));
    }

    runCountUp(newPage);
  }

  navItems.forEach(item => {
    item.addEventListener('click', () => switchPage(item.dataset.page));
  });

  /* ---------------- 数字滚动（800ms，减速曲线） ---------------- */
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
  const fmt = n => (n >= 1000 ? n.toLocaleString('en-US') : String(n));

  /* ---------------- Toast ---------------- */
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

  /* ---------------- Wizard ---------------- */
  const wizard = $('#wizardOverlay');
  let wizStep = 0;
  const wizSteps = $$('.wiz-step', wizard || document);
  const wizDots = $$('.wiz-dots i', wizard || document);

  function setWizStep(n) {
    wizStep = Math.max(0, Math.min(wizSteps.length - 1, n));
    wizSteps.forEach((s, i) => s.classList.toggle('on', i === wizStep));
    wizDots.forEach((d, i) => d.classList.toggle('on', i === wizStep));
  }
  function openWizard() { setWizStep(0); wizard.classList.add('open'); }
  function closeWizard() { wizard.classList.remove('open'); }

  /* ---------------- 播放控制 ---------------- */
  const pbPlay = $('#pbPlay');
  const pbPlayIcon = $('#pbPlayIcon');
  function setPlaying(next) {
    isPlaying = next;
    $$('.wave').forEach(w => w.classList.toggle('paused', !isPlaying));
    if (pbPlayIcon) pbPlayIcon.querySelector('use').setAttribute('href', isPlaying ? '#i-pause' : '#i-play');
    const fp = $('#flyoutPlay use');
    if (fp) fp.setAttribute('href', isPlaying ? '#i-pause' : '#i-play');
  }
  setPlaying(true);

  /* ---------------- Connected Animation (FLIP)：封面 → 播放详情 ---------------- */
  const playerOverlay = $('#playerOverlay');
  const flyoutCover = $('#flyoutCover');

  function openPlayer(sourceEl) {
    if (!playerOverlay || !sourceEl) return;
    // First：记录源位置
    const first = sourceEl.getBoundingClientRect();
    // 标记源飞走
    sourceEl.classList.add('flying');
    // 显示浮层
    playerOverlay.classList.add('open');
    // Last：目标位置
    const last = flyoutCover.getBoundingClientRect();
    // Invert：把目标瞬移到源位置
    const dx = first.left - last.left;
    const dy = first.top - last.top;
    const sx = first.width / last.width;
    const sy = first.height / last.height;
    flyoutCover.style.transformOrigin = '0 0';
    flyoutCover.style.transform = `translate(${dx}px, ${dy}px) scale(${sx}, ${sy})`;
    flyoutCover.style.transition = 'none';
    // 强制 reflow
    void flyoutCover.offsetWidth;
    // Play：补间到目标
    flyoutCover.style.transition = `transform 300ms var(--ease-decelerate), border-radius 300ms var(--ease-decelerate)`;
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

  /* ---------------- 委托交互 ---------------- */
  document.addEventListener('click', ev => {
    const target = ev.target.closest('button, .chip, .switch, .cmd, .nav-item, .icon-btn, [data-open-player]');
    if (!target) return;

    // 播放详情浮层
    if (target.hasAttribute('data-open-player')) {
      const src = target.closest('.flip-source') || target;
      openPlayer(src);
      return;
    }

    if (target.dataset.nav) { switchPage(target.dataset.nav); return; }
    if (target.dataset.toast) toast(target.dataset.toast);

    if (target.hasAttribute('data-open-wizard')) { openWizard(); return; }
    if (target.hasAttribute('data-close-wizard')) { closeWizard(); return; }
    if (target.hasAttribute('data-wiz-next')) { setWizStep(wizStep + 1); return; }
    if (target.hasAttribute('data-wiz-prev')) { setWizStep(wizStep - 1); return; }

    if (target.classList.contains('switch')) {
      target.classList.toggle('on');
      return;
    }

    if (target.closest('.segmented') && target.tagName === 'BUTTON') {
      $$('button', target.closest('.segmented')).forEach(b => b.classList.toggle('on', b === target));
      return;
    }

    if (target.classList.contains('chip')) {
      const row = target.closest('.chips-row');
      if (row && !target.dataset.free) {
        $$('.chip', row).forEach(c => { if (c !== target && c.classList.contains('on') && !c.dataset.free) c.classList.remove('on'); });
      }
      target.classList.toggle('on');
      return;
    }

    if (target.classList.contains('del')) {
      const row = target.closest('.row');
      if (row) {
        row.classList.add('removing');
        setTimeout(() => row.remove(), 240);
      }
      toast('已从队列移除');
      return;
    }

    if (target === pbPlay || target.classList.contains('pb-play') || target.id === 'flyoutPlay') {
      setPlaying(!isPlaying);
      toast(isPlaying ? '继续播放' : '已暂停');
      return;
    }

    if (target.id === 'botToggle') {
      const paused = target.dataset.paused === '1';
      target.dataset.paused = paused ? '0' : '1';
      target.classList.toggle('primary', paused);
      target.querySelector('use').setAttribute('href', paused ? '#i-pause' : '#i-play');
      target.querySelector('span').textContent = paused ? '暂停接单' : '开始接单';
      toast(paused ? '已恢复接单' : '已暂停接单，队列保留');
      return;
    }
  });

  /* Esc / 点遮罩关闭浮层 */
  document.addEventListener('keydown', ev => {
    if (ev.key !== 'Escape') return;
    if (playerOverlay.classList.contains('open')) { closePlayer(); return; }
    if (wizard.classList.contains('open')) closeWizard();
  });
  if (wizard) wizard.addEventListener('click', ev => { if (ev.target === wizard) closeWizard(); });
  if (playerOverlay) playerOverlay.addEventListener('click', ev => { if (ev.target === playerOverlay) closePlayer(); });

  /* ---------------- 初始化 ---------------- */
  requestAnimationFrame(() => {
    staggerIn($(`#page-${current}`));
    runCountUp($(`#page-${current}`));
  });

  /* ---------------- Electron 外壳 ---------------- */
  const shell = window.songbot;
  if (shell) {
    document.body.dataset.platform = shell.platform;
    $$('[data-win]').forEach(btn => {
      btn.addEventListener('click', async () => {
        const action = btn.dataset.win;
        if (action === 'minimize') shell.minimize();
        else if (action === 'maximize') await shell.toggleMaximize();
        else if (action === 'close') shell.close();
      });
    });
    shell.onWindowState(({ maximized }) => {
      const btn = $('[data-win="maximize"]');
      if (btn) btn.setAttribute('aria-label', maximized ? '还原' : '最大化');
    });
  }
})();
