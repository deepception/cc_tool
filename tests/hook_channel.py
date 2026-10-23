"""The hook's side of cc-dash's hook channel, for tests (not a test itself).

dash-control.py talks to cc-dash over <control dir>/hook.sock (HTTP over a Unix
socket), sending the lease key and a fresh nonce; cc-dash answers with
X-CC-Dash-Proof, an HMAC under the key of the nonce and the body.

  hook_call(ctl_dir, key, method, path, body=None) -> (status, json or bytes, headers)
"""
import hmac
import http.client
import json
import os
import socket


def hook_call(ctl_dir, key, method, path, body=None, nonce=None, sock_path=None, sock=None):
    """`sock`: an already connected socket to use (and leave open)."""
    s = sock or socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(10)
    sp = sock_path or os.path.join(ctl_dir, "hook.sock")
    if sock is not None:
        pass
    elif len(sp.encode()) < 100:
        s.connect(sp)
    else:                                  # too long for sun_path: through an fd of its directory, as the hook does
        fd = os.open(os.path.dirname(sp), os.O_RDONLY | os.O_DIRECTORY)
        try:
            s.connect("/proc/self/fd/%d/%s" % (fd, os.path.basename(sp)))
        finally:
            os.close(fd)
    c = http.client.HTTPConnection("cc-dash-hook", timeout=10)
    c.sock = s
    nonce = nonce or os.urandom(16).hex()
    h = {"Host": "cc-dash-hook", "X-CC-Dash-Nonce": nonce}
    if key:
        h["X-CC-Dash-Lease"] = key
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        h["Content-Type"] = "application/json"
    try:
        c.request(method, path, body=data, headers=h)
        r = c.getresponse()
        raw = r.read()
        headers = dict(r.getheaders())
    finally:
        if sock is None:
            c.close()
    try:
        out = json.loads(raw)
    except ValueError:
        out = raw
    headers["_proof_ok"] = bool(key) and hmac.compare_digest(
        headers.get("X-CC-Dash-Proof") or "", hmac.new(key.encode(), nonce.encode() + b"\n" + raw, "sha256").hexdigest())
    return r.status, out, headers
