/* cc-dash pairing and the per-launch token (see lib/cc_dash/auth.py).

   cc-dash opens /pair#code=<one-time code>. This script takes the code from
   the fragment (a fragment is never sent in a request or a Referer), strips it
   from the address bar at once, and POSTs it to /api/pair for this launch's
   token. The token lives in localStorage, which is scoped to this exact origin
   including the port, so another local server on another port cannot read it,
   and new tabs share it. Every /api request carries it as X-CC-Dash-Token
   (ccAuth.fetch); a cookie is never used, since cookies are not port-scoped.
   On Linux a page opened by the same user pairs itself instead
   (POST /api/pair-local; the server checks the connecting socket's owner).

   Both give a READ-ONLY token. Control mode's writes need a control token,
   which only the control code shown in cc-dash's terminal buys (unlock(code),
   POST /api/control/unlock). It is kept in this tab's memory and nowhere else:
   localStorage and sessionStorage are files in the browser profile, readable
   by anything that runs as you, agents included. A reload asks for the code
   again. Exposes window.ccAuth = { ready, fetch, token, failed, unlock, scope }. */
'use strict';
(() => {
  const KEY = 'cc-dash-token';
  let token = null;
  let failed = '';
  try { token = localStorage.getItem(KEY); } catch (e) { token = null; }   // private mode: memory only
  let ready = Promise.resolve();
  const m = location.pathname === '/pair' ? /^#code=([A-Za-z0-9_-]{8,200})$/.exec(location.hash) : null;
  if (location.pathname === '/pair') history.replaceState(null, '', '/#/live');   // the code leaves the address bar first
  const post = (path, body) => fetch(path, { method: 'POST', cache: 'no-store', credentials: 'omit', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })
    .then(async (r) => {
      let b = null;
      try { b = await r.json(); } catch (e) { b = null; }
      return r.ok && b && b.token ? { token: b.token } : { error: (b && b.error) || ('HTTP ' + r.status), status: r.status };
    })
    .catch((e) => ({ error: String(e && e.message || e) }));
  const keep = (t) => { token = t; try { localStorage.setItem(KEY, t); } catch (e) { /* kept in memory for this tab */ } };
  if (m) {
    ready = post('/api/pair', { code: m[1] }).then((r) => { if (r.token) keep(r.token); else failed = r.error; });
  } else {
    // Linux: a browser run by the same user pairs itself; the server checks who
    // owns the connecting socket. 404 elsewhere (macOS), 403 for another user:
    // then a stored token or the one-time link from the terminal is used.
    ready = post('/api/pair-local', {}).then((r) => { if (r.token) keep(r.token); });
  }
  let control = null;            // the control-scoped token: memory only, never stored
  function authFetch(path, opts) {
    const o = Object.assign({ cache: 'no-store', credentials: 'omit' }, opts || {});
    const t = control || token;
    o.headers = Object.assign({}, o.headers || {}, t ? { 'X-CC-Dash-Token': t } : {});
    return fetch(path, o).then((r) => {
      if (r.status === 401 && control && t === control) { control = null; document.dispatchEvent(new CustomEvent('cc-auth:scope')); }
      return r;
    });
  }
  /* The control code from cc-dash's terminal -> {ok} or {error, status, locked}. */
  async function unlock(code) {
    let r;
    try {
      r = await fetch('/api/control/unlock', { method: 'POST', cache: 'no-store', credentials: 'omit',
        headers: { 'Content-Type': 'application/json', 'X-CC-Dash-Token': token || '' }, body: JSON.stringify({ code }) });
    } catch (e) { return { error: 'cc-dash is not answering.', status: 0 }; }
    let b = null;
    try { b = await r.json(); } catch (e) { b = null; }
    if (r.ok && b && b.token) {
      control = b.token;
      document.dispatchEvent(new CustomEvent('cc-auth:scope'));
      return { ok: true };
    }
    return { error: (b && b.error) || ('HTTP ' + r.status), status: r.status, locked: !!(b && b.locked) };
  }
  window.ccAuth = { ready, fetch: authFetch, token: () => token, failed: () => failed, unlock, scope: () => (control ? 'control' : 'read') };
})();
