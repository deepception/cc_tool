"""Subscription usage limits: the freshest reading bin/cc-statusline saved.

Claude Code hands the 5-hour and 7-day limits of a Claude subscription only to
the status line command (`rate_limits` in its JSON), never to a file. So
bin/cc-statusline, set as the user's status line by install.sh, writes each
session's latest reading to

   ${XDG_STATE_HOME:-~/.local/state}/cc_tool/usage/<session id>.json
   {"session_id", "changed_at", "five_hour": {"used_percentage", "resets_at"},
    "seven_day": {...}}

with changed_at moved only when the numbers change. Sessions disagree: each
holds the reading from its own last API reply, and an idle session saves that
old reading the first time it draws its line. So changed_at does not say which
reading is newest, but the numbers do: within one window (one resets_at) usage
only rises, and a later resets_at is a newer window. Each window is taken from
the record with the latest resets_at and, within it, the highest percentage;
as_of is the latest time any session saved that reading. A reading is as fresh
as the last API reply of any open session; nothing here estimates usage.
"""
import json
import os
import stat
import time

from .attention import _private_dir, _private_file, state_dir

WINDOWS = ("five_hour", "seven_day")
MAX_FILES = 200          # one per session over the last week; more is not ours
MAX_BYTES = 4096


def usage_dir():
    return os.path.join(state_dir(), "usage")


def _read(path):
    """The record at path, or None unless it is a small private regular file of ours."""
    st = _private_file(path)
    if st is None or st.st_size > MAX_BYTES:
        return None
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
    except OSError:
        return None
    with os.fdopen(fd, "rb") as fh:
        fst = os.fstat(fh.fileno())
        if (fst.st_dev, fst.st_ino) != (st.st_dev, st.st_ino) or not stat.S_ISREG(fst.st_mode):
            return None
        try:
            rec = json.loads(fh.read(MAX_BYTES))
        except ValueError:
            return None
    return rec if isinstance(rec, dict) else None


def _num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _window(rec, name):
    w = rec.get(name)
    if not isinstance(w, dict):
        return None
    pct, resets = w.get("used_percentage"), w.get("resets_at")
    if not (_num(pct) and 0 <= pct <= 100 and _num(resets) and resets > 0):
        return None
    return {"used_percentage": pct, "resets_at": resets}


def _window_id(resets_at):
    """Which window a reset time names. Sessions report the same reset time for a
    window; rounding to the minute keeps a second's jitter from passing for a
    newer window."""
    return round(resets_at / 60)


def payload(directory=None, now=None):
    """{window: {used_percentage, resets_at, as_of, expired}} from the freshest
    reading of each window, or None when there is none."""
    directory = directory or usage_dir()
    now = time.time() if now is None else now
    if not _private_dir(directory):
        return None
    try:
        names = sorted(n for n in os.listdir(directory) if n.endswith(".json"))[:MAX_FILES]
    except OSError:
        return None
    best = {}
    for name in names:
        rec = _read(os.path.join(directory, name))
        if not rec or not _num(rec.get("changed_at")):
            continue
        for w in WINDOWS:
            win = _window(rec, w)
            if win:
                key = (_window_id(win["resets_at"]), win["used_percentage"], rec["changed_at"])
                if w not in best or key > best[w][0]:
                    best[w] = (key, dict(win, as_of=rec["changed_at"]))
    best = {w: win for w, (_key, win) in best.items()}
    if not best:
        return None
    for win in best.values():
        win["expired"] = win["resets_at"] <= now
    return best
