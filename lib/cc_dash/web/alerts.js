/* cc-dash alerts: what your agents are waiting on you for.
   Polls /api/alerts every 1.5 s (from alerts-worker.js, so a background tab
   keeps up), and shows each open alert as a toast, in the bell drawer, in the
   tab title "(2) cc-dash" and as a dot on the favicon. Opt-in desktop
   notifications and an optional soft chime. Every alert text goes into the
   DOM through textContent. Independent of app.js: it only sets location.hash.
   Other views can read the latest payload from window.ccDashAlerts or listen
   for the "cc-dash:alerts" event on document. */
'use strict';
(() => {
  const POLL_MS = 1500;
  const MAX_TOASTS = 4;
  const maxToasts = () => (window.matchMedia && matchMedia('(max-width: 560px)').matches ? 1 : MAX_TOASTS);   // phones: one toast, the bell has the rest
  const SOFT_KINDS = { idle: 12000 };          // toasts that fold into the drawer after a while
  // "Needs a decision" (amber) is only a permission, question or plan; an idle session is "Your turn" (neutral).
  const KIND_LABEL = { permission: 'Needs a decision', question: 'Needs a decision', plan: 'Needs a decision', idle: 'Your turn', error: 'Failed', other: 'Needs you' };
  // The dashboard's status language (app.css "One status language").
  const KIND_STATE = { permission: 'decide', question: 'decide', plan: 'decide', idle: 'turn', error: 'failed', other: 'stalled' };
  const ctl = () => (window.ccControl && window.ccControl.on() ? window.ccControl : null);
  const pref = {
    get(k) { try { return localStorage.getItem('cc-dash-alerts-' + k); } catch (e) { return null; } },
    set(k, v) { try { localStorage.setItem('cc-dash-alerts-' + k, v); } catch (e) { /* private mode */ } },
  };

  // ── tiny DOM helper (text only, never innerHTML) ─────────────────────
  function el(tag, attrs, ...kids) {
    const n = document.createElement(tag);
    for (const k in attrs || {}) {
      const v = attrs[k];
      if (v == null || v === false) continue;
      if (k === 'class') n.className = v;
      else if (k === 'text') n.textContent = v;
      else if (k.startsWith('on')) n.addEventListener(k.slice(2), v);
      else n.setAttribute(k, v === true ? '' : v);
    }
    for (const c of kids.flat()) if (c != null && c !== false) n.append(typeof c === 'string' ? document.createTextNode(c) : c);
    return n;
  }
  const SVGNS = 'http://www.w3.org/2000/svg';
  function icon(paths, size = 14) {
    const s = document.createElementNS(SVGNS, 'svg');
    s.setAttribute('viewBox', '0 0 16 16'); s.setAttribute('width', size); s.setAttribute('height', size);
    s.setAttribute('aria-hidden', 'true'); s.setAttribute('fill', 'none'); s.setAttribute('stroke', 'currentColor');
    s.setAttribute('stroke-width', '1.5'); s.setAttribute('stroke-linecap', 'round'); s.setAttribute('stroke-linejoin', 'round');
    for (const d of paths) { const p = document.createElementNS(SVGNS, 'path'); p.setAttribute('d', d); s.append(p); }
    return s;
  }
  const BELL = ['M4 11.5V7a4 4 0 0 1 8 0v4.5l1 1.5H3z', 'M6.5 14.5a1.6 1.6 0 0 0 3 0'];
  const CLOSE = ['M4.5 4.5l7 7M11.5 4.5l-7 7'];

  // ── formatting ───────────────────────────────────────────────────────
  function waited(sec) {
    sec = Math.max(0, Math.round(sec));
    if (sec < 60) return sec + 's';
    const m = Math.floor(sec / 60);
    if (m < 60) return m + 'm ' + String(sec % 60).padStart(2, '0') + 's';
    const h = Math.floor(m / 60);
    return h + 'h ' + String(m % 60).padStart(2, '0') + 'm';
  }
  const who = (a) => a.project + (a.agent_id ? ' · ' + (a.agent_label || 'subagent') : '');
  // Keep the hash's ?p=…&wt=… project filter, as the rest of the page does.
  const keepQuery = () => { const i = location.hash.indexOf('?'); return i >= 0 ? location.hash.slice(i) : ''; };
  const href = (a) => '#/s/' + encodeURIComponent(a.session) + (a.agent_id ? '/a/' + encodeURIComponent(a.agent_id) : '') + keepQuery();

  // ── state ────────────────────────────────────────────────────────────
  const A = { list: [], byId: new Map(), first: true, offsetMs: 0, ok: true, desktop: pref.get('desktop') === 'on', sound: pref.get('sound') === 'on' };
  const toasts = new Map();      // id -> {node, timer}
  const dismissed = new Set();   // toasts closed by hand (the alert stays in the drawer)
  const notes = new Map();       // id -> Notification
  const ageNodes = new Set();    // nodes with data-since that tick every second
  const drawerCards = new Map(); // id -> drawer card node

  // ── chrome: bell, drawer, toast stack ────────────────────────────────
  const bellCount = el('span', { class: 'al-bell-count', hidden: true });
  const bell = el('button', { class: 'icon-btn al-bell', id: 'alerts-btn', type: 'button', 'aria-label': 'Alerts: none open', title: 'Alerts (a)', 'aria-expanded': 'false', 'aria-controls': 'alert-drawer', onclick: () => toggle() }, icon(BELL), bellCount);
  const right = document.querySelector('.top-right');
  if (right) right.insertBefore(bell, document.getElementById('theme') || null);

  const stack = el('div', { class: 'al-stack', role: 'region', 'aria-label': 'New alerts', 'aria-live': 'polite' });
  const list = el('div', { class: 'al-list' });
  const deskBtn = el('button', { class: 'btn al-opt', type: 'button', onclick: desktopClick });
  const soundBtn = el('button', { class: 'btn al-opt', type: 'button', 'aria-pressed': 'false', onclick: soundClick });
  const status = el('p', { class: 'al-status', role: 'status' });
  const drawer = el('aside', { class: 'al-drawer', id: 'alert-drawer', role: 'dialog', 'aria-label': 'Alerts', 'aria-modal': 'false', hidden: true, tabindex: '-1' },
    el('header', { class: 'al-head' },
      el('h2', { class: 'al-h' }, 'Needs you ', el('span', { class: 'count al-head-count' })),
      el('button', { class: 'icon-btn', type: 'button', 'aria-label': 'Close alerts', title: 'Close (Esc)', onclick: () => toggle(false) }, icon(CLOSE))),
    list,
    el('footer', { class: 'al-foot' }, el('div', { class: 'al-opts' }, deskBtn, soundBtn), status));
  const scrim = el('div', { class: 'al-scrim', hidden: true, onclick: () => toggle(false) });
  document.body.append(stack, scrim, drawer);

  let lastFocus = null;
  window.addEventListener('hashchange', () => toggle(false));   // the drawer belongs to the page you opened it on
  function toggle(on) {
    const open = on == null ? drawer.hidden : on;
    if (open === !drawer.hidden) return;
    drawer.hidden = !open; scrim.hidden = !open;
    bell.setAttribute('aria-expanded', String(open));
    document.documentElement.classList.toggle('al-drawer-open', open);
    if (open) { lastFocus = document.activeElement; renderDrawer(); drawer.focus(); }
    else if (lastFocus && lastFocus.focus) lastFocus.focus();
  }

  // Keys: "a" toggles the drawer; while it is open, keys stay inside it. A modal dialog (help, a
  // decision's review) keeps its keys to itself.
  window.addEventListener('keydown', (e) => {
    if (e.metaKey || e.ctrlKey || e.altKey || document.querySelector('dialog[open]')) return;
    const tag = (e.target.tagName || '').toLowerCase();
    if (!drawer.hidden) {
      if (e.key === 'Escape') { e.preventDefault(); toggle(false); }
      else if (e.key === 'a' && !['input', 'textarea', 'select'].includes(tag)) toggle(false);
      if (e.key !== 'Tab') e.stopPropagation();
      return;
    }
    if (e.key === 'a' && !['input', 'select', 'textarea'].includes(tag)) { e.stopPropagation(); toggle(true); }
  }, true);

  function go(a) {
    toggle(false);
    location.hash = href(a);
  }

  // ── rendering ────────────────────────────────────────────────────────
  function card(a, inToast) {
    const age = el('span', { class: 'al-age num', 'data-since': String(a.since_ts) });
    age.textContent = 'waiting ' + waited(Date.now() / 1000 - A.offsetMs / 1000 - a.since_ts);
    ageNodes.add(age);
    const body = [
      el('div', { class: 'al-row1' },
        el('span', { class: 'pill s-' + (KIND_STATE[a.kind] || 'stalled') }, el('span', { class: 'g', 'aria-hidden': 'true' }), KIND_LABEL[a.kind] || 'Needs you'),
        el('span', { class: 'al-who ellipsis', text: who(a), title: (a.cwd || '') + (a.session_title ? '\n' + a.session_title : '') }),
        age),
      el('div', { class: 'al-title', text: a.title }),
      // A decision is read and made on the request's own card (control.js) below, never on this
      // line: alert text comes from attention.jsonl, which anything running as you can append to.
      a.message && !decides(a) ? el('div', { class: 'al-msg' + (a.tool === 'Bash' ? ' mono' : ''), text: a.message }) : null,
    ];
    if (a.source === 'transcript') body.push(el('div', { class: 'al-src faint', text: 'From the transcript, no cc_tool hook in this project' }));
    const open = el('a', { class: 'al-open', href: href(a), 'aria-label': 'Open session: ' + a.title + ', ' + who(a), onclick: (e) => { e.preventDefault(); go(a); } }, body);
    const n = el('div', { class: 'al-card k-' + a.kind + (inToast ? ' al-toast' : ''), 'data-id': a.id }, open);
    addControls(n, a, inToast);
    if (inToast) {
      n.append(el('button', { class: 'al-x', type: 'button', 'aria-label': 'Dismiss (stays in the alerts list)', title: 'Dismiss', onclick: () => { dismissed.add(a.id); dropToast(a.id); } }, icon(CLOSE, 12)));
    }
    return n;
  }

  /* Control mode: the decision card under a permission, question or plan, or a
     Message button on a "Your turn" card. Added late when the request id or
     pairing arrives after the card was first drawn. */
  const decides = (a) => { const c = ctl(); return !!(c && a.request && c.isDecision(a.kind)); };
  function addControls(n, a, inToast) {
    const c = ctl();
    if (!c || n.__ctlDone) return;
    if (a.request && c.isDecision(a.kind)) {
      const msg = n.querySelector('.al-msg');      // drawn before the request was known: the card replaces it
      if (msg) msg.remove();
      n.append(c.slot(a.request, { compact: inToast }));
      n.__ctlDone = true;
    } else if (a.kind === 'idle') {
      n.append(el('div', { class: 'ctl-slot' }, el('div', { class: 'ctl-acts al-turn' },
        el('button', { class: 'btn ctl-mini', type: 'button', 'data-ctl-msg': a.session, 'data-ctl-agent': a.agent_id || '', text: 'Message…' }))));
      n.__ctlDone = true;
    }
  }

  /* The page of the session (or subagent) an alert belongs to already shows its
     decision card at the top, so no toast repeats it there. */
  function onItsPage(a) {
    const m = location.hash.match(/^#\/s\/([^/?]+)(?:\/a\/([^/?]+))?/);
    if (!m) return false;
    const sid = decodeURIComponent(m[1]), aid = m[2] ? decodeURIComponent(m[2]) : null;
    return sid === a.session && (!aid || (a.agent_id || null) === aid);   // a session page shows its subagents' cards too
  }
  window.addEventListener('hashchange', () => { for (const a of A.list) if (toasts.has(a.id) && onItsPage(a)) dropToast(a.id, true); });
  function addToast(a) {
    if (toasts.has(a.id) || dismissed.has(a.id) || onItsPage(a)) return;
    const node = card(a, true);
    stack.prepend(node);
    const t = { node, timer: null };
    if (SOFT_KINDS[a.kind]) t.timer = setTimeout(() => dropToast(a.id, true), SOFT_KINDS[a.kind]);
    toasts.set(a.id, t);
    const ids = [...toasts.keys()];                 // oldest first
    for (const id of ids.slice(0, Math.max(0, ids.length - maxToasts()))) dropToast(id);
  }

  // quiet: a timed fold into the drawer, not a change in the data, so no exit animation.
  function dropToast(id, quiet, alert) {
    const t = toasts.get(id);
    if (!t) return;
    // A decision just made here: keep the toast a moment so its result ("Applied") shows.
    const c = ctl();
    const wait = !quiet && alert && alert.request && c ? c.lingerMs(alert.request) : 0;
    if (wait > 0 && !t.lingering) {
      t.lingering = true;
      t.node.classList.add('al-linger');
      setTimeout(() => { t.lingering = false; dropToast(id, quiet); }, Math.min(wait, 4000));
      return;
    }
    if (t.lingering) return;
    toasts.delete(id);
    clearTimeout(t.timer);
    const n = t.node;
    for (const x of n.querySelectorAll('[data-since]')) ageNodes.delete(x);
    if (quiet) { n.remove(); return; }
    n.classList.add('al-leave');
    const done = () => n.remove();
    n.addEventListener('animationend', done, { once: true });
    setTimeout(done, 400);
  }

  function renderDrawer() {
    if (drawer.hidden) return;
    const items = [...A.list].sort((x, y) => x.since_ts - y.since_ts);   // longest waiting first
    drawer.querySelector('.al-head-count').textContent = items.length ? String(items.length) : '';
    if (!items.length) {
      drawerCards.clear();
      list.replaceChildren(el('div', { class: 'al-empty' },
        el('p', { class: 'al-empty-t', text: 'Nothing needs you right now.' }),
        el('p', { class: 'faint', text: 'Permission prompts, questions, plans waiting for approval and idle sessions show up here within a few seconds.' })));
    } else {
      // Keyed by alert id: cards already shown stay the same nodes (no blink, focus kept).
      const kids = items.map((a) => {
        let n = drawerCards.get(a.id);
        if (!n) { n = card(a, false); drawerCards.set(a.id, n); }
        for (const x of n.querySelectorAll('[data-since]')) ageNodes.add(x);
        return n;
      });
      for (const id of [...drawerCards.keys()]) if (!A.byId.has(id)) drawerCards.delete(id);
      const empty = list.querySelector('.al-empty');
      if (empty) empty.remove();
      if (window.ccDom) window.ccDom.reconcile(list, kids); else list.replaceChildren(...kids);
    }
    renderOpts();
  }

  function renderOpts() {
    const sup = 'Notification' in window;
    const perm = sup ? Notification.permission : 'unsupported';
    deskBtn.disabled = !sup || perm === 'denied';
    deskBtn.setAttribute('aria-pressed', String(A.desktop && perm === 'granted'));
    deskBtn.textContent = !sup ? 'Desktop notifications not supported'
      : perm === 'denied' ? 'Desktop notifications blocked by the browser'
        : A.desktop && perm === 'granted' ? 'Desktop notifications: on' : 'Turn on desktop notifications';
    soundBtn.setAttribute('aria-pressed', String(A.sound));
    soundBtn.textContent = A.sound ? 'Sound: on' : 'Sound: off';
    status.textContent = A.ok ? '' : 'cc-dash stopped responding; alerts may be out of date.';
  }

  // ── title badge and favicon dot ──────────────────────────────────────
  const iconLink = document.querySelector('link[rel="icon"]');
  const baseIcon = iconLink ? iconLink.getAttribute('href') : '';
  const DOT_ICON = 'data:image/svg+xml,' + encodeURIComponent(
    "<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 16 16'><rect x='1' y='1' width='14' height='14' rx='3.5' fill='#171715'/>" +
    "<rect x='4' y='9' width='2' height='3' rx='.6' fill='#fcfcfb'/><rect x='7' y='6' width='2' height='6' rx='.6' fill='#fcfcfb'/>" +
    "<rect x='10' y='4' width='2' height='8' rx='.6' fill='#fcfcfb'/><circle cx='12.6' cy='3.4' r='3.4' fill='#e8a10c' stroke='#fcfcfb' stroke-width='1.2'/></svg>");
  function badge(n) {
    const base = document.title.replace(/^\(\d+\)\s+/, '');
    const want = n ? '(' + n + ') ' + base : base;
    if (document.title !== want) document.title = want;
    if (iconLink) {
      const h = n ? DOT_ICON : baseIcon;
      if (iconLink.getAttribute('href') !== h) iconLink.setAttribute('href', h);
    }
    bellCount.hidden = !n;
    bellCount.textContent = n > 99 ? '99+' : String(n);
    bell.classList.toggle('on', n > 0);
    bell.setAttribute('aria-label', n ? 'Alerts: ' + n + ' open' : 'Alerts: none open');
  }

  // ── desktop notifications (opt-in) and sound ─────────────────────────
  async function desktopClick() {
    if (!('Notification' in window)) return;
    if (A.desktop && Notification.permission === 'granted') {
      A.desktop = false; pref.set('desktop', 'off');
      for (const n of notes.values()) n.close();
      notes.clear();
    } else {
      let p = Notification.permission;
      if (p === 'default') { try { p = await Notification.requestPermission(); } catch (e) { p = 'denied'; } }
      A.desktop = p === 'granted';
      pref.set('desktop', A.desktop ? 'on' : 'off');
    }
    renderOpts();
  }

  let audio = null;
  function soundClick() {
    A.sound = !A.sound;
    pref.set('sound', A.sound ? 'on' : 'off');
    if (A.sound) { ensureAudio(); chime(); }
    renderOpts();
  }
  function ensureAudio() {
    const AC = window.AudioContext || window.webkitAudioContext;
    if (!AC) return null;
    if (!audio) { try { audio = new AC(); } catch (e) { audio = null; } }
    if (audio && audio.state === 'suspended') audio.resume().catch(() => {});
    return audio;
  }
  function chime() {
    const ac = ensureAudio();
    if (!ac) return;
    const t = ac.currentTime + 0.02;
    [[659.25, 0], [880, 0.12]].forEach(([f, dt]) => {
      const o = ac.createOscillator(), g = ac.createGain();
      o.type = 'sine'; o.frequency.value = f;
      g.gain.setValueAtTime(0, t + dt);
      g.gain.linearRampToValueAtTime(0.05, t + dt + 0.02);
      g.gain.exponentialRampToValueAtTime(0.0001, t + dt + 0.35);
      o.connect(g); g.connect(ac.destination);
      o.start(t + dt); o.stop(t + dt + 0.4);
    });
  }
  // Browsers only start audio after a gesture; any click on the page counts.
  document.addEventListener('pointerdown', () => { if (A.sound) ensureAudio(); }, { once: true, capture: true });

  function desktop(a) {
    if (!A.desktop || !('Notification' in window) || Notification.permission !== 'granted') return;
    if (!document.hidden && document.hasFocus()) return;   // the toast already shows it
    try {
      const n = new Notification(a.title + ' · ' + a.project, {
        body: (a.agent_id ? (a.agent_label || 'subagent') + ': ' : '') + (a.message || ''),
        tag: 'cc-dash-' + a.id, silent: !A.sound,
      });
      n.onclick = () => { window.focus(); go(a); n.close(); };
      notes.set(a.id, n);
    } catch (e) { /* some platforms only allow notifications from a service worker */ }
  }

  // ── applying a payload ───────────────────────────────────────────────
  function apply(data, at) {
    A.ok = true;
    const now = Date.parse(data.now);
    if (isFinite(now)) A.offsetMs = (at || Date.now()) - now;     // client clock minus server clock
    const next = new Map();
    for (const a of data.alerts || []) if (a && typeof a.id === 'string') next.set(a.id, a);
    const fresh = [...next.values()].filter((a) => !A.byId.has(a.id));
    const gone = [...A.byId.keys()].filter((id) => !next.has(id));
    const before = A.byId;
    A.list = [...next.values()];
    A.byId = next;
    for (const id of gone) {
      dropToast(id, false, before.get(id));
      dismissed.delete(id);
      const n = notes.get(id);
      if (n) { n.close(); notes.delete(id); }
    }
    // On the first load show what is already open, but do not ring or notify for it.
    const show = A.first ? fresh.sort((x, y) => y.since_ts - x.since_ts).slice(0, maxToasts()).reverse() : fresh.sort((x, y) => x.since_ts - y.since_ts);
    for (const a of show) addToast(a);
    if (!A.first && fresh.length) {
      for (const a of fresh) desktop(a);
      if (A.sound && fresh.some((a) => a.kind !== 'idle')) chime();
    }
    A.first = false;
    for (const a of A.list) {          // a request id or pairing that arrived after the card was drawn
      const t = toasts.get(a.id);
      if (t) addControls(t.node, a, true);
      const d = drawerCards.get(a.id);
      if (d) addControls(d, a, false);
    }
    badge(A.list.length);
    if (gone.length || fresh.length) renderDrawer(); else renderOpts();
    window.ccDashAlerts = { alerts: A.list, now: data.now, at: Date.now() };
    document.dispatchEvent(new CustomEvent('cc-dash:alerts', { detail: window.ccDashAlerts }));
  }

  function failed() { A.ok = false; renderOpts(); }

  // Waiting timers tick once a second (throttled in a hidden tab, which is fine: nothing visible).
  setInterval(() => {
    const t = Date.now() / 1000 - A.offsetMs / 1000;
    for (const n of ageNodes) {
      if (!n.isConnected) { ageNodes.delete(n); continue; }
      const v = 'waiting ' + waited(t - Number(n.dataset.since));
      if (window.ccDom) window.ccDom.setText(n, v); else n.textContent = v;
    }
  }, 1000);

  // ── polling: a worker, or page timers when workers are unavailable ───
  let worker = null;
  window.ccAuth.ready.then(() => {
    try {
      worker = new Worker('/alerts-worker.js');
      worker.onmessage = (e) => { const m = e.data || {}; if (m.type === 'data') apply(m.data, m.at); else if (m.type === 'error') failed(); };
      worker.onerror = () => { worker = null; pagePoll(); };
      worker.postMessage({ type: 'interval', ms: POLL_MS });
      worker.postMessage({ type: 'token', token: window.ccAuth.token() || '' });   // workers cannot read localStorage
    } catch (e) { worker = null; pagePoll(); }
  });
  let pageTimer = null;
  function pagePoll() {
    clearTimeout(pageTimer);
    window.ccAuth.fetch('/api/alerts').then((r) => (r.ok ? r.json() : Promise.reject(new Error('HTTP ' + r.status))))
      .then((d) => apply(d, Date.now()), failed)
      .finally(() => { if (!worker) pageTimer = setTimeout(pagePoll, POLL_MS); });
  }
  document.addEventListener('visibilitychange', () => { if (!document.hidden && worker) worker.postMessage({ type: 'now' }); });
  renderOpts();
})();
