"""Guard for tests that run cc-setup, cc-update-project or the hooks (not a test itself).

cc-setup registers the project in ${XDG_STATE_HOME:-~/.local/state}/cc_tool/
projects.json, and the hooks keep a git worktree's state in .../cc_tool/repos/.
A test must point them at a temp state dir; this records the REAL registry and
repos/ listing (as the environment named them when the test started) before the
run and reports any change afterwards.

  guard = StateGuard()      # first thing in the test, before any env override
  ...
  why = guard.problem()     # '' when the real projects.json is untouched
"""
import os


def real_projects_json(environ=None):
    environ = os.environ if environ is None else environ
    base = environ.get("XDG_STATE_HOME") or ""
    if not os.path.isabs(base):
        base = os.path.join(environ.get("HOME") or os.path.expanduser("~"), ".local", "state")
    return os.path.join(base, "cc_tool", "projects.json")


def _snap(path):
    try:
        st = os.lstat(path)
        with open(path, "rb") as fh:
            return (st.st_mtime_ns, st.st_size, fh.read())
    except OSError:
        return None


def _listing(d):
    try:
        return sorted(os.listdir(d))
    except OSError:
        return None


class StateGuard:
    def __init__(self):
        self.path = real_projects_json()
        self.repos = os.path.join(os.path.dirname(self.path), "repos")
        self.before = _snap(self.path)
        self.repos_before = _listing(self.repos)

    def problem(self):
        if _snap(self.path) != self.before:
            return "the real %s changed during the run (a cc-setup ran without a temp XDG_STATE_HOME)" % self.path
        if _listing(self.repos) != self.repos_before:
            return "the real %s changed during the run (a hook ran without a temp XDG_STATE_HOME)" % self.repos
        return ""
