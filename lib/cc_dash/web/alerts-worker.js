/* cc-dash alerts poller. Runs in a dedicated worker so a background tab keeps
   polling at 1.5 s: browsers throttle page timers in hidden tabs (Chrome to
   once a minute after five minutes), not worker timers. Same origin and CSP
   as the page; it only ever GETs /api/alerts. */
'use strict';
let every = 1500, timer = null, busy = false, token = '';   // token: the page's X-CC-Dash-Token

async function poll() {
  if (busy) return;
  busy = true;
  try {
    const r = await fetch('/api/alerts', { cache: 'no-store', credentials: 'omit', headers: { Accept: 'application/json', 'X-CC-Dash-Token': token } });
    if (r.status === 401) { postMessage({ type: 'unpaired' }); return; }   // the page shows "Re-pair"
    if (!r.ok) throw new Error('HTTP ' + r.status);
    postMessage({ type: 'data', data: await r.json(), at: Date.now() });
  } catch (e) {
    postMessage({ type: 'error', error: String(e && e.message || e) });
  } finally {
    busy = false;
  }
}

function loop() { clearTimeout(timer); poll().finally(() => { timer = setTimeout(loop, every); }); }

onmessage = (e) => {
  const m = e.data || {};
  if (m.type === 'token') { const first = !token; token = String(m.token || ''); if (first) loop(); }
  if (m.type === 'now' && token) loop();
  if (m.type === 'interval' && m.ms >= 500) every = m.ms;
};
