/* cc-dash front end. No dependencies, no network beyond this origin.
   Every piece of transcript text goes into the DOM through textContent. */
'use strict';
(() => {
  // ── DOM helpers ──────────────────────────────────────────────────────
  const SVGNS = 'http://www.w3.org/2000/svg';
  const P = window.ccDom;         // web/dom.js: in-place updates, so polls never blink
  function h(tag, attrs, ...kids) {
    const el = document.createElement(tag);
    if (attrs) setAttrs(el, attrs);
    append(el, kids);
    return el;
  }
  function s(tag, attrs, ...kids) {
    const el = document.createElementNS(SVGNS, tag);
    if (attrs) for (const k in attrs) { const v = attrs[k]; if (v == null) continue; if (k.startsWith('on')) P.listen(el, k.slice(2), v); else el.setAttribute(k, v); }
    append(el, kids);
    return el;
  }
  function setAttrs(el, attrs) {
    for (const k in attrs) {
      const v = attrs[k];
      if (v == null || v === false) continue;
      if (k === 'class') el.className = v;
      else if (k === 'text') el.textContent = v;
      else if (k === 'style' && typeof v === 'object') for (const p in v) el.style.setProperty(p.startsWith('-') ? p : p.replace(/[A-Z]/g, (m) => '-' + m.toLowerCase()), v[p]);
      else if (k.startsWith('on')) P.listen(el, k.slice(2), v);
      else if (k === 'dataset') Object.assign(el.dataset, v);
      else el.setAttribute(k, v === true ? '' : v);
    }
  }
  function append(el, kids) {
    for (const k of kids.flat(Infinity)) {
      if (k == null || k === false) continue;
      el.appendChild(typeof k === 'string' || typeof k === 'number' ? document.createTextNode(String(k)) : k);
    }
  }
  const $ = (q, r = document) => r.querySelector(q);

  // ── Formatting ───────────────────────────────────────────────────────
  const nowS = () => Date.now() / 1000;
  const int = (n) => (n == null ? '0' : Math.round(n).toLocaleString('en-US'));
  function compact(n) {
    if (n == null) return '0';
    const a = Math.abs(n);
    if (a < 1000) return String(Math.round(n));
    if (a < 1e6) return (n / 1e3).toFixed(a < 1e4 ? 1 : 0).replace(/\.0$/, '') + 'K';
    if (a < 1e9) return (n / 1e6).toFixed(a < 1e7 ? 1 : 0).replace(/\.0$/, '') + 'M';
    return (n / 1e9).toFixed(a < 1e10 ? 2 : 1).replace(/\.0+$/, '') + 'B';
  }
  function money(v, precise) {
    if (v == null) return 'n/a';
    if (v === 0) return '$0';
    if (v < 0.01) return '<$0.01';
    if (v < 100 || precise) return '$' + v.toFixed(2);
    if (v < 10000) return '$' + Math.round(v).toLocaleString('en-US');
    return '$' + (v / 1000).toFixed(1) + 'K';
  }
  function dur(sec) {
    if (sec == null || !isFinite(sec)) return '';
    sec = Math.max(0, Math.round(sec));
    if (sec < 60) return sec + 's';
    const m = Math.floor(sec / 60), ss = sec % 60;
    if (m < 60) return m + 'm' + (m < 10 && ss ? ' ' + String(ss).padStart(2, '0') + 's' : '');
    const hh = Math.floor(m / 60), mm = m % 60;
    if (hh < 48) return hh + 'h ' + String(mm).padStart(2, '0') + 'm';
    return Math.floor(hh / 24) + 'd ' + (hh % 24) + 'h';
  }
  function ago(ts) {
    if (!ts) return '';
    const d = nowS() - ts;
    if (d < 5) return 'just now';
    if (d < 60) return Math.round(d) + 's ago';
    if (d < 3600) return Math.round(d / 60) + 'm ago';
    if (d < 86400) return Math.round(d / 3600) + 'h ago';
    if (d < 2 * 86400) return 'yesterday';
    return dateOnly(ts);
  }
  const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
  const DOW = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];
  const pad = (n) => String(n).padStart(2, '0');
  function clock(ts, secs = true) { const d = new Date(ts * 1000); return pad(d.getHours()) + ':' + pad(d.getMinutes()) + (secs ? ':' + pad(d.getSeconds()) : ''); }
  function dateOnly(ts) { const d = new Date(ts * 1000); return MONTHS[d.getMonth()] + ' ' + d.getDate(); }
  function dateTime(ts) { return ts ? dateOnly(ts) + ', ' + clock(ts, false) : ''; }
  const pct = (a, b) => (b ? (100 * a / b) : 0);
  function pctStr(a, b) { const p = pct(a, b); return (p > 0 && p < 0.1 ? '<0.1' : p < 10 ? p.toFixed(1) : Math.round(p)) + '%'; }
  const plural = (n, one, many) => int(n) + ' ' + (n === 1 ? one : (many || one + 's'));
  const FAMILIES = ['opus', 'sonnet', 'haiku', 'fable', 'other'];
  const FAMILY_NAME = { opus: 'Opus', sonnet: 'Sonnet', haiku: 'Haiku', fable: 'Fable', other: 'Other' };
  const famOf = (name) => { const n = (name || '').toLowerCase(); return FAMILIES.find((f) => n.includes(f)) || 'other'; };
  const key = (fam, line) => h('span', { class: 'key f-' + (fam || 'other') + (line ? ' line' : ''), 'aria-hidden': 'true' });
  const showBranch = (r) => r.branch && r.branch !== r.worktree && r.branch !== 'worktree-' + r.worktree;
  const modelTag = (name, fam) => h('span', { class: 'model' }, key(fam || famOf(name)), name || 'no model yet');

  // ── State ────────────────────────────────────────────────────────────
  const S = {
    route: { view: 'live' }, meta: null,
    data: { live: null, sessions: null, overview: null, session: {}, agent: {} },
    err: {}, lastOk: 0, lastFail: null,
    ui: {
      q: '', project: '', wt: '', family: '', errorsOnly: false, sort: { k: 'last_write', d: -1 }, sel: -1,
      scope: '', psort: { k: 'cost', d: -1 }, pexp: new Set(), unclamp: new Set(),
      liveSel: -1, dtab: 'timeline', expanded: new Set(), expandAll: false, tl: 'all', showAll: false,
      act: 'tools',
    },
  };
  const main = $('#main');

  // ── Theme ────────────────────────────────────────────────────────────
  const THEMES = ['system', 'light', 'dark'];
  const store = { get(k) { try { return localStorage.getItem(k); } catch (e) { return null; } },
    set(k, v) { try { localStorage.setItem(k, v); } catch (e) { /* private mode */ } } };
  function themeIcon(t) {
    const svg = s('svg', { viewBox: '0 0 16 16', 'aria-hidden': 'true' });
    if (t === 'light') svg.append(s('circle', { cx: 8, cy: 8, r: 3.2, fill: 'currentColor' }),
      ...[0, 45, 90, 135, 180, 225, 270, 315].map((a) => s('line', { x1: 8, y1: 1.6, x2: 8, y2: 3.1, stroke: 'currentColor', 'stroke-width': 1.4, 'stroke-linecap': 'round', transform: `rotate(${a} 8 8)` })));
    else if (t === 'dark') svg.append(s('path', { d: 'M10.8 10.9A5 5 0 0 1 5.1 5.2a5 5 0 1 0 5.7 5.7Z', fill: 'currentColor' }));
    else svg.append(s('circle', { cx: 8, cy: 8, r: 5.2, fill: 'none', stroke: 'currentColor', 'stroke-width': 1.4 }), s('path', { d: 'M8 2.8a5.2 5.2 0 0 1 0 10.4Z', fill: 'currentColor' }));
    return svg;
  }
  function applyTheme(t) {
    const root = document.documentElement;
    if (t === 'light' || t === 'dark') root.dataset.theme = t; else delete root.dataset.theme;
    const b = $('#theme');
    b.replaceChildren(themeIcon(t));
    b.setAttribute('aria-label', 'Theme: ' + (t === 'system' ? 'follow system' : t));
  }
  let theme = new URLSearchParams(location.search).get('theme') || store.get('cc-dash-theme') || 'system';
  if (!THEMES.includes(theme)) theme = 'system';
  applyTheme(theme);
  function cycleTheme() { theme = THEMES[(THEMES.indexOf(theme) + 1) % 3]; store.set('cc-dash-theme', theme); applyTheme(theme); rerenderCharts(); }
  $('#theme').addEventListener('click', cycleTheme);

  // ── Network ──────────────────────────────────────────────────────────
  async function api(path) {
    const r = await fetch(path, { cache: 'no-store', credentials: 'same-origin' });
    let body = null;
    try { body = await r.json(); } catch (e) { /* not json */ }
    if (!r.ok) { const e = new Error((body && body.error) || ('HTTP ' + r.status)); e.status = r.status; throw e; }
    S.lastOk = nowS(); S.lastFail = null;
    return body;
  }
  function failed(e) {
    S.lastFail = e && e.message ? e.message : 'Server unreachable'; syncText();
    if (current && ['live', 'sessions'].includes(S.route.view) && !S.data[S.route.view]) update();   // first load failed: say so instead of a skeleton
  }

  // ── Router ───────────────────────────────────────────────────────────
  /* #/sessions?p=<project>&wt=<worktree> and #/overview?p=<project> carry the
     project filter and scope, so reload and back keep them. Values are only
     ever compared with project names and sent as a query value. */
  function parseRoute() {
    const raw = location.hash.replace(/^#\/?/, '');
    const qi = raw.indexOf('?');
    const q = new URLSearchParams(qi >= 0 ? raw.slice(qi + 1) : '');
    const safe = (fn) => { try { return fn(); } catch (e) { return null; } };
    const p = (qi >= 0 ? raw.slice(0, qi) : raw).split('/').map((x) => safe(() => decodeURIComponent(x)) || '');
    if (p[0] === 's' && p[1]) return p[2] === 'a' && p[3] ? { view: 'agent', id: p[1], aid: p[3] } : { view: 'session', id: p[1] };
    if (['live', 'sessions', 'overview'].includes(p[0])) return { view: p[0], p: (q.get('p') || '').slice(0, 200), wt: p[0] === 'sessions' ? (q.get('wt') || '').slice(0, 200) : '' };
    return { view: 'live' };
  }
  function viewHash(view, p, wt) {
    const q = new URLSearchParams();
    if (p) q.set('p', p);
    if (p && wt) q.set('wt', wt);
    const qs = q.toString();
    return '#/' + view + (qs ? '?' + qs : '');
  }
  const sessionsHash = () => viewHash('sessions', S.ui.project, S.ui.wt);
  const overviewHash = () => viewHash('overview', S.ui.scope);
  function syncTabLinks() {
    const sl = document.querySelector('.tabs a[data-view="sessions"]');
    const ol = document.querySelector('.tabs a[data-view="overview"]');
    if (sl) sl.setAttribute('href', sessionsHash());
    if (ol) ol.setAttribute('href', overviewHash());
  }
  let prevRoute = null;
  function onRoute() {
    const r = parseRoute();
    const same = prevRoute && prevRoute.view === r.view && prevRoute.id === r.id && prevRoute.aid === r.aid;
    if (!same && (r.view === 'session' || r.view === 'agent') && (!prevRoute || prevRoute.id !== r.id || prevRoute.aid !== r.aid)) {
      S.ui.dtab = 'timeline'; S.ui.expanded = new Set(); S.ui.expandAll = false; S.ui.tl = 'all'; S.ui.showAll = false; S.ui.unclamp = new Set();
    }
    if (r.view === 'sessions') {
      if (S.ui.project !== r.p || S.ui.wt !== r.wt) S.ui.sel = -1;
      S.ui.project = r.p; S.ui.wt = r.wt;
    }
    if (r.view === 'overview') S.ui.scope = r.p;
    S.route = r;
    if (!['session', 'agent'].includes(r.view)) S.backTo = location.hash || '#/' + r.view;
    prevRoute = r;
    syncTabLinks();
    for (const a of document.querySelectorAll('.tabs a')) {
      const on = a.dataset.view === r.view || (a.dataset.view === 'sessions' && ['session', 'agent'].includes(r.view) && !/^#\/live/.test(S.backTo || ''));
      if (on) a.setAttribute('aria-current', 'page'); else a.removeAttribute('aria-current');
    }
    if (!same) { mount(); window.scrollTo(0, 0); } else update();
    tick(true);
  }
  window.addEventListener('hashchange', onRoute);

  // ── Polling ──────────────────────────────────────────────────────────
  let timer = null, inflight = false;
  const due = {};
  function interval() {
    const v = S.route.view;
    if (v === 'live') return 3000;
    if (v === 'sessions') return 10000;
    if (v === 'overview') return 30000;
    const d = v === 'session' ? S.data.session[S.route.id] : S.data.agent[S.route.id + '/' + S.route.aid];
    const live = d && (v === 'session' ? d.session.state : d.agent.status === 'running');
    return live ? 3000 : 20000;
  }
  async function tick(force) {
    clearTimeout(timer);
    if (document.hidden && !force) { timer = setTimeout(tick, 5000); return; }
    if (inflight) { timer = setTimeout(tick, 500); return; }
    inflight = true;
    const r = S.route;
    const t = nowS();
    try {
      const jobs = [];
      if (!S.meta) jobs.push(api('/api/meta').then((m) => { S.meta = m; scopeText(); }));
      if (r.view === 'live' || force || !due.live || t - due.live > 5) {
        jobs.push(api('/api/live').then((d) => { S.data.live = d; due.live = t; liveCount(); if (S.route.view === 'live') update(); }));
      }
      if (r.view === 'sessions' || (r.view === 'live' && !S.data.sessions)) {
        jobs.push(api('/api/sessions').then((d) => { S.data.sessions = d; if (['sessions', 'live'].includes(S.route.view)) update(); }));
      } else if (r.view === 'overview') {
        const scope = S.ui.scope;
        jobs.push(api('/api/overview' + (scope ? '?project=' + encodeURIComponent(scope) : '')).then((d) => {
          if (S.ui.scope !== scope) return;           // the scope changed while this was in flight
          S.data.overview = d; S.data.overviewScope = scope; S.err.overview = null; if (S.route.view === 'overview') update();
        }).catch((e) => { S.err.overview = e; if (S.route.view === 'overview') update(); throw e; }));
      } else if (r.view === 'session') {
        const id = r.id;
        jobs.push(api('/api/session?id=' + encodeURIComponent(id)).then((d) => { S.data.session[id] = d; S.err.session = null; if (S.route.id === id) update(); })
          .catch((e) => { S.err.session = e; if (S.route.id === id) update(); if (e.status !== 404) throw e; }));
      } else if (r.view === 'agent') {
        const k = r.id + '/' + r.aid;
        jobs.push(api('/api/agent?session=' + encodeURIComponent(r.id) + '&id=' + encodeURIComponent(r.aid)).then((d) => { S.data.agent[k] = d; S.err.agent = null; update(); })
          .catch((e) => { S.err.agent = e; update(); if (e.status !== 404) throw e; }));
      }
      const res = await Promise.allSettled(jobs);
      const bad = res.find((x) => x.status === 'rejected');
      if (bad) failed(bad.reason);
    } finally {
      inflight = false;
      syncText();
      timer = setTimeout(tick, interval());
    }
  }
  document.addEventListener('visibilitychange', () => { if (!document.hidden) tick(true); });

  // ── Chrome: scope, sync, live count ──────────────────────────────────
  function scopeText() {
    const m = S.meta;
    if (!m) return;
    $('#scope').textContent = 'Last ' + (m.days === 1 ? 'day' : m.days + ' days') + (m.project ? ', ' + m.project : '') + ', ' + m.root;
  }
  function syncText() {
    const el = $('#sync');
    if (S.lastFail) { el.className = 'sync err'; el.replaceChildren(h('span', { class: 'dot', style: { background: 'var(--bad)' } }), 'cc-dash stopped responding'); return; }
    if (!S.lastOk) { el.className = 'sync'; el.textContent = 'Loading'; return; }
    el.className = 'sync';
    const d = nowS() - S.lastOk;
    el.replaceChildren('Updated ' + (d < 2 ? 'just now' : Math.round(d) + 's ago'));
  }
  function liveCount() {
    const n = S.data.live ? S.data.live.sessions.length : 0;
    const el = $('#live-count');
    el.hidden = !n;
    el.textContent = n;
  }
  setInterval(() => {
    syncText();
    const t = nowS();
    for (const el of document.querySelectorAll('[data-since]')) P.setText(el, dur(t - Number(el.dataset.since)));
    for (const el of document.querySelectorAll('[data-ago]')) P.setText(el, ago(Number(el.dataset.ago)));
  }, 1000);

  // ── Tooltip ──────────────────────────────────────────────────────────
  const tip = $('#tip');
  function showTip(ev, title, rows, total) {
    tip.replaceChildren(h('div', { class: 'tt', text: title }),
      ...rows.map((r) => h('div', { class: 'tr' }, r.fam ? key(r.fam, true) : h('span', { class: 'key line', style: { background: r.color || 'var(--ink-3)' } }), h('span', { text: r.name }), h('b', { text: r.value }))),
      total ? h('div', { class: 'tr total' }, h('span'), h('span', { text: total.name }), h('b', { text: total.value })) : null);
    tip.hidden = false;
    moveTip(ev);
  }
  function moveTip(ev) {
    const pad = 14, w = tip.offsetWidth, hh = tip.offsetHeight;
    let x = ev.clientX + pad, y = ev.clientY + pad;
    if (x + w > innerWidth - 8) x = ev.clientX - w - pad;
    if (y + hh > innerHeight - 8) y = ev.clientY - hh - pad;
    tip.style.left = Math.max(8, x) + 'px';
    tip.style.top = Math.max(8, y) + 'px';
  }
  const hideTip = () => { tip.hidden = true; };

  // ── Charts ───────────────────────────────────────────────────────────
  function niceStep(v) {
    if (v <= 0) return 1;
    const e = Math.pow(10, Math.floor(Math.log10(v)));
    const m = v / e;
    return (m <= 1 ? 1 : m <= 2 ? 2 : m <= 2.5 ? 2.5 : m <= 5 ? 5 : 10) * e;
  }
  function topRounded(x, y, w, hh, r) {
    r = Math.min(r, w / 2, hh);
    return `M${x},${y + hh}V${y + r}Q${x},${y} ${x + r},${y}H${x + w - r}Q${x + w},${y} ${x + w},${y + r}V${y + hh}Z`;
  }
  /* Stacked columns. buckets: [{label, title, values:{seriesKey: n}}]; series: [{k, name, fam|color}] */
  function columns(host, { buckets, series, fmt, height = 220, totalLabel = 'Total' }) {
    const W = Math.max(280, host.clientWidth || 600), H = height;
    const m = { t: 10, r: 4, b: 26, l: 46 };
    const iw = W - m.l - m.r, ih = H - m.t - m.b;
    const totals = buckets.map((b) => series.reduce((a, sr) => a + (b.values[sr.k] || 0), 0));
    const max = Math.max(0, ...totals);
    const step = niceStep(max / 4 || 1);
    const top = Math.max(step, Math.ceil(max / step) * step);
    const svg = s('svg', { viewBox: `0 0 ${W} ${H}`, height: H, role: 'img' });
    for (let v = 0; v <= top + 1e-9; v += step) {
      const y = m.t + ih - (v / top) * ih;
      // Keyed by role and position, so a changed scale patches in place instead of shifting nodes.
      const yi = Math.round(v / step);
      svg.append(s('line', { 'data-key': 'gl' + yi, class: v === 0 ? 'base-l' : 'grid-l', x1: m.l, x2: W - m.r, y1: Math.round(y) + .5, y2: Math.round(y) + .5 }));
      svg.append(s('text', { 'data-key': 'yt' + yi, x: m.l - 8, y: y + 3.5, 'text-anchor': 'end' }, fmt(v, true)));
    }
    const n = buckets.length || 1;
    const band = iw / n;
    const bw = Math.max(2, Math.min(24, band * 0.62));
    const every = Math.max(1, Math.ceil(46 / band));
    buckets.forEach((b, i) => {
      const cx = m.l + band * i + band / 2;
      const g = s('g', { class: 'col', 'data-key': 'g' + i });
      let y = m.t + ih;
      const segs = series.map((sr) => ({ sr, v: b.values[sr.k] || 0 })).filter((x) => x.v > 0);
      segs.forEach((sg, j) => {
        const hh = (sg.v / top) * ih;
        const gap = j > 0 ? 2 : 0;
        const hEff = Math.max(0, hh - gap);
        if (hEff < 0.4) { y -= hh; return; }
        const yTop = y - hh;
        const fill = sg.sr.fam ? `var(--${sg.sr.fam})` : sg.sr.color;
        const last = j === segs.length - 1;
        g.append(last ? s('path', { 'data-key': sg.sr.k, d: topRounded(cx - bw / 2, yTop, bw, hEff, 4), fill }) : s('rect', { 'data-key': sg.sr.k, x: cx - bw / 2, y: yTop, width: bw, height: hEff, fill }));
        y = yTop;
      });
      const hover = s('rect', { class: 'band-hover', x: m.l + band * i, y: m.t, width: band, height: ih, opacity: 0 });
      // Handlers look up the live nodes from the event: after an in-place patch
      // the rect on screen is an older node than the one built here.
      const band$ = (hit) => hit.parentNode && hit.parentNode.querySelector('.band-hover[data-i="' + i + '"]');
      const show = (ev) => {
        const hit = ev.currentTarget, hv = band$(hit);
        if (hv) hv.setAttribute('opacity', 1);
        const rows = series.filter((sr) => (b.values[sr.k] || 0) > 0).map((sr) => ({ fam: sr.fam, color: sr.color, name: sr.name, value: fmt(b.values[sr.k]) }));
        const box = hit.getBoundingClientRect();
        showTip(ev.clientX != null ? ev : { clientX: box.right, clientY: box.top + 30 }, b.title,
          rows.length ? rows : [{ name: 'Nothing recorded', value: '' }], series.length > 1 && rows.length > 1 ? { name: totalLabel, value: fmt(totals[i]) } : null);
      };
      const off = (ev) => { const hv = band$(ev.currentTarget); if (hv) hv.setAttribute('opacity', 0); hideTip(); };
      hover.setAttribute('data-i', i);
      hover.setAttribute('data-key', 'h' + i);
      const hit = s('rect', { class: 'hit', 'data-key': 'x' + i, x: m.l + band * i, y: m.t, width: band, height: ih + m.b, tabindex: 0, 'aria-label': b.title + ': ' + fmt(totals[i]),
        onpointerenter: show, onpointermove: moveTip, onpointerleave: off, onfocus: show, onblur: off });
      svg.append(hover, g);
      if (i % every === 0 || i === n - 1 && n < 12) svg.append(s('text', { 'data-key': 'l' + i, x: cx, y: H - 8, 'text-anchor': 'middle' }, b.label));
      svg.append(hit);
    });
    P.into(host, svg);
  }
  /* A chart host redraws only when its data signature changed (or on resize
     and theme change), and the redraw patches the existing SVG in place. */
  function chart(host, sig, fn) { host.setAttribute('data-chart', ''); host.__chart = { sig: JSON.stringify(sig), draw: fn }; return host; }
  function rerenderCharts() { P.flushCharts(main, true); }
  let rz = null;
  window.addEventListener('resize', () => { clearTimeout(rz); rz = setTimeout(rerenderCharts, 120); });

  /* Time buckets in local time: days, or hours when the window is two days or less. */
  function timeBuckets(cutoff, now) {
    const hourly = now - cutoff <= 2 * 86400 + 1;
    const out = [];
    const d = new Date(cutoff * 1000);
    if (hourly) { d.setMinutes(0, 0, 0); } else { d.setHours(0, 0, 0, 0); }
    while (d.getTime() / 1000 <= now) {
      const ts = d.getTime() / 1000;
      const label = hourly ? pad(d.getHours()) + ':00' : (out.length === 0 || d.getDate() === 1 ? MONTHS[d.getMonth()] + ' ' : '') + d.getDate();
      const title = hourly ? DOW[d.getDay()] + ' ' + dateOnly(ts) + ', ' + pad(d.getHours()) + ':00' : DOW[d.getDay()] + ', ' + dateOnly(ts);
      out.push({ ts, label, title, values: {} });
      if (hourly) d.setHours(d.getHours() + 1); else d.setDate(d.getDate() + 1);
    }
    return { hourly, buckets: out };
  }
  function bucketIndex(buckets, ts) {
    let lo = 0, hi = buckets.length - 1, ans = -1;
    while (lo <= hi) { const mid = (lo + hi) >> 1; if (buckets[mid].ts <= ts) { ans = mid; lo = mid + 1; } else hi = mid - 1; }
    return ans;
  }

  // ── Shared pieces ────────────────────────────────────────────────────
  function skeleton(kind) {
    if (kind === 'agents') return h('div', { class: 'board-wrap' }, h('div', { class: 'agroup' }, h('div', { class: 'agroup-h' }, h('div', { class: 'sk sk-line', style: { width: '30%' } })),
      [0, 1, 2].map((i) => h('div', { class: 'arow' }, h('div', { class: 'sk sk-line', style: { width: (80 - i * 15) + '%' } }), h('div', { class: 'sk sk-line', style: { width: '70%' } })))),
    h('div', { class: 'panel feed' }, [0, 1, 2, 3].map(() => h('div', { class: 'sk sk-line', style: { width: '85%', margin: '14px 16px' } }))));
    if (kind === 'cards') return h('div', { class: 'live-grid' }, [0, 1].map(() => h('div', { class: 'card' }, h('div', { class: 'sk sk-line', style: { width: '40%' } }), h('div', { class: 'sk sk-line', style: { width: '75%', height: '18px' } }), h('div', { class: 'sk', style: { height: '54px' } }), h('div', { class: 'sk sk-line', style: { width: '60%' } }))));
    if (kind === 'table') return h('div', { class: 'table-wrap', style: { padding: '12px 16px' } }, [...Array(8)].map((_, i) => h('div', { class: 'sk sk-line', style: { width: (92 - (i % 3) * 9) + '%', margin: '14px 0' } })));
    return h('div', {}, h('div', { class: 'kpis' }, [...Array(6)].map(() => h('div', { class: 'kpi' }, h('div', { class: 'sk sk-line', style: { width: '50%' } }), h('div', { class: 'sk', style: { height: '28px', width: '70%', marginTop: '8px' } })))),
      h('div', { class: 'grid g-3-2' }, h('div', { class: 'panel panel-b' }, h('div', { class: 'sk sk-block' })), h('div', { class: 'panel panel-b' }, h('div', { class: 'sk sk-block' }))));
  }
  function errorBox(e, what) {
    const icon = s('svg', { viewBox: '0 0 16 16', 'aria-hidden': 'true' });
    icon.append(s('circle', { cx: 8, cy: 8, r: 6.25, fill: 'none', stroke: 'currentColor', 'stroke-width': 1.5 }),
      s('path', { d: 'M8 4.9v3.5M8 11v.1', fill: 'none', stroke: 'currentColor', 'stroke-width': 1.7, 'stroke-linecap': 'round' }));
    const gone = !e || !e.message || e instanceof TypeError || /failed to fetch|networkerror|unreachable/i.test(e.message);   // nothing answered
    return h('div', { class: 'error-box', role: 'alert' }, icon, h('b', { text: 'Could not load ' + what }),
      h('div', { class: 'err-msg', text: gone ? 'cc-dash is not answering. If you stopped it, start it again with cc-dash.' : e.message }),
      h('button', { class: 'btn', type: 'button', text: 'Try again', onclick: () => tick(true) }));
  }
  function kpi(label, value, note, cls) {
    const v = h('div', { class: 'value' });
    if (Array.isArray(value)) append(v, [value[0], h('small', { text: value[1] })]); else v.textContent = value;
    return h('div', { class: 'kpi ' + (cls || '') }, h('div', { class: 'label', text: label }), v, note ? h('div', { class: 'note', text: note }) : null);
  }
  function statePill(state) {
    if (state === 'working') return h('span', { class: 'pill good' }, h('span', { class: 'dot working' }), 'Working');
    if (state === 'waiting') return h('span', { class: 'pill warn' }, h('span', { class: 'dot waiting' }), 'Waiting for you');
    if (state === 'idle') return h('span', { class: 'pill' }, h('span', { class: 'dot idle' }), 'Idle');
    return null;
  }
  function subStatus(st) {
    if (st === 'running') return h('span', { class: 'pill good' }, h('span', { class: 'dot working' }), 'Running');
    if (st === 'failed') return h('span', { class: 'pill bad', text: 'Failed' });
    if (st === 'stopped') return h('span', { class: 'pill warn', text: 'Did not finish' });
    if (st === 'done' || st === 'completed') return h('span', { class: 'pill', text: 'Done' });
    if (st === 'error') return h('span', { class: 'pill bad', text: 'Error' });
    return h('span', { class: 'pill', text: st || 'Unknown' });
  }
  const chev = () => { const v = s('svg', { class: 'chev', viewBox: '0 0 12 12', 'aria-hidden': 'true' }); v.append(s('path', { d: 'M4.5 2.5 8 6l-3.5 3.5', fill: 'none', stroke: 'currentColor', 'stroke-width': 1.5, 'stroke-linecap': 'round', 'stroke-linejoin': 'round' })); return v; };
  function shareBar(rows, valueKey) {
    const total = rows.reduce((a, r) => a + (r[valueKey] || 0), 0);
    if (!total) return null;
    return h('div', { class: 'share', role: 'img', 'aria-label': rows.map((r) => r.name + ' ' + pctStr(r[valueKey] || 0, total)).join(', ') },
      rows.filter((r) => r[valueKey] > 0).map((r) => h('span', { class: 'f-' + r.family, style: { flex: String(r[valueKey] / total) } })));
  }

  // ── Views ────────────────────────────────────────────────────────────
  const views = {};
  let current = null;
  function mount() {
    hideTip();
    current = views[S.route.view];
    main.replaceChildren();
    current.mount(main);
    update();
  }
  function update() { if (current) current.update(); }

  // Live ----------------------------------------------------------------
  /* Two modes. Agents: every agent working now (main threads, subagents,
     workflow agents) as a row in its session's tree, plus a feed of tool
     calls. Sessions: one card per session (the original Live view).

     Refreshes never rebuild the page: each session, agent, workflow, feed
     entry and card keeps one node, keyed by its id. Every poll builds a fresh
     tree with the same builder and ccDom.patch() writes only the text and
     attributes that changed, so hover, focus, scroll and selection survive.
     Motion marks change only: a new agent fades in, one that finishes,
     fails or starts needing you gets a single wash, one that leaves fades
     out. Timers tick by rewriting their text node in place. */
  const { patch, reconcile, setText } = window.ccDom;
  const LIVE_MODES = ['agents', 'sessions'];
  let liveMode = store.get('cc-dash-live-mode');
  if (!LIVE_MODES.includes(liveMode)) liveMode = 'agents';
  const reducedMotion = () => window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;

  /* "Needs you" comes from GET /api/alerts, served by the hooks feature. A
     missing endpoint (404), an error or an empty list shows nothing. */
  const alerts = { list: [], sig: '', timer: null, missing: false };
  async function pollAlerts() {
    clearTimeout(alerts.timer);
    alerts.timer = null;
    if (S.route.view !== 'live') return;
    let next = 2000;
    if (!document.hidden) {
      try {
        const r = await fetch('/api/alerts', { cache: 'no-store', credentials: 'same-origin' });
        alerts.missing = r.status === 404;
        let d = null;
        if (r.ok) { try { d = await r.json(); } catch (e) { d = null; } }
        setAlerts(d && Array.isArray(d.alerts) ? d.alerts : []);
        if (alerts.missing) next = 60000;   // no alerts endpoint in this build: check rarely, say nothing
      } catch (e) { next = 5000; }
    }
    if (S.route.view === 'live') alerts.timer = setTimeout(pollAlerts, next);
  }
  function setAlerts(list) {
    const clean = list.filter((a) => a && typeof a.session === 'string' && a.session);
    const sig = JSON.stringify(clean.map((a) => [a.id, a.session, a.agent_id, a.kind, a.tool, a.title, a.message, a.since]));
    if (sig === alerts.sig) return;
    alerts.sig = sig;
    alerts.list = clean;
    if (S.route.view === 'live') update();
  }
  const isoTs = (v) => { const t = Date.parse(v || ''); return isFinite(t) ? t / 1000 : null; };
  function needsText(n) {
    if (n.kind === 'permission') return 'approve ' + (n.tool || 'a tool call');
    if (n.kind === 'question') return 'answer a question';
    if (n.kind === 'plan') return 'review the plan';
    if (n.kind === 'idle') return 'waiting for your input';
    if (n.kind === 'error') return (n.title || 'an error').toLowerCase();
    return n.title || 'needs attention';
  }
  const needsPill = (n) => h('span', { class: 'pill warn needs-pill' }, h('span', { class: 'dot waiting' }), 'Needs you: ' + needsText(n));

  /* Live timers use their own attributes and rewrite the text node only, so
     a tick is a characterData change, never a node swap. */
  const lsince = (t, cls) => h('span', { class: cls || null, dataset: { lsince: t }, text: dur(nowS() - t) });
  const lago = (t, cls) => h('span', { class: cls || null, dataset: { lago: t }, text: ago(t) });
  setInterval(() => {
    if (S.route.view !== 'live') return;
    const t = nowS();
    for (const el of main.querySelectorAll('[data-lsince]')) setText(el, dur(t - Number(el.dataset.lsince)));
    for (const el of main.querySelectorAll('[data-lago]')) setText(el, ago(Number(el.dataset.lago)));
  }, 1000);
  /* A removed agent row or session fades out, then goes. */
  function leave(el) {
    if (reducedMotion() || !['arow', 'agroup', 'card'].some((c) => el.classList.contains(c))) return false;
    el.classList.add('leaving');
    setTimeout(() => el.remove(), 260);
    return true;
  }

  const RUNNING = ['tool', 'generating', 'waiting'];
  const stateCat = (a) => (a.needs ? 'needs' : RUNNING.includes(a.state) ? 'working' : a.state);
  const openAgent = (a) => { location.hash = '#/s/' + encodeURIComponent(a.session) + (a.agent_id ? '/a/' + encodeURIComponent(a.agent_id) : ''); };
  /* One cached node per key: the first render inserts the fresh node, later
     renders patch the fresh build into it. */
  function keep(cache, k, fresh) {
    const old = cache.get(k);
    const el = old ? patch(old, fresh) : fresh;
    cache.set(k, el);
    return el;
  }
  function glyph(cat) {
    const v = s('svg', { class: 'sg sg-' + cat, viewBox: '0 0 12 12', 'aria-hidden': 'true' });
    if (cat === 'done') v.append(s('path', { d: 'M2.6 6.3 5 8.6l4.4-5', fill: 'none', stroke: 'currentColor', 'stroke-width': 1.6, 'stroke-linecap': 'round', 'stroke-linejoin': 'round' }));
    else if (cat === 'failed') v.append(s('path', { d: 'M3.2 3.2l5.6 5.6M8.8 3.2 3.2 8.8', fill: 'none', stroke: 'currentColor', 'stroke-width': 1.6, 'stroke-linecap': 'round' }));
    else if (cat === 'working' || cat === 'needs') v.append(s('circle', { cx: 6, cy: 6, r: 3.6, fill: 'currentColor' }));
    else v.append(s('circle', { cx: 6, cy: 6, r: 3.4, fill: 'none', stroke: 'currentColor', 'stroke-width': 1.4, 'stroke-dasharray': cat === 'stalled' ? '1.6 1.6' : null }));
    return v;
  }
  /* Tool calls per minute over the last 15 minutes: history bars in a
     recessive tone, the current minute in the main tone. One scale for every
     row, so a busy agent looks busier than a quiet one. The hover reads the
     values from the element, which patches keep current. */
  function sparkTip(ev) {
    const svg = ev.currentTarget;
    const vals = (svg.getAttribute('data-spark') || '').split(',').map(Number);
    const end = Number(svg.getAttribute('data-end'));
    const n = vals.length, r = svg.getBoundingClientRect();
    const i = Math.max(0, Math.min(n - 1, Math.floor((ev.clientX - r.left) / (r.width / n))));
    showTip(ev, clock(end - (n - i) * 60, false) + (i === n - 1 ? ', this minute' : ''), [{ name: 'Tool calls', value: int(vals[i]), color: i === n - 1 ? 'var(--series-main)' : 'var(--ink-3)' }]);
  }
  function sparkline(a, max) {
    const n = a.spark.length, W = 76, H = 18, bw = 4, gap = W / n - bw;
    const svg = s('svg', { class: 'spark', viewBox: `0 0 ${W} ${H}`, width: W, height: H, role: 'img', 'data-spark': a.spark.join(','), 'data-end': a.spark_end,
      'aria-label': 'Tool calls per minute, last ' + n + ' minutes: ' + a.spark.join(', ') });
    a.spark.forEach((v, i) => {
      const x = i * (bw + gap) + gap / 2;
      if (!v) { svg.append(s('rect', { class: 'sp0', x, y: H - 1, width: bw, height: 1 })); return; }
      const hh = Math.max(2, Math.round((v / max) * (H - 1)));
      svg.append(s('path', { class: i === n - 1 ? 'sp-now' : 'sp', d: topRounded(x, H - hh, bw, hh, 1.2) }));
    });
    svg.addEventListener('pointermove', sparkTip);
    svg.addEventListener('pointerleave', hideTip);
    return svg;
  }
  function rateText(b) {
    if (!b || !b.tok_min) return '';
    return b.cost_min >= 0.005 ? money(b.cost_min) + '/min' : compact(b.tok_min) + ' tok/min';
  }

  views.live = {
    mount(root) {
      this.seg = h('div', { class: 'seg', role: 'group', 'aria-label': 'Live view' }, LIVE_MODES.map((m) => h('button', { type: 'button', 'aria-pressed': String(liveMode === m), dataset: { mode: m }, onclick: () => this.setMode(m) }, m === 'agents' ? 'Agents' : 'Sessions')));
      this.head = h('div', { class: 'page-head' }, h('div', {}, h('h1', { text: 'Live' }), this.sub = h('div', { class: 'sub', text: 'What your agents are doing right now.' })), this.seg);
      this.body = h('div', {});
      root.append(this.head, this.body);
      this.reset();
      this.prevCat = new Map();
      this.seenOnce = false;
      if (!alerts.timer) pollAlerts();
    },
    reset() {
      this.rows = new Map();      // agent or workflow key -> node
      this.groups = new Map();    // session id -> {el, head, list}
      this.feedItems = new Map(); // feed id -> node
      this.cards = new Map();     // session id -> card node (Sessions mode)
      this.shown = null;          // what this.body holds: 'board', 'cards', 'empty' or null
      this.loose = null;
      this.feedEmpty = null;
    },
    setMode(m) {
      liveMode = m;
      store.set('cc-dash-live-mode', m);
      for (const b of this.seg.children) b.setAttribute('aria-pressed', String(b.dataset.mode === m));
      S.ui.liveSel = -1;
      this.reset();
      this.update();
    },
    show(kind, el) { if (this.shown !== kind) { this.body.replaceChildren(el); this.shown = kind; } },
    update() {
      const d = S.data.live;
      if (!d) {
        const want = S.lastFail ? 'error' : 'skeleton';
        if (!this.shown || (this.shown !== want && ['skeleton', 'error'].includes(this.shown))) {
          this.body.replaceChildren(want === 'error' ? errorBox({ message: S.lastFail }, 'live activity') : skeleton(liveMode === 'agents' ? 'agents' : 'cards'));
          this.shown = want;
        }
        return;
      }
      if (liveMode === 'sessions') this.sessions(d); else this.agents(d);
      this.seenOnce = true;
    },

    // Agents mode ------------------------------------------------------
    agents(d) {
      const byKey = new Map();
      for (const al of alerts.list) { const k = al.agent_id ? al.session + '/' + al.agent_id : al.session; if (!byKey.has(k)) byKey.set(k, al); }
      const matched = new Set();
      const list = (d.agents || []).map((a) => {
        const al = byKey.get(a.key);
        if (!al) return a;
        matched.add(al);
        return { ...a, needs: { kind: al.kind, title: al.title, message: al.message, tool: al.tool, since: isoTs(al.since), source: al.source } };
      });
      const loose = alerts.list.filter((al) => !matched.has(al));
      const cats = { working: 0, needs: 0, idle: 0, done: 0, failed: 0, stalled: 0 };
      for (const a of list) cats[stateCat(a)] = (cats[stateCat(a)] || 0) + 1;
      cats.needs += loose.length;
      const groups = (d.groups || []).map((g) => ({ ...g, rows: list.filter((a) => a.session === g.session) }));
      for (const g of groups) g.needsNow = g.rows.filter((a) => a.needs).length;
      groups.sort((a, b) => (b.needsNow > 0) - (a.needsNow > 0) || (b.running > 0) - (a.running > 0) || b.last_write - a.last_write);
      if (!list.length && !loose.length) {
        setText(this.sub, 'What your agents are doing right now. Updates every 3 seconds.');
        if (this.shown !== 'empty') { this.reset(); this.show('empty', this.empty(true)); }
        return;
      }
      setText(this.sub, this.summary(cats, groups.length));
      if (this.shown !== 'board') {
        this.reset();
        this.boardEl = h('div', { class: 'board' });
        this.feedList = h('div', { class: 'feed-list', role: 'list' });
        this.feedNote = h('span', { class: 'faint feed-note' });
        const feed = h('aside', { class: 'panel feed', 'aria-label': 'Activity feed' },
          h('div', { class: 'feed-h' }, h('h2', { text: 'Activity' }), this.feedNote), this.feedList);
        feed.addEventListener('pointerenter', () => { this.feedPaused = true; this.feedNoteText(); });
        feed.addEventListener('pointerleave', () => { this.feedPaused = false; this.feed(S.data.live); });
        this.boardEl.addEventListener('focusin', (e) => { const r = e.target.closest('.arow[data-key]'); this.focusKey = r ? r.dataset.key : null; });
        this.feedPaused = false;
        this.show('board', h('div', { class: 'board-wrap' }, this.boardEl, feed));
      }
      const max = Math.max(4, ...list.map((a) => Math.max(...a.spark)));
      const wfs = new Map((d.workflows || []).map((w) => [w.run_id, w]));
      const flat = [];
      const els = [];
      if (loose.length) els.push(this.looseGroup(loose));
      else this.loose = null;
      for (const g of groups) els.push(this.group(g, wfs, max, flat));
      reconcile(this.boardEl, els, leave);
      this.flat = flat;
      const live = new Set(list.map((a) => a.key));
      for (const a of list) this.prevCat.set(a.key, stateCat(a));
      for (const k of [...this.rows.keys()]) if (!live.has(k) && !k.startsWith('wf:')) { this.rows.delete(k); this.prevCat.delete(k); }
      const wfLive = new Set((d.workflows || []).map((w) => 'wf:' + w.run_id));
      for (const k of [...this.rows.keys()]) if (k.startsWith('wf:') && !wfLive.has(k)) this.rows.delete(k);
      for (const k of [...this.groups.keys()]) if (!groups.some((g) => g.session === k)) this.groups.delete(k);
      this.feed(d);
    },
    summary(c, nGroups) {
      const parts = [];
      if (c.needs) parts.push(c.needs + ' need' + (c.needs === 1 ? 's' : '') + ' you');
      parts.push(plural(c.working, 'agent') + ' working');
      if (c.idle) parts.push(c.idle + ' idle');
      if (c.stalled) parts.push(c.stalled + ' stalled');
      if (c.done + c.failed) parts.push((c.done + c.failed) + ' just finished' + (c.failed ? ' (' + c.failed + ' failed)' : ''));
      return parts.join(', ') + (nGroups ? ' in ' + plural(nGroups, 'session') : '') + '. Updates every 3 seconds.';
    },
    group(g, wfs, max, flat) {
      let c = this.groups.get(g.session);
      if (!c) {
        c = { el: h('section', { class: 'agroup' + (this.seenOnce ? ' enter' : ''), dataset: { key: 'g:' + g.session } }), head: null, list: h('div', { class: 'agroup-b', role: 'list' }) };
        this.groups.set(g.session, c);
      } else if (c.el.classList.contains('enter')) c.el.classList.remove('enter');
      const working = g.rows.filter((a) => RUNNING.includes(a.state)).length;
      const head = h('div', { class: 'agroup-h' },
        h('a', { class: 'g-proj', href: '#/s/' + encodeURIComponent(g.session) }, g.project),
        g.worktree ? h('span', { class: 'tag', text: g.worktree }) : null,
        showBranch(g) ? h('span', { class: 'branch ellipsis', text: g.branch }) : null,
        h('span', { class: 'spacer' }),
        g.needsNow ? h('span', { class: 'g-needs', text: g.needsNow + ' need' + (g.needsNow === 1 ? 's' : '') + ' you' }) : null,
        h('span', { class: 'nowrap', text: plural(g.rows.length, 'agent') + (working ? ', ' + working + ' working' : '') }),
        h('span', { class: 'nowrap' }, h('b', { text: money(g.cost) }), ' est.'),
        h('span', { class: 'nowrap faint', text: 'started ' + dateTime(g.start) }));
      if (!c.head) { c.head = head; c.el.append(c.head, c.list); } else c.head = patch(c.head, head);
      const kids = [];
      const seenRun = new Set();
      for (const a of g.rows) {
        if (a.run_id && !seenRun.has(a.run_id)) {
          seenRun.add(a.run_id);
          const w = wfs.get(a.run_id);
          if (w) kids.push(this.wfRow(w));
        }
        flat.push(a);
        kids.push(this.row(a, max, flat.length - 1));
      }
      reconcile(c.list, kids, leave);
      return c.el;
    },
    wfRow(w) {
      const prog = (w.total ? w.finished + ' of ' + w.total + ' finished' : plural(w.finished, 'agent') + ' finished') + (w.running ? ', ' + w.running + ' running' : '') + (w.failed ? ', ' + w.failed + ' failed' : '');
      return keep(this.rows, 'wf:' + w.run_id, h('div', { class: 'wfrow', role: 'listitem', dataset: { key: 'wf:' + w.run_id }, style: { '--d': String(w.depth) } },
        w.depth ? h('span', { class: 'a-branch', 'aria-hidden': 'true' }) : null,
        h('span', { class: 'tag', text: 'Workflow' }), h('span', { class: 'wf-name ellipsis', text: w.name }),
        h('span', { class: 'faint nowrap', text: prog }),
        w.phases && w.phases.length ? h('span', { class: 'faint ellipsis', text: w.phases.join(', ') }) : null));
    },
    row(a, max, idx) {
      const cat = stateCat(a);
      const prev = this.prevCat.get(a.key);
      const isNew = this.seenOnce && !this.rows.has(a.key) && prev === undefined;
      const changed = prev !== undefined && prev !== cat && ['done', 'failed', 'needs'].includes(cat);
      const rate = RUNNING.includes(a.state) ? rateText(a.burn) : '';
      const [line1, line2, word] = this.activity(a, cat);
      const meta = [a.model ? modelTag(a.model, a.family) : h('span', { class: 'faint', text: 'no model yet' })];
      if (a.kind === 'main') meta.push(h('span', { class: 'faint', text: 'main thread' }));
      else meta.push(h('span', { class: 'mono faint', text: a.type === 'workflow-subagent' ? (a.phase || 'workflow agent') : a.type }));
      if (a.background) meta.push(h('span', { class: 'faint', text: 'background' }));
      const old = this.rows.get(a.key);
      // "flash" stays on while the state holds, so a patch never restarts it; it is gone after the next change.
      const flash = changed || (old && old.classList.contains('flash') && prev === cat);
      const fresh = h('div', { class: 'arow c-' + cat + (idx === S.ui.liveSel ? ' sel' : '') + (isNew ? ' enter' : '') + (flash ? ' flash' : ''), role: 'listitem', tabindex: 0, dataset: { key: a.key },
        'aria-label': a.label + ': ' + word, onclick: () => openAgent(a), onkeydown: (e) => { if (e.key === 'Enter') openAgent(a); } },
      h('div', { class: 'a-id', style: { '--d': String(a.depth) } },
        a.depth ? h('span', { class: 'a-branch', 'aria-hidden': 'true' }) : null, glyph(cat),
        h('div', { class: 'a-idt' }, h('div', { class: 'a-label ellipsis', text: a.label, title: a.label }), h('div', { class: 'a-meta' }, meta))),
      h('div', { class: 'a-act' }, line1, line2),
      h('div', { class: 'a-spark' }, sparkline(a, max)),
      h('div', { class: 'a-num' }, h('div', { text: compact(a.tokens), title: int(a.tokens) + ' tokens, cache reads included' }), h('div', { class: 'faint', text: rate || 'tokens', title: rate ? 'Last 5 minutes: ' + compact(a.burn.tok_min) + ' tokens per minute (' + compact(a.burn.out_min) + ' output), about ' + money(a.burn.cost_min, true) + ' per minute' : null })),
      h('div', { class: 'a-num' }, h('div', { text: int(a.tool_calls) }), a.errors ? h('div', { class: 'bad', text: plural(a.errors, 'error') }) : h('div', { class: 'faint', text: 'calls' })));
      return keep(this.rows, a.key, fresh);
    },
    /* [state line, detail line, a word or two for screen readers] */
    activity(a, cat) {
      const since = (t, pre) => h('span', { class: 'faint nowrap num' }, pre || '', lsince(t));
      const agoEl = (t) => lago(t, 'faint nowrap');
      const detail = (text, mono) => h('div', { class: 'a-detail' + (mono ? ' mono' : ''), text: text || '', title: text || null });
      const t = a.tool;
      if (a.needs) return [h('div', { class: 'a-state' }, needsPill(a.needs), a.needs.since ? agoEl(a.needs.since) : null), detail(a.needs.message || (t ? t.target : '') || a.last_text), 'needs you, ' + needsText(a.needs)];
      if (a.state === 'tool') return [h('div', { class: 'a-state' }, h('span', { class: 'st' }, 'Running ', h('b', { class: 'mono', text: t.name + (t.n > 1 ? ' ×' + t.n : '') })), since(t.t)), detail(t.target || t.name, true), 'running ' + t.name];
      if (a.state === 'waiting') {
        const what = a.waiting_on ? 'Waiting on ' + plural(a.waiting_on, 'agent') : 'Waiting on its subagent';
        return [h('div', { class: 'a-state' }, h('span', { class: 'st', text: what }), t ? since(t.t) : null), detail(t ? t.target : a.last_text, !!t), what.toLowerCase()];
      }
      if (a.state === 'generating') {
        const what = a.tool_calls || a.last_text ? 'Thinking' : 'Starting';
        return [h('div', { class: 'a-state' }, h('span', { class: 'st', text: what }), since(a.since)), detail(a.last_tool ? 'after ' + a.last_tool.name + '  ' + (a.last_tool.target || '') : a.last_text, !!a.last_tool), what.toLowerCase()];
      }
      if (a.state === 'idle') return [h('div', { class: 'a-state' }, h('span', { class: 'st', text: 'Your turn' }), agoEl(a.since)), detail(a.last_text), 'your turn'];
      if (a.state === 'done') return [h('div', { class: 'a-state' }, h('span', { class: 'st', text: 'Finished' }), agoEl(a.since)), detail(a.last_text), 'finished'];
      if (a.state === 'failed') return [h('div', { class: 'a-state' }, h('span', { class: 'st bad', text: 'Failed' }), agoEl(a.since)), detail(a.last_text), 'failed'];
      if (a.state === 'stalled') {
        const was = t ? 'was running ' + t.name + (t.target ? ': ' + t.target : '') : 'was thinking';
        return [h('div', { class: 'a-state' }, h('span', { class: 'st', text: 'Stalled?' }), since(a.last_write, 'no writes for ')), detail(was, !!t), 'stalled, no recent writes'];
      }
      return [h('div', { class: 'a-state', text: a.state }), detail(''), a.state];
    },
    looseGroup(list) {
      const fresh = h('section', { class: 'agroup loose', dataset: { key: 'g:loose' } }, h('div', { class: 'agroup-h' }, h('span', { class: 'g-proj', text: 'Needs you' }), h('span', { class: 'faint', text: 'sessions not on the board' })),
        h('div', { class: 'agroup-b', role: 'list' }, list.map((al) => {
          const a = { session: al.session, agent_id: al.agent_id };
          return h('div', { class: 'arow c-needs', role: 'listitem', tabindex: 0, dataset: { key: 'alert:' + (al.id || al.session) }, onclick: () => openAgent(a), onkeydown: (e) => { if (e.key === 'Enter') openAgent(a); } },
            h('div', { class: 'a-id' }, glyph('needs'), h('div', { class: 'a-idt' }, h('div', { class: 'a-label ellipsis', text: al.title || al.project || al.session }), h('div', { class: 'a-meta' }, h('span', { text: al.project || '' }), al.agent_id ? h('span', { class: 'faint', text: 'subagent' }) : null))),
            h('div', { class: 'a-act' }, h('div', { class: 'a-state' }, needsPill(al), isoTs(al.since) ? lago(isoTs(al.since), 'faint') : null), h('div', { class: 'a-detail', text: al.message || '' })));
        })));
      this.loose = this.loose ? patch(this.loose, fresh) : fresh;
      return this.loose;
    },
    feed(d) {
      if (this.shown !== 'board' || !d) return;
      const items = d.feed || [];
      if (this.feedPaused) {   // hovered: the list does not move; the note counts what is waiting
        this.feedPending = items.filter((f) => !this.feedItems.has(f.key + '|' + f.id)).length;
        this.feedNoteText();
        return;
      }
      const next = new Map();
      const els = items.map((f) => {
        const k = f.key + '|' + f.id;
        const who = f.kind === 'main' ? f.project : f.label;
        const target = { session: f.key.split('/')[0], agent_id: f.key.includes('/') ? f.key.split('/')[1] : null };
        const el = keep(this.feedItems, k, h('div', { class: 'fi' + (f.status === 'error' || f.status === 'blocked' ? ' err' : ''), role: 'listitem', dataset: { key: k }, onclick: () => openAgent(target) },
          h('div', { class: 'fi-1' }, h('span', { class: 'num faint', text: clock(f.t) }), key(f.family), h('span', { class: 'fi-who ellipsis', text: who, title: f.label }),
            f.status === 'pending' ? h('span', { class: 'fi-run', text: 'running' }) : f.status === 'blocked' ? h('span', { class: 'bad', text: 'blocked' }) : f.status === 'error' ? h('span', { class: 'bad', text: 'failed' }) : null),
          h('div', { class: 'fi-2' }, h('span', { class: 'fi-tool', text: f.name }), h('span', { class: 'fi-target', text: f.target || '', title: f.target || null }))));
        next.set(k, el);
        return el;
      });
      this.feedItems = next;
      this.feedPending = 0;
      if (!els.length) { this.feedEmpty = this.feedEmpty || h('div', { class: 'faint feed-empty', text: 'No tool calls in the last 15 minutes.' }); els.push(this.feedEmpty); }
      reconcile(this.feedList, els);
      this.feedNoteText();
    },
    feedNoteText() {
      if (!this.feedNote) return;
      setText(this.feedNote, this.feedPaused ? 'Paused' + (this.feedPending ? ', ' + plural(this.feedPending, 'new call') : '') : 'Tool calls, newest first');
      if (this.feedNote.classList.contains('paused') !== !!this.feedPaused) this.feedNote.classList.toggle('paused', !!this.feedPaused);
    },
    empty(agents) {
      const recent = S.data.sessions && S.data.sessions.sessions[0];
      return h('div', { class: 'empty' + (this.seenOnce ? '' : ' fade-in') }, h('h2', { text: agents ? 'No agents are working right now' : 'Nothing is running right now' }),
        h('p', { text: agents ? 'An agent shows up here while Claude Code writes its transcript. Finished agents stay for two minutes, idle sessions for ten.' : 'A session shows up here while Claude Code is writing its transcript, and stays for 10 minutes after the last write.' }),
        recent ? h('p', {}, 'Last activity: ', h('a', { href: '#/s/' + encodeURIComponent(recent.id), style: { color: 'var(--ink)', fontWeight: 500 }, text: recent.title }), ' in ' + recent.project + ', ' + ago(recent.last_write) + '.') : null,
        h('a', { class: 'btn', href: '#/sessions', style: { display: 'inline-flex', alignItems: 'center' }, text: 'Browse sessions' }));
    },

    // Sessions mode (the original Live view, now patched in place) -----
    sessions(d) {
      const list = d.sessions;
      if (!list.length) {
        setText(this.sub, 'Sessions whose transcript changed in the last 10 minutes.');
        if (this.shown !== 'empty') { this.reset(); this.show('empty', this.empty(false)); }
        return;
      }
      setText(this.sub, plural(list.length, 'session') + ' active in the last ' + Math.round(d.live_secs / 60) + ' minutes. Updates every 3 seconds.');
      if (S.ui.liveSel >= list.length) S.ui.liveSel = list.length - 1;
      if (this.shown !== 'cards') { this.reset(); this.grid = h('div', { class: 'live-grid' }); this.show('cards', this.grid); }
      const next = new Map();
      const els = list.map((r, i) => { const el = keep(this.cards, r.id, this.card(r, i, !this.cards.has(r.id) && this.seenOnce)); next.set(r.id, el); return el; });
      this.cards = next;
      reconcile(this.grid, els, leave);
    },
    card(r, i, isNew) {
      const open = () => { location.hash = '#/s/' + encodeURIComponent(r.id); };
      const alert = alerts.list.find((al) => al.session === r.id);
      let now;
      if (r.pending) {
        now = h('div', { class: 'now' }, h('div', { class: 'now-label' }, 'Running ' + r.pending.name, h('span', { class: 'faint' }, 'for ', lsince(r.pending.t || r.last_write))),
          h('div', { class: 'now-body', text: r.pending.target || r.pending.name, title: r.pending.target || '' }));
      } else if (r.running_subs.length) {
        const busy = r.running_subs.slice().sort((a, b) => ((b.last_tool && b.last_tool.t) || 0) - ((a.last_tool && a.last_tool.t) || 0))[0];
        now = h('div', { class: 'now' }, h('div', { class: 'now-label' }, 'Waiting on ' + plural(r.running_subs.length, 'subagent') + ', latest from ' + (busy.description || busy.agent_type || 'a subagent'),
          busy.last_tool ? lago(busy.last_tool.t, 'faint') : null),
          h('div', { class: 'now-body' }, busy.last_tool ? [h('span', { class: 'muted', text: busy.last_tool.name + '  ' }), busy.last_tool.target || ''] : (busy.last_text || 'starting')));
      } else if (r.state === 'waiting' && r.last_text) {
        now = h('div', { class: 'now' }, h('div', { class: 'now-label', text: 'Last reply' }), h('div', { class: 'now-text', text: r.last_text }));
      } else if (r.last_tool) {
        now = h('div', { class: 'now' }, h('div', { class: 'now-label' }, 'Last tool call ', lago(r.last_tool.t, 'faint')),
          h('div', { class: 'now-body' }, h('span', { class: 'muted', text: r.last_tool.name + '  ' }), r.last_tool.target || ''));
      } else {
        now = h('div', { class: 'now' }, h('div', { class: 'now-label', text: 'Starting' }), h('div', { class: 'now-text', text: r.first_prompt || 'No prompt yet' }));
      }
      const recent = r.recent.filter((e) => e.k === 'tool' && !(r.last_tool && !r.pending && !r.running_subs.length && r.state !== 'waiting' && e.t === r.last_tool.t && e.name === r.last_tool.name)).slice(-4).reverse();
      return h('article', { class: 'card' + (isNew ? ' enter' : '') + (i === S.ui.liveSel ? ' sel' : ''), tabindex: 0, role: 'link', 'aria-label': r.title, dataset: { key: r.id }, onclick: open, onkeydown: (e) => { if (e.key === 'Enter') open(); } },
        h('div', { class: 'card-top' }, alert ? needsPill(alert) : statePill(r.state), h('span', { class: 'proj', text: r.project }), r.worktree ? h('span', { class: 'tag', text: r.worktree }) : null,
          showBranch(r) ? h('span', { class: 'branch ellipsis', text: r.branch }) : null, h('span', { class: 'spacer' }),
          r.last_model ? modelTag(r.last_model, r.last_family) : null, lago(r.last_write, 'faint nowrap')),
        h('div', { class: 'card-title', text: r.title }),
        now,
        recent.length ? h('div', { class: 'recent' }, recent.map((e) => h('div', {}, h('span', { class: 'num faint', text: clock(e.t) }), h('span', { class: 'rn', text: e.name }),
          h('span', { class: 'rt' + (e.status === 'error' || e.status === 'blocked' ? ' bad' : ''), text: e.status === 'blocked' ? 'blocked by guard: ' + e.target : (e.target || '') })))) : null,
        r.running_subs.length ? h('div', { class: 'subs' }, h('div', { class: 'subs-h', text: plural(r.running_subs.length, 'subagent') + ' running' }),
          r.running_subs.slice(0, 6).map((x) => h('div', { class: 'sub-row' }, key(x.family), h('span', { class: 'd ellipsis', text: x.description || x.agent_type, title: x.description }),
            h('span', { class: 'ellipsis mono muted', text: x.last_tool ? x.last_tool.name + ' ' + (x.last_tool.target || '') : (x.agent_type || '') }),
            x.start ? lsince(x.start, 'num muted r') : h('span', { class: 'num muted r' }),
            h('span', { class: 'num muted', style: { textAlign: 'right' }, text: compact(x.tokens) + ' tok' }))),
          r.running_subs.length > 6 ? h('div', { class: 'faint', text: '+' + (r.running_subs.length - 6) + ' more' }) : null) : null,
        h('div', { class: 'card-foot' }, h('span', {}, h('b', { text: money(r.cost) }), ' est.'), h('span', {}, h('b', { text: compact(r.tokens) }), ' tokens'),
          h('span', {}, h('b', { text: int(r.tool_calls_all) }), ' tool calls'), h('span', {}, h('b', { text: int(r.subagents) }), ' subagents'),
          r.errors ? h('span', { class: 'bad' }, h('b', { class: 'bad', text: int(r.errors) }), ' errors') : null,
          h('span', { class: 'spacer' }), h('span', { text: 'started ' + dateTime(r.start) })));
    },
    keys(k) {
      if (k === 'v') { this.setMode(liveMode === 'agents' ? 'sessions' : 'agents'); return true; }
      if (liveMode === 'agents') {
        const list = this.flat || [];
        if (!list.length) return false;
        if (k === 'j' || k === 'k') {
          S.ui.liveSel = Math.max(0, Math.min(list.length - 1, S.ui.liveSel + (k === 'j' ? 1 : -1)));
          update();
          const el = this.boardEl.querySelector('.arow.sel');
          if (el) { el.scrollIntoView({ block: 'nearest' }); el.focus({ preventScroll: true }); }
          return true;
        }
        if (k === 'Enter' && list[S.ui.liveSel]) { openAgent(list[S.ui.liveSel]); return true; }
        return false;
      }
      const list = S.data.live ? S.data.live.sessions : [];
      if (!list.length) return false;
      if (k === 'j' || k === 'k') { S.ui.liveSel = Math.max(0, Math.min(list.length - 1, S.ui.liveSel + (k === 'j' ? 1 : -1))); update(); const el = main.querySelectorAll('.card')[S.ui.liveSel]; if (el) el.scrollIntoView({ block: 'nearest' }); return true; }
      if (k === 'Enter' && S.ui.liveSel >= 0) { location.hash = '#/s/' + encodeURIComponent(list[S.ui.liveSel].id); return true; }
      return false;
    },
  };

  /* Project picker: a button that opens a filterable listbox of projects with
     session counts; worktrees fold under their project (→ or the chevron shows
     them, typing finds them). It is built once per view and only patched on a
     poll, so an open list never closes or changes under the pointer. */
  let pickerSeq = 0;
  function projectPicker({ label, allLabel, withWorktrees, onPick }) {
    const uid = 'pk' + (++pickerSeq);
    const st = { open: false, q: '', active: '', exp: new Set(), items: [], value: { p: '', wt: '' }, total: 0 };
    const keyOf = (p, wt) => (wt ? 'w:' + p + '\u0000' + wt : 'p:' + p);
    const btnLabel = h('span', { class: 'picker-label ellipsis' });
    const btnCount = h('span', { class: 'picker-n num' });
    const btn = h('button', { type: 'button', class: 'picker-btn', 'aria-haspopup': 'listbox', 'aria-expanded': 'false', 'aria-label': label,
      onclick: () => (st.open ? close(false) : open()),
      onkeydown: (e) => { if (e.key === 'ArrowDown' || e.key === 'ArrowUp') { e.preventDefault(); open(); } } }, btnLabel, btnCount, chev());
    const input = h('input', { type: 'text', class: 'picker-q', role: 'combobox', 'aria-autocomplete': 'list', 'aria-expanded': 'true', 'aria-controls': uid + '-list',
      placeholder: withWorktrees ? 'Filter projects and worktrees' : 'Filter projects', 'aria-label': 'Filter ' + label.toLowerCase(), autocomplete: 'off', spellcheck: 'false',
      oninput: (e) => { st.q = e.target.value; const v = visible(); st.active = v.length ? (v.find((r) => r.key === st.active) || v[0]).key : ''; render(); },
      onkeydown: (e) => keys(e) });
    const list = h('ul', { class: 'picker-list', role: 'listbox', id: uid + '-list', 'aria-label': label });
    const hint = h('div', { class: 'picker-hint', text: withWorktrees ? '↑ ↓ move  ·  Enter pick  ·  → worktrees  ·  Esc close' : '↑ ↓ move  ·  Enter pick  ·  Esc close' });
    const pop = h('div', { class: 'picker-pop', hidden: true }, h('div', { class: 'picker-search' }, input), list, hint);
    const el = h('div', { class: 'picker' }, btn, pop);
    function visible() {
      const q = st.q.trim().toLowerCase();
      const rows = [];
      if (!q) rows.push({ key: keyOf('', ''), p: '', wt: '', name: allLabel, n: st.total, all: true });
      for (const it of st.items) {
        const wts = withWorktrees ? (it.worktrees || []) : [];
        const pm = !q || it.project.toLowerCase().includes(q);
        const wm = q ? wts.filter((w) => w.name.toLowerCase().includes(q)) : [];
        if (!pm && !wm.length) continue;
        const expanded = st.exp.has(it.project);
        rows.push({ key: keyOf(it.project, ''), p: it.project, wt: '', name: it.project, n: it.sessions, nwt: wts.length || it.worktrees_n || 0, expanded, canExpand: wts.length > 0 });
        const show = expanded ? wts : wm;
        for (const w of show) rows.push({ key: keyOf(it.project, w.name), p: it.project, wt: w.name, name: w.name, n: w.sessions, child: true });
      }
      return rows;
    }
    function render() {
      const rows = visible();
      const sel = keyOf(st.value.p, st.value.wt);
      if (!rows.some((r) => r.key === st.active)) st.active = rows.length ? (rows.find((r) => r.key === sel) || rows[0]).key : '';
      P.into(list, rows.length ? rows.map((r, i) => h('li', {
        role: 'option', id: uid + '-o' + i, dataset: { key: r.key }, 'aria-selected': String(r.key === sel),
        class: 'picker-opt' + (r.child ? ' child' : '') + (r.all ? ' all' : '') + (r.key === st.active ? ' active' : ''),
        onpointermove: () => { if (st.active !== r.key) { st.active = r.key; render(); } },
        onclick: () => pick(r),
      },
      r.canExpand ? h('button', { type: 'button', class: 'picker-exp' + (r.expanded ? ' open' : ''), tabindex: -1, 'aria-label': (r.expanded ? 'Hide ' : 'Show ') + plural(r.nwt, 'worktree'),
        onclick: (e) => { e.stopPropagation(); toggle(r.p); } }, chev()) : h('span', { class: 'picker-exp-sp', 'aria-hidden': 'true' }),
      h('span', { class: 'picker-name ellipsis', text: r.child ? r.name : r.name, title: r.name }),
      r.nwt && !r.child ? h('span', { class: 'picker-wt', text: plural(r.nwt, 'worktree') }) : null,
      h('span', { class: 'picker-n num', text: int(r.n) }))) : h('li', { class: 'picker-empty', role: 'presentation', dataset: { key: 'none' }, text: 'No project matches' }));
      const ai = rows.findIndex((r) => r.key === st.active);
      P.attr(input, 'aria-activedescendant', ai >= 0 ? uid + '-o' + ai : null);
      const v = st.value;
      P.setText(btnLabel, v.p ? (v.wt ? v.p + ' › ' + v.wt : v.p) : allLabel);
      const cur = v.p ? st.items.find((x) => x.project === v.p) : null;
      const curN = !v.p ? st.total : cur ? (v.wt ? ((cur.worktrees || []).find((w) => w.name === v.wt) || { sessions: 0 }).sessions : cur.sessions) : 0;
      P.setText(btnCount, int(curN));
      P.attr(btn, 'aria-expanded', String(st.open));
      P.attr(btn, 'class', 'picker-btn' + (v.p ? ' on' : ''));
    }
    function scrollActive() { const a = list.querySelector('.active'); if (a) a.scrollIntoView({ block: 'nearest' }); }
    function toggle(p) { if (st.exp.has(p)) st.exp.delete(p); else st.exp.add(p); render(); }
    function open() {
      if (st.open) return;
      st.open = true; st.q = ''; input.value = '';
      if (st.value.p) st.exp.add(st.value.p);
      st.active = keyOf(st.value.p, st.value.wt);
      pop.hidden = false;
      render();
      input.focus();
      scrollActive();
      document.addEventListener('pointerdown', outside, true);
    }
    function close(refocus) {
      if (!st.open) return;
      st.open = false; pop.hidden = true;
      document.removeEventListener('pointerdown', outside, true);
      render();
      if (refocus) btn.focus();
    }
    function outside(e) { if (!el.contains(e.target)) close(false); }
    function pick(r) { st.value = { p: r.p, wt: r.wt }; close(true); onPick(r.p, r.wt); }
    function keys(e) {
      const rows = visible();
      const i = rows.findIndex((r) => r.key === st.active);
      const go = (j) => { if (!rows.length) return; st.active = rows[Math.max(0, Math.min(rows.length - 1, j))].key; render(); scrollActive(); };
      const r = rows[i];
      if (e.key === 'ArrowDown') { e.preventDefault(); go(i + 1); }
      else if (e.key === 'ArrowUp') { e.preventDefault(); go(i - 1); }
      else if (e.key === 'Home' && !st.q) { e.preventDefault(); go(0); }
      else if (e.key === 'End' && !st.q) { e.preventDefault(); go(rows.length - 1); }
      else if (e.key === 'PageDown') { e.preventDefault(); go(i + 8); }
      else if (e.key === 'PageUp') { e.preventDefault(); go(i - 8); }
      else if (e.key === 'Enter') { e.preventDefault(); if (r) pick(r); }
      else if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); close(true); }
      else if (e.key === 'Tab') close(false);
      else if (e.key === 'ArrowRight' && r && r.canExpand && !r.expanded && input.selectionStart === input.value.length) { e.preventDefault(); toggle(r.p); }
      else if (e.key === 'ArrowLeft' && r && (r.expanded || r.child) && input.selectionStart === 0) {
        e.preventDefault();
        if (r.child) { st.active = keyOf(r.p, ''); if (st.exp.has(r.p)) st.exp.delete(r.p); render(); scrollActive(); } else toggle(r.p);
      }
    }
    return {
      el,
      /* items: [{project, sessions, worktrees:[{name, sessions}]}]; value: {p, wt} */
      set(items, value, total) {
        st.items = items; st.total = total;
        if (value) st.value = { p: value.p || '', wt: value.wt || '' };
        render();
      },
      isOpen: () => st.open,
    };
  }

  // Sessions -------------------------------------------------------------
  const COLS = [
    { k: 'state', label: '', w: '28px' },
    { k: 'title', label: 'Session', sort: (r) => r.title.toLowerCase() },
    { k: 'last_write', label: 'Last active', r: true },
    { k: 'active', label: 'Active', r: true },
    { k: 'model', label: 'Model', sort: (r) => r.model },
    { k: 'prompts', label: 'Turns', r: true },
    { k: 'tool_calls_all', label: 'Tool calls', r: true },
    { k: 'subagents', label: 'Subagents', r: true },
    { k: 'tokens', label: 'Tokens', r: true },
    { k: 'cost', label: 'Cost', r: true },
    { k: 'errors', label: 'Errors', r: true },
    { k: 'guard', label: 'Guard', r: true },
  ];
  function filteredSessions() {
    const d = S.data.sessions;
    if (!d) return [];
    const q = S.ui.q.trim().toLowerCase();
    let rows = d.sessions.filter((r) => (!S.ui.project || r.project === S.ui.project) && (!S.ui.wt || r.worktree === S.ui.wt)
      && (!S.ui.family || r.family === S.ui.family) && (!S.ui.errorsOnly || r.errors > 0 || r.guard > 0));
    if (q) rows = rows.filter((r) => [r.title, r.first_prompt, r.project, r.branch, r.cwd, r.id, r.worktree, r.models.join(' ')].join(' ').toLowerCase().includes(q));
    const c = COLS.find((x) => x.k === S.ui.sort.k) || COLS[2];
    const get = c.sort || ((r) => r[c.k] || 0);
    rows = rows.slice().sort((a, b) => { const x = get(a), y = get(b); return (x < y ? -1 : x > y ? 1 : 0) * S.ui.sort.d; });
    return rows;
  }
  views.sessions = {
    mount(root) {
      const search = h('input', { type: 'search', placeholder: 'Search titles, prompts, branches', 'aria-label': 'Search sessions', value: S.ui.q, id: 'q',
        oninput: (e) => { S.ui.q = e.target.value; S.ui.sel = -1; this.table(); },
        onkeydown: (e) => { if (e.key === 'Escape') { e.target.value = ''; S.ui.q = ''; this.table(); e.target.blur(); } if (e.key === 'Enter' || e.key === 'ArrowDown') { e.target.blur(); S.ui.sel = 0; this.table(); } } });
      const icon = s('svg', { viewBox: '0 0 16 16', 'aria-hidden': 'true' });
      icon.append(s('circle', { cx: 7, cy: 7, r: 4.5, fill: 'none', stroke: 'currentColor', 'stroke-width': 1.5 }), s('path', { d: 'm10.5 10.5 3 3', stroke: 'currentColor', 'stroke-width': 1.5, 'stroke-linecap': 'round' }));
      this.picker = projectPicker({ label: 'Project', allLabel: 'All projects', withWorktrees: true,
        onPick: (p, wt) => { S.ui.project = p; S.ui.wt = wt; S.ui.sel = -1; location.hash = sessionsHash(); } });
      this.famSeg = h('div', { class: 'seg', role: 'group', 'aria-label': 'Model' });
      this.errBtn = h('button', { 'aria-pressed': String(S.ui.errorsOnly), type: 'button', onclick: () => { S.ui.errorsOnly = !S.ui.errorsOnly; this.errBtn.setAttribute('aria-pressed', String(S.ui.errorsOnly)); this.table(); } }, 'Errors or guard catches');
      this.summary = h('div', { class: 'summary' });
      this.sub = h('div', { class: 'sub' });
      root.append(h('div', { class: 'page-head' }, h('div', {}, h('h1', { text: 'Sessions' }), this.sub)),
        h('div', { class: 'filters' }, h('div', { class: 'search' }, icon, search, h('kbd', { text: '/' })), this.picker.el, this.famSeg, h('div', { class: 'seg' }, this.errBtn), h('div', { class: 'spacer' }), this.summary),
        this.wrap = h('div', {}));
    },
    update() {
      const d = S.data.sessions;
      if (!d) {
        if (S.lastFail) { const e = errorBox({ message: S.lastFail }, 'sessions'); e.dataset.key = 'error'; P.into(this.wrap, e); }
        else if (!this.wrap.firstChild) this.wrap.append(skeleton('table'));
        return;
      }
      P.setText(this.sub, plural(d.sessions.length, 'session') + ' in the last ' + (d.days === 1 ? 'day' : d.days + ' days') + '. Cost and tokens include subagents.');
      this.picker.set(d.projects || [], { p: S.ui.project, wt: S.ui.wt }, d.sessions.length);
      const fams = FAMILIES.filter((f) => d.sessions.some((r) => r.family === f));
      P.into(this.famSeg, [''].concat(fams).map((f) => h('button', { type: 'button', dataset: { key: f || 'all' }, 'aria-pressed': String(S.ui.family === f), onclick: () => { S.ui.family = f; this.update(); } },
        f ? key(f) : null, f ? FAMILY_NAME[f] : 'All models')));
      this.table();
    },
    table() {
      const d = S.data.sessions;
      if (!d) return;
      const rows = filteredSessions();
      this.rows = rows;
      const tot = rows.reduce((a, r) => { a.cost += r.cost; a.tools += r.tool_calls_all; a.tok += r.tokens; return a; }, { cost: 0, tools: 0, tok: 0 });
      P.into(this.summary, h('b', { text: int(rows.length) }), rows.length === 1 ? ' session  ' : ' sessions  ', h('b', { text: money(tot.cost) }), ' est.  ', h('b', { text: compact(tot.tok) }), ' tokens  ', h('b', { text: int(tot.tools) }), ' tool calls');
      if (!d.sessions.length) {
        P.into(this.wrap, h('div', { class: 'empty', dataset: { key: 'empty' } }, h('h2', { text: 'No sessions in this window' }),
          h('p', {}, 'cc-dash reads transcripts from ', h('code', { text: (S.meta ? S.meta.root : '~/.claude') + '/projects' }), '. Start a Claude Code session, or widen the window with ', h('code', { text: 'cc-dash --days 30' }), '.')));
        return;
      }
      if (!rows.length) {
        P.into(this.wrap, h('div', { class: 'empty', dataset: { key: 'nomatch' } }, h('h2', { text: 'No sessions match' }), h('p', { text: 'Nothing matches the search and filters above.' }),
          h('button', { class: 'btn', type: 'button', text: 'Clear filters', onclick: () => { S.ui.q = ''; S.ui.project = ''; S.ui.wt = ''; S.ui.family = ''; S.ui.errorsOnly = false; if (location.hash !== '#/sessions') history.pushState(null, '', '#/sessions'); syncTabLinks(); mount(); } })));
        return;
      }
      if (S.ui.sel >= rows.length) S.ui.sel = rows.length - 1;
      const thead = h('tr', {}, COLS.map((c) => {
        const sorted = S.ui.sort.k === c.k;
        return h('th', { class: (c.r ? 'r ' : '') + (c.label ? 'sortable' : ''), style: c.w ? { width: c.w } : null, 'aria-sort': sorted ? (S.ui.sort.d > 0 ? 'ascending' : 'descending') : null,
          onclick: c.label ? () => { S.ui.sort = { k: c.k, d: sorted ? -S.ui.sort.d : (c.sort ? 1 : -1) }; this.table(); } : null },
        c.label, c.label ? h('span', { class: 'arrow', text: sorted ? (S.ui.sort.d > 0 ? '↑' : '↓') : '' }) : null);
      }));
      const z = (n, txt) => h('td', { class: 'r' + (n ? '' : ' zero'), text: txt != null ? txt : int(n) });
      const tbody = h('tbody', {}, rows.map((r, i) => h('tr', { class: 'link' + (i === S.ui.sel ? ' sel' : ''), dataset: { key: r.id }, onclick: () => { location.hash = '#/s/' + encodeURIComponent(r.id); } },
        h('td', {}, r.state ? h('span', { class: 'dot ' + r.state, title: r.state === 'working' ? 'Working' : r.state === 'waiting' ? 'Waiting for you' : 'Idle, written in the last 10 minutes' }) : null),
        h('td', {}, h('div', { class: 't-title ellipsis', text: r.title, title: r.first_prompt || r.title }),
          h('div', { class: 't-meta' }, h('span', { text: r.project }), r.worktree ? h('span', { class: 'tag', text: r.worktree }) : null, showBranch(r) ? h('span', { class: 'branch ellipsis', text: r.branch }) : null, h('span', { class: 'faint nowrap', text: 'started ' + dateTime(r.start) }))),
        h('td', { class: 'r muted', text: ago(r.last_write), title: dateTime(r.last_write) }),
        h('td', { class: 'r', text: dur(r.active), title: 'Active time, pauses over 30 minutes left out. Wall clock: ' + dur(r.duration) }),
        h('td', { class: 'nowrap ellipsis' }, r.model ? modelTag(r.model, r.family) : h('span', { class: 'faint', text: 'none' }), r.models.length > 1 ? h('span', { class: 'faint', text: ' +' + (r.models.length - 1), title: r.models.join(', ') }) : null),
        z(r.prompts), z(r.tool_calls_all), z(r.subagents), z(r.tokens, compact(r.tokens)),
        h('td', { class: 'r', text: money(r.cost), title: r.unpriced_tokens ? compact(r.unpriced_tokens) + ' tokens on models without a price' : null }),
        h('td', { class: 'r' + (r.errors ? ' bad' : ' zero'), text: int(r.errors), title: r.errors ? int(r.tool_errors) + ' tool errors, ' + int(r.api_errors) + ' API errors' : null }),
        h('td', { class: 'r' + (r.guard ? '' : ' zero'), text: int(r.guard), title: r.guard ? `${r.guard_kinds.deny} blocked, ${r.guard_kinds.ask} asked, ${r.guard_kinds.warn} warned` : null }))));
      const widths = ['30px', null, '92px', '76px', '128px', '60px', '84px', '84px', '72px', '76px', '62px', '58px'];
      P.into(this.wrap, h('div', { class: 'table-wrap', dataset: { key: 'table' } }, h('table', { class: 'fixed' }, h('colgroup', {}, widths.map((w) => h('col', { style: w ? { width: w } : null }))), h('thead', {}, thead), tbody)));
    },
    keys(k) {
      const rows = this.rows || [];
      if (k === 'j' || k === 'k') {
        S.ui.sel = Math.max(0, Math.min(rows.length - 1, S.ui.sel + (k === 'j' ? 1 : -1)));
        this.table();
        const el = main.querySelector('tr.sel');
        if (el) el.scrollIntoView({ block: 'nearest' });
        return true;
      }
      if (k === 'Enter' && rows[S.ui.sel]) { location.hash = '#/s/' + encodeURIComponent(rows[S.ui.sel].id); return true; }
      return false;
    },
  };

  // Overview -------------------------------------------------------------
  /* Per-day (or per-hour) cost as a tiny column chart: earlier buckets in the
     recessive series tone, the current one in the main tone. */
  function spark(costHours, buckets, hourly, title) {
    const W = 84, H = 22, n = Math.max(1, buckets.length);
    const vals = new Array(n).fill(0);
    for (const [hr, c] of costHours) { const i = bucketIndex(buckets, hr * 3600); if (i >= 0) vals[i] += c; }
    const max = Math.max(0, ...vals);
    const band = W / n, bw = Math.max(1.5, Math.min(9, band - 2));
    const unit = hourly ? 'hour' : 'day';
    const svg = s('svg', { class: 'spark', viewBox: `0 0 ${W} ${H}`, width: W, height: H, role: 'img',
      'aria-label': 'Cost per ' + unit + ': ' + buckets.map((b, i) => b.label + ' ' + money(vals[i])).filter((_, i) => vals[i] > 0).join(', ') || 'Cost per ' + unit + ': none' });
    svg.append(s('line', { class: 'spark-base', x1: 0, x2: W, y1: H - 0.5, y2: H - 0.5 }));
    vals.forEach((v, i) => {
      if (!v || !max) return;
      const hh = Math.max(1.5, (v / max) * (H - 3));
      svg.append(s('path', { class: i === n - 1 ? 'spark-now' : 'spark-bar', d: topRounded(i * band + (band - bw) / 2, H - 1 - hh, bw, hh, 1.5) }));
    });
    const tipRows = () => {
      let rows = buckets.map((b, i) => ({ name: b.title, value: money(vals[i]), v: vals[i], color: i === n - 1 ? 'var(--series-main)' : 'var(--series-sub)' }));
      if (hourly) rows = rows.filter((r) => r.v > 0).slice(-12);
      return rows.length ? rows : [{ name: 'Nothing recorded', value: '' }];
    };
    svg.append(s('rect', { class: 'spark-hit', x: 0, y: 0, width: W, height: H,
      onpointerenter: (ev) => showTip(ev, title + ', cost per ' + unit, tipRows(), { name: 'Total', value: money(vals.reduce((a, b) => a + b, 0)) }),
      onpointermove: moveTip, onpointerleave: hideTip }));
    return svg;
  }
  /* Opus/Sonnet/Haiku share of a project's tokens, in the model palette. */
  function mixShare(fam) {
    const rows = FAMILIES.filter((f) => (fam[f] || 0) > 0).map((f) => ({ family: f, name: FAMILY_NAME[f], v: fam[f] }));
    const total = rows.reduce((a, r) => a + r.v, 0);
    if (!total) return h('span', { class: 'faint', text: 'none' });
    return h('div', { class: 'share pmix', role: 'img', 'aria-label': 'Share of tokens: ' + rows.map((r) => r.name + ' ' + pctStr(r.v, total)).join(', '),
      onpointerenter: (ev) => showTip(ev, 'Share of tokens', rows.map((r) => ({ fam: r.family, name: r.name, value: pctStr(r.v, total) + '  ' + compact(r.v) }))),
      onpointermove: moveTip, onpointerleave: hideTip },
    rows.map((r) => h('span', { class: 'f-' + r.family, dataset: { key: r.family }, style: { flex: String(r.v / total) } })));
  }
  const PCOLS = [
    { k: 'project', label: 'Project', sort: (r) => r.project.toLowerCase() },
    { k: 'sessions', label: 'Sessions', r: true, w: '78px' },
    { k: 'active', label: 'Active', r: true, w: '84px' },
    { k: 'trend', label: 'Trend', w: '104px' },
    { k: 'cost', label: 'Cost', r: true, w: '84px' },
    { k: 'tokens', label: 'Tokens', r: true, w: '78px' },
    { k: 'tool_calls', label: 'Tool calls', r: true, w: '84px' },
    { k: 'error_rate', label: 'Error rate', r: true, w: '84px' },
    { k: 'subagents', label: 'Subagents', r: true, w: '84px' },
    { k: 'mix', label: 'Model mix', w: '120px' },
    { k: 'guard', label: 'Guard', r: true, w: '64px' },
    { k: 'last_active', label: 'Last active', r: true, w: '96px' },
  ];
  views.overview = {
    mount(root) {
      this.sub = h('div', { class: 'sub' });
      this.picker = projectPicker({ label: 'Overview scope', allLabel: 'All projects', withWorktrees: false,
        onPick: (p) => { S.ui.scope = p; location.hash = overviewHash(); } });
      root.append(h('div', { class: 'page-head' }, h('div', {}, h('h1', { text: 'Overview' }), this.sub),
        h('div', { class: 'page-tools' }, h('span', { class: 'muted', text: 'Scope' }), this.picker.el)), this.body = h('div', {}));
      this.sig = null;
      this.entered = false;
    },
    update() {
      const d = S.data.overview;
      const list = d ? d.project_list : [];
      this.picker.set(list.map((p) => ({ project: p.project, sessions: p.sessions, worktrees: [], worktrees_n: p.worktrees })), { p: S.ui.scope, wt: '' },
        list.reduce((a, p) => a + p.sessions, 0));
      if (!d) {
        if (S.err.overview) P.into(this.body, errorBox(S.err.overview, 'the overview'));
        else if (!this.body.firstChild) this.body.append(skeleton());
        return;
      }
      P.attr(this.body, 'class', S.data.overviewScope !== S.ui.scope ? 'stale' : null);
      P.attr(this.body, 'aria-busy', S.data.overviewScope !== S.ui.scope ? 'true' : null);
      const sig = JSON.stringify([d.scope, d.totals, d.hours, d.projects, d.guard, d.sub_types, S.ui.psort, [...S.ui.pexp], S.ui.act, theme]);
      if (sig === this.sig) return;
      this.sig = sig;
      const scope = d.scope.project;
      P.setText(this.sub, 'Last ' + (d.days === 1 ? 'day' : d.days + ' days') + (scope ? ', project ' + scope : S.meta && S.meta.project ? ', projects matching ' + S.meta.project : ', all projects') + '. Cost is an estimate from token counts.');
      const first = !this.entered;
      this.entered = true;
      P.into(this.body, h('div', { class: first ? 'fade-in' : '', dataset: { key: 'ov' } }, this.render(d)));
    },
    render(d) {
      const t = d.totals;
      const scope = d.scope.project;
      if (!t.sessions) {
        return h('div', { class: 'empty', dataset: { key: 'empty' } }, h('h2', { text: scope ? 'No sessions in ' + scope + ' in this window' : 'No sessions in this window' }),
          scope ? h('p', {}, 'Nothing in the last ' + d.days + ' days matches this project. ', h('a', { href: viewHash('overview', ''), class: 'link-u', text: 'Show all projects' }), '.')
            : h('p', {}, 'Nothing was written under ', h('code', { text: (S.meta ? S.meta.root : '~/.claude') + '/projects' }), ' in the last ' + d.days + ' days. Try ', h('code', { text: 'cc-dash --days 30' }), '.'));
      }
      const guardTotal = d.guard.deny + d.guard.ask + d.guard.warn;
      const kpis = h('div', { class: 'kpis', dataset: { key: 'kpis' } },
        kpi('Estimated cost', money(t.cost), t.unpriced_tokens ? compact(t.unpriced_tokens) + ' tokens unpriced' : money(t.cost / Math.max(1, t.sessions), true) + ' per session', 'hero'),
        kpi('Sessions', int(t.sessions), t.live ? t.live + ' live now' : plural(t.prompts, 'prompt')),
        kpi('Active time', dur(t.active), dur(t.active / Math.max(1, t.sessions)) + ' per session'),
        kpi('Tokens', compact(t.tokens), 'cache reads included'),
        kpi('Tool calls', compact(t.tool_calls), int(Math.round(t.tool_calls / Math.max(1, t.sessions))) + ' per session'),
        kpi('Tool error rate', pctStr(t.tool_errors, t.tool_calls), int(t.tool_errors) + ' failed calls'),
        kpi('Subagents', int(t.subagents), (t.subagents / Math.max(1, t.sessions)).toFixed(1) + ' per session'),
        kpi('Guard catches', int(guardTotal), guardTotal ? `${d.guard.deny} blocked, ${d.guard.ask} asked` : 'from .git/cc_tool logs'));
      const { hourly, buckets } = timeBuckets(d.cutoff, d.now);
      const costB = buckets.map((b) => ({ ...b, values: {} }));
      const actB = buckets.map((b) => ({ ...b, values: {} }));
      for (const hr of d.hours) {
        const i = bucketIndex(buckets, hr.h * 3600);
        if (i < 0) continue;
        for (const f in hr.c) costB[i].values[f] = (costB[i].values[f] || 0) + hr.c[f];
        const v = actB[i].values;
        v.tools = (v.tools || 0) + hr.tools; v.sess = (v.sess || 0) + hr.sess; v.err = (v.err || 0) + hr.err;
      }
      const famsUsed = FAMILIES.filter((f) => costB.some((b) => b.values[f] > 0));
      const costSeries = famsUsed.map((f) => ({ k: f, name: FAMILY_NAME[f], fam: f }));
      const famCost = {};
      for (const b of costB) for (const f in b.values) famCost[f] = (famCost[f] || 0) + b.values[f];
      const costFmt = (v, axis) => axis ? (v >= 1000 ? '$' + compact(v) : '$' + (v < 10 && v % 1 ? v.toFixed(1) : Math.round(v))) : money(v);
      const costPanel = h('section', { class: 'panel' }, h('div', { class: 'panel-h' }, h('h2', { text: 'Cost per ' + (hourly ? 'hour' : 'day') + ' by model' }), h('span', { class: 'muted', text: 'estimated, subagents included' })),
        h('div', { class: 'panel-b' }, h('div', { class: 'legend', style: { marginBottom: '10px' } }, costSeries.map((sr) => h('span', { dataset: { key: sr.k } }, key(sr.fam), sr.name, h('b', { text: money(famCost[sr.k]) })))),
          chart(h('div', { class: 'chart', dataset: { key: 'cost' } }), [costB.map((b) => b.values), costSeries.map((x) => x.k)],
            (host) => columns(host, { buckets: costB, series: costSeries, height: 300, fmt: costFmt, totalLabel: 'All models' }))));
      const ACT = { tools: { name: 'Tool calls', color: 'var(--series-main)' }, sess: { name: 'Sessions started', color: 'var(--series-main)' }, err: { name: 'Errors', color: 'var(--bad)' } };
      const act = S.ui.act;
      const actSeg = h('div', { class: 'seg', role: 'group', 'aria-label': 'Activity measure' }, Object.keys(ACT).map((k) => h('button', { type: 'button', dataset: { key: k }, 'aria-pressed': String(act === k),
        onclick: () => { S.ui.act = k; this.update(); } }, ACT[k].name)));
      const actPanel = h('section', { class: 'panel' }, h('div', { class: 'panel-h' }, h('h2', { text: 'Activity' }), actSeg),
        h('div', { class: 'panel-b' }, chart(h('div', { class: 'chart', dataset: { key: 'act' } }), [act, actB.map((b) => b.values[act] || 0)],
          (host) => columns(host, { height: 250, buckets: actB.map((b) => ({ ...b, values: { [act]: b.values[act] || 0 } })), series: [{ k: act, name: ACT[act].name, color: ACT[act].color }], fmt: (v) => compact(v) }))));
      // model mix: main sessions vs subagents, by tokens
      const mm = {};
      for (const m of d.models) {
        const f = m.family;
        mm[f] = mm[f] || { family: f, name: FAMILY_NAME[f], main: 0, sub: 0, cost: 0, unpriced: false };
        mm[f].main += m.main_tokens; mm[f].sub += m.sub_tokens;
        if (m.cost == null) mm[f].unpriced = true; else mm[f].cost += m.cost;
      }
      const mmRows = FAMILIES.filter((f) => mm[f]).map((f) => mm[f]);
      const mixBar = (label, k) => {
        const total = mmRows.reduce((a, r) => a + r[k], 0);
        return h('div', { class: 'mix-row' }, h('div', { class: 'mix-l' }, h('span', { text: label }), h('b', { text: compact(total) + ' tokens' })),
          total ? h('div', { class: 'mix-bar', role: 'img', 'aria-label': label + ': ' + mmRows.filter((r) => r[k]).map((r) => r.name + ' ' + pctStr(r[k], total)).join(', ') },
            mmRows.filter((r) => r[k] > 0).map((r) => { const p = pct(r[k], total); return h('span', { class: 'f-' + r.family, dataset: { key: r.family }, style: { flex: String(r[k] / total) }, title: r.name + ': ' + pctStr(r[k], total) }, p >= 12 && r.family !== 'fable' && r.family !== 'haiku' ? h('em', { text: Math.round(p) + '%' }) : null); }))
            : h('div', { class: 'faint', text: 'None in this window' }));
      };
      const subMixTotal = d.sub_mix.reduce((a, r) => a + r.count, 0);
      const mixPanel = h('section', { class: 'panel' }, h('div', { class: 'panel-h' }, h('h2', { text: 'Which model did the work' }), h('span', { class: 'muted', text: 'share of tokens' })),
        h('div', { class: 'panel-b' }, h('div', { class: 'mix' }, mixBar('Main sessions', 'main'), mixBar('Subagents', 'sub')),
          h('table', { class: 'compact', style: { marginTop: '14px' } }, h('thead', {}, h('tr', {}, h('th', { text: 'Model' }), h('th', { class: 'r', text: 'Session tok' }), h('th', { class: 'r', text: 'Subagent tok' }), h('th', { class: 'r', text: 'Spawned' }), h('th', { class: 'r', text: 'Cost' }))),
            h('tbody', {}, d.models.map((m) => {
              const sm = d.sub_mix.find((x) => x.family === m.family);
              const cnt = sm && sm.models[m.name];
              return h('tr', { dataset: { key: m.model } }, h('td', {}, modelTag(m.name, m.family)), h('td', { class: 'r', text: compact(m.main_tokens) }), h('td', { class: 'r', text: compact(m.sub_tokens) }), h('td', { class: 'r' + (cnt ? '' : ' zero'), text: int(cnt || 0) }), h('td', { class: 'r', text: money(m.cost) }));
            }))),
          subMixTotal ? h('p', { class: 'insight', text: this.mixInsight(d, subMixTotal) }) : null));
      // top tools
      const maxTool = Math.max(1, ...d.tools.map((r) => r.calls + r.sub_calls));
      const toolsPanel = h('section', { class: 'panel' }, h('div', { class: 'panel-h' }, h('h2', { text: 'Top tools' }), h('div', { class: 'legend' }, h('span', {}, h('span', { class: 'key', style: { background: 'var(--series-main)' } }), 'Main session'), h('span', {}, h('span', { class: 'key', style: { background: 'var(--series-sub)' } }), 'Subagents'))),
        h('div', { class: 'panel-b' }, h('div', { class: 'hbars' }, d.tools.slice(0, 12).map((r) => hbar(r, maxTool)))));
      // subagent types and reliability
      const typesPanel = h('section', { class: 'panel' }, h('div', { class: 'panel-h' }, h('h2', { text: 'Subagents by type' }), h('span', { class: 'muted', text: plural(t.subagents, 'subagent') })),
        h('div', { class: 'panel-b' }, d.sub_types.length ? h('table', { class: 'compact' }, h('thead', {}, h('tr', {}, h('th', { text: 'Type' }), h('th', { class: 'r', text: 'Spawned' }), h('th', { class: 'r', text: 'Unfinished' }))),
          h('tbody', {}, d.sub_types.slice(0, 10).map((r) => h('tr', { dataset: { key: r.type } }, h('td', { class: 'mono nowrap', text: r.type }), h('td', { class: 'r', text: int(r.count) }), h('td', { class: 'r' + (r.failed ? ' bad' : ' zero'), text: r.failed ? int(r.failed) + ' (' + pctStr(r.failed, r.count) + ')' : '0' })))))
          : h('div', { class: 'faint', text: 'No subagents in this window.' })));
      const relPanel = h('section', { class: 'panel' }, h('div', { class: 'panel-h' }, h('h2', { text: 'Where things went wrong' })),
        h('div', { class: 'panel-b' }, h('table', { class: 'compact' }, h('tbody', {},
          this.relRow('Failed tool calls', t.tool_errors, pctStr(t.tool_errors, t.tool_calls) + ' of calls'),
          this.relRow('Refused by a cc_tool hook', t.blocked, 'in transcripts'),
          this.relRow('API errors and retries', t.api_errors, ''),
          this.relRow('Guard blocked', d.guard.deny, 'activity log'),
          this.relRow('Guard asked first', d.guard.ask, 'activity log'),
          this.relRow('Guard warned', d.guard.warn, 'activity log')))));
      return [kpis,
        h('div', { class: 'grid g-3-2', dataset: { key: 'g1' } }, costPanel, mixPanel),
        h('div', { class: 'grid g-3-2', dataset: { key: 'g2' }, style: { marginTop: '16px' } }, actPanel, toolsPanel),
        this.projects(d, buckets, hourly),
        h('div', { class: 'grid g-2', dataset: { key: 'g3' }, style: { marginTop: '16px' } }, typesPanel, relPanel),
        pricingFoot()];
    },
    /* Every project in the window (or the scoped one), sortable, worktrees folded under each. */
    projects(d, buckets, hourly) {
      const scope = d.scope.project;
      const ps = S.ui.psort;
      const col = PCOLS.find((c) => c.k === ps.k) || PCOLS[4];
      const get = col.sort || ((r) => r[col.k] || 0);
      const cmp = (a, b) => { const x = get(a), y = get(b); return (x < y ? -1 : x > y ? 1 : 0) * ps.d; };
      const rows = d.projects.slice().sort(cmp);
      const famsSeen = FAMILIES.filter((f) => d.projects.some((p) => (p.fam_tokens[f] || 0) > 0));
      const go = (p, wt) => { location.hash = viewHash('sessions', p, wt); };
      const line = (r, child, parent) => {
        const name = child ? (r.worktree || 'main checkout') : r.project;
        const open = !child && (scope || S.ui.pexp.has(r.project));
        const target = () => go(child ? parent : r.project, child ? r.worktree : '');
        const nwt = !child ? r.worktrees.filter((w) => w.worktree).length : 0;
        return h('tr', { class: 'link' + (child ? ' p-child' : ''), dataset: { key: (child ? 'w:' + parent + '\u0000' : 'p:') + (child ? r.worktree : r.project) }, tabindex: 0,
          'aria-label': (child ? parent + ', ' : '') + name + ': open its sessions',
          onclick: target, onkeydown: (e) => { if (e.key === 'Enter' && e.target === e.currentTarget) target(); } },
        h('td', {}, h('div', { class: 'p-name' },
          r.worktrees && r.worktrees.length && !child ? h('button', { type: 'button', class: 'p-exp' + (open ? ' open' : ''), 'aria-expanded': String(!!open), disabled: !!scope,
            'aria-label': (open ? 'Hide' : 'Show') + ' worktrees of ' + r.project,
            onclick: (e) => { e.stopPropagation(); if (S.ui.pexp.has(r.project)) S.ui.pexp.delete(r.project); else S.ui.pexp.add(r.project); this.update(); } }, chev())
            : h('span', { class: 'p-exp-sp' }),
          h('span', { class: (child ? 'mono ' : '') + 'p-label ellipsis', text: name, title: child ? name : r.project }),
          nwt ? h('span', { class: 'tag', text: plural(nwt, 'worktree') }) : null,
          r.live ? h('span', { class: 'dot working', title: plural(r.live, 'session') + ' live now' }) : null)),
        h('td', { class: 'r', text: int(r.sessions) }),
        h('td', { class: 'r', text: dur(r.active) || '0s', title: 'Active time of these sessions, pauses over 30 minutes left out' }),
        h('td', { class: 'spark-cell' }, spark(r.cost_hours, buckets, hourly, name)),
        h('td', { class: 'r', text: money(r.cost), title: r.unpriced_tokens ? compact(r.unpriced_tokens) + ' tokens on models without a price' : null }),
        h('td', { class: 'r', text: compact(r.tokens) }),
        h('td', { class: 'r' + (r.tool_calls ? '' : ' zero'), text: int(r.tool_calls) }),
        h('td', { class: 'r' + (r.tool_errors ? '' : ' zero'), text: r.tool_calls ? pctStr(r.tool_errors, r.tool_calls) : '–', title: int(r.tool_errors) + ' failed of ' + int(r.tool_calls) + ' calls' }),
        h('td', { class: 'r' + (r.subagents ? '' : ' zero'), text: int(r.subagents) }),
        h('td', {}, mixShare(r.fam_tokens)),
        h('td', { class: 'r' + (r.guard ? '' : ' zero'), text: int(r.guard), title: r.guard ? `${r.guard_kinds.deny} blocked, ${r.guard_kinds.ask} asked, ${r.guard_kinds.warn} warned` : null }),
        h('td', { class: 'r muted', text: ago(r.last_active), title: dateTime(r.last_active) }));
      };
      const body = [];
      for (const r of rows) {
        body.push(line(r, false));
        if (r.worktrees.length && (scope || S.ui.pexp.has(r.project))) for (const w of r.worktrees.slice().sort(cmp)) body.push(line(w, true, r.project));
      }
      const thead = h('tr', {}, PCOLS.map((c) => {
        const sortable = c.k !== 'trend' && c.k !== 'mix';
        const sorted = ps.k === c.k;
        const label = c.k === 'trend' ? 'Cost per ' + (hourly ? 'hour' : 'day') : c.label;
        return h('th', { class: (c.r ? 'r ' : '') + (sortable ? 'sortable' : ''), 'aria-sort': sorted ? (ps.d > 0 ? 'ascending' : 'descending') : null,
          tabindex: sortable ? 0 : null,
          onclick: sortable ? () => { S.ui.psort = { k: c.k, d: sorted ? -ps.d : (c.sort ? 1 : -1) }; this.update(); } : null,
          onkeydown: sortable ? (e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); e.currentTarget.click(); } } : null },
        label, sortable ? h('span', { class: 'arrow', text: sorted ? (ps.d > 0 ? '↑' : '↓') : '' }) : null);
      }));
      const n = d.projects.length;
      return h('section', { class: 'panel p-panel', dataset: { key: 'projects' }, style: { marginTop: '16px' } },
        h('div', { class: 'panel-h' }, h('div', {}, h('h2', { text: scope ? 'Project and its worktrees' : 'Projects' }),
          h('div', { class: 'muted p-sub', text: scope ? 'Click a row for its sessions.' : plural(n, 'project') + ' in this window, worktrees folded under each. Click a row for its sessions.' })),
        famsSeen.length ? h('div', { class: 'legend', 'aria-label': 'Model mix colours' }, famsSeen.map((f) => h('span', { dataset: { key: f } }, key(f), FAMILY_NAME[f]))) : null),
        h('div', { class: 'panel-b' }, h('table', { class: 'compact p-table' },
          h('colgroup', {}, PCOLS.map((c) => h('col', { style: c.w ? { width: c.w } : null }))),
          h('thead', {}, thead), h('tbody', {}, body))));
    },
    relRow(label, n, note) { return h('tr', { dataset: { key: label } }, h('td', { text: label }), h('td', { class: 'r' + (n ? '' : ' zero'), text: int(n) }), h('td', { class: 'r faint', text: note })); },
    mixInsight(d, total) {
      const parts = d.sub_mix.filter((r) => r.family !== 'other' && r.count).map((r) => FAMILY_NAME[r.family] + ' ' + pctStr(r.count, total));
      const none = d.sub_mix.find((r) => r.family === 'other');
      return 'Subagents by count: ' + parts.join(', ') + '.' + (none && none.count ? ' ' + plural(none.count, 'subagent') + ' made no API call.' : '');
    },
  };
  function hbar(r, max) {
    const total = r.calls + r.sub_calls;
    return h('div', { class: 'hbar' }, h('span', { class: 'hn', text: r.name, title: r.name }),
      h('div', { class: 'track', role: 'img', 'aria-label': `${r.name}: ${r.calls} in the main session, ${r.sub_calls} in subagents` },
        r.calls ? h('span', { style: { width: (100 * r.calls / max) + '%', background: 'var(--series-main)' } }) : null,
        r.sub_calls ? h('span', { style: { width: (100 * r.sub_calls / max) + '%', background: 'var(--series-sub)' } }) : null),
      h('span', { class: 'hv' }, int(total), r.errors ? h('span', { class: 'bad', text: '  ' + int(r.errors) + ' err' }) : null));
  }
  function pricingFoot() {
    const p = S.meta ? S.meta.pricing : [];
    return h('div', { class: 'foot' }, h('details', { class: 'how' }, h('summary', { text: 'How cost is estimated' }),
      h('p', { text: 'Each API message is priced once from its usage block: input, output, cache reads, and cache writes (5-minute writes at 1.25x the input price, 1-hour writes at 2x). Haiku 5.5 prompts over 100K tokens use its higher tier, and fast mode doubles the price. A model missing from this table shows tokens with cost n/a. Prices are USD per million tokens.' }),
      h('div', { class: 'table-wrap', style: { maxWidth: '760px' } }, h('table', { class: 'compact' }, h('thead', {}, h('tr', {}, ['Model', 'Input', 'Output', 'Cache read', 'Write 5m', 'Write 1h'].map((x, i) => h('th', { class: i ? 'r' : '', text: x })))),
        h('tbody', {}, p.map((r) => h('tr', {}, h('td', {}, modelTag(r.name, r.family), h('span', { class: 'faint mono', text: '  ' + r.id })), ...['input', 'output', 'cache_read', 'cache_write_5m', 'cache_write_1h'].map((k) => h('td', { class: 'r', text: '$' + r[k] })))))))));
  }

  // Session and agent detail ----------------------------------------------
  /* Timelines run newest first by default (the order toggle is remembered).
     Rows are keyed by event index, so a poll that adds events inserts them at
     the top and touches nothing else; when the reader has scrolled down, the
     page is shifted by the inserted height so what they read stays put, and a
     pill counts the new entries above. */
  const TL_ORDERS = ['newest', 'oldest'];
  S.ui.order = store.get('cc-dash-tl-order');
  if (!TL_ORDERS.includes(S.ui.order)) S.ui.order = 'newest';
  function timeline(events, ctx) {
    const mode = S.ui.tl;
    const newest = S.ui.order === 'newest';
    const { shown, hidden } = window.ccTimeline.arrange(events, { order: S.ui.order, mode, limit: 700, showAll: S.ui.showAll });
    const rows = [];
    const more = hidden ? h('div', { class: 'tl-row', dataset: { key: 'more' } }, h('span'), h('button', { class: 'btn', type: 'button', text: 'Show ' + int(hidden) + (newest ? ' older entries' : ' earlier entries'), onclick: () => { S.ui.showAll = true; ctx.redraw(); } })) : null;
    if (more && !newest) rows.push(more);
    let lastDay = null;
    for (const it of shown) {
      const day = it.t ? dateOnly(it.t) : null;
      if (day && day !== lastDay) { if (lastDay) rows.push(h('div', { class: 'tl-row', dataset: { key: 'd' + day } }, h('span'), h('div', { class: 'marker', text: day }))); lastDay = day; }
      rows.push(h('div', { class: 'tl-row', dataset: { key: 'e' + it.key } }, h('span', { class: 'tl-time', text: it.t ? clock(it.t) : '' }), h('div', { class: 'tl-body' }, tlItem(it, ctx))));
    }
    if (more && newest) rows.push(more);
    if (!rows.length) rows.push(h('div', { class: 'empty', dataset: { key: 'none' }, text: mode === 'problems' ? 'No errors, refusals or interruptions in this transcript.' : 'Nothing recorded yet.' }));
    return h('div', { class: 'tl' + (newest ? ' newest' : ''), dataset: { key: 'tl-' + S.ui.order } }, rows);
  }
  function clampText(text, cls, lines, id, ctx) {
    const long = text.length > 360 || text.split('\n').length > (lines || 6);
    const open = long && S.ui.unclamp.has(id);
    return h('div', {}, h('div', { class: cls }, h('div', { class: open ? '' : 'clamp', style: lines ? { '-webkit-line-clamp': String(lines) } : null, text })),
      long ? h('button', { class: 'more', type: 'button', text: open ? 'Show less' : 'Show all', onclick: (e) => { e.stopPropagation(); if (S.ui.unclamp.has(id)) S.ui.unclamp.delete(id); else S.ui.unclamp.add(id); ctx.redraw(); } }) : null);
  }
  function tlItem(it, ctx) {
    const e = it.e;
    switch (it.type) {
      case 'prompt': return h('div', { class: 'prompt' }, h('div', { class: 'who', text: ctx.sub ? 'Task from the parent agent' : 'You' }), clampText(e.text, 'bubble', 6, it.key, ctx));
      case 'text': return e.report ? h('div', { class: 'prompt' }, h('div', { class: 'who' }, key(famOf(e.model)), 'Report handed back to the parent'), clampText(e.text, 'bubble', 10, it.key, ctx))
        : h('div', {}, h('div', { class: 'who' }, key(famOf(e.model)), e.model || 'Assistant'), clampText(e.text, 'atext', 4, it.key, ctx));
      case 'command': return h('div', { class: 'marker' }, h('span', { class: 'mono', text: e.text }));
      case 'notify': return h('div', { class: 'marker', text: 'Background task: ' + e.text });
      case 'peer': return h('div', { class: 'marker', text: 'Message from another session' });
      case 'interrupt': return h('div', { class: 'marker err', text: 'You interrupted the turn' });
      case 'compact': return h('div', { class: 'marker', text: e.text || 'Context compacted' });
      case 'fallback': return h('div', { class: 'marker err', text: e.text });
      case 'error': return h('div', { class: 'marker err', text: 'API error: ' + e.text });
      case 'tool': return spawnRow(e, ctx);
      case 'group': return toolGroup(it, ctx);
      default: return h('div', { class: 'marker', text: it.type });
    }
  }
  function spawnRow(e, ctx) {
    const child = ctx.childFor ? ctx.childFor(e.id) : [];
    const first = child[0];
    const isWf = e.name === 'Workflow';
    const click = first && !isWf ? () => { location.hash = '#/s/' + encodeURIComponent(ctx.sid) + '/a/' + encodeURIComponent(first.agent_id); } : () => { ctx.tab('agents'); };
    return h('div', { class: 'spawn', role: 'link', tabindex: 0, onclick: click, onkeydown: (ev) => { if (ev.key === 'Enter') click(); } },
      h('span', { class: 'tag', text: isWf ? 'Workflow' : 'Subagent' }), first ? key(first.family) : null,
      h('span', { class: 'sd', text: e.target || e.name }),
      h('span', { class: 'spacer' }),
      isWf ? h('span', { class: 'muted nowrap', text: plural(child.length, 'agent') }) : first ? h('span', { class: 'muted nowrap meta-gap' }, h('span', { text: first.model || '' }), h('span', { text: dur(first.duration) }), h('span', { text: compact(first.tokens) + ' tokens' })) : null,
      first ? subStatus(isWf ? (child.some((c) => c.status === 'running') ? 'running' : child.some((c) => c.status === 'failed') ? 'failed' : 'done') : first.status) : e.status === 'error' ? subStatus('error') : null);
  }
  function toolGroup(it, ctx) {
    const calls = it.calls;
    const counts = {};
    for (const c of calls) counts[c.name] = (counts[c.name] || 0) + 1;
    const names = Object.entries(counts).sort((a, b) => b[1] - a[1]).map(([n, c]) => c > 1 ? n + ' ×' + c : n).join(', ');
    const errs = calls.filter((c) => c.status === 'error').length;
    const blocked = calls.filter((c) => c.status === 'blocked').length;
    const pending = calls.some((c) => c.status === 'pending');
    const total = calls.reduce((a, c) => a + (c.dur || 0), 0);
    const open = S.ui.expandAll ? !S.ui.expanded.has('x' + it.key) : S.ui.expanded.has(it.key);
    // State, not DOM, decides what is open: the next poll re-renders the same thing.
    const toggle = () => { const k = S.ui.expandAll ? 'x' + it.key : it.key; if (S.ui.expanded.has(k)) S.ui.expanded.delete(k); else S.ui.expanded.add(k); ctx.redraw(); };
    return h('div', { class: 'group' + (open ? ' open' : '') },
      h('button', { class: 'group-h', type: 'button', 'aria-expanded': String(open), onclick: toggle }, chev(),
        h('span', { class: 'nowrap', style: { color: 'var(--ink)', fontWeight: 500 }, text: calls.length === 1 ? calls[0].name : plural(calls.length, 'tool call') }),
        h('span', { class: 'names', text: calls.length === 1 ? (calls[0].target || '') : names }), h('span', { class: 'spacer' }),
        blocked ? h('span', { class: 'pill warn', text: blocked + ' blocked by guard' }) : null,
        errs ? h('span', { class: 'pill bad', text: errs + ' failed' }) : null,
        pending ? h('span', { class: 'pill good' }, h('span', { class: 'dot working' }), 'running') : null,
        total ? h('span', { class: 'faint num nowrap', text: dur(total) }) : null),
      open ? callList(calls) : null);
  }
  function callList(calls) {
    return h('div', { class: 'calls' }, calls.map((c, i) => h('div', { class: 'call' + (c.status === 'error' || c.status === 'blocked' ? ' error' : ''), dataset: { key: c.id || 'i' + i } },
      h('span', { class: 'cn', text: c.name }), h('span', { class: 'ct', text: c.target || '', title: c.target || '' }),
      h('span', { class: 'cd' }, c.status === 'pending' ? h('span', { dataset: { since: c.t }, text: dur(nowS() - c.t) }) : (c.dur != null ? dur(c.dur) : '')),
      c.err ? h('span', { class: 'cerr', text: c.err }) : null)));
  }
  function usageTable(rows, label) {
    const cols = [['msgs', 'Messages'], ['input', 'Input'], ['output', 'Output'], ['cache_read', 'Cache read'], ['cache_write_5m', 'Cache write 5m'], ['cache_write_1h', 'Cache write 1h'], ['tokens', 'Total']];
    const sum = (k) => rows.reduce((a, r) => a + (r[k] || 0), 0);
    const costSum = rows.reduce((a, r) => a + (r.cost || 0), 0);
    return h('table', { class: 'compact usage' }, h('thead', {}, h('tr', {}, h('th', { text: label }), cols.map(([, n]) => h('th', { class: 'r', text: n })), h('th', { class: 'r', text: 'Cost' }))),
      h('tbody', {}, rows.map((r) => h('tr', {}, h('td', {}, modelTag(r.name, r.family)), cols.map(([k]) => h('td', { class: 'r' + (r[k] ? '' : ' zero'), text: k === 'msgs' ? int(r[k]) : compact(r[k]), title: int(r[k]) })), h('td', { class: 'r', text: money(r.cost, true) }))),
        rows.length > 1 ? h('tr', {}, h('td', { style: { fontWeight: 600 }, text: 'Total' }), cols.map(([k]) => h('td', { class: 'r', style: { fontWeight: 600 }, text: k === 'msgs' ? int(sum(k)) : compact(sum(k)) })), h('td', { class: 'r', style: { fontWeight: 600 }, text: money(costSum, true) })) : null));
  }
  function costInsight(rows) {
    const p = {};
    for (const r of (S.meta ? S.meta.pricing : [])) p[r.id] = r;
    let cr = 0, all = 0, crTok = 0, tok = 0, out = 0;
    for (const r of rows) {
      tok += r.tokens; crTok += r.cache_read;
      const pr = p[r.model.replace(/\[.*\]$/, '').replace(/-\d{8}$/, '')];
      if (!pr) continue;
      const parts = [r.input * pr.input, r.output * pr.output, r.cache_read * pr.cache_read, r.cache_write_5m * pr.cache_write_5m, r.cache_write_1h * pr.cache_write_1h];
      all += parts.reduce((a, b) => a + b, 0); cr += parts[2]; out += parts[1];
    }
    if (!tok) return null;
    return 'Cache reads are ' + pctStr(crTok, tok) + ' of tokens and about ' + pctStr(cr, all) + ' of the cost. Output tokens are about ' + pctStr(out, all) + ' of the cost.';
  }
  function toolsTab(tools) {
    if (!tools.length) return h('div', { class: 'empty', text: 'No tool calls.' });
    const max = Math.max(1, ...tools.map((r) => r.calls + r.sub_calls));
    const anySub = tools.some((r) => r.sub_calls);
    return h('div', { class: 'grid g-2-1' },
      h('section', { class: 'panel' }, h('div', { class: 'panel-h' }, h('h2', { text: 'Calls per tool' }), anySub ? h('div', { class: 'legend' }, h('span', {}, h('span', { class: 'key', style: { background: 'var(--series-main)' } }), 'This agent'), h('span', {}, h('span', { class: 'key', style: { background: 'var(--series-sub)' } }), 'Its subagents')) : null),
        h('div', { class: 'panel-b' }, h('div', { class: 'hbars' }, tools.slice(0, 20).map((r) => hbar(r, max))))),
      h('section', { class: 'table-wrap' }, h('table', { class: 'compact' }, h('thead', {}, h('tr', {}, h('th', { text: 'Tool' }), h('th', { class: 'r', text: 'Calls' }), h('th', { class: 'r', text: 'Errors' }), h('th', { class: 'r', text: 'Avg time' }))),
        h('tbody', {}, tools.map((r) => h('tr', {}, h('td', { class: 'mono', text: r.name }), h('td', { class: 'r', text: int(r.calls + r.sub_calls) }), h('td', { class: 'r' + (r.errors ? ' bad' : ' zero'), text: r.errors ? int(r.errors) + ' (' + pctStr(r.errors, r.calls + r.sub_calls) + ')' : '0' }), h('td', { class: 'r muted', text: r.avg_dur != null ? (r.avg_dur < 1 ? r.avg_dur.toFixed(2) + 's' : dur(r.avg_dur)) : '' })))))));
  }
  /* A time axis that collapses idle stretches: sessions resumed days later
     would otherwise squeeze every subagent into a sliver. */
  function timeAxis(intervals, t0, t1) {
    const GAP = 1800, PAD = 60;
    const iv = intervals.map(([a, b]) => [Math.max(t0, a - PAD), Math.min(t1, Math.max(a, b) + PAD)]).sort((x, y) => x[0] - y[0]);
    const segs = [];
    for (const [a, b] of iv) {
      const last = segs[segs.length - 1];
      if (last && a - last[1] <= GAP) last[1] = Math.max(last[1], b); else segs.push([a, b]);
    }
    if (!segs.length) segs.push([t0, t1]);
    const active = segs.reduce((s2, [a, b]) => s2 + Math.max(1, b - a), 0);
    const nGap = segs.length - 1;
    const gapW = nGap ? Math.min(0.03, 0.3 / nGap) : 0;
    const scale = (1 - nGap * gapW) / active;
    let x = 0;
    const placed = segs.map(([a, b], i) => { const p = { a, b, x }; x += Math.max(1, b - a) * scale + (i < nGap ? gapW : 0); return p; });
    const map = (t) => {
      for (let i = placed.length - 1; i >= 0; i--) {
        const p = placed[i];
        if (t >= p.a) return Math.min(1, p.x + Math.min(t - p.a, Math.max(1, p.b - p.a)) * scale + (t > p.b && i < nGap ? gapW * 0.5 : 0));
      }
      return 0;
    };
    return { map, segs: placed, gapW, scale };
  }
  function gantt(nodes) {
    const rows = nodes.filter((n) => n.start).sort((a, b) => a.start - b.start);
    if (!rows.length) return null;
    const endOf = (n) => (n.status === 'running' ? nowS() : (n.end || n.start));
    const t0 = Math.min(...rows.map((n) => n.start)), t1 = Math.max(...rows.map(endOf));
    const ax = timeAxis(rows.map((n) => [n.start, endOf(n)]), t0, t1);
    const multiDay = dateOnly(t0) !== dateOnly(t1);
    const labels = [];
    ax.segs.forEach((sg, i) => {
      labels.push(h('span', { class: 'gx', style: { left: (100 * sg.x) + '%' }, text: (multiDay ? dateOnly(sg.a) + ' ' : '') + clock(sg.a, false) }));
      if (i < ax.segs.length - 1) labels.push(h('span', { class: 'gbreak', style: { left: (100 * (sg.x + (sg.b - sg.a) * ax.scale)) + '%', width: (100 * ax.gapW) + '%' }, title: dur(ax.segs[i + 1].a - sg.b) + ' with no subagent running' }));
    });
    const breaks = ax.segs.slice(0, -1).map((sg) => h('span', { class: 'gbreak', style: { left: (100 * (sg.x + (sg.b - sg.a) * ax.scale)) + '%', width: (100 * ax.gapW) + '%' } }));
    return h('div', {}, h('div', { class: 'gantt-axis' }, h('span'), h('div', { class: 'ga' }, labels)),
      rows.slice(0, 150).map((n) => {
        const l = 100 * ax.map(n.start), r = 100 * ax.map(endOf(n));
        const row = h('div', { class: 'gantt-row', onclick: () => { location.hash = '#/s/' + encodeURIComponent(n._sid) + '/a/' + encodeURIComponent(n.agent_id); } },
          h('span', { class: 'gl', text: n.description || n.agent_type }),
          h('div', { class: 'gt' }, breaks.map((b) => b.cloneNode()), h('span', { class: 'gb f-' + n.family + (n.status === 'running' ? ' running' : '') + (n.status === 'failed' ? ' failed' : ''), style: { left: l + '%', width: Math.max(0.35, r - l) + '%' } })));
        P.listen(row, 'pointerenter', (ev) => showTip(ev, n.description || n.agent_type, [
          { fam: n.family, name: 'Model', value: n.model || 'none' }, { name: 'Status', value: n.status }, { name: 'Ran', value: dateTime(n.start) },
          { name: 'Duration', value: dur(n.duration) }, { name: 'Tokens', value: compact(n.tokens) }, { name: 'Cost', value: money(n.cost, true) }, { name: 'Tool calls', value: int(n.tool_calls) }]));
        P.listen(row, 'pointermove', moveTip);
        P.listen(row, 'pointerleave', hideTip);
        return row;
      }), rows.length > 150 ? h('div', { class: 'faint', text: '+' + (rows.length - 150) + ' more in the list below' }) : null,
      ax.segs.length > 1 ? h('p', { class: 'insight', text: 'Shaded gaps are idle stretches of more than 30 minutes, drawn narrow so the work stays readable.' }) : null);
  }
  const KID_HEAD = () => h('div', { class: 'kid head' }, h('span'), h('span', { text: 'Subagent' }), h('span', { class: 'hide-s', text: 'Model' }), h('span', { text: 'Status' }),
    h('span', { class: 'r', text: 'Ran for' }), h('span', { class: 'r hide-s', text: 'Tokens' }), h('span', { class: 'r hide-s', text: 'Cost' }), h('span', { class: 'r hide-s', text: 'Tool calls' }));
  function kidRow(c, sid, depth) {
    const go = () => { location.hash = '#/s/' + encodeURIComponent(sid) + '/a/' + encodeURIComponent(c.agent_id); };
    const asked = c.model_requested && c.model && famOf(c.model_requested) !== c.family;
    return h('div', { class: 'kid link', role: 'link', tabindex: 0, onclick: go, onkeydown: (e) => { if (e.key === 'Enter') go(); }, style: depth ? { paddingLeft: (10 + depth * 22) + 'px' } : null },
      key(c.family), h('span', { class: 'ellipsis' }, depth ? h('span', { class: 'faint', text: '└ ' }) : null, h('span', { class: 'kd', text: c.description || '(no description)' }),
        c.agent_type && c.agent_type !== 'workflow-subagent' ? h('span', { class: 'faint mono', text: '  ' + c.agent_type }) : null, c.background ? h('span', { class: 'faint', text: '  background' }) : null),
      h('span', { class: 'muted ellipsis hide-s', title: c.model_requested ? 'asked for ' + c.model_requested : '' }, c.model || 'no API call', asked ? h('span', { class: 'faint', text: ' (asked ' + c.model_requested + ')' }) : null),
      h('span', {}, subStatus(c.status)), h('span', { class: 'r num muted', text: dur(c.duration) }),
      h('span', { class: 'r num muted hide-s', text: compact(c.tokens) }),
      h('span', { class: 'r num muted hide-s', text: money(c.cost) }),
      h('span', { class: 'r num muted hide-s', text: int(c.tool_calls) }));
  }
  function treeView(d) {
    const sid = d.session.id;
    const nodes = [];
    const collect = (spawns) => { for (const sp of spawns) for (const c of sp.children) { c._sid = sid; nodes.push(c); collect(c.spawns || []); } };
    collect(d.tree);
    for (const o of d.orphans) { o._sid = sid; nodes.push(o); }
    if (!nodes.length && !d.tree.length) return h('div', { class: 'empty' }, h('h2', { text: 'No subagents' }), h('p', { text: 'This session did all its work in the main conversation.' }));
    const famCount = {};
    for (const n of nodes) famCount[n.family] = (famCount[n.family] || 0) + 1;
    const rows = [];
    const addSpawn = (sp, depth) => {
      if (sp.kind === 'workflow') {
        const wf = sp.workflow || {};
        const kids = sp.children;
        const st = wf.status === 'completed' ? 'done' : (kids.some((k) => k.status === 'running') ? 'running' : kids.some((k) => k.status === 'failed') ? 'failed' : (wf.status || sp.status));
        rows.push(h('div', { class: 'wf-head', style: depth ? { paddingLeft: (14 + depth * 22) + 'px' } : null }, h('span', { class: 'tag', text: 'Workflow' }), h('span', { class: 'kd', text: wf.name || sp.description }),
          h('span', { class: 'faint', text: plural(kids.length, 'agent') + (wf.phases && wf.phases.length ? ' in ' + wf.phases.join(', ') : '') + (wf.duration_ms ? ', ' + dur(wf.duration_ms / 1000) : '') }), h('span', { class: 'spacer' }), subStatus(st)));
        const phases = [...new Set(kids.map((k) => k.phase || ''))];
        for (const p of phases) {
          if (p && phases.length > 1) rows.push(h('div', { class: 'phase', style: { paddingLeft: (34 + depth * 22) + 'px' }, text: p }));
          for (const k of kids.filter((x) => (x.phase || '') === p)) rows.push(kidRow(k, sid, depth + 1));
        }
        return;
      }
      if (!sp.children.length) {
        rows.push(h('div', { class: 'kid' }, key('other'), h('span', { class: 'ellipsis' }, h('span', { class: 'kd', text: sp.description || '(no description)' }), h('span', { class: 'faint mono', text: '  ' + (sp.subagent_type || '') })),
          h('span', { class: 'muted hide-s', text: sp.model_requested || '' }), h('span', {}, subStatus(sp.status === 'completed' ? 'done' : sp.status)),
          h('span', { class: 'faint', style: { gridColumn: '5 / -1' }, text: sp.status === 'error' ? 'The call failed: ' + (sp.error || 'no detail') : 'No transcript on disk' })));
        return;
      }
      for (const c of sp.children) {
        rows.push(kidRow(c, sid, depth));
        for (const sp2 of c.spawns || []) addSpawn(sp2, depth + 1);
      }
    };
    d.tree.forEach((sp) => addSpawn(sp, 0));
    if (d.orphans.length) {
      rows.push(h('div', { class: 'wf-head' }, h('span', { class: 'tag', text: 'Unlinked' }), h('span', { class: 'muted', text: 'Subagent transcripts with no matching spawn call' })));
      d.orphans.forEach((k) => rows.push(kidRow(k, sid, 1)));
    }
    const g = gantt(nodes);
    return h('div', { class: 'grid' },
      g ? h('section', { class: 'panel' }, h('div', { class: 'panel-h' }, h('h2', { text: 'When each subagent ran' }), h('div', { class: 'legend' }, FAMILIES.filter((f) => famCount[f]).map((f) => h('span', {}, key(f), FAMILY_NAME[f], h('b', { text: famCount[f] }))))), h('div', { class: 'panel-b' }, g)) : null,
      h('section', { class: 'panel kids-list' }, KID_HEAD(), rows));
  }
  function guardTab(d) {
    const g = d.guard;
    if (!g.length) {
      return h('div', { class: 'empty' }, h('h2', { text: d.session.blocked ? plural(d.session.blocked, 'tool call') + ' refused by a hook' : 'No guard catches' }),
        h('p', {}, 'Guard decisions come from ', h('code', { text: (d.session.cwd || '<repo>') + '/.git/cc_tool/activity.jsonl' }), ', which the cc_tool hooks write. A project set up without cc-setup has no such log.'),
        d.session.blocked ? h('p', { text: 'The refusals above were read from the transcript itself; open Problems in the timeline to see them.' }) : null);
    }
    const label = { deny: ['bad', 'Blocked'], ask: ['warn', 'Asked'], warn: ['', 'Warned'] };
    return h('div', { class: 'table-wrap' }, h('table', {}, h('thead', {}, h('tr', {}, ['Time', 'Decision', 'Hook', 'What', 'Message'].map((x) => h('th', { text: x })))),
      h('tbody', {}, g.map((e) => h('tr', {}, h('td', { class: 'num muted nowrap', text: dateOnly(e.t) + ' ' + clock(e.t) }), h('td', {}, h('span', { class: 'pill ' + label[e.kind][0], text: label[e.kind][1] })),
        h('td', { class: 'mono muted nowrap', text: e.hook }), h('td', { class: 'mono', style: { maxWidth: '360px', overflowWrap: 'anywhere' }, text: e.target }), h('td', { class: 'muted', style: { maxWidth: '480px' }, text: e.message }))))));
  }

  function detailFrame(root) {
    const o = {};
    o.crumb = h('a', { class: 'crumb', href: S.backTo || '#/sessions' });
    o.head = h('div', { class: 'd-head' });
    o.kpis = h('div', {});
    o.tabs = h('div', { class: 'dtabs', role: 'tablist' });
    o.body = h('div', {});
    o.pill = h('button', { type: 'button', class: 'new-pill', hidden: true, onclick: () => tlToTop(o) });
    root.append(o.crumb, o.head, o.kpis, o.tabs, o.body, o.pill);
    return o;
  }
  function tabs(o, list, redraw) {
    P.into(o.tabs, list.map((t, i) => h('button', { role: 'tab', type: 'button', dataset: { key: t.k }, 'aria-selected': String(S.ui.dtab === t.k), title: 'Key ' + (i + 1), onclick: () => { S.ui.dtab = t.k; redraw(true); } },
      t.label, t.n != null ? h('span', { class: 'n' + (t.bad ? ' bad' : ''), text: int(t.n) }) : null)));
  }
  const crumbFor = (back) => (/^#\/live/.test(back || '') ? '← Live' : /^#\/overview/.test(back || '') ? '← Overview' : '← Sessions');
  /* The timeline top counts as "seen" while its first row is in view. */
  function tlAtTop(o) {
    const tl = o.body.querySelector('.tl');
    if (!tl) return true;
    const head = document.querySelector('.top');
    return tl.getBoundingClientRect().top >= (head ? head.getBoundingClientRect().bottom : 0) - 4;
  }
  function tlToTop(o) {
    const tl = o.body.querySelector('.tl');
    const head = document.querySelector('.top');
    if (tl) window.scrollTo({ top: Math.max(0, window.scrollY + tl.getBoundingClientRect().top - (head ? head.offsetHeight : 0) - 70), behavior: reducedMotion() ? 'auto' : 'smooth' });
    o.pill.hidden = true;
  }
  /* Patch the detail body; with newest-first and the reader scrolled down,
     hold the first visible row still and count what arrived above it. */
  function patchDetail(view, body, first, nEvents) {
    const o = view.o;
    const newest = S.ui.order === 'newest' && S.ui.dtab === 'timeline';
    const top = tlAtTop(o);
    let anchor = null, y0 = 0;
    if (newest && !top && !first) {
      const head = document.querySelector('.top');
      const hb = head ? head.getBoundingClientRect().bottom : 0;
      for (const r of o.body.querySelectorAll('.tl > .tl-row[data-key]')) { const b = r.getBoundingClientRect(); if (b.bottom > hb) { anchor = r; y0 = b.top; break; } }
    }
    P.into(o.body, h('div', { class: first ? 'fade-in' : '', dataset: { key: S.ui.dtab } }, body));
    if (anchor && anchor.isConnected) { const dy = anchor.getBoundingClientRect().top - y0; if (Math.abs(dy) > 0.5) window.scrollBy(0, dy); }
    if (!newest || top || first || view.seen == null) view.seen = nEvents;
    const fresh = nEvents - view.seen;
    o.pill.hidden = !(newest && fresh > 0 && !tlAtTop(o));
    P.setText(o.pill, '↑ ' + plural(fresh, 'new entry', 'new entries'));
  }
  window.addEventListener('scroll', () => {
    if (!current || !current.o || !current.o.pill || current.o.pill.hidden) return;
    if (tlAtTop(current.o)) { current.o.pill.hidden = true; current.seen = current.nEvents; }
  }, { passive: true });
  function tlTools(view, d) {
    const counts = { problems: d.events.filter((e) => e.k === 'error' || e.k === 'fallback' || e.k === 'interrupt' || e.status === 'error' || e.status === 'blocked').length };
    const seg = h('div', { class: 'seg', role: 'group', 'aria-label': 'Timeline filter' }, [['all', 'Everything'], ['talk', 'Conversation'], ['problems', 'Problems']].map(([k, n]) =>
      h('button', { type: 'button', dataset: { key: k }, 'aria-pressed': String(S.ui.tl === k), onclick: () => { S.ui.tl = k; view.update(true); } }, n, k === 'problems' ? h('span', { class: 'n', text: int(counts.problems) }) : null)));
    const order = h('div', { class: 'seg', role: 'group', 'aria-label': 'Timeline order' }, [['newest', 'Newest first'], ['oldest', 'Oldest first']].map(([k, n]) =>
      h('button', { type: 'button', dataset: { key: k }, 'aria-pressed': String(S.ui.order === k), onclick: () => { S.ui.order = k; store.set('cc-dash-tl-order', k); view.seen = null; view.update(true); } }, n)));
    return h('div', { class: 'tl-tools' }, seg, order,
      h('button', { class: 'btn', type: 'button', text: S.ui.expandAll ? 'Collapse tool calls' : 'Expand tool calls', onclick: () => { S.ui.expandAll = !S.ui.expandAll; S.ui.expanded = new Set(); view.update(true); } }),
      d.dropped_events ? h('span', { class: 'faint', text: int(d.dropped_events) + ' later events not shown (timeline cap)' }) : null, d.bad_lines ? h('span', { class: 'faint', text: plural(d.bad_lines, 'unreadable line') + ' skipped' }) : null);
  }
  views.session = {
    mount(root) { this.o = detailFrame(root); this.sig = null; this.tabList = []; this.seen = null; },
    update(force) {
      const o = this.o;
      const d = S.data.session[S.route.id];
      P.setText(o.crumb, crumbFor(S.backTo));
      if (!d) {
        if (S.err.session) { P.into(o.head, errorBox(S.err.session, 'this session')); return; }
        if (!o.head.firstChild) {
          o.head.append(h('div', { class: 'sk sk-line', style: { width: '45%', height: '22px' } }), h('div', { class: 'sk sk-line', style: { width: '30%' } }));
          o.kpis.append(skeleton().firstChild);
        }
        return;
      }
      const r = d.session;
      const last = d.events[d.events.length - 1];
      const sig = JSON.stringify([d.events.length, last && last.status, last && last.text && last.text.length, r.cost, r.state, r.subagents, r.guard, S.ui.dtab, S.ui.tl, S.ui.expandAll, S.ui.showAll, S.ui.order]);
      if (sig === this.sig && !force) return;
      const first = this.sig == null;
      this.sig = sig;
      this.nEvents = d.events.length;
      if (first) { o.head.replaceChildren(); o.kpis.replaceChildren(); }
      P.into(o.head,
        h('h1', { class: 'd-title', text: r.title }),
        h('div', { class: 'd-meta' }, statePill(r.state), h('span', { style: { color: 'var(--ink)', fontWeight: 500 }, text: r.project }), r.worktree ? h('span', { class: 'tag', text: r.worktree }) : null,
          showBranch(r) ? h('span', { class: 'branch', text: r.branch }) : null, h('span', { text: dateTime(r.start) + ' to ' + (r.state ? 'now' : (dateOnly(r.end) !== dateOnly(r.start) ? dateTime(r.end) : clock(r.end, false))) + ', ' + dur(r.active) + ' active' }),
          h('span', { style: { gap: '12px' } }, r.models.map((m) => modelTag(m))), r.entrypoint ? h('span', { class: 'faint', text: r.entrypoint + (r.version ? ' ' + r.version : '') }) : null,
          h('span', { class: 'faint mono ellipsis', text: r.cwd, title: d.path })));
      const all = d.usage.all;
      P.into(o.kpis, h('div', { class: 'kpis' },
        kpi('Estimated cost', money(r.cost, r.cost < 100), r.unpriced_tokens ? compact(r.unpriced_tokens) + ' tokens unpriced' : 'subagents included', 'hero'),
        kpi('Tokens', compact(r.tokens), compact(all.reduce((a, x) => a + x.cache_read, 0)) + ' cache reads'),
        kpi('Turns', int(r.prompts), plural(r.api_msgs, 'API call')),
        kpi('Tool calls', int(r.tool_calls_all), r.tool_calls_all !== r.tool_calls ? int(r.tool_calls) + ' in this session' : null),
        kpi('Subagents', int(r.subagents), r.workflows ? plural(r.workflows, 'workflow') : null),
        kpi('Errors', int(r.errors), r.errors ? int(r.tool_errors) + ' tool, ' + int(r.api_errors) + ' API' : 'none', ''),
        kpi('Guard catches', int(r.guard), r.blocked ? plural(r.blocked, 'refusal') + ' in transcript' : null)),
      all.length > 1 ? h('div', { style: { margin: '-4px 0 6px' } }, shareBar(all.map((x) => ({ ...x, cost: x.cost || 0 })), 'cost'), h('div', { class: 'legend' }, all.map((x) => h('span', { dataset: { key: x.model } }, key(x.family), x.name, h('b', { text: money(x.cost) }))))) : null);
      this.tabList = [{ k: 'timeline', label: 'Timeline', n: d.events.length }, { k: 'agents', label: 'Subagents', n: r.subagents },
        { k: 'tools', label: 'Tools', n: d.tools.length }, { k: 'tokens', label: 'Tokens and cost' }, { k: 'guard', label: 'Guard', n: r.guard, bad: r.guard > 0 }];
      tabs(o, this.tabList, (f) => this.update(f));
      const childFor = (toolId) => { const sp = d.tree.find((x) => x.id === toolId); return sp ? sp.children : []; };
      const ctx = { sid: r.id, childFor, tab: (k) => { S.ui.dtab = k; this.update(true); }, redraw: () => this.update(true) };
      let body;
      if (S.ui.dtab === 'timeline') body = this.tl(d, ctx);
      else if (S.ui.dtab === 'agents') body = treeView(d);
      else if (S.ui.dtab === 'tools') body = toolsTab(d.tools);
      else if (S.ui.dtab === 'tokens') {
        const ins = costInsight(all);
        body = h('div', { class: 'grid' }, h('section', { class: 'table-wrap' }, usageTable(all, 'All models')), ins ? h('p', { class: 'insight', text: ins }) : null,
          d.usage.sub.length ? h('div', { class: 'grid' }, h('section', { class: 'table-wrap' }, usageTable(d.usage.main, 'Main session')), h('section', { class: 'table-wrap' }, usageTable(d.usage.sub, 'Subagents'))) : null,
          pricingFoot());
      } else body = guardTab(d);
      patchDetail(this, body, first, d.events.length);
    },
    tl(d, ctx) { return h('div', {}, tlTools(this, d), timeline(d.events, ctx)); },
    keys(k) {
      const n = Number(k);
      if (n >= 1 && n <= this.tabList.length) { S.ui.dtab = this.tabList[n - 1].k; this.update(true); return true; }
      if (k === 'e') { S.ui.expandAll = !S.ui.expandAll; S.ui.expanded = new Set(); this.update(true); return true; }
      return false;
    },
  };
  views.agent = {
    mount(root) { this.o = detailFrame(root); this.sig = null; this.tabList = []; this.seen = null; },
    update(force) {
      const o = this.o;
      const d = S.data.agent[S.route.id + '/' + S.route.aid];
      P.attr(o.crumb, 'href', '#/s/' + encodeURIComponent(S.route.id));
      if (!d) {
        P.setText(o.crumb, '← Session');
        if (S.err.agent) { P.into(o.head, errorBox(S.err.agent, 'this subagent')); return; }
        if (!o.head.firstChild) o.head.append(h('div', { class: 'sk sk-line', style: { width: '45%', height: '22px' } }));
        return;
      }
      const a = d.agent;
      const last = d.events[d.events.length - 1];
      const sig = JSON.stringify([d.events.length, last && last.status, a.status, a.tokens, S.ui.dtab, S.ui.tl, S.ui.expandAll, S.ui.showAll, S.ui.order]);
      if (sig === this.sig && !force) return;
      const first = this.sig == null;
      this.sig = sig;
      this.nEvents = d.events.length;
      if (first) o.head.replaceChildren();
      P.setText(o.crumb, '← ' + d.session_title);
      P.into(o.head, h('h1', { class: 'd-title', text: a.description || a.agent_type || 'Subagent' }),
        h('div', { class: 'd-meta' }, subStatus(a.status), h('span', { class: 'tag', text: a.agent_type || 'subagent' }), a.phase ? h('span', { text: 'phase ' + a.phase }) : null,
          a.model ? modelTag(a.model, a.family) : null, a.model_requested ? h('span', { class: 'faint', text: 'asked for ' + a.model_requested }) : null,
          a.start ? h('span', { text: dateTime(a.start) + ', ' + dur(a.duration) }) : null, a.background ? h('span', { class: 'faint', text: 'background' }) : null,
          a.worktree ? h('span', { class: 'branch', text: a.worktree }) : null, h('span', { class: 'faint mono ellipsis', text: d.path })));
      P.into(o.kpis, h('div', { class: 'kpis' }, kpi('Estimated cost', money(a.cost, true), null, 'hero'), kpi('Tokens', compact(a.tokens)), kpi('Tool calls', int(a.tool_calls)),
        kpi('Errors', int(a.tool_errors), a.blocked ? plural(a.blocked, 'guard refusal') : null), kpi('Subagents', int(d.children.length))));
      this.tabList = [{ k: 'timeline', label: 'Timeline', n: d.events.length }, { k: 'tools', label: 'Tools', n: d.tools.length }, { k: 'tokens', label: 'Tokens and cost' }];
      if (!this.tabList.some((t) => t.k === S.ui.dtab)) S.ui.dtab = 'timeline';
      tabs(o, this.tabList, (f) => this.update(f));
      const ctx = { sid: S.route.id, sub: true, childFor: () => d.children, tab: () => {}, redraw: () => this.update(true) };
      let body;
      if (S.ui.dtab === 'timeline') body = h('div', {}, tlTools(this, d), timeline(d.events, ctx));
      else if (S.ui.dtab === 'tools') body = toolsTab(d.tools);
      else { const ins = costInsight(d.usage); body = h('div', { class: 'grid' }, h('section', { class: 'table-wrap' }, usageTable(d.usage, 'Model')), ins ? h('p', { class: 'insight', text: ins }) : null); }
      patchDetail(this, body, first, d.events.length);
    },
    keys(k) { return views.session.keys.call(this, k); },
  };

  // ── Keyboard ─────────────────────────────────────────────────────────
  const help = $('#help');
  $('#help-btn').addEventListener('click', () => help.showModal());
  $('#help-close').addEventListener('click', () => help.close());
  document.addEventListener('keydown', (e) => {
    if (e.metaKey || e.ctrlKey || e.altKey) return;
    const tag = (e.target.tagName || '').toLowerCase();
    if (tag === 'input' || tag === 'select' || tag === 'textarea' || help.open) return;
    const k = e.key;
    if (k === '/') { e.preventDefault(); if (S.route.view !== 'sessions') location.hash = sessionsHash(); setTimeout(() => { const q = $('#q'); if (q) q.focus(); }, 30); return; }
    if (k === '?') { help.showModal(); return; }
    if (k === 't') { cycleTheme(); return; }
    if (k === 'l') { location.hash = '#/live'; return; }
    if (k === 's') { location.hash = sessionsHash(); return; }
    if (k === 'o') { location.hash = overviewHash(); return; }
    if (k === 'Escape' && (S.route.view === 'session' || S.route.view === 'agent')) { location.hash = S.route.view === 'agent' ? '#/s/' + encodeURIComponent(S.route.id) : (S.backTo || '#/sessions'); return; }
    if (current && current.keys && current.keys(k)) e.preventDefault();
  });

  onRoute();
})();
