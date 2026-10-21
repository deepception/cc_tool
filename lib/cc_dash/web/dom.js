/* cc-dash keyed DOM patching, for views that refresh on a timer.

   Build a fresh element tree with the same builder as the first render, then
   patch() it into the node already on the page: unchanged nodes stay where
   they are, and only text and attributes that differ are written. Nothing
   blinks, and hover, focus, scroll position and text selection survive a
   refresh. reconcile() orders a parent's keyed children, inserting new ones
   and removing gone ones (optionally after an exit animation). setText()
   changes a node's text without replacing its text node.

   Event listeners stay on the node that was first rendered, so a listener
   should read anything that can change from the element (data-* attributes)
   or depend only on the item's key. Exposes window.ccDom.

   morph(live, fresh) / into(container, ...kids) are the keyed variant used by
   Sessions, Overview and the session and agent pages: children are matched by
   data-key (or, without one, the next unused sibling with the same tag), so a
   row inserted at the top or removed from the middle moves nothing else.
   A kept node takes the fresh node's handlers when they were attached with
   listen() (h() and s() in app.js use it), so it always runs the newest
   closure. <details open> keeps the user's choice, a focused <input> keeps its
   value, and an element carrying __chart ({sig, draw}) keeps its children:
   flushCharts() redraws it only when sig changed. */
'use strict';
(() => {
  const keyOf = (n) => (n.nodeType === 1 ? n.getAttribute('data-key') : null);
  /* The page's CSP (style-src 'self') blocks setAttribute('style', ...), so a
     changed inline style is copied through the CSSOM, which CSP allows. */
  function setAttr(el, name, value) {
    if (name === 'style') el.style.cssText = value;
    else el.setAttribute(name, value);
  }

  function patch(live, fresh) {
    if (live.nodeType !== fresh.nodeType || live.nodeName !== fresh.nodeName || keyOf(live) !== keyOf(fresh)) {
      live.replaceWith(fresh);
      return fresh;
    }
    if (live.nodeType === 3 || live.nodeType === 8) {
      if (live.nodeValue !== fresh.nodeValue) live.nodeValue = fresh.nodeValue;
      return live;
    }
    for (const at of [...live.attributes]) if (!fresh.hasAttribute(at.name)) live.removeAttribute(at.name);
    for (const at of fresh.attributes) if (live.getAttribute(at.name) !== at.value) setAttr(live, at.name, at.value);
    const kids = [...fresh.childNodes];
    kids.forEach((k, i) => {
      const cur = live.childNodes[i];
      if (cur) patch(cur, k); else live.appendChild(k);
    });
    while (live.childNodes.length > kids.length) live.lastChild.remove();
    return live;
  }

  /* Put `kids` (element nodes, reused or new) in `parent` in this order.
     A child that is no longer wanted goes through leave(el) when given: it
     returns true to remove the node itself later (after an exit animation),
     and such a node carries the class "leaving" until then. */
  function reconcile(parent, kids, leave) {
    const want = new Set(kids);
    for (const c of [...parent.children]) {
      if (want.has(c) || c.classList.contains('leaving')) continue;
      if (!(leave && leave(c))) c.remove();
    }
    let cur = parent.firstElementChild;
    for (const k of kids) {
      while (cur && cur !== k && cur.classList.contains('leaving')) cur = cur.nextElementSibling;
      if (cur === k) { cur = cur.nextElementSibling; continue; }
      parent.insertBefore(k, cur);
    }
  }

  function setText(el, value) {
    const v = String(value);
    const n = el.firstChild;
    if (n && n.nodeType === 3 && !n.nextSibling) { if (n.nodeValue !== v) n.nodeValue = v; } else if (el.textContent !== v) el.textContent = v;
  }

  function dispatch(ev) {
    const f = this.__ccOn && this.__ccOn[ev.type];
    if (f) return f.call(this, ev);
    return undefined;
  }
  function listen(el, type, fn) {
    if (!el.__ccOn) el.__ccOn = {};
    if (!(type in el.__ccOn)) el.addEventListener(type, dispatch);
    el.__ccOn[type] = fn;
  }
  function attr(el, k, v) {
    if (v == null || v === false) { if (el.hasAttribute(k)) el.removeAttribute(k); return; }
    v = v === true ? '' : String(v);
    if (el.getAttribute(k) !== v) setAttr(el, k, v);
  }

  function morphAttrs(from, to) {
    const keepOpen = from.tagName === 'DETAILS';
    for (const a of Array.from(from.attributes)) {
      if (keepOpen && a.name === 'open') continue;
      if (!to.hasAttribute(a.name)) from.removeAttribute(a.name);
    }
    for (const a of Array.from(to.attributes)) {
      if (keepOpen && a.name === 'open') continue;
      if (from.getAttribute(a.name) !== a.value) setAttr(from, a.name, a.value);
    }
    if (from.tagName === 'INPUT' && from !== document.activeElement) {
      if (from.value !== to.value) from.value = to.value;
      if (from.checked !== to.checked) from.checked = to.checked;
    }
    const on = to.__ccOn;
    if (on) for (const t in on) listen(from, t, on[t]);
    if (from.__ccOn) for (const t in from.__ccOn) if (!on || !(t in on)) from.__ccOn[t] = null;
  }

  function morph(from, to) {
    if (from.nodeType === 3) { if (from.data !== to.data) from.data = to.data; return; }
    morphAttrs(from, to);
    if (to.__chart) { from.__chart = to.__chart; return; }
    morphChildren(from, to);
  }

  function morphChildren(from, to) {
    const next = Array.from(to.childNodes).filter((n) => n.nodeType === 1 || n.nodeType === 3);
    const old = Array.from(from.childNodes);
    const byKey = new Map();
    const queues = new Map();
    for (const n of old) {
      if (n.nodeType !== 1 && n.nodeType !== 3) continue;
      const k = keyOf(n);
      if (k != null) { if (!byKey.has(k)) byKey.set(k, n); continue; }
      const q = queues.get(n.nodeName);
      if (q) q.push(n); else queues.set(n.nodeName, [n]);
    }
    const plan = next.map((n) => {
      const k = keyOf(n);
      let m = null;
      if (k != null) {
        m = byKey.get(k) || null;
        if (m && m.nodeName !== n.nodeName) m = null;
        if (m) byKey.delete(k);
      } else {
        const q = queues.get(n.nodeName);
        if (q && q.length) m = q.shift();
      }
      return [n, m];
    });
    const used = new Set(plan.map(([, m]) => m).filter(Boolean));
    for (const n of old) if (!used.has(n)) from.removeChild(n);
    plan.forEach(([n, m], i) => {
      const node = m || n;
      if (m) morph(m, n);
      const cur = from.childNodes[i];
      if (cur !== node) from.insertBefore(node, cur || null);
    });
  }

  function flatten(kids, out) {
    for (const k of kids) {
      if (k == null || k === false) continue;
      if (Array.isArray(k)) flatten(k, out);
      else out.push(typeof k === 'string' || typeof k === 'number' ? document.createTextNode(String(k)) : k);
    }
    return out;
  }
  /* Make container's children match kids, patching in place. */
  function into(container, ...kids) {
    const shadow = container.cloneNode(false);
    for (const k of flatten(kids, [])) shadow.appendChild(k);
    morphChildren(container, shadow);
    flushCharts(container);
    return container;
  }
  function flushCharts(root, force) {
    const hosts = root.querySelectorAll ? root.querySelectorAll('[data-chart]') : [];
    for (const el of hosts) {
      const c = el.__chart;
      if (!c || !el.isConnected) continue;
      if (force || el.__drawn !== c.sig) { el.__drawn = c.sig; c.draw.call(el, el); }
    }
  }

  window.ccDom = { patch, reconcile, setText, listen, attr, morph, into, flushCharts };
})();
