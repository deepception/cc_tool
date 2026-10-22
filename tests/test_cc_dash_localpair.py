#!/usr/bin/env python3
"""Same-user pairing for cc-dash on Linux (lib/cc_dash/auth.py peer_uid, POST /api/pair-local).

A browser run by the user who started cc-dash pairs itself: the server reads
the kernel socket table to find who owns the connecting socket. Checks the
table parser on synthetic lines, then a live server: same user gets a token,
a wrong Origin / Sec-Fetch-Site / Content-Type is refused, another user's uid
is refused, and the token unlocks the API.

Run:  python3 tests/test_cc_dash_localpair.py
"""
import http.client
import json
import os
import shutil
import sys
import tempfile
import threading

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "lib"))
from cc_dash import auth as auth_mod  # noqa: E402
from cc_dash.server import make_server  # noqa: E402
from cc_dash.store import Store  # noqa: E402

RESULTS = []


def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond), str(detail)))


def test_parser():
    d = tempfile.mkdtemp(prefix="ccdash-lp-")
    try:
        t4 = os.path.join(d, "tcp")
        open(t4, "w").write(
            "  sl  local_address rem_address   st tx_queue rx_queue tr tm->when retrnsmt   uid  timeout inode\n"
            "   0: 0100007F:1EC6 00000000:0000 0A 00000000:00000000 00:00000000 00000000  1000        0 1 1\n"
            "   1: 0100007F:D431 0100007F:1EC6 01 00000000:00000000 00:00000000 00000000  1234        0 2 1\n")
        t6 = os.path.join(d, "tcp6")
        open(t6, "w").write(
            "  sl  local_address                         remote_address                        st tx_queue rx_queue tr tm->when retrnsmt   uid  timeout inode\n"
            "   0: 0000000000000000FFFF00000100007F:D432 0000000000000000FFFF00000100007F:1EC6 01 00000000:00000000 00:00000000 00000000  4321        0 3 1\n")
        check("parser: ipv4 client socket owner", auth_mod.peer_uid(("127.0.0.1", 0xD431), 0x1EC6, (t4, t6)) == 1234)
        check("parser: v4-mapped ipv6 socket owner", auth_mod.peer_uid(("::ffff:127.0.0.1", 0xD432), 0x1EC6, (t4, t6)) == 4321)
        check("parser: the listening socket is not mistaken for the client", auth_mod.peer_uid(("127.0.0.1", 0x1EC6), 0x1EC6, (t4, t6)) is None)
        check("parser: unknown port gives None", auth_mod.peer_uid(("127.0.0.1", 1), 0x1EC6, (t4, t6)) is None)
        check("parser: garbage address gives None", auth_mod.peer_uid(("nope", "x"), 0x1EC6, (t4, t6)) is None)
    finally:
        shutil.rmtree(d, ignore_errors=True)


def _post(port, headers, body=b"{}"):
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    c.request("POST", "/api/pair-local", body=body, headers=headers)
    r = c.getresponse()
    data = r.read()
    c.close()
    try:
        return r.status, json.loads(data or b"null")
    except ValueError:
        return r.status, None


def test_server():
    if not auth_mod.local_pairing_supported():
        check("server: skipped (no /proc/net/tcp)", True)
        return
    root = tempfile.mkdtemp(prefix="ccdash-lps-")
    cfg = os.path.join(root, "claude")
    os.makedirs(os.path.join(cfg, "projects"))
    try:
        srv = make_server(0, Store(root=cfg, days=1))
        port = srv.server_address[1]
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        good = {"Host": "127.0.0.1:%d" % port, "Origin": "http://127.0.0.1:%d" % port,
                "Sec-Fetch-Site": "same-origin", "Content-Type": "application/json"}
        st, body = _post(port, good)
        token = (body or {}).get("token")
        check("server: same user gets a token", st == 200 and token, (st, body))
        c = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
        c.request("GET", "/api/live", headers={"Host": good["Host"], "X-CC-Dash-Token": token or ""})
        r = c.getresponse(); r.read(); c.close()
        check("server: the token unlocks the API", r.status == 200, r.status)
        for k, v, why in (("Origin", "https://evil.example", "foreign Origin"), ("Sec-Fetch-Site", "cross-site", "cross-site fetch"),
                          ("Content-Type", "text/plain", "simple form post")):
            h = dict(good, **{k: v})
            st, _ = _post(port, h)
            check("server: refused on " + why, st == 403, st)
        h = dict(good); h.pop("Origin")
        st, _ = _post(port, h)
        check("server: refused without Origin", st == 403, st)
        real = auth_mod.peer_uid
        auth_mod.peer_uid = lambda *a, **k: os.getuid() + 1
        try:
            st, body = _post(port, good)
            check("server: another user's socket is refused", st == 403 and not (body or {}).get("token"), (st, body))
        finally:
            auth_mod.peer_uid = real
        srv.shutdown(); srv.server_close()
    finally:
        shutil.rmtree(root, ignore_errors=True)


def main():
    for fn in (test_parser, test_server):
        try:
            fn()
        except Exception as e:  # noqa: BLE001
            import traceback
            traceback.print_exc()
            check(fn.__name__ + " raised", False, "%s: %s" % (type(e).__name__, e))
    fails = [r for r in RESULTS if not r[1]]
    print("total=%d  pass=%d  fail=%d" % (len(RESULTS), len(RESULTS) - len(fails), len(fails)))
    for name, _, detail in fails:
        print("  FAIL %-50s %s" % (name, detail[:200]))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
