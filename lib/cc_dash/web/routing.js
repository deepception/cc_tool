/* cc-dash routing: the Overview's "Routing" panel (routing.py, /api/routing).

   One panel in the Projects area's Overview, scoped like the rest of the page
   (one project, or all of them). It answers: who picked each subagent's model
   and effort, what that cost, and where it diverges from the managed block's
   Model routing rules. It is built from existing parts only: panel, stat
   labels, the compact table, the hbar, the .tag effort chip and the amber
   .pill count. app.js fetches the data with the Overview and calls panel();
   the panel patches in place with ccDom like everything else, so polls never
   blink. Every piece of transcript text goes in through textContent.
   Exposes window.ccRouting. */
'use strict';
(() => {
  let C = null;
  const enc = encodeURIComponent;
  const WHY = {
    no_model: ['No model named', 'No model was named in the call, so the harness picked one. The managed block says to name it on every call.'],
    execution_opus: ['Execution on Opus', 'Spec\'d execution: the managed block routes it to Sonnet.'],
    mechanical_opus: ['Read-only on Opus', 'Read-only locating or extracting: the managed block routes it to Haiku.'],
    mechanical_sonnet: ['Read-only on Sonnet', 'Read-only locating or extracting under 100K context: the managed block routes it to Haiku.'],
  };
  const SOURCE = {
    explicit: 'named in the Agent call', workflow: 'named by the workflow', type: 'set by the agent type\'s definition',
    default: 'not named: the harness picked it',
  };
  const TARGET = { execution_opus: 'Sonnet', mechanical_opus: 'Haiku or Sonnet', mechanical_sonnet: 'Haiku' };
  const LEVEL_NAME = { unrecorded: 'not recorded' };
  const sessHash = (sid, aid) => '#/s/' + enc(sid) + '/a/' + enc(aid);

  function stat(label, value, note, title) {
    const { h } = C;
    return h('div', { class: 'route-stat', dataset: { key: label }, title: title || null },
      h('div', { class: 'label', text: label }), h('div', { class: 'value', text: value }), h('div', { class: 'note', text: note }));
  }

  function stats(d) {
    const { money, int, pctStr, plural } = C;
    const df = d.default, ef = d.effort, rw = d.rewrites, c3 = d.ctx300;
    const opusHigh = ef.levels.filter((l) => l.effort === 'xhigh' || l.effort === 'max').reduce((a, l) => a + l.cost, 0);
    const named = ef.named_agents;
    return h4([
      stat('Picked by default', df.agents ? money(df.cost) : 'None',
        df.agents ? plural(df.agents, 'agent') + ' of ' + int(d.agents) + ', ' + pctStr(df.cost, d.cost) + ' of agent cost' : 'every agent had its model named',
        df.agents ? 'No model was named, so the harness picked one: ' + plural(df.opus_agents, 'agent') + ' ended up on Opus (' + money(df.opus_cost) + ').' : null),
      stat('Opus at xhigh or max', ef.opus_cost ? pctStr(opusHigh, ef.opus_cost) : 'None',
        ef.opus_cost ? 'of Opus agent cost; ' + int(named) + ' of ' + plural(ef.opus_agents, 'Opus agent') + ' asked for an effort' : 'no Opus agents here',
        'Subagents inherit the session\'s effort unless the call names one. Effort is read from the replies themselves.'),
      stat('Re-sending expired context', money(rw.sub.cost),
        plural(rw.sub.count, 'rewrite') + ', ' + pctStr(rw.sub.cost, rw.sub.spend) + ' of agent cost. Main sessions: ' + money(rw.main.cost) + ', ' + pctStr(rw.main.cost, rw.main.spend),
        'A reply that wrote at least 50K tokens of cache (half its context) after the cache had expired: idle more than ' + (rw.sub.ttl / 60) + ' minutes for a subagent, ' + (rw.main.ttl / 3600) + ' hour for a main session. '
        + 'Big writes after a shorter gap are left out (' + int(rw.sub.other_count + rw.main.other_count) + ', ' + money(rw.sub.other_cost + rw.main.other_cost) + ').'),
      stat('Context over 300K', money(c3.cost), plural(c3.agents, 'agent') + ', ' + pctStr(c3.cost, d.cost) + ' of agent cost',
        'Agents whose context passed 300K tokens, all their cost. The replies sent at that size alone cost ' + money(c3.msg_cost) + '.'),
    ]);
  }
  const h4 = (kids) => C.h('div', { class: 'route-stats' }, kids);

  // (a) cost by work type and model, flagged where the routing diverges
  function matrix(d) {
    const { h, money, int, key, FAMILY_NAME } = C;
    const fams = ['opus', 'sonnet', 'haiku'].concat(d.families.filter((f) => !['opus', 'sonnet', 'haiku'].includes(f)));
    const any = d.classes.some((r) => r.cells.some((c) => c.flagged_agents));
    const cellOf = (row, f) => row.cells.find((c) => c.family === f);
    return h('div', { class: 'route-sec', dataset: { key: 'matrix' } },
      h('h3', { text: 'Cost by work type and model' }),
      h('table', { class: 'compact route-table route-matrix' },
        h('thead', {}, h('tr', {}, h('th', { text: 'Work type' }), fams.map((f) => h('th', { class: 'r', dataset: { key: f } }, h('span', { class: 'model' }, key(f), FAMILY_NAME[f]))), h('th', { class: 'r', text: 'Total' }))),
        h('tbody', {}, d.classes.map((row) => h('tr', { dataset: { key: row.cls } },
          h('td', { text: row.label }),
          fams.map((f) => {
            const c = cellOf(row, f);
            if (!c || !c.agents) return h('td', { class: 'r zero', dataset: { key: f }, text: '–' });
            const money$ = money(c.cost);
            const tip = int(c.agents) + ' agents, ' + money(c.cost)
              + (c.flagged_agents ? '. ' + int(c.flagged_agents) + ' off the managed block\'s routing (' + money(c.flagged_cost) + ')' + (c.save ? '; the same tokens on the model it names would cost about ' + money(c.save) + ' less.' : '.') : '');
            return h('td', { class: 'r', dataset: { key: f }, title: tip },
              c.flagged_agents ? h('span', { class: 'pill warn', text: money$ }) : h('span', { text: money$ }),
              h('span', { class: 'faint route-n', text: int(c.agents) }));
          }),
          h('td', { class: 'r muted', text: money(row.cost) }))))),
      h('p', { class: 'insight', text: (any ? 'Amber: off the managed block\'s routing (execution on Opus, read-only work on Opus or Sonnet). ' : '') + 'Small figures are agents.' }));
  }

  // (c) effort mix of Opus agent cost
  function effort(d) {
    const { h, money, pctStr, int, plural } = C;
    const ef = d.effort;
    const rows = ef.levels.filter((l) => l.cost > 0);
    return h('div', { class: 'route-sec', dataset: { key: 'effort' } },
      h('h3', { text: 'Opus agents by effort' }),
      rows.length ? h('div', { class: 'hbars route-bars' }, rows.map((l) => h('div', { class: 'hbar', dataset: { key: l.effort } },
        h('span', { class: 'hn', text: LEVEL_NAME[l.effort] || l.effort }),
        h('div', { class: 'track', role: 'img', 'aria-label': (LEVEL_NAME[l.effort] || l.effort) + ': ' + pctStr(l.cost, ef.opus_cost) + ' of Opus agent cost' },
          h('span', { class: 'f-opus', style: { width: (100 * l.cost / ef.opus_cost) + '%', background: 'var(--c)' } })),
        h('span', { class: 'hv', title: money(l.cost, true) + (l.named_cost ? ', ' + money(l.named_cost, true) + ' of it asked for in the call' : '') }, pctStr(l.cost, ef.opus_cost) + '  ' + money(l.cost)))))
        : h('p', { class: 'muted', text: 'No Opus agents in this window.' }),
      h('p', { class: 'insight', text: ef.opus_agents ? int(ef.named_agents) + ' of ' + plural(ef.opus_agents, 'Opus agent') + ' named an effort in the call; the rest ran at the level they inherited.' : '' }));
  }

  // (e) the five flagged agents worth the most
  function top(d, all) {
    const { h, money, compact, modelTag } = C;
    if (!d.top.length) return h('div', { class: 'route-sec', dataset: { key: 'top' } }, h('h3', { text: 'Worth a look' }), h('p', { class: 'muted', text: 'Nothing here diverges from the managed block\'s routing.' }));
    const go = (r) => { location.hash = sessHash(r.sid, r.aid); };
    return h('div', { class: 'route-sec', dataset: { key: 'top' } },
      h('div', { class: 'route-sec-h' }, h('h3', { text: 'Worth a look' }), h('span', { class: 'muted', text: 'the ' + d.top.length + ' flagged agents with the most to save, of ' + C.int(d.flagged.agents) + ' flagged' })),
      h('table', { class: 'compact route-table route-top' },
        h('thead', {}, h('tr', {}, [['Agent'], ['Model'], ['Effort'], ['Why flagged'], ['Cost', true], ['If re-routed', true]].map(([t, r]) => h('th', { class: r ? 'r' : '', text: t })))),
        h('tbody', {}, d.top.map((r) => {
          const tgt = r.flags.map((f) => TARGET[f]).find(Boolean);
          return h('tr', { class: 'link', dataset: { key: r.sid + '/' + r.aid }, tabindex: 0, 'aria-label': r.label + ': open its timeline',
            onclick: () => go(r), onkeydown: (e) => { if (e.key === 'Enter' && e.target === e.currentTarget) go(r); } },
          h('td', {}, h('div', { class: 'route-name' }, h('span', { class: 'p-label ellipsis', text: r.label, title: r.label }),
            all ? h('span', { class: 'faint nowrap', text: r.project }) : null)),
          h('td', { class: 'nowrap', title: 'Model ' + SOURCE[r.source] }, modelTag(r.model, r.family)),
          h('td', {}, r.effort ? h('span', { class: 'tag' + (r.effort_named ? '' : ' inferred'), text: r.effort,
            title: r.effort_named ? 'Asked for in the call' : 'Inherited: the call named no effort' }) : h('span', { class: 'faint', text: 'n/a' })),
          h('td', {}, h('div', { class: 'route-why' }, r.flags.map((f) => h('span', { class: 'pill warn', dataset: { key: f }, text: WHY[f][0], title: WHY[f][1] })))),
          h('td', { class: 'r', text: money(r.cost), title: 'Context up to ' + compact(r.max_ctx) + ' tokens' }),
          r.save ? h('td', { class: 'r', text: '−' + money(r.save), title: 'Estimate: the same tokens priced on ' + tgt }) : h('td', { class: 'r zero', text: '–' }));
        }))));
  }

  function panel(d, scope, stale) {
    const { h, money, plural, compact } = C;
    if (!d || !d.agents) return null;
    const all = !scope;
    return h('section', { class: 'panel' + (stale ? ' stale' : ''), dataset: { key: 'routing' } },
      h('div', { class: 'panel-h' }, h('div', {}, h('h2', { text: 'Routing' }),
        h('div', { class: 'muted p-sub', text: 'Who picked each subagent\'s model and effort, and what it cost: ' + plural(d.agents, 'agent') + ', ' + money(d.cost) + ', ' + compact(d.new_tokens) + ' new tokens (+' + compact(d.cache_read) + ' re-read from cache).' })),
        d.flagged.agents ? h('span', { class: 'pill warn', text: C.plural(d.flagged.agents, 'agent') + ' flagged' }) : null),
      h('div', { class: 'panel-b' },
        stats(d),
        h('div', { class: 'grid g-3-2 route-grid' }, matrix(d), effort(d)),
        top(d, all),
        h('p', { class: 'insight', text: 'Work type is estimated from the agent\'s description and tools, so a few agents land in the wrong row. Flags follow the managed block\'s Model routing; "If re-routed" prices the same tokens on the model it names. Cost is an estimate from token counts.' })));
  }

  function init(ctx) {
    C = ctx;
    return { panel };
  }
  window.ccRouting = { init };
})();
