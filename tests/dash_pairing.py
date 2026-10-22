"""Test helper (not a test): pair with a cc-dash server the way the page does.

cc-dash answers /api/* only with the per-launch token from POST /api/pair,
sent as X-CC-Dash-Token. pair(srv) gets one; auto_token(srv) makes every
http.client request to that server's port carry it, so tests written before
pairing keep their helpers.
"""
import http.client
import json

TOKENS = {}
_orig_putrequest = http.client.HTTPConnection.putrequest
_orig_endheaders = http.client.HTTPConnection.endheaders


def pair(srv, code=None):
    """A fresh token for srv (a DashServer or anything with .auth and .port)."""
    code = code or srv.auth.new_code()
    body = json.dumps({"code": code}).encode()
    c = http.client.HTTPConnection("127.0.0.1", srv.port, timeout=10)
    c.putrequest("POST", "/api/pair", skip_host=True)
    for k, v in (("Host", "127.0.0.1:%d" % srv.port), ("Origin", "http://127.0.0.1:%d" % srv.port),
                 ("Content-Type", "application/json"), ("Content-Length", str(len(body)))):
        c.putheader(k, v)
    _orig_endheaders(c, body)
    r = c.getresponse()
    out = r.read()
    c.close()
    if r.status != 200:
        raise RuntimeError("pairing failed: HTTP %d %r" % (r.status, out[:200]))
    return json.loads(out)["token"]


def _endheaders(self, *a, **kw):
    tok = TOKENS.get(self.port)
    if tok and not getattr(self, "_cc_sent", False):
        self._cc_sent = True
        self.putheader("X-CC-Dash-Token", tok)
    return _orig_endheaders(self, *a, **kw)


def _putrequest(self, *a, **kw):
    self._cc_sent = False
    return _orig_putrequest(self, *a, **kw)


def auto_token(srv):
    TOKENS[srv.port] = pair(srv)
    http.client.HTTPConnection.putrequest = _putrequest
    http.client.HTTPConnection.endheaders = _endheaders
    return TOKENS[srv.port]


auto_cookie = auto_token     # the old name, kept for the existing tests
