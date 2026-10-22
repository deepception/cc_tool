/* cc-dash session Agents tab: the recursive agent tree (/api/session/tree,
   tree.py) as four charts and an expandable table, plus the one effort chip
   every view uses.

   Charts, all hand-drawn SVG in the existing chart style and tooltip:
     depth     cost (or tokens) per spawn level, stacked by model
     matrix    model x effort: agents and cost per cell, a solid dot for an
               effort recorded or asked for, a hollow one for an inferred one
     thinking  thinking tokens as a share of output, per model and effort
     icicle    subtree cost: each row one level deeper, width is cost
   then the tree table and the nested "when each agent ran" timeline, which
   share one expand/collapse state.

   Effort is shown in one way everywhere: a .tag reading the level; an
   inferred effort (inherited from the parent, or the model default) is the
   same tag dashed and unfilled, with the reason in its tooltip.

   Everything is rebuilt each poll with the app's builders and morphed in
   place (rows keyed by node id); charts redraw only when their data
   signature changes. Exposes window.ccAgents; app.js calls init(). */
'use strict';
(() => {
  let A = null;
  const LEVELS = ['low', 'medium', 'high', 'xhigh', 'max'];
  const SOURCE = {
    recorded: 'recorded on its API requests',
    requested: 'asked for in the call that spawned it',
    inherited: 'not recorded; assumed from its parent, which ran at this effort',
    default: 'not recorded; assumed from the model default',
    unknown: 'not recorded, and the model default is not known',
  };

  function effortTag(e, opts) {
    const { h } = A;
    const o = opts || {};
    if (!e || !e.value) return o.always ? h('span', { class: 'tag inferred', text: o.bare ? 'n/a' : 'effort n/a', title: 'Effort ' + SOURCE.unknown }) : null;
    const why = 'Effort ' + e.value + ': ' + SOURCE[e.source] + (e.requested ? ' (the spawn call asked for ' + e.requested + ')' : '') + '.';
    return h('span', { class: 'tag' + (e.explicit ? '' : ' inferred'), text: (o.bare ? '' : 'effort ') + e.value, title: why });
  }

  // ── tree helpers ──────────────────────────────────────────────────────
  const isOpen = (n) => {
    const t = A.S.ui.tcol;
    if (t.has(n.id)) return t.get(n.id);
    return !(n.kind === 'workflow' && n.children.length > 8) && n.children.length <= 40;
  };
  function visible(by, root) {
    const out = [];
    const walk = (id) => {
      const n = by.get(id);
      if (!n) return;
      out.push(n);
      if (isOpen(n)) for (const c of n.children) walk(c);
    };
    walk(root);
    return out;
  }
  const label = (n) => (n.kind === 'main' ? 'Main thread' : n.label || n.agent_type || n.id);
  const famVar = (n) => (n.kind === 'workflow' ? 'var(--ink-4)' : 'var(--' + (n.family || 'other') + ')');
  function go(n, ctx) {
    if (n.kind === 'main') { ctx.tab('timeline'); return; }
    if (n.kind === 'agent' && n.agent_id && !n.missing) location.hash = '#/s/' + encodeURIComponent(ctx.sid) + '/a/' + encodeURIComponent(n.agent_id);
  }
  function toggle(n, ctx) { A.S.ui.tcol.set(n.id, !isOpen(n)); ctx.redraw(); }
  function statusOf(n) {
    const { stPill, subStatus, statePill } = A;
    if (n.kind === 'main') return n.status === 'ended' ? A.h('span', { class: 'faint', text: 'Ended' }) : statePill(n.status);
    if (n.missing) return n.status === 'failed' ? stPill('failed', 'Spawn failed') : stPill('idle', 'No transcript');
    return subStatus(n.status);
  }
  function tipRows(n, total) {
    const { money, compact, dur, pctStr } = A;
    const kids = n.children.length;
    return [
      n.model ? { fam: n.family, name: 'Model', value: n.model } : null,
      n.effort && n.effort.value ? { name: 'Effort', value: n.effort.value + (n.effort.explicit ? '' : ' (' + n.effort.source + ')') } : null,
      { name: 'Own cost', value: money(n.own.cost, true) },
      kids ? { name: 'With its agents', value: money(n.sub.cost, true) } : null,
      total ? { name: 'Share of session', value: pctStr(n.sub.cost, total) } : null,
      { name: 'New tokens', value: (n.sub.output_estimated ? '≥' : '') + compact(kids ? n.sub.tokens : n.own.tokens) },
      { name: 'Cache reads', value: compact(kids ? n.sub.cache_read : n.own.cache_read) },
      n.own.active ? { name: 'Active', value: dur(n.own.active) } : null,
    ].filter(Boolean);
  }
  function hover(el, title, rows) {
    const { P, showTip, moveTip, hideTip } = A;
    P.listen(el, 'pointerenter', (ev) => showTip(ev, title, rows()));
    P.listen(el, 'pointermove', moveTip);
    P.listen(el, 'pointerleave', hideTip);
    P.listen(el, 'focus', (ev) => { const b = ev.currentTarget.getBoundingClientRect(); showTip({ clientX: b.right, clientY: b.top + 10 }, title, rows()); });
    P.listen(el, 'blur', hideTip);
  }

  // ── (a) cost or tokens per level ──────────────────────────────────────
  function depthPanel(t, ctx) {
    const { h, chart, columns, money, compact, plural, FAMILIES, FAMILY_NAME, key, S } = A;
    const by = ['tokens', 'cache'].includes(S.ui.depthBy) ? S.ui.depthBy : 'cost';
    const lv = t.summary.levels;
    const field = { cost: 'fam_cost', tokens: 'fam_tokens', cache: 'fam_cache' }[by];
    const fams = FAMILIES.filter((f) => lv.some((l) => (l[field][f] || 0) > 0));
    const fmt = by === 'cost' ? (v, axis) => (axis ? (v >= 1 || v === 0 ? '$' + compact(v) : '$' + v.toFixed(2)) : money(v, true)) : (v) => compact(v);
    const buckets = lv.map((l) => ({ label: l.level ? 'Depth ' + l.level : 'Main', values: l[field],
      title: (l.level ? 'Depth ' + l.level + (l.level === 1 ? ': spawned by the main thread' : ': spawned at depth ' + (l.level - 1)) : 'The main thread') + ', ' + plural(l.agents, 'agent') }));
    const series = fams.map((f) => ({ k: f, name: FAMILY_NAME[f], fam: f }));
    const seg = h('div', { class: 'seg', role: 'group', 'aria-label': 'Measure' }, [['cost', 'Cost'], ['tokens', 'New tokens'], ['cache', 'Cache reads']].map(([k, n]) =>
      h('button', { type: 'button', dataset: { key: k }, 'aria-pressed': String(by === k), onclick: () => { S.ui.depthBy = k; ctx.redraw(); } }, n)));
    const host = chart(h('div', { class: 'chart', dataset: { key: 'depth-' + by } }), [buckets, fams, by], (el) => columns(el, { buckets, series, fmt, height: 196 }));
    return h('section', { class: 'panel', dataset: { key: 'depth' } },
      h('div', { class: 'panel-h' }, h('h2', { text: { cost: 'Cost', tokens: 'New tokens', cache: 'Cache reads' }[by] + ' by depth' }), seg),
      h('div', { class: 'panel-b' }, fams.length > 1 ? h('div', { class: 'legend legend-above' }, fams.map((f) => h('span', { dataset: { key: f } }, key(f), FAMILY_NAME[f]))) : null, host));
  }

  // ── (d) model x effort ────────────────────────────────────────────────
  function matrixPanel(t) {
    const { h, s, P, chart, money, plural, int } = A;
    const cells = t.summary.matrix;
    const models = [...new Set(cells.map((c) => c.model))];
    const modelCost = (m) => cells.filter((c) => c.model === m).reduce((a, c) => a + c.explicit.cost + c.inferred.cost, 0);
    models.sort((a, b) => modelCost(b) - modelCost(a));
    const cols = LEVELS.concat(cells.some((c) => !LEVELS.includes(c.effort)) ? ['unknown'] : []);
    const maxCost = Math.max(1e-9, ...cells.map((c) => Math.max(c.explicit.cost, c.inferred.cost)));
    const anyInferred = cells.some((c) => c.inferred.agents);
    const anyExplicit = cells.some((c) => c.explicit.agents);
    const draw = (el) => {
      const W = Math.max(260, el.clientWidth || 420);
      const lw = Math.min(104, Math.round(W * 0.27)), top = 18, rh = 46, legend = 26;
      const cw = (W - lw) / cols.length;
      const H = top + rh * models.length + legend;
      const svg = s('svg', { viewBox: `0 0 ${W} ${H}`, height: H, role: 'img', 'aria-label': 'Agents by model and effort' });
      cols.forEach((c, i) => svg.append(s('text', { 'data-key': 'c' + c, x: lw + cw * i + cw / 2, y: 11, 'text-anchor': 'middle' }, c === 'unknown' ? 'n/a' : c)));
      models.forEach((m, r) => {
        const y = top + rh * r;
        svg.append(s('line', { 'data-key': 'gl' + r, class: 'grid-l', x1: 0, x2: W, y1: Math.round(y) + 0.5, y2: Math.round(y) + 0.5 }));
        svg.append(s('text', { 'data-key': 'm' + r, class: 'lbl', x: 0, y: y + rh / 2 + 4 }, m.length > 14 ? m.slice(0, 13) + '…' : m));
        cols.forEach((c, i) => {
          const cell = cells.find((x) => x.model === m && (x.effort === c || (c === 'unknown' && !LEVELS.includes(x.effort))));
          if (!cell) return;
          const cx = lw + cw * i + cw / 2, cy = y + rh / 2 - 4;
          const both = cell.explicit.agents && cell.inferred.agents;
          const rad = (cost) => 4 + 8 * Math.sqrt(cost / maxCost);
          const g = s('g', { 'data-key': 'g' + r + c });
          const fill = 'var(--' + cell.family + ')';
          if (cell.explicit.agents) g.append(s('circle', { cx: both ? cx - 9 : cx, cy, r: rad(cell.explicit.cost), fill }));
          if (cell.inferred.agents) g.append(s('circle', { cx: both ? cx + 9 : cx, cy, r: rad(cell.inferred.cost) - 1, fill: 'none', stroke: fill, 'stroke-width': 2 }));
          g.append(s('text', { x: cx, y: y + rh - 5, 'text-anchor': 'middle' }, int(cell.explicit.agents + cell.inferred.agents)));
          const hit = s('rect', { class: 'hit', x: lw + cw * i, y, width: cw, height: rh, tabindex: 0,
            'aria-label': m + ' at ' + c + ': ' + plural(cell.explicit.agents + cell.inferred.agents, 'agent') + ', ' + money(cell.explicit.cost + cell.inferred.cost, true) });
          hover(hit, m + ' at ' + (c === 'unknown' ? 'an unknown effort' : c + ' effort'), () => [
            cell.explicit.agents ? { fam: cell.family, name: 'Recorded or asked for', value: plural(cell.explicit.agents, 'agent') + ', ' + money(cell.explicit.cost, true) } : null,
            cell.inferred.agents ? { name: 'Inferred', value: plural(cell.inferred.agents, 'agent') + ', ' + money(cell.inferred.cost, true) } : null].filter(Boolean));
          svg.append(g, hit);
        });
      });
      const ly = H - 8;
      let lx = 0;
      if (anyExplicit) {
        svg.append(s('circle', { 'data-key': 'lg1', cx: lx + 5, cy: ly - 4, r: 4.5, fill: 'var(--ink-3)' }), s('text', { 'data-key': 'lt1', x: lx + 14, y: ly }, 'recorded or asked for'));
        lx += 138;
      }
      if (anyInferred) svg.append(s('circle', { 'data-key': 'lg2', cx: lx + 5, cy: ly - 4, r: 3.5, fill: 'none', stroke: 'var(--ink-3)', 'stroke-width': 2 }), s('text', { 'data-key': 'lt2', x: lx + 14, y: ly }, 'inferred (parent or model default)'));
      P.into(el, svg);
    };
    const host = chart(h('div', { class: 'chart', dataset: { key: 'matrix' } }), [cells, cols], draw);
    return h('section', { class: 'panel', dataset: { key: 'matrix' } },
      h('div', { class: 'panel-h' }, h('h2', { text: 'Model and effort' }), h('span', { class: 'muted', text: 'agents per cell, dot size is cost' })),
      h('div', { class: 'panel-b' }, host));
  }

  // ── (e) thinking share ────────────────────────────────────────────────
  function thinkingPanel(t) {
    const { h, compact, int, pctStr, plural, key } = A;
    const rows = t.summary.thinking.filter((r) => r.output > 0);
    const b = t.summary.thinking_blocks;
    const max = 100;
    const body = rows.length ? h('div', { class: 'hbars' }, rows.slice(0, 8).map((r) => {
      const p = 100 * r.thinking / r.output;
      const inferred = r.explicit < r.agents;
      const name = r.model + ' ' + (r.effort === 'unknown' ? 'n/a' : r.effort) + (inferred ? '?' : '');
      return h('div', { class: 'hbar', dataset: { key: r.model + '|' + r.effort } },
        h('span', { class: 'hn', text: name, title: name + (inferred ? ' (effort inferred for some agents)' : '') }),
        h('div', { class: 'track', role: 'img', 'aria-label': name + ': thinking is ' + pctStr(r.thinking, r.output) + ' of ' + compact(r.output) + ' output tokens' },
          r.thinking ? h('span', { class: 'f-' + r.family, style: { width: (p * 100 / max) + '%', background: 'var(--c)' } }) : null,
          h('span', { style: { width: ((100 - p) * 100 / max) + '%', background: 'var(--series-sub)' } })),
        h('span', { class: 'hv', title: int(r.thinking) + ' thinking of ' + int(r.output) + ' output tokens, ' + plural(r.measured_msgs, 'reply', 'replies') }, pctStr(r.thinking, r.output)));
    })) : h('p', { class: 'muted', text: 'No reply in this tree saved a thinking count.' });
    const note = 'Measured on ' + int(b.measured_msgs) + ' of ' + plural(b.msgs, 'reply', 'replies') + ': a thinking count is saved only once a reply finishes, and subagent transcripts mostly save usage before that. '
      + plural(b.blocks, 'thinking block') + (b.blocks ? ', ' + (b.text_blocks ? int(b.text_blocks) + ' with text (' + compact(b.chars) + ' characters)' : 'none with text') + ', the rest signatures only.' : '.');
    return h('section', { class: 'panel', dataset: { key: 'thinking' } },
      h('div', { class: 'panel-h' }, h('h2', { text: 'Thinking share' }), rows.length ? h('div', { class: 'legend' }, h('span', {}, key(rows[0].family), 'thinking'), h('span', {}, h('span', { class: 'key', style: { background: 'var(--series-sub)' } }), 'other output')) : null),
      h('div', { class: 'panel-b' }, body, h('p', { class: 'insight', text: note })));
  }

  // ── (c) icicle of subtree cost ────────────────────────────────────────
  function iciclePanel(t, by, ctx) {
    const { h, s, P, chart, money, compact } = A;
    const root = t.nodes[0];
    const useCost = root.sub.cost > 0;
    const val = (n) => (useCost ? n.sub.cost : n.sub.tokens);
    const total = val(root);
    const maxDepth = Math.max(0, ...t.nodes.map((n) => n.depth));
    const sigNodes = t.nodes.map((n) => [n.id, val(n), n.depth, n.family, label(n)]);
    const draw = (el) => {
      const W = Math.max(280, el.clientWidth || 800), rh = 30, H = (maxDepth + 1) * rh;
      const svg = s('svg', { viewBox: `0 0 ${W} ${H}`, height: H, role: 'img', 'aria-label': 'Cost of each agent and everything it spawned, one row per level' });
      const place = (n, x0, w) => {
        if (w < 0.6 || !total) return;
        const y = n.depth * rh;
        const g = s('g', { 'data-key': n.id });
        const fill = famVar(n);
        const cw = Math.max(0.6, w - (w > 3 ? 2 : 0));
        g.append(s('rect', { x: x0, y, width: cw, height: rh - 2, fill, 'fill-opacity': 0.16 }));
        g.append(s('rect', { x: x0, y, width: cw, height: 3, fill }));
        const chars = Math.floor((cw - 12) / 6.4);
        if (chars >= 4) {
          const l = label(n);
          g.append(s('text', { class: 'lbl', x: x0 + 6, y: y + 19 }, l.length > chars ? l.slice(0, Math.max(1, chars - 1)) + '…' : l));
        }
        const hit = s('rect', { class: 'hit', x: x0, y, width: cw, height: rh - 2, tabindex: n.kind === 'workflow' ? null : 0, 'aria-label': label(n) + ': ' + (useCost ? money(n.sub.cost, true) : compact(n.sub.tokens)) });
        hover(hit, label(n), () => tipRows(n, useCost ? total : 0));
        P.listen(hit, 'click', () => go(n, ctx));
        if (n.kind === 'agent' && !n.missing) hit.setAttribute('cursor', 'pointer');
        g.append(hit);
        svg.append(g);
        let x = x0;
        const kids = n.children.map((id) => by.get(id)).filter(Boolean).sort((a, b) => val(b) - val(a));
        const v = val(n);
        for (const c of kids) {
          const cw2 = v ? w * val(c) / v : 0;
          place(c, x, cw2);
          x += cw2;
        }
      };
      place(root, 0, W);
      P.into(el, svg);
    };
    const host = chart(h('div', { class: 'chart', dataset: { key: 'icicle' } }), [sigNodes, useCost], draw);
    return h('section', { class: 'panel', dataset: { key: 'icicle' } },
      h('div', { class: 'panel-h' }, h('h2', { text: 'Where the ' + (useCost ? 'cost' : 'new tokens') + ' went' }), h('span', { class: 'muted', text: 'each row one level deeper; a block’s width is it plus everything it spawned' })),
      h('div', { class: 'panel-b' }, host));
  }

  // ── tree table ────────────────────────────────────────────────────────
  const tokTitle = (u) => A.compact(u.cache_read) + ' cache reads not included' + (u.output_estimated ? '; about ' + A.compact(u.output_estimated) + ' output tokens estimated from replies saved before they finished' : '');
  const TCOLS = [
    { label: 'Agent' }, { label: 'Model', w: '132px' }, { label: 'Effort', w: '86px' }, { label: 'Status', w: '128px' },
    { label: 'New tokens', r: true, w: '92px', title: 'Input, cache writes and output of this agent alone; cache reads left out' }, { label: 'With subagents', r: true, w: '112px', title: 'This agent plus everything it spawned, at any depth' },
    { label: 'Cost', r: true, w: '78px', title: 'This agent alone' }, { label: 'With subagents', r: true, w: '112px', title: 'This agent plus everything it spawned, at any depth' },
    { label: 'Active', r: true, w: '78px', title: 'Time spent working, pauses over 30 minutes left out' }, { label: 'Wall', r: true, w: '78px', title: 'First to last record' },
  ];
  function treePanel(t, rows, ctx) {
    const { h, chev, modelTag, compact, money, dur, plural } = A;
    const s0 = t.summary;
    const collapsible = t.nodes.filter((n) => n.children.length);
    const allOpen = collapsible.every(isOpen);
    const body = rows.map((n) => {
      const kids = n.children.length;
      const open = isOpen(n);
      const nav = n.kind === 'main' || (n.kind === 'agent' && !n.missing);
      const act = () => (nav ? go(n, ctx) : kids ? toggle(n, ctx) : null);
      const leafDash = (v) => (kids ? v : '–');
      return h('tr', { class: nav || kids ? 'link' : '', dataset: { key: n.id }, tabindex: nav || kids ? 0 : null,
        'aria-label': label(n) + (nav ? ': open its timeline' : ''), onclick: act, onkeydown: (e) => { if (e.key === 'Enter' && e.target === e.currentTarget) act(); } },
      h('td', {}, h('div', { class: 'p-name', style: n.depth ? { paddingLeft: ((n.depth - 1) * 18) + 'px' } : null },
        n.depth ? h('span', { class: 'faint', text: '└', 'aria-hidden': 'true' }) : null,
        kids ? h('button', { type: 'button', class: 'p-exp' + (open ? ' open' : ''), 'aria-expanded': String(open), 'aria-label': (open ? 'Hide' : 'Show') + ' the ' + plural(kids, 'agent') + ' under ' + label(n),
          onclick: (e) => { e.stopPropagation(); toggle(n, ctx); } }, chev()) : h('span', { class: 'p-exp-sp' }),
        n.kind === 'workflow' ? h('span', { class: 'tag', text: 'Workflow' }) : null,
        h('span', { class: 'p-label ellipsis', text: label(n), title: label(n) + (n.agent_type && n.kind === 'agent' ? ' (' + n.agent_type + ')' : '') }),
        n.kind === 'agent' && n.agent_type && !['workflow-subagent', 'general-purpose', 'subagent'].includes(n.agent_type) ? h('span', { class: 'faint mono nowrap', text: n.agent_type }) : null,
        n.phase ? h('span', { class: 'faint nowrap', text: n.phase }) : null,
        n.link === 'orphan' || n.link === 'cycle' ? h('span', { class: 'tag', text: 'unlinked', title: 'No spawn call matches this transcript; shown under the main thread' }) : null,
        n.link === 'meta' ? h('span', { class: 'faint nowrap', text: 'linked by meta', title: 'Linked through its parentAgentId; no spawn call matched' }) : null)),
      h('td', { class: 'nowrap ellipsis' }, n.model ? modelTag(n.model, n.family) : h('span', { class: 'faint', text: n.kind === 'workflow' ? '' : 'no API call' }),
        n.models && n.models.length > 1 ? h('span', { class: 'faint', text: ' +' + (n.models.length - 1), title: n.models.join(', ') }) : null),
      h('td', {}, effortTag(n.effort, { bare: true })),
      h('td', {}, statusOf(n)),
      h('td', { class: 'r' + (n.own.tokens ? '' : ' zero'), text: n.kind === 'workflow' ? '–' : (n.own.output_estimated ? '≥' : '') + compact(n.own.tokens), title: n.kind === 'workflow' ? null : tokTitle(n.own) }),
      h('td', { class: 'r' + (kids ? '' : ' zero'), text: leafDash((n.sub.output_estimated ? '≥' : '') + compact(n.sub.tokens)), title: kids ? tokTitle(n.sub) : null }),
      h('td', { class: 'r' + (n.own.cost ? '' : ' zero'), text: n.kind === 'workflow' ? '–' : money(n.own.cost) }),
      h('td', { class: 'r' + (kids ? '' : ' zero'), text: leafDash(money(n.sub.cost)) }),
      h('td', { class: 'r muted', text: dur(n.own.active) || '–' }),
      h('td', { class: 'r muted', text: dur(kids ? n.sub.wall : n.own.wall) || '–', title: kids ? 'With its subagents' : null }));
    });
    return h('section', { class: 'panel p-panel', dataset: { key: 'tree' } },
      h('div', { class: 'panel-h' }, h('div', {}, h('h2', { text: 'Agent tree' }),
        h('div', { class: 'muted p-sub', text: plural(s0.agents, 'subagent') + (s0.max_depth > 1 ? ', ' + s0.max_depth + ' levels deep' : '') + (s0.workflows ? ', ' + plural(s0.workflows, 'workflow') : '') + '. Click a row for its timeline.' })),
        collapsible.length ? h('button', { class: 'btn', type: 'button', text: allOpen ? 'Collapse all' : 'Expand all',
          onclick: () => { for (const n of collapsible) A.S.ui.tcol.set(n.id, !allOpen); ctx.redraw(); } }) : null),
      h('div', { class: 'panel-b' }, h('table', { class: 'compact p-table' },
        h('colgroup', {}, TCOLS.map((c) => h('col', { style: c.w ? { width: c.w } : null }))),
        h('thead', {}, h('tr', {}, TCOLS.map((c) => h('th', { class: c.r ? 'r' : '', text: c.label, title: c.title || null })))),
        h('tbody', {}, body))));
  }

  // ── (b) when each agent ran, nested ───────────────────────────────────
  function ganttPanel(t, rows, ctx) {
    const { h, timeAxis, dateOnly, clock, dur, nowS, FAMILIES, FAMILY_NAME, key } = A;
    const list = rows.filter((n) => n.kind !== 'main' && (n.start || n.sub.start));
    if (!list.length) return null;
    const startOf = (n) => (n.kind === 'workflow' ? n.sub.start : n.start);
    const endOf = (n) => {
      if (n.kind === 'workflow') return n.status === 'running' ? nowS() : (n.sub.end || n.sub.start);
      return n.status === 'running' ? nowS() : (n.end || n.start);
    };
    const t0 = Math.min(...list.map(startOf)), t1 = Math.max(...list.map(endOf));
    const ax = timeAxis(list.map((n) => [startOf(n), endOf(n)]), t0, t1);
    const multiDay = dateOnly(t0) !== dateOnly(t1);
    const breakAt = (sg) => (100 * (sg.x + (sg.b - sg.a) * ax.scale)) + '%';
    const labels = [];
    ax.segs.forEach((sg, i) => {
      labels.push(h('span', { class: 'gx', dataset: { key: 'x' + i }, style: { left: (100 * sg.x) + '%' }, text: (multiDay ? dateOnly(sg.a) + ' ' : '') + clock(sg.a, false) }));
      if (i < ax.segs.length - 1) labels.push(h('span', { class: 'gbreak', dataset: { key: 'b' + i }, style: { left: breakAt(sg), width: (100 * ax.gapW) + '%' }, title: dur(ax.segs[i + 1].a - sg.b) + ' with no agent running' }));
    });
    const breaks = () => ax.segs.slice(0, -1).map((sg, i) => h('span', { class: 'gbreak', dataset: { key: 'b' + i }, style: { left: breakAt(sg), width: (100 * ax.gapW) + '%' } }));
    const famCount = {};
    for (const n of list) if (n.kind === 'agent' && !n.missing) famCount[n.family] = (famCount[n.family] || 0) + 1;
    const shown = list.slice(0, 200);
    return h('section', { class: 'panel', dataset: { key: 'gantt' } },
      h('div', { class: 'panel-h' }, h('h2', { text: 'When each agent ran' }), h('div', { class: 'legend' }, FAMILIES.filter((f) => famCount[f]).map((f) => h('span', { dataset: { key: f } }, key(f), FAMILY_NAME[f], h('b', { text: famCount[f] }))))),
      h('div', { class: 'panel-b' },
        h('div', { class: 'gantt-axis' }, h('span'), h('div', { class: 'ga' }, labels)),
        shown.map((n) => {
          const l = 100 * ax.map(startOf(n)), r = 100 * ax.map(endOf(n));
          const ind = Math.max(0, n.depth - 1);
          const row = h('div', { class: 'gantt-row', dataset: { key: n.id }, onclick: () => (n.kind === 'workflow' ? toggle(n, ctx) : go(n, ctx)) },
            h('span', { class: 'gl', style: ind ? { paddingLeft: (8 + ind * 14) + 'px' } : null }, ind ? h('span', { class: 'faint', text: '└ ' }) : null,
              n.kind === 'workflow' ? 'Workflow: ' + label(n) : label(n)),
            h('div', { class: 'gt' }, breaks(), h('span', { class: 'gb ' + (n.kind === 'workflow' ? 'wf' : 'f-' + n.family) + (n.status === 'running' ? ' running' : '') + (n.status === 'failed' ? ' failed' : ''),
              style: { left: l + '%', width: Math.max(0.35, r - l) + '%' } })));
          hover(row, label(n), () => tipRows(n, 0).concat([{ name: 'Ran', value: A.dateTime(startOf(n)) + ', ' + dur(endOf(n) - startOf(n)) }]));
          return row;
        }),
        list.length > shown.length ? h('div', { class: 'faint', text: '+' + (list.length - shown.length) + ' more in the tree above' }) : null,
        h('p', { class: 'insight', text: (ax.segs.length > 1 ? 'Shaded gaps are idle stretches of more than 30 minutes, drawn narrow. ' : '') + 'Collapsed rows in the tree hide their agents here too.' })));
  }

  // ── the tab ───────────────────────────────────────────────────────────
  function tab(t, ctx) {
    const { h } = A;
    if (!t) return h('div', { class: 'stack' }, h('div', { class: 'grid g-3' }, [0, 1, 2].map(() => h('div', { class: 'panel panel-b' }, h('div', { class: 'sk sk-block' })))));
    const s0 = t.summary;
    if (!s0.agents && !s0.workflows && !s0.missing) {
      return h('div', { class: 'empty' }, h('h2', { text: 'No subagents' }), h('p', { text: 'This session did all its work in the main conversation.' }));
    }
    const by = new Map(t.nodes.map((n) => [n.id, n]));
    const rows = visible(by, t.nodes[0].id);
    return h('div', { class: 'stack' },
      h('div', { class: 'grid g-3', dataset: { key: 'charts' } }, depthPanel(t, ctx), matrixPanel(t), thinkingPanel(t)),
      iciclePanel(t, by, ctx),
      treePanel(t, rows, ctx),
      ganttPanel(t, rows, ctx),
      t.truncated ? h('p', { class: 'insight', text: 'This session has more agents than the tree shows (' + t.nodes.length + ' kept).' }) : null);
  }

  /* What decides whether the tab needs a redraw. */
  function sig(t) {
    if (!t) return null;
    return [t.nodes.length, t.nodes.map((n) => [n.id, n.status, n.own.tokens, n.sub.tokens, Math.round(n.sub.cost * 100), n.effort && n.effort.value, n.end]),
      A.S.ui.depthBy, [...A.S.ui.tcol]];
  }

  window.ccAgents = { init(helpers) { A = helpers; A.S.ui.tcol = new Map(); return { tab, sig, effortTag }; } };
})();
