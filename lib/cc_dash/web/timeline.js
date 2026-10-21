/* cc-dash timeline ordering: pure functions, no DOM, so tests can run them in node.

   group(events) folds runs of plain tool calls into one item (spawns stay on
   their own), keyed by the index of the run's first event, so a run that grows
   on a poll keeps its key.
   arrange(events, opts) filters by mode ('all' | 'talk' | 'problems'), keeps
   the newest `limit` items (the rest are counted as hidden, older ones), and
   for order 'newest' reverses both the items and the calls inside each run,
   so the latest call is always on top. Exposes window.ccTimeline. */
'use strict';
(() => {
  const BAD = (s) => s === 'error' || s === 'blocked';
  function group(events) {
    const out = [];
    let g = null;
    events.forEach((e, i) => {
      if (e.k === 'tool' && !e.spawn) {
        if (!g) { g = { type: 'group', key: i, t: e.t, calls: [] }; out.push(g); }
        g.calls.push(e);
        return;
      }
      g = null;
      out.push({ type: e.k, key: i, t: e.t, e });
    });
    return out;
  }
  function keep(it, mode) {
    if (mode === 'talk') return ['prompt', 'command', 'text', 'tool', 'notify'].includes(it.type);
    if (mode === 'problems') {
      return it.type === 'error' || it.type === 'fallback' || it.type === 'interrupt'
        || (it.type === 'group' && it.calls.some((c) => BAD(c.status))) || (it.type === 'tool' && BAD(it.e.status));
    }
    return true;
  }
  function arrange(events, opts) {
    const o = Object.assign({ order: 'newest', mode: 'all', limit: 700, showAll: false }, opts || {});
    let shown = group(events).filter((it) => keep(it, o.mode));
    let hidden = 0;
    if (!o.showAll && shown.length > o.limit) { hidden = shown.length - o.limit; shown = shown.slice(-o.limit); }
    if (o.order === 'newest') {
      shown = shown.slice().reverse().map((it) => {
        if (it.type !== 'group') return it;
        const calls = it.calls.slice().reverse();
        return Object.assign({}, it, { calls, t: calls[0].t });
      });
    }
    return { shown, hidden };
  }
  (typeof window !== 'undefined' ? window : globalThis).ccTimeline = { group, arrange };
})();
