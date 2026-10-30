/* ============================================================
   SongBot Desktop · interaction layer
   Spring / Stagger / Kinetic
   ============================================================ */
(() => {
  'use strict';

  const ORDER = ['dashboard', 'request', 'queue', 'player', 'live', 'library', 'sources', 'settings', 'logs', 'design'];
  const $ = (sel, root = document) => root.querySelector(sel);
  const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

  const nav = $('#nav');
  const pill = $('#navPill');
  const pages = $$('.page');
  const navItems = $$('.nav-item');
  const pageTitle = $('#pageTitle');
  const pageEyebrow = $('#pageEyebrow');
  const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;

  let current = 'dashboard';
  let isPlaying = true;

  /* ---------------- stagger indices ---------------- */
  $$('[data-stagger]').forEach(group => {
    Array.from(group.children).forEach((child, i) => {
      child.style.setProperty('--i', String(i));
    });
  });

  /* ---------------- nav pill ---------------- */
  function movePill(item) {
    if (!item) return;
    pill.style.height = `${item.offsetHeight}px`;
    pill.style.transform = `translateY(${item.offsetTop}px)`;
    pill.style.opacity = '1';
  }

  const activeItem = () => navItems.find(i => i.dataset.page === current);

  window.addEventListener('resize', () => movePill(activeItem()));

  /* ---------------- page switching ---------------- */
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

    // outgoing
    if (oldPage && !reducedMotion) {
      oldPage.classList.remove('active', 'entering-left', 'entering-right');
      oldPage.classList.add(forward ? 'leaving-left' : 'leaving-right');
      const leaving = oldPage;
      setTimeout(() => leaving.classList.remove('leaving-left', 'leaving-right'), 210);
    } else if (oldPage) {
      oldPage.classList.remove('active');
    }

    // incoming
    newPage.classList.remove('leaving-left', 'leaving-right');
    if (!reducedMotion) {
      newPage.classList.add(forward ? 'entering-right' : 'entering-left');
      setTimeout(() => newPage.classList.remove('entering-right', 'entering-left'), 560);
    }
    newPage.classList.add('active');
    newPage.scrollTop = 0;

    // title
    const item = navItems.find(i => i.dataset.page === id);
    if (item) {
      pageTitle.textContent = item.dataset.title || pageTitle.textContent;
      pageEyebrow.textContent = item.dataset.eyebrow || pageEyebrow.textContent;
      navItems.forEach(n => n.classList.toggle('active', n === item));
      movePill(item);
    }

    // per-page kinetic
    runCountUp(newPage);
    if (id === 'design') replayDemo($('.motion-demo', newPage));
  }

  navItems.forEach(item => {
    item.addEventListener('click', () => switchPage(item.dataset.page));
  });

  /* ---------------- kinetic count-up ---------------- */
  function runCountUp(root) {
    $$('[data-count]', root).forEach(el => {
      const target = Number(el.dataset.count || 0);
      if (reducedMotion) { el.textContent = fmt(target); return; }
      const dur = 900;
      const t0 = performance.now();
      const from = 0;
      function frame(t) {
        const p = Math.min(1, (t - t0) / dur);
        const eased = 1 - Math.pow(1 - p, 3);
        const val = Math.round(from + (target - from) * eased);
        el.textContent = fmt(val);
        if (p < 1) requestAnimationFrame(frame);
      }
      requestAnimationFrame(frame);
    });
  }
  const fmt = n => (n >= 1000 ? n.toLocaleString('en-US') : String(n));

  /* ---------------- kinetic title ---------------- */
  $$('[data-kinetic]').forEach(el => {
    const text = el.dataset.kinetic || '';
    el.innerHTML = Array.from(text).map((ch, i) =>
      `<span style="--k:${i}">${ch === ' ' ? '&nbsp;' : ch}</span>`).join('');
  });

  /* ---------------- toast ---------------- */
  const toastWrap = $('#toastWrap');
  function toast(msg) {
    if (!msg || !toastWrap) return;
    const el = document.createElement('div');
    el.className = 'toast';
    el.innerHTML = '<svg viewBox="0 0 24 24" fill="currentColor"><use href="#i-check"/></svg><span></span>';
    el.querySelector('span').textContent = msg;
    toastWrap.appendChild(el);
    setTimeout(() => {
      el.classList.add('out');
      setTimeout(() => el.remove(), 280);
    }, 3000);
  }

  /* ---------------- ripple ---------------- */
  function ripple(host, ev) {
    if (reducedMotion) return;
    const rect = host.getBoundingClientRect();
    const size = Math.max(rect.width, rect.height);
    const span = document.createElement('span');
    span.className = 'ripple';
    span.style.width = span.style.height = `${size}px`;
    span.style.left = `${(ev.clientX ?? rect.left + rect.width / 2) - rect.left - size / 2}px`;
    span.style.top = `${(ev.clientY ?? rect.top + rect.height / 2) - rect.top - size / 2}px`;
    host.appendChild(span);
    setTimeout(() => span.remove(), 650);
  }

  /* ---------------- wizard ---------------- */
  const wizard = $('#wizardOverlay');
  let wizStep = 0;
  const wizSteps = $$('.wiz-step', wizard || document);
  const wizDots = $$('.wiz-dots i', wizard || document);

  function setWizStep(n) {
    wizStep = Math.max(0, Math.min(wizSteps.length - 1, n));
    wizSteps.forEach((s, i) => s.classList.toggle('on', i === wizStep));
    wizDots.forEach((d, i) => d.classList.toggle('on', i === wizStep));
  }
  function openWizard() {
    setWizStep(0);
    wizard.classList.add('open');
  }
  function closeWizard() { wizard.classList.remove('open'); }

  /* ---------------- player ---------------- */
  const pbPlay = $('#pbPlay');
  const pbPlayIcon = $('#pbPlayIcon');
  function setPlaying(next) {
    isPlaying = next;
    $$('.wave').forEach(w => w.classList.toggle('paused', !isPlaying));
    if (pbPlayIcon) pbPlayIcon.querySelector('use').setAttribute('href', isPlaying ? '#i-pause' : '#i-play');
  }
  setPlaying(true);

  /* ---------------- motion demos ---------------- */
  function replayDemo(demo) {
    if (!demo) return;
    demo.classList.remove('play');
    // force reflow so the animation restarts
    void demo.offsetWidth;
    demo.classList.add('play');
  }

  /* ---------------- delegated interactions ---------------- */
  document.addEventListener('click', ev => {
    const target = ev.target.closest('button, .chip, .switch, .quick, .nav-item, .icon-btn');
    if (!target) return;

    // ripple for material buttons
    if (target.matches('.btn, .icon-btn, .quick, .pb-play, .nav-item')) {
      ripple(target, ev);
    }

    // navigation
    if (target.dataset.nav) {
      switchPage(target.dataset.nav);
      return;
    }

    // toast
    if (target.dataset.toast) toast(target.dataset.toast);

    // wizard controls
    if (target.hasAttribute('data-open-wizard')) { openWizard(); return; }
    if (target.hasAttribute('data-close-wizard')) closeWizard();
    if (target.hasAttribute('data-wiz-next')) { setWizStep(wizStep + 1); return; }
    if (target.hasAttribute('data-wiz-prev')) { setWizStep(wizStep - 1); return; }

    // switches
    if (target.classList.contains('switch')) {
      target.classList.toggle('on');
      return;
    }

    // segmented controls (exclusive within group)
    if (target.closest('.segmented') && target.tagName === 'BUTTON') {
      $$('button', target.closest('.segmented')).forEach(b => b.classList.toggle('on', b === target));
      return;
    }

    // chips (exclusive within a chips-row)
    if (target.classList.contains('chip')) {
      const row = target.closest('.chips-row');
      if (row && !target.dataset.free) {
        $$('.chip', row).forEach(c => { if (c !== target && c.classList.contains('on') && !c.dataset.free) c.classList.remove('on'); });
      }
      target.classList.toggle('on');
      return;
    }

    // queue remove
    if (target.classList.contains('del')) {
      const row = target.closest('.row');
      if (row) {
        row.classList.add('removing');
        setTimeout(() => row.remove(), 300);
      }
      toast('已从队列移除');
      return;
    }

    // play / pause
    if (target === pbPlay || target.classList.contains('pb-play')) {
      setPlaying(!isPlaying);
      toast(isPlaying ? '继续播放' : '已暂停');
      return;
    }

    // motion replay
    if (target.dataset.replay) {
      const demo = target.closest('.motion-card')?.querySelector('.motion-demo');
      replayDemo(demo);
      return;
    }

    // bot toggle
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

  /* ---------------- overlays with Esc ---------------- */
  document.addEventListener('keydown', ev => {
    if (ev.key === 'Escape' && wizard.classList.contains('open')) closeWizard();
  });
  if (wizard) {
    wizard.addEventListener('click', ev => { if (ev.target === wizard) closeWizard(); });
  }

  /* ---------------- init ---------------- */
  requestAnimationFrame(() => {
    const first = navItems.find(i => i.dataset.page === current);
    movePill(first);
    runCountUp($(`#page-${current}`));
  });

  /* ---------------- electron shell（浏览器内打开时自动跳过） ---------------- */
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
