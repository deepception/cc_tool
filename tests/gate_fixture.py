"""A copy of the cc_tool tree for tests of gates from the dashboard (not a test itself).

These tests run under Claude Code, where no process can pass as a real dashboard, and nothing in
the real CLI turns that check off. So the allowed paths run a copy of the tree whose cc_procs.py is
the real file with three rules overridden (`make_tree`, like factory_cli_cases.make_tree): the parent
passes dashboard_problem, code_after_start and control_mode_problem. Everything else stays real in
the copy: the kernel's peer checks, the Claude Code variables, /proc, the pin, and the manifest, which
`plant_record` writes into a project for that tree (a real copy of lib/, since the record refuses
symlinks). PASS_AS_CC_DASH leaves control_mode_problem real, for a parent that has to earn it.

  make_tree(base, name, extra)   the copy's bin/cc-factory; `extra` is appended to its cc_procs.py
  plant_record(project, root)    <project>/.claude/hooks/{cc_tool_root,cc_dash_manifest.json} for checkout `root`
"""
import importlib.util
import json
import os
import shutil

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROCS = os.path.join(REPO, "templates", "hooks", "cc_procs.py")
PASS_AS_CC_DASH = ('\n\ndef dashboard_problem(pid, root, proc="/proc", python=None, terminals=None, cache=None):\n    return ""\n'
                   '\n\ndef code_after_start(pid, root, manifest_path, proc="/proc", start=None):\n    return ""\n')
PASS_AS_DASHBOARD = PASS_AS_CC_DASH + '\n\ndef control_mode_problem(pid, proc="/proc"):\n    return ""\n'


def load_procs():
    spec = importlib.util.spec_from_file_location("cc_procs_gate_fixture", PROCS)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def make_tree(base, name="gate", extra=PASS_AS_DASHBOARD):
    d = os.path.realpath(os.path.join(base, "tree-" + name))
    os.makedirs(os.path.join(d, "bin"))
    os.makedirs(os.path.join(d, "templates", "hooks"))
    for rel in ("bin/cc-factory", "bin/cc-dash"):
        shutil.copy(os.path.join(REPO, rel), os.path.join(d, rel))
    shutil.copytree(os.path.join(REPO, "lib"), os.path.join(d, "lib"), ignore=shutil.ignore_patterns("__pycache__"))
    os.symlink(os.path.join(REPO, "templates", "factory"), os.path.join(d, "templates", "factory"))
    with open(PROCS, encoding="utf-8") as fh:
        src = fh.read()
    with open(os.path.join(d, "templates", "hooks", "cc_procs.py"), "w", encoding="utf-8") as fh:
        fh.write(src + extra)
    return os.path.join(d, "bin", "cc-factory")


def plant_record(project, root):
    """What cc-setup leaves beside a project's hooks for checkout `root`: the pin and the code's manifest."""
    procs = load_procs()
    hooks = os.path.join(project, ".claude", "hooks")
    os.makedirs(hooks, exist_ok=True)
    for name, text in ((procs.PIN_NAME, os.path.realpath(root) + "\n"),
                       (procs.MANIFEST_NAME, json.dumps(procs.build_manifest(root, procs.python_record(), [])))):
        path = os.path.join(hooks, name)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)
        os.chmod(path, 0o644)      # whatever the umask: the readers refuse a file others can write
