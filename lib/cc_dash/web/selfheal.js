/* cc-dash self-heal: the header control beside the window picker and the
   proposals drawer (lib/cc_dash/selfheal.py: GET /api/selfheal, GET
   /api/selfheal/run, POST /api/control/selfheal).

   The control (#selfheal, in the header right after #scope):
     button   #selfheal-btn opens a small popover (#selfheal-pop). Without
              control mode it is disabled, and its title and accessible
              description say "Self-heal needs ccdash --control". Until the
              control code is typed the popover holds control.js's code field
              (ccControl.lockBox); then it says what a run covers (the window:
              since the last completed run, or this page's window for a first
              one; every project in scope; the budget cap; proposals only)
              and Run POSTs. A 409 shows the server's "already going" line.
     status   #selfheal-st: "running · 4m" (a link: the run's session on the
              Factory floor when it is there, else its page), "last: 3d ago"
              or "last: never", and after a failed, timed-out or interrupted
              run the failed glyph and the time, the error in its title. Times
              tick through app.js's 1 s ticker (data-since and data-ago with
              data-fmt="short"), never by a repaint.
     count    #selfheal-n: the last run's proposals; opens the drawer.
     At 900 px and narrower the button is a square with a corner glyph
     (running, failed) and the status and count live in the popover.
   Polls GET /api/selfheal every 60 s, every 3 s while a run is going, at once
   after a POST and when the tab comes back.

   The drawer (#selfheal-drawer: the alerts drawer's frame, wider for diffs):
   one run's proposals grouped by project (cc_tool's own templates last) then
   severity, each with its title, target, failure mode, evidence (links to
   the sessions), risk, the check's note, the diff (one span per line), Copy
   patch, the apply command and "File as factory order", which only shows the
   cc-factory command to copy. Nothing here runs or applies anything.

   Everything a run wrote is untrusted: every string goes into the DOM through
   textContent (h()'s text and text children), never innerHTML, and nothing
   sets a style attribute (the CSP blocks inline styles). Escape closes the
   popover or the drawer and focus goes back to the button that opened it;
   while either is open the page's single-key shortcuts stay out of it. */
'use strict';
(() => {
  const A = window.ccApp;
  const root = document.getElementById('selfheal');
  if (!A || !root) return;
  const { h, s, P } = A;
  const POLL_IDLE = 60000, POLL_RUN = 3000;
  const RUN_ID_RE = /^self-heal-\d{8}-\d{6}-[0-9a-f]{4}$/;
  const NEEDS = 'Self-heal needs ccdash --control';
  const SEV = ['high', 'medium', 'low'];
  const SEV_WORD = { high: 'High', medium: 'Medium', low: 'Low' };
  const FAILED = ['failed', 'timeout', 'interrupted'];
  const FAIL_WORD = { failed: 'Failed', timeout: 'Timed out', interrupted: 'Interrupted' };
  const KIND_WORD = { guard_denial: 'guard denial', tool_error: 'tool error', permission_stall: 'permission stall', api_error: 'API error',
    test_failure: 'test failure', factory_rework: 'factory rework', verify_failure: 'verify failure', user_correction: 'user correction',
    stalled_agent: 'stalled agent', interrupt: 'interrupt' };
  const enc = encodeURIComponent;
  const num = (x) => (typeof x === 'number' && isFinite(x) ? x : null);
  const str = (x) => (typeof x === 'string' ? x : '');
  const rec = (r) => (r && typeof r === 'object' && typeof r.id === 'string' ? r : null);
  const shown = (n) => !!(n && n.isConnected && n.getClientRects().length);
  const unlocked = () => !!(window.ccAuth && window.ccAuth.scope() === 'control');
  const glyph = (state) => h('span', { class: 'g s-' + state, 'aria-hidden': 'true' });
  const nowS = () => Date.now() / 1000;
  const agoEl = (t) => h('span', { class: 'num', dataset: { ago: String(t), fmt: 'short' }, text: A.agoShort(t) });
  const sinceEl = (t) => h('span', { class: 'num', dataset: { since: String(t), fmt: 'short' }, text: A.durShort(nowS() - t) });

  const SH = {
    d: null, gone: false, err: '', key: '',
    open: false, posting: false, msg: '', msgBad: false,
    dId: null, dOpener: null, runs: new Map(), built: null,
  };

  // ── icons ────────────────────────────────────────────────────────────
  function bandage() {   // the narrow header's square: an outline bandage
    const v = s('svg', { class: 'sh-ico', viewBox: '0 0 16 16', 'aria-hidden': 'true' });
    const g = s('g', { transform: 'rotate(-45 8 8)', fill: 'none', stroke: 'currentColor', 'stroke-width': 1.4 });
    g.append(s('rect', { x: 1, y: 4.6, width: 14, height: 6.8, rx: 1.6 }), s('path', { d: 'M5.4 4.6v6.8M10.6 4.6v6.8' }),
      s('path', { d: 'M7.2 7.2v.01M8.8 7.2v.01M7.2 8.8v.01M8.8 8.8v.01', 'stroke-width': 1.5, 'stroke-linecap': 'round' }));
    v.append(g);
    return v;
  }
  function xIcon() {
    const v = s('svg', { viewBox: '0 0 16 16', width: 14, height: 14, 'aria-hidden': 'true' });
    v.append(s('path', { d: 'M4.5 4.5l7 7M11.5 4.5l-7 7', fill: 'none', stroke: 'currentColor', 'stroke-width': 1.5, 'stroke-linecap': 'round' }));
    return v;
  }

  // ── the header control ───────────────────────────────────────────────
  const desc = h('span', { class: 'vh', id: 'selfheal-desc' });
  const badge = h('span', { class: 'sh-badge', 'aria-hidden': 'true', hidden: true }, h('span', { class: 'g' }));
  const btn = h('button', { type: 'button', id: 'selfheal-btn', class: 'sh-btn', 'aria-label': 'Self-heal', 'aria-haspopup': 'dialog', 'aria-expanded': 'false',
    'aria-controls': 'selfheal-pop', 'aria-describedby': 'selfheal-desc', disabled: true, onclick: () => (SH.open ? closePop(true) : openPop()) },
  bandage(), h('span', { class: 'sh-label', text: 'Self-heal' }), badge);
  const st = h('span', { class: 'sh-st', id: 'selfheal-st' });
  const nBtn = h('button', { type: 'button', id: 'selfheal-n', class: 'sh-n', hidden: true, 'aria-controls': 'selfheal-drawer', 'aria-expanded': 'false',
    onclick: () => { const v = derive(); if (v.last && v.last.status === 'done') openDrawer(v.last.id, nBtn); } });
  const live = h('span', { class: 'vh', role: 'status' });

  const popNow = h('div', { class: 'sh-now' });
  const popKv = h('dl', { class: 'sh-kv' });
  const lockSlot = h('div', { class: 'sh-lock' });
  const runBtn = h('button', { type: 'button', id: 'selfheal-run', class: 'btn primary', text: 'Run self-heal', onclick: () => run() });
  const acts = h('div', { class: 'sh-acts' }, runBtn);
  const msg = h('p', { class: 'sh-msg', id: 'selfheal-msg', role: 'status', hidden: true });
  const pop = h('div', { class: 'picker-pop sh-pop', id: 'selfheal-pop', role: 'dialog', 'aria-labelledby': 'selfheal-pop-h', tabindex: '-1', hidden: true },
    h('div', {}, h('h2', { class: 'sh-pop-h', id: 'selfheal-pop-h', text: 'Self-heal' }),
      h('p', { class: 'sh-lede', text: 'One headless Claude run reads the errors of recent sessions and proposes harness fixes as patches.' })),
    popNow, popKv, lockSlot, acts, msg);
  root.replaceChildren(btn, st, nBtn, pop, desc, live);
  // Focus leaving the popover (Tab past its end) closes it, as the window picker does.
  pop.addEventListener('focusout', (e) => { if (SH.open && e.relatedTarget && !root.contains(e.relatedTarget)) closePop(false); });

  function derive() {
    const d = SH.d || {};
    const last = rec(d.last), done = rec(d.last_completed);
    const run = rec(d.running) || (last && last.status === 'running' ? last : null);
    const state = run ? 'running' : !last ? 'never' : last.status === 'done' || FAILED.includes(last.status) ? last.status : 'failed';
    return { d, enabled: !!d.enabled, last, done, run, state };
  }
  const ended = (r) => num(r.ended) || num(r.started);
  function failTitle(r) {
    const t = ended(r);
    return (FAIL_WORD[r.status] || 'Failed') + (t ? ' ' + A.dateTime(t) : '') + (str(r.error) ? ': ' + r.error : '');
  }
  /* The run's session: on the Factory floor when it is there, else its page. */
  function watchLink(r, kids, cls) {
    const sid = str(r.session_id);
    if (!sid) return h('span', { class: cls }, kids);
    return h('a', { class: cls, href: '#/s/' + enc(sid), title: 'Show the run’s session on the Factory floor, or open its page',
      onclick: (e) => { const F = window.ccFactory; if (F && F.focus && F.focus('s:' + sid)) { e.preventDefault(); closePop(false); } } }, kids);
  }
  function sentence(v) {
    if (v.state === 'running') { const t = num(v.run.started); return 'A run is going' + (t ? ', started ' + A.clock(t, false) : '') + '.'; }
    if (v.state === 'never') return 'It has not run yet.';
    const r = v.last, t = ended(r), when = t ? ' ' + A.dateTime(t) : '';
    if (v.state === 'done') return 'Last run' + when + ': ' + A.plural(num(r.proposals) || 0, 'proposal') + '.';
    return failTitle(r) + '.';
  }

  function renderHead() {
    const v = derive();
    root.hidden = SH.gone;
    root.dataset.state = SH.d ? v.state : 'loading';
    root.dataset.control = !SH.d ? 'unknown' : !v.enabled ? 'off' : unlocked() ? 'on' : 'locked';
    btn.disabled = !SH.d || !v.enabled;
    P.attr(btn, 'title', !SH.d ? (SH.err ? 'Self-heal: ' + SH.err : null) : !v.enabled ? NEEDS : 'Self-heal: propose harness fixes from recent errors');
    P.setText(desc, !SH.d ? '' : !v.enabled ? NEEDS + '. ' + sentence(v) : sentence(v));
    P.attr(btn, 'aria-expanded', String(SH.open));
    const mark = v.state === 'running' ? 'working' : FAILED.includes(v.state) ? 'failed' : '';
    badge.hidden = !mark;
    badge.firstChild.className = 'g' + (mark ? ' s-' + mark : '');
    // The status text: patched in place, so the ticking span is never replaced.
    let kids = [];
    if (SH.d) {
      if (v.state === 'running') kids = [watchLink(v.run, [glyph('working'), 'running · ', sinceEl(num(v.run.started) || nowS())], 'sh-watch')];
      else if (v.state === 'never') kids = v.enabled ? ['last: never'] : [];
      else if (v.state === 'done') kids = ['last: ', agoEl(ended(v.last))];
      else kids = [glyph('failed'), h('span', { class: 'vh', text: (FAIL_WORD[v.state] || 'Failed').toLowerCase() + ', ' }), 'last: ', agoEl(ended(v.last))];
    }
    P.into(st, kids);
    P.attr(st, 'title', SH.d && FAILED.includes(v.state) ? failTitle(v.last) : null);
    const n = v.last && v.last.status === 'done' && v.state === 'done' ? num(v.last.proposals) : null;
    nBtn.hidden = n == null;
    if (n != null) {
      P.setText(nBtn, A.plural(n, 'proposal'));
      P.attr(nBtn, 'title', 'Show the proposals of the run ' + (ended(v.last) ? A.dateTime(ended(v.last)) : ''));
    }
    P.attr(nBtn, 'aria-expanded', String(!drawer.hidden && SH.dOpener === nBtn));
  }

  // ── the popover ──────────────────────────────────────────────────────
  function viewBtn(id, label) {
    const b = h('button', { type: 'button', class: 'btn quiet sh-view', dataset: { run: id }, text: label, onclick: () => openDrawer(id, b) });
    return b;
  }
  function nowKids(v) {
    const out = [];
    const prev = (r) => (r && r.status === 'done' ? h('p', { class: 'sh-row sh-prev' },
      h('span', { class: 'sh-row-t' }, 'Previous results: ' + A.plural(num(r.proposals) || 0, 'proposal') + (ended(r) ? ' from ' + A.dateTime(ended(r)) : '')),
      viewBtn(r.id, 'View')) : null);
    if (v.state === 'running') {
      const r = v.run, t = num(r.started) || nowS();
      out.push(h('p', { class: 'sh-row' },
        h('span', { class: 'sh-row-t' }, glyph('working'), 'Running · ', sinceEl(t), ' · ', r.phase === 'collecting' ? 'collecting evidence' : 'analyzing'),
        watchLink(r, 'Watch the run', 'btn quiet sh-view')));
      out.push(prev(v.done));
    } else if (v.state === 'done') {
      const r = v.last, cost = num(r.cost_usd);
      out.push(h('p', { class: 'sh-row' },
        h('span', { class: 'sh-row-t' }, 'Last run ', agoEl(ended(r)), ': ' + A.plural(num(r.proposals) || 0, 'proposal') + (cost != null ? ' · ' + A.money(cost, true) : '')),
        viewBtn(r.id, 'View')));
    } else if (FAILED.includes(v.state)) {
      const r = v.last;
      out.push(h('p', { class: 'sh-row sh-failed', title: failTitle(r) },
        h('span', { class: 'sh-row-t' }, glyph('failed'), 'Last run ' + (r.status === 'timeout' ? 'timed out ' : r.status === 'interrupted' ? 'was interrupted ' : 'failed '), agoEl(ended(r)),
          str(r.error) ? h('span', { class: 'sh-err', text: r.error }) : null)));
      out.push(prev(v.done && v.done.id !== r.id ? v.done : null));
    }
    return out;
  }
  function kv(label, value) { return [h('dt', { text: label }), h('dd', { text: value })]; }
  function kvKids(v) {
    const d = v.d, meta = A.S.meta || {};
    if (v.state === 'running') {
      const r = v.run, w = r.window || {}, ps = Array.isArray(r.projects) ? r.projects.filter((x) => typeof x === 'string') : [];
      return [kv('Window', num(w.since) ? 'Since ' + A.dateTime(w.since) + (w.clamped ? ' (as far back as cc-dash keeps)' : '') : 'This page’s window'),
        kv('Projects', ps.length ? A.plural(ps.length, 'project') + ': ' + ps.join(', ') : 'All projects'),
        kv('Budget', A.money(num(r.budget_usd) || num(d.budget_usd) || 0, true) + ' cap · proposals only, nothing is applied')];
    }
    const since = num(d.next_since);
    const win = since ? 'Since ' + A.dateTime(since) + ' (the last completed run)' : (A.winLabel(A.winCur()) || 'This page’s window') + ' (a first run)';
    return [kv('Window', win), kv('Projects', meta.project ? 'All projects matching ' + meta.project : 'All projects'),
      kv('Budget', A.money(num(d.budget_usd) || 0, true) + ' cap · proposals only, nothing is applied')];
  }
  function renderPop() {
    if (!SH.open) return;
    const v = derive();
    P.into(popNow, nowKids(v));
    P.into(popKv, kvKids(v));
    acts.hidden = lockSlot.hidden = v.state === 'running' || !v.enabled;
    runBtn.disabled = SH.posting;
    P.setText(runBtn, SH.posting ? 'Starting…' : 'Run self-heal');
    msg.hidden = !SH.msg;
    P.setText(msg, SH.msg);
    msg.classList.toggle('bad', SH.msgBad);
  }
  function ensureLock() {
    if (lockSlot.firstChild || !window.ccControl || !window.ccControl.lockBox) return;
    lockSlot.append(window.ccControl.lockBox(true));
  }
  function focusFirst() {
    const code = lockSlot.querySelector('.ctl-code');
    const t = [code, runBtn, popNow.querySelector('a[href], button')].find((x) => shown(x) && !x.disabled) || pop;
    t.focus({ preventScroll: true });
  }
  function openPop() {
    if (SH.open || btn.disabled) return;
    ensureLock();
    SH.open = true; SH.msg = ''; SH.msgBad = false;
    pop.hidden = false;
    renderHead();
    renderPop();
    document.addEventListener('pointerdown', outside, true);
    focusFirst();
  }
  function closePop(refocus) {
    if (!SH.open) return;
    SH.open = false;
    pop.hidden = true;
    document.removeEventListener('pointerdown', outside, true);
    renderHead();
    if (refocus && shown(btn)) btn.focus({ preventScroll: true });
  }
  function outside(e) { if (!root.contains(e.target)) closePop(false); }

  async function run() {
    const v = derive();
    if (SH.posting || !v.enabled || v.state === 'running') return;
    const hadFocus = document.activeElement === runBtn;   // read before the button is disabled (that drops its focus)
    SH.posting = true; SH.msg = '';
    renderPop();
    const days = A.winCur();
    let status = 0, body = null;
    try {
      const r = await window.ccAuth.fetch('/api/control/selfheal', { method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(days > 0 ? { days } : {}) });
      status = r.status;
      try { body = await r.json(); } catch (e) { body = null; }
    } catch (e) { status = 0; }
    SH.posting = false;
    const err = body && typeof body.error === 'string' ? body.error : '';
    if (status === 202 && body && rec(body.run)) {
      SH.d = Object.assign({}, SH.d, { running: body.run, last: body.run });
      SH.msg = '';
    } else if (status === 409) {
      if (body && rec(body.running)) SH.d = Object.assign({}, SH.d, { running: body.running, last: body.running });
      SH.msg = err || 'A self-heal run is already going.'; SH.msgBad = false;
    } else {
      SH.msg = err || (status ? 'Self-heal did not start (HTTP ' + status + ').' : 'cc-dash is not answering.'); SH.msgBad = true;
    }
    render();
    // Keep focus in the popover: back on Run, or, when Run gave way to the running line, on its link.
    if (hadFocus && SH.open && !pop.contains(document.activeElement)) focusFirst();
    poll();
  }

  // ── the drawer ───────────────────────────────────────────────────────
  const dCount = h('span', { class: 'count' });
  const dSub = h('p', { class: 'sh-d-sub' });
  const dList = h('div', { class: 'al-list sh-list' });
  const dFoot = h('p', { class: 'sh-d-foot' });
  const drawer = h('aside', { class: 'al-drawer sh-drawer', id: 'selfheal-drawer', role: 'dialog', 'aria-modal': 'false', 'aria-labelledby': 'selfheal-drawer-h', hidden: true, tabindex: '-1' },
    h('header', { class: 'al-head sh-d-head' },
      h('div', { class: 'sh-d-id' }, h('h2', { class: 'al-h', id: 'selfheal-drawer-h' }, 'Self-heal proposals ', dCount), dSub),
      h('button', { class: 'icon-btn sh-d-x', type: 'button', 'aria-label': 'Close proposals', title: 'Close (Esc)', onclick: () => closeDrawer(true) }, xIcon())),
    dList,
    h('footer', { class: 'al-foot' }, dFoot));
  const scrim = h('div', { class: 'al-scrim', hidden: true, onclick: () => closeDrawer(true) });
  document.body.append(scrim, drawer);

  function openDrawer(id, opener) {
    if (!RUN_ID_RE.test(id)) return;
    closePop(false);
    SH.dOpener = opener && opener !== document.body ? opener : btn;
    if (SH.dId !== id) SH.built = null;
    SH.dId = id;
    drawer.hidden = scrim.hidden = false;
    document.documentElement.classList.add('sh-drawer-open');
    renderDrawer();
    renderHead();
    drawer.focus({ preventScroll: true });
    const got = SH.runs.get(id);
    if (!got || got.err) load(id);
  }
  function closeDrawer(refocus) {
    if (drawer.hidden) return;
    drawer.hidden = scrim.hidden = true;
    document.documentElement.classList.remove('sh-drawer-open');
    const opener = SH.dOpener;
    SH.dOpener = null;
    renderHead();
    if (!refocus) return;
    const to = shown(opener) ? opener : shown(btn) && !btn.disabled ? btn : shown(nBtn) ? nBtn : null;
    if (to) to.focus({ preventScroll: true });
  }
  async function load(id) {
    SH.runs.set(id, null);
    if (SH.dId === id) renderDrawer();
    try {
      const d = await A.api('/api/selfheal/run?id=' + enc(id));
      SH.runs.set(id, { d: d && typeof d === 'object' ? d : {} });
    } catch (e) {
      SH.runs.set(id, { err: (e && e.message) || 'cc-dash is not answering.' });
    }
    if (SH.dId === id) { SH.built = null; renderDrawer(); }
  }

  function renderDrawer() {
    if (drawer.hidden) return;
    const id = SH.dId, got = SH.runs.get(id);
    if (SH.built === id) return;          // a run's results never change: built once, so copy states and open orders stay
    if (!got) {
      P.setText(dCount, ''); P.setText(dSub, 'Loading…'); P.setText(dFoot, '');
      dList.replaceChildren(h('div', { class: 'sh-loading', 'aria-busy': 'true' }, [1, 2].map(() => h('div', { class: 'sh-sk-card' },
        h('div', { class: 'sk sk-line sh-sk-a' }), h('div', { class: 'sk sk-line sh-sk-b' }), h('div', { class: 'sk sh-sk-c' })))));
      return;
    }
    if (got.err) {
      P.setText(dCount, ''); P.setText(dSub, ''); P.setText(dFoot, '');
      const box = A.errorBox({ message: got.err }, 'the proposals');   // app.js's error box; its button retries this load
      const again = box.querySelector('.btn');
      if (again) P.listen(again, 'click', () => load(id));
      dList.replaceChildren(box);
      return;
    }
    SH.built = id;
    const d = got.d, r = rec(d.run) || {}, props = Array.isArray(d.proposals) ? d.proposals.filter((p) => p && typeof p === 'object') : [];
    P.setText(dCount, String(props.length));
    const ps = Array.isArray(r.projects) ? r.projects.length : 0, cost = num(r.cost_usd), budget = num(r.budget_usd);
    P.setText(dSub, [ended(r) ? 'Run ' + A.dateTime(ended(r)) : '', ps ? A.plural(ps, 'project') : '',
      cost != null ? A.money(cost, true) + (budget ? ' of ' + A.money(budget, true) : '') : '', 'nothing here is applied or run'].filter(Boolean).join(' · '));
    const md = str(d.markdown_path);
    P.setText(dFoot, md ? 'The full write-up: ' + md : '');
    P.attr(dFoot, 'title', md || null);
    const kids = [];
    const problems = Array.isArray(d.problems) ? d.problems.filter((x) => typeof x === 'string' && x) : [];
    if (problems.length) {
      kids.push(h('div', { class: 'sh-problems', role: 'alert' }, glyph('failed'),
        h('div', {}, h('b', { text: props.length ? 'cc-dash found problems in this run’s files' : 'This run’s proposals are withheld' }),
          h('ul', {}, problems.map((x) => h('li', { text: x }))))));
    }
    if (!props.length && !problems.length) {
      kids.push(h('div', { class: 'al-empty sh-empty' }, h('p', { class: 'al-empty-t', text: 'No proposals' }),
        h('p', { class: 'faint', text: str(d.notes) || 'Nothing in this run’s window called for a harness change.' })));
    }
    for (const [proj, list] of groups(props)) kids.push(group(proj, list));
    const dropped = Array.isArray(d.dropped) ? d.dropped.filter((x) => x && typeof x === 'object') : [];
    if (dropped.length) {
      kids.push(h('section', { class: 'sh-group sh-dropped' },
        h('div', { class: 'sh-g-head' }, h('h3', { class: 'sh-g-h', text: 'Dropped by the check' }), h('span', { class: 'count', text: String(dropped.length) })),
        h('ul', { class: 'sh-drop-list' }, dropped.map((x) => h('li', {}, h('span', { class: 'sh-drop-t', text: str(x.title) }), str(x.reason) ? h('span', { class: 'sh-drop-r', text: x.reason }) : null)))));
    }
    if (props.length && str(d.notes)) kids.push(h('p', { class: 'sh-notes', text: d.notes }));
    dList.replaceChildren(...kids);
    dList.scrollTop = 0;
  }

  const rank = (p) => { const i = SEV.indexOf(p.severity); return i < 0 ? SEV.length : i; };
  function groups(props) {
    const by = new Map();
    for (const p of props) {
      const k = str(p.project) || '?';
      if (!by.has(k)) by.set(k, []);
      by.get(k).push(p);
    }
    const keys = [...by.keys()].filter((k) => k !== '_cc_tool');
    if (by.has('_cc_tool')) keys.push('_cc_tool');
    return keys.map((k) => [k, by.get(k).slice().sort((a, b) => rank(a) - rank(b))]);
  }
  function group(proj, list) {
    const tpl = proj === '_cc_tool';
    return h('section', { class: 'sh-group', dataset: { project: proj }, 'aria-label': tpl ? 'cc_tool templates, for every project' : proj },
      h('div', { class: 'sh-g-head' },
        h('h3', { class: 'sh-g-h', text: tpl ? 'cc_tool templates' : proj }),
        h('span', { class: 'count', text: String(list.length) }),
        h('span', { class: 'sh-g-sub ellipsis', text: tpl ? 'for every project' : str(list[0].project_root), title: tpl ? null : str(list[0].project_root) || null })),
      list.map(propCard));
  }

  function fact(label, value) {
    if (value == null || value === '') return null;
    return [h('dt', { text: label }), h('dd', {}, value)];
  }
  function evidence(list) {
    if (!Array.isArray(list)) return null;
    const items = list.filter((e) => e && typeof e === 'object');
    if (!items.length) return null;
    return h('ul', { class: 'sh-ev' }, items.map((e) => {
      const sid = str(e.session), n = num(e.count);
      return h('li', {},
        h('span', { class: 'sh-ev-k', text: (KIND_WORD[e.kind] || str(e.kind).replace(/_/g, ' ') || 'evidence') + (n > 1 ? ' ×' + n : '') }),
        sid ? h('a', { class: 'link-u sh-ev-s', href: '#/s/' + enc(sid), title: 'Open session ' + sid, text: 'session ' + sid.slice(0, 8) }) : null,
        str(e.excerpt) ? h('span', { class: 'sh-ev-x mono', title: e.excerpt, text: e.excerpt }) : null);
    }));
  }
  /* One span per line, its kind from the unified-diff grammar: the file
     headers before the first hunk, then hunk headers, +, -, context, "\". */
  function diffBox(patch, target) {
    let inHunk = false;
    const lines = patch.replace(/\n$/, '').split('\n').map((l) => {
      let k;
      if (l.startsWith('@@')) { inHunk = true; k = 'hunk'; }
      else if (!inHunk) k = 'hdr';
      else if (l.startsWith('+')) k = 'add';
      else if (l.startsWith('-')) k = 'del';
      else if (l.startsWith('\\')) k = 'meta';
      else k = 'ctx';
      return h('span', { class: 'sh-l ' + k, text: l });
    });
    return h('pre', { class: 'sh-diff', tabindex: '0', 'aria-label': 'Patch for ' + target }, h('code', { class: 'sh-diff-in' }, lines));
  }
  function cmdBox(cmd, kind, label) {
    return h('div', { class: 'sh-cmd' },
      h('span', { class: 'sh-cmd-l', text: label }),
      h('code', { class: 'sh-cmd-t', dataset: { cmd: kind }, text: cmd }),
      h('button', { type: 'button', class: 'ctl-x sh-cmd-copy', dataset: { copy: kind }, text: 'Copy', 'aria-label': 'Copy the ' + label.toLowerCase() + ' command',
        onclick: (e) => copy(cmd, e.currentTarget) }));
  }
  function propCard(p) {
    const sev = SEV.includes(p.severity) ? p.severity : 'low';
    const target = str(p.target), patch = str(p.patch), ok = p.patch_ok === true && !!patch;
    const tid = 'sh-p-' + String(p.id || '').replace(/[^A-Za-z0-9_-]/g, '') + '-' + Math.random().toString(36).slice(2, 7);
    const order = str(p.order_cmd);
    const orderBox = order ? h('div', { class: 'sh-order', hidden: true },
      cmdBox(order, 'order', 'Order'),
      str(p.order_note) ? h('p', { class: 'sh-note', text: p.order_note }) : null) : null;
    const orderBtn = order ? h('button', { type: 'button', class: 'btn quiet sh-order-btn', 'aria-expanded': 'false', text: 'File as factory order',
      onclick: () => { orderBox.hidden = !orderBox.hidden; orderBtn.setAttribute('aria-expanded', String(!orderBox.hidden)); } }) : null;
    const copyBtn = ok ? h('button', { type: 'button', class: 'btn sh-copy', dataset: { copy: 'patch' }, text: 'Copy patch', onclick: (e) => copy(patch, e.currentTarget) }) : null;
    // The server's caution (a patch that widens permissions or adds hooks or env): read before the diff.
    const caution = str(p.caution).trim();
    const data = { id: str(p.id), project: str(p.project), severity: sev };
    if (caution) data.caution = caution;
    return h('article', { class: 'sh-prop', dataset: data, 'aria-labelledby': tid },
      h('div', { class: 'sh-p-row1' },
        h('span', { class: 'pill sh-sev sev-' + sev, text: SEV_WORD[sev] }),
        target ? h('span', { class: 'tag sh-target', title: target, text: target }) : null,
        p.verdict === 'downgraded' ? h('span', { class: 'sh-verdict', text: 'downgraded by the check' }) : null),
      h('h4', { class: 'sh-p-title', id: tid, text: str(p.title) || 'Untitled proposal' }),
      h('dl', { class: 'sh-facts' }, fact('Failure mode', str(p.failure_mode)), fact('Evidence', evidence(p.evidence)), fact('Risk', str(p.risk)), fact('Check', str(p.check))),
      caution ? h('p', { class: 'sh-caution' }, h('b', { text: 'Caution: ' }), caution) : null,
      ok ? diffBox(patch, target) : h('p', { class: 'sh-problem' }, glyph('failed'),
        h('span', {}, h('b', { text: 'Patch withheld: ' }), str(p.patch_problem) || 'cc-dash could not validate it.')),
      ok && str(p.apply_cmd) ? cmdBox(p.apply_cmd, 'apply', 'Apply') : null,
      copyBtn || orderBtn ? h('div', { class: 'sh-p-acts' }, copyBtn, orderBtn) : null,
      orderBox);
  }

  /* Copy: the async clipboard, else the exact text selected in a read-only box under the button. */
  async function copy(text, b) {
    let ok = false;
    try { if (navigator.clipboard && navigator.clipboard.writeText) { await navigator.clipboard.writeText(text); ok = true; } } catch (e) { ok = false; }
    if (!ok) ok = selectCopy(text, b);
    flash(b, ok);
  }
  function selectCopy(text, b) {
    const row = b.closest('.sh-p-acts, .sh-cmd');
    let ta = row.nextElementSibling;
    if (!ta || !ta.classList.contains('sh-copy-ta')) {
      ta = h('textarea', { class: 'sh-copy-ta', readonly: true, rows: '4', spellcheck: 'false', 'aria-label': 'Selected text: press Ctrl+C to copy' });
      row.after(ta);
    }
    ta.value = text;
    ta.focus({ preventScroll: true });
    ta.select();
    let ok = false;
    try { ok = document.execCommand('copy'); } catch (e) { ok = false; }
    if (ok) { ta.remove(); b.focus({ preventScroll: true }); }
    return ok;
  }
  function flash(b, ok) {
    if (!b.__label) b.__label = b.textContent;
    P.setText(b, ok ? 'Copied' : 'Selected below');
    b.classList.add('copied');
    say(ok ? 'Copied to the clipboard.' : 'The text is selected: press Ctrl+C to copy it.');
    clearTimeout(b.__t);
    b.__t = setTimeout(() => { P.setText(b, b.__label); b.classList.remove('copied'); }, 1600);
  }

  // ── announcements, keys, routes ──────────────────────────────────────
  function say(text) { live.textContent = ''; setTimeout(() => { live.textContent = text; }, 30); }
  function announce() {
    const v = derive();
    const key = v.state + ':' + (v.last ? v.last.id : '');
    if (SH.key && key !== SH.key) {
      if (v.state === 'running') say('Self-heal started.');
      else if (v.state === 'done') say('Self-heal finished: ' + A.plural(num(v.last.proposals) || 0, 'proposal') + '.');
      else if (FAILED.includes(v.state)) say('Self-heal ' + (FAIL_WORD[v.state] || 'failed').toLowerCase() + (str(v.last.error) ? ': ' + v.last.error : '.'));
    }
    SH.key = key;
  }
  function render() { renderHead(); renderPop(); }

  /* While the popover or the drawer is open, Escape closes it (focus back to its
     button) and no other key reaches the page's single-key shortcuts: app.js's
     on document, and alerts.js's on window, which loads after this, so this
     capture listener runs before it and stopImmediatePropagation holds it back.
     Typing in a field (the control code, a copy box) is left to the field. */
  window.addEventListener('keydown', (e) => {
    const inDrawer = !drawer.hidden;
    if ((!SH.open && !inDrawer) || e.metaKey || e.ctrlKey || e.altKey) return;
    if (document.querySelector('dialog[open]')) return;
    const t = e.target;
    if (!inDrawer && !root.contains(t) && t !== document.body) return;
    if (e.key === 'Escape') {
      e.preventDefault();
      e.stopImmediatePropagation();
      if (inDrawer) closeDrawer(true); else closePop(true);
      return;
    }
    if (e.key === 'Tab') return;
    const tag = (t.tagName || '').toLowerCase();
    if (tag === 'input' || tag === 'textarea' || tag === 'select') return;
    e.stopImmediatePropagation();   // default actions (Enter or Space on a button, Enter on a link) still happen
  }, true);
  window.addEventListener('hashchange', () => { closePop(false); closeDrawer(false); renderHead(); });
  // The code typed (or the token lost): the popover swaps the field for Run, and focus follows.
  const onScope = () => {
    if (!SH.d) return;
    const lost = SH.open && (document.activeElement === document.body || !shown(document.activeElement));
    render();
    if (lost) focusFirst();
  };
  document.addEventListener('cc-auth:scope', onScope);
  document.addEventListener('cc-control:ready', onScope);

  // ── polling ──────────────────────────────────────────────────────────
  let timer = 0, inflight = false, again = false;
  async function poll() {
    clearTimeout(timer);
    if (inflight) { again = true; return; }
    inflight = true;
    try {
      const d = await A.api('/api/selfheal');
      if (d && typeof d === 'object') {
        SH.d = d; SH.gone = false; SH.err = '';
        announce();
        if (SH.open && !d.enabled) closePop(false);   // control mode went away (cc-dash restarted without it)
        render();
      }
    } catch (e) {
      SH.err = (e && e.message) || 'cc-dash is not answering.';
      if (e && e.status === 404) SH.gone = true;   // a cc-dash without self-heal: no control at all
      renderHead();   // before the first answer the button stays disabled, the reason in its title; later the last state stays
    } finally {
      inflight = false;
      if (again) { again = false; poll(); } else timer = setTimeout(poll, derive().state === 'running' && !document.hidden ? POLL_RUN : POLL_IDLE);
    }
  }
  document.addEventListener('visibilitychange', () => { if (!document.hidden) poll(); });
  renderHead();
  window.ccAuth.ready.then(poll);
})();
