/* cc-dash gates: pass a work order's spec or merge gate from the dashboard, in
   control mode (lib/cc_dash/gates.py: GET and POST /api/control/gate).

   window.ccGates.action(order) is what plans.js places beside an order waiting
   at a gate: a button, or null when control mode is off (plans.js then shows
   the terminal commands). The button carries everything in data-*, because a
   poll may redraw the row.

   The button opens the review dialog (control.js) with a gate's card, built
   here from the server's card and nothing else: the whole order body at the
   spec gate, the stored merge-gate briefing at the merge gate. No gate is
   passed from a one-line summary, and no scenario text is ever sent here.
   Everything in the card is a project's text, so an agent may have written
   it: it goes into the DOM through textContent only, and invisible or
   direction-changing characters are made visible.

   A decision needs the control code (the card asks for it, as every card does)
   and the word approve or reject typed out. It sends back the spec hash or the
   reviewed lane head the card showed and the card's one-time view id, so what
   is decided is what was read; the server recomputes the value, and refuses
   a spec edited or a lane moved since. A refused decision shows the server's
   words, and the card is fetched again, since its view id is spent. The
   dashboard never merges: at the merge gate approve records a merge you made,
   and refuses with the git merge --no-ff command when you have not. */
'use strict';
(() => {
  const GATE_WORD = { spec: 'Spec gate', merge: 'Merge gate' };

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
    for (const c of kids.flat(Infinity)) if (c != null && c !== false) n.append(typeof c === 'string' ? document.createTextNode(c) : c);
    return n;
  }
  const show = (n, on) => { n.hidden = !on; };

  // ── the button ───────────────────────────────────────────────────────
  function action(order) {
    const ctl = window.ccControl;
    const g = order && order.gate;
    if (!ctl || !ctl.on() || !g || !g.waiting || !GATE_WORD[g.name]) return null;
    const unlocked = window.ccAuth.scope() === 'control';
    return el('button', { type: 'button', class: 'btn ctl-mini' + (unlocked ? ' primary' : ''), text: 'Review ' + g.name + ' gate', title: 'Open the full card to decide',
      'aria-haspopup': 'dialog', dataset: { ctlGate: g.name, ctlProject: String(order.project || ''), ctlOrder: String(order.id || '') } });
  }
  // Delegated, and in the capture phase, as control.js's Review: the row holding the button must not also take the click.
  document.addEventListener('click', (e) => {
    const t = e.target.closest && e.target.closest('[data-ctl-gate]');
    if (!t) return;
    e.stopPropagation();
    e.preventDefault();
    open(t.dataset.ctlProject, t.dataset.ctlOrder, t);
  }, true);

  // ── the card ─────────────────────────────────────────────────────────
  const OPEN = { project: '', order: '', opener: null };

  async function fetchCard(project, order) {
    let r;
    try {
      r = await window.ccAuth.fetch('/api/control/gate?project=' + encodeURIComponent(project) + '&order=' + encodeURIComponent(order));
    } catch (e) { return { status: 0, body: { error: 'cc-dash is not answering.' } }; }
    let body = null;
    try { body = await r.json(); } catch (e) { /* not json */ }
    return { status: r.status, body: body || {} };
  }

  function openError(text) {
    const ctl = window.ccControl;
    const node = el('div', { class: 'ctl-card k-gate', tabindex: '0', role: 'group', 'aria-label': 'Gate' }, el('div', { class: 'ctl-err', role: 'alert', text }));
    ctl.reviewNode(OPEN.order || 'Gate', OPEN.project, node, OPEN.opener);
  }

  async function open(project, order, opener) {
    OPEN.project = project; OPEN.order = order; OPEN.opener = opener;
    const r = await fetchCard(project, order);
    if (r.status !== 200) { openError(r.body.error || 'Something went wrong (HTTP ' + r.status + ').'); return; }
    const parts = buildCard(r.body);
    OPEN.parts = parts;
    window.ccControl.reviewNode(r.body.order + ' · ' + GATE_WORD[r.body.gate], whoLine(r.body), parts.card, opener);
  }
  const whoLine = (v) => [v.project, v.line + ' line', v.priority, v.title].filter(Boolean).join(' · ');

  /* The text a human decides on: the whole of it, scrollable, never clipped. */
  function reading(summary, text) {
    const ctl = window.ccControl;
    const lines = text ? text.split('\n').length : 0;
    return el('details', { class: 'ctl-plan', open: '' }, el('summary', { text: summary + (lines ? ' (' + lines + ' lines)' : '') }),
      el('pre', { class: 'ctl-plan-text', tabindex: '0', text: ctl.visible(text) || 'Nothing is stored.' }));
  }
  const notice = (title, text) => el('div', { class: 'ctl-guard', role: 'note' }, el('b', { text: title }), el('p', { text }));

  function buildCard(v) {
    const ctl = window.ccControl;
    const err = el('div', { class: 'ctl-err', role: 'alert', hidden: true });
    const status = el('div', { class: 'ctl-status' }, el('span', { class: 'g', 'aria-hidden': 'true' }), el('span'));
    status.setAttribute('role', 'status');
    status.setAttribute('aria-live', 'polite');
    const card = el('div', { class: 'ctl-card k-gate', tabindex: '0', role: 'group', 'aria-label': v.order + ', ' + GATE_WORD[v.gate] });
    const parts = { card, view: v.view, value: v.gate === 'spec' ? v.spec_hash : v.lane_head, busy: false, buttons: [], sub: null };
    const shown = v.gate === 'spec' ? v.body : v.briefing;

    card.append(el('div', { class: 'ctl-row1' }, el('span', { class: 'pill s-decide' }, el('span', { class: 'g', 'aria-hidden': 'true' }), 'Needs a decision'),
      el('span', { class: 'ctl-who ellipsis', text: 'status ' + v.status + (v.rework ? ' · sent back ' + v.rework + 'x' : '') })));
    card.append(el('div', { class: 'ctl-where mono ellipsis', title: v.root, text: 'in ' + ctl.visible(v.root) }));
    if (v.gate === 'spec') {
      card.append(el('div', { class: 'ctl-meta mono ellipsis', title: v.spec_hash, text: v.path + ' · spec sha256 ' + v.spec_hash.slice(0, 16) }));
      card.append(reading('The spec', v.body));
      // The hash covers the line too, so approving accepts these as well: they are shown, never summed up.
      const cmds = Object.entries(v.commands || {});
      card.append(el('div', { class: 'ctl-more faint' }, 'Approving also accepts the line: ' + (v.stations || []).map((x) => x.name).join(', ') + ', ' + v.retries + (v.retries === 1 ? ' retry' : ' retries')
        + (v.threshold < 1 ? ', pass mark ' + v.threshold : '') + (cmds.length ? ', and verify runs ' : '.'), cmds.map(([k, c], i) => [i ? ', ' : '', k + ' ', el('code', { class: 'mono', text: ctl.visible(c) })])));
    } else {
      card.append(el('div', { class: 'ctl-meta mono ellipsis', title: v.lane_head, text: [v.branch, v.lane_head ? 'reviewed ' + v.lane_head.slice(0, 12) : '', v.recommendation ? 'recommendation ' + v.recommendation : ''].filter(Boolean).join(' · ') }));
      card.append(reading('Merge-gate briefing', v.briefing));
      if (v.moved) card.append(notice('The lane moved after the review', 'Approving would pass commits no station reviewed, so it is refused. Send it back, or reject it.'));
      if (v.merge_command) {
        card.append(el('div', { class: 'ctl-more faint' }, 'The dashboard does not merge. Run this from the main checkout, then approve: ',
          el('code', { class: 'mono', text: v.merge_command })));
      }
    }
    if (ctl.hasInvisible(shown)) card.append(notice('Hidden characters', 'This contains invisible or direction-changing characters, shown as ⟨U+…⟩. What the agents get may read differently from what it looks like.'));

    const note = el('textarea', { class: 'ctl-text', rows: '2', maxlength: '500', placeholder: 'Note for the ledger (optional). Never a secret.', 'aria-label': 'Note for the ledger' });
    const btn = (label, cls, onclick, title) => { const b = el('button', { type: 'button', class: 'btn ' + (cls || ''), text: label, title, onclick }); parts.buttons.push(b); return b; };

    // One typed confirm, whichever decision opened it: the whole word, every time.
    const typed = el('input', { class: 'ctl-other', type: 'text', autocomplete: 'off', spellcheck: 'false' });
    const line = el('p');
    const go = btn('Confirm', 'warn-solid', () => submit());
    const cancel = btn('Cancel', 'quiet', () => closeConfirm());
    const confirm = el('div', { class: 'ctl-sub ctl-confirm', hidden: true }, line, el('div', { class: 'ctl-acts' }, typed, go, cancel));
    const acts = el('div', { class: 'ctl-acts' });
    let pick = null;
    const ready = () => !!pick && typed.value.trim().toLowerCase() === pick.word && !parts.busy;
    const sync = () => { go.disabled = !ready(); };
    typed.addEventListener('input', sync);
    typed.addEventListener('keydown', (e) => { if (e.key === 'Enter' && ready()) submit(); e.stopPropagation(); });
    function closeConfirm() { pick = null; typed.value = ''; sync(); show(confirm, false); show(acts, true); }
    function openConfirm(word, rework, text) {
      pick = { word, rework };
      typed.value = '';
      typed.placeholder = 'type ' + word;
      typed.setAttribute('aria-label', 'Type ' + word + ' to confirm');
      line.textContent = text + ' Type ' + word + ' to confirm.';
      sync();
      show(acts, false);
      show(confirm, true);
      typed.focus();
    }
    parts.closeSub = () => { if (confirm.hidden) return false; closeConfirm(); card.focus({ preventScroll: true }); return true; };

    async function submit() {
      if (!ready()) return;
      parts.busy = true;
      sync();
      show(err, false);
      const res = await ctl.post('/api/control/gate', { project: v.project, order: v.order, gate: v.gate, decision: pick.word, confirm: pick.word, view: parts.view,
        [v.gate === 'spec' ? 'spec_hash' : 'lane_head']: parts.value, rework: pick.rework, note: note.value });
      parts.busy = false;
      if (res.status === 200) {
        closeConfirm();
        status.className = 'ctl-status s-done';
        status.lastChild.textContent = (res.body && res.body.result) || 'Recorded.';
        card.classList.add('resolved');
        for (const b of parts.buttons) b.disabled = true;
        note.disabled = true;
        return;
      }
      err.textContent = (res.body && res.body.error) || 'Something went wrong (HTTP ' + res.status + ').';
      show(err, true);
      closeConfirm();
      // The view id is spent. A fresh one for the same text; if the text changed, the new card replaces this one.
      const again = await fetchCard(v.project, v.order);
      if (again.status !== 200) return;
      const value = v.gate === 'spec' ? again.body.spec_hash : again.body.lane_head;
      if (value === parts.value && again.body.gate === v.gate) parts.view = again.body.view;
      else if (OPEN.parts === parts) { const fresh = buildCard(again.body); OPEN.parts = fresh; fresh.err.textContent = err.textContent; show(fresh.err, true); ctl.reviewNode(again.body.order + ' · ' + GATE_WORD[again.body.gate], whoLine(again.body), fresh.card, OPEN.opener); }
    }

    if (v.gate === 'spec') {
      acts.append(btn('Approve spec…', 'primary', () => openConfirm('approve', false, 'This approves the spec as shown, and the line may run it.')),
        btn('Reject…', '', () => openConfirm('reject', false, 'This rejects the order. A rejected order cannot run.')));
    } else {
      // No approve the server would have to refuse: a moved lane, or a review with nothing to decide from.
      if (!v.moved && v.lane_head && v.briefing) acts.append(btn('Approve merge…', 'primary', () => openConfirm('approve', false, 'This records the merge you made of exactly the reviewed commit, and the order is done.')));
      acts.append(btn('Send back for rework…', '', () => openConfirm('reject', true, 'This sends the order back for another run. The lane is reused.')),
        btn('Reject…', '', () => openConfirm('reject', false, 'This rejects the order without merging it.')));
    }
    card.append(el('div', { class: 'ctl-sub' }, note), acts, confirm, ctl.lockBox(false), err, status);
    parts.err = err;
    card.__ctl = { parts };
    return parts;
  }

  /* Unlocking changes the token, and a card's view id belongs to the token that fetched it. */
  document.addEventListener('cc-auth:scope', async () => {
    const parts = OPEN.parts;
    if (!parts || !parts.card.isConnected) return;
    const r = await fetchCard(OPEN.project, OPEN.order);
    if (r.status === 200 && OPEN.parts === parts) parts.view = r.body.view;
  });

  window.ccGates = { action };
})();
