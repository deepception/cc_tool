/* cc-dash plans: progress of the implementation plans your agents execute (plans.py, /api/plans).

   Where it lives: the Projects area, beside Overview, Sessions and Docs,
   sharing their project scope (#/plans?p=<project>; app.js projectHead). Also
   reached from the Overview's "Active plans" panel, from the plan chip on a
   session header, and with the p key. A plan's page is
   #/plan?p=<project>&f=<path in the repo>.

   One progress language everywhere: bar() is a track split into one segment
   per milestone (or per task when the plan has no milestones), each segment
   as wide as its share of the steps and filled by how many of them are done,
   so the filled length is the share of steps done. Next to it the label
   always reads "13 of 14 tasks done · 91% of steps"; the task being worked
   on (or the first one still open) is shown on its own, never folded into
   that count.

   app.js calls init(ctx) once with its helpers and registers the returned
   views; everything here renders through ctx.h (textContent only) and patches
   in place with ccDom, so polls never blink. Exposes window.ccPlans. */
'use strict';
(() => {
  let C = null;
  const st = { list: null, listAll: false, err: null, detail: {}, derr: {}, timer: null, busy: false, chipSig: '', older: false, open: new Set(), shut: new Set() };

  // ── formatting ────────────────────────────────────────────────────────
  const enc = encodeURIComponent;
  const plansHash = (p) => '#/plans' + (p ? '?p=' + enc(p) : '');
  const planHash = (p, f) => '#/plan?p=' + enc(p) + '&f=' + enc(f);
  const docHash = (p, f) => '#/docs?p=' + enc(p) + '&f=' + enc(f);   // the Docs view (another branch)
  const sessHash = (sid, aid) => '#/s/' + enc(sid) + (aid ? '/a/' + enc(aid) : '');
  const pctOf = (d, t) => (t ? Math.round((100 * d) / t) : 0);
  /* Escaped text with `inline code` as code spans; nothing is parsed as HTML. */
  function inline(text, cls) {
    const { h } = C;
    const parts = String(text || '').split(/(`[^`]+`)/g).filter(Boolean);
    return h('span', { class: cls || null }, parts.map((x) => (x.length > 2 && x[0] === '`' && x[x.length - 1] === '`' ? h('code', { class: 'pl-code', text: x.slice(1, -1) }) : x)));
  }
  /* "Widget" for "Widget Implementation Plan": the Superpowers template's suffix says nothing in a chip. */
  const short = (t) => String(t || '').replace(/\s*[\u2014\u2013:-]?\s*(implementation plan|plan)\s*$/i, '') || t;
  /* "13 of 14 tasks done": how many are done, never which one is current. */
  function label(p) {
    const t = p.tasks, s = p.steps;
    const unit = p.mode === 'todo' ? 'items' : 'tasks';
    if (t.total && t.done === t.total) return 'All ' + C.plural(t.total, unit.slice(0, -1), unit) + ' done';
    if (t.total) return t.done + ' of ' + t.total + ' ' + unit + ' done';
    return s.done + ' of ' + s.total + ' steps done';
  }
  /* "91% of steps": the share the bar fills. */
  function measure(p) {
    const s = p.steps, t = p.tasks;
    if (s.total && p.unit !== 'tasks' && t.total) return pctOf(s.done, s.total) + '% of steps';
    if (!s.total && t.total && t.done < t.total) return pctOf(t.done, t.total) + '%';
    return '';
  }
  const progressText = (p) => label(p) + (measure(p) ? ' \u00b7 ' + measure(p) : '');
  /* The current task, said apart from the count: what is running, or the first one still open. */
  function currentLine(p) {
    const c = p.current;
    if (!c || p.status === 'done') return null;
    const name = 'Task ' + (c.id || c.pos) + ': ' + c.title;
    if (p.status === 'blocked') return { label: p.blocked && p.blocked.kind === 'idle' ? 'Waiting for you' : 'Waiting for your decision', name };
    if (c.running || p.status === 'live') return { label: 'Working on', name };
    if (p.status === 'not_started') return { label: 'Starts with', name };
    if (c.later_done) return { label: 'First open task', name, note: C.plural(p.tasks.total - p.tasks.done, 'task') + ' open, done out of order' };
    return { label: 'Next up', name };
  }
  /* Plan status in the dashboard's one status language (app.css): a plan whose
     executing session waits on you reads exactly like that session on the Factory floor. */
  const STATUS = { live: ['working', 'Working'], active: ['idle', 'Idle'], paused: ['paused', 'Paused'], not_started: ['idle', 'Not started'], done: ['done', 'Done'] };
  function stateOf(p) {
    if (p.status === 'blocked') return C.isDecision(p.blocked || {}) ? ['decide', 'Needs a decision'] : ['turn', 'Your turn'];
    return STATUS[p.status] || STATUS.not_started;
  }
  const pill = (p) => C.stPill(...stateOf(p));
  /* The one progress visual: milestone segments weighted by steps, each filled by completion. */
  function bar(p, size) {
    const { h } = C;
    const segs = p.segments && p.segments.length ? p.segments : [{ title: 'Plan', done: p.steps.done || p.tasks.done, total: p.steps.total || p.tasks.total || 1 }];
    const aria = label(p) + (measure(p) ? ', ' + measure(p) : '') + (segs.length > 1 ? ', ' + segs.length + ' segments' : '');
    return h('div', { class: 'pl-bar' + (size ? ' ' + size : ''), role: 'img', 'aria-label': aria },
      segs.map((sg, i) => h('span', { class: 'pl-seg', dataset: { key: 's' + i }, style: { flexGrow: String(Math.max(1, sg.total || 1)) },
        title: size === 'mini' ? null : sg.title + ': ' + (sg.total ? sg.done + ' of ' + sg.total : 'nothing to tick') + (sg.tasks > 1 ? ' (' + sg.tasks_done + ' of ' + sg.tasks + ' tasks)' : '') },
      h('span', { class: 'pl-fill', style: { width: (sg.total ? (100 * sg.done) / sg.total : 0) + '%' } }))));
  }
  function eta(p) {
    if (!p.eta) return null;
    const left = C.dur(p.eta.secs_left);
    return p.eta.at ? 'about ' + left + ' left (est.)' : 'about ' + left + ' of work left (est.)';
  }
  function whoLine(p) {
    const { h } = C;
    const c = p.current;
    if (!c || !c.who) return null;
    const w = c.who;
    return h('span', { class: 'pl-who' }, w.model ? C.modelTag(w.model, w.family) : null,
      h('span', { class: 'ellipsis', text: w.agent_id ? w.label : 'main thread', title: w.label }),
      w.running && w.since ? h('span', { class: 'num faint nowrap', dataset: { since: w.since }, text: C.dur(C.now() - w.since) }) : null);
  }

  // ── data ──────────────────────────────────────────────────────────────
  const anyLive = () => !!(st.list && st.list.plans.some((p) => p.status === 'live' || p.status === 'blocked'));
  function cadence() {
    const v = C.route().view;
    if (document.hidden) return 10000;
    if (v === 'plan') { const d = st.detail[detailKey()]; return d && ['live', 'blocked'].includes(d.plan.status) ? 3000 : 10000; }
    if (['plans', 'session', 'overview'].includes(v)) return anyLive() ? 3000 : 12000;
    return 30000;
  }
  const detailKey = () => { const r = C.route(); return r.p + '\u0000' + r.f; };
  async function poll() {
    clearTimeout(st.timer);
    if (st.busy) { st.timer = setTimeout(poll, 500); return; }
    st.busy = true;
    const r = C.route();
    const t0 = performance.now();
    let ok = false;
    try {
      const jobs = [];
      const wantAll = r.view === 'plans' && st.older;
      jobs.push(C.api('/api/plans' + (wantAll ? '?all=1' : '')).then((d) => { st.list = d; st.listAll = wantAll; st.err = null; })
        .catch((e) => { st.err = e; }));
      if (r.view === 'plan' && r.f) {
        const k = detailKey();
        jobs.push(C.api('/api/plans/detail?project=' + enc(r.p) + '&path=' + enc(r.f)).then((d) => { st.detail[k] = d; st.derr[k] = null; })
          .catch((e) => { st.derr[k] = e; }));
      }
      await Promise.all(jobs);
      refresh();
      // A missing plan (404) is an answer; anything else means the server did not give one.
      ok = ![st.err, r.view === 'plan' ? st.derr[detailKey()] : null].some((e) => e && e.status !== 404);
    } finally {
      st.busy = false;
      st.timer = setTimeout(poll, cadence());
      if (C.synced) C.synced(ok, cadence(), performance.now() - t0);   // the header sync bar, while a Plans view is open
    }
  }
  /* Repaint whatever shows plan data; other views only when their chips or panel changed. */
  function refresh() {
    const v = C.route().view;
    if (v === 'plans' || v === 'plan') { C.update(); return; }
    const sig = JSON.stringify([st.list && st.list.chips, st.list && st.list.plans.map((p) => [p.key, p.status, p.tasks, p.steps, p.current && p.current.pos])]);
    if (sig !== st.chipSig) { st.chipSig = sig; if (['session', 'overview'].includes(v)) C.update(); }
  }
  document.addEventListener('visibilitychange', () => { if (!document.hidden && C) poll(); });

  // ── hooks for app.js ────────────────────────────────────────────────────
  /* The plan chip for a session (or the subagent driving the plan): mini bar, "Plan: <title> · Task 4 of 9". */
  function chip(sid, aid) {
    const { h } = C;
    const chips = st.list ? st.list.chips : null;
    const c = chips && chips[sid + (aid ? '/' + aid : '')];
    if (!c) return null;
    const p = { status: c.status, tasks: { done: c.done, total: c.total }, steps: c.steps, current: c.pos ? { pos: c.pos } : null, segments: c.segments, mode: '', unit: c.unit };
    return h('a', { class: 'tag pl-chip', href: planHash(c.project, c.key), title: c.title + ': ' + progressText(p),
      onclick: (e) => e.stopPropagation() }, bar(p, 'mini'),
    h('span', { class: 'ellipsis', text: 'Plan: ' + short(c.title) }), h('span', { class: 'nowrap', text: '\u00b7 ' + (p.tasks.total ? p.tasks.done + ' of ' + p.tasks.total + ' done' : label(p)) }));
  }
  /* The one compact plan list: Overview's "Active plans" panel and the quiet
     groups of the Plans page (paused, not started, done). */
  function planTable(rows, showProject) {
    const { h } = C;
    return h('table', { class: 'compact pl-table' },
      h('thead', {}, h('tr', {}, h('th', { text: 'Plan' }), h('th', { text: 'Status' }), h('th', { text: 'Progress' }), h('th', { text: 'Now' }), h('th', { class: 'r', text: 'Last activity' }))),
      h('tbody', {}, rows.map((p) => {
        const go = () => { location.hash = planHash(p.project, p.key); };
        const cur = currentLine(p);
        return h('tr', { class: 'link', tabindex: 0, dataset: { key: p.project + '\u0000' + p.key }, onclick: go, onkeydown: (e) => { if (e.key === 'Enter') go(); } },
          h('td', {}, h('div', { class: 'pl-cell-t ellipsis', text: p.title, title: p.title }), showProject ? h('div', { class: 'faint', text: p.project }) : null),
          h('td', {}, pill(p)),
          h('td', {}, h('div', { class: 'pl-prog' }, bar(p), h('div', { class: 'pl-prog-l' }, h('b', { text: label(p) }), measure(p) ? h('span', { class: 'muted', text: measure(p) }) : null))),
          h('td', { class: 'pl-now' }, cur ? [h('span', { class: 'faint', text: cur.label }), inline(cur.name, 'ellipsis')] : h('span', { class: 'faint', text: p.status === 'done' ? 'Finished' : '–' })),
          h('td', { class: 'r muted', dataset: { ago: p.last_activity }, text: C.ago(p.last_activity), title: C.dateTime(p.last_activity) }));
      })));
  }
  /* The Overview's "Active plans" panel, scoped like the rest of the page. */
  function overviewPanel(scope) {
    const { h } = C;
    const d = st.list;
    if (!d) return null;
    const mine = d.plans.filter((p) => (!scope || p.project === scope));
    if (!mine.length) return null;
    const active = mine.filter((p) => ['live', 'blocked', 'active', 'paused'].includes(p.status));
    const rows = (active.length ? active : mine).slice(0, 6);
    return h('section', { class: 'panel', dataset: { key: 'plans' } },
      h('div', { class: 'panel-h' }, h('h2', { text: active.length ? 'Active plans' : 'Plans' }),
        h('a', { class: 'link-u', href: plansHash(scope), text: 'All ' + C.plural(mine.length, 'plan') })),
      h('div', { class: 'panel-b' }, planTable(rows, !scope)));
  }
  const isPlanView = (v) => v === 'plans' || v === 'plan';
  function route(parts, q) {
    if (parts[0] === 'plans') return { view: 'plans', p: (q.get('p') || '').slice(0, 200) };
    if (parts[0] === 'plan') return { view: 'plan', p: (q.get('p') || '').slice(0, 200), f: (q.get('f') || '').slice(0, 1024) };
    return null;
  }

  // ── the Plans view ──────────────────────────────────────────────────────
  const views = {};
  views.plans = {
    mount(root) {
      const { h } = C;
      this.head = C.projectHead('plans');
      this.sub = this.head.sub;
      C.setText(this.sub, 'Implementation plans and how far your agents got with them.');
      root.append(this.head.el, this.body = h('div', {}));
      this.first = true;
      poll();
    },
    update() {
      const { h, P } = C;
      const scope = C.route().p;
      const d = st.list;
      const projects = d ? d.projects : [];
      this.head.set(projects.map((x) => ({ project: x.project, sessions: st.older ? x.plans : x.recent, worktrees: [] })), { p: scope, wt: '' },
        projects.reduce((a, x) => a + (st.older ? x.plans : x.recent), 0));
      if (!d) {
        if (st.err) P.into(this.body, C.errorBox(st.err, 'plans'));
        else if (!this.body.firstChild) this.body.append(C.skeleton('cards'));
        return;
      }
      const list = d.plans.filter((p) => !scope || p.project === scope);
      const older = scope ? Math.max(0, ((projects.find((x) => x.project === scope) || {}).plans || 0) - ((projects.find((x) => x.project === scope) || {}).recent || 0)) : d.older;
      const busy = list.filter((p) => ['live', 'blocked', 'active'].includes(p.status));
      const live = list.filter((p) => ['live', 'blocked'].includes(p.status)).length;
      C.setText(this.sub, (list.length ? C.plural(list.length, 'plan') + ' with activity in this window' + (live ? ', ' + live + ' running now' : '') + '. ' : '')
        + 'Progress comes from the plan’s checkboxes, the SDD ledger, todo lists and the subagents that ran each task.');
      const first = this.first;
      this.first = false;
      if (!list.length) {
        P.into(this.body, h('div', { class: 'empty', dataset: { key: 'empty' } }, h('h2', { text: scope ? 'No plans in ' + scope : 'No plans found' }),
          h('p', {}, 'cc-dash looks for plans in ', h('code', { text: 'docs/superpowers/plans/' }), ', checkbox task lists under ', h('code', { text: 'docs/' }), ' and ', h('code', { text: 'TASKS.md' }), ' in the projects of your recent sessions.'),
          older && !st.older ? h('button', { class: 'btn', type: 'button', text: 'Show ' + C.plural(older, 'older plan'), onclick: () => { st.older = true; poll(); } }) : null));
        return;
      }
      // Running plans lead as cards; the rest recede into one compact list per state.
      const groups = [
        ['paused', 'Paused', list.filter((p) => p.status === 'paused'), false],
        ['not_started', 'Not started', list.filter((p) => p.status === 'not_started'), true],
        ['done', 'Done', list.filter((p) => p.status === 'done'), true],
      ].filter((g) => g[2].length);
      P.into(this.body, h('div', { class: 'stack' + (first ? ' fade-in' : ''), dataset: { key: 'plans' } },
        busy.length ? h('section', { class: 'pl-running', dataset: { key: 'running' } },
          h('div', { class: 'sec-h' }, h('h2', { text: 'In progress' }), h('span', { class: 'muted num', text: String(busy.length) })),
          h('div', { class: 'live-grid pl-grid' }, busy.map((p) => card(p, !scope)))) : null,
        groups.map(([k, title, rows, fold]) => this.group(k, title, rows, fold, !scope)),
        older > 0 || st.older ? h('div', { class: 'pl-more', dataset: { key: 'more' } }, h('button', { class: 'btn', type: 'button',
          text: st.older ? 'Hide older plans' : 'Show ' + C.plural(older, 'older plan') + ' (no activity in this window)',
          onclick: () => { st.older = !st.older; poll(); } })) : null));
    },
    /* A quiet group: a panel whose head folds it (not started and done start folded; the choice is kept). */
    group(k, title, rows, fold, showProject) {
      const { h } = C;
      const open = fold ? st.open.has(k) : !st.shut.has(k);
      const toggle = () => { const s = fold ? st.open : st.shut; if (s.has(k)) s.delete(k); else s.add(k); this.update(); };
      return h('section', { class: 'panel pl-group', dataset: { key: 'g:' + k } },
        h('button', { type: 'button', class: 'panel-h pl-group-h' + (open ? ' open' : ''), 'aria-expanded': String(open), onclick: toggle },
          C.chev(), h('h2', { text: title }), h('span', { class: 'muted num', text: String(rows.length) })),
        open ? h('div', { class: 'panel-b' }, planTable(rows, showProject)) : null);
    },
    keys() { return false; },
  };
  function card(p, showProject) {
    const { h } = C;
    const open = () => { location.hash = planHash(p.project, p.key); };
    const cur = currentLine(p);
    let now = null;
    if (p.status === 'blocked' && p.blocked) {
      now = h('div', { class: 'now' }, h('div', { class: 'now-label' }, cur ? cur.label : 'Waiting for you',
        h('a', { class: 'link-u', href: sessHash(p.blocked.session, p.blocked.agent_id), onclick: (e) => e.stopPropagation(), text: 'Open the decision' })),
      h('div', { class: 'now-text', text: p.blocked.message || p.blocked.title || (cur ? cur.name : '') }));
    } else if (cur) {
      now = h('div', { class: 'now' }, h('div', { class: 'now-label' }, cur.label, cur.note ? h('span', { class: 'faint', text: cur.note }) : null, p.status === 'live' ? whoLine(p) : null),
        inline(cur.name, 'now-text'));
    }
    const e = eta(p);
    return h('article', { class: 'card pl-card', tabindex: 0, role: 'link', 'aria-label': p.title + ', ' + progressText(p), dataset: { key: p.project + '\u0000' + p.key },
      onclick: open, onkeydown: (ev) => { if (ev.key === 'Enter' && ev.target === ev.currentTarget) open(); } },
    h('div', { class: 'card-top' }, pill(p), showProject ? h('span', { class: 'proj', text: p.project }) : null, h('span', { class: 'spacer' }),
      p.last_activity ? h('span', { class: 'faint nowrap', dataset: { ago: p.last_activity }, text: C.ago(p.last_activity), title: C.dateTime(p.last_activity) }) : null),
    h('div', { class: 'card-title', text: p.title }),
    h('div', { class: 'pl-prog' }, bar(p), h('div', { class: 'pl-prog-l' }, h('b', { text: label(p) }), measure(p) ? h('span', { class: 'muted', text: measure(p) }) : null, h('span', { class: 'spacer' }),
      e ? h('span', { class: 'muted', text: e }) : null)),
    now,
    h('div', { class: 'card-foot' },
      p.started ? h('span', {}, 'started ', h('b', { text: C.dateTime(p.started) })) : h('span', { text: 'no session has run it in this window' }),
      p.median_task_secs ? h('span', {}, h('b', { text: C.dur(p.median_task_secs) }), ' per task (median working time)') : null,
      h('span', { class: 'spacer' }), h('span', { class: 'mono ellipsis', text: p.key, title: p.path })));
  }

  // ── the plan page ───────────────────────────────────────────────────────
  views.plan = {
    mount(root) {
      const { h } = C;
      this.o = { crumb: h('a', { class: 'crumb' }), head: h('div', { class: 'd-head' }), kpis: h('div', {}), body: h('div', {}) };
      root.append(this.o.crumb, this.o.head, this.o.kpis, this.o.body);
      this.sig = null;
      this.open = new Set();
      poll();
    },
    update() {
      const { h, P } = C;
      const r = C.route();
      const o = this.o;
      P.attr(o.crumb, 'href', plansHash(r.p));
      C.setText(o.crumb, '← Plans');
      const k = detailKey();
      const d = st.detail[k];
      if (!d) {
        if (st.derr[k]) P.into(o.head, C.errorBox(st.derr[k], 'this plan'));
        else if (!o.head.firstChild) o.head.append(h('div', { class: 'sk sk-line', style: { width: '45%', height: '22px' } }), h('div', { class: 'sk sk-line', style: { width: '30%' } }));
        return;
      }
      const p = d.plan;
      const sig = JSON.stringify([p, [...this.open], C.theme()]);
      if (sig === this.sig) return;
      const first = this.sig == null;
      this.sig = sig;
      if (first) { o.head.replaceChildren(); o.kpis.replaceChildren(); }
      const ex = p.executors.slice().sort((a, b) => b.last_write - a.last_write)[0];
      P.into(o.head, h('h1', { class: 'd-title', text: p.title }),
        p.goal ? inline(p.goal, 'pl-goal') : null,
        h('div', { class: 'd-meta' }, pill(p), h('span', { style: { color: 'var(--ink)', fontWeight: 500 }, text: p.project }),
          p.started ? h('span', { text: 'started ' + C.dateTime(p.started) }) : null,
          p.last_activity ? h('span', {}, 'last activity ', h('span', { dataset: { ago: p.last_activity }, text: C.ago(p.last_activity) })) : null,
          p.doc ? h('a', { class: 'link-u', href: docHash(p.project, p.doc), text: 'Open plan' }) : null,
          ex ? h('a', { class: 'link-u', href: sessHash(ex.session), text: 'Open session' }) : null,
          p.blocked ? h('a', { class: 'link-u', href: sessHash(p.blocked.session, p.blocked.agent_id), text: 'Open the decision' }) : null,
          h('span', { class: 'faint mono ellipsis', text: p.path, title: p.copies > 1 ? 'Newest of ' + p.copies + ' copies (worktrees)' : null })));
      const tasks = p.task_list;
      const nCommits = p.commits.length;
      const cur = currentLine(p);
      P.into(o.kpis, h('div', { class: 'kpis' },
        C.kpi('Tasks done', [String(p.tasks.done), 'of ' + p.tasks.total], p.source.tasks ? 'from ' + p.source.tasks : 'nothing done yet', 'hero'),
        C.kpi('Steps ticked', p.steps.total ? pctOf(p.steps.done, p.steps.total) + '%' : 'n/a', p.steps.total ? p.steps.done + ' of ' + p.steps.total + ' checkboxes' : 'the plan has no checkboxes'),
        C.kpi(cur ? cur.label : 'Current task', p.current ? 'Task ' + (p.current.id || p.current.pos) : p.status === 'done' ? 'Done' : '\u2013',
          cur && cur.note ? cur.note : p.current && p.current.who ? (p.current.who.agent_id ? p.current.who.model || 'subagent' : 'main thread') : null),
        C.kpi('Per task', p.median_task_secs ? C.dur(p.median_task_secs) : 'n/a', p.median_task_secs ? 'median working time of ' + (p.eta ? p.eta.basis : tasks.filter((t) => t.active != null).length) + ' tasks' : 'needs 2 timed tasks'),
        C.kpi('Estimate', p.eta ? C.dur(p.eta.secs_left) : 'n/a', p.eta ? (p.eta.at ? 'left, done about ' + C.clock(p.eta.at, false) : 'of work left') + ' (est.)' : 'shown once 2 tasks are done'),
        C.kpi('Commits', String(nCommits), nCommits ? 'in the tasks’ windows' : 'none matched')));
      const body = [
        h('section', { class: 'panel', dataset: { key: 'progress' } },
          h('div', { class: 'panel-h' }, h('h2', { text: 'Progress' }), h('span', { class: 'muted', text: progressText(p) })),
          h('div', { class: 'panel-b' }, bar(p, 'big'), milestones(p), progressChart(p, d.now))),
        h('section', { class: 'panel', dataset: { key: 'tasks' } },
          h('div', { class: 'panel-h' }, h('h2', { text: p.mode === 'todo' ? 'Todo list' : 'Tasks' }),
            h('span', { class: 'muted', text: p.mode === 'todo' ? 'The plan has no tasks or checkboxes; this is the executing session’s todo list.' : 'Click a task for its steps, agents and commits.' })),
          h('div', { class: 'panel-b pl-tasks' }, tasks.length ? tasks.map((t) => this.task(p, t)) : h('div', { class: 'faint', text: 'No tasks.' }))),
        p.loose && p.loose.length ? h('section', { class: 'panel', dataset: { key: 'loose' } },
          h('div', { class: 'panel-h' }, h('h2', { text: 'Other checklists' }), h('span', { class: 'muted', text: 'outside any task; not counted' })),
          h('div', { class: 'panel-b' }, h('ul', { class: 'pl-steps' }, p.loose.map((sp, i) => step(sp, i))))) : null,
        p.sessions.length ? h('section', { class: 'panel', dataset: { key: 'sessions' } },
          h('div', { class: 'panel-h' }, h('h2', { text: 'Sessions' }), h('span', { class: 'muted', text: 'that executed, wrote or read this plan in this window' })),
          h('div', { class: 'panel-b' }, h('table', { class: 'compact' }, h('tbody', {}, p.sessions.map((s) => h('tr', { dataset: { key: s.id } },
            h('td', {}, h('a', { class: 'link-u', href: sessHash(s.id), text: s.title }), h('div', { class: 'muted', text: s.role === 'executing' ? 'Executed it' : s.role === 'wrote' ? 'Wrote it' : 'Read it' })),
            h('td', {}, s.live ? C.stPill('working', 'Working') : null),
            h('td', { class: 'r muted', text: C.dateTime(s.last_write) }))))))) : null,
      ];
      P.into(o.body, h('div', { class: 'stack' + (first ? ' fade-in' : ''), dataset: { key: 'plan' } }, body));
    },
    task(p, t) {
      const { h } = C;
      const k = 't' + t.pos;
      const running = t.agents.find((a) => a.status === 'running');
      const isCur = p.current && p.current.pos === t.pos;
      const blocked = isCur && p.status === 'blocked';
      const state = t.done ? 'done' : blocked ? 'needs' : running || (isCur && p.status === 'live') ? 'working' : 'pending';
      const impl = t.agents.filter((a) => a.role === 'implement' || a.role === 'fix');
      const who = (impl.length ? impl[impl.length - 1] : t.agents[t.agents.length - 1]) || null;
      let when;
      if (t.done) {
        // Working time, pauses over 30 minutes left out (a resumed subagent's idle hours are not work);
        // a wall clock that still holds such a pause is said to.
        const paused = t.wall != null && t.active != null && t.wall - t.active > 1800;
        const took = t.active != null ? h('span', { class: 'faint', text: ' \u00b7 ' + C.dur(t.active), title: paused ? C.dur(t.wall) + ' wall clock; pauses over 30 minutes left out' : 'working time' })
          : t.wall != null ? h('span', { class: 'faint', text: ' \u00b7 ' + C.dur(t.wall) + ' with pauses', title: 'Wall clock from start to finish, including pauses' }) : null;
        when = t.finished ? [h('span', { text: C.dateTime(t.finished) }), took] : [h('span', { class: 'faint', text: 'done, time unknown' })];
      } else if (state === 'working' && (running || t.started)) {
        const since = running ? running.start : t.started;
        when = [h('span', { class: 'num', dataset: { since }, text: C.dur(C.now() - since) })];
      } else if (blocked) {
        when = [h('span', { text: 'blocked' })];
      } else when = [h('span', { class: 'faint', text: t.started ? 'started ' + C.dateTime(t.started) : 'pending' })];
      const open = this.open.has(k);
      const sum = h('summary', { class: 'pl-task-h' },
        C.glyph(state), h('span', { class: 'pl-tid mono', text: t.id != null ? 'Task ' + t.id : String(t.pos) }), inline(t.title, 'pl-ttl'),
        t.steps_total ? h('span', { class: 'pl-steps-n num muted', text: t.steps_done + '/' + t.steps_total }) : null,
        who ? h('span', { class: 'pl-agent' }, C.key(who.family || C.famOf(who.model)), h('span', { class: 'ellipsis', text: who.model || who.role })) : h('span', { class: 'pl-agent' }),
        h('span', { class: 'pl-when nowrap' }, when),
        h('span', { class: 'pl-cm num muted', text: t.commits.length ? C.plural(t.commits.length, 'commit') : '' }));
      const det = h('details', { class: 'pl-task c-' + state + (isCur ? ' cur' : ''), dataset: { key: k }, open: open || null,
        ontoggle: (e) => {
          const was = this.open.has(k);
          if (e.currentTarget.open) this.open.add(k); else this.open.delete(k);
          if (was !== this.open.has(k)) this.update();   // build or drop the body; the sig includes the open set
        } }, sum);
      if (open) det.append(h('div', { class: 'pl-task-b' },
        t.steps.length ? h('ul', { class: 'pl-steps' }, t.steps.map((sp, i) => step(sp, i))) : null,
        t.agents.length ? h('div', { class: 'pl-sub' }, h('h3', { text: 'Agents' }), h('ul', { class: 'pl-list' }, t.agents.map((a, i) => h('li', { dataset: { key: 'a' + i } },
          a.agent_id ? h('a', { class: 'link-u ellipsis', href: sessHash(a.session, a.agent_id), text: a.label }) : h('span', { class: 'ellipsis', text: a.label }),
          a.model ? C.modelTag(a.model, a.family) : null,
          h('span', { class: a.status === 'failed' ? 'bad' : 'muted', text: a.status === 'running' ? 'running' : a.status === 'done' ? 'finished' : a.status === 'stopped' ? 'did not finish' : a.status }),
          h('span', { class: 'faint num nowrap', text: a.start ? C.clock(a.start, false) + (a.active ? ' \u00b7 ' + C.dur(a.active) + ' working' : a.end ? ' \u00b7 ' + C.dur(a.end - a.start) : '') : '' }))))) : null,
        t.commits.length ? h('div', { class: 'pl-sub' }, h('h3', { text: 'Commits' }), h('ul', { class: 'pl-list' }, t.commits.map((c) => h('li', { dataset: { key: c.hash } },
          h('span', { class: 'mono', text: c.short }), h('span', { class: 'ellipsis', text: c.subject }), h('span', { class: 'faint num nowrap', text: C.dateTime(c.t) }))))) : null,
        h('div', { class: 'pl-src faint' }, srcText(t))));
      return det;
    },
    keys() { return false; },
  };
  function srcText(t) {
    const done = { checkboxes: 'all its checkboxes are ticked', ledger: 'the SDD ledger marks it complete', todo: 'its todo item is completed', agent: 'its implementer subagent finished (nobody ticks this plan)' }[t.done_src];
    const fin = { tick: 'the Edit that ticked its last step', ledger: 'the command that wrote its ledger line', 'bash tick': 'the command that ticked it', agent: 'its last agent’s end', todo: 'the todo update' }[t.finished_src];
    if (!t.done) return t.started ? 'In progress since ' + C.dateTime(t.started) + '.' : 'Not started.';
    return 'Done because ' + done + '.' + (fin ? ' Finish time from ' + fin + '.' : '') + (t.ledger ? ' Ledger: ' + t.ledger : '');
  }
  function step(sp, i) {
    const { h } = C;
    return h('li', { class: sp.done ? 'on' : null, dataset: { key: 'p' + i }, style: sp.indent ? { paddingLeft: Math.min(48, sp.indent * 4) + 'px' } : null },
      C.glyph(sp.done ? 'done' : 'pending'), inline(sp.text), sp.t ? h('span', { class: 'faint num nowrap', text: C.dateTime(sp.t) }) : h('span'));
  }
  function milestones(p) {
    const { h } = C;
    if (!p.segments || p.segments.length < 2 || p.segments.length > 12 || p.segments.every((s) => s.tasks === 1)) return null;
    return h('ol', { class: 'pl-ms' }, p.segments.map((s, i) => h('li', { dataset: { key: 'm' + i } },
      h('span', { class: 'pl-ms-t ellipsis', text: s.title, title: s.title }), h('span', { class: 'num muted', text: s.tasks_done + ' of ' + s.tasks + ' tasks' }))));
  }
  /* Cumulative tasks done over time: a step line, one dot per finished task, hover for which. */
  function progressChart(p, now) {
    const { h, s } = C;
    const pts = p.task_list.filter((t) => t.done && t.finished).sort((a, b) => a.finished - b.finished);
    if (pts.length < 1 || !p.started) {
      return h('p', { class: 'insight', dataset: { key: 'nochart' }, text: p.done_untimed ? p.done_untimed + ' tasks are done but no transcript in this window shows when, so there is no timeline.' : 'The timeline appears when the first task is done.' });
    }
    const host = h('div', { class: 'chart pl-chart', dataset: { key: 'chart' } });
    const t0 = Math.min(p.started, pts[0].finished);
    const t1 = Math.max(pts[pts.length - 1].finished, ['live', 'active', 'blocked'].includes(p.status) ? now : 0);
    return C.chart(host, [pts.map((t) => t.finished), t0, Math.round(t1 / 60), p.tasks.total], (el) => {
      const W = Math.max(280, el.clientWidth || 600), H = 220, m = { t: 12, r: 12, b: 26, l: 36 };
      const iw = W - m.l - m.r, ih = H - m.t - m.b;
      const total = Math.max(1, p.tasks.total);
      const x = (t) => m.l + (t1 > t0 ? ((t - t0) / (t1 - t0)) * iw : iw);
      const y = (n) => m.t + ih - (n / total) * ih;
      const svg = s('svg', { viewBox: `0 0 ${W} ${H}`, height: H, role: 'img', 'aria-label': 'Tasks done over time: ' + pts.length + ' of ' + total + ' with a finish time' });
      const step = Math.max(1, C.niceStep(total / 4));
      for (let v = 0; v <= total; v += step) {
        svg.append(s('line', { 'data-key': 'g' + v, class: v === 0 ? 'base-l' : 'grid-l', x1: m.l, x2: W - m.r, y1: Math.round(y(v)) + 0.5, y2: Math.round(y(v)) + 0.5 }),
          s('text', { 'data-key': 'y' + v, x: m.l - 8, y: y(v) + 3.5, 'text-anchor': 'end' }, String(v)));
      }
      if (total % step) svg.append(s('line', { 'data-key': 'gt', class: 'grid-l', x1: m.l, x2: W - m.r, y1: Math.round(y(total)) + 0.5, y2: Math.round(y(total)) + 0.5 }),
        s('text', { 'data-key': 'yt', x: m.l - 8, y: y(total) + 3.5, 'text-anchor': 'end' }, String(total)));
      const span = t1 - t0;
      const ticks = 4;
      for (let i = 0; i <= ticks; i++) {
        const t = t0 + (span * i) / ticks;
        const lbl = span > 2 * 86400 ? C.dateOnly(t) : span > 86400 ? C.dateOnly(t) + ' ' + C.clock(t, false) : C.clock(t, false);
        svg.append(s('text', { 'data-key': 'x' + i, x: x(t), y: H - 8, 'text-anchor': i === 0 ? 'start' : i === ticks ? 'end' : 'middle' }, lbl));
      }
      let d = `M${x(t0)},${y(0)}`;
      pts.forEach((t, i) => { d += `H${x(t.finished)}V${y(i + 1)}`; });
      d += `H${x(t1)}`;
      svg.append(s('path', { 'data-key': 'line', d, class: 'pl-line' }));
      pts.forEach((t, i) => {
        const cx = x(t.finished), cy = y(i + 1);
        const tipRows = [{ name: 'Tasks done', value: String(i + 1) + ' of ' + total, color: 'var(--series-main)' }];
        const show = (ev) => C.showTip(ev.clientX != null ? ev : { clientX: ev.currentTarget.getBoundingClientRect().right, clientY: ev.currentTarget.getBoundingClientRect().top },
          C.dateTime(t.finished) + ' · Task ' + (t.id || t.pos) + ': ' + t.title, tipRows);
        svg.append(s('circle', { 'data-key': 'd' + i, class: 'pl-dot', cx, cy, r: 4 }),
          s('circle', { 'data-key': 'h' + i, class: 'hit', cx, cy, r: 10, tabindex: 0, 'aria-label': 'Task ' + (t.id || t.pos) + ' done ' + C.dateTime(t.finished),
            onpointerenter: show, onpointermove: C.moveTip, onpointerleave: C.hideTip, onfocus: show, onblur: C.hideTip }));
      });
      C.P.into(el, svg);
    });
  }

  /* Plans in a scope ('' = all projects) with activity in the window: the count on the Plans tab. */
  function count(p) {
    if (!st.list) return null;
    if (!p) return st.list.plans.length;
    const x = st.list.projects.find((y) => y.project === p);
    return x ? x.recent : 0;
  }
  function init(ctx) {
    C = ctx;
    setTimeout(poll, 0);
    return { views, route, isPlanView, chip, overviewPanel, count, sig: () => st.chipSig, poll };
  }
  window.ccPlans = { init };
})();
