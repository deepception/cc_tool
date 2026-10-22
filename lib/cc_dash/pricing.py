"""Model prices (USD per million tokens) and the per-message cost estimate.

Current models come from README.md "Choosing a model". Older ids are the ones
seen in real transcripts; their prices were cross-checked against the
`cost-state` records Claude Code writes into newer transcripts.

Cache writes: a 5-minute write costs 1.25x the input price, a 1-hour write 2x.
Cache reads use the listed price. A model id missing from this table shows its
tokens with cost "n/a" rather than a guess.
"""
import re

# id: (input, output, cache_read)  -- cache-write prices derive from input.
PRICES = {
    "claude-opus-5-5": (4.00, 20.00, 0.20),
    "claude-sonnet-5-5": (2.00, 10.00, 0.20),
    "claude-haiku-5-5": (0.10, 0.50, 0.01),
    "claude-opus-5": (5.00, 25.00, 0.50),
    "claude-sonnet-5": (2.00, 10.00, 0.20),
    "claude-fable-5": (10.00, 50.00, 1.00),
    "claude-fable-5-1": (10.00, 50.00, 1.00),
    "claude-opus-4-8": (5.00, 25.00, 0.50),
    "claude-opus-4-7": (5.00, 25.00, 0.50),
    "claude-opus-4-6": (5.00, 25.00, 0.50),
    "claude-opus-4-5": (5.00, 25.00, 0.50),
    "claude-opus-4-1": (15.00, 75.00, 1.50),
    "claude-opus-4": (15.00, 75.00, 1.50),
    "claude-sonnet-4-6": (3.00, 15.00, 0.30),
    "claude-sonnet-4-5": (3.00, 15.00, 0.30),
    "claude-sonnet-4": (3.00, 15.00, 0.30),
    "claude-haiku-4-5": (1.00, 5.00, 0.10),
    "claude-3-5-haiku": (0.80, 4.00, 0.08),
}

# Haiku 5.5 charges a higher tier once a request's prompt passes 100K tokens.
LONG_CONTEXT = {
    "claude-haiku-5-5": (100_000, (0.50, 2.50, 0.05)),
}

# Claude Code's default effort per model when nothing sets one (README.md
# "Choosing a model"). Used only to label an unrecorded effort as a default.
DEFAULT_EFFORT = {
    "claude-opus-5-5": "medium",
    "claude-sonnet-5-5": "medium",
    "claude-haiku-5-5": "medium",
}
EFFORT_LEVELS = ("low", "medium", "high", "xhigh", "max")

# Fast mode (usage.speed == "fast") doubles the price ($8/$40 for Opus 5.5).
FAST_MULTIPLIER = 2.0

_DATE_SUFFIX = re.compile(r"-\d{8}$")
_BRACKET = re.compile(r"\[.*?\]$")


def normalize(model: str) -> str:
    """claude-haiku-4-5-20251001 -> claude-haiku-4-5; claude-opus-5[1m] -> claude-opus-5."""
    m = (model or "").strip().lower()
    m = _BRACKET.sub("", m)
    m = _DATE_SUFFIX.sub("", m)
    if m.startswith("us.anthropic.") or m.startswith("anthropic."):
        m = m.split("anthropic.", 1)[1]
    return m


def family(model: str) -> str:
    m = normalize(model)
    for f in ("opus", "sonnet", "haiku", "fable"):
        if f in m:
            return f
    return "other"


def display_name(model: str) -> str:
    """claude-opus-5-5 -> Opus 5.5; claude-3-5-haiku -> Haiku 3.5."""
    m = normalize(model)
    if not m:
        return "Unknown"
    if m.startswith("<"):
        return m
    parts = m.replace("claude-", "").split("-")
    fam = next((p for p in parts if p in ("opus", "sonnet", "haiku", "fable")), None)
    if fam is None:
        return m
    nums = [p for p in parts if p.isdigit()]
    return fam.capitalize() + (" " + ".".join(nums) if nums else "")


def default_effort(model: str) -> str:
    """The effort Claude Code uses for this model when none is set, or '' when we do not know it."""
    return DEFAULT_EFFORT.get(normalize(model), "")


def has_price(model: str) -> bool:
    return normalize(model) in PRICES


def message_cost(model: str, inp: int, out: int, cread: int, cw5: int, cw1h: int,
                 speed: str = ""):
    """Estimated USD for one API message, or None when the model is unpriced."""
    m = normalize(model)
    p = PRICES.get(m)
    if p is None:
        return None
    pin, pout, pread = p
    lc = LONG_CONTEXT.get(m)
    if lc and (inp + cread + cw5 + cw1h) > lc[0]:
        pin, pout, pread = lc[1]
    cost = (inp * pin + out * pout + cread * pread + cw5 * pin * 1.25 + cw1h * pin * 2.0) / 1e6
    if speed == "fast":
        cost *= FAST_MULTIPLIER
    return cost


def cache_write_cost(model: str, inp: int, cread: int, cw5: int, cw1h: int, speed: str = ""):
    """The cache-write share of one message's cost (routing.py's cache-expiry rewrites), or None when unpriced."""
    m = normalize(model)
    p = PRICES.get(m)
    if p is None:
        return None
    pin = p[0]
    lc = LONG_CONTEXT.get(m)
    if lc and (inp + cread + cw5 + cw1h) > lc[0]:
        pin = lc[1][0]
    cost = (cw5 * pin * 1.25 + cw1h * pin * 2.0) / 1e6
    return cost * FAST_MULTIPLIER if speed == "fast" else cost


def table():
    """The pricing table as rows for the dashboard's footnote."""
    rows = []
    for mid, (pin, pout, pread) in PRICES.items():
        rows.append({"id": mid, "name": display_name(mid), "family": family(mid),
                     "input": pin, "output": pout, "cache_read": pread,
                     "cache_write_5m": round(pin * 1.25, 4), "cache_write_1h": round(pin * 2, 4)})
    return rows
