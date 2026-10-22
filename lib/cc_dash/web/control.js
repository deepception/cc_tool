/* cc-dash control mode (cc-dash --control): act on your agents from here.

   Approve or deny permission prompts, answer questions, approve or reject
   plans, and send messages to a session or subagent. Off unless /api/meta says
   control is enabled; without pairing it only shows how to pair.

   Pieces other scripts place (window.ccControl):
     card slots   slot(requestId, {compact}) -> a node this script fills with
                  the request's action card and keeps current. The card is
                  built once per slot, so a half-typed reason or a picked
                  answer survives every poll; only its status line and buttons
                  change.
     inline(...)  a Review (Answer, Review plan) button for a Live row, which
                  opens the session page and its full decision card. A row
                  never decides: its text is a one-line summary, and only the
                  card shows the whole request. Rows are rebuilt on each poll,
                  so the button carries everything in data-*.
     detail(...)  the session or subagent page panel: open requests and the
                  control history.
     composer(...) the docked message box with each message's state:
                  Queued, Delivered (mid-turn after a tool call, at turn end, or
                  with the next prompt), Seen.
     quickSend(...) the same box as a popover from a Live row.
   Every text goes into the DOM through textContent; nothing is set through
   innerHTML or a style attribute (the CSP blocks inline styles).

   Every decision (toast, drawer, session page) is made on buildCard's card,
   drawn from the request record the hook wrote, never from alert text. The
   card shows every field in SHOWN_KEYS (lib/cc_dash/control.py), which the
   decision digest covers: the whole input (behind "Show all" when long), the
   session, agent, working directory, permission mode, MCP server, flags and
   the rules "Allow for this session" adds. A request whose input the hook had
   to shorten (input_truncated) offers only Deny and Answer in the terminal. */
'use strict';
(() => {
  const POLL_MS = 1000;
  const HIDDEN_MS = 4000;
  const CLEAR_MS = 4000;          // a resolved card shows its result this long, then goes

  // ── tiny DOM helper (text only) ──────────────────────────────────────
  function el(tag, attrs, ...kids) {
    const n = document.createElement(tag);
    for (const k in attrs || {}) {
      const v = attrs[k];
      if (v == null || v === false) continue;
      if (k === 'class') n.className = v;
      else if (k === 'text') n.textContent = v;
      else if (k === 'dataset') Object.assign(n.dataset, v);
      else if (k.startsWith('on')) n.addEventListener(k.slice(2), v);
      else n.setAttribute(k, v === true ? '' : v);
    }
    for (const c of kids.flat(Infinity)) if (c != null && c !== false) n.append(typeof c === 'string' || typeof c === 'number' ? document.createTextNode(String(c)) : c);
    return n;
  }
  const setText = (n, v) => { v = String(v); if (n.textContent !== v) n.textContent = v; };
  /* The dashboard's status language (app.css "One status language"). */
  const stPill = (state, label) => el('span', { class: 'pill s-' + state }, el('span', { class: 'g', 'aria-hidden': 'true' }), label);
  function stLine(cls) { const n = el('div', { class: cls }, el('span', { class: 'g', 'aria-hidden': 'true' }), el('span')); return n; }
  function setLine(n, base, state, txt) { const c = base + (state ? ' s-' + state : ''); if (n.className !== c) n.className = c; setText(n.lastChild, txt); }
  const show = (n, on) => { if (n.hidden === !!on) n.hidden = !on; };
  function waited(sec) {
    sec = Math.max(0, Math.round(sec));
    if (sec < 60) return sec + 's';
    const m = Math.floor(sec / 60);
    if (m < 60) return m + 'm ' + String(sec % 60).padStart(2, '0') + 's';
    return Math.floor(m / 60) + 'h ' + String(m % 60).padStart(2, '0') + 'm';
  }
  const pad = (n) => String(n).padStart(2, '0');
  const clock = (ts) => { const d = new Date(ts * 1000); return pad(d.getHours()) + ':' + pad(d.getMinutes()) + ':' + pad(d.getSeconds()); };

  // ── state ────────────────────────────────────────────────────────────
  const C = {
    on: false, paired: false, ready: false, state: null, offset: 0,
    scope: 'read', locked: false,  // read-only until the control code is typed; locked after 5 wrong codes
    locks: new Set(),             // every "enter the control code" field on the page
    reqs: new Map(), msgs: [], audit: [], stops: [], firstSeen: new Map(),
    slots: new Set(), panels: new Set(), composers: new Set(),
    arm: null,                    // {id, action, until}: keyboard y/n waiting for its second press
  };
  const now = () => Date.now() / 1000 - C.offset;

  async function getJSON(path) {
    const r = await window.ccAuth.fetch(path);
    let body = null;
    try { body = await r.json(); } catch (e) { /* not json */ }
    return { status: r.status, body };
  }
  /* Writes: JSON POSTs carrying the launch token (auth.js adds X-CC-Dash-Token). */
  async function post(path, body) {
    let r;
    try {
      r = await window.ccAuth.fetch(path, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
    } catch (e) { return { status: 0, body: { error: 'cc-dash is not answering.' } }; }
    let out = null;
    try { out = await r.json(); } catch (e) { out = null; }
    if (r.status === 401) setPaired(false);
    if (r.status === 403 && out && out.unlock) setScope('read');
    return { status: r.status, body: out || {} };
  }

  // ── scope: read-only until the control code from the terminal is typed ─
  /* app.css/control.css hide every acting control under html.ctl-locked and show
     the code field instead, so a card or composer built while locked needs no rebuild. */
  function setScope(scope, locked) {
    C.scope = scope === 'control' ? 'control' : 'read';
    if (locked !== undefined) C.locked = !!locked;
    const root = document.documentElement;
    root.classList.toggle('ctl-locked', C.on && C.scope !== 'control');
    for (const l of [...C.locks]) syncLock(l);
    if (C.on) document.dispatchEvent(new CustomEvent('cc-control:state'));   // Live rows rebuild their buttons
  }
  document.addEventListener('cc-auth:scope', () => setScope(window.ccAuth.scope()));
  const LOCKED_TEXT = 'Too many wrong codes from this tab. Reload the page to try again.';
  /* The quiet "enter the code" field: one per card and composer, all sharing one state. */
  function lockBox(compact) {
    const input = el('input', { class: 'ctl-other ctl-code mono', type: 'text', autocomplete: 'off', spellcheck: 'false', autocapitalize: 'characters',
      maxlength: '24', placeholder: 'XXXX-XXXX-XXXX', 'aria-label': 'Control code from your terminal' });
    const go = el('button', { type: 'button', class: 'btn', text: 'Unlock' });
    const err = el('div', { class: 'ctl-err', role: 'alert', hidden: true });
    const label = el('p', { class: 'ctl-lock-t', text: 'Enter the control code from your terminal to act' });
    const n = el('div', { class: 'ctl-lock' + (compact ? ' compact' : ''), role: 'group', 'aria-label': 'Unlock control' }, label,
      el('div', { class: 'ctl-acts' }, input, go), err);
    n.__lock = { input, go, err, label };
    async function submit() {
      const code = input.value.trim();
      if (!code) { input.focus(); return; }
      go.disabled = true;
      show(err, false);
      const r = await window.ccAuth.unlock(code);
      go.disabled = false;
      if (r.ok) { input.value = ''; return; }        // cc-auth:scope unlocks every field
      if (r.locked) { setScope(C.scope, true); return; }   // the field's label says it; no second copy below
      setText(err, r.error);
      show(err, true);
      input.select();
    }
    go.addEventListener('click', submit);
    input.addEventListener('keydown', (e) => { if (e.key === 'Enter') { e.preventDefault(); submit(); } e.stopPropagation(); });
    C.locks.add(n);
    syncLock(n);
    return n;
  }
  function syncLock(n) {
    if (!n.isConnected && n.__lock.mounted) { C.locks.delete(n); return; }
    if (n.isConnected) n.__lock.mounted = true;
    const l = n.__lock;
    l.input.disabled = C.locked;
    l.go.disabled = C.locked;
    setText(l.label, C.locked ? LOCKED_TEXT : 'Enter the control code from your terminal to act');
    if (C.locked) show(l.err, false);
  }

  async function init() {
    await window.ccAuth.ready;
    let meta;
    try { meta = await getJSON('/api/meta'); } catch (e) { setTimeout(init, 3000); return; }
    if (meta.status === 401) { setTimeout(init, 4000); return; }   // app.js shows "Re-pair"; try again later
    const c = meta.body && meta.body.control;
    if (!c || !c.enabled) return;            // not in control mode: no controls anywhere
    C.on = true;
    document.documentElement.classList.add('ctl-on');
    setScope(c.scope === 'control' || window.ccAuth.scope() === 'control' ? 'control' : 'read', !!c.locked);
    setPaired(true);
    poll();
    document.dispatchEvent(new CustomEvent('cc-control:ready'));
  }

  let timer = null;
  async function poll() {
    clearTimeout(timer);
    if (C.paired) {
      try {
        const r = await getJSON('/api/control/state');
        if (r.status === 401) setPaired(false);
        else if (r.status === 200 && r.body) apply(r.body);
      } catch (e) { /* the next poll retries */ }
    }
    timer = setTimeout(poll, document.hidden ? HIDDEN_MS : POLL_MS);
  }
  document.addEventListener('visibilitychange', () => { if (!document.hidden && C.on) poll(); });

  function apply(d) {
    C.state = d;
    if (typeof d.now === 'number') C.offset = Date.now() / 1000 - d.now;
    C.reqs = new Map((d.requests || []).map((r) => [r.id, r]));
    for (const id of C.reqs.keys()) if (!C.firstSeen.has(id)) C.firstSeen.set(id, Date.now());
    C.msgs = d.messages || [];
    C.audit = d.audit || [];
    C.stops = d.stops || [];
    C.ready = true;
    for (const s of [...C.slots]) syncSlot(s);
    for (const p of [...C.panels]) syncPanel(p);
    for (const c of [...C.composers]) syncComposer(c);
    document.dispatchEvent(new CustomEvent('cc-control:state'));
  }

  // ── pairing (app.js shows the "Re-pair" screen; controls just hide) ──
  function setPaired(p) {
    const was = C.paired;
    C.paired = p;
    document.documentElement.classList.toggle('ctl-paired', p);
    if (!p && was) setTimeout(recheck, 4000);
    if (p && !was && C.on) poll();
  }
  async function recheck() {
    const r = await getJSON('/api/meta').catch(() => ({ status: 0 }));
    if (r.status === 200) setPaired(true); else setTimeout(recheck, 4000);
  }

  /* What is shown as the exact command: bidi controls and invisible characters
     made visible (they can make a command read differently from what runs), and
     very long values cut in the middle with a "Show all" toggle. */
  const INVISIBLE = /[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f-\u009f­؜ᅟᅠ឴឵᠋-᠏​-‏‪-‮⁠-⁯ㅤ︀-️﻿ﾠ￹-￻]/g;
  const visible = (s) => String(s == null ? '' : s).replace(INVISIBLE, (c) => '⟨U+' + c.charCodeAt(0).toString(16).toUpperCase().padStart(4, '0') + '⟩');
  const hasInvisible = (s) => { INVISIBLE.lastIndex = 0; const r = INVISIBLE.test(String(s || '')); INVISIBLE.lastIndex = 0; return r; };
  function elideMiddle(s, max) {
    if (s.length <= max) return s;
    const keep = Math.floor((max - 40) / 2);
    return s.slice(0, keep) + '\n  … ' + (s.length - 2 * keep).toLocaleString('en-US') + ' characters not shown …\n' + s.slice(s.length - keep);
  }

  // ── labels ───────────────────────────────────────────────────────────
  function whatText(r) {
    const t = r.tool || '';
    if (r.kind === 'question') return 'Has a question for you';
    if (r.kind === 'plan') return 'Plan ready for your approval';
    if (t === 'Bash') return 'Wants to run a command';
    if (['Edit', 'Write', 'MultiEdit', 'NotebookEdit'].includes(t)) return 'Wants to edit a file';
    if (t === 'WebFetch' || t === 'WebSearch') return 'Wants to go online';
    return t ? 'Wants to use ' + t : 'Needs your permission';
  }
  /* Everything in the request's input as text, nothing left out: the fields a
     tool is known by first (a command, a file and its whole new content, every
     edit of a MultiEdit with its old and new text, a notebook cell's source),
     then every other field as JSON (a Bash timeout or sandbox flag, Edit's
     replace_all, an MCP tool's arguments). The card shows this text, whole
     behind "Show all" when it is long; the input it comes from is what the
     decision digest covers. */
  const asJSON = (v) => { try { return JSON.stringify(v, null, 2); } catch (e) { return String(v); } };
  const str = (v) => (v == null ? '' : typeof v === 'string' ? v : asJSON(v));
  const marked = (v, mark) => str(v).split('\n').map((l) => mark + l).join('\n');
  const isObj = (v) => !!v && typeof v === 'object' && !Array.isArray(v);
  function boxText(r) {
    const i = isObj(r.input) ? r.input : {};
    const used = new Set();
    const take = (k) => { used.add(k); return i[k]; };
    const out = [];
    const t = r.tool;
    if (t === 'Bash') out.push(str(take('command')));
    else if (t === 'Write') out.push(str(take('file_path')), '', str(take('content')));
    else if (t === 'Edit') out.push(str(take('file_path')), '', marked(take('old_string'), '- '), marked(take('new_string'), '+ '));
    else if (t === 'MultiEdit') {
      out.push(str(take('file_path')));
      if (Array.isArray(i.edits)) {
        take('edits');
        i.edits.forEach((e, n) => {
          out.push('', 'Edit ' + (n + 1) + ' of ' + i.edits.length + ':');
          if (!isObj(e)) { out.push(asJSON(e)); return; }
          out.push(marked(e.old_string, '- '), marked(e.new_string, '+ '));
          const extra = Object.keys(e).filter((k) => k !== 'old_string' && k !== 'new_string');
          if (extra.length) out.push(asJSON(Object.fromEntries(extra.map((k) => [k, e[k]]))));
        });
      }
    } else if (t === 'NotebookEdit') out.push(str(take('notebook_path')), '', str(take('new_source')));
    else if (t === 'WebFetch') out.push(str(take('url')), '', str(take('prompt')));
    const rest = Object.keys(i).filter((k) => !used.has(k));
    if (rest.length) out.push((out.length ? '\n' : '') + asJSON(Object.fromEntries(rest.map((k) => [k, i[k]]))));
    return out.join('\n');
  }
  /* Session, agent, permission mode and MCP server: the rest of what the card shows. */
  function metaText(r) {
    const bits = ['session ' + String(r.session || '').slice(0, 8)];
    if (r.agent_id) bits.push('agent ' + String(r.agent_id).slice(0, 12));
    if (r.mode) bits.push(r.mode + ' mode');
    if (r.server) bits.push('MCP server ' + r.server);
    return bits.join(' · ');
  }
  const agentLabel = (r) => (r.agent_id ? (r.agent_type || 'subagent') : 'main thread');
  function statusText(r) {
    const d = r.decision || {};
    const act = d.action === 'allow' ? (r.kind === 'plan' ? 'Plan approved' : 'Approved') : d.action === 'deny' ? (r.kind === 'plan' ? 'Plan rejected' : 'Denied') : d.action === 'answer' ? 'Answer sent' : '';
    switch (r.status) {
      case 'sent': return [act ? act + ': sent, waiting for the agent' : 'Sent', 'working'];
      case 'applied': return [(act || 'Done') + ': applied', 'done'];
      case 'terminal': return ['Answered in the terminal', 'idle'];
      case 'released': return ['Released to the terminal', 'idle'];
      case 'timeout': return ['The hook stopped waiting; answer in the terminal', 'stalled'];
      case 'lease': return ['cc-dash stopped; answer in the terminal', 'stalled'];
      case 'closed': return ['Closed', 'idle'];
      default: return ['', ''];
    }
  }

  // ── acting ───────────────────────────────────────────────────────────
  async function decide(id, action, extra) {
    const r = C.reqs.get(id);
    if (!r || r.status !== 'open') return { status: 409, body: { error: 'This request is no longer open.' } };
    const res = await post('/api/control/decide', Object.assign({ id, action, hash: r.hash }, extra || {}));
    if (res.status === 200 && res.body.request) {
      C.reqs.set(id, Object.assign({}, r, res.body.request));
      for (const s of [...C.slots]) syncSlot(s);
      for (const p of [...C.panels]) syncPanel(p);
      document.dispatchEvent(new CustomEvent('cc-control:state'));
      setTimeout(poll, 250);
    }
    return res;
  }

  // ── the action card ──────────────────────────────────────────────────
  /* Built once per slot and request; sync() updates the age, status and
     which buttons are live. Local UI state (a reason being typed, picked
     answers, an open confirm) lives in the card and survives polls. */
  function buildCard(r, opts) {
    const compact = !!(opts && opts.compact);
    const age = el('span', { class: 'ctl-age num', dataset: { ctlSince: String(r.ts) } });
    const status = stLine('ctl-status');
    status.setAttribute('role', 'status');
    status.setAttribute('aria-live', 'polite');
    const err = el('div', { class: 'ctl-err', role: 'alert', hidden: true });
    const armNote = el('div', { class: 'ctl-arm', hidden: true });
    const short = !!r.input_truncated;    // the hook passed a shortened copy: no approval from here
    const card = el('div', { class: 'ctl-card k-' + r.kind + (r.guard ? ' has-guard' : '') + (compact ? ' compact' : ''), tabindex: '0', role: 'group', dataset: { req: r.id },
      'aria-label': whatText(r) + ', ' + agentLabel(r) + (short ? '. Too long to approve here: press n to deny.' : '. Press y to approve, n to deny.') });
    const badge = stPill('decide', 'Needs a decision');
    const parts = { card, age, status, err, armNote, badge, buttons: [] };
    const fail = (res) => { setText(err, (res.body && res.body.error) || 'Something went wrong (HTTP ' + res.status + ').'); show(err, true); };
    const busy = (on) => { for (const b of parts.buttons) b.disabled = on || b.dataset.dead === '1'; };
    async function act(action, extra) {
      show(err, false);
      busy(true);
      const res = await decide(r.id, action, extra);
      if (res.status !== 200) { busy(false); fail(res); }
      return res;
    }
    parts.act = act;
    const btn = (label, cls, onclick, title) => { const b = el('button', { type: 'button', class: 'btn ' + (cls || ''), text: label, title: title || null, onclick }); parts.buttons.push(b); return b; };

    const head = el('div', { class: 'ctl-row1' },
      badge,
      el('span', { class: 'ctl-who ellipsis', text: agentLabel(r) + (r.tool && r.kind === 'permission' ? ' · ' + r.tool : '') }), age);
    // Always who and where, never Claude's own description of the call alone.
    card.append(head, el('div', { class: 'ctl-what', text: whatText(r) }),
      r.cwd ? el('div', { class: 'ctl-where mono ellipsis', title: r.cwd, text: 'in ' + visible(r.cwd) }) : null,
      el('div', { class: 'ctl-meta mono ellipsis', title: metaText(r), text: visible(metaText(r)) }));
    // Too long to show whole: the hook passed a shortened copy, so nothing here approves it.
    const shortNote = () => el('div', { class: 'ctl-guard', role: 'note' }, el('b', { text: 'Too long to approve here' }),
      el('p', { text: 'The hook passed a shortened copy of this request, so the dashboard cannot show all of it and offers no approval. Deny it here, or answer in the terminal, which shows all of it.' }));
    const releaseBtn = () => btn('Answer in the terminal', 'quiet', () => act('release'), 'Leave this prompt to the terminal');

    if (r.kind === 'permission') {
      const full = visible(boxText(r));
      const cut = elideMiddle(full, compact ? 600 : 2400);
      const box = el('pre', { class: 'ctl-box mono' + (compact ? ' short' : ''), text: cut, tabindex: '0', 'aria-label': 'Exact ' + (r.tool === 'Bash' ? 'command' : 'request') });
      card.append(box);
      if (cut !== full) {
        // Shown in a toast too: whatever can be approved can be read in full first.
        const all = el('button', { type: 'button', class: 'ctl-x', text: 'Show all', onclick: () => { const on = box.dataset.all !== '1'; box.dataset.all = on ? '1' : '0'; box.textContent = on ? full : cut; all.textContent = on ? 'Show less' : 'Show all'; } });
        card.append(el('div', { class: 'ctl-more faint' }, 'Long: the middle is cut. ', all));
      }
      if (hasInvisible(boxText(r)) || hasInvisible(r.cwd)) card.append(el('div', { class: 'ctl-guard', role: 'note' }, el('b', { text: 'Hidden characters' }), el('p', { text: 'This contains invisible or direction-changing characters, shown as ⟨U+…⟩. What runs may read differently from what it looks like.' })));
      if (short) card.append(shortNote());
      const flagged = r.guard || r.ask_rule;
      if (r.guard) card.append(el('div', { class: 'ctl-guard', role: 'note' }, el('b', { text: 'bash-guard asked about this command' }), el('p', { text: r.guard.replace(/^NEEDS APPROVAL:\s*/, '') })));
      else if (r.ask_rule && r.ask_rule.startsWith('(')) card.append(el('div', { class: 'ctl-guard', role: 'note' }, el('b', { text: 'Could not check this against your ask rules' }), el('p', { text: r.ask_rule.replace(/^\(|\)$/g, '') + '. Treated as if a rule matched.' })));
      else if (r.ask_rule) card.append(el('div', { class: 'ctl-guard', role: 'note' }, el('b', { text: 'Your settings ask before this' }), el('p', {}, 'It matches the permissions.ask rule ', el('code', { class: 'mono', text: r.ask_rule }), '.')));
      const reason = el('textarea', { class: 'ctl-text', rows: '2', placeholder: 'Why? Claude reads this.', 'aria-label': 'Reason for denying' });
      const reasonRow = el('div', { class: 'ctl-sub', hidden: true }, reason,
        el('div', { class: 'ctl-acts' }, btn('Deny with this reason', 'danger', () => act('deny', { message: reason.value })), btn('Cancel', 'quiet', () => { show(reasonRow, false); show(acts, true); })));
      // Flagged commands: a typed "approve", never a click.
      const typed = el('input', { class: 'ctl-other', type: 'text', autocomplete: 'off', spellcheck: 'false', placeholder: 'type approve', 'aria-label': 'Type approve to run this flagged command' });
      const go = btn('Run it', 'warn-solid', () => { if (typed.value.trim().toLowerCase() === 'approve') act('allow', { confirm: 'approve' }); });
      go.dataset.dead = '1';
      typed.addEventListener('input', () => { const ok = typed.value.trim().toLowerCase() === 'approve' && (C.reqs.get(r.id) || r).status === 'open'; go.dataset.dead = ok ? '0' : '1'; go.disabled = !ok; });
      typed.addEventListener('keydown', (e) => { if (e.key === 'Enter' && !go.disabled) go.click(); if (e.key === 'Escape') { show(confirm, false); show(acts, true); } });
      const confirm = el('div', { class: 'ctl-sub ctl-confirm', hidden: true },
        el('p', { text: (r.guard ? 'bash-guard flagged this command.' : r.ask_rule.startsWith('(') ? 'This could not be checked against your ask rules.' : 'Your settings ask before this command.') + ' Approving runs it exactly as shown. Type approve to confirm.' }),
        el('div', { class: 'ctl-acts' }, typed, go, btn('Cancel', 'quiet', () => { show(confirm, false); show(acts, true); })));
      const openConfirm = () => { show(acts, false); show(confirm, true); typed.focus(); return null; };
      const rules = Array.isArray(r.session_rules) ? r.session_rules.map(String) : [];
      const acts = el('div', { class: 'ctl-acts' },
        short ? null : btn('Approve', 'primary', () => (flagged ? openConfirm() : act('allow')), flagged ? 'Approve (asks you to type approve)' : 'Approve (y y)'),
        short || flagged || !rules.length ? null : btn('Allow for this session', '', () => act('allow_session'), 'Adds ' + rules.join(', ') + ' to this session only (not saved to any settings file)'),
        btn('Deny', '', () => act('deny'), 'Deny (n n)'),
        btn('Deny with reason…', 'quiet', () => { show(acts, false); show(reasonRow, true); reason.focus(); }),
        short ? releaseBtn() : null);
      // The rules are shown in full wherever the button is (a toast included): they are what it adds.
      if (!short && !flagged && rules.length) card.append(el('div', { class: 'ctl-more faint' }, 'Allow for this session adds ', el('code', { class: 'mono', text: visible(rules.join(', ')) }), ' until the session ends.'));
      card.append(acts, confirm, reasonRow);
      parts.allow = () => (short ? null : flagged ? openConfirm() : act('allow'));
      parts.deny = () => act('deny');
    } else if (r.kind === 'plan') {
      const pin = isObj(r.input) ? r.input : {};
      const plan = str(pin.plan);
      card.append(el('details', { class: 'ctl-plan', open: compact ? null : '' }, el('summary', { text: 'Plan' + (plan ? ' (' + plan.split('\n').length + ' lines)' : '') }), el('pre', { class: 'ctl-plan-text', text: visible(plan) || 'The plan text was not passed to the hook.' })));
      // Anything else the plan carries (permissions it asks for, say) is part of what approving accepts.
      const extra = Object.keys(pin).filter((k) => k !== 'plan');
      if (extra.length) card.append(el('details', { class: 'ctl-plan', open: '' }, el('summary', { text: 'Also in this request' }), el('pre', { class: 'ctl-plan-text mono', text: visible(asJSON(Object.fromEntries(extra.map((k) => [k, pin[k]])))) })));
      if (short) card.append(shortNote());
      const fb = el('textarea', { class: 'ctl-text', rows: '3', placeholder: 'What should change? Claude reads this and revises the plan.', 'aria-label': 'Feedback on the plan' });
      const fbRow = el('div', { class: 'ctl-sub', hidden: true }, fb,
        el('div', { class: 'ctl-acts' }, btn('Send feedback', 'danger', () => act('deny', { message: fb.value })), btn('Cancel', 'quiet', () => { show(fbRow, false); show(acts, true); })));
      const acts = el('div', { class: 'ctl-acts' },
        short ? null : btn('Approve plan', 'primary', () => act('allow'), 'Approve (y y)'),
        btn('Reject with feedback…', '', () => { show(acts, false); show(fbRow, true); fb.focus(); }, 'Reject (n)'),
        short ? releaseBtn() : null);
      card.append(acts, fbRow);
      parts.allow = () => (short ? null : act('allow'));
      parts.deny = () => { show(acts, false); show(fbRow, true); fb.focus(); return null; };
    } else {
      const qin = isObj(r.input) ? r.input : {};
      const qs = (Array.isArray(qin.questions) ? qin.questions : []).filter(isObj);
      if (short) card.append(shortNote());
      const picks = qs.map(() => new Set());
      const others = [];
      const send = btn('Send answer' + (qs.length > 1 ? 's' : ''), 'primary', () => {
        const answers = {};
        qs.forEach((q, i) => {
          const chosen = [...picks[i]];
          const o = others[i].value.trim();
          if (o) chosen.push(o);
          if (chosen.length) answers[q.question] = chosen.join(', ');
        });
        act('answer', { answers });
      });
      const refresh = () => {
        const ok = !short && qs.length > 0 && qs.every((q, i) => picks[i].size || others[i].value.trim());
        const live = C.reqs.get(r.id);
        send.dataset.dead = ok && (!live || live.status === 'open') ? '0' : '1';
        send.disabled = send.dataset.dead === '1';
      };
      qs.forEach((q, i) => {
        const multi = !!q.multiSelect;
        const opts = el('div', { class: 'ctl-opts', role: multi ? 'group' : 'radiogroup', 'aria-label': q.question || 'Options' });
        for (const o of Array.isArray(q.options) ? q.options : []) {
          const label = String(o && o.label != null ? o.label : o);
          const b = btn(label, 'opt', () => {
            if (multi) { if (picks[i].has(label)) picks[i].delete(label); else picks[i].add(label); } else { picks[i].clear(); picks[i].add(label); if (!multi) others[i].value = ''; }
            for (const x of opts.children) x.setAttribute('aria-checked', String(picks[i].has(x.dataset.label)));
            refresh();
          }, o && o.description ? String(o.description) : null);
          b.dataset.label = label;
          b.setAttribute('role', multi ? 'checkbox' : 'radio');
          b.setAttribute('aria-checked', 'false');
          opts.append(b);
        }
        const other = el('input', { class: 'ctl-other', type: 'text', placeholder: multi ? 'Other (added to what you picked)' : 'Other: type your own answer', 'aria-label': 'Other answer to: ' + (q.question || ''), oninput: () => { if (!multi && other.value.trim()) { picks[i].clear(); for (const x of opts.children) x.setAttribute('aria-checked', 'false'); } refresh(); } });
        others.push(other);
        card.append(el('fieldset', { class: 'ctl-q' }, el('legend', {}, q.header ? el('span', { class: 'tag', text: str(q.header) }) : null, multi ? el('span', { class: 'faint', text: ' pick any' }) : null),
          el('p', { class: 'ctl-qt', text: visible(str(q.question)) }), opts, other));
      });
      // Any other field of the request is shown too: the answer is sent back with it.
      const qextra = Object.keys(qin).filter((k) => k !== 'questions');
      if (qextra.length) card.append(el('details', { class: 'ctl-plan' }, el('summary', { text: 'Also in this request' }), el('pre', { class: 'ctl-plan-text mono', text: visible(asJSON(Object.fromEntries(qextra.map((k) => [k, qin[k]])))) })));
      card.append(el('div', { class: 'ctl-acts' }, short ? null : send, btn('Decline', 'quiet', () => act('deny'), 'Tell Claude you will not answer'), short ? releaseBtn() : null),
        el('div', { class: 'ctl-note faint', text: 'The question is also open in the terminal; the first answer wins.' }));
      refresh();
      parts.allow = () => { if (!send.disabled) send.click(); return null; };
      parts.deny = () => act('deny');
    }
    card.append(lockBox(compact), armNote, err, status);
    // A card that just appeared under the pointer cannot be clicked through by accident.
    parts.armed = false;
    setTimeout(() => { parts.armed = true; syncCard(parts, C.reqs.get(r.id) || r); }, 700);
    return parts;
  }

  function syncCard(parts, r) {
    const [txt, cls] = statusText(r);
    setLine(parts.status, 'ctl-status', cls, txt);
    const open = r.status === 'open';
    parts.card.classList.toggle('resolved', !open);
    const bc = 'pill s-' + (open ? 'decide' : 'idle');
    if (parts.badge.className !== bc) parts.badge.className = bc;
    for (const b of parts.buttons) { if (!open) b.dataset.dead = '1'; b.disabled = b.dataset.dead === '1' || !parts.armed; }
    if (!open) { show(parts.armNote, false); for (const x of parts.card.querySelectorAll('textarea, input')) x.disabled = true; }
    setText(parts.age, open ? 'waiting ' + waited(now() - r.ts) : '');
  }

  /* A slot: a node someone placed that shows one request's card. */
  function slot(id, opts) {
    const n = el('div', { class: 'ctl-slot', dataset: { ctlSlot: id } });
    n.__ctl = { id, opts: opts || {}, parts: null, made: Date.now() };
    C.slots.add(n);
    syncSlot(n);
    return n;
  }
  function syncSlot(n) {
    const s = n.__ctl;
    if (!n.isConnected && Date.now() - s.made > 5000) { C.slots.delete(n); return; }
    const r = C.reqs.get(s.id);
    if (!r) return;
    if (!s.parts) { s.parts = buildCard(r, s.opts); n.append(s.parts.card); }
    syncCard(s.parts, r);
  }
  /* How long a toast whose alert just closed should stay to show the result. */
  function lingerMs(id) {
    const r = C.reqs.get(id);
    if (!r || r.status === 'open' || !r.resolved_at) return r && r.status === 'sent' ? 2500 : 0;
    return Math.max(0, CLEAR_MS - (now() - r.resolved_at) * 1000);
  }

  // ── the Review button for a Live row (rebuilt on every poll) ─────────
  /* A row's text is a one-line summary, so a row never approves or denies: its
     one button opens the session page, where the full card is. */
  function inline(id, href) {
    if (!C.on || !C.paired) return null;
    const r = C.reqs.get(id);
    if (!r) return null;
    if (r.status !== 'open') { const [txt, cls] = statusText(r); if (!txt) return null; const n = stLine('ctl-status ctl-inline-st'); setLine(n, 'ctl-status ctl-inline-st', cls, txt); return n; }
    const label = r.kind === 'question' ? 'Answer' : r.kind === 'plan' ? 'Review plan' : 'Review';
    return el('span', { class: 'ctl-inline' }, el('button', { type: 'button', class: 'btn ctl-mini' + (C.scope === 'control' ? ' primary' : ''),
      text: label, title: 'Open the full request to decide', dataset: { ctlAct: 'open', ctlReq: id, ctlHref: href || '' } }));
  }
  // Delegated, and in the capture phase: a row's own click (open the agent) must not also fire.
  document.addEventListener('click', async (e) => {
    const t = e.target.closest && e.target.closest('[data-ctl-act]');
    if (!t) {
      const m = e.target.closest && e.target.closest('[data-ctl-msg]');
      if (m) { e.stopPropagation(); e.preventDefault(); quickSend(m.dataset.ctlMsg, m.dataset.ctlAgent || null, m); }
      return;
    }
    e.stopPropagation();
    e.preventDefault();
    // Rows only open the card (inline() makes no other button); nothing here decides.
    if (t.dataset.ctlAct === 'open' && t.dataset.ctlHref) location.hash = t.dataset.ctlHref;
  }, true);

  // Enter or Space on those buttons must not reach the row's own key handler (open the agent).
  document.addEventListener('keydown', (e) => {
    if ((e.key === 'Enter' || e.key === ' ') && e.target.closest && e.target.closest('[data-ctl-act], [data-ctl-msg]')) e.stopPropagation();
  }, true);

  // ── session / subagent panel ─────────────────────────────────────────
  function detail(session, agentId) {
    const cards = el('div', { class: 'ctl-cards' });
    const hist = el('ol', { class: 'ctl-hist-list' });
    const histCount = el('span', { class: 'faint num' });
    const history = el('details', { class: 'ctl-history' }, el('summary', {}, 'Control history ', histCount), hist);
    const stopSt = el('span', { class: 'ctl-stop-st faint', role: 'status' });
    const stopBtn = el('button', { type: 'button', class: 'btn ctl-stop', text: 'Stop at next tool call', title: 'Denies the next tool call and ends the turn. A tool that is already running finishes first.' });
    stopBtn.addEventListener('click', async () => {
      stopBtn.disabled = true;
      const r = await post('/api/control/stop', { session, agent_id: agentId || null });
      if (r.status !== 200) { stopBtn.disabled = false; setText(stopSt, (r.body && r.body.error) || 'Could not stop (HTTP ' + r.status + ').'); return; }
      C.stops = C.stops.filter((x) => x.key !== r.body.stop.key).concat([r.body.stop]);
      syncPanel(n);
      setTimeout(poll, 300);
    });
    const stopRow = el('div', { class: 'ctl-stop-row', hidden: true }, stopBtn, stopSt);
    const n = el('section', { class: 'ctl-panel', 'aria-label': 'Control' }, cards, el('div', { class: 'ctl-tools' }, stopRow, history));
    n.__ctl = { session, agentId: agentId || null, cardSlots: new Map(), histKeys: new Map(), stopBtn, stopSt, stopRow, cards, history };
    C.panels.add(n);
    syncPanel(n);
    return n;
  }
  function syncPanel(n) {
    const p = n.__ctl;
    if (!n.isConnected && p.mounted) { C.panels.delete(n); return; }
    if (n.isConnected) p.mounted = true;
    const cards = p.cards, history = p.history;
    show(n, C.on && C.paired);
    const key = p.session + '.' + (p.agentId || 'main');
    const st = C.stops.find((x) => x.key === key);
    const live = liveState(p.session, p.agentId);
    const pending = st && st.state === 'pending';
    const dis = !!pending || live === 'idle' || live === 'done' || live === '';
    if (p.stopBtn.disabled !== dis) p.stopBtn.disabled = dis;   // assigning the same value still writes the attribute (a DOM mutation every poll)
    // Stop shows only while there is a turn to stop, or to report how a stop went. A subagent that
    // finished, failed or stalled gets no new turn: no Stop row at all, not even to say so.
    const subRunning = ['tool', 'generating', 'waiting'].includes(live);
    show(p.stopRow, p.agentId ? subRunning : (['tool', 'generating', 'waiting', 'stalled'].includes(live) || !!st));
    setText(p.stopSt, pending ? 'Stopping: waits for the next tool call' : st && st.state === 'stopped' ? 'Stopped at ' + (st.tool || 'a tool call') + ' (' + clock(st.applied_ts) + ')'
      : st && st.state === 'turn ended' ? 'The turn ended before another tool call' : st && st.state === 'expired' ? 'Stop expired (no tool call came)' : (live === 'idle' || live === 'done') ? 'At its prompt: nothing to stop' : '');
    const t = now();
    const mine = [...C.reqs.values()].filter((r) => r.session === p.session && (!p.agentId || r.agent_id === p.agentId)
      && (r.status === 'open' || r.status === 'sent' || (r.resolved_at && t - r.resolved_at < CLEAR_MS / 1000)));
    const want = mine.map((r) => { let s = p.cardSlots.get(r.id); if (!s) { s = slot(r.id); p.cardSlots.set(r.id, s); } return s; });
    for (const [id, s] of p.cardSlots) if (!mine.some((r) => r.id === id)) { p.cardSlots.delete(id); C.slots.delete(s); }
    window.ccDom.reconcile(cards, want);
    const audit = C.audit.filter((a) => a.session === p.session && (!p.agentId || a.agent_id === p.agentId));
    setText(history.querySelector('.num'), audit.length ? '(' + audit.length + ')' : '(none yet)');
    // A subagent's history shows when it has entries, or as "(none yet)" while it runs; with
    // nothing to show at all the panel goes away.
    if (p.agentId) {
      show(history, audit.length > 0 || subRunning);
      show(n, C.on && C.paired && (audit.length > 0 || subRunning || mine.length > 0));
    }
    const rows = audit.slice(-60).reverse().map((a) => {
      const k = a.ts + '|' + a.action + '|' + (a.request || a.message || '');
      let row = p.histKeys.get(k);
      if (!row) { row = histRow(a); p.histKeys.set(k, row); }
      return row;
    });
    window.ccDom.reconcile(history.querySelector('ol'), rows);
  }
  function histRow(a) {
    const what = { stop: 'Stop requested', allow_session: 'Allowed for the session', allow: 'Approved', deny: a.kind === 'plan' ? 'Rejected plan' : 'Denied', answer: 'Answered', release: 'Released', message: 'Sent message', cancel: 'Cancelled message', result: 'Result', unlock: 'Control code', refused: 'Ignored a forged file' }[a.action] || a.action;
    let detailText = a.target || a.text || '';
    if (a.action === 'answer' && a.answers) detailText = Object.entries(a.answers).map(([q, v]) => q + ' → ' + v).join('; ');
    if (a.action === 'deny' && a.message) detailText = (a.target ? a.target + '. ' : '') + 'Reason: ' + a.message;
    let res = a.action === 'result' ? (a.result === 'terminal' ? 'answered in the terminal' : a.result + (a.via ? ' (' + (VIA[a.via] || a.via) + ')' : '')) : a.result;
    let label = what + (a.tool && a.action !== 'message' ? ' ' + a.tool : '') + (a.agent_id ? ' (subagent)' : '');
    if (a.action === 'result') {
      const m = a.message ? C.msgs.find((x) => x.id === a.message) : null;
      label = a.message ? 'Message' : a.stop ? 'Stop' : (a.tool ? a.tool + ' prompt' : 'Prompt');
      if (m) detailText = m.text;
      if (a.request) { const r = C.reqs.get(a.request); if (r) detailText = r.sig || detailText; }
      res = res || '';
    }
    return el('li', { class: 'ctl-hist a-' + a.action },
      el('span', { class: 'num faint', text: clock(a.ts) }),
      el('span', { class: 'ctl-hist-what', text: label }),
      el('span', { class: 'ctl-hist-detail', text: detailText, title: detailText || null }),
      el('span', { class: 'ctl-hist-res faint', text: res || '' }));
  }

  // ── message composer ─────────────────────────────────────────────────
  const VIA = { socket: 'to the session', PostToolUse: 'mid-turn, after a tool call', PostToolUseFailure: 'mid-turn, after a tool call', Stop: 'at turn end', SubagentStop: 'at turn end', UserPromptSubmit: 'with your next prompt' };
  function msgState(m) {
    if (m.state === 'queued') return ['Queued', 'idle'];
    if (m.state === 'delivering') return ['Delivering', 'working'];
    if (m.state === 'delivered') return [m.via === 'socket' ? 'Delivered to the session' : 'Delivered ' + (VIA[m.via] || ''), 'done'];
    if (m.state === 'seen') return ['Seen', 'done'];
    if (m.state === 'cancelled') return ['Cancelled', 'idle'];
    if (m.state === 'expired') return ['Expired', 'stalled'];
    return [m.state || '', 'idle'];
  }
  function composer(session, agentId, opts) {
    opts = opts || {};
    const list = el('ol', { class: 'ctl-msgs', 'aria-label': 'Messages sent from here', 'aria-live': 'polite' });
    const who = agentId ? 'this subagent' : 'this session';
    const ta = el('textarea', { class: 'ctl-text ctl-input', rows: opts.pop ? '3' : '1', placeholder: 'Message ' + who + '…', 'aria-label': 'Message ' + who, 'aria-describedby': null });
    const sendBtn = el('button', { type: 'button', class: 'btn primary', text: 'Send', title: 'Send (Ctrl+Enter)' });
    const hint = el('div', { class: 'ctl-hint faint' });
    const err = el('div', { class: 'ctl-err', role: 'alert', hidden: true });
    const n = el('section', { class: 'ctl-composer' + (opts.pop ? ' pop' : ''), 'aria-label': 'Message ' + (agentId ? 'subagent' : 'session') },
      list, lockBox(true), el('div', { class: 'ctl-comp-row' }, ta, sendBtn), el('div', { class: 'ctl-comp-foot' }, hint, opts.pop ? null : el('span', { class: 'ctl-keys faint' }, el('kbd', { text: 'm' }), ' focus  ', el('kbd', { text: 'Ctrl' }), el('kbd', { text: 'Enter' }), ' send')), err);
    // One line until you write more: the box grows with its text, up to a third of the screen.
    const grow = () => { ta.style.height = 'auto'; ta.style.height = Math.min(ta.scrollHeight + 2, Math.round(window.innerHeight / 3)) + 'px'; };
    ta.addEventListener('input', grow);
    n.__ctl = { session, agentId: agentId || null, items: new Map(), opts };
    async function send() {
      const text = ta.value.trim();
      if (!text) { ta.focus(); return; }
      sendBtn.disabled = true;
      show(err, false);
      const res = await post('/api/control/message', { session, agent_id: agentId || null, text });
      sendBtn.disabled = false;
      if (res.status !== 200) { setText(err, (res.body && res.body.error) || 'Could not send (HTTP ' + res.status + ').'); show(err, true); return; }
      ta.value = '';
      grow();
      C.msgs = C.msgs.concat([res.body.message]);
      syncComposer(n);
      if (opts.onSent) opts.onSent(res.body.message);
      setTimeout(poll, 300);
    }
    sendBtn.addEventListener('click', send);
    ta.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) { e.preventDefault(); send(); }
      else if (e.key === 'Escape') { e.stopPropagation(); if (opts.onClose) opts.onClose(); else ta.blur(); }
    });
    n.focusInput = () => { const f = n.querySelector('.ctl-code'); if (C.scope !== 'control' && f && !f.disabled) f.focus(); else ta.focus(); };
    // The docked box sits at the bottom right where toasts stack: lift the toasts above it.
    if (!opts.pop && window.ResizeObserver) {
      const root = document.documentElement;
      const ro = new ResizeObserver(() => {
        if (!n.isConnected) { ro.disconnect(); root.style.removeProperty('--ctl-comp-h'); return; }
        root.style.setProperty('--ctl-comp-h', (n.hidden ? 0 : Math.ceil(n.getBoundingClientRect().height)) + 'px');
      });
      ro.observe(n);
      window.addEventListener('hashchange', () => { if (!n.isConnected) { ro.disconnect(); root.style.removeProperty('--ctl-comp-h'); } });
    }
    C.composers.add(n);
    syncComposer(n);
    return n;
  }
  function liveState(session, agentId) {
    const d = window.ccDashLive;
    if (!d) return '';
    const a = (d.agents || []).find((x) => x.session === session && (x.agent_id || null) === (agentId || null));
    return a ? a.state : '';
  }
  function syncComposer(n) {
    const c = n.__ctl;
    if (!n.isConnected && c.mounted) { C.composers.delete(n); return; }
    if (n.isConnected) c.mounted = true;
    show(n, C.on && C.paired);
    const list = n.querySelector('.ctl-msgs');
    const mine = C.msgs.filter((m) => m.session === c.session && (m.agent_id || null) === c.agentId && m.state !== 'lost').slice(c.opts.pop ? -3 : -8);
    const kids = mine.map((m) => {
      let it = c.items.get(m.id);
      if (!it) {
        const st = stPill('idle', '');
        const cancel = el('button', { type: 'button', class: 'ctl-x', text: 'Cancel', 'aria-label': 'Cancel this queued message', onclick: async () => { cancel.disabled = true; const r = await post('/api/control/cancel', { id: m.id }); if (r.status === 200) { setTimeout(poll, 100); } else cancel.disabled = false; } });
        it = el('li', { class: 'ctl-msg', dataset: { key: m.id } }, el('span', { class: 'num faint', text: clock(m.ts) }), el('span', { class: 'ctl-mtext', text: m.text, title: m.text }), st, cancel);
        it.__st = st; it.__cancel = cancel;
        c.items.set(m.id, it);
      }
      const [txt, cls] = msgState(m);
      setText(it.__st.lastChild, txt);
      if (it.__st.className !== 'pill s-' + cls) it.__st.className = 'pill s-' + cls;
      show(it.__cancel, m.state === 'queued');
      return it;
    });
    window.ccDom.reconcile(list, kids);
    show(list, kids.length > 0);
    const st = liveState(c.session, c.agentId);
    const queued = mine.some((m) => m.state === 'queued');
    // One short line; the full delivery rules are in the tooltip and docs/guides/commands.md.
    const idle = !['tool', 'generating', 'waiting', 'stalled'].includes(st);   // at its prompt, finished, or not on Live
    let hint, more;
    if (c.agentId) {
      hint = idle ? (queued ? 'Queued for this subagent’s next turn.' : 'At its prompt: a message waits for its next turn.') : 'Arrives after its next tool call, or when it finishes.';
      more = 'Delivered through the dash-control hook as extra context after the subagent’s next tool call or at its end.';
    } else {
      hint = idle ? (queued ? 'Queued for the session’s next turn.' : 'Starts a turn now when cc-dash can reach the session; otherwise waits for its next turn.') : 'Arrives after the running tool call, or when the turn ends.';
      more = 'Sent over the session’s inbox socket when cc-dash can reach it, otherwise through the dash-control hook after the next tool call, at turn end or with your next prompt. Claude sees it as a message from another session, not as a prompt you typed; a session in bypass mode holds it for approval for 5 minutes.';
    }
    const h = n.querySelector('.ctl-hint');
    setText(h, hint);
    if (h.title !== more) h.title = more;
  }

  // ── quick send popover (Live rows, drawer) ───────────────────────────
  let pop = null;
  function closePop() { if (!pop) return; const { node, anchor } = pop; pop = null; node.remove(); if (anchor && anchor.isConnected && anchor.focus) anchor.focus({ preventScroll: true }); }
  function quickSend(session, agentId, anchor) {
    if (!C.on || !C.paired) return;
    closePop();
    const comp = composer(session, agentId, { pop: true, onClose: closePop, onSent: () => setTimeout(closePop, 1400) });
    const node = el('div', { class: 'ctl-pop', role: 'dialog', 'aria-label': 'Quick message' },
      el('div', { class: 'ctl-pop-h' }, el('b', { text: agentId ? 'Message subagent' : 'Message session' }), el('button', { type: 'button', class: 'ctl-x', text: 'Close', onclick: closePop })), comp);
    document.body.append(node);
    const r = anchor && anchor.getBoundingClientRect ? anchor.getBoundingClientRect() : { left: 16, bottom: 80, right: 16 };
    const w = Math.min(380, window.innerWidth - 24);
    node.style.width = w + 'px';
    node.style.left = Math.max(12, Math.min(window.innerWidth - w - 12, r.right - w)) + 'px';
    const below = r.bottom + 6;
    node.style.top = (below + 220 > window.innerHeight ? Math.max(12, r.top - 226) : below) + 'px';
    pop = { node, anchor };
    comp.focusInput();
  }
  document.addEventListener('pointerdown', (e) => { if (pop && !pop.node.contains(e.target) && !(e.target.closest && e.target.closest('[data-ctl-msg]'))) closePop(); }, true);
  window.addEventListener('hashchange', closePop);

  // ── keyboard: y / n on a focused decision card, m for the message box ─
  window.addEventListener('keydown', (e) => {
    if (!C.on || !C.paired || e.metaKey || e.ctrlKey || e.altKey) return;
    const tag = (e.target.tagName || '').toLowerCase();
    if (['input', 'textarea', 'select'].includes(tag) || e.target.isContentEditable) return;
    const help = document.getElementById('help');
    if (help && help.open) return;
    const k = e.key;
    if (k === 'y' || k === 'n') {
      const card = e.target.closest && e.target.closest('.ctl-card[data-req]');
      if (!card) return;
      const s = card.parentElement && card.parentElement.__ctl;
      const r = C.reqs.get(card.dataset.req);
      if (!s || !s.parts || !r || r.status !== 'open') return;
      e.preventDefault(); e.stopImmediatePropagation();
      if (C.scope !== 'control') { const f = card.querySelector('.ctl-code'); if (f && !f.disabled) f.focus(); return; }
      const action = k === 'y' ? 'allow' : 'deny';
      const note = s.parts.armNote;
      if (C.arm && C.arm.id === r.id && C.arm.action === action && Date.now() < C.arm.until) {
        C.arm = null; show(note, false);
        (action === 'allow' ? s.parts.allow : s.parts.deny)();
        return;
      }
      C.arm = { id: r.id, action, until: Date.now() + 4000 };
      setText(note, action === 'allow' ? (r.input_truncated ? 'Too long to approve here: deny, or answer in the terminal.' : r.kind === 'question' ? 'Press y again to send the answer.' : r.guard ? 'Press y again to review the bash-guard confirm.' : 'Press y again to approve.') : (r.kind === 'plan' ? 'Press n again to write feedback.' : 'Press n again to deny.'));
      show(note, true);
      setTimeout(() => { if (C.arm && C.arm.id === r.id && Date.now() >= C.arm.until) { C.arm = null; show(note, false); } }, 4100);
      return;
    }
    if (k === 'm') {
      const c = [...C.composers].find((x) => x.isConnected && !x.__ctl.opts.pop && !x.hidden);
      if (c) { e.preventDefault(); e.stopImmediatePropagation(); c.focusInput(); c.scrollIntoView({ block: 'nearest' }); return; }
      const row = e.target.closest && e.target.closest('.arow');
      const btn = row ? row.querySelector('[data-ctl-msg]') : document.querySelector('.arow.sel [data-ctl-msg]');
      if (btn) { e.preventDefault(); e.stopImmediatePropagation(); quickSend(btn.dataset.ctlMsg, btn.dataset.ctlAgent || null, btn); }
    }
    if (k === 'Escape' && pop) { e.preventDefault(); e.stopImmediatePropagation(); closePop(); }
  }, true);

  // Waiting timers tick once a second.
  setInterval(() => {
    if (!C.on) return;
    const t = now();
    for (const n of document.querySelectorAll('.ctl-card:not(.resolved) [data-ctl-since]')) setText(n, 'waiting ' + waited(t - Number(n.dataset.ctlSince)));
  }, 1000);

  /* Status words shared with app.js and alerts.js: an idle session is "Your
     turn" (neutral); only a permission, question or plan is "Needs a decision". */
  const DECISION_KINDS = ['permission', 'question', 'plan'];
  window.ccControl = {
    on: () => C.on && C.paired,
    enabled: () => C.on,
    ready: () => C.ready,
    isDecision: (kind) => DECISION_KINDS.includes(kind),
    request: (id) => C.reqs.get(id) || null,
    requests: (session, agentId) => [...C.reqs.values()].filter((r) => r.session === session && (agentId === undefined || (r.agent_id || null) === (agentId || null))),
    slot, inline, lingerMs, detail, composer, quickSend,
  };
  init();
})();
