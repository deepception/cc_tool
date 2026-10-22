"""cc-dash's process checks: templates/hooks/cc_procs.py of this same checkout.

That file is the one source. cc-setup copies it into each project's
.claude/hooks/, where the control hook imports it from beside itself (a
protected path in Claude Code), so the hook never runs code from the checkout;
cc-dash runs it from here. Same code, so both judge a process the same way.
"""
import importlib.util
import os
import sys

_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.realpath(__file__)))),
                     "templates", "hooks", "cc_procs.py")
_spec = importlib.util.spec_from_file_location("cc_procs", _PATH)
_mod = importlib.util.module_from_spec(_spec)
_was, sys.dont_write_bytecode = sys.dont_write_bytecode, True     # cc-dash writes nothing into the checkout
try:
    _spec.loader.exec_module(_mod)
finally:
    sys.dont_write_bytecode = _was
globals().update({k: v for k, v in vars(_mod).items() if not k.startswith("__")})
