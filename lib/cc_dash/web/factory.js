/* cc-dash Factory floor: the whole floor as one live graph (GET /api/graph, graph.py).

   What it shows: every project with a live session or an open work order is a
   hub; its sessions, the agents they spawned (nested as they spawned), workflow
   runs and orders hang off it, joined the way they run (member, spawn, step and
   runs edges). It is the dashboard's landing view (the nav is Factory and
   Projects) and answers in one place what is working, what needs me, what it
   costs, where each order is.
   Token burn is light flowing down the spawn tree (particles in the target's
   model colour, drawn by graph.js); each hub's rim is its model mix.

   Pieces, all inside one stage (factory.css: the one non-flat surface):
     face(node)   each node's glass face: model colour, state glyph and word
                  (the app's status language), tool and target, time in the
                  current state, cost, effort, workflow progress, the order's
                  station stepper and rework count, the breathing amber halo on
                  anything that needs you; less at mid and far zoom, where
                  factory.css gives failed, stalled and needs-you weight instead;
                  a hub's name is fitted inside its disc at every zoom (hubName)
     HUD          payload.kpis as they come, so every figure adds up with the floor
     alerts       payload.alerts; a click or Enter selects, centres and focuses the node;
                  they hold still while focus is in them
                  (HUD and alerts are one panel across the top of the stage; the band
                  under the totals ends in the Activity toggle for the column)
     column       the right-hand slot when no node is selected (#fx-side, two frosted
                  panels): Recent actions, the newest 8 tool calls of payload.feed, newest
                  first, a click focuses the node, the rows (and their ages) hold still
                  under the pointer or focus (WCAG 2.2.2); then Last hour, three small
                  column charts of payload.series (new tokens by model family, tool calls
                  with the failed ones, working agents) in 3-minute buckets, always the
                  last 60 minutes whatever the window, each headed by the hour's figure;
                  hover or the arrow keys on a chart show a bucket in the shared tooltip,
                  and a hidden table has them all. On a short window the list shows fewer
                  whole rows (and scrolls) before the charts get small (colFit).
                  Shown by default wider than 900 px, a bottom sheet on a phone
     inspector    the selected node's data (what its session, agent or project page
                  knows), with links into those pages; a node that leaves the floor
                  keeps its last data and one line saying so; it takes the column's slot
                  (same width, so the floor never re-fits for a pick) and the column
                  comes back when it closes; opening it re-frames the floor so the node,
                  its parent and its children are clear of it (graph.js reframe)
     decisions    control mode only (cc-dash --control, window.ccControl): a session
                  or agent with an open permission, question or plan request gets
                  control.js's Review button (inline()) beside its alert and in its
                  inspector's "needs" box, which shows the request's own summary, not
                  the alert's text. Review opens control.js's review dialog with the
                  full card; nothing on the floor decides. The strip and the inspector
                  redraw on cc-control:state only when the open decisions change.
     zoom, list   + - fit; the List mode is the same tree as buttons (the
                  screen-reader path, and the only mode when graph.js is missing)
     toasts       alerts.js's toast stack keeps clear of the zoom buttons and of the
                  column or inspector while the floor is up (toasts(): html --al-dx,
                  --al-dy, .al-fx-away); the stack moves, the floor and its camera do not

   Data: app.js tick() calls poll() every 3 s while this view is open; poll()
   keeps the answer only for the scope it was asked for. update() patches every
   piece in place (ccDom keyed morph), so a poll never blinks, and hands nodes and
   edges to the engine only when the payload's sig changed; the engine diffs by id,
   so a state-only poll moves nothing. index() builds the one tree both use
   (first node per id wins; a missing parent, a self-parent or the link closing
   a cycle makes a root), and every walk up it is cycle-proof. An answer that
   is not a graph fails like an HTTP error: the last good floor stays and the
   app shows the failure. A node's aria-label is its kind, name and status
   only, so a poll does not re-announce it. Selection lives here (st.sel); the engine
   draws it. g.setInsets() keeps fit, centre and framing clear of that panel,
   the column or the inspector and the zoom buttons. The charts redraw only when
   their data changed (ctx.chart signatures). Fields read: §5.7 of
   docs/superpowers/specs/2026-10-25-software-factory-design.md, nothing else.
   All transcript text goes in through textContent (ctx.h).
   Exposes window.ccFactory = { init(ctx), graph(), focus(id) }. */
'use strict';
(() => {
  let C = null;
  const MODES = ['graph', 'list'];
  const MODE_KEY = 'cc-dash-factory-mode';
  const RUNNING = ['tool', 'generating', 'waiting'];
  const FAMS = ['opus', 'sonnet', 'haiku', 'fable', 'other'];
  // Node boxes in world px at zoom 1 (the engine sizes, collides and hit-tests with these).
  const KINDS = {
    project: { w: 128, h: 128, shape: 'circle', hub: true },
    session: { w: 232, h: 72 },
    agent: { w: 200, h: 58 },
    workflow: { w: 212, h: 64 },
    order: { w: 208, h: 66, orbit: true },
  };
  const ORDER_FLOW = ['queued', 'spec', 'approved', 'building', 'verifying', 'review', 'done', 'blocked', 'rejected', 'failed'];
  const ORDER_WORD = { queued: 'Queued', spec: 'Spec', approved: 'Approved', building: 'Building', verifying: 'Verifying', review: 'In review',
    done: 'Done', blocked: 'Blocked', rejected: 'Rejected', failed: 'Failed' };
  const KIND_NAME = { project: 'Project', session: 'Session', agent: 'Agent', workflow: 'Workflow run', order: 'Work order' };
  const st = {
    g: null, el: null, picker: null, ro: null,
    mode: 'graph', sel: null, open: false, last: null, goneAt: null,
    d: null, byId: new Map(), par: new Map(), kids: new Map(), roots: [], order: [], alertBy: new Map(), appliedScope: null,
    projects: new Map(), scope: { p: '', all: false }, err: null,
    dataKey: null, insetSig: '', seenFeed: new Set(), feedReady: false, shown: null, closeT: 0, sideT: 0, hold: null,
    dec: new Map(), decSig: '', toastSig: '', onScroll: null, wide: null, onWide: null,
  };
  const freshHold = () => ({ acts: { ptr: false, foc: false, d: null }, alert: { ptr: false, foc: false, d: null } });
  /* The Activity toggle's last press (true shows the column, false hides it), kept for the page's
     life; null until the first press, and then the column follows the width (shown past 900 px). */
  let sidePref = null;
  const WIDE = '(min-width: 901px)';

  // ── small helpers ─────────────────────────────────────────────────────
  const enc = encodeURIComponent;
  const fam = (n) => (n && FAMS.includes(n.family) ? n.family : 'other');
  const scopeKey = (r) => (r.p || '') + '\u0000' + (r.all ? '1' : '');
  function hashFor(p, all) {
    const q = [];
    if (p) q.push('p=' + enc(p));
    if (all) q.push('all=1');
    return '#/factory' + (q.length ? '?' + q.join('&') : '');
  }
  function hash() {
    const r = C.route();
    const sc = r.view === 'factory' ? { p: r.p || '', all: !!r.all } : st.scope;
    return hashFor(sc.p, sc.all);
  }
  const days = () => { const w = st.d && st.d.window_days; return w === 1 ? '1 day' : (w || 7) + ' days'; };
  const dot = () => C.h('span', { class: 'fx-dot', 'aria-hidden': 'true', text: '·' });
  const sinceEl = (t, cls) => C.h('span', { class: 'num ' + (cls || 'faint'), dataset: { since: t }, text: C.dur(C.nowS() - t) });
  const agoEl = (t, cls) => C.h('span', { class: 'num ' + (cls || 'faint'), dataset: { ago: t }, text: C.ago(t) });
  /* The burn rate: dollars a minute, else new tokens a minute (input, cache writes, output).
     Never tok_min: it counts cache reads, which are never added into a token figure here. */
  function rate(b) {
    if (!b) return '';
    if (b.cost_min > 0) return money(b.cost_min) + '/min';
    return b.new_min > 0 ? compact(b.new_min) + ' new tok/min' : '';
  }
  const explicit = (n) => ['recorded', 'requested'].includes(n.effort_source);
  const effort = (n, bare) => (n.effort ? C.effortTag({ value: n.effort, source: n.effort_source || 'default', explicit: explicit(n) }, { bare }) : null);
  /* Payload figures as numbers: a string or junk figure renders as n/a instead of throwing in a face. */
  const num = (v) => (typeof v === 'number' ? (Number.isFinite(v) ? v : null)
    : typeof v === 'string' && v.trim() !== '' && Number.isFinite(+v) ? +v : null);
  const money = (v, precise) => C.money(num(v), precise);
  const compact = (v) => C.compact(num(v));
  /* The floor's new tokens a minute, summed over its sessions as graph.py sums kpis.burn_cost_min
     (the HUD's fallback for an unpriced floor). kpis.burn_tok_min counts cache reads: never shown. */
  function floorNewMin() {
    let t = 0;
    for (const n of st.byId.values()) if (n.kind === 'session' && n.burn) t += num(n.burn.new_min) || 0;
    return t;
  }
  /* n, then its parents up to its root. On the floor the parents are index()'s (cycles cut, as
     graph.js cuts them); a node that left the floor starts from its own parent. Cycle-proof. */
  function lineage(n) {
    const out = [], seen = new Set();
    for (let x = n; x && !seen.has(x.id);) {
      seen.add(x.id);
      out.push(x);
      const pid = st.byId.get(x.id) === x ? st.par.get(x.id) : x.parent;
      x = pid != null ? st.byId.get(pid) : null;
    }
    return out;
  }
  /* The session a node belongs to (its own detail.session, or the nearest session above it). */
  function sessionOf(n) {
    for (const x of lineage(n)) if (x.detail && x.detail.session) return x.detail.session;
    return null;
  }
  const projectOf = (n) => lineage(n).find((x) => x.kind === 'project') || null;
  function pageFor(n) {
    if (!n) return null;
    if (n.kind === 'project') return C.areaHash('overview', n.label);
    if (n.kind === 'order') { const p = projectOf(n); return p ? C.areaHash('overview', p.label) : null; }
    const sid = sessionOf(n);
    if (!sid) return null;
    return '#/s/' + enc(sid) + (n.kind === 'agent' && n.detail && n.detail.agent_id ? '/a/' + enc(n.detail.agent_id) : '');
  }

  // ── decisions (control mode) ─────────────────────────────────────────
  /* control.js when control mode is on and paired, else null: then nothing on the floor offers Review. */
  const ctl = () => (window.ccControl && window.ccControl.on() ? window.ccControl : null);
  /* A session's (its main thread's) or an agent's oldest open permission, question or plan request. */
  function decisionOf(n) {
    const c = ctl();
    if (!c || !n || (n.kind !== 'session' && n.kind !== 'agent')) return null;
    const sid = sessionOf(n), aid = n.kind === 'agent' ? (n.detail && n.detail.agent_id) || null : null;
    if (!sid || (n.kind === 'agent' && !aid)) return null;
    let best = null;
    for (const r of c.requests(sid, aid)) if (r.status === 'open' && c.isDecision(r.kind) && (!best || r.ts < best.ts)) best = r;
    return best;
  }
  /* st.dec: node id -> its open decision, for the nodes on the floor. Returns the signature that says
     when the strip and the inspector must be drawn again (control state comes every second). */
  function decisions() {
    const dec = new Map();
    if (ctl()) for (const n of st.byId.values()) { const r = decisionOf(n); if (r) dec.set(n.id, r); }
    st.dec = dec;
    return [...dec].map(([id, r]) => id + '=' + r.id).join('|') + (document.documentElement.classList.contains('ctl-locked') ? '#locked' : '');
  }

  // ── the status language (app.css "One status language") ──────────────
  /* One category per node: working · needs · turn · stalled · done · failed · blocked · idle. */
  function cat(n) {
    if (n.kind === 'project') return n.state === 'needs' ? 'needs' : n.state === 'working' ? 'working' : 'idle';
    if (n.kind === 'workflow') return n.state === 'running' ? 'working' : n.state === 'failed' ? 'failed' : n.state === 'done' ? 'done' : 'idle';
    if (n.kind === 'order') {
      const dt = n.detail || {};
      if (dt.gate && dt.gate.waiting) return 'needs';
      if (['building', 'verifying', 'review'].includes(n.state)) return 'working';
      if (n.state === 'done') return 'done';
      if (n.state === 'failed') return 'failed';
      if (n.state === 'blocked') return 'blocked';
      return 'idle';
    }
    if (n.needs) return n.needs === 'idle' ? 'turn' : 'needs';
    if (RUNNING.includes(n.state)) return 'working';
    if (n.state === 'idle') return 'turn';
    return ['done', 'failed', 'stalled'].includes(n.state) ? n.state : 'idle';
  }
  function glyph(c) {
    const v = C.glyph(c === 'blocked' ? 'failed' : c === 'turn' ? 'idle' : c);
    if (c === 'blocked' || c === 'turn') v.setAttribute('class', 'sg sg-' + c);
    return v;
  }
  const PILL = { working: ['working', 'Working'], needs: ['decide', 'Needs a decision'], turn: ['turn', 'Your turn'], stalled: ['stalled', 'Stalled'],
    done: ['done', 'Done'], failed: ['failed', 'Failed'], blocked: ['blocked', 'Blocked'], idle: ['idle', 'Idle'] };
  function pill(n) {
    const c = cat(n);
    if (n.kind === 'order') {
      const dt = n.detail || {};
      if (c === 'needs') return C.stPill('decide', cap(dt.gate.name) + ' gate: waiting for you');
      return C.stPill(PILL[c][0], ORDER_WORD[n.state] || n.state);
    }
    if (n.kind === 'project' && c === 'needs') return C.stPill('decide', 'Needs you');
    if (n.kind !== 'project' && n.kind !== 'workflow' && c === 'done') return C.stPill('done', 'Finished');
    if (n.kind === 'workflow' && c === 'working') return C.stPill('working', 'Running');
    return C.stPill(...PILL[c]);
  }
  const cap = (x) => String(x || '').charAt(0).toUpperCase() + String(x || '').slice(1);
  function needsText(kind, tool) {
    if (kind === 'permission') return 'approve ' + (tool || 'a tool call');
    if (kind === 'question') return 'answer a question';
    if (kind === 'plan') return 'review the plan';
    if (kind === 'idle') return 'waiting for your input';
    if (kind === 'error') return 'an error';
    return 'needs attention';
  }
  const ALERT_WORD = { permission: 'Permission', question: 'Question', plan: 'Plan', idle: 'Your turn', error: 'Error' };
  /* A feed entry's status: [data-status, the word, the glyph] (the app's status language). */
  const EV = { pending: ['running', 'running', 'working'], running: ['running', 'running', 'working'], ok: ['ok', 'ok', 'done'],
    error: ['error', 'failed', 'failed'], blocked: ['error', 'blocked', 'blocked'] };
  /* The words for what a session or agent is doing now (the session pages' vocabulary). */
  function doing(n) {
    const dt = n.detail || {};
    if (n.needs) { const al = st.alertBy.get(n.id); return n.needs === 'idle' ? 'Your turn' : 'Needs a decision: ' + needsText(n.needs, al && al.tool); }
    if (n.state === 'tool') return 'Running ' + (n.tool || 'a tool');
    if (n.state === 'generating') return n.tool_calls || n.last_text || dt.tool_calls ? 'Thinking' : 'Starting';
    if (n.state === 'waiting') {
      const w = n.kind === 'agent' ? dt.waiting_on : dt.running;
      return w ? 'Waiting on ' + C.plural(w, 'agent') : 'Waiting on its subagents';
    }
    if (n.state === 'idle') return 'Your turn';
    if (n.state === 'done') return 'Finished';
    if (n.state === 'failed') return 'Failed';
    if (n.state === 'stalled') return 'Stalled?';
    return cap(n.state);
  }
  /* A short state word for any kind (list rows, aria labels). */
  function word(n) {
    if (n.kind === 'session' || n.kind === 'agent') return doing(n);
    if (n.kind === 'project') return n.state === 'needs' ? 'needs you' : n.state === 'working' ? 'working' : (n.detail && n.detail.sessions_live ? 'idle' : 'dormant');
    if (n.kind === 'workflow') return n.state === 'running' ? 'running' : n.state;
    const dt = n.detail || {};
    if (dt.gate && dt.gate.waiting) return cap(dt.gate.name) + ' gate: waiting for you';
    const stn = (dt.stations || []).find((x) => x.name === dt.station);
    return (ORDER_WORD[n.state] || n.state) + (stn && n.state !== 'done' ? ' · ' + stn.title : '');
  }
  function figure(n) {
    if (n.kind === 'workflow') { const dt = n.detail || {}; return (dt.finished || 0) + ' of ' + (dt.total || 0); }
    if (n.kind === 'order') return (n.detail && n.detail.id) || '';
    return money(n.cost);
  }
  function phaseOf(n) {
    const dt = n.detail || {};
    const run = (st.kids.get(n.id) || []).map((id) => st.byId.get(id)).filter((x) => x && RUNNING.includes(x.state) && x.detail && x.detail.phase);
    if (run.length) return run[run.length - 1].detail.phase;
    const ph = dt.phases || [];
    return n.state === 'running' && ph.length && dt.finished < ph.length ? ph[dt.finished] : '';
  }

  // ── project hub names, fitted inside the disc ─────────────────────────
  /* A hub's name stays inside its disc at every level of detail (each has its
     own variant; CSS shows the one for host[data-lod]): one line at the level's
     size, else two lines broken after a hyphen, underscore, dot or slash or at a
     space (the most even split that fits), stepping down the type scale to
     11 px; past that, two lines cut where they must (at a break when one is
     near, else anywhere) with an ellipsis ending the second. The full name
     stays in the node's aria-label and the name's title.
     Geometry: the disc is KINDS.project.w across; the name keeps HUB_IN px
     inside its edge (the 7.5 px rim, factory.css .fx-rim, and 3 px of air);
     under the name each level stacks what factory.css shows there: the live
     line and the cost at near (2 + 16 + 2 + 16 px), the live line at mid
     (2 + 16), nothing at far. Every corner of every line box must lie inside. */
  const HUB_IN = 10.5;
  const HUB_LODS = [['near', [14, 13, 12, 11], 36], ['mid', [20, 16, 14, 13, 12, 11], 18], ['far', [36, 28, 24, 20, 16, 14, 13, 12, 11], 0]];
  const hubPlans = new Map();
  let measure = null, family = '', monoFamily = '';
  const widths = new Map();   // font + text -> px (faces measure the same few words over and over)
  /* Width of s at fs px: weight 600 in the body face by default, else at weight w, in the mono face if mono. */
  function textW(s, fs, w, mono) {
    if (!measure) {
      measure = document.createElement('canvas').getContext('2d');
      family = getComputedStyle(document.body).fontFamily || 'system-ui, sans-serif';
      monoFamily = getComputedStyle(document.documentElement).getPropertyValue('--mono').trim() || 'monospace';
    }
    const font = (w || 600) + ' ' + fs + 'px ' + (mono ? monoFamily : family), k = font + '\u0000' + s;
    let v = widths.get(k);
    if (v == null) {
      if (widths.size > 4000) widths.clear();
      measure.font = font;
      widths.set(k, v = measure.measureText(s).width + 1);   // ignores the -.012em tracking: errs wide
    }
    return v;
  }
  /* The widest line i of n (line height L, `below` px stacked under them) may be. */
  function hubRoom(i, n, L, below) {
    const R = KINDS.project.w / 2 - HUB_IN, y0 = -(n * L + below) / 2 + i * L;
    const y = Math.max(Math.abs(y0), Math.abs(y0 + L));
    return 2 * Math.sqrt(Math.max(0, R * R - y * y));
  }
  function clipTo(t, room, fs) {
    if (textW(t, fs) <= room) return t;
    let lo = 0, hi = t.length;
    while (lo < hi) { const m = (lo + hi + 1) >> 1; if (textW(t.slice(0, m) + '…', fs) <= room) lo = m; else hi = m - 1; }
    return t.slice(0, lo).replace(/[\s\-_./]+$/, '') + '…';
  }
  function hubPlan(s, sizes, below) {
    const cuts = [];
    for (let i = 1; i < s.length - 1; i++) {
      if (s[i] === ' ') cuts.push([s.slice(0, i), s.slice(i + 1)]);
      else if ('-_./'.includes(s[i])) cuts.push([s.slice(0, i + 1), s.slice(i + 1)]);
    }
    for (const fs of sizes) {
      const L = Math.round(fs * 1.2);
      if (textW(s, fs) <= hubRoom(0, 1, L, below)) return { fs, L, lines: [s] };
      let best = null;
      for (const [a, b] of cuts) {
        const wa = textW(a, fs), wb = textW(b, fs);
        if (wa <= hubRoom(0, 2, L, below) && wb <= hubRoom(1, 2, L, below) && (!best || Math.max(wa, wb) < best.m)) best = { m: Math.max(wa, wb), lines: [a, b] };
      }
      if (best) return { fs, L, lines: best.lines };
    }
    const fs = sizes[sizes.length - 1], L = Math.round(fs * 1.2), r0 = hubRoom(0, 2, L, below);
    let p = 0;
    while (p < s.length && textW(s.slice(0, p + 1), fs) <= r0) p++;
    let cut = p;
    for (let i = p - 1; i >= Math.ceil(p * 0.6); i--) if ('-_./ '.includes(s[i])) { cut = i + 1; break; }
    return { fs, L, lines: [s.slice(0, cut).trim(), clipTo(s.slice(cut).trim(), hubRoom(1, 2, L, below), fs)] };
  }
  function hubName(label) {
    const s = String(label || '');
    let plans = hubPlans.get(s);
    if (!plans) {
      plans = HUB_LODS.map(([lod, sizes, below]) => [lod, hubPlan(s, sizes, below)]);
      if (hubPlans.size > 200) hubPlans.clear();
      hubPlans.set(s, plans);
    }
    return C.h('span', { class: 'fx-hub-name', title: s }, plans.map(([lod, p]) => C.h('span', { class: 'fx-hn', dataset: { lod }, style: { '--fs': p.fs + 'px', '--lh': p.L + 'px' } },
      p.lines.map((t) => C.h('span', { text: t })))));
  }

  // ── node faces (inside the engine's .gx-node) ─────────────────────────
  const name = (n) => C.h('span', { class: 'fx-name', text: n.label || n.id });
  /* Workflow progress: one segment per agent, done ink, running green, failed red. */
  function segs(dt) {
    const total = Math.max(dt.total || 0, (dt.finished || 0) + (dt.running || 0) + (dt.failed || 0));
    const out = [];
    for (let i = 0; i < Math.min(total, 24); i++) {
      const s = i < dt.finished ? 'done' : i < dt.finished + dt.failed ? 'failed' : i < dt.finished + dt.failed + dt.running ? 'run' : 'todo';
      out.push(C.h('i', { dataset: { s } }));
    }
    return C.h('div', { class: 'fx-segs', 'aria-hidden': 'true' }, out);
  }
  /* The order's line: the spec gate, its stations, the merge gate; the waiting gate amber. */
  function steps(dt) {
    const g = dt.gate;
    const gate = (where) => (g && g.name === where ? C.h('span', { class: 'fx-step fx-gate', dataset: { status: g.waiting ? 'waiting' : 'pending' }, title: cap(g.name) + ' gate' + (g.waiting ? ': waiting for you' : '') }) : null);
    return C.h('div', { class: 'fx-steps', 'aria-hidden': 'true' }, gate('spec'),
      (dt.stations || []).map((x) => C.h('span', { class: 'fx-step', dataset: { status: x.status || 'pending' }, title: x.title + ': ' + (x.status || 'pending') })), gate('merge'));
  }
  /* The face's few words for an open decision (the inspector and the aria label say it in full). */
  function needsShort(n) {
    const al = st.alertBy.get(n.id);
    if (n.needs === 'permission') return 'Approve ' + ((al && al.tool) || 'a tool call') + '?';
    if (n.needs === 'question') return 'Has a question for you';
    if (n.needs === 'plan') return 'Plan to review';
    return 'Needs you';
  }
  /* What a session or agent is doing, line 2's first item. The target carries data-cut: the levels
     of detail where it goes while the time in state shows (sinceFit). */
  function activity(n, cut) {
    const c = cat(n);
    const tgt = (text, mono) => C.h('span', { class: (mono ? 'mono ' : '') + 'fx-tgt', text, dataset: cut ? { cut } : null });
    if (n.needs && n.needs !== 'idle') return C.h('span', { class: 'fx-act fx-warn', text: needsShort(n) });
    if (n.state === 'tool') return C.h('span', { class: 'fx-act' }, C.h('b', { class: 'mono', text: n.tool || 'tool' }), n.target ? tgt(n.target, true) : null);
    if (n.state === 'stalled') return C.h('span', { class: 'fx-act fx-warn', text: 'Stalled?' + (n.tool ? ' ' + n.tool : '') });
    return C.h('span', { class: 'fx-act' + (c === 'failed' ? ' bad' : '') }, doing(n), n.state === 'waiting' && n.target ? tgt(n.target, false) : null);
  }
  /* Time in the current state. A working or your-turn session or agent shows "· 4m" right after
     what it is doing once it has been in that state (`since`) a minute, in whole minutes. It keeps
     counting between polls without a face repaint (a quiet floor's sig holds still, so face() does
     not run): sinceTick(), every 5 s while the floor is up, shows the span when it crosses the
     minute and rewrites only its text. Near and mid zoom only (line 2 is gone at far). The state
     words are never cut for it: sinceFit() measures line 2 at each level of detail and leaves the
     time out where the words and the figures already fill it, and the tool's target out (while the
     time shows) where fewer than TGT_MIN of its characters would be left. The inspector's "now" box
     says the same to the second; the time is aria-hidden, so a focused node is not re-announced. */
  const TGT_MIN = 8;
  const SINCE_LODS = [['near', 12, 11, false], ['mid', 15, 14, true]];   // level, text px, mono px, cost on line 2 (factory.css)
  let sinceTimer = 0;
  function sinceText(t) {
    const m = Math.max(0, Math.floor((C.nowS() - t) / 60));
    return m < 60 ? m + 'm' : Math.floor(m / 60) + 'h ' + String(m % 60).padStart(2, '0') + 'm';
  }
  function sinceTick() {
    if (!st.el) { clearInterval(sinceTimer); sinceTimer = 0; return; }
    if (document.hidden || st.mode !== 'graph') return;
    for (const el of st.el.graph.querySelectorAll('.fx-since')) {
      const t = num(el.dataset.t), on = t != null && C.nowS() - t >= 60;
      if (el.hidden === on) el.hidden = !on;
      if (on) P().setText(el.lastChild, sinceText(t));
    }
  }
  /* The levels where line 2 has no room for the time (drop), and where the target goes while it shows (cut). */
  function sinceFit(n, t) {
    const room0 = (KINDS[n.kind] || KINDS.agent).w - 31;   // the 1px border and the 17 + 12 px padding of .fx-node
    const tool = n.state === 'tool', target = (tool || n.state === 'waiting') && n.target ? String(n.target) : '';
    const words = tool ? n.tool || 'tool' : doing(n);
    const label = '· ' + (C.nowS() - t < 3600 ? '59m' : sinceText(t));
    const drop = [], cut = [];
    for (const [lod, fs, ms, cost] of SINCE_LODS) {
      let used = (tool ? textW(words, ms, 500, true) + 6 : textW(words, fs, 400)) + 6 + textW(label, fs, 400) + 4;
      if (!cost && n.kind === 'agent' && explicit(n) && n.effort) used +=6 + textW(String(n.effort), 11, 500, true) + 10;   // the effort tag
      if (cost) used += 6 + textW(money(n.cost), fs, 400) + 6;                                                     // the cost (.fx-fig2)
      if (used > room0) drop.push(lod);
      else if (target && room0 - used - (tool ? 0 : 6) < textW(target.length > TGT_MIN ? target.slice(0, TGT_MIN) + '…' : target, tool ? ms : fs, 400, tool)) cut.push(lod);
    }
    return { drop: drop.join(' ') || null, cut: cut.join(' ') || null };
  }
  function sinceTag(fit, t) {
    if (!sinceTimer) sinceTimer = setInterval(sinceTick, 5000);
    const on = C.nowS() - t >= 60;
    return C.h('span', { class: 'fx-since num', 'aria-hidden': 'true', hidden: !on, dataset: fit.drop ? { t, drop: fit.drop } : { t } },
      C.h('span', { class: 'fx-sep', text: '·' }), C.h('span', { text: on ? sinceText(t) : '' }));
  }
  /* An order sent back round its line (detail.rework): "rework 2" at the end of its stepper, only
     when it went back at least once. Near and mid zoom (line 2 is gone at far); the inspector's
     Rework row says it in full. */
  function reworkEl(v) {
    const k = num(v);
    return k > 0 ? C.h('span', { class: 'fx-rework num', 'aria-hidden': 'true', title: 'Sent back round its line ' + C.plural(k, 'time'), text: 'rework ' + C.int(k) }) : null;
  }
  function face(n) {
    const { h } = C;
    const c = cat(n), dt = n.detail || {};
    const cls = 'fx-node k-' + n.kind + ' f-' + fam(n) + ' st-' + n.state + ' c-' + c + (n.needs ? ' needs' : '')
      + (n.kind === 'project' && !dt.sessions_live ? ' dormant' : '');
    const halo = n.needs && n.needs !== 'idle' ? h('span', { class: 'fx-halo', 'aria-hidden': 'true' }) : null;
    if (n.kind === 'project') {
      const mix = (dt.model_mix || []).filter((m) => (m.cost || m.tokens) > 0);
      const useCost = mix.length && mix.every((m) => m.cost != null) && mix.some((m) => m.cost > 0);
      const tot = mix.reduce((a, m) => a + ((useCost ? m.cost : m.tokens) || 0), 0);
      let acc = 0;
      const stops = tot ? mix.map((m) => {
        const a = acc / tot * 100, b = (acc += (useCost ? m.cost : m.tokens) || 0) / tot * 100;
        return 'var(--' + (FAMS.includes(m.family) ? m.family : 'other') + ') ' + a.toFixed(2) + '% ' + b.toFixed(2) + '%';
      }) : [];
      return h('div', { class: cls }, halo,
        h('span', { class: 'fx-rim', 'aria-hidden': 'true', style: stops.length ? { '--mix': 'conic-gradient(from -90deg, ' + stops.join(', ') + ')' } : null }),
        h('div', { class: 'fx-hub' },
          hubName(n.label),
          h('span', { class: 'fx-hub-meta fx-l2' }, glyph(c), (dt.sessions_live || 0) + ' live'),
          h('span', { class: 'fx-hub-cost fx-l2', text: money(dt.cost_window != null ? dt.cost_window : n.cost) + ' · ' + days() })));
    }
    if (n.kind === 'workflow') {
      const ph = phaseOf(n);
      return h('div', { class: cls },
        h('div', { class: 'fx-l1' }, glyph(c), h('span', { class: 'fx-kind', text: 'Workflow' }), name(n), h('span', { class: 'fx-fig num', text: (dt.finished || 0) + '/' + (dt.total || 0) })),
        h('div', { class: 'fx-l2' }, segs(dt)),
        h('div', { class: 'fx-l3' }, h('span', { class: 'fx-act' }, ph ? h('b', { text: ph }) : null,
          (ph ? ' · ' : '') + (dt.running ? dt.running + ' running' : n.state === 'running' ? 'running' : cap(n.state)) + (dt.failed ? ', ' + dt.failed + ' failed' : ''))));
    }
    if (n.kind === 'order') {
      const gateWait = dt.gate && dt.gate.waiting;
      return h('div', { class: cls },
        h('div', { class: 'fx-l1' }, glyph(c), h('span', { class: 'fx-oid mono', text: dt.id || '' }), name(n)),
        h('div', { class: 'fx-l2' }, steps(dt), reworkEl(dt.rework)),
        h('div', { class: 'fx-l3' }, h('span', { class: 'fx-act' + (gateWait ? ' fx-warn' : ''), text: word(n) }), dt.priority ? h('span', { class: 'tag', text: dt.priority }) : null));
    }
    // session and agent
    // The cost sits on line 1 up close; at mid zoom (larger type) it moves to the end of line 2, so the name keeps the width.
    const top = h('div', { class: 'fx-l1' }, glyph(c), name(n), h('span', { class: 'fx-fig num', text: money(n.cost) }));
    const cost2 = h('span', { class: 'fx-fig fx-fig2 num', 'aria-hidden': 'true', text: money(n.cost) });
    const t = num(n.since), fit = t != null && (c === 'working' || c === 'turn') ? sinceFit(n, t) : null;
    const act = activity(n, fit && fit.cut), since = fit ? sinceTag(fit, t) : null;
    if (n.kind === 'agent') {
      return h('div', { class: cls }, halo, top, h('div', { class: 'fx-l2' }, act, since, explicit(n) ? effort(n, true) : null, cost2));
    }
    const r = RUNNING.includes(n.state) ? rate(n.burn) : '';
    return h('div', { class: cls }, halo, top, h('div', { class: 'fx-l2' }, act, since, cost2),
      h('div', { class: 'fx-l3' }, h('span', { class: 'fx-model' }, C.key(fam(n)), n.model || 'no model yet'), explicit(n) ? effort(n, true) : null,
        r ? h('span', { class: 'fx-rate num', text: r }) : null));
  }
  /* A node's accessible name: what it is, its status, and what it needs if anything. Nothing that
     ticks (cost, tool, target, progress), so a focused node is not re-announced every poll; the
     inspector and List mode carry the rest. */
  const STATUS = { working: 'working', turn: 'your turn', stalled: 'stalled', done: 'finished', failed: 'failed', blocked: 'blocked', idle: 'idle' };
  function nodeLabel(n) {
    const dt = n.detail || {};
    let status;
    if (n.kind === 'project' || n.kind === 'workflow') status = word(n);
    else if (n.kind === 'order') status = dt.gate && dt.gate.waiting ? cap(dt.gate.name) + ' gate: waiting for you' : (ORDER_WORD[n.state] || n.state);
    else if (n.needs && n.needs !== 'idle') { const al = st.alertBy.get(n.id); status = 'needs you: ' + needsText(n.needs, al && al.tool); }
    else status = STATUS[cat(n)];
    return [n.kind === 'order' ? dt.id : null, (KIND_NAME[n.kind] || 'Node') + ' ' + (n.label || n.id), status].filter(Boolean).join(', ');
  }
  function edgeStyle(e, src, tgt) {
    const model = tgt && (tgt.kind === 'session' || tgt.kind === 'agent');
    return { color: model ? '--' + fam(tgt) : '--fx-edge-neutral', width: e.active ? 1.6 : 1, dash: e.kind === 'runs' ? [4, 5] : null };
  }

  // ── data ──────────────────────────────────────────────────────────────
  function poll() {
    const r = C.route();
    const p = r.p || '', all = !!r.all, k = scopeKey(r);
    const q = [p ? 'project=' + enc(p) : null, all ? 'all=1' : null].filter(Boolean).join('&');
    return C.api('/api/graph' + (q ? '?' + q : '')).then((d) => {
      // Anything but a graph goes the way of a failed request: the last good floor stays, the error shows.
      if (!d || typeof d !== 'object' || !Array.isArray(d.nodes)) throw new Error('/api/graph answered with ' + (d == null ? 'something that is not JSON' : 'no node list'));
      const now = C.route();
      if (now.view === 'factory' && scopeKey(now) !== k) return;   // the scope changed while this was in flight
      C.S.data.graph = d; C.S.data.graphScope = k; st.err = null;
      if (now.view === 'factory') C.update();
    }).catch((e) => { st.err = e; if (C.route().view === 'factory') C.update(); throw e; });
  }
  /* The tree the view and the engine share. The first node with an id wins (as in graph.js);
     a missing parent, a self-parent or the link that closes a cycle makes a root (graph.js
     cuts cycles the same way), so every node is reached and every walk ends. */
  function index(d) {
    const byId = new Map(), nodes = [];
    for (const n of d.nodes || []) if (n && typeof n === 'object' && n.id != null && !byId.has(n.id)) { byId.set(n.id, n); nodes.push(n); }
    const par = new Map();
    for (const n of nodes) par.set(n.id, n.parent != null && n.parent !== n.id && byId.has(n.parent) ? n.parent : null);
    for (const n of nodes) {
      let p = par.get(n.id), steps = 0;
      while (p != null && p !== n.id && steps++ <= nodes.length) p = par.get(p);
      if (p === n.id) par.set(n.id, null);
    }
    const kids = new Map(), roots = [], order = [];
    for (const n of nodes) {
      const p = par.get(n.id);
      if (p == null) roots.push(n.id);
      else { if (!kids.has(p)) kids.set(p, []); kids.get(p).push(n.id); }
    }
    const walk = (id) => { order.push(id); for (const c of kids.get(id) || []) walk(c); };
    roots.forEach(walk);
    st.byId = byId; st.par = par; st.kids = kids; st.roots = roots; st.order = order;
    st.alertBy = new Map();
    for (const a of d.alerts || []) if (!st.alertBy.has(a.node)) st.alertBy.set(a.node, a);
  }
  /* The picker lists every project seen: an unscoped poll replaces the list, a scoped one updates its entry. */
  function rememberProjects(d) {
    if (!d.scope || !d.scope.project) st.projects = new Map();
    for (const n of st.byId.values()) if (n.kind === 'project') st.projects.set(n.label, { project: n.label, sessions: (n.detail && n.detail.sessions_live) || 0 });
  }

  // ── the column's last hour (payload.series) ───────────────────────────
  /* 20 buckets of bucket_secs (180), clock-aligned, oldest first; the last one holds now and is
     still filling. A measure that is not 20 non-negative numbers is junk and its chart shows the
     empty state, as every chart does for a payload with no series (an older server). */
  const SLOTS = 20;
  const hourArr = (v) => (Array.isArray(v) && v.length === SLOTS && v.every((x) => typeof x === 'number' && Number.isFinite(x) && x >= 0) ? v.map((x) => Math.round(x)) : null);
  function hourOf(d) {
    const s = d && d.series && typeof d.series === 'object' && !Array.isArray(d.series) ? d.series : {};
    const bs = num(s.bucket_secs) > 0 ? num(s.bucket_secs) : 180;
    const now = num(d && d.now) || C.nowS();
    const start = num(s.start) != null ? num(s.start) : (Math.floor(now / bs) + 1 - SLOTS) * bs;
    const byFam = s.tokens_by_family && typeof s.tokens_by_family === 'object' ? s.tokens_by_family : {};
    const fams = {};
    for (const f of FAMS) { const a = hourArr(byFam[f]); if (a && a.some((x) => x > 0)) fams[f] = a; }
    const calls = hourArr(s.tool_calls), errs = hourArr(s.tool_errors), at = num(s.agents_total);
    return { bs, start, tokens: hourArr(s.tokens), fams, calls, errs: calls ? calls.map((c, i) => (errs ? Math.min(errs[i], c) : 0)) : null,
      agents: hourArr(s.agents), agentsTotal: at != null && at >= 0 ? Math.round(at) : null };
  }
  /* The top of a chart's own scale: the first of 1, 1.5, 2, 2.5, 3, 4, 5, 6, 8 (x 10^n) at or above
     the peak, whole for counts. */
  function niceTop(max, whole) {
    const e = Math.pow(10, Math.floor(Math.log10(max)));
    for (const m of [1, 1.5, 2, 2.5, 3, 4, 5, 6, 8]) { const t = m * e; if (t >= max && (!whole || Number.isInteger(t))) return t; }
    return 10 * e;
  }
  /* The three charts as data: per bucket its total and its stacked segments (bottom up, each a
     class that names its colour), the tooltip's rows, the header figures, the summary sentence
     and the table. Everything a chart draws comes from here, so its signature is this data. */
  function hourCharts(H) {
    const span = (i) => C.clock(H.start + H.bs * i, false) + '–' + C.clock(H.start + H.bs * (i + 1), false) + (i === SLOTS - 1 ? ' (so far)' : '');
    const peakAt = (vals) => vals.indexOf(Math.max(...vals));
    const at = (i) => C.clock(H.start + H.bs * i, false);
    const none = (label) => ({ ok: false, vals: new Array(SLOTS).fill(0), stacks: new Array(SLOTS).fill([]), sum: null,
      aria: label + ' per 3 minutes over the last hour: no data from this server.', rows: () => [[{ name: 'No data', value: '', nokey: true }], null], cells: () => ['–'] });
    // New tokens, stacked by model family (what a reply's model family is, pricing.family on the server).
    let tokens;
    if (H.tokens) {
      const stacks = H.tokens.map((tot, i) => {
        const segs = [];
        let sum = 0;
        for (const f of FAMS) if (H.fams[f] && H.fams[f][i] > 0) { segs.push({ c: 'f-' + f, f, v: H.fams[f][i] }); sum += H.fams[f][i]; }
        if (tot > sum) { const o = segs.find((x) => x.f === 'other'); if (o) o.v += tot - sum; else segs.push({ c: 'f-other', f: 'other', v: tot - sum }); }
        return segs;
      });
      const vals = stacks.map((segs, i) => Math.max(H.tokens[i], segs.reduce((a, x) => a + x.v, 0)));
      const fams = FAMS.filter((f) => stacks.some((segs) => segs.some((x) => x.f === f)));
      const sum = vals.reduce((a, b) => a + b, 0), pk = peakAt(vals);
      tokens = { ok: true, vals, stacks, sum: [{ b: compact(sum) }, ' in all'],
        legend: fams.map((f) => ({ c: 'f-' + f, name: C.FAMILY_NAME[f] })),
        aria: 'New tokens per 3 minutes over the last hour: ' + (sum ? C.int(sum) + ' in all, the most ' + C.int(vals[pk]) + ' at ' + at(pk) + '; ' + C.int(vals[SLOTS - 1]) + ' so far in the current 3 minutes.' : 'none recorded.'),
        rows: (i) => {
          const rows = stacks[i].map((x) => ({ fam: x.f, name: C.FAMILY_NAME[x.f], value: C.int(x.v) }));
          return rows.length ? [rows, rows.length > 1 ? { name: 'Total', value: C.int(vals[i]) } : null] : [[{ name: 'Nothing recorded', value: '', nokey: true }], null];
        },
        head: ['Total'].concat(fams.map((f) => C.FAMILY_NAME[f])),
        cells: (i) => [C.int(vals[i])].concat(fams.map((f) => C.int((stacks[i].find((x) => x.f === f) || { v: 0 }).v))) };
    } else tokens = none('New tokens');
    // Tool calls started in the bucket; the failed and guard-blocked ones are the red cap.
    let tools;
    if (H.calls) {
      const vals = H.calls, errs = H.errs;
      const stacks = vals.map((n, i) => [n - errs[i] > 0 ? { c: 'fx-m', v: n - errs[i] } : null, errs[i] > 0 ? { c: 'fx-bad', v: errs[i] } : null].filter(Boolean));
      const sum = vals.reduce((a, b) => a + b, 0), bad = errs.reduce((a, b) => a + b, 0), pk = peakAt(vals);
      // Its legend is the failed figure's key in the header: the main tone is the chart's own name.
      tools = { ok: true, vals, stacks, sum: [{ b: C.int(sum) }, ' in all'].concat(bad ? [' · ', { k: 'fx-bad' }, { b: C.int(bad) }, ' failed'] : []),
        aria: 'Tool calls per 3 minutes over the last hour: ' + (sum ? C.int(sum) + ' in all, ' + C.int(bad) + ' failed, the most ' + C.int(vals[pk]) + ' at ' + at(pk) + '; ' + C.int(vals[SLOTS - 1]) + ' so far in the current 3 minutes.' : 'none recorded.'),
        rows: (i) => [[{ name: 'Tool calls', value: C.int(vals[i]), color: 'var(--series-main)' }].concat(errs[i] > 0 ? [{ name: 'Failed', value: C.int(errs[i]), color: 'var(--bad)' }] : []), null],
        head: ['Tool calls', 'Failed'], cells: (i) => [C.int(vals[i]), C.int(errs[i])] };
    } else tools = none('Tool calls');
    // Distinct transcripts (main threads and subagents) at work in the bucket.
    let agents;
    if (H.agents) {
      const vals = H.agents, pk = peakAt(vals), peak = vals[pk];
      const total = H.agentsTotal != null ? H.agentsTotal : null;
      agents = { ok: true, vals, stacks: vals.map((n) => (n > 0 ? [{ c: 'fx-m', v: n }] : [])),
        sum: ['peak ', { b: C.int(peak) }].concat(total != null ? [' · ', { b: C.int(total) }, ' in all'] : []),
        aria: 'Working agents per 3 minutes over the last hour: ' + (peak ? 'at most ' + C.int(peak) + ' at once, at ' + at(pk) + (total != null ? ', ' + C.plural(total, 'agent') + ' in all' : '') + '; ' + C.int(vals[SLOTS - 1]) + ' in the current 3 minutes.' : 'none at work.'),
        rows: (i) => [[{ name: 'Working agents', value: C.int(vals[i]), color: 'var(--series-main)' }], null],
        head: ['Working agents'], cells: (i) => [C.int(vals[i])] };
    } else agents = none('Working agents');
    return [Object.assign(tokens, { k: 'tokens', label: 'Tokens', fmt: compact, whole: false }),
      Object.assign(tools, { k: 'tools', label: 'Tool calls', fmt: C.int, whole: true }),
      Object.assign(agents, { k: 'agents', label: 'Working agents', fmt: C.int, whole: true })].map((c) => Object.assign(c, { span, t0: H.start, bs: H.bs, legend: c.legend || (c.k === 'tokens' ? [] : null),
      sig: [c.k, H.start, H.bs, c.ok, c.vals, c.stacks.map((segs) => segs.map((x) => x.c + ':' + x.v).join()), c.sum, c.legend ? c.legend.map((x) => x.c) : null] }));
  }
  /* One chart, drawn into its host (ctx.chart calls this when the data changed, and on resize and
     theme): the header (label and the hour's figures), the legend for a stacked chart, then
     20 thin columns on the chart's own scale with one top gridline and its label at the right, the
     still-filling bucket lighter, clock times under the last chart, a hit band per bucket over the
     whole plot, and the table for screen readers. The svg is one tab stop: the arrows, Home and
     End step through the buckets and show the same tooltip as the pointer. */
  function drawHour(host, c, axis) {
    const { h, s } = C;
    const W = Math.max(200, Math.round(host.clientWidth) || 334), G = 34, T = 6, PH = plotH(), XL = axis ? 16 : 0, HH = T + PH + 1 + XL;
    const band = (W - G) / SLOTS, bw = Math.max(3, Math.min(12, band - 5)), base = T + PH;
    const max = Math.max(0, ...c.vals), top = max > 0 ? niceTop(max, c.whole) : 0;
    host.__fx = c; host.__ph = PH;
    const svg = s('svg', { 'data-key': 'svg', viewBox: `0 0 ${W} ${HH}`, width: W, height: HH, role: 'img', tabindex: 0, 'aria-label': c.aria,
      onkeydown: (e) => hourKey(host, e), onfocus: () => { if (host.querySelector('svg:focus-visible')) hourTip(host, host.__fxI != null ? host.__fxI : SLOTS - 1); },
      onblur: () => hourOff(host) });
    if (top) {
      svg.append(s('line', { 'data-key': 'gt', class: 'grid-l', x1: 0, x2: W - G + 4, y1: T + .5, y2: T + .5 }),
        s('text', { 'data-key': 'yt', x: W, y: T + 4, 'text-anchor': 'end' }, c.fmt(top)));
    }
    svg.append(s('line', { 'data-key': 'gb', class: 'base-l', x1: 0, x2: W - G + 4, y1: base + .5, y2: base + .5 }));
    for (let i = 0; i < SLOTS; i++) {
      const x = band * i + (band - bw) / 2, g = s('g', { 'data-key': 'c' + i, 'data-i': i, class: 'fx-col' + (i === SLOTS - 1 ? ' now' : '') });
      const segs = c.stacks[i], tot = c.vals[i];
      if (top && tot > 0 && segs.length) {
        // Heights in px: each segment at least 2 px of ink (after the 2 px gap above the one below
        // it), the difference taken from the biggest, so a single failed call still shows.
        const hTot = Math.max(2, (tot / top) * PH), hs = segs.map((sg) => (sg.v / tot) * hTot);
        hs.forEach((v, j) => { const min = j ? 4 : 2; if (v < min) { const big = hs.indexOf(Math.max(...hs)); hs[big] -= min - v; hs[j] = min; } });
        let y = base;
        segs.forEach((sg, j) => {
          const hh = hs[j] - (j ? 2 : 0), yTop = y - hs[j];
          if (hh >= 0.5) g.append(j === segs.length - 1 ? s('path', { 'data-key': sg.c, class: sg.c, d: roundTop(x, yTop, bw, hh, 4) }) : s('rect', { 'data-key': sg.c, class: sg.c, x, y: yTop, width: bw, height: hh }));
          y = yTop;
        });
      }
      svg.append(s('rect', { 'data-key': 'w' + i, 'data-i': i, class: 'fx-wash', x: band * i, y: T, width: band, height: PH }), g);
    }
    if (axis) {
      // Clock times on the quarter hours (on the bucket edges, every 5 of them when they do not line up).
      let edges = [];
      for (let e = 0; e <= SLOTS; e++) if (Math.round(c.t0 + c.bs * e) % 900 === 0) edges.push(e);
      if (!edges.length) edges = [0, 5, 10, 15, 20];
      for (const e of edges) {
        const x = band * e, anchor = x < 14 ? 'start' : x > W - G - 14 ? 'end' : 'middle';
        svg.append(s('text', { 'data-key': 'x' + e, x: anchor === 'start' ? 0 : anchor === 'end' ? W - G : x, y: HH - 3, 'text-anchor': anchor }, C.clock(c.t0 + c.bs * e, false)));
      }
    }
    for (let i = 0; i < SLOTS; i++) {
      svg.append(s('rect', { 'data-key': 'h' + i, 'data-i': i, class: 'hit', x: band * i, y: 0, width: band, height: HH,
        onpointerenter: (ev) => hourTip(host, i, ev), onpointermove: (ev) => { if (host.__fxTip) host.__fxTip.ev = { clientX: ev.clientX, clientY: ev.clientY }; C.moveTip(ev); },
        onpointerleave: () => hourOff(host) }));
    }
    // The header says the hour (its total, or its peak); the current bucket is the lighter column,
    // with its figure in the tooltip and the svg's sentence. "Now" is the instrument panel's.
    const head = h('div', { class: 'fx-chart-h', dataset: { key: 'h' } }, h('span', { class: 'fx-chart-l', text: c.label }),
      h('span', { class: 'fx-chart-sum num', title: c.ok ? null : 'No data from this server' },
        c.ok ? c.sum.map((x) => (typeof x === 'string' ? x : x.k ? h('i', { class: 'key ' + x.k, 'aria-hidden': 'true' }) : h('b', { text: x.b }))) : '–'));
    const leg = c.legend ? h('div', { class: 'fx-chart-leg', dataset: { key: 'leg' } }, c.legend.map((x) => h('span', { dataset: { key: x.c } }, h('i', { class: 'key ' + x.c, 'aria-hidden': 'true' }), x.name))) : null;
    // The table's own box ignores overflow (a table grows to its cells), so a block clips it.
    const table = h('div', { class: 'vh', dataset: { key: 'tbl' } }, h('table', { class: 'vh' }, h('caption', { text: c.label + ' per 3 minutes, the last hour, oldest first' }),
      h('thead', null, h('tr', null, h('th', { scope: 'col', text: 'Time' }), (c.head || [c.label]).map((x) => h('th', { scope: 'col', text: x })))),
      h('tbody', null, Array.from({ length: SLOTS }, (_, i) => h('tr', { dataset: { key: 'r' + i } }, h('th', { scope: 'row', text: c.span(i) }), c.cells(i).map((v) => h('td', { text: v })))))));
    P().into(host, head, leg, svg, table);
    // A redraw (new data, resize, theme) keeps the bucket the pointer or the keyboard is on.
    if (host.__fxTip) hourTip(host, host.__fxTip.i, host.__fxTip.ev);
    else hourMark(host, null);
  }
  /* The column's split of the slot's height between Recent actions and Last hour. The charts keep a
     legible plot and the list gives way first: whole rows, with PEEK px of the next one under the
     fade when rows are cut (the rest scroll inside), down to 3 rows beside 40 px plots (FITS[0],
     a 1366 x 768 window). As the slot grows the rows come back first (up to the feed's 8), then the
     plots grow, up to PLOT_MAX, so on a tall window the column fills its slot as the inspector
     does (an empty hour keeps 40 px plots). Shorter than 1366 x 768, the plots give way to 30 px,
     then the rows to 1 (FITS[1], down to a window about 670 px tall), and below that the column
     scrolls as one (over). A phone's bottom sheet scrolls as a whole: every row, PLOT_SHEET plots.
     The heights follow factory.css: a row (.fx-ev) is 34 px, the list's panel adds 36 (.fx-acts),
     the hour's panel 173 around its three plots (.fx-hour). */
  const ROW_H = 34, PEEK = 12, ACTS_PAD = 36, HOUR_PAD = 173, PLOT_MAX = 96, PLOT_SHEET = 48;
  const FITS = [[3, 40], [1, 30]];   // [fewest rows, smallest plot]: the comfortable floor, then the tight one
  function colFit() {
    const el = st.el;
    if (!el || el.side.hidden) return null;
    if (el.side.offsetWidth >= el.stage.clientWidth - 40) return { sheet: true, ph: PLOT_SHEET, list: null, over: false };
    const gap = parseFloat(getComputedStyle(el.stage).getPropertyValue('--gap')) || 12;
    const n = el.evs.hidden ? 0 : el.evs.children.length;
    const room = el.side.clientHeight - gap - HOUR_PAD - (n ? ACTS_PAD : el.acts.offsetHeight);   // no rows: the empty line's height
    const need = (r) => r * ROW_H + (r < n ? PEEK : 0);
    let rows = n;
    for (const [rmin, pmin] of FITS) {                // the most rows that leave pmin px plots, never under rmin
      rows = n;
      while (rows > Math.min(rmin, n) && need(rows) + 3 * pmin > room) rows--;
      if (need(rows) + 3 * pmin <= room) break;
    }
    // An hour with nothing in it is drawn honestly but small: the plots do not grow for empty baselines.
    const H = st.d ? hourOf(st.d) : null, quiet = !H || ![H.tokens, H.calls, H.agents].some((a) => a && a.some((x) => x > 0));
    const list = need(rows), ph = Math.floor(Math.min(quiet ? FITS[0][1] : PLOT_MAX, Math.max(FITS[FITS.length - 1][1], (room - list) / 3)));
    return { sheet: false, ph, list: rows < n ? list : null, over: room - list - 3 * ph < -1 };   // list: a cap only when rows are cut
  }
  function plotH() {
    const f = colFit();
    return f ? f.ph : FITS[0][1];
  }
  function roundTop(x, y, w, hh, r) {
    r = Math.min(r, w / 2, hh);
    return `M${x},${y + hh}V${y + r}Q${x},${y} ${x + r},${y}H${x + w - r}Q${x + w},${y} ${x + w},${y + r}V${y + hh}Z`;
  }
  function hourMark(host, i) {
    for (const x of host.querySelectorAll('.fx-wash.on, .fx-col.on')) x.classList.remove('on');
    if (i == null) return;
    for (const x of host.querySelectorAll('[data-i="' + i + '"]:is(.fx-wash, .fx-col)')) x.classList.add('on');
  }
  /* Bucket i in the shared tooltip: at the pointer, else (the keyboard) beside its column. */
  function hourTip(host, i, ev) {
    const c = host.__fx;
    if (!c) return;
    if (!ev) {
      const hit = host.querySelector('rect.hit[data-i="' + i + '"]'), b = hit && hit.getBoundingClientRect();
      ev = b ? { clientX: b.left + b.width / 2, clientY: b.top + 6 } : { clientX: 0, clientY: 0 };
    }
    host.__fxI = i;
    host.__fxTip = { i, ev: { clientX: ev.clientX, clientY: ev.clientY } };
    hourMark(host, i);
    const [rows, total] = c.rows(i);
    C.showTip(ev, c.span(i), rows, total);
  }
  function hourOff(host) {
    if (!host.__fxTip) return;
    host.__fxTip = null;
    hourMark(host, null);
    C.hideTip();
  }
  function hourKey(host, e) {
    const cur = host.__fxI != null ? host.__fxI : SLOTS - 1;
    const i = e.key === 'ArrowLeft' ? Math.max(0, cur - 1) : e.key === 'ArrowRight' ? Math.min(SLOTS - 1, cur + 1) : e.key === 'Home' ? 0 : e.key === 'End' ? SLOTS - 1 : null;
    if (e.key === 'Escape') { hourOff(host); return; }
    if (i == null) return;
    e.preventDefault(); e.stopPropagation();
    hourTip(host, i);
  }

  // ── view ──────────────────────────────────────────────────────────────
  const ICON = { in: 'M8 3.5v9M3.5 8h9', out: 'M3.5 8h9', fit: 'M3 6.2V3h3.2M9.8 3H13v3.2M13 9.8V13H9.8M6.2 13H3V9.8', close: 'M4.5 4.5l7 7M11.5 4.5l-7 7',
    side: 'M3 3.5h10a.5.5 0 0 1 .5.5v8a.5.5 0 0 1-.5.5H3a.5.5 0 0 1-.5-.5V4a.5.5 0 0 1 .5-.5ZM9.5 3.5v9' };
  function icon(k) {
    const v = C.s('svg', { viewBox: '0 0 16 16', width: 14, height: 14, 'aria-hidden': 'true' });
    v.append(C.s('path', { d: ICON[k], fill: 'none', stroke: 'currentColor', 'stroke-width': 1.5, 'stroke-linecap': 'round', 'stroke-linejoin': 'round' }));
    return v;
  }
  const view = {
    mount(root) {
      const { h } = C;
      const saved = C.store.get(MODE_KEY);
      st.mode = MODES.includes(saved) ? saved : 'graph';
      Object.assign(st, { sel: null, open: false, last: null, goneAt: null, d: null, dataKey: null, insetSig: '', seenFeed: new Set(), feedReady: false, shown: null, hold: freshHold(), appliedScope: null,
        dec: new Map(), decSig: '', toastSig: '', wide: matchMedia(WIDE) });
      const el = st.el = {};
      el.sub = h('div', { class: 'sub fx-sub' });
      st.picker = C.projectPicker({ label: 'Project', allLabel: 'All projects', withWorktrees: false,
        onPick: (p) => { C.setScope(p); location.hash = hashFor(p, !!C.route().all); } });
      el.dormant = h('button', { type: 'button', class: 'fx-dormant', 'aria-pressed': 'false', title: 'Also show projects with no live session or open order',
        onclick: () => { const r = C.route(); location.hash = hashFor(r.p, !r.all); } }, 'Dormant projects');
      el.mode = h('div', { class: 'seg fx-mode', role: 'group', 'aria-label': 'Show the floor as' },
        MODES.map((m) => h('button', { type: 'button', dataset: { mode: m }, 'aria-pressed': String(st.mode === m), onclick: () => this.setMode(m) }, m === 'graph' ? 'Graph' : 'List')));
      el.head = h('div', { class: 'page-head fx-head' }, h('div', { class: 'fx-id' }, h('h1', { text: 'Factory' }), el.sub),
        h('div', { class: 'fx-tools' }, st.picker.el, h('div', { class: 'seg' }, el.dormant), el.mode));
      el.graph = h('div', { class: 'fx-graph' });
      el.listUl = h('ul', { class: 'fx-ul', role: 'list' });
      el.list = h('div', { class: 'fx-list', hidden: st.mode !== 'list' }, h('h2', { class: 'vh', text: 'Everything on the floor' }), el.listUl);
      el.hud = h('div', { class: 'fx-hud', role: 'group', 'aria-label': 'Factory totals' });
      el.alerts = h('div', { class: 'fx-alerts', role: 'list', 'aria-label': 'Needs you' });
      el.sideBtn = h('button', { type: 'button', class: 'fx-side-btn', 'aria-pressed': 'false', 'aria-controls': 'fx-side', title: 'Recent actions and the last hour, on the right',
        onclick: () => this.toggleSide() }, icon('side'), 'Activity');
      /* One frosted instrument panel across the top: the totals, then a line with what needs
         you (left) and the Activity toggle (right). It is always there, so the floor below it
         never changes size when an alert comes or goes. */
      el.top = h('div', { class: 'fx-top' }, el.hud, h('div', { class: 'fx-band' }, el.alerts, el.sideBtn));
      /* The column (#fx-side): the right-hand slot when no node is selected. Recent actions holds
         the feed rows (the hold box); Last hour is drawn by hour(). */
      el.evs = h('div', { class: 'fx-evs', role: 'list', 'aria-label': 'Recent actions, newest first', onscroll: () => this.more() });
      el.evsNone = h('div', { class: 'fx-evs-none', hidden: true }, glyph('idle'), 'No tool calls yet');
      el.acts = h('section', { class: 'fx-acts', 'aria-labelledby': 'fx-acts-h' }, h('h2', { class: 'fx-side-h', id: 'fx-acts-h', text: 'Recent actions' }), el.evs, el.evsNone);
      el.hour = h('section', { class: 'fx-hour', 'aria-labelledby': 'fx-hour-h' });
      el.side = h('aside', { class: 'fx-side', id: 'fx-side', 'aria-label': 'Floor activity', hidden: true }, el.acts, el.hour);
      /* WCAG 2.2.2: the recent actions change every poll, so they hold still while the pointer is
         over them or focus is in them, their ages included; the newest feed waits and goes in
         when neither holds (new rows still slide in then). The alerts hold for focus only: a
         focused alert must not be removed or moved under the keyboard; a resting pointer is fine,
         since they change only when a decision comes or goes, and their ages count on in place
         either way. */
      const hold = (k, box, hover) => {
        const s = st.hold[k];
        const on = () => { if (k === 'acts') this.ages(true); };
        if (hover) {
          box.addEventListener('pointerenter', () => { s.ptr = true; on(); });
          box.addEventListener('pointerleave', () => { s.ptr = false; this.release(k); });
        }
        box.addEventListener('focusin', () => { s.foc = true; on(); });
        box.addEventListener('focusout', (e) => { if (!box.contains(e.relatedTarget)) { s.foc = false; this.release(k); } });
      };
      hold('acts', el.acts, true);
      hold('alert', el.alerts, false);
      el.zoom = h('div', { class: 'fx-zoom', role: 'group', 'aria-label': 'Zoom' },
        [['in', 'Zoom in (+)'], ['out', 'Zoom out (-)'], ['fit', 'Fit everything (0)']].map(([k, l]) => h('button', { type: 'button', class: 'fx-zbtn', dataset: { zoom: k }, 'aria-label': l, title: l,
          onclick: () => { if (!st.g) return; if (k === 'fit') st.g.fit(); else st.g.zoomBy(k === 'in' ? 1.25 : 0.8); } }, icon(k))));
      el.ikind = h('div', { class: 'fx-insp-kind' });
      el.ititle = h('h2', { class: 'fx-insp-title' });
      el.ilinks = h('div', { class: 'fx-insp-links' });
      el.ibody = h('div', { class: 'fx-insp-body' });
      el.insp = h('aside', { class: 'fx-inspector', role: 'region', 'aria-label': 'Inspector', hidden: true },
        h('div', { class: 'fx-insp-head' }, h('div', { class: 'fx-insp-id' }, el.ikind, el.ititle),
          h('button', { type: 'button', class: 'icon-btn fx-insp-close', 'aria-label': 'Close', title: 'Close (Esc)', onclick: () => this.close(true) }, icon('close'))),
        el.ilinks, el.ibody);
      el.msg = h('div', { class: 'fx-msg', hidden: true });
      el.stage = h('div', { class: 'fx-stage', dataset: { mode: st.mode } }, el.top, el.graph, el.list, el.zoom, el.side, el.insp, el.msg);   // tab order: totals, what needs you, the toggle, the floor, zoom, the slot
      el.root = h('section', { class: 'fx' }, el.head, el.stage);
      root.append(el.root);
      st.g = null;
      if (window.ccGraph && typeof window.ccGraph.create === 'function') {
        try {
          st.g = window.ccGraph.create(el.graph, { kinds: KINDS, face, nodeLabel, edgeStyle, label: 'Factory floor',
            nodeClass: (n) => (n.needs && n.needs !== 'idle' ? 'fx-has-needs' : ''),
            onSelect: (id) => this.select(id, { engine: true }),   // the engine leaves drawing the pick to us
            onActivate: (id) => { const u = pageFor(st.byId.get(id)); if (u) location.hash = u; } });
        } catch (e) { st.g = null; if (window.console) console.error('cc-dash factory: the graph engine failed to start', e); }
        if (st.g && st.mode === 'list') st.g.pause(true);   // List mode: the hidden floor does not draw
      }
      if (!st.g) {
        st.mode = 'list';
        el.stage.dataset.mode = 'list'; el.list.hidden = false;
        for (const b of el.mode.children) { P().attr(b, 'aria-pressed', String(b.dataset.mode === 'list')); if (b.dataset.mode === 'graph') { b.disabled = true; b.title = 'The graph engine (graph.js) did not load'; } }
      }
      if (window.ResizeObserver) {
        st.ro = new ResizeObserver(() => this.insets());
        for (const x of [el.stage, el.top, el.insp, el.side]) st.ro.observe(x);
        const stack = document.querySelector('.al-stack');
        if (stack) st.ro.observe(stack);
      }
      el.stage.addEventListener('keydown', (e) => this.listKey(e));
      // Until the toggle is pressed, the column follows the width: shown past 900 px.
      st.onWide = () => { if (sidePref == null && st.el) { this.sideSync(); this.insets(); } };
      st.wide.addEventListener('change', st.onWide);
      // The toast stack is fixed to the window and the stage is not: a scroll of a short window moves them apart.
      st.onScroll = () => this.toasts();
      window.addEventListener('scroll', st.onScroll, { passive: true });
    },
    unmount() {
      clearTimeout(st.closeT); clearTimeout(st.sideT);
      if (st.wide && st.onWide) st.wide.removeEventListener('change', st.onWide);
      st.onWide = null;
      C.hideTip();
      if (st.ro) { st.ro.disconnect(); st.ro = null; }
      if (st.g) { try { st.g.destroy(); } catch (e) { /* already gone */ } st.g = null; }
      if (st.onScroll) { window.removeEventListener('scroll', st.onScroll); st.onScroll = null; }
      const root = document.documentElement;
      root.classList.remove('al-fx', 'al-fx-glide', 'al-fx-away');
      root.style.removeProperty('--al-dx'); root.style.removeProperty('--al-dy');
      st.el = null; st.picker = null; st.dec = new Map(); st.decSig = ''; st.toastSig = '';
    },
    setMode(m) {
      if (!st.el || (m === 'graph' && !st.g)) return;
      st.mode = m;
      C.store.set(MODE_KEY, m);
      const el = st.el;
      el.stage.dataset.mode = m;
      for (const b of el.mode.children) P().attr(b, 'aria-pressed', String(b.dataset.mode === m));
      el.list.hidden = m !== 'list';
      if (st.g) st.g.pause(m === 'list');
      this.list();
      this.insets();
    },

    update() {
      if (!st.el) return;
      const el = st.el, r = C.route();
      let d = C.S.data.graph;
      st.scope = { p: r.p || '', all: !!r.all };
      P().attr(el.dormant, 'aria-pressed', String(!!r.all));
      if (!d) {
        st.picker.set([...st.projects.values()], { p: r.p || '' }, 0);
        if (st.err || (C.S.lastFail && !st.d)) this.message('error', C.errorBox(st.err || { message: C.S.lastFail }, 'the factory floor'));
        else this.message('loading');
        return;
      }
      // poll() let in only a node list; the rest of the payload is taken as far as it is well formed
      const list = (v) => (Array.isArray(v) ? v.filter((x) => x && typeof x === 'object') : []);
      d = { ...d, edges: list(d.edges), alerts: list(d.alerts), feed: list(d.feed), kpis: d.kpis && typeof d.kpis === 'object' ? d.kpis : {} };
      st.d = d;
      el.stage.classList.toggle('fx-stale', C.S.data.graphScope !== scopeKey(r));
      index(d);
      st.decSig = decisions();
      rememberProjects(d);
      const items = [...st.projects.values()].sort((a, b) => b.sessions - a.sessions || a.project.localeCompare(b.project));
      st.picker.set(items, { p: r.p || '' }, items.reduce((a, x) => a + x.sessions, 0));
      P().setText(el.sub, this.finding(d));
      this.hud(d);
      const empty = !st.order.length;
      this.alerts(empty ? { alerts: [] } : d);
      this.acts(empty ? { feed: [] } : d);
      this.hour(d);
      el.stage.classList.toggle('fx-empty-floor', empty);
      if (empty) this.message('empty');
      else this.message(null);
      this.sideSync();
      this.insets();
      const key = (d.sig || '') + '\u0000' + scopeKey(r) + '\u0000' + st.order.length + '\u0000' + d.window_days;
      if (st.g && key !== st.dataKey) {
        st.dataKey = key;
        // In tree order (new elements go in at their place, so reading order follows the tree),
        // with index()'s parents. age and stalled_for tick every poll and no face shows them: leave
        // them out, so the engine (which re-runs face() only when a node's JSON changed) does not
        // redraw for them.
        st.g.setData({ nodes: st.order.map((id) => ({ ...st.byId.get(id), parent: st.par.get(id), age: undefined, win: d.window_days })), edges: d.edges });   // win: a new window changes every hub caption, so every face repaints
        // A workflow's face names the phase its running agent is in: redraw it when only an agent changed.
        for (const id of st.order) if (st.byId.get(id).kind === 'workflow') st.g.invalidate(id);
        // A new scope (a project picked, the dormant toggle) is a new floor: fit it.
        const sc = C.S.data.graphScope;
        if (st.appliedScope != null && sc !== st.appliedScope) st.g.fit();
        st.appliedScope = sc;
      }
      if (st.sel && st.byId.has(st.sel)) {
        st.last = st.byId.get(st.sel); st.goneAt = null;
        if (st.g && st.g.selected() !== st.sel) st.g.select(st.sel);
      } else if (st.sel && !st.goneAt) st.goneAt = C.nowS();
      this.list();
      this.inspector();
    },
    message(kind, node) {
      const el = st.el, { h } = C;
      el.stage.classList.toggle('fx-failed', kind === 'error');   // the totals' placeholders stop shimmering: nothing is loading
      if (kind === 'loading') {
        if (st.shown !== 'loading') {
          P().into(el.hud, [0, 1, 2, 3].map((i) => h('div', { class: 'fx-kpi', dataset: { key: 'sk' + i } }, h('div', { class: 'sk sk-line', style: { width: '64px' } }), h('div', { class: 'sk', style: { width: (40 + i * 8) + 'px', height: '18px', marginTop: '6px' } }))));
        }
        el.msg.hidden = true; st.shown = 'loading';
        return;
      }
      if (kind === 'error') { P().into(el.msg, node); el.msg.hidden = false; el.msg.dataset.kind = 'error'; st.shown = 'error'; return; }
      if (kind === 'empty') {
        const all = C.route().all;
        P().into(el.msg, h('div', { class: 'empty fx-empty', dataset: { key: all ? 'empty-all' : 'empty' } },
          h('h2', { text: all ? 'No projects in the last ' + days() : 'Nothing is on the floor right now' }),
          h('p', { text: 'A project comes onto the floor while one of its sessions is live or it has an open work order: its sessions, the agents they spawn, workflow runs and orders, joined the way they run, with token burn flowing down the lines.' }),
          all ? h('p', { text: 'cc-dash found no transcripts in this window. Start a Claude Code session and it shows up here within a few seconds.' })
            : h('button', { class: 'btn', type: 'button', text: 'Show dormant projects', onclick: () => { location.hash = hashFor(C.route().p, true); } })));
        el.msg.hidden = false; el.msg.dataset.kind = 'empty'; st.shown = 'empty';
        return;
      }
      if (!el.msg.hidden) { el.msg.hidden = true; el.msg.replaceChildren(); }
      st.shown = 'data';
    },
    /* The one finding of this poll first: who needs you (or what failed or stalled), then how busy, then the burn. */
    finding(d) {
      const k = d.kpis || {}, parts = [];
      const nodes = [...st.byId.values()];
      if (!nodes.length) return 'Projects, sessions, agents, workflows and orders, live. Updates every 3 seconds.';
      if (k.needs) {
        const a = (d.alerts || []).find((x) => x.kind !== 'idle') || (d.alerts || [])[0];
        const n = a ? st.byId.get(a.node) : nodes.find((x) => (x.kind === 'agent' || x.kind === 'session') && x.needs && x.needs !== 'idle');
        const ask = a ? (a.kind === 'permission' ? 'asks to run ' + (a.tool || 'a tool') : a.kind === 'question' ? 'has a question' : a.kind === 'plan' ? 'has a plan for you to review'
          : a.kind === 'idle' ? 'is waiting for your input' : a.kind === 'error' ? 'hit an error' : 'needs attention') : 'needs a decision';
        parts.push(k.needs + (k.needs === 1 ? ' needs' : ' need') + ' you' + (n ? ': ' + n.label + ' ' + ask + (k.needs > 1 ? ', and ' + (k.needs - 1) + ' more' : '') : ''));
      } else if (k.failed || k.stalled) {
        const bad = k.failed ? 'failed' : 'stalled';
        const n = nodes.find((x) => (x.kind === 'agent' || x.kind === 'session') && x.state === bad);
        parts.push((k[bad]) + ' ' + bad + (n ? ': ' + n.label : ''));
      }
      const busy = nodes.filter((n) => n.kind === 'project' && n.state !== 'idle').length;
      parts.push(C.int(k.working) + ' working' + (busy ? ' in ' + C.plural(busy, 'project') : ''));
      if (k.burn_cost_min > 0) parts.push(money(k.burn_cost_min) + '/min');
      else if (floorNewMin() > 0) parts.push(compact(floorNewMin()) + ' new tok/min');
      return parts.join(' · ');
    },
    hud(d) {
      const { h } = C, k = d.kpis || {};
      const g = k.guard || {}, o = k.orders || {};
      const small = (t) => h('small', { text: t });
      const b = (t) => h('b', { text: t });
      const kp = (key, label, value, extra) => h('div', { class: 'fx-kpi' + (extra && extra.cls ? ' ' + extra.cls : ''), dataset: { k: key, key }, title: (extra && extra.title) || null },
        h('div', { class: 'fx-kpi-l' }, extra && extra.g ? glyph(extra.g) : null, label), h('div', { class: 'fx-kpi-v num' }, value));
      const ords = ORDER_FLOW.filter((s) => o[s] > 0).map((s) => [b(C.int(o[s])), small(' ' + (ORDER_WORD[s] || s).toLowerCase())]);
      P().into(st.el.hud,
        kp('live', 'Sessions live', C.int(k.sessions_live)),
        kp('working', 'Working', C.int(k.working), { g: 'working', title: 'Sessions and agents running a tool, thinking or waiting on their subagents' }),
        kp('needs', 'Needs you', C.int(k.needs), { g: k.needs ? 'needs' : null, cls: k.needs ? 'on' : 'zero' }),
        kp('stalled', 'Stalled · failed', [glyph('stalled'), b(C.int(k.stalled)), glyph('failed'), b(C.int(k.failed))], { cls: k.stalled || k.failed ? '' : 'zero' }),
        kp('burn', 'Burn', k.burn_cost_min > 0 ? [money(k.burn_cost_min), small('/min')] : [compact(floorNewMin()), small(' new tok/min')],
          { title: 'Over the last 5 minutes, per minute: ' + (k.burn_cost_min > 0 ? 'about ' + money(k.burn_cost_min, true) + ' and ' : '') + compact(floorNewMin())
            + ' new tokens (input, cache writes and output; cache reads are not counted)' }),
        kp('cost', 'Cost', [money(k.cost_live), small(' live · ' + money(k.cost_window) + ' in ' + days())],
          { title: 'Live: the sessions on the floor, subagents included. Window: every session of these projects in the last ' + days() + '.' }),
        kp('guard', 'Guard catches', [b(C.int(g.deny)), small(' deny · '), b(C.int(g.ask)), small(' ask · '), b(C.int(g.warn)), small(' warn')],
          { title: 'Guard hooks in the last ' + days() + ': ' + C.int(g.deny) + ' denied, ' + C.int(g.ask) + ' asked, ' + C.int(g.warn) + ' warned', cls: g.deny || g.ask || g.warn ? '' : 'zero' }),
        kp('orders', 'Orders', ords.length ? ords.map((x, i) => (i ? [small(' · '), x] : x)) : small('none open'), { cls: ords.length ? '' : 'zero' }));
    },
    /* The newest data for a held piece goes in once nothing holds it (see mount). */
    held(k, d) {
      const s = st.hold[k];
      if (!s.ptr && !s.foc) return false;
      s.d = d;
      return true;
    },
    release(k) {
      const s = st.hold && st.hold[k];
      if (!st.el || !s || s.ptr || s.foc) return;
      if (k === 'acts') this.ages(false);
      if (!s.d) return;
      const d = s.d;
      s.d = null;
      if (k === 'acts') this.acts(d); else this.alerts(d);
    },
    /* Stop (or restart) the ages of the recent actions: they hold still with the rows. */
    ages(stop) {
      if (!st.el) return;
      for (const x of st.el.acts.querySelectorAll(stop ? '[data-ago]' : '[data-ago-held]')) {
        if (stop) { x.dataset.agoHeld = x.dataset.ago; delete x.dataset.ago; } else { x.dataset.ago = x.dataset.agoHeld; delete x.dataset.agoHeld; P().setText(x, C.ago(Number(x.dataset.ago))); }
      }
    },
    alerts(d) {
      if (this.held('alert', d)) return;
      const { h } = C, list = d.alerts || [];
      const el = st.el.alerts;
      const now = st.d ? st.d.now : C.nowS();
      P().attr(el, 'data-n', String(list.length));
      if (!list.length) { P().into(el, h('div', { class: 'fx-alert-none', role: 'listitem', dataset: { key: 'none' } }, glyph('idle'), 'Nothing needs you')); return; }
      const c = ctl(), reviewed = new Set();
      P().into(el, list.map((a) => {
        const n = st.byId.get(a.node);
        const who = n ? n.label : a.node;
        const what = ALERT_WORD[a.kind] || a.title || 'Needs you';
        // Control mode: the node's open decision gets a Review button beside its alert (one per node). The
        // alert's own text (attention.jsonl, which anything running as you can append to) is then left
        // out: what the decision is about is read on the card the button opens.
        const dec = c && c.isDecision(a.kind) && !reviewed.has(a.node) ? st.dec.get(a.node) : null;
        if (dec) reviewed.add(a.node);
        const msg = dec ? '' : a.message;
        return h('div', { class: 'fx-alert-w', role: 'listitem', dataset: { key: a.id } },
          h('button', { type: 'button', class: 'fx-alert', dataset: { node: a.node, key: a.id }, title: (a.title || what) + (msg ? '\n' + msg : ''),
            'aria-label': what + (a.tool ? ', ' + a.tool : '') + ', ' + who + (msg ? ': ' + msg : '') + '. Show it on the floor.',
            onclick: () => this.focus(a.node) },
          glyph(a.kind === 'idle' ? 'turn' : 'needs'), h('b', { text: what }), a.tool ? [dot(), h('span', { class: 'mono', text: a.tool })] : null,
          dot(), h('span', { class: 'fx-alert-who', text: who }), dot(), sinceEl(now - (a.age || 0), 'fx-alert-age')),
          dec ? c.inline(dec.id, pageFor(n)) : null);
      }));
    },
    /* Recent actions: the newest 8 tool calls (payload.feed by time, newest first). A row is when,
       who (the node's label, in its model colour), the tool and its target, and how it went in the
       status language; a click shows the node. pending is running; blocked is an error, said so. */
    acts(d) {
      if (this.held('acts', d)) return;
      const { h } = C, el = st.el;
      const feed = [...(d.feed || [])].sort((a, b) => (num(b.t) || 0) - (num(a.t) || 0)).slice(0, 8);
      const motion = st.feedReady && !matchMedia('(prefers-reduced-motion: reduce)').matches;
      P().attr(el.evs, 'hidden', !feed.length);
      P().attr(el.evsNone, 'hidden', !!feed.length);
      P().into(el.evs, feed.map((f) => {
        const k = f.key + '|' + f.id;
        const [status, said, g] = EV[f.status] || EV.ok;
        const n = f.node != null ? st.byId.get(f.node) : null;
        const who = String((n && n.label) || f.label || f.project || '');
        const tool = String(f.name || 'tool'), tgt = f.target != null ? String(f.target) : '', t = num(f.t);
        return h('div', { class: 'fx-ev-w' + (motion && !st.seenFeed.has(k) ? ' enter' : ''), role: 'listitem', dataset: { key: k } },
          h('button', { type: 'button', class: 'fx-ev', dataset: { node: f.node != null ? String(f.node) : '', status }, title: tool + (tgt ? ' ' + tgt : '') + '\n' + who,
            'aria-label': tool + (tgt ? ' ' + tgt : '') + ', ' + who + ', ' + said + (t ? ', at ' + C.clock(t) : '') + '. Show it on the floor.',
            onclick: () => this.focus(f.node) },
          h('span', { class: 'num fx-ev-t', dataset: t ? { ago: t } : null, text: t ? C.ago(t) : '' }),
          C.key(FAMS.includes(f.family) ? f.family : 'other'),
          h('span', { class: 'fx-ev-who', text: who }),
          h('b', { class: 'mono fx-ev-tool', text: tool }),
          h('span', { class: 'mono fx-ev-tgt', text: tgt }),
          h('span', { class: 'fx-ev-st' + (f.status === 'blocked' ? ' blocked' : '') }, glyph(g), said)));
      }));
      st.seenFeed = new Set((d.feed || []).map((f) => f.key + '|' + f.id));
      st.feedReady = true;
      this.fitCol();   // the row count can move the split
    },
    /* A list cut short by a short slot fades out at its bottom edge while rows remain below, so the
       next row's peek reads as more to scroll to (factory.css .fx-evs[data-more]). */
    more() {
      const x = st.el && st.el.evs;
      if (!x) return;
      P().attr(x, 'data-more', !x.hidden && x.scrollHeight - x.scrollTop > x.clientHeight + 1);
    },
    /* Last hour: payload.series as three charts (hourCharts, drawHour), whatever the window. Not
       while the column is hidden (the inspector holds the slot, or the toggle is off): its buckets
       move with the clock, and nothing out of sight is redrawn; sideSync() draws it on the way in. */
    hour(d) {
      if (st.el.side.hidden) return;
      const { h } = C;
      const specs = hourCharts(hourOf(d));
      P().into(st.el.hour, h('h2', { class: 'fx-side-h', id: 'fx-hour-h', text: 'Last hour' }),
        h('p', { class: 'fx-hour-cap', text: '3-minute bars, whatever the window' }),
        specs.map((c, i) => C.chart(h('div', { class: 'fx-chart', dataset: { k: c.k, key: c.k } }), c.sig, (host) => drawHour(host, c, i === specs.length - 1))));
    },
    /* The slot holds the column when it is wanted (the toggle's last press, else the width) and no
       node is selected; the inspector otherwise (inspector()). Called before insets() wherever
       either can change; the toggle's aria-pressed says whether the column is showing. */
    sideSync() {
      const el = st.el;
      if (!el) return;
      const show = (sidePref != null ? sidePref : !!(st.wide && st.wide.matches)) && !st.open && !!st.d;
      P().attr(el.sideBtn, 'aria-pressed', String(show));
      const side = el.side, leaving = side.classList.contains('leaving');
      if (show) {
        if (!side.hidden && !leaving) return;
        clearTimeout(st.sideT);
        side.classList.remove('leaving');
        side.hidden = false;
        this.hour(st.d);                 // skipped while hidden
        P().flushCharts(side, true);     // and measured now that it has a width
        return;
      }
      if (side.hidden || leaving) return;
      const s = st.hold.acts;
      s.ptr = false; s.foc = false;    // a panel on its way out holds nothing (no pointerleave comes for it)
      for (const c of side.querySelectorAll('.fx-chart')) hourOff(c);
      if (matchMedia('(prefers-reduced-motion: reduce)').matches) side.hidden = true;
      else {
        side.classList.add('leaving');
        st.sideT = setTimeout(() => { if (st.el && side.classList.contains('leaving')) { side.hidden = true; side.classList.remove('leaving'); } }, 200);
      }
      this.release('acts');
    },
    /* The Activity toggle: shows or hides the column for the page's life. With the inspector open
       it closes the inspector, and the column takes the slot back. */
    toggleSide() {
      if (!st.el) return;
      const showing = st.el.sideBtn.getAttribute('aria-pressed') === 'true';
      sidePref = !showing;
      if (!showing && st.open) { this.close(false); return; }
      this.sideSync();
      this.insets();
    },
    /* What holds the slot now: the open inspector, else the column (not while it leaves); sheet when
       it is the phone's bottom sheet. */
    slot() {
      const el = st.el, vis = (x) => !x.hidden && x.offsetParent !== null;
      const x = st.open && vis(el.insp) ? el.insp : vis(el.side) && !el.side.classList.contains('leaving') ? el.side : null;
      return x ? { el: x, sheet: x.offsetWidth >= el.stage.clientWidth - 40 } : null;
    },
    /* The list: the same tree as nested buttons (the screen-reader path; List mode shows it). */
    list() {
      if (!st.el || st.mode !== 'list' || !st.d) return;
      const { h } = C;
      const row = (id) => {
        const n = st.byId.get(id);
        const kids = st.kids.get(id) || [];
        const c = cat(n);
        return h('li', { dataset: { key: id }, class: 'fx-li-w k-' + n.kind },
          h('button', { type: 'button', class: 'fx-li c-' + c + (st.sel === id ? ' sel' : ''), dataset: { node: id }, 'aria-current': st.sel === id ? 'true' : null,
            onclick: () => this.select(id, { engine: true }) },
          n.kind !== 'project' && n.kind !== 'workflow' && n.kind !== 'order' ? C.key(fam(n)) : null, glyph(c),
          h('span', { class: 'fx-li-name', text: (n.kind === 'order' && n.detail && n.detail.id ? n.detail.id + ' ' : '') + n.label }),
          h('span', { class: 'fx-li-kind', text: KIND_NAME[n.kind] }),
          h('span', { class: 'fx-li-word', text: word(n) }), h('span', { class: 'fx-li-fig num', text: figure(n) })),
          kids.length ? h('ul', { class: 'fx-ul', role: 'list' }, kids.map(row)) : null);
      };
      P().into(st.el.listUl, st.roots.map(row));
    },
    listKey(e) {
      if (st.mode !== 'list' || !e.target.classList || !e.target.classList.contains('fx-li')) return;
      if (e.key !== 'ArrowDown' && e.key !== 'ArrowUp') return;
      e.preventDefault(); e.stopPropagation();
      this.step(e.key === 'ArrowDown' ? 1 : -1);
    },

    // ── selection and the inspector ──────────────────────────────────
    /* o.engine: also select in the engine (every pick: a node click or key, an alert, a recent action, the list). */
    select(id, o) {
      o = o || {};
      if (!st.el) return;
      if (!id) { this.close(false); return; }
      clearTimeout(st.closeT);
      st.sel = id; st.open = true;
      st.last = st.byId.get(id) || null; st.goneAt = null;
      this.inspector();
      this.list();
      this.sideSync();   // the inspector takes the column's slot: the same width, so no refit
      this.insets({ frame: false });
      if (!st.g) return;
      if (o.engine) st.g.select(id, { focus: !!o.focus && st.mode === 'graph' });
      // From an alert or a recent action: centre it, close enough to read its face.
      if (o.center && st.mode === 'graph') { st.g.center(id, { zoom: Math.max(st.g.camera().k, 0.9) }); return; }
      // Any other pick: it, its parent and its children come out from under the inspector
      // (right, or the phone's bottom sheet). graph.js reframe() has the rule: a floor still
      // fitted is fitted again into what is left while that stays readable, else it pans,
      // zooming out only when they cannot fit otherwise.
      st.g.reframe(id);
    },
    focus(id) {
      if (!id || !st.byId.has(id)) return;
      this.select(id, { engine: true, center: true, focus: true });
      if (st.mode === 'list') { const b = st.el.list.querySelector('.fx-li[data-node="' + CSS.escape(id) + '"]'); if (b) { b.focus(); b.scrollIntoView({ block: 'nearest' }); } }
    },
    close(refocus) {
      const id = st.sel, last = st.last || st.byId.get(id) || null;
      st.open = false; st.sel = null; st.last = null; st.goneAt = null;
      if (st.g && st.g.selected()) st.g.select(null);
      this.inspector();
      this.list();
      this.sideSync();   // the column comes back into the slot if it was showing
      this.insets({ frame: false });
      if (st.g) st.g.reframe(null);   // a floor that was fitted before the inspector opened fits again
      if (!refocus || !id) return;
      // Focus goes back to the node, or, when it has left the floor, to its nearest ancestor still
      // on it; else to the floor's roving node (the first list row); else the stage. Never <body>.
      const back = last ? lineage(last).find((x) => st.byId.has(x.id)) : null;
      if (st.mode === 'graph' && st.g) {
        if (back) { st.g.focusNode(back.id); return; }
        const rov = st.el.graph.querySelector('.gx-node[tabindex="0"]');
        if (rov) { rov.focus({ preventScroll: true }); return; }
      } else {
        const b = (back && st.el.list.querySelector('.fx-li[data-node="' + CSS.escape(back.id) + '"]')) || st.el.list.querySelector('.fx-li');
        if (b) { b.focus(); return; }
      }
      st.el.stage.tabIndex = -1;
      st.el.stage.focus({ preventScroll: true });
    },
    inspector() {
      const el = st.el, n = st.open && st.sel ? (st.byId.get(st.sel) || st.last) : null;
      if (!n) {
        if (!el.insp.hidden && !el.insp.classList.contains('leaving')) {
          if (matchMedia('(prefers-reduced-motion: reduce)').matches) el.insp.hidden = true;
          else { el.insp.classList.add('leaving'); st.closeT = setTimeout(() => { if (st.el && !st.open) { el.insp.hidden = true; el.insp.classList.remove('leaving'); } }, 200); }
        }
        return;
      }
      const { h } = C;
      // Written only when they change: a poll with the inspector open makes no mutation here.
      if (el.insp.classList.contains('leaving')) el.insp.classList.remove('leaving');
      if (el.insp.hidden) el.insp.hidden = false;
      P().attr(el.insp, 'data-kind', n.kind);
      P().attr(el.insp, 'aria-label', KIND_NAME[n.kind] + ': ' + (n.label || n.id));
      P().into(el.ikind, h('span', { class: 'fx-insp-k', text: KIND_NAME[n.kind] }),
        n.model ? C.modelTag(n.model, fam(n)) : null, n.kind === 'session' || n.kind === 'agent' ? effort(n) : null,
        n.kind === 'order' && n.detail && n.detail.priority ? h('span', { class: 'tag', text: n.detail.priority }) : null);
      P().setText(el.ititle, (n.kind === 'order' && n.detail && n.detail.id ? n.detail.id + ' · ' : '') + (n.label || n.id));
      P().into(el.ilinks, this.links(n));
      const gone = !st.byId.has(n.id);
      P().into(el.ibody, gone ? h('p', { class: 'fx-gone', dataset: { key: 'gone' } }, 'Left the floor ', agoEl(st.goneAt || C.nowS()), '. This is the last data it had.') : null,
        ...this.sections(n));
    },
    links(n) {
      const { h } = C;
      const a = (href, text, key) => h('a', { class: 'btn fx-link', href, dataset: { key }, text });
      const dt = n.detail || {};
      const sid = sessionOf(n);
      if (n.kind === 'project') return [a(C.areaHash('overview', n.label), 'Overview', 'ov'), a(C.areaHash('plans', n.label), 'Plans', 'pl')];
      if (n.kind === 'agent') return [a(pageFor(n), 'Open agent', 'ag'), sid ? a('#/s/' + enc(sid), 'Session', 'ss') : null];
      if (n.kind === 'session') return [a(pageFor(n), 'Open session', 'ss'), dt.project ? a(C.areaHash('overview', dt.project), 'Project', 'ov') : null];
      if (n.kind === 'workflow') return [sid ? a('#/s/' + enc(sid), 'Open session', 'ss') : null];
      const p = projectOf(n);
      return [p ? a(C.areaHash('overview', p.label), 'Project overview', 'ov') : null, p ? a(C.areaHash('plans', p.label), 'Plans', 'pl') : null];
    },
    sections(n) {
      if (n.kind === 'session' || n.kind === 'agent') return [this.secLive(n), this.secRun(n), this.secTokens(n), n.kind === 'session' ? this.secGuard(n.detail && n.detail.guard) : null];
      if (n.kind === 'project') return [this.secProject(n), this.secGuard(n.detail && n.detail.guard), this.secPlans(n), this.secOrders(n)];
      if (n.kind === 'workflow') return [this.secWorkflow(n)];
      return [this.secOrder(n)];
    },
    sec(k, title, ...kids) { return C.h('section', { class: 'fx-sec', dataset: { sec: k, key: k } }, title ? C.h('h3', { class: 'fx-sec-h', text: title }) : null, kids); },
    row(label, ...value) { return C.h('div', { class: 'fx-row', dataset: { key: label } }, C.h('div', { class: 'fx-row-l', text: label }), C.h('div', { class: 'fx-row-v' }, value)); },
    /* A node of this floor as a button: selects it and shows it. */
    nodeBtn(id, text) { return C.h('button', { type: 'button', class: 'fx-nlink', text, onclick: () => this.focus(id) }); },
    secLive(n) {
      const { h } = C, dt = n.detail || {}, now = st.d ? st.d.now : C.nowS();
      const al = st.alertBy.get(n.id);
      const dec = st.byId.get(n.id) === n ? st.dec.get(n.id) : null, c = dec ? ctl() : null;
      let box;
      if (dec && c) {
        // Control mode: the request's own record says what it is (its summary, never the alert's text), and
        // the one button opens the review dialog with the full card; the decision is made there.
        const tool = dec.tool || (al && al.tool);
        box = h('div', { class: 'now fx-now needs' }, h('div', { class: 'now-label' }, 'Needs a decision: ' + needsText(dec.kind, tool),
          al && al.age != null ? sinceEl(now - al.age) : sinceEl(dec.ts)),
          h('div', { class: dec.tool === 'Bash' ? 'now-body' : 'now-text', text: c.summary(dec.id) }),
          h('div', { class: 'fx-now-act' }, c.inline(dec.id, pageFor(n))));
      } else if (n.needs && n.needs !== 'idle') {
        box = h('div', { class: 'now fx-now needs' }, h('div', { class: 'now-label' }, doing(n), al && al.age != null ? sinceEl(now - al.age) : null),
          h('div', { class: al && al.tool === 'Bash' ? 'now-body' : 'now-text', text: (al && al.message) || n.target || n.last_text || '' }));
      } else if (n.state === 'tool') {
        box = h('div', { class: 'now fx-now' }, h('div', { class: 'now-label' }, 'Running ', h('b', { class: 'mono', text: n.tool || 'a tool' }), n.since ? sinceEl(n.since) : null),
          h('div', { class: 'now-body', text: n.target || n.tool || '' }));
      } else if (n.state === 'stalled') {
        box = h('div', { class: 'now fx-now' }, h('div', { class: 'now-label' }, 'Stalled? no writes for ', sinceEl(now - (dt.stalled_for != null ? dt.stalled_for : n.age || 0))),
          h('div', { class: n.tool ? 'now-body' : 'now-text', text: n.tool ? 'was running ' + n.tool + (n.target ? ': ' + n.target : '') : 'was thinking' }));
      } else {
        const ago = ['idle', 'done', 'failed'].includes(n.state);
        box = h('div', { class: 'now fx-now' }, h('div', { class: 'now-label' }, doing(n), n.since ? (ago ? agoEl(n.since) : sinceEl(n.since)) : null),
          h('div', { class: n.state === 'waiting' && n.target ? 'now-body' : 'now-text', text: (n.state === 'waiting' && n.target ? (n.tool ? n.tool + '  ' : '') + n.target : n.last_text) || '' }));
      }
      const said = n.last_text && (n.state === 'tool' || n.state === 'waiting' || n.state === 'stalled' || (n.needs && n.needs !== 'idle') || dec)
        ? h('div', { class: 'fx-said', dataset: { key: 'said' } }, h('span', { class: 'fx-row-l', text: 'Last said' }), h('p', { class: 'fx-quote', text: n.last_text })) : null;
      const sp = Array.isArray(n.spark) && n.spark.length ? h('div', { class: 'fx-spark-row', dataset: { key: 'spark' } }, this.spark(n.spark),
        h('span', { class: 'faint', text: 'tool calls a minute, last ' + n.spark.length + ' min' })) : null;
      return this.sec('live', null, h('div', { class: 'fx-state', dataset: { key: 'state' } }, pill(n), n.start ? h('span', { class: 'faint num', text: 'started ' + C.dateTime(n.start) }) : null), box, said, sp);
    },
    spark(arr) {
      const W = 120, H = 20, n = arr.length, band = W / n, bw = Math.max(2, Math.min(5, band - 2));
      const max = Math.max(4, ...arr);
      const svg = C.s('svg', { class: 'spark fx-spark', viewBox: `0 0 ${W} ${H}`, width: W, height: H, role: 'img',
        'aria-label': 'Tool calls per minute, last ' + n + ' minutes, oldest first: ' + arr.join(', ') + ' (the last minute is still running)' });
      arr.forEach((v, i) => {
        const x = (i * band + (band - bw) / 2).toFixed(1);
        const hh = v ? Math.max(2, Math.round((v / max) * H)) : 1;
        svg.append(C.s('rect', { 'data-key': 'b' + i, class: !v ? 'sp0' : i === n - 1 ? 'sp-now' : 'sp', x, y: H - hh, width: bw, height: hh, rx: v ? 1 : 0 }));
      });
      return svg;
    },
    secRun(n) {
      const { h } = C, dt = n.detail || {}, row = this.row.bind(this);
      const errs = n.kind === 'session' ? (dt.tool_errors || 0) + (dt.api_errors || 0) : (n.errors || 0);
      const rows = [];
      if (n.kind === 'session') {
        const p = st.byId.get(n.parent);
        rows.push(row('Project', p ? this.nodeBtn(p.id, p.label) : (dt.project || '')));
        if (dt.branch || dt.worktree) rows.push(row('Branch', dt.worktree ? h('span', { class: 'tag', text: dt.worktree }) : null, dt.branch ? h('span', { class: 'mono', text: dt.branch }) : null));
        rows.push(row('Prompts', C.int(dt.prompts)), row('Tool calls', C.int(dt.tool_calls)));
        rows.push(row('Errors', h('span', { class: errs ? 'bad' : null, text: C.int(dt.tool_errors) + ' tool · ' + C.int(dt.api_errors) + ' API' })));
        rows.push(row('Subagents', C.int(dt.subagents) + (dt.running ? ' · ' + C.int(dt.running) + ' running' : '')));
        if (dt.plan) rows.push(row('Plan', h('span', { class: 'fx-plan' }, h('span', { class: 'fx-plan-t', text: dt.plan.title }), this.bar(dt.plan.done, dt.plan.total),
          h('span', { class: 'num faint', text: dt.plan.done + ' of ' + dt.plan.total + ' tasks' }))));
      } else {
        const par = st.byId.get(n.parent);
        if (par) rows.push(row(par.kind === 'workflow' ? 'Workflow' : 'Spawned by', this.nodeBtn(par.id, par.label)));
        rows.push(row('Type', h('span', { class: 'mono', text: dt.type || 'subagent' }), dt.background ? h('span', { class: 'faint', text: ' background' }) : null));
        if (dt.phase) rows.push(row('Phase', dt.phase));
        if (dt.order) {
          const o = [...st.byId.values()].find((x) => x.kind === 'order' && x.detail && x.detail.id === dt.order && projectOf(x) === projectOf(n));
          rows.push(row('Order', o ? this.nodeBtn(o.id, dt.order + ' · ' + o.label) : dt.order));
        }
        if (dt.waiting_on) rows.push(row('Waiting on', C.plural(dt.waiting_on, 'agent')));
        rows.push(row('Tool calls', C.int(dt.tool_calls)), row('Errors', h('span', { class: errs ? 'bad' : null, text: C.int(errs) })));
        const kids = (st.kids.get(n.id) || []).length;
        if (kids) rows.push(row('Subagents', C.int(kids) + ' on the floor'));
      }
      return this.sec('session', n.kind === 'session' ? 'Session' : 'Agent', rows);
    },
    secTokens(n) {
      const dt = n.detail || {}, u = dt.usage, row = this.row.bind(this);
      const r = rate(n.burn), b = n.burn || {};
      // The rate as everywhere (dollars, else new tokens, a minute); new tokens beside dollars. Cache reads
      // re-count the whole context on every call, so they are only named apart, in the title.
      const burn = r ? C.h('span', { title: 'Over the last 5 minutes, per minute: ' + compact(b.new_min) + ' new tokens (input, cache writes and output)'
        + (b.tok_min > b.new_min ? '; cache reads, counted apart, ' + compact(b.tok_min - b.new_min) : ''),
      text: r + (b.cost_min > 0 && b.new_min > 0 ? ' · ' + compact(b.new_min) + ' new tok/min' : '') }) : null;
      return this.sec('tokens', 'Tokens and cost',
        row('Cost', money(n.cost, true)), row('New tokens', C.h('span', { title: C.int(n.tokens) + ' tokens: input, cache writes and output', text: compact(n.tokens) })),
        RUNNING.includes(n.state) && burn ? row('Burn', burn) : null,
        u ? row('Input · output', compact(u.input) + ' · ' + compact(u.output)) : null,
        u ? row('Cache write · read', compact(u.cache_write) + ' · ' + compact(u.cache_read)) : null);
    },
    secGuard(g) {
      if (!g) return null;
      const { h } = C;
      return this.sec('guard', 'Guard', this.row('Catches', h('span', { class: g.deny || g.ask || g.warn ? null : 'faint', text: C.int(g.deny) + ' deny · ' + C.int(g.ask) + ' ask · ' + C.int(g.warn) + ' warn' })));
    },
    bar(done, total) {
      return C.h('span', { class: 'pl-bar fx-bar', role: 'img', 'aria-label': done + ' of ' + total + ' done' },
        C.h('span', { class: 'pl-seg' }, C.h('span', { class: 'pl-fill', style: { width: (total ? (100 * done) / total : 0) + '%' } })));
    },
    secProject(n) {
      const { h } = C, dt = n.detail || {}, row = this.row.bind(this);
      const mix = (dt.model_mix || []).filter((m) => (m.cost || m.tokens) > 0);
      const by = mix.every((m) => m.cost != null) && mix.some((m) => m.cost > 0) ? 'cost' : 'tokens';
      const tot = mix.reduce((a, m) => a + (m[by] || 0), 0);
      const share = tot ? h('div', { class: 'share fx-share', role: 'img', 'aria-label': 'Model mix by ' + by + ': ' + mix.map((m) => m.model + ' ' + C.pctStr(m[by] || 0, tot)).join(', ') },
        mix.map((m) => h('span', { class: 'f-' + (FAMS.includes(m.family) ? m.family : 'other'), dataset: { key: m.model }, style: { flex: String((m[by] || 0) / tot) } }))) : null;
      return this.sec('project', null,
        row('Root', h('span', { class: 'mono fx-ell', title: dt.root || '', text: dt.root || '' })),
        row('Sessions', C.int(dt.sessions_live) + ' live · ' + C.int(dt.sessions_window) + ' in ' + days()),
        row('Cost', money(dt.cost_window, true) + ' in ' + days()),
        row('New tokens', compact(dt.tokens_window) + ' in ' + days()),
        row('Errors', h('span', { class: dt.errors ? 'bad' : null, text: C.int(dt.errors) })),
        mix.length ? h('div', { class: 'fx-mix', dataset: { key: 'mix' } }, h('div', { class: 'fx-row-l', text: 'Model mix, by ' + by }), share,
          h('div', { class: 'fx-legend' }, mix.map((m) => h('div', { class: 'fx-leg', dataset: { key: m.model } }, C.key(FAMS.includes(m.family) ? m.family : 'other'),
            h('span', { class: 'fx-leg-n', text: m.model }), h('span', { class: 'num', text: (m.cost != null ? money(m.cost) + ' · ' : '') + compact(m.tokens) + ' tok' }),
            h('b', { class: 'num', text: C.pctStr(m[by] || 0, tot) }))))) : null);
    },
    secPlans(n) {
      const { h } = C, plans = (n.detail && n.detail.plans) || [];
      if (!plans.length) return null;
      const STATUS = { live: ['working', 'Working'], active: ['idle', 'Idle'], paused: ['paused', 'Paused'], not_started: ['idle', 'Not started'], done: ['done', 'Done'], blocked: ['turn', 'Waiting for you'] };
      return this.sec('plans', 'Plans', plans.slice(0, 6).map((p) => h('div', { class: 'fx-plan-row', dataset: { key: p.key } },
        h('div', { class: 'fx-plan-h' }, h('span', { class: 'fx-plan-t', text: p.title }), C.stPill(...(STATUS[p.status] || STATUS.not_started))),
        h('div', { class: 'fx-plan-p' }, this.bar(p.done, p.total), h('span', { class: 'num faint', text: p.done + ' of ' + p.total + ' tasks' })))),
      plans.length > 6 ? h('div', { class: 'faint', dataset: { key: 'more' }, text: '+' + (plans.length - 6) + ' more on the Plans page' }) : null);
    },
    secOrders(n) {
      const { h } = C, o = (n.detail && n.detail.orders) || {};
      const mine = (st.kids.get(n.id) || []).map((id) => st.byId.get(id)).filter((x) => x && x.kind === 'order');
      const counts = ORDER_FLOW.filter((s) => o[s] > 0);
      if (!counts.length && !mine.length) return null;
      return this.sec('orders', 'Orders',
        counts.length ? this.row('By status', counts.map((s) => o[s] + ' ' + (ORDER_WORD[s] || s).toLowerCase()).join(' · ')) : null,
        mine.map((x) => h('div', { class: 'fx-ord', dataset: { key: x.id } }, glyph(cat(x)), this.nodeBtn(x.id, (x.detail && x.detail.id ? x.detail.id + ' · ' : '') + x.label), h('span', { class: 'faint', text: word(x) }))));
    },
    secWorkflow(n) {
      const { h } = C, dt = n.detail || {}, row = this.row.bind(this);
      const ph = phaseOf(n);
      const order = dt.order ? [...st.byId.values()].find((x) => x.kind === 'order' && x.detail && x.detail.id === dt.order && projectOf(x) === projectOf(n)) : null;
      const par = st.byId.get(n.parent);
      return this.sec('workflow', null,
        h('div', { class: 'fx-state', dataset: { key: 'state' } }, pill(n)),
        row('Progress', h('span', { class: 'fx-plan' }, segs(dt), h('span', { class: 'num', text: (dt.finished || 0) + ' of ' + (dt.total || 0) + ' finished'
          + (dt.running ? ' · ' + dt.running + ' running' : '') + (dt.failed ? ' · ' + dt.failed + ' failed' : '') }))),
        (dt.phases || []).length ? row('Phases', h('span', { class: 'fx-phases' }, dt.phases.map((p, i) => h('span', { class: p === ph ? 'on' : i < (dt.finished || 0) ? 'done' : null, dataset: { key: p + i }, text: p })))) : null,
        par ? row('Started by', this.nodeBtn(par.id, par.label)) : null,
        dt.order ? row('Order', order ? this.nodeBtn(order.id, dt.order + ' · ' + order.label) : dt.order) : null,
        dt.run_id ? row('Run', h('span', { class: 'mono', text: dt.run_id })) : null);
    },
    secOrder(n) {
      const { h } = C, dt = n.detail || {}, row = this.row.bind(this);
      const runs = st.d.edges.filter((e) => e.kind === 'runs' && e.source === n.id).map((e) => st.byId.get(e.target)).filter(Boolean);
      const SW = { pending: 'pending', active: 'working now', done: 'done', failed: 'failed', skipped: 'skipped' };
      const sg = { pending: 'idle', active: 'working', done: 'done', failed: 'failed', skipped: 'idle' };
      return this.sec('order', null,
        h('div', { class: 'fx-state', dataset: { key: 'state' } }, pill(n), dt.updated ? h('span', { class: 'faint num' }, 'updated ', agoEl(dt.updated, '')) : null),
        h('ol', { class: 'fx-stations', dataset: { key: 'stations' } },
          dt.gate && dt.gate.name === 'spec' ? h('li', { class: 'fx-st gate' + (dt.gate.waiting ? ' wait' : ''), dataset: { key: 'gate' } }, glyph(dt.gate.waiting ? 'needs' : 'idle'),
            h('span', { text: 'Spec gate' }), h('span', { class: dt.gate.waiting ? 'fx-warn' : 'faint', text: dt.gate.waiting ? 'waiting for you' : 'ahead' })) : null,
          (dt.stations || []).map((x) => h('li', { class: 'fx-st' + (x.status === 'active' ? ' on' : ''), dataset: { key: x.name } }, glyph(sg[x.status] || 'idle'),
            h('span', { text: x.title || x.name }), h('span', { class: 'faint num', text: (SW[x.status] || x.status) + (x.t ? ' · ' + C.dateTime(x.t) : '') }))),
          dt.gate && dt.gate.name === 'merge' ? h('li', { class: 'fx-st gate' + (dt.gate.waiting ? ' wait' : ''), dataset: { key: 'gate' } }, glyph(dt.gate.waiting ? 'needs' : 'idle'),
            h('span', { text: 'Merge gate' }), h('span', { class: dt.gate.waiting ? 'fx-warn' : 'faint', text: dt.gate.waiting ? 'waiting for you' : 'ahead' })) : null),
        dt.lane ? row('Lane', h('span', { class: 'mono fx-ell', title: dt.lane.branch, text: dt.lane.branch || '' })) : null,
        dt.lane && dt.lane.worktree ? row('Worktree', h('span', { class: 'mono fx-ell', title: dt.lane.worktree, text: dt.lane.worktree })) : null,
        runs.length ? row('Run', runs.map((x) => this.nodeBtn(x.id, x.label))) : dt.run_id ? row('Run', h('span', { class: 'mono', text: dt.run_id })) : null,
        row('Rework', dt.rework ? C.plural(dt.rework, 'cycle') : 'none'),
        row('Events', C.int(dt.events)),
        dt.created ? row('Created', C.dateTime(dt.created)) : null,
        dt.path ? row('Spec', h('span', { class: 'mono fx-ell', title: dt.path, text: dt.path })) : null);
    },

    // ── geometry ──────────────────────────────────────────────────────
    /* Screen px the glass covers, so fit, centre and framing keep nodes out from
       under it: the instrument panel across the top (totals, alerts) and what
       holds the right-hand slot, the column or the inspector (right, or the
       phone's bottom sheet), plus the zoom buttons, the one piece that floats
       over the floor, as a rect to avoid. Column and inspector are one width, so
       a pick changes nothing here. The same figures place the slot and pad the
       list and the messages (CSS variables). When they change the engine
       re-frames (graph.js reframe()), unless the caller does that itself
       (o.frame false). */
    insets(o) {
      if (!st.el) return;
      this.more();
      const el = st.el, gap = 12;
      const vis = (x) => !x.hidden && x.offsetParent !== null;
      const H = el.stage.clientHeight, W = el.stage.clientWidth;
      const hudB = vis(el.top) ? el.top.offsetTop + el.top.offsetHeight : 0;
      el.stage.style.setProperty('--fx-hud-b', hudB + 'px');
      const top = hudB ? hudB + gap : 0;
      let bottom = 0, right = 0;
      const sl = this.slot();
      if (sl) {
        if (sl.sheet) bottom = H - sl.el.offsetTop + gap;   // the phone's bottom sheet
        else right = W - sl.el.offsetLeft + gap;
      }
      el.stage.style.setProperty('--fx-top', top + 'px');
      el.stage.style.setProperty('--fx-right', right + 'px');
      el.stage.style.setProperty('--fx-bottom', bottom + 'px');
      // Where the zoom buttons come to rest: factory.css slides them left of the column
      // or inspector by --fx-right less the stage gap (offsets ignore that transform).
      let avoid = null;
      if (vis(el.zoom)) {
        const sg = parseFloat(getComputedStyle(el.stage).getPropertyValue('--gap')) || gap;
        avoid = { x: el.zoom.offsetLeft - Math.max(0, right - sg), y: el.zoom.offsetTop, w: el.zoom.offsetWidth, h: el.zoom.offsetHeight };
      }
      const v = { top: Math.round(top), right: Math.round(right), bottom: Math.round(bottom), left: 0, avoid };
      const sig = JSON.stringify(v);
      if (st.g && sig !== st.insetSig) {
        st.insetSig = sig;
        st.g.setInsets(v);
        if (!o || o.frame !== false) st.g.reframe(st.open ? st.sel : null);
      }
      this.toasts(right);
      this.fitCol();
    },
    /* The column's split (colFit): the list's height in whole rows (--fx-evs-h, factory.css), the
       scroll of last resort (.over), and the charts drawn again when their plot height moved. */
    fitCol() {
      const el = st.el;
      if (!el) return;
      const f = colFit(), list = f && f.list != null ? f.list + 'px' : '';
      if (el.side.style.getPropertyValue('--fx-evs-h') !== list) {
        if (list) el.side.style.setProperty('--fx-evs-h', list);
        else el.side.style.removeProperty('--fx-evs-h');
      }
      el.side.classList.toggle('over', !!(f && f.over));
      this.more();
      const ch = f ? el.side.querySelector('.fx-chart') : null;
      if (ch && ch.__ph != null && ch.__ph !== f.ph) P().flushCharts(el.side, true);
    },
    /* Toasts (alerts.js .al-stack, fixed to the window's bottom right) never cover the zoom buttons
       or what holds the slot (the column or the inspector), and nothing on the floor moves for one:
       no refit, and the zoom buttons stay where graph.js keeps nodes out from under them. So the
       stack moves. Its right edge sits left of the zoom buttons where they rest (they slide left of
       the slot, and the stack glides with them). A stack as wide as the window (a phone) sits above
       the zoom row instead. While a phone's bottom sheet (the column's or the inspector's) is up the
       stack steps aside, as it does for the bell drawer: the floor left above the sheet is too small
       to share with a toast, and the band over it still lists what needs you, with Review. So does
       a stack with no room left of the slot (a window just wider than a phone). Sets --al-dx, --al-dy
       and .al-fx-away on <html> for alerts.css, only while the floor is up (unmount clears them). */
    toasts(right) {
      const root = document.documentElement, stack = document.querySelector('.al-stack');
      if (!st.el || !stack) return;
      const el = st.el, gap = 12, edge = 12;
      const vis = (x) => !x.hidden && x.offsetParent !== null;
      const vw = root.clientWidth, vh = root.clientHeight;
      const s = el.stage.getBoundingClientRect(), ox = s.left + el.stage.clientLeft, oy = s.top + el.stage.clientTop;
      const cs = getComputedStyle(stack);
      const sr = parseFloat(cs.right) || 0, sb = parseFloat(cs.bottom) || 0, sw = stack.offsetWidth;
      const full = sw + 2 * edge >= vw - 1;
      if (right == null) right = parseFloat(el.stage.style.getPropertyValue('--fx-right')) || 0;
      const sg = parseFloat(getComputedStyle(el.stage).getPropertyValue('--gap')) || gap;
      // Where the zoom buttons and the slot rest (offsets: no transition or slide in them).
      const zoom = vis(el.zoom) ? { l: ox + el.zoom.offsetLeft - Math.max(0, right - sg), t: oy + el.zoom.offsetTop } : null;
      const sl = this.slot(), insp = sl ? { l: ox + sl.el.offsetLeft, sheet: sl.sheet } : null;
      let dx = 0, dy = 0, away = false;
      if (insp && insp.sheet) away = true;
      else {
        const wall = Math.min(zoom ? zoom.l : Infinity, insp ? insp.l : Infinity);
        const lift = () => (zoom ? Math.max(0, (vh - sb) - (zoom.t - gap)) : 0);
        if (wall < vw - sr) {
          if (full) dy = lift();
          else {
            dx = Math.max(0, (vw - sr) - (wall - gap));
            if (vw - sr - dx - sw < edge) { dx = 0; if (insp) away = true; else dy = lift(); }
          }
        }
      }
      const sig = Math.round(dx) + ',' + Math.round(dy) + ',' + away;
      if (sig === st.toastSig) return;
      const first = !st.toastSig;
      st.toastSig = sig;
      root.style.setProperty('--al-dx', Math.round(dx) + 'px');
      root.style.setProperty('--al-dy', Math.round(dy) + 'px');
      root.classList.toggle('al-fx-away', away);
      if (first) {
        root.classList.add('al-fx');   // placed at once; it glides from the next move on
        requestAnimationFrame(() => requestAnimationFrame(() => { if (st.el) root.classList.add('al-fx-glide'); }));
      }
    },

    // ── keys (app.js routes unhandled keys here) ─────────────────────
    keys(k) {
      if (!st.el) return false;
      if (k === 'Escape') {
        if (st.open) { this.close(true); return true; }
        // Narrow screens (the column is the bottom sheet, or a panel over half the floor): Esc puts it away.
        if (st.el.sideBtn.getAttribute('aria-pressed') === 'true' && !matchMedia(WIDE).matches) {
          const inside = st.el.side.contains(document.activeElement);
          this.toggleSide();
          if (inside) st.el.sideBtn.focus();
          return true;
        }
        return false;
      }
      if (k === 'j' || k === 'k') return this.step(k === 'j' ? 1 : -1);
      if (st.mode !== 'graph' || !st.g) return false;
      if (k === '+' || k === '=') { st.g.zoomBy(1.25); return true; }
      if (k === '-' || k === '_') { st.g.zoomBy(0.8); return true; }
      if (k === '0') { st.g.fit(); return true; }
      return false;
    },
    /* Focus the next or previous node in tree order (projects, then each one's subtree). */
    step(dir) {
      const ids = st.order;
      if (!ids.length) return false;
      const a = document.activeElement;
      const cur = a && a.closest ? (a.closest('[data-id]') || a.closest('[data-node]')) : null;
      const curId = cur ? cur.getAttribute('data-id') || cur.getAttribute('data-node') : st.sel;
      let i = ids.indexOf(curId);
      i = i < 0 ? (dir > 0 ? 0 : ids.length - 1) : Math.max(0, Math.min(ids.length - 1, i + dir));
      const id = ids[i];
      if (st.mode === 'graph' && st.g) st.g.focusNode(id);
      else { const b = st.el.list.querySelector('.fx-li[data-node="' + CSS.escape(id) + '"]'); if (b) { b.focus(); b.scrollIntoView({ block: 'nearest' }); } }
      return true;
    },
    poll,
  };
  const P = () => C.P;

  /* control.js's state comes every second: the strip and the inspector are drawn again only when the
     open decisions (or the lock) changed. The strip still holds still while focus is in it. */
  function onControl() {
    if (!st.el || !st.d) return;
    const sig = decisions();
    if (sig === st.decSig) return;
    st.decSig = sig;
    view.alerts(st.order.length ? st.d : { alerts: [] });
    view.inspector();
  }
  document.addEventListener('cc-control:state', onControl);
  document.addEventListener('cc-control:ready', onControl);

  function init(ctx) {
    C = ctx;
    return { views: { factory: view }, hash };
  }
  /* focus(id): select, centre and focus node `id` when the floor is up and holds it (selfheal.js's
     link to a run's session); false when it cannot, so the caller falls back to the node's page. */
  function focusNode(id) {
    if (!C || !st.el || C.route().view !== 'factory' || !st.byId.has(id)) return false;
    view.focus(id);
    return true;
  }
  window.ccFactory = { init, graph: () => st.g, focus: focusNode };
})();
