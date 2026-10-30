"""Subscription usage limits: the freshest reading bin/cc-statusline saved.

Claude Code hands the 5-hour and 7-day limits of a Claude subscription only to
the status line command (`rate_limits` in its JSON), never to a file. So
bin/cc-statusline, set as the user's status line by install.sh, writes each
session's latest reading to

   ${XDG_STATE_HOME:-~/.local/state}/cc_tool/usage/<session id>.json
   {"session_id", "changed_at", "seen_at", "reply", "five_hour": {"used_percentage", "resets_at"},
    "seven_day": {...}}

with changed_at moved only when the numbers change, and seen_at also when a fresh
API reply confirmed them (`reply`, the session's cost.total_api_duration_ms, moved). Sessions disagree: each
holds the reading from its own last API reply, and an idle session saves that
old reading the first time it draws its line. So changed_at does not say which
reading is newest, but the numbers do: within one window (one resets_at) usage
only rises, and a later resets_at is a newer window. Each window is taken from
the record with the latest resets_at and, within it, the highest percentage;
as_of is the latest time any session saw that reading (seen_at, else changed_at,
for records cc-statusline wrote before it had seen_at). A reading is as fresh as
the last API reply of any open session; nothing here estimates usage.

Known limit: a percentage that falls within one window (a plan change mid-window)
shows the older, higher figure until a session that has not replied since is
pruned or the window resets. The records cannot tell such a fall from an idle
session's stale save.
"""
import json
import math
import os
import stat
import time

from .attention import _private_dir, _private_file, state_dir

WINDOWS = ("five_hour", "seven_day")
MAX_FILES = 200          # records read per call: the most recently written (one per session, pruned after a week)
MAX_LISTED = 5000        # directory entries looked at, at most
MAX_AHEAD = 30 * 86400   # a reset time further out than this is not a 5-hour or weekly window
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
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def _window(rec, name, now):
    w = rec.get(name)
    if not isinstance(w, dict):
        return None
    pct, resets = w.get("used_percentage"), w.get("resets_at")
    if not (_num(pct) and 0 <= pct <= 100 and _num(resets) and 0 < resets <= now + MAX_AHEAD):
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
        names = [n for n in os.listdir(directory) if n.endswith(".json")]
    except OSError:
        return None
    # The MAX_FILES most recently written, so a busy week (more sessions than that) never hides
    # the session that wrote last; a file that vanishes in between is skipped.
    dated = []
    for name in names[:MAX_LISTED]:
        try:
            dated.append((os.lstat(os.path.join(directory, name)).st_mtime, name))
        except OSError:
            pass
    dated.sort(reverse=True)
    best, seen = {}, {}
    for _mtime, name in dated[:MAX_FILES]:
        rec = _read(os.path.join(directory, name))
        if not rec or not _num(rec.get("changed_at")):
            continue
        at = rec["seen_at"] if _num(rec.get("seen_at")) and rec["seen_at"] >= rec["changed_at"] else rec["changed_at"]
        for w in WINDOWS:
            win = _window(rec, w, now)
            if win:
                key = (_window_id(win["resets_at"]), win["used_percentage"])
                if w not in best or (key, rec["changed_at"]) > best[w][0]:
                    best[w] = ((key, rec["changed_at"]), win)
                seen[(w, key)] = max(seen.get((w, key), at), at)     # the latest any session saw this reading
    best = {w: dict(win, as_of=seen[(w, k[0])]) for w, (k, win) in best.items()}
    if not best:
        return None
    for win in best.values():
        win["expired"] = win["resets_at"] <= now
    return best
