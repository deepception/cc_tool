#!/usr/bin/env python3
"""Headless-Chrome checks for the cc-dash Work page (web/plans.js, plans.css; the Plans page grown into Work).

The page is served by the real cc_dash.server.Handler over an empty temp CLAUDE_CONFIG_DIR; GET /api/plans is
stubbed with a fixture in the shape of Contract B (the plans payload plus orders, documents and board), auth-checked
like every /api route. GET /__test/v?n=full|bare switches the payload: `full` has all four sections, `bare` only plans.

Browser steps (needs node, playwright-core and Chrome, else "browser: skipped" and exit 0): all four sections render
in order with their counts and the Work tab label; empty sections are absent; the type chips filter Documents and a
row opens Docs; an order at a gate shows the terminal commands; an order deep link highlights its row; an order title
holding <img onerror> stays text; phone width has no horizontal scroll; light and dark both render without errors.

Run:  python3 tests/test_cc_dash_work_ui.py
Env:  CC_DASH_PLAYWRIGHT  playwright-core directory     CC_DASH_CHROME  Chrome binary
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from http.server import ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BASE = tempfile.mkdtemp(prefix="cc-dash-work-ui-test-")
os.environ["CLAUDE_CONFIG_DIR"] = os.path.join(BASE, "claude")     # before cc_dash is imported: an empty config dir
os.makedirs(os.environ["CLAUDE_CONFIG_DIR"], exist_ok=True)
sys.path.insert(0, os.path.join(REPO, "lib"))
from cc_dash import server as server_mod  # noqa: E402
from cc_dash.auth import Auth  # noqa: E402
from cc_dash.store import Store  # noqa: E402

RESULTS = []
PLAYWRIGHT = os.environ.get("CC_DASH_PLAYWRIGHT", "/home/lukasz/Files/hobby/color-analyzer/webapp/node_modules/playwright-core")
CHROME = os.environ.get("CC_DASH_CHROME", "/usr/bin/google-chrome")
STATE = {"n": "full"}
EVIL = '<img src=x onerror="window.__pwn=1">Fix the digest'


def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond), detail))


def plan(key, title, status, done, total, **extra):
    now = time.time()
    p = {"key": key, "project": "alpha", "title": title, "status": status, "path": "~/alpha/" + key, "doc": key, "mode": "plan", "unit": "tasks",
         "tasks": {"done": done, "total": total}, "steps": {"done": done * 2, "total": total * 2}, "current": None, "blocked": None, "eta": None,
         "segments": [{"title": "Plan", "done": done * 2, "total": total * 2, "tasks": total, "tasks_done": done}],
         "last_activity": now - 60, "started": now - 3600, "median_task_secs": 0, "order": None, "spec": "", "spec_title": "",
         "spec_status": "", "spec_exists": False}
    p.update(extra)
    return p


def payload(n):
    now = time.time()
    plans = [plan("docs/superpowers/plans/2026-10-30-fo-001-x.md", "Widget plan", "live", 1, 4, order="FO-001",
                  spec="factory/orders/FO-001-x.md", spec_title="Widget", spec_status="approved", spec_exists=True,
                  current={"pos": 2, "id": "2", "title": "Build it", "running": True, "who": None}),
             plan("docs/plans/old.md", "Older plan", "done", 3, 3)]
    d = {"now": now, "plans": plans, "total": len(plans), "chips": {}, "older": 0, "scope": {"project": ""}, "pause_secs": 1800,
         "projects": [{"project": "alpha", "plans": 2, "active": 1, "recent": 2}]}
    if n == "full":
        stations = [{"name": s, "title": s.title(), "status": st} for s, st in (("plan", "pass"), ("build", "pass"), ("test", "active"), ("review", "pending"))]
        order = {"project": "alpha", "title": "Widget", "status": "building", "priority": "p2", "line": "quick", "gate": None, "stations": stations,
                 "done": 2, "total": 4, "rework": 0, "recommendation": "", "branch": "factory/FO-001-x", "worktree": "",
                 "path": "factory/orders/FO-001-x.md", "updated": now - 30,
                 "plan": {"key": plans[0]["key"], "path": plans[0]["path"], "title": "Widget plan", "tasks": {"done": 3, "total": 7}}}
        waiting = dict(order, id="FO-002", title=EVIL, status="review", gate={"name": "merge", "waiting": True}, plan=None, branch="factory/FO-002-y",
                       path="factory/orders/FO-002-y.md")
        d["orders"] = [dict(order, id="FO-001"), waiting]
        d["documents"] = [{"project": "alpha", "path": "docs/UI-UX-REVIEW.md", "type": "report", "title": "UI review", "status": "", "updated": now - 90},
                          {"project": "alpha", "path": "docs/adr/0001.md", "type": "decision", "title": "Use SQLite", "status": "accepted", "updated": now - 99},
                          {"project": "alpha", "path": "docs/notes.md", "type": "report", "title": "Notes", "status": "", "updated": now - 120}]
        d["board"] = [{"project": "alpha", "run": "default-factory", "label": "work-ui", "path": ".cc-board/default-factory/work-ui/checkpoint.md",
                       "head": "Done: Task 4 view", "updated": now - 10}]
    return d


class WorkHandler(server_mod.Handler):
    def do_GET(self):  # noqa: N802
        parts = urlsplit(self.path)
        q = {k: v[0] for k, v in parse_qs(parts.query).items()}
        if parts.path.startswith("/__test/") and not self._host_ok():
            return self._deny(421, "no")
        if parts.path == "/__test/code":
            return self._json(200, {"code": self.server.auth.new_code()})
        if parts.path == "/__test/v":
            STATE["n"] = q.get("n", "full")
            return self._json(200, {"n": STATE["n"]})
        if parts.path == "/api/plans":
            if self.server.auth.session(self.headers) is None:
                return self._json(401, {"error": "Not paired.", "repair": True})
            return self._json(200, payload(STATE["n"]))
        return super().do_GET()


def start_server():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), WorkHandler)
    srv.daemon_threads = True
    srv.store, srv.verbose = Store(days=1, clock=time.time), False
    srv.port = srv.server_address[1]
    srv.allowed_hosts = {"127.0.0.1:%d" % srv.port, "localhost:%d" % srv.port}
    srv.auth, srv.control = Auth(srv.port), None
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


BROWSER_JS = r"""
const { chromium } = require(process.env.PW);
const BASE = process.env.BASE;
const out = {};
const getJSON = async (p) => (await fetch(BASE + p)).json();
async function open(browser, hash, o) {
  o = Object.assign({ w: 1440, h: 900, scheme: 'light', n: 'full' }, o);
  await getJSON('/__test/v?n=' + o.n);
  const ctx = await browser.newContext({ viewport: { width: o.w, height: o.h }, colorScheme: o.scheme, locale: 'en-US' });
  const page = await ctx.newPage();
  page.setDefaultTimeout(8000);
  const errs = [];
  page.on('pageerror', (e) => errs.push(String(e).slice(0, 200)));
  const { code } = await getJSON('/__test/code');
  await page.goto(BASE + '/pair#code=' + code, { waitUntil: 'load' });
  await page.waitForFunction(() => window.ccAuth && window.ccAuth.token());
  await page.evaluate((h) => { location.hash = h; }, hash);
  await page.waitForSelector('.pl-sec');
  return { ctx, page, errs };
}
const secs = (page) => page.$$eval('.pl-sec', (e) => e.map((x) => [x.dataset.key, x.querySelector('.sec-h .num').textContent]));
(async () => {
  const browser = await chromium.launch({ executablePath: process.env.CHROME, headless: true });
  try {
    for (const scheme of ['light', 'dark']) {
      const { ctx, page, errs } = await open(browser, '#/plans', { scheme });
      const r = { sections: await secs(page), errs };
      r.tab = await page.$eval('.area-tabs a[data-key="plans"]', (a) => a.textContent.trim());
      r.pwn = await page.evaluate(() => window.__pwn || null);
      r.imgs = await page.$$eval('main img', (e) => e.length);
      r.evilText = await page.$eval('tr[data-order="FO-002"]', (e) => e.textContent.includes('<img src=x'));
      r.steps = await page.$$eval('tr[data-order="FO-001"] .fx-step', (e) => e.length);
      r.passed = await page.$$eval('tr[data-order="FO-001"] .fx-step[data-status="done"]', (e) => e.length);
      r.planTasks = await page.$eval('tr[data-order="FO-001"]', (e) => e.textContent.includes('3 of 7 tasks'));
      r.hint = await page.$eval('tr[data-order="FO-002"] .pl-gate', (e) => e.textContent);
      if (process.env.SHOTS) await page.screenshot({ path: process.env.SHOTS + '/work-' + scheme + '.png', fullPage: true });
      r.nohint =await page.$eval('tr[data-order="FO-001"]', (e) => !e.querySelector('.pl-gate'));
      r.cardSpec = await page.$$eval('.pl-card a', (e) => e.map((a) => a.textContent + '|' + a.getAttribute('href')));
      r.planLink = await page.$eval('tr[data-order="FO-001"] a[href^="#/plan?"]', (a) => a.textContent);
      if (scheme === 'light') {
        r.chips = await page.$$eval('.pl-sec[data-key="sec:docs"] .seg button', (e) => e.map((b) => b.textContent));
        await page.click('.pl-sec[data-key="sec:docs"] .seg button:has-text("decision")');
        r.rowsAfter = await page.$$eval('.pl-docs tbody tr', (e) => e.length);
        await page.click('.pl-docs tbody tr');
        await page.waitForFunction(() => location.hash.startsWith('#/docs'));
        r.docHash = await page.evaluate(() => location.hash);
      }
      r.bg = await page.evaluate(() => getComputedStyle(document.body).backgroundColor);
      out[scheme] = r;
      await ctx.close();
    }
    {
      const { ctx, page, errs } = await open(browser, '#/plans', { n: 'bare' });
      out.bare = { sections: await secs(page), errs };
      await ctx.close();
    }
    {
      const { ctx, page, errs } = await open(browser, '#/plans?order=FO-002');
      await page.waitForSelector('tr.sel[data-order="FO-002"]');
      out.deep = { errs, sel: await page.$$eval('tr.sel', (e) => e.map((x) => x.dataset.order)),
        visible: await page.$eval('tr.sel', (e) => { const b = e.getBoundingClientRect(); return b.top >= 0 && b.bottom <= innerHeight; }) };
      await ctx.close();
    }
    {
      const { ctx, page, errs } = await open(browser, '#/plans', { w: 390, h: 844 });
      out.phone = { errs, sw: await page.evaluate(() => [document.documentElement.scrollWidth, document.documentElement.clientWidth]) };
      await ctx.close();
    }
  } finally { await browser.close(); }
  console.log(JSON.stringify(out));
})().catch((e) => { console.error(e && e.stack || e); process.exit(1); });
"""


def test_browser(srv):
    if not (os.path.isdir(PLAYWRIGHT) and os.path.exists(CHROME) and shutil.which("node")):
        print("browser: skipped (no playwright-core, Chrome or node)")
        return
    js = os.path.join(BASE, "work_ui_check.js")
    with open(js, "w") as fh:
        fh.write(BROWSER_JS)
    env = dict(os.environ, PW=PLAYWRIGHT, CHROME=CHROME, BASE="http://127.0.0.1:%d" % srv.port, SHOTS=os.environ.get("CC_DASH_SHOTS", ""))
    r = subprocess.run(["node", js], capture_output=True, text=True, timeout=300, env=env)
    if r.returncode != 0:
        check("browser: the page check ran", False, r.stderr[-1200:])
        return
    o = json.loads(r.stdout.strip().splitlines()[-1])
    want = [["sec:orders", "2"], ["sec:plans", "2"], ["sec:docs", "3"], ["sec:runs", "1"]]
    for scheme in ("light", "dark"):
        s = o[scheme]
        check("%s: four sections in order, each with its count" % scheme, s["sections"] == want, s["sections"])
        check("%s: no page errors" % scheme, not s["errs"], s["errs"])
        check("%s: the tab reads Work" % scheme, s["tab"].startswith("Work"), s["tab"])
        check("%s: an order title holding <img onerror> stays text" % scheme, s["pwn"] is None and s["imgs"] == 0 and s["evilText"], s)
        check("%s: the stepper has one step per station plus the merge gate where one is set" % scheme, s["steps"] == 4, s["steps"])
        check("%s: a passed station shows as done on the stepper" % scheme, s["passed"] == 2, s["passed"])
        check("%s: an order row shows its plan's task progress" % scheme, s["planTasks"] is True, s["planTasks"])
        check("%s: an order waiting at a gate shows the terminal commands, one that is not shows none" % scheme,
              "cc-factory approve FO-002 --gate merge" in s["hint"] and "cc-factory reject FO-002 --gate merge" in s["hint"] and s["nohint"], s["hint"])
        check("%s: a plan card links its spec and its order" % scheme,
              any(x.startswith("Spec") and "#/docs?p=alpha&f=factory%2Forders%2FFO-001-x.md" in x for x in s["cardSpec"])
              and any(x.startswith("FO-001|#/plans?p=alpha&order=FO-001") for x in s["cardSpec"]), s["cardSpec"])
        check("%s: an order links its plan" % scheme, s["planLink"] == "Widget plan", s["planLink"])
    check("documents: type chips are All plus each type with counts", o["light"]["chips"] == ["All3", "decision1", "report2"], o["light"]["chips"])
    check("documents: a type chip filters the rows", o["light"]["rowsAfter"] == 1, o["light"]["rowsAfter"])
    check("documents: a row opens Docs", o["light"]["docHash"].startswith("#/docs?p=alpha&f=docs%2Fadr"), o["light"]["docHash"])
    check("light and dark differ in background", o["light"]["bg"] != o["dark"]["bg"], (o["light"]["bg"], o["dark"]["bg"]))
    check("empty sections are absent", o["bare"]["sections"] == [["sec:plans", "2"]] and not o["bare"]["errs"], o["bare"])
    check("an order deep link highlights its row and scrolls it into view", o["deep"]["sel"] == ["FO-002"] and o["deep"]["visible"] and not o["deep"]["errs"], o["deep"])
    check("phone width: no horizontal scroll", o["phone"]["sw"][0] <= o["phone"]["sw"][1] and not o["phone"]["errs"], o["phone"])


def main():
    srv = start_server()
    try:
        test_browser(srv)
    finally:
        srv.shutdown()
        srv.server_close()
        shutil.rmtree(BASE, ignore_errors=True)
    fails = [r for r in RESULTS if not r[1]]
    for name, ok, detail in RESULTS:
        if not ok:
            print(f"FAIL  {name}  {detail}")
    print(f"total={len(RESULTS)}  pass={len(RESULTS) - len(fails)}  fail={len(fails)}")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
