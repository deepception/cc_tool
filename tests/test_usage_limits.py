#!/usr/bin/env python3
"""Usage limits: bin/cc-statusline records them, cc-dash shows them.

Claude Code hands its 5-hour and 7-day subscription limits only to the status
line command (`rate_limits` in the status line JSON), never to a file. So
bin/cc-statusline prints the status line and saves each session's latest
reading under ${XDG_STATE_HOME}/cc_tool/usage/, and lib/cc_dash/limits.py reads
the freshest one for /api/live. Stdlib only; every case runs in a temp
XDG_STATE_HOME.
"""
import http.client
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import time

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY = os.path.realpath("/proc/self/exe") if os.path.exists("/proc/self/exe") else sys.executable
SCRIPT = os.path.join(REPO, "bin", "cc-statusline")
sys.path.insert(0, os.path.join(REPO, "lib"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from cc_dash import limits  # noqa: E402
from cc_dash.server import make_server  # noqa: E402
from cc_dash.store import Store  # noqa: E402
from dash_pairing import auto_cookie  # noqa: E402
from state_guard import StateGuard  # noqa: E402  (tests/state_guard.py)

STATE_GUARD = StateGuard()
RESULTS = []
BASE = tempfile.mkdtemp(prefix="cc-usage-test-")
SID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"


def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond), detail))


def payload(sid=SID, five=(42.4, None), week=(61.0, None), model="Opus 5.5", ctx=34.2):
    now = int(time.time())
    rl = {}
    if five:
        rl["five_hour"] = {"used_percentage": five[0], "resets_at": five[1] or now + 5000}
    if week:
        rl["seven_day"] = {"used_percentage": week[0], "resets_at": week[1] or now + 300000}
    d = {"session_id": sid, "model": {"id": "claude-opus-5-5", "display_name": model},
         "context_window": {"used_percentage": ctx}}
    if rl:
        d["rate_limits"] = rl
    return d


def run(xdg, data, raw=None):
    env = dict(os.environ, XDG_STATE_HOME=xdg)
    p = subprocess.run([PY, SCRIPT], input=raw if raw is not None else json.dumps(data),
                       capture_output=True, text=True, env=env, timeout=10)
    return p.returncode, p.stdout, p.stderr


def fresh_state(name):
    xdg = os.path.join(BASE, name)
    os.makedirs(xdg)
    return xdg, os.path.join(xdg, "cc_tool", "usage")


def test_statusline():
    xdg, udir = fresh_state("s1")
    rc, out, err = run(xdg, payload())
    line = out.strip()
    check("statusline: exit 0, one line", rc == 0 and len(out.splitlines()) == 1, repr(out) + err)
    check("statusline: shows model, context, 5h and week", all(s in line for s in ("Opus 5.5", "ctx 34%", "5h 42%", "week 61%")), line)
    check("statusline: shows reset times", line.count("resets") == 2, line)

    f = os.path.join(udir, SID + ".json")
    check("record: written per session", os.path.isfile(f))
    if not os.path.isfile(f):
        return
    rec = json.load(open(f))
    check("record: both windows", rec.get("five_hour", {}).get("used_percentage") == 42.4
          and rec.get("seven_day", {}).get("used_percentage") == 61.0, str(rec))
    check("record: changed_at", isinstance(rec.get("changed_at"), (int, float)), str(rec))
    check("record: only limits, no prompt or path data", set(rec) <= {"five_hour", "seven_day", "changed_at", "session_id"}, str(set(rec)))
    check("record: file 0600", stat.S_IMODE(os.stat(f).st_mode) == 0o600, oct(os.stat(f).st_mode))
    check("record: dir 0700", stat.S_IMODE(os.stat(udir).st_mode) == 0o700, oct(os.stat(udir).st_mode))

    # Same reading again: an idle session redrawing its line must not look fresh.
    first = rec["changed_at"]
    data = payload()
    data["rate_limits"] = rec_limits(rec)
    time.sleep(0.05)
    run(xdg, data)
    check("record: unchanged reading keeps changed_at", json.load(open(f))["changed_at"] == first)
    data["rate_limits"]["five_hour"]["used_percentage"] = 43.0
    time.sleep(0.05)
    run(xdg, data)
    rec2 = json.load(open(f))
    check("record: new reading updates changed_at", rec2["changed_at"] > first and rec2["five_hour"]["used_percentage"] == 43.0, str(rec2))


def rec_limits(rec):
    return {k: dict(rec[k]) for k in ("five_hour", "seven_day") if k in rec}


def test_statusline_edges():
    xdg, udir = fresh_state("s2")
    rc, out, _ = run(xdg, payload(five=None, week=None))
    check("no limits (API key, before first reply): prints model, writes nothing",
          rc == 0 and "Opus 5.5" in out and "5h" not in out and not os.path.exists(udir), out)

    rc, out, _ = run(xdg, None, raw="not json{")
    check("bad stdin: exit 0, no traceback", rc == 0 and "Traceback" not in out)

    rc, _, _ = run(xdg, payload(sid="../../escape"))
    check("bad session id: nothing written outside usage/", rc == 0 and not os.path.exists(os.path.join(xdg, "escape.json"))
          and not (os.path.isdir(udir) and os.listdir(udir)), str(os.listdir(udir)) if os.path.isdir(udir) else "")

    rc, _, _ = run(xdg, payload(five=("lots", None)))
    f = os.path.join(udir, SID + ".json")
    rec = json.load(open(f)) if os.path.isfile(f) else {}
    check("non-numeric percentage: that window is dropped", rc == 0 and "five_hour" not in rec and "seven_day" in rec, str(rec))

    # A planted symlink is not followed.
    xdg, udir = fresh_state("s3")
    os.makedirs(udir, mode=0o700)
    target = os.path.join(BASE, "victim.txt")
    with open(target, "w") as fh:
        fh.write("keep")
    os.symlink(target, os.path.join(udir, SID + ".json"))
    rc, _, _ = run(xdg, payload())
    check("symlink in usage/: target untouched", rc == 0 and open(target).read() == "keep")

    # A usage/ directory others can write to is not used.
    xdg, udir = fresh_state("s4")
    os.makedirs(udir)
    os.chmod(udir, 0o777)
    rc, _, _ = run(xdg, payload())
    check("group/world-writable usage/: nothing written", rc == 0 and not os.listdir(udir))

    # Readings older than a week are pruned when a new one is written.
    xdg, udir = fresh_state("s5")
    os.makedirs(udir, mode=0o700)
    old = os.path.join(udir, "old-session.json")
    with open(old, "w") as fh:
        fh.write("{}")
    os.utime(old, (time.time() - 9 * 86400,) * 2)
    run(xdg, payload())
    check("old readings pruned", not os.path.exists(old))


def write_rec(udir, sid, changed_at, five=None, week=None):
    os.makedirs(udir, mode=0o700, exist_ok=True)
    rec = {"session_id": sid, "changed_at": changed_at}
    if five:
        rec["five_hour"] = {"used_percentage": five[0], "resets_at": five[1]}
    if week:
        rec["seven_day"] = {"used_percentage": week[0], "resets_at": week[1]}
    p = os.path.join(udir, sid + ".json")
    with open(p, "w") as fh:
        json.dump(rec, fh)
    os.chmod(p, 0o600)
    return p


def test_reader():
    now = time.time()
    _xdg, udir = fresh_state("r1")
    check("reader: no directory -> None", limits.payload(directory=udir, now=now) is None)

    write_rec(udir, "s-old", now - 600, five=(10, now + 1000), week=(50, now + 90000))
    write_rec(udir, "s-new", now - 60, five=(20, now + 1000))
    d = limits.payload(directory=udir, now=now)
    check("reader: freshest 5h reading wins", d and d["five_hour"]["used_percentage"] == 20, str(d))
    check("reader: each window from its own freshest reading", d and d["seven_day"]["used_percentage"] == 50, str(d))
    check("reader: as_of per window", d and d["five_hour"]["as_of"] == now - 60 and d["seven_day"]["as_of"] == now - 600, str(d))
    check("reader: not expired before resets_at", d and d["five_hour"]["expired"] is False)

    # An idle session's first redraw saves an old reading with a fresh changed_at. Within one
    # window (same resets_at) usage only rises, so the highest reading is the newest; a later
    # resets_at is a newer window.
    _xdg, udir = fresh_state("r1b")
    reset5, reset7 = now + 3000, now + 90000
    write_rec(udir, "busy", now - 300, five=(38, reset5), week=(41, reset7))
    write_rec(udir, "idle", now - 10, five=(36, reset5), week=(36, reset7))
    d = limits.payload(directory=udir, now=now)
    check("reader: same window, highest reading wins over a later-saved stale one",
          d and d["five_hour"]["used_percentage"] == 38 and d["seven_day"]["used_percentage"] == 41, str(d))
    write_rec(udir, "next", now - 600, five=(2, reset5 + 18000))
    d = limits.payload(directory=udir, now=now)
    check("reader: a later window wins even with a lower reading", d and d["five_hour"]["used_percentage"] == 2, str(d))

    _xdg, udir = fresh_state("r2")
    write_rec(udir, "s1", now - 7200, five=(90, now - 60), week=(70, now + 9000))
    d = limits.payload(directory=udir, now=now)
    check("reader: past resets_at is marked expired", d and d["five_hour"]["expired"] is True and d["seven_day"]["expired"] is False, str(d))

    _xdg, udir = fresh_state("r3")
    good = write_rec(udir, "good", now - 30, five=(30, now + 100))
    bad = write_rec(udir, "loose", now - 1, five=(99, now + 100))
    os.chmod(bad, 0o666)
    with open(os.path.join(udir, "junk.json"), "w") as fh:
        fh.write("{nope")
    os.chmod(os.path.join(udir, "junk.json"), 0o600)
    write_rec(udir, "range", now - 2, five=(250, now + 100))
    os.symlink(good, os.path.join(udir, "link.json"))
    d = limits.payload(directory=udir, now=now)
    check("reader: skips writable-by-others, junk, out-of-range and symlinked files",
          d and d["five_hour"]["used_percentage"] == 30, str(d))


def install(settings):
    p = subprocess.run([PY, SCRIPT, "--install", settings], capture_output=True, text=True, timeout=10)
    return p.returncode, p.stdout + p.stderr


def test_install():
    d = os.path.join(BASE, "inst")
    os.makedirs(d)
    want = os.path.realpath(SCRIPT)

    s1 = os.path.join(d, "none", "settings.json")
    rc, out = install(s1)
    got = json.load(open(s1)) if os.path.isfile(s1) else {}
    check("install: creates settings with the status line", rc == 0 and got.get("statusLine", {}).get("type") == "command"
          and want in got["statusLine"].get("command", ""), out + str(got))
    check("install: new settings file is 0600", os.path.isfile(s1) and stat.S_IMODE(os.stat(s1).st_mode) == 0o600)

    s2 = os.path.join(d, "keep.json")
    with open(s2, "w") as fh:
        json.dump({"theme": "auto", "enabledPlugins": {"x@y": True}}, fh)
    rc, out = install(s2)
    got = json.load(open(s2))
    check("install: keeps the other settings", rc == 0 and got.get("theme") == "auto" and got.get("enabledPlugins") == {"x@y": True}
          and want in got.get("statusLine", {}).get("command", ""), out + str(got))
    rc, out = install(s2)
    check("install: second run says it is already set", rc == 0 and "already" in out and json.load(open(s2)) == got, out)

    s3 = os.path.join(d, "theirs.json")
    theirs = {"statusLine": {"type": "command", "command": "~/my-line.sh"}}
    with open(s3, "w") as fh:
        json.dump(theirs, fh)
    rc, out = install(s3)
    check("install: leaves someone else's status line alone and says how to add it",
          rc == 0 and json.load(open(s3)) == theirs and want in out, out)

    s4 = os.path.join(d, "broken.json")
    with open(s4, "w") as fh:
        fh.write("{ not json")
    rc, out = install(s4)
    check("install: unreadable settings are not touched", rc != 0 and open(s4).read() == "{ not json", out)


def _get(port, path):
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    c.request("GET", path, headers={"Host": "127.0.0.1:%d" % port})
    r = c.getresponse()
    body = r.read()
    c.close()
    return r.status, body


def test_api_live():
    xdg, udir = fresh_state("api")
    now = time.time()
    write_rec(udir, SID, now - 5, five=(42, now + 1000), week=(61, now + 90000))
    cfg = os.path.join(BASE, "claude")
    os.makedirs(os.path.join(cfg, "projects"))
    saved = os.environ.get("XDG_STATE_HOME")
    os.environ["XDG_STATE_HOME"] = xdg
    try:
        srv = make_server(0, Store(root=cfg, days=1))
        th = threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.1}, daemon=True)
        th.start()
        auto_cookie(srv)
        try:
            s, b = _get(srv.port, "/api/live")
            d = json.loads(b) if s == 200 else {}
            lim = d.get("usage_limits") or {}
            check("/api/live carries usage_limits", s == 200 and lim.get("five_hour", {}).get("used_percentage") == 42
                  and lim.get("seven_day", {}).get("used_percentage") == 61, str(lim))
        finally:
            srv.shutdown()
            srv.server_close()
    finally:
        if saved is None:
            os.environ.pop("XDG_STATE_HOME", None)
        else:
            os.environ["XDG_STATE_HOME"] = saved


def main():
    try:
        for fn in (test_statusline, test_statusline_edges, test_reader, test_install, test_api_live):
            try:
                fn()
            except Exception as e:  # noqa: BLE001
                import traceback
                traceback.print_exc()
                check(fn.__name__ + " raised", False, f"{type(e).__name__}: {e}")
        why = STATE_GUARD.problem()
        check("real state directory untouched", not why, why or "")
    finally:
        shutil.rmtree(BASE, ignore_errors=True)
    fails = [r for r in RESULTS if not r[1]]
    print("total=%d  pass=%d  fail=%d" % (len(RESULTS), len(RESULTS) - len(fails), len(fails)))
    for name, _, detail in fails:
        print("  FAIL %-60s %s" % (name, detail[:200]))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
