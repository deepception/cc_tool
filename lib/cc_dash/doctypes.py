"""Document types: the one rule set cc-docs-migrate writes into `type:` and cc-dash's Work view groups by.

classify() reads the folder first, then the name, then (when a parsed plan is given) the shape.
"""
import posixpath
import re

from . import plans

TRACKER_NAMES = {"tasks.md", "todo.md", "backlog.md", "open-items.md"}
REPORT_NAMES = {"e2e-test-plan.md", "ui-ux-review.md", "frontend-review.md"}
GUIDE_NAME = re.compile(r"(?i)(^|[-_.])(guide|how-?to)([-_.]|$)")            # not GUIDELINES.md
REPORT_NAME = re.compile(r"(?i)(^|[-_.])(report|review|audit|assessment|findings)([-_.]|$)")


def classify(rel: str, parsed: dict | None = None, h1: str = "") -> str:
    """The folder first, then the name, then the shape (contract v2 order: spec, plan, adr, glossary,
    start-here, task, tracker, plan by checkboxes, guide, reference, prd, log, report, plan by a
    `plans` folder, note). With parsed=None the shape-based branches (plan by checkboxes) are skipped."""
    name = posixpath.basename(rel.rstrip("/"))
    parts = {p.lower() for p in rel.split("/")[:-1]}
    low = name.lower()
    if rel.startswith("vault/notes/"):
        return "note"
    if rel.startswith("docs/superpowers/specs/"):
        return "spec"
    if rel.startswith("docs/superpowers/plans/") and plans.is_plan(parsed, "plans"):
        return "plan"
    if parts & {"adr", "adrs", "decisions"}:
        return "adr"
    if low == "glossary.md":
        return "glossary"
    if rel == "docs/start-here.md":
        return "start-here"
    if "tasks" in parts:
        return "task"
    if low in TRACKER_NAMES or re.search(r"(?i)[-_](tasks|todo|backlog)\.md$", name):
        return "tracker"
    if plans.is_plan(parsed, "docs"):
        return "plan"   # what cc-dash's Plans view tracks as one
    if parts & {"guides", "how-to-guides", "how-to"} or GUIDE_NAME.search(name):
        return "guide"
    if "reference" in parts or re.match(r"(?i)^(architecture|structure|api|quick-reference|reference)\b", name):
        return "reference"
    if re.search(r"(?i)(^|[-_])prd([-_.]|$)", name) or "product requirements" in (h1 or "").lower():
        return "prd"
    if re.search(r"(?i)(^|[-_])(log|journal|history)\.md$", name) or low == "log.md":
        return "log"
    if low in REPORT_NAMES or "reports" in parts or REPORT_NAME.search(name):
        return "report"
    if "plans" in parts:
        return "plan"   # a plan folder's prose plan (LOG.md, reviews and evidence beside it matched above)
    return "note"
