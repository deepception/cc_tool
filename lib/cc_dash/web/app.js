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
  /* The compact forms for the header's tight spots (selfheal.js): "4m", "1h 05m"
     and "3d ago", never a date or "yesterday". The 1 s ticker below uses them
     for an element marked data-fmt="short". */
  function durShort(sec) {
    if (sec == null || !isFinite(sec)) return '';
    sec = Math.max(0, sec);
    if (sec < 60) return '<1m';
    const m = Math.floor(sec / 60);
    if (m < 60) return m + 'm';
    const hh = Math.floor(m / 60);
    return hh < 48 ? hh + 'h ' + String(m % 60).padStart(2, '0') + 'm' : Math.floor(hh / 24) + 'd ' + (hh % 24) + 'h';
  }
  function agoShort(ts) {
    if (!ts) return '';
    const d = nowS() - ts;
    if (d < 60) return 'just now';
    if (d < 3600) return Math.floor(d / 60) + 'm ago';
    if (d < 86400) return Math.floor(d / 3600) + 'h ago';
    return Math.floor(d / 86400) + 'd ago';
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
    route: { view: 'factory' }, meta: null,
    // live: GET /api/live, polled in the background for control.js (window.ccDashLive)
    data: { live: null, sessions: null, overview: null, routing: null, routingScope: null, session: {}, agent: {}, tree: {} },
    err: {}, lastOk: 0, lastFail: null,
    ui: {
      q: '', wt: '', family: '', errorsOnly: false, sort: { k: 'last_write', d: -1 }, sel: -1,
      scope: '', psort: { k: 'cost', d: -1 }, pexp: new Set(), unclamp: new Set(),
      dtab: 'timeline', expanded: new Set(), expandAll: false, tl: 'all', showAll: false,
      act: 'tools',
      days: null,   // the time window in days (header picker); null until decided, then requests carry no days and the server uses its launch value
    },
  };
  const main = $('#main');
  let PL = null;   // plans.js (window.ccPlans): the Plans views, plan chips and the Overview panel; set at the end
  let RT = null;   // routing.js (window.ccRouting): the Overview's Routing panel; set at the end
  let AG = null;   // agents.js (window.ccAgents): the session Agents tab and the effort chip; set at the end
  let FA = null;   // factory.js (window.ccFactory): the Factory floor, the landing view; set at the end
  const effortTag = (e, o) => (AG ? AG.effortTag(e, o) : null);

  // ── Theme ────────────────────────────────────────────────────────────
  const THEMES = ['system', 'light', 'dark'];
  const store = { get(k) { try { return localStorage.getItem(k); } catch (e) { return null; } },
    set(k, v) { try { localStorage.setItem(k, v); } catch (e) { /* private mode */ } } };
  { const d = Number(store.get('cc-dash-days')); S.ui.days = [1, 3, 7, 14, 30].includes(d) ? d : null; }   // until /api/meta lands only a standard choice is trusted
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
  /* The endpoints that cut by the time window take the picked one as days=<n>
     (S.ui.days; none until decided, then the server uses its launch value).
     winGen counts picks: while a windowed view waits for the first answer of the
     newest pick, #main is dimmed and aria-busy (winBusy). An answer to an older
     pick still renders; the next poll replaces it. */
  const WINDOWED = /^\/api\/(graph|sessions|overview|routing|plans|plans\/detail|session|session\/tree|agent)$/;
  let winGen = 0, winDim = false;
  function winBusy(on) {
    if (on === winDim) return;
    winDim = on;
    main.classList.toggle('stale', on);
    if (on) main.setAttribute('aria-busy', 'true'); else main.removeAttribute('aria-busy');
  }
  async function api(path) {
    const qi = path.indexOf('?');
    const win = WINDOWED.test(qi < 0 ? path : path.slice(0, qi)), gen = winGen;
    if (win && S.ui.days != null) path += (qi < 0 ? '?' : '&') + 'days=' + S.ui.days;
    try {
      const r = await window.ccAuth.fetch(path);
      let body = null;
      try { body = await r.json(); } catch (e) { /* not json */ }
      if (r.status === 401) { repair(); const e = new Error('Not paired'); e.status = 401; throw e; }
      if (!r.ok) { const e = new Error((body && body.error) || ('HTTP ' + r.status)); e.status = r.status; throw e; }
      S.lastOk = nowS(); S.lastFail = null;
      if (S.unpaired) { S.unpaired = false; document.documentElement.classList.remove('unpaired'); mount(); }
      return body;
    } finally {
      if (win && gen === winGen) winBusy(false);   // the newest window's first answer (or failure) landed: undim before the view renders it
    }
  }
  /* Every data request needs this launch's pairing cookie. Without it (a new
     browser, or cc-dash was restarted) the page says how to pair and keeps
     polling, so it recovers by itself once the link is opened here. */
  function repair() {
    if (S.unpaired) return;
    S.unpaired = true;
    document.documentElement.classList.add('unpaired');
    if (current && current.unmount) current.unmount();
    current = null;
    main.replaceChildren(h('div', { class: 'empty repair' },
      h('h2', { text: 'Pair this browser with cc-dash' }),
      window.ccAuth.failed() ? h('p', { class: 'bad', text: window.ccAuth.failed() }) : null,
      h('p', { text: 'cc-dash shows your sessions only to a browser that opened its pairing link. Each link works once, for 60 seconds, and a restart of cc-dash ends every pairing.' }),
      h('p', {}, 'Press Enter in the terminal running cc-dash for a new link, or start ', h('code', { text: 'cc-dash' }), ' and open the link it prints.'),
      h('p', { class: 'faint', text: 'This page checks again every few seconds.' })));
  }
  function failed(e) {
    if (S.unpaired) return;
    S.lastFail = e && e.message ? e.message : 'Server unreachable';
    if (current && S.route.view === 'sessions' && !S.data.sessions) update();   // first load failed: say so instead of a skeleton
  }

  // ── Router ───────────────────────────────────────────────────────────
  /* The Projects area's views carry their project in the hash
     (#/overview?p=<project>, #/sessions?p=<project>&wt=<worktree>, #/plans?p=,
     #/docs?p=), so reload and back keep it. Values are only ever compared
     with project names and sent as a query value. */
  function parseRoute() {
    const raw = location.hash.replace(/^#\/?/, '');
    const qi = raw.indexOf('?');
    const q = new URLSearchParams(qi >= 0 ? raw.slice(qi + 1) : '');
    const safe = (fn) => { try { return fn(); } catch (e) { return null; } };
    const p = (qi >= 0 ? raw.slice(0, qi) : raw).split('/').map((x) => safe(() => decodeURIComponent(x)) || '');
    const qp = () => (q.get('p') || '').slice(0, 200);
    if (p[0] === 's' && p[1]) return p[2] === 'a' && p[3] ? { view: 'agent', id: p[1], aid: p[3] } : { view: 'session', id: p[1] };
    if (p[0] === 'docs') return { view: 'docs', p: qp(), f: (q.get('f') || '').slice(0, 1024), h: (q.get('h') || '').slice(0, 200) };
    if (p[0] === 'sessions') return { view: 'sessions', p: qp(), wt: qp() ? (q.get('wt') || '').slice(0, 200) : '' };
    if (p[0] === 'overview') return { view: 'overview', p: qp() };
    const pr = PL && PL.route(p, q);   // plans.js: #/plans?p= and #/plan?p=&f=
    if (pr) return pr;
    // #/factory?p=<project>&all=1, also the landing view (an empty or unknown
    // hash); the Overview, in the remembered scope, when factory.js is not loaded.
    const fx = p[0] === 'factory';
    if (!views.factory) return { view: 'overview', p: fx ? qp() : S.ui.scope };
    return { view: 'factory', p: fx ? qp() : '', all: fx && q.get('all') === '1' };
  }
  /* The Live tab is gone (the Factory floor took its place): #/live and
     #/live?p=<project> are replaced, with no extra Back entry, by the floor
     with the project kept, or by the Overview when factory.js is not loaded. */
  function movedHash() {
    if (!/^#\/?live(?:[/?]|$)/.test(location.hash)) return null;
    const qi = location.hash.indexOf('?');
    const p = (new URLSearchParams(qi >= 0 ? location.hash.slice(qi + 1) : '').get('p') || '').slice(0, 200);
    return views.factory ? '#/factory' + (p ? '?p=' + encodeURIComponent(p) : '') : viewHash('overview', p || S.ui.scope);
  }
  function viewHash(view, p, wt) {
    const q = new URLSearchParams();
    if (p) q.set('p', p);
    if (p && wt) q.set('wt', wt);
    const qs = q.toString();
    return '#/' + view + (qs ? '?' + qs : '');
  }
  /* The Projects area: Overview, Sessions, Plans and Docs share one project
     scope (S.ui.scope, '' for all projects). It is picked in the page title,
     carried in each view's URL (?p=) and remembered, so moving between them
     keeps the project; the Projects tab returns to the last of them you used.
     Sessions also filters by worktree (S.ui.wt, ?wt=): picked in the same
     title, kept while the project stays, dropped when it changes. */
  const AREA = [['overview', 'Overview'], ['sessions', 'Sessions'], ['plans', 'Work'], ['docs', 'Docs']];
  const AREA_VIEWS = ['overview', 'sessions', 'plans', 'plan', 'docs'];
  S.ui.scope = store.get('cc-dash-scope') || '';
  S.ui.area = store.get('cc-dash-area') || 'overview';
  if (!AREA.some(([k]) => k === S.ui.area)) S.ui.area = 'overview';
  function setScope(p) {
    p = p || '';
    if (p !== S.ui.scope) S.ui.wt = '';
    S.ui.scope = p;
    store.set('cc-dash-scope', p);
  }
  function areaHash(view, p) {
    if (view === 'sessions') return viewHash('sessions', p, p && p === S.ui.scope ? S.ui.wt : '');
    if (view === 'plans') return '#/plans' + (p ? '?p=' + encodeURIComponent(p) : '');
    if (view === 'docs') return views.docs ? views.docs.hashFor(p) : '#/docs';
    return viewHash('overview', p);
  }
  function syncTabLinks() {
    const pl = document.querySelector('.tabs a[data-view="projects"]');
    const fl = document.querySelector('.tabs a[data-view="factory"]');
    if (pl) pl.setAttribute('href', areaHash(S.ui.area, S.ui.scope));
    if (fl && FA) fl.setAttribute('href', FA.hash());
  }
  let prevRoute = null;
  function onRoute() {
    const moved = movedHash();
    if (moved) history.replaceState(null, '', moved);
    const r = parseRoute();
    const same = prevRoute && prevRoute.view === r.view && prevRoute.id === r.id && prevRoute.aid === r.aid && (r.view !== 'plan' || prevRoute.f === r.f);
    if (!same && (r.view === 'session' || r.view === 'agent') && (!prevRoute || prevRoute.id !== r.id || prevRoute.aid !== r.aid)) {
      S.ui.dtab = 'timeline'; S.ui.expanded = new Set(); S.ui.expandAll = false; S.ui.tl = 'all'; S.ui.showAll = false; S.ui.unclamp = new Set();
    }
    if (r.view === 'sessions') {
      if (S.ui.scope !== r.p || S.ui.wt !== r.wt) S.ui.sel = -1;
      setScope(r.p);
      S.ui.wt = r.wt;
    }
    if (r.view === 'overview' || r.view === 'plans' || r.view === 'plan' || (r.view === 'docs' && r.p)) setScope(r.p);
    if (AREA.some(([k]) => k === r.view)) { S.ui.area = r.view; store.set('cc-dash-area', r.view); }
    S.route = r;
    if (!['session', 'agent'].includes(r.view)) S.backTo = location.hash || '#/' + r.view;
    prevRoute = r;
    syncTabLinks();
    // A session or agent page stays under the tab it was opened from: Factory from the floor, else Projects.
    const tab = ['session', 'agent'].includes(r.view) ? (/^#\/factory/.test(S.backTo || '') ? 'factory' : 'projects')
      : AREA_VIEWS.includes(r.view) ? 'projects' : r.view;
    for (const a of document.querySelectorAll('.tabs a')) {
      if (a.dataset.view === tab) a.setAttribute('aria-current', 'page'); else a.removeAttribute('aria-current');
    }
    if (!same) { mount(); window.scrollTo(0, 0); } else update();
    syncSource();
    tick(true);
  }
  window.addEventListener('hashchange', onRoute);

  // ── Polling ──────────────────────────────────────────────────────────
  let timer = null, inflight = false;
  const due = {};
  function interval() {
    const v = S.route.view;
    if (v === 'factory') return 3000;
    if (v === 'sessions') return 10000;
    if (v === 'overview') return 30000;
    if (v === 'docs') return 3000;          // the open document's mtime (live reload)
    const d = v === 'session' ? S.data.session[S.route.id] : S.data.agent[S.route.id + '/' + S.route.aid];
    const live = d && (v === 'session' ? d.session.state : d.agent.status === 'running');
    return live ? 3000 : 20000;
  }
  /* The Agents tab's tree (/api/session/tree), fetched only while that tab is open. */
  function loadTree(id) {
    return api('/api/session/tree?id=' + encodeURIComponent(id)).then((d) => { S.data.tree[id] = d; if (S.route.id === id && S.ui.dtab === 'agents') update(); });
  }
  async function tick(force) {
    clearTimeout(timer);
    if (document.hidden && !force) { timer = setTimeout(tick, 5000); return; }
    if (inflight) { timer = setTimeout(tick, 500); return; }
    inflight = true;
    const r = S.route;
    const t = nowS(), t0 = performance.now();
    let ok = false;
    try {
      const jobs = [];
      if (!S.meta) jobs.push(api('/api/meta').then((m) => { S.meta = m; scopeText(); }));
      // What is running now, for control.js (its composer reads window.ccDashLive); no view draws it.
      if (force || !due.live || t - due.live > 5) {
        jobs.push(api('/api/live').then((d) => { S.data.live = d; window.ccDashLive = d; due.live = t; }));
      }
      if (r.view === 'sessions') {
        jobs.push(api('/api/sessions').then((d) => { S.data.sessions = d; if (S.route.view === 'sessions') update(); }));
      } else if (r.view === 'overview') {
        const scope = S.ui.scope;
        jobs.push(api('/api/overview' + (scope ? '?project=' + encodeURIComponent(scope) : '')).then((d) => {
          if (S.ui.scope !== scope) return;           // the scope changed while this was in flight
          S.data.overview = d; S.data.overviewScope = scope; S.err.overview = null; if (S.route.view === 'overview') update();
        }).catch((e) => { S.err.overview = e; if (S.route.view === 'overview') update(); throw e; }));
        // The Routing panel is extra: if its endpoint fails, the Overview just goes without it.
        jobs.push(api('/api/routing' + (scope ? '?project=' + encodeURIComponent(scope) : '')).then((d) => {
          if (S.ui.scope !== scope) return;
          S.data.routing = d; S.data.routingScope = scope; if (S.route.view === 'overview') update();
        }).catch(() => {}));
      } else if (r.view === 'docs' && views.docs) {
        jobs.push(views.docs.poll());
      } else if (r.view === 'factory' && views.factory) {
        jobs.push(views.factory.poll());
      } else if (r.view === 'session') {
        const id = r.id;
        jobs.push(api('/api/session?id=' + encodeURIComponent(id)).then((d) => { S.data.session[id] = d; S.err.session = null; if (S.route.id === id) update(); })
          .catch((e) => { S.err.session = e; if (S.route.id === id) update(); if (e.status !== 404) throw e; }));
        if (S.ui.dtab === 'agents') jobs.push(loadTree(id));
      } else if (r.view === 'agent') {
        const k = r.id + '/' + r.aid;
        jobs.push(api('/api/agent?session=' + encodeURIComponent(r.id) + '&id=' + encodeURIComponent(r.aid)).then((d) => { S.data.agent[k] = d; S.err.agent = null; update(); })
          .catch((e) => { S.err.agent = e; update(); if (e.status !== 404) throw e; }));
      }
      const res = await Promise.allSettled(jobs);
      const bad = res.find((x) => x.status === 'rejected');
      if (bad) failed(bad.reason); else ok = true;
    } finally {
      inflight = false;
      timer = setTimeout(tick, interval());
      syncDone('main', ok, interval(), performance.now() - t0);
    }
  }
  document.addEventListener('visibilitychange', () => { if (!document.hidden) tick(true); });

  // ── Chrome: window picker, scope and sync bar ────────────────────────
  /* The window picker: the house dropdown (see projectPicker) with a plain
     listbox of 1, 3, 7, 14 and 30 days plus the launch value, in the header
     beside the scope text. Choosing sets S.ui.days, remembers it in this
     browser and refreshes the view; the server reads older transcripts the
     first time a wider window is asked for, so that answer can take a while
     and #main stays dimmed (winBusy) until it lands. Keyboard: Enter, Space
     or an arrow opens; arrows, Home and End move; Enter or Space picks; Esc
     closes to the button; Tab closes. */
  const WIN_STD = [1, 3, 7, 14, 30];
  const W = { open: false, active: 0, opts: [], resolved: false };
  const fmtDays = (n) => String(+Number(n).toFixed(2));
  function winLabel(d) {
    if (!(d > 0)) return '';
    if (d < 1) { const hr = Math.round(d * 240) / 10; return hr === 1 ? 'Last hour' : 'Last ' + fmtDays(hr) + ' hours'; }
    return d === 1 ? 'Last day' : 'Last ' + fmtDays(d) + ' days';
  }
  const winShort = (d) => (d > 0 ? (d < 1 ? fmtDays(Math.round(d * 240) / 10) + 'h' : fmtDays(d) + 'd') : '');   // the phone's label: 7d, 12h
  function winOptions() {
    const m = S.meta || {};
    const o = Array.isArray(m.days_options) && m.days_options.length ? m.days_options : WIN_STD.concat(m.days > 0 ? [m.days] : []);
    return [...new Set(o.filter((n) => typeof n === 'number' && n > 0))].sort((a, b) => a - b);
  }
  const winCur = () => (S.ui.days != null ? S.ui.days : S.meta ? S.meta.days : null);
  /* The empty views point at the picker, unless it is already at its widest. */
  function canWiden() { const o = winOptions(), d = winCur(); return d == null || (o.length > 0 && d < o[o.length - 1]); }
  function winPaint() {
    const cur = winCur(), lbl = winLabel(cur);
    P.setText(W.label, lbl);
    P.attr(W.label, 'data-short', winShort(cur));
    P.attr(W.btn, 'aria-label', lbl ? 'Window: ' + lbl.toLowerCase() : 'Window');
    P.attr(W.btn, 'aria-expanded', String(W.open));
    [...W.list.children].forEach((li, i) => {
      li.classList.toggle('active', W.open && i === W.active);
      li.setAttribute('aria-selected', String(W.opts[i] === cur));
    });
    P.attr(W.list, 'aria-activedescendant', W.open ? 'win-o' + W.active : null);
  }
  function winBuild() {
    W.opts = winOptions();
    const def = S.meta && S.meta.days;
    W.list.replaceChildren(...W.opts.map((n, i) => h('li', { class: 'picker-opt', role: 'option', id: 'win-o' + i, dataset: { days: String(n) }, 'aria-selected': 'false',
      onpointermove: () => { if (W.active !== i) { W.active = i; winPaint(); } }, onclick: () => winPick(n) },
    h('span', { class: 'picker-name ellipsis', text: winLabel(n) }), n === def ? h('span', { class: 'picker-n', text: 'default' }) : null)));
  }
  function winInit() {
    if (W.el) return;
    W.label = h('span', { class: 'picker-label' });
    W.btn = h('button', { type: 'button', id: 'win-btn', class: 'picker-btn', 'aria-haspopup': 'listbox', 'aria-expanded': 'false', 'aria-controls': 'win-pop',
      onclick: () => (W.open ? winClose(false) : winOpen()),
      onkeydown: (e) => { if (e.key === 'ArrowDown' || e.key === 'ArrowUp') { e.preventDefault(); winOpen(); } } }, W.label, chev());
    W.list = h('ul', { class: 'picker-list', role: 'listbox', id: 'win-list', tabindex: '-1', 'aria-label': 'Time window', onkeydown: winKeys });
    W.pop = h('div', { class: 'picker-pop', id: 'win-pop', hidden: true }, W.list);
    W.el = h('span', { class: 'picker win', id: 'win' }, W.btn, W.pop);
    W.sep = h('span', { class: 'sep', text: ', ' });   // the comma of "Last 7 days, ~/.claude": text for readers of the DOM, not drawn beside the button
    W.rest = document.createTextNode('');
    W.txt = h('span', { id: 'scope-txt', class: 'scope-txt' }, W.sep, W.rest);
    $('#scope').replaceChildren(W.el, W.txt);
  }
  function winOpen() {
    if (W.open) return;
    W.open = true; W.pop.hidden = false;
    W.active = Math.max(0, W.opts.indexOf(winCur()));
    winPaint();
    W.list.focus({ preventScroll: true });
    document.addEventListener('pointerdown', winOutside, true);
  }
  function winClose(refocus) {
    if (!W.open) return;
    W.open = false; W.pop.hidden = true;
    document.removeEventListener('pointerdown', winOutside, true);
    winPaint();
    if (refocus) W.btn.focus();
  }
  function winOutside(e) { if (!W.el.contains(e.target)) winClose(false); }
  function winPick(n) { winClose(true); setWindow(n, true); }
  function winKeys(e) {
    e.stopPropagation();   // the page's single-key shortcuts must not fire while the list has focus
    const go = (j) => { W.active = Math.max(0, Math.min(W.opts.length - 1, j)); winPaint(); };
    if (e.key === 'ArrowDown') { e.preventDefault(); go(W.active + 1); }
    else if (e.key === 'ArrowUp') { e.preventDefault(); go(W.active - 1); }
    else if (e.key === 'Home') { e.preventDefault(); go(0); }
    else if (e.key === 'End') { e.preventDefault(); go(W.opts.length - 1); }
    else if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); if (W.opts.length) winPick(W.opts[W.active]); }
    else if (e.key === 'Escape') { e.preventDefault(); winClose(true); }
    else if (e.key === 'Tab') winClose(true);   // focus is back on the button, so Tab moves on from there
  }
  /* A new window: remember it when the person picked it, dim a windowed view
     until the newest window's answer lands, then poll everything that reads it. */
  function setWindow(n, persist) {
    if (persist) store.set('cc-dash-days', String(n));
    if (n === S.ui.days) return;
    S.ui.days = n;
    winGen++;
    if (S.route.view !== 'docs') winBusy(true);
    winPaint();
    tick(true);
    if (PL && PL.poll) PL.poll();
  }
  // A view that sends no windowed request has nothing to wait for.
  window.addEventListener('hashchange', () => { if (S.route.view === 'docs') winBusy(false); });
  function scopeText() {
    const m = S.meta;
    if (!m) return;
    winInit();
    winBuild();
    if (!W.resolved) {   // once: the remembered window if this server offers it, else the launch value
      W.resolved = true;
      const stored = Number(store.get('cc-dash-days')), want = W.opts.includes(stored) ? stored : m.days;
      if (want !== (S.ui.days == null ? m.days : S.ui.days)) setWindow(want, false); else S.ui.days = want;
    }
    winPaint();
    W.rest.nodeValue = (m.project ? m.project + ', ' : '') + m.root;
  }
  /* The sync bar. It fills over the time until the view's next poll answer is
     due (the poll interval plus the recent round trip) and empties the moment
     one lands and is applied, so an empty bar means fresh data. Its clock is
     that real schedule: a Web Animation restarted at each answer, so the fill
     runs on the compositor and writes nothing to the DOM per frame. Past due it
     holds full and pulses faintly; a failed poll freezes it full in the failed
     colour beside the established "stopped responding" line. A hidden tab
     pauses it; on return it holds until the answer to the catch-up poll lands
     (or resyncs to the real schedule if that is slow). Reduced motion swaps it
     for the plain "Updated ..." text. Screen readers hear only the two state
     changes, never a routine poll; the full detail is in the tooltip.
     Sources: the app's own poll (tick) drives every view except Plans, whose
     data comes from plans.js's poll. */
  const syncEl = $('#sync'), syncFill = syncEl.querySelector('.sync-fill'), syncMsg = syncEl.querySelector('.sync-msg');
  const RM = matchMedia('(prefers-reduced-motion: reduce)');
  const SYNC_GRACE = 700;       // ms past due before the bar reads as late
  const SB = { main: { at: 0, wall: 0, every: 0, rtt: 0, fail: false }, plans: { at: 0, wall: 0, every: 0, rtt: 0, fail: false },
    cur: 'main', anim: null, lateT: 0, resync: false, wasFail: false };
  const syncSrc = () => (PL && PL.isPlanView(S.route.view) ? 'plans' : 'main');
  function syncDone(src, ok, every, rtt) {
    const x = SB[src];
    if (ok) { x.at = performance.now(); x.wall = nowS(); x.every = every; x.rtt = x.rtt ? Math.min(rtt, (x.rtt + rtt) / 2) : rtt; x.fail = false; }
    else x.fail = !S.unpaired;
    if (src !== syncSrc()) return;
    if (ok) SB.resync = false;
    syncFillRun();
    syncText();
  }
  function syncSource() {
    if (syncSrc() === SB.cur) return;
    SB.cur = syncSrc();
    syncFillRun();
    syncText();
  }
  function syncLate(on) { syncEl.classList.toggle('late', on); }
  function syncFillRun() {
    const x = SB[SB.cur];
    clearTimeout(SB.lateT);
    syncLate(false);
    if (SB.anim) { SB.anim.cancel(); SB.anim = null; }
    // Empty track before the first answer and while unpaired; full (by CSS) on failure.
    if (!x.at || x.fail || S.unpaired || RM.matches) return;
    const due = x.every + Math.min(x.rtt, 2000);
    const off = Math.min(performance.now() - x.at, due);
    SB.anim = syncFill.animate([{ transform: 'scaleX(0)' }, { transform: 'scaleX(1)' }], { duration: due, fill: 'forwards' });
    SB.anim.currentTime = off;
    if (document.hidden) { SB.anim.pause(); return; }
    SB.lateT = setTimeout(() => { if (!document.hidden && !SB.resync) syncLate(true); }, due - off + SYNC_GRACE);
  }
  document.addEventListener('visibilitychange', () => {
    if (document.hidden) { if (SB.anim) SB.anim.pause(); clearTimeout(SB.lateT); return; }
    // Back: a catch-up poll is under way (tick and plans.js poll now); hold
    // the frozen bar until it lands rather than flashing to full first.
    SB.resync = true;
    setTimeout(() => { if (SB.resync) { SB.resync = false; syncFillRun(); } }, SYNC_GRACE);
  });
  RM.addEventListener('change', () => { syncFillRun(); syncText(); });
  const syncAge = (x) => { const d = (performance.now() - x.at) / 1000; return 'Updated ' + (d < 2 ? 'just now' : dur(d) + ' ago'); };
  function syncText() {
    const x = SB[SB.cur], fail = x.fail && !S.unpaired;
    syncEl.classList.toggle('err', fail);
    P.setText(syncMsg, fail ? 'cc-dash stopped responding' : !RM.matches ? '' : x.at ? syncAge(x) : 'Loading');
    if (fail !== SB.wasFail) {
      $('#sync-live').textContent = fail ? 'cc-dash stopped responding' : 'cc-dash is responding again';
      SB.wasFail = fail;
    }
    if (syncEl.matches(':hover')) syncTitle();
  }
  // The tooltip is written while the pointer is on it, not for every poll.
  function syncTitle() {
    const x = SB[SB.cur];
    const parts = [x.fail && !S.unpaired ? 'cc-dash stopped responding' : null, x.at ? syncAge(x) : 'Loading', x.every ? 'refreshes every ' + dur(x.every / 1000) : null];
    const t = parts.filter(Boolean).join(' · ');
    if (syncEl.title !== t) syncEl.title = t;
  }
  syncEl.addEventListener('pointerenter', syncTitle);

  setInterval(() => {
    syncText();
    const t = nowS();
    for (const el of document.querySelectorAll('[data-since]')) P.setText(el, (el.dataset.fmt === 'short' ? durShort : dur)(t - Number(el.dataset.since)));
    for (const el of document.querySelectorAll('[data-ago]')) P.setText(el, (el.dataset.fmt === 'short' ? agoShort : ago)(Number(el.dataset.ago)));
  }, 1000);

  // ── Tooltip ──────────────────────────────────────────────────────────
  const tip = $('#tip');
  function showTip(ev, title, rows, total) {
    tip.replaceChildren(h('div', { class: 'tt', text: title }),
      ...rows.map((r) => h('div', { class: 'tr' + (r.cls ? ' ' + r.cls : '') }, r.fam ? key(r.fam, true) : r.nokey ? h('span') : h('span', { class: 'key line', style: { background: r.color || 'var(--ink-3)' } }), h('span', { class: r.mono ? 'mono' : null, text: r.name }), h('b', { text: r.value }))),
      ...(total ? [h('div', { class: 'tr total' }, h('span'), h('span', { text: total.name }), h('b', { text: total.value }))] : []));
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
  function kpi(label, value, note, cls, title) {
    const v = h('div', { class: 'value' });
    if (Array.isArray(value)) append(v, [value[0], h('small', { text: value[1] })]); else v.textContent = value;
    return h('div', { class: 'kpi ' + (cls || ''), title: title || null }, h('div', { class: 'label', text: label }), v, note ? h('div', { class: 'note', text: note }) : null);
  }
  /* One rule for tokens everywhere: cost is the headline; "New tokens" is input + cache
     writes + output; cache reads are shown apart and never added in. Output on replies saved
     before they finished is estimated from their content (parser.py) and said so. */
  function cacheOff(rows) {
    const p = {};
    for (const r of (S.meta ? S.meta.pricing : [])) p[r.id] = r;
    const top = (rows || []).slice().sort((a, b) => (b.cost || 0) - (a.cost || 0))[0];
    const pr = top && p[top.model.replace(/\[.*\]$/, '').replace(/-\d{8}$/, '')];
    return pr && pr.input ? Math.round(100 * (1 - pr.cache_read / pr.input)) : null;
  }
  const estNote = (t) => (t.output_estimated ? 'Includes about ' + compact(t.output_estimated) + ' output tokens estimated from ' + plural(t.est_msgs, 'reply', 'replies')
    + ' saved before they finished (hidden thinking is not visible, so this is a floor).' : '');
  function tokenTitle(t) {
    return ['Input ' + int(t.input), 'Output ' + int(t.output) + (t.output_estimated ? ' (about ' + int(t.output_estimated) + ' estimated)' : ''), 'Cache write ' + int(t.cache_write),
      'Cache read ' + int(t.cache_read) + ' (not in the headline)'].join('\n') + (t.output_estimated ? '\n' + estNote(t) : '');
  }
  function newTokensKpi(t, rows) {
    const off = cacheOff(rows);
    // The note stays short enough for a nine-tile strip; the tooltip says it in full.
    return kpi('New tokens', (t.output_estimated ? '≥' : '') + compact(t.tokens),
      t.cache_read ? '+' + compact(t.cache_read) + ' re-read' + (off != null ? ', ' + off + '% off' : '') : 'no cache reads', '',
      (t.cache_read ? '+' + int(t.cache_read) + ' tokens re-read from cache' + (off != null ? ' (' + off + '% off the input price)' : '') + ', not counted here.\n' : '') + tokenTitle(t));
  }
  const contextKpi = (t) => kpi('Context', compact(t.context || 0), 'last call\'s prompt', '', 'Input + cache read + cache write of the latest API call: how much the model re-reads each turn, which is what makes cost grow.');
  /* The status language (app.css "One status language"): one word, colour and
     glyph per state. control.js, alerts.js and plans.js build the same markup. */
  const stGlyph = (state) => h('span', { class: 'g s-' + state, 'aria-hidden': 'true' });
  const stPill = (state, label, cls) => h('span', { class: 'pill s-' + state + (cls ? ' ' + cls : '') }, h('span', { class: 'g', 'aria-hidden': 'true' }), label);
  function statePill(state) {
    if (state === 'working') return stPill('working', 'Working');
    if (state === 'waiting') return stPill('turn', 'Your turn');
    if (state === 'idle') return stPill('idle', 'Idle');
    return null;
  }
  function subStatus(st) {
    if (st === 'running') return stPill('working', 'Working');
    if (st === 'failed') return stPill('failed', 'Failed');
    if (st === 'stopped') return stPill('stalled', 'Did not finish');
    if (st === 'done' || st === 'completed') return stPill('done', 'Done');
    if (st === 'error') return stPill('failed', 'Error');
    return stPill('idle', st || 'Unknown');
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
    if (S.unpaired) return;
    hideTip();
    if (current && current.unmount) current.unmount();   // the Factory stops its graph engine
    current = views[S.route.view];
    main.replaceChildren();
    current.mount(main);
    update();
  }
  function update() { if (current && !S.unpaired) current.update(); }

  // Decisions and state glyphs ------------------------------------------
  const { setText } = window.ccDom;
  const reducedMotion = () => window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  /* Two different things: "Needs a decision" (a permission, question or plan:
     amber, with the action buttons beside it in control mode) and "Your turn"
     (idle at the prompt: neutral, a message box away). */
  const DECISIONS = ['permission', 'question', 'plan'];
  const isDecision = (n) => !!n && DECISIONS.includes(n.kind);
  function needsText(n) {
    if (n.kind === 'permission') return 'approve ' + (n.tool || 'a tool call');
    if (n.kind === 'question') return 'answer a question';
    if (n.kind === 'plan') return 'review the plan';
    return n.title || 'needs attention';
  }
  /* The decision (from alerts.js) open for a session or one of its agents, for detail headers. */
  function openDecision(session, aid) {
    const al = (window.ccDashAlerts && window.ccDashAlerts.alerts) || [];
    return al.find((a) => a.session === session && isDecision(a) && (aid === undefined || (a.agent_id || null) === (aid || null))) || null;
  }
  const decidePill = (al) => stPill('decide', 'Needs a decision: ' + needsText(al));
  document.addEventListener('cc-dash:alerts', () => { if (['session', 'agent'].includes(S.route.view)) update(); });
  document.addEventListener('cc-control:ready', () => update());
  /* The state glyph (plan tasks, the Factory floor): a check, a cross, a dot or a ring. */
  function glyph(cat) {
    const v = s('svg', { class: 'sg sg-' + cat, viewBox: '0 0 12 12', 'aria-hidden': 'true' });
    if (cat === 'done') v.append(s('path', { d: 'M2.6 6.3 5 8.6l4.4-5', fill: 'none', stroke: 'currentColor', 'stroke-width': 1.6, 'stroke-linecap': 'round', 'stroke-linejoin': 'round' }));
    else if (cat === 'failed') v.append(s('path', { d: 'M3.2 3.2l5.6 5.6M8.8 3.2 3.2 8.8', fill: 'none', stroke: 'currentColor', 'stroke-width': 1.6, 'stroke-linecap': 'round' }));
    else if (cat === 'working' || cat === 'needs') v.append(s('circle', { cx: 6, cy: 6, r: 3.6, fill: 'currentColor' }));
    else v.append(s('circle', { cx: 6, cy: 6, r: 3.4, fill: 'none', stroke: 'currentColor', 'stroke-width': 1.4, 'stroke-dasharray': cat === 'stalled' ? '1.6 1.6' : null }));
    return v;
  }

  /* Project picker: a button that opens a filterable listbox of projects with
     session counts; worktrees fold under their project (→ or the chevron shows
     them, typing finds them). It is built once per view and only patched on a
     poll, so an open list never closes or changes under the pointer. */
  let pickerSeq = 0;
  function projectPicker({ label, allLabel, withWorktrees, onPick, noAll = false, counts = true, title = false }) {
    const uid = 'pk' + (++pickerSeq);
    const st = { open: false, q: '', active: '', exp: new Set(), items: [], value: { p: '', wt: '' }, total: 0 };
    const keyOf = (p, wt) => (wt ? 'w:' + p + '\u0000' + wt : 'p:' + p);
    const btnLabel = h('span', { class: 'picker-label ellipsis' });
    const btnCount = h('span', { class: 'picker-n num' });
    const btn = h('button', { type: 'button', class: 'picker-btn', 'aria-haspopup': 'listbox', 'aria-expanded': 'false', 'aria-label': label,
      onclick: () => (st.open ? close(false) : open()),
      onkeydown: (e) => { if (e.key === 'ArrowDown' || e.key === 'ArrowUp') { e.preventDefault(); open(); } } }, btnLabel, counts ? btnCount : null, chev());
    const input = h('input', { type: 'text', class: 'picker-q', role: 'combobox', 'aria-autocomplete': 'list', 'aria-expanded': 'true', 'aria-controls': uid + '-list',
      placeholder: withWorktrees ? 'Filter projects and worktrees' : 'Filter projects', 'aria-label': 'Filter ' + label.toLowerCase(), autocomplete: 'off', spellcheck: 'false',
      oninput: (e) => { st.q = e.target.value; const v = visible(); st.active = v.length ? (v.find((r) => r.key === st.active) || v[0]).key : ''; render(); },
      onkeydown: (e) => keys(e) });
    const list = h('ul', { class: 'picker-list', role: 'listbox', id: uid + '-list', 'aria-label': label });
    const hint = h('div', { class: 'picker-hint', text: withWorktrees ? '↑ ↓ move  ·  Enter pick  ·  → worktrees  ·  Esc close' : '↑ ↓ move  ·  Enter pick  ·  Esc close' });
    const pop = h('div', { class: 'picker-pop', hidden: true }, h('div', { class: 'picker-search' }, input), list, hint);
    const el = h('div', { class: 'picker' + (title ? ' title' : '') }, btn, pop);
    function visible() {
      const q = st.q.trim().toLowerCase();
      const rows = [];
      if (!q && !noAll) rows.push({ key: keyOf('', ''), p: '', wt: '', name: allLabel, n: st.total, all: true });
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
      counts ? h('span', { class: 'picker-n num', text: int(r.n) }) : null)) : h('li', { class: 'picker-empty', role: 'presentation', dataset: { key: 'none' }, text: 'No project matches' }));
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

  /* The head of the four Projects views: the project picker is the page
     title, a line of context under it, and Overview / Sessions / Work / Docs
     as tabs that keep the project. opts.worktrees (Sessions) folds each
     project's worktrees under it in the picker; picking one filters by it.
     Built once per view; set() patches it. */
  function projectHead(view, opts) {
    opts = opts || {};
    const wts = !!opts.worktrees;
    const picker = projectPicker({ label: 'Project', allLabel: 'All projects', withWorktrees: wts, noAll: !!opts.noAll, counts: opts.counts !== false, title: true,
      onPick: (p, wt) => { setScope(p); if (wts) { S.ui.wt = (p && wt) || ''; S.ui.sel = -1; } location.hash = areaHash(view, p); } });
    const sub = h('div', { class: 'sub' });
    const tools = h('div', { class: 'page-tools' });
    const nav = h('nav', { class: 'dtabs area-tabs', 'aria-label': 'Project views' });
    const el = h('div', { class: 'area-head' }, h('div', { class: 'page-head' }, h('div', { class: 'area-id' }, h('h1', { class: 'area-title' }, picker.el), sub), tools), nav);
    function renderNav(p) {
      P.into(nav, AREA.map(([k, label]) => {
        const n = k === 'plans' && PL ? PL.count(p) : null;
        return h('a', { href: areaHash(k, p), dataset: { key: k }, 'aria-current': k === view ? 'page' : null }, label, n ? h('span', { class: 'n', text: int(n) }) : null);
      }));
    }
    renderNav(S.ui.scope);
    return { el, sub, tools, picker, nav: renderNav,
      set(items, value, total) { picker.set(items, value, total); renderNav(value.p); } };
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
    { k: 'tokens', label: 'New tokens', r: true },
    { k: 'cost', label: 'Cost', r: true },
    { k: 'errors', label: 'Errors', r: true },
    { k: 'guard', label: 'Guard', r: true },
  ];
  /* The Projects scope (and the worktree picked with it) decides which sessions
     the page is about; the filter row narrows those. */
  const inScope = (r) => (!S.ui.scope || r.project === S.ui.scope) && (!S.ui.wt || r.worktree === S.ui.wt);
  function filteredSessions() {
    const d = S.data.sessions;
    if (!d) return [];
    const q = S.ui.q.trim().toLowerCase();
    let rows = d.sessions.filter((r) => inScope(r) && (!S.ui.family || r.family === S.ui.family) && (!S.ui.errorsOnly || r.errors > 0 || r.guard > 0));
    if (q) rows = rows.filter((r) => [r.title, r.first_prompt, r.project, r.branch, r.cwd, r.id, r.worktree, r.models.join(' ')].join(' ').toLowerCase().includes(q));
    const c = COLS.find((x) => x.k === S.ui.sort.k) || COLS[2];
    const get = c.sort || ((r) => r[c.k] || 0);
    rows = rows.slice().sort((a, b) => { const x = get(a), y = get(b); return (x < y ? -1 : x > y ? 1 : 0) * S.ui.sort.d; });
    return rows;
  }
  /* The Sessions tab of the Projects area (#/sessions?p=&wt=): every session
     in the window for the picked project, searchable, filterable and sortable. */
  views.sessions = {
    mount(root) {
      this.head = projectHead('sessions', { worktrees: true });
      this.sub = this.head.sub;
      const search = h('input', { type: 'search', placeholder: 'Search titles, prompts, branches', 'aria-label': 'Search sessions', value: S.ui.q, id: 'q',
        oninput: (e) => { S.ui.q = e.target.value; S.ui.sel = -1; this.table(); },
        onkeydown: (e) => { if (e.key === 'Escape') { e.target.value = ''; S.ui.q = ''; this.table(); e.target.blur(); } if (e.key === 'Enter' || e.key === 'ArrowDown') { e.target.blur(); S.ui.sel = 0; this.table(); } } });
      const icon = s('svg', { viewBox: '0 0 16 16', 'aria-hidden': 'true' });
      icon.append(s('circle', { cx: 7, cy: 7, r: 4.5, fill: 'none', stroke: 'currentColor', 'stroke-width': 1.5 }), s('path', { d: 'm10.5 10.5 3 3', stroke: 'currentColor', 'stroke-width': 1.5, 'stroke-linecap': 'round' }));
      this.famSeg = h('div', { class: 'seg', role: 'group', 'aria-label': 'Model' });
      this.errBtn = h('button', { 'aria-pressed': String(S.ui.errorsOnly), type: 'button', onclick: () => { S.ui.errorsOnly = !S.ui.errorsOnly; this.errBtn.setAttribute('aria-pressed', String(S.ui.errorsOnly)); this.table(); } }, 'Errors or guard catches');
      this.summary = h('div', { class: 'summary' });
      root.append(this.head.el,
        h('div', { class: 'filters' }, h('div', { class: 'search' }, icon, search, h('kbd', { text: '/' })), this.famSeg, h('div', { class: 'seg' }, this.errBtn), h('div', { class: 'spacer' }), this.summary),
        this.wrap = h('div', {}));
    },
    update() {
      const d = S.data.sessions;
      this.head.set(d ? d.projects || [] : [], { p: S.ui.scope, wt: S.ui.wt }, d ? d.sessions.length : 0);
      if (!d) {
        if (S.lastFail) { const e = errorBox({ message: S.lastFail }, 'sessions'); e.dataset.key = 'error'; P.into(this.wrap, e); }
        else if (!this.wrap.firstChild) this.wrap.append(skeleton('table'));
        return;
      }
      const n = d.sessions.filter(inScope).length;
      const where = S.ui.scope ? 'in ' + S.ui.scope + (S.ui.wt ? ' › ' + S.ui.wt : '') : 'across ' + (S.meta && S.meta.project ? 'projects matching ' + S.meta.project : 'every project');
      P.setText(this.sub, plural(n, 'session') + ' ' + where + ', last ' + (d.days === 1 ? 'day' : d.days + ' days') + '. Cost and new tokens include subagents; cache reads are left out of the token counts.');
      const fams = FAMILIES.filter((f) => f === S.ui.family || d.sessions.some((r) => inScope(r) && r.family === f));   // the models of this scope
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
      P.into(this.summary, h('b', { text: int(rows.length) }), rows.length === 1 ? ' session  ' : ' sessions  ', h('b', { text: money(tot.cost) }), ' est.  ', h('b', { text: compact(tot.tok) }), ' new tokens  ', h('b', { text: int(tot.tools) }), ' tool calls');
      if (!d.sessions.length) {
        P.into(this.wrap, h('div', { class: 'empty', dataset: { key: 'empty' } }, h('h2', { text: 'No sessions in this window' }),
          h('p', {}, 'cc-dash reads transcripts from ', h('code', { text: (S.meta ? S.meta.root : '~/.claude') + '/projects' }), '. Start a Claude Code session' + (canWiden() ? ', or pick a longer window in the header.' : '.'))));
        return;
      }
      if (!d.sessions.some(inScope)) {
        const p = S.ui.scope, wt = S.ui.wt;
        P.into(this.wrap, h('div', { class: 'empty', dataset: { key: 'noscope' } }, h('h2', { text: 'No sessions in ' + (wt ? p + ' › ' + wt : p) + ' in this window' }),
          h('p', {}, 'Nothing in the last ' + (d.days === 1 ? 'day' : d.days + ' days') + ' matches this ' + (wt ? 'worktree' : 'project') + '. ',
            h('a', { href: wt ? viewHash('sessions', p) : viewHash('sessions', ''), class: 'link-u', text: wt ? 'Show all of ' + p : 'Show all projects' }), '.')));
        return;
      }
      if (!rows.length) {
        // The search, model and errors filters go; the project and worktree in the title stay.
        P.into(this.wrap, h('div', { class: 'empty', dataset: { key: 'nomatch' } }, h('h2', { text: 'No sessions match' }), h('p', { text: 'Nothing matches the search and filters above.' }),
          h('button', { class: 'btn', type: 'button', text: 'Clear filters', onclick: () => { S.ui.q = ''; S.ui.family = ''; S.ui.errorsOnly = false; S.ui.sel = -1; mount(); } })));
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
        h('td', {}, r.state ? h('span', { class: 'g s-' + (r.state === 'working' ? 'working' : r.state === 'waiting' ? 'turn' : 'idle'), title: r.state === 'working' ? 'Working' : r.state === 'waiting' ? 'Your turn' : 'Idle, written in the last 10 minutes' }) : null),
        h('td', {}, h('div', { class: 't-title ellipsis', text: r.title, title: r.first_prompt || r.title }),
          h('div', { class: 't-meta' }, h('span', { text: r.project }), r.worktree ? h('span', { class: 'tag', text: r.worktree }) : null, showBranch(r) ? h('span', { class: 'branch ellipsis', text: r.branch }) : null, h('span', { class: 'faint nowrap', text: 'started ' + dateTime(r.start) }))),
        h('td', { class: 'r muted', text: ago(r.last_write), title: dateTime(r.last_write) }),
        h('td', { class: 'r', text: dur(r.active), title: 'Active time, pauses over 30 minutes left out. Wall clock: ' + dur(r.duration) }),
        h('td', { class: 'nowrap ellipsis' }, r.model ? modelTag(r.model, r.family) : h('span', { class: 'faint', text: 'none' }), r.models.length > 1 ? h('span', { class: 'faint', text: ' +' + (r.models.length - 1), title: r.models.join(', ') }) : null),
        z(r.prompts), z(r.tool_calls_all), z(r.subagents), z(r.tokens, compact(r.tokens)),
        h('td', { class: 'r', text: money(r.cost), title: r.unpriced_tokens ? compact(r.unpriced_tokens) + ' tokens on models without a price' : null }),
        h('td', { class: 'r' + (r.errors ? ' bad' : ' zero'), text: int(r.errors), title: r.errors ? int(r.tool_errors) + ' tool errors, ' + int(r.api_errors) + ' API errors' : null }),
        h('td', { class: 'r' + (r.guard ? '' : ' zero'), text: int(r.guard), title: r.guard ? `${r.guard_kinds.deny} blocked, ${r.guard_kinds.ask} asked, ${r.guard_kinds.warn} warned` : null }))));
      const widths = ['30px', null, '92px', '76px', '128px', '60px', '84px', '84px', '96px', '76px', '62px', '58px'];
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
    { k: 'tokens', label: 'New tokens', r: true, w: '96px' },
    { k: 'tool_calls', label: 'Tool calls', r: true, w: '84px' },
    { k: 'error_rate', label: 'Error rate', r: true, w: '84px' },
    { k: 'subagents', label: 'Subagents', r: true, w: '84px' },
    { k: 'mix', label: 'Model mix', w: '120px' },
    { k: 'guard', label: 'Guard', r: true, w: '64px' },
    { k: 'last_active', label: 'Last active', r: true, w: '96px' },
  ];
  views.overview = {
    mount(root) {
      this.head = projectHead('overview');
      this.sub = this.head.sub;
      root.append(this.head.el, this.body = h('div', {}));
      this.sig = null;
      this.entered = false;
    },
    update() {
      const d = S.data.overview;
      const list = d ? d.project_list : [];
      this.head.set(list.map((p) => ({ project: p.project, sessions: p.sessions, worktrees: [], worktrees_n: p.worktrees })), { p: S.ui.scope, wt: '' },
        list.reduce((a, p) => a + p.sessions, 0));
      if (!d) {
        if (S.err.overview) P.into(this.body, errorBox(S.err.overview, 'the overview'));
        else if (!this.body.firstChild) this.body.append(skeleton());
        return;
      }
      P.attr(this.body, 'class', S.data.overviewScope !== S.ui.scope ? 'stale' : null);
      P.attr(this.body, 'aria-busy', S.data.overviewScope !== S.ui.scope ? 'true' : null);
      const sig = JSON.stringify([d.scope, d.totals, d.hours, d.projects, d.guard, d.sub_types, S.ui.psort, [...S.ui.pexp], S.ui.act, theme, PL && PL.sig(), S.data.routing, S.data.routingScope]);
      if (sig === this.sig) return;
      this.sig = sig;
      const scope = d.scope.project;
      P.setText(this.sub, (scope ? 'Cost, activity and models in ' + scope : 'Cost, activity and models across ' + (S.meta && S.meta.project ? 'projects matching ' + S.meta.project : 'every project'))
        + ', last ' + (d.days === 1 ? 'day' : d.days + ' days') + '. Cost is an estimate from token counts.');
      const first = !this.entered;
      this.entered = true;
      P.into(this.body, h('div', { class: 'stack' + (first ? ' fade-in' : ''), dataset: { key: 'ov' } }, this.render(d)));
    },
    render(d) {
      const t = d.totals;
      const scope = d.scope.project;
      if (!t.sessions) {
        return h('div', { class: 'empty', dataset: { key: 'empty' } }, h('h2', { text: scope ? 'No sessions in ' + scope + ' in this window' : 'No sessions in this window' }),
          scope ? h('p', {}, 'Nothing in the last ' + d.days + ' days matches this project. ', h('a', { href: viewHash('overview', ''), class: 'link-u', text: 'Show all projects' }), '.')
            : h('p', {}, 'Nothing was written under ', h('code', { text: (S.meta ? S.meta.root : '~/.claude') + '/projects' }), ' in the last ' + d.days + ' days.' + (canWiden() ? ' Try a longer window in the header.' : '')));
      }
      const guardTotal = d.guard.deny + d.guard.ask + d.guard.warn;
      const kpis = h('div', { class: 'kpis', dataset: { key: 'kpis' } },
        kpi('Estimated cost', money(t.cost), t.unpriced_tokens ? compact(t.unpriced_tokens) + ' tokens unpriced' : money(t.cost / Math.max(1, t.sessions), true) + ' per session', 'hero'),
        kpi('Sessions', int(t.sessions), t.live ? t.live + ' live now' : plural(t.prompts, 'prompt')),
        kpi('Active time', dur(t.active), dur(t.active / Math.max(1, t.sessions)) + ' per session'),
        kpi('New tokens', compact(t.tokens), t.cache_read ? '+' + compact(t.cache_read) + ' re-read from cache' : 'no cache reads', '', 'Input, cache writes and output. Cache reads (' + int(t.cache_read || 0) + ') are counted apart.'),
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
        h('div', { class: 'panel-b' }, h('div', { class: 'legend legend-above' }, costSeries.map((sr) => h('span', { dataset: { key: sr.k } }, key(sr.fam), sr.name, h('b', { text: money(famCost[sr.k]) })))),
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
      const mixPanel = h('section', { class: 'panel' }, h('div', { class: 'panel-h' }, h('h2', { text: 'Which model did the work' }), h('span', { class: 'muted', text: 'share of new tokens' })),
        h('div', { class: 'panel-b' }, h('div', { class: 'mix' }, mixBar('Main sessions', 'main'), mixBar('Subagents', 'sub')),
          h('table', { class: 'compact mix-table' }, h('thead', {}, h('tr', {}, h('th', { text: 'Model' }), h('th', { class: 'r', text: 'Session tok' }), h('th', { class: 'r', text: 'Subagent tok' }), h('th', { class: 'r', text: 'Spawned' }), h('th', { class: 'r', text: 'Cost' }))),
            h('tbody', {}, d.models.map((m) => {
              const sm = d.sub_mix.find((x) => x.family === m.family);
              const cnt = sm && sm.models[m.name];
              return h('tr', { dataset: { key: m.model } }, h('td', {}, modelTag(m.name, m.family)), h('td', { class: 'r', text: compact(m.main_tokens) }), h('td', { class: 'r', text: compact(m.sub_tokens) }), h('td', { class: 'r' + (cnt ? '' : ' zero'), text: int(cnt || 0) }), h('td', { class: 'r', text: money(m.cost) }));
            }))),
          subMixTotal ? h('p', { class: 'insight', text: this.mixInsight(d, subMixTotal) }) : null,
          this.effortInsight(d) ? h('p', { class: 'insight', text: this.effortInsight(d) }) : null));
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
        h('div', { class: 'grid g-3-2', dataset: { key: 'g2' } }, actPanel, toolsPanel),
        PL ? PL.overviewPanel(scope) : null,   // plans.js: "Active plans" in this scope
        RT ? RT.panel(S.data.routing, scope, S.data.routingScope !== scope) : null,   // routing.js: who picked models and effort
        this.projects(d, buckets, hourly),
        h('div', { class: 'grid g-2', dataset: { key: 'g3' } }, typesPanel, relPanel),
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
          r.live ? h('span', { class: 'g s-working', title: plural(r.live, 'session') + ' live now' }) : null)),
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
      return h('section', { class: 'panel p-panel', dataset: { key: 'projects' } },
        h('div', { class: 'panel-h' }, h('div', {}, h('h2', { text: scope ? 'Project and its worktrees' : 'Projects' }),
          h('div', { class: 'muted p-sub', text: scope ? 'Click a row for its sessions.' : plural(n, 'project') + ' in this window, worktrees folded under each. Click a row for its sessions.' })),
        famsSeen.length ? h('div', { class: 'legend', 'aria-label': 'Model mix colours' }, famsSeen.map((f) => h('span', { dataset: { key: f } }, key(f), FAMILY_NAME[f]))) : null),
        h('div', { class: 'panel-b' }, h('table', { class: 'compact p-table' },
          h('colgroup', {}, PCOLS.map((c) => h('col', { style: c.w ? { width: c.w } : null }))),
          h('thead', {}, thead), h('tbody', {}, body))));
    },
    relRow(label, n, note) { return h('tr', { dataset: { key: label } }, h('td', { text: label }), h('td', { class: 'r' + (n ? '' : ' zero'), text: int(n) }), h('td', { class: 'r faint', text: note })); },
    /* Effort per model, from the messages whose transcript recorded one (Claude Code 2.1.29x and later). */
    effortInsight(d) {
      const parts = [];
      let n = 0;
      for (const m of d.models) {
        const e = m.effort || {};
        const tot = Object.values(e).reduce((a, b) => a + b, 0);
        if (!tot) continue;
        n += tot;
        parts.push(m.name + ' ' + Object.entries(e).sort((a, b) => b[1] - a[1]).map(([k, v]) => k + ' ' + pctStr(v, tot)).join(', '));
      }
      return parts.length ? 'Recorded effort: ' + parts.join('; ') + ' (' + plural(n, 'reply', 'replies') + ' that record one; older Claude Code versions record none).' : '';
    },
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
  /* md: render the text as Markdown with the Docs renderer (docs.js, window.ccApp.markdown). */
  function clampText(text, cls, lines, id, ctx, md) {
    const long = text.length > 360 || text.split('\n').length > (lines || 6);
    const open = long && S.ui.unclamp.has(id);
    const mdOf = md && window.ccApp && window.ccApp.markdown;
    const inner = mdOf ? h('div', { class: 'md md-tl' + (open ? '' : ' clamp'), style: lines ? { '-webkit-line-clamp': String(lines) } : null }, mdOf(text))
      : h('div', { class: open ? '' : 'clamp', style: lines ? { '-webkit-line-clamp': String(lines) } : null, text });
    return h('div', {}, h('div', { class: cls }, inner),
      long ? h('button', { class: 'more', type: 'button', text: open ? 'Show less' : 'Show all', onclick: (e) => { e.stopPropagation(); if (S.ui.unclamp.has(id)) S.ui.unclamp.delete(id); else S.ui.unclamp.add(id); ctx.redraw(); } }) : null);
  }
  function tlItem(it, ctx) {
    const e = it.e;
    switch (it.type) {
      case 'prompt': return h('div', { class: 'prompt' }, h('div', { class: 'who', text: ctx.sub ? 'Task from the parent agent' : 'You' }), clampText(e.text, 'bubble', 6, it.key, ctx));
      case 'text': return e.report ? h('div', { class: 'prompt' }, h('div', { class: 'who' }, key(famOf(e.model)), 'Report handed back to the parent'), clampText(e.text, 'bubble', 10, it.key, ctx, true))
        : h('div', {}, h('div', { class: 'who' }, key(famOf(e.model)), e.model || 'Assistant'), clampText(e.text, 'atext', 4, it.key, ctx, true));
      case 'command': return h('div', { class: 'marker' }, h('span', { class: 'mono', text: e.text }));
      case 'notify': return h('div', { class: 'marker', text: 'Background task: ' + e.text });
      case 'peer': return h('div', { class: 'marker', text: 'Message from another session' });
      case 'interrupt': return h('div', { class: 'marker err', text: 'You interrupted the turn' });
      case 'compact': return h('div', { class: 'marker', text: e.text || 'Context compacted' });
      case 'fallback': return h('div', { class: 'marker err', text: e.text });
      case 'effort': return h('div', { class: 'marker', text: e.first ? 'Requests sent at ' + e.effort + ' effort' : 'Effort changed to ' + e.effort, title: 'From the api-request records in the transcript' });
      case 'model': return h('div', { class: 'marker', text: e.text });
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
        blocked ? stPill('blocked', blocked + ' blocked by guard') : null,
        errs ? stPill('failed', errs + ' failed') : null,
        pending ? stPill('working', 'running') : null,
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
    const cols = [['msgs', 'Messages'], ['input', 'Input'], ['output', 'Output'], ['cache_read', 'Cache read'], ['cache_write_5m', 'Cache write 5m'], ['cache_write_1h', 'Cache write 1h'], ['tokens', 'New tokens']];
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
    const est = rows.reduce((a, r) => a + (r.output_estimated || 0), 0);
    return 'Cache reads are ' + pctStr(crTok, tok + crTok) + ' of all tokens moved but about ' + pctStr(cr, all) + ' of the cost, so they stay out of the New tokens count. Output tokens are about ' + pctStr(out, all) + ' of the cost.'
      + (est ? ' About ' + compact(est) + ' output tokens are estimated from replies saved before they finished.' : '');
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
  /* The session header's effort: the main thread's (recorded, or the model default) with
     thinking mode and any change in its tooltip, and "fast mode" once a reply was billed fast. */
  function sessionEffort(e) {
    if (!e) return null;
    const chip = effortTag({ value: e.value, source: e.source, explicit: e.explicit });
    const extra = [];
    if (e.log.length > 1) extra.push('Changed during the session: ' + e.log.map((x) => (x.t ? clock(x.t, false) + ' ' : '') + x.effort).join(', '));
    const cmds = e.commands.filter((c) => /^\/(effort|fast)\b/.test(c.text));
    if (cmds.length) extra.push(cmds.map((c) => c.text).join(', '));
    if (e.thinking_mode) extra.push('Thinking ' + e.thinking_mode.replace('/', ', display '));
    if (chip && extra.length) chip.title += ' ' + extra.join('. ') + '.';
    return [chip, e.fast_msgs ? h('span', { class: 'tag', text: 'fast mode', title: plural(e.fast_msgs, 'reply', 'replies') + ' billed at fast-mode speed' }) : null];
  }
  function guardTab(d) {
    const g = d.guard;
    if (!g.length) {
      return h('div', { class: 'empty' }, h('h2', { text: d.session.blocked ? plural(d.session.blocked, 'tool call') + ' refused by a hook' : 'No guard catches' }),
        h('p', {}, 'Guard decisions come from ', h('code', { text: (d.session.cwd || '<repo>') + '/.git/cc_tool/activity.jsonl' }), ', which the cc_tool hooks write. A project set up without cc-setup has no such log.'),
        d.session.blocked ? h('p', { text: 'The refusals above were read from the transcript itself; open Problems in the timeline to see them.' }) : null);
    }
    const label = { deny: (x) => stPill('blocked', x), ask: (x) => h('span', { class: 'pill warn', text: x }), warn: (x) => h('span', { class: 'pill', text: x }) };
    const word = { deny: 'Blocked', ask: 'Asked first', warn: 'Warned' };
    return h('div', { class: 'table-wrap' }, h('table', {}, h('thead', {}, h('tr', {}, ['Time', 'Decision', 'Hook', 'What', 'Message'].map((x) => h('th', { text: x })))),
      h('tbody', {}, g.map((e) => h('tr', {}, h('td', { class: 'num muted nowrap', text: dateOnly(e.t) + ' ' + clock(e.t) }), h('td', {}, label[e.kind](word[e.kind])),
        h('td', { class: 'mono muted nowrap', text: e.hook }), h('td', { class: 'mono', style: { maxWidth: '360px', overflowWrap: 'anywhere' }, text: e.target }), h('td', { class: 'muted', style: { maxWidth: '480px' }, text: e.message }))))));
  }

  function detailFrame(root) {
    const o = {};
    o.crumb = h('a', { class: 'crumb', href: S.backTo || areaHash('sessions', S.ui.scope) });
    o.head = h('div', { class: 'd-head' });
    o.kpis = h('div', {});
    o.tabs = h('div', { class: 'dtabs', role: 'tablist' });
    o.body = h('div', {});
    o.pill = h('button', { type: 'button', class: 'new-pill', hidden: true, onclick: () => tlToTop(o) });
    root.append(o.crumb, o.head, o.kpis, o.tabs, o.body, o.pill);
    return o;
  }
  /* Control mode: the decision cards and history under the header, and the
     message box docked at the bottom. Both are built once per page and kept
     current by control.js; they stay hidden unless control mode is on and paired. */
  function mountControl(o, sid, aid) {
    if (!window.ccControl || o.ctl) return;
    o.ctl = window.ccControl.detail(sid, aid);
    o.head.after(o.ctl);
    o.comp = window.ccControl.composer(sid, aid);
    o.body.parentNode.append(o.comp);
  }
  function tabs(o, list, redraw) {
    P.into(o.tabs, list.map((t, i) => h('button', { role: 'tab', type: 'button', dataset: { key: t.k }, 'aria-selected': String(S.ui.dtab === t.k), title: 'Key ' + (i + 1), onclick: () => { S.ui.dtab = t.k; redraw(true); } },
      t.label, t.n != null ? h('span', { class: 'n' + (t.bad ? ' bad' : ''), text: int(t.n) }) : null)));
  }
  const crumbFor = (back) => (/^#\/factory/.test(back || '') ? '← Factory' : /^#\/overview/.test(back || '') ? '← Overview' : /^#\/plans?\b/.test(back || '') ? '← Work'
    : /^#\/docs\b/.test(back || '') ? '← Docs' : '← Sessions');
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
      mountControl(o, r.id, null);     // control only on a page that loaded, never beside an error
      const last = d.events[d.events.length - 1];
      const dec = openDecision(r.id);
      const tree = S.data.tree[r.id];
      if (S.ui.dtab === 'agents' && !tree && !this.treeAsked) { this.treeAsked = true; loadTree(r.id).catch(() => { this.treeAsked = false; }); }
      const sig = JSON.stringify([d.events.length, last && last.status, last && last.text && last.text.length, r.cost, r.state, r.subagents, r.guard, S.ui.dtab, S.ui.tl, S.ui.expandAll, S.ui.showAll, S.ui.order, dec && dec.id, PL && PL.sig(),
        d.effort && [d.effort.value, d.effort.source, d.effort.log.length, d.effort.fast_msgs], d.agents && [d.agents.agents, d.agents.max_depth, d.agents.sub_share],
        S.ui.dtab === 'agents' && AG ? AG.sig(tree) : null]);
      if (sig === this.sig && !force) return;
      const first = this.sig == null;
      this.sig = sig;
      this.nEvents = d.events.length;
      if (first) { o.head.replaceChildren(); o.kpis.replaceChildren(); }
      P.into(o.head,
        h('h1', { class: 'd-title', text: r.title }),
        h('div', { class: 'd-meta' }, dec ? decidePill(dec) : statePill(r.state), h('span', { style: { color: 'var(--ink)', fontWeight: 500 }, text: r.project }), r.worktree ? h('span', { class: 'tag', text: r.worktree }) : null,
          showBranch(r) ? h('span', { class: 'branch', text: r.branch }) : null, h('span', { text: dateTime(r.start) + ' to ' + (r.state ? 'now' : (dateOnly(r.end) !== dateOnly(r.start) ? dateTime(r.end) : clock(r.end, false))) + ', ' + dur(r.active) + ' active' }),
          h('span', { style: { gap: '12px' } }, r.models.map((m) => modelTag(m))), sessionEffort(d.effort), PL ? PL.chip(r.id, null) : null,   /* plans.js chip */
          r.entrypoint ? h('span', { class: 'faint', text: r.entrypoint + (r.version ? ' ' + r.version : '') }) : null,
          h('span', { class: 'faint mono ellipsis', text: r.cwd, title: d.path })));
      const all = d.usage.all;
      const ag = d.agents || { agents: r.subagents, max_depth: r.subagents ? 1 : 0, workflows: r.workflows, sub_cost: 0, cost: r.cost };
      P.into(o.kpis, h('div', { class: 'kpis' },
        kpi('Estimated cost', money(r.cost, r.cost < 100), r.unpriced_tokens ? compact(r.unpriced_tokens) + ' tokens unpriced' : r.output_estimated ? 'some output estimated' : 'subagents included', 'hero', r.output_estimated ? 'Subagents included. ' + estNote(r) : null),
        newTokensKpi(r, all), contextKpi(r),
        kpi('Turns', int(r.prompts), plural(r.api_msgs, 'API call')),
        kpi('Tool calls', int(r.tool_calls_all), r.tool_calls_all !== r.tool_calls ? int(r.tool_calls) + ' in this session' : null),
        kpi('Subagents', int(ag.agents), [ag.max_depth > 1 ? ag.max_depth + ' levels deep' : ag.agents ? 'one level' : null, ag.workflows ? plural(ag.workflows, 'workflow') : null].filter(Boolean).join(', ') || null),
        ag.agents ? kpi('Cost in subagents', pctStr(ag.sub_cost, ag.cost), money(ag.sub_cost) + ' of ' + money(ag.cost)) : null,
        kpi('Errors', int(r.errors), r.errors ? int(r.tool_errors) + ' tool, ' + int(r.api_errors) + ' API' : 'none', ''),
        kpi('Guard catches', int(r.guard), r.blocked ? plural(r.blocked, 'refusal') + ' in transcript' : null)),
      all.length > 1 ? h('div', { class: 'cost-share' }, shareBar(all.map((x) => ({ ...x, cost: x.cost || 0 })), 'cost'), h('div', { class: 'legend' }, all.map((x) => h('span', { dataset: { key: x.model } }, key(x.family), x.name, h('b', { text: money(x.cost) }))))) : null);
      this.tabList = [{ k: 'timeline', label: 'Timeline', n: d.events.length }, { k: 'agents', label: 'Agents', n: ag.agents },
        { k: 'tools', label: 'Tools', n: d.tools.length }, { k: 'tokens', label: 'Tokens and cost' }, { k: 'guard', label: 'Guard', n: r.guard, bad: r.guard > 0 }];
      tabs(o, this.tabList, (f) => this.update(f));
      const childFor = (toolId) => { const sp = d.tree.find((x) => x.id === toolId); return sp ? sp.children : []; };
      const ctx = { sid: r.id, childFor, tab: (k) => { S.ui.dtab = k; this.update(true); }, redraw: () => this.update(true) };
      let body;
      if (S.ui.dtab === 'timeline') body = this.tl(d, ctx);
      else if (S.ui.dtab === 'agents') body = AG ? AG.tab(tree, ctx) : null;
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
      if (a.status === 'running' || openDecision(S.route.id, S.route.aid)) mountControl(o, S.route.id, S.route.aid);
      const last = d.events[d.events.length - 1];
      const dec = openDecision(S.route.id, S.route.aid);
      const sig = JSON.stringify([d.events.length, last && last.status, a.status, a.tokens, S.ui.dtab, S.ui.tl, S.ui.expandAll, S.ui.showAll, S.ui.order, dec && dec.id, d.node && d.node.effort]);
      if (sig === this.sig && !force) return;
      const first = this.sig == null;
      this.sig = sig;
      this.nEvents = d.events.length;
      if (first) o.head.replaceChildren();
      P.setText(o.crumb, '← ' + d.session_title);
      P.into(o.head, h('h1', { class: 'd-title', text: a.description || a.agent_type || 'Subagent' }),
        h('div', { class: 'd-meta' }, dec ? decidePill(dec) : subStatus(a.status), h('span', { class: 'tag', text: a.agent_type || 'subagent' }), a.phase ? h('span', { text: 'phase ' + a.phase }) : null,
          a.model ? modelTag(a.model, a.family) : null, d.node ? effortTag(d.node.effort) : null, a.model_requested ? h('span', { class: 'faint', text: 'asked for ' + a.model_requested }) : null,
          a.start ? h('span', { text: dateTime(a.start) + ', ' + dur(a.duration) }) : null, a.background ? h('span', { class: 'faint', text: 'background' }) : null,
          a.worktree ? h('span', { class: 'branch', text: a.worktree }) : null, h('span', { class: 'faint mono ellipsis', text: d.path })));
      P.into(o.kpis, h('div', { class: 'kpis' }, kpi('Estimated cost', money(a.cost, true), a.output_estimated ? 'some output estimated' : null, 'hero', estNote(a) || null), newTokensKpi(a, d.usage), contextKpi(a), kpi('Tool calls', int(a.tool_calls)),
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
    if (document.querySelector('dialog[open]')) return;   // the help, or a review dialog over the floor (control.js), has the keys
    const tag = (e.target.tagName || '').toLowerCase();
    if (tag === 'input' || tag === 'select' || tag === 'textarea') return;
    const k = e.key;
    if (k === '/' && S.route.view === 'docs' && views.docs) { e.preventDefault(); views.docs.focusSearch(); return; }
    if (k === '/') { e.preventDefault(); if (S.route.view !== 'sessions') location.hash = areaHash('sessions', S.ui.scope); setTimeout(() => { const q = $('#q'); if (q) q.focus(); }, 30); return; }
    if (k === '?') { help.showModal(); return; }
    if (k === 't') { cycleTheme(); return; }
    if (k === 'f') { location.hash = FA ? FA.hash() : '#/factory'; return; }
    // o s p d: the Projects area's views, for the project picked there
    if (k === 'o') { location.hash = areaHash('overview', S.ui.scope); return; }
    if (k === 's') { location.hash = areaHash('sessions', S.ui.scope); return; }
    if (k === 'p' && PL) { location.hash = areaHash('plans', S.ui.scope); return; }
    if (k === 'd' && views.docs) { location.hash = areaHash('docs', S.ui.scope); return; }
    if (k === 'Escape' && (S.route.view === 'session' || S.route.view === 'agent')) { location.hash = S.route.view === 'agent' ? '#/s/' + encodeURIComponent(S.route.id) : (S.backTo || areaHash('sessions', S.ui.scope)); return; }
    if (current && current.keys && current.keys(k)) e.preventDefault();
  });

  /* docs.js (the Docs view) builds on these helpers and registers views.docs;
     selfheal.js (the header's Self-heal control) on these and the window's label. */
  window.ccApp = { h, s, api, P, S, views, main, stPill, stGlyph, projectPicker, projectHead, setScope, errorBox, skeleton, chev, ago, dateTime, plural, int, store, tick, syncTabLinks,
    dur, durShort, agoShort, money, clock, winCur, winLabel };

  // plans.js hook: hand it the shared helpers, register its views
  PL = window.ccPlans ? window.ccPlans.init({ h, s, P, api, chev, dur, ago, dateTime, dateOnly, clock, plural, modelTag, key, famOf, glyph, kpi, errorBox, skeleton,
    projectPicker, projectHead, isDecision, setText, chart, stPill, stGlyph, niceStep, showTip, moveTip, hideTip, now: nowS, route: () => S.route, update: () => update(), theme: () => theme, synced: (ok, every, rtt) => syncDone('plans', ok, every, rtt) }) : null;
  if (PL) Object.assign(views, PL.views);
  // routing.js hook: the Overview's Routing panel
  RT = window.ccRouting ? window.ccRouting.init({ h, money, compact, int, plural, pctStr, modelTag, key, FAMILY_NAME }) : null;
  // agents.js hook: the session Agents tab and the effort chip
  AG = window.ccAgents ? window.ccAgents.init({ h, s, P, S, chart, columns, showTip, moveTip, hideTip, money, compact, int, dur, plural, pctStr, modelTag, key,
    FAMILIES, FAMILY_NAME, famOf, subStatus, statePill, stPill, chev, timeAxis, dateOnly, clock, dateTime, nowS }) : null;
  // factory.js hook: the Factory floor (one live graph of projects, sessions, agents, workflows and orders)
  FA = window.ccFactory ? window.ccFactory.init({ h, s, P, S, api, store, money, compact, int, dur, ago, clock, dateTime,
    plural, pctStr, modelTag, key, famOf, FAMILY_NAME, stPill, stGlyph, glyph, effortTag, projectPicker, setScope,
    areaHash, errorBox, skeleton, chev, nowS, chart, showTip, moveTip, hideTip, PL, update: () => update(), route: () => S.route }) : null;
  if (FA) Object.assign(views, FA.views);

  // Pairing (auth.js) finishes before the first request, and every script on the
  // page (docs.js registers a view) has run before the first route.
  const start = () => (document.readyState === 'loading' ? document.addEventListener('DOMContentLoaded', onRoute, { once: true }) : onRoute());
  window.ccAuth.ready.then(start);
})();
