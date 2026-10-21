#!/usr/bin/env python3
"""Gate: can a viewer read every line of on-screen copy before it leaves?

Rule (calibrated on a launch film where 22 of 34 lines failed and the viewer called it "too fast"):
  need = 0.4 s to find the line + words / 3 words-per-second to read it
A line's window runs from its cue + LAG (the entrance settling into legibility) to its until-cue, or
to the scene's end minus FLOOD_COVER when the next scene enters with a liquid flood.
Lines marked {"flurry": True} are exempt (a deliberate blur of options; only its landing is read).

Prints each line, the verdict, and the minimum length each scene needs. Exit 1 if any line is short.
usage: python3 reading_audit.py <project_dir>
"""

import math
import sys
from pathlib import Path

proj = Path(sys.argv[1] if len(sys.argv) > 1 else ".").resolve()
sys.path.insert(0, str(proj / "scripts"))
import schedule as S  # noqa: E402

B = 60 / S.BPM
LAG = 0.15
FLOOD_COVER = 0.2
WPS = 3.0
FIND = 0.4


def need(words):
    return FIND + words / WPS


def words_in(text):
    return len([w for w in text.replace("·", " ").replace("—", " ").split() if any(ch.isalnum() for ch in w)])


short = 0
print(f"{'scene':10s} {'line':46s} {'need':>6s} {'shown':>7s}")
for i, sc in enumerate(S.SCENES):
    length = sc["beats"] * B if "beats" in sc else float(sc["seconds"])
    nxt = S.SCENES[i + 1] if i + 1 < len(S.SCENES) else None
    cover = FLOOD_COVER if nxt and nxt.get("enter") == "flood" else 0.0
    cues = sc.get("cues", {})
    min_len = 0.0
    for line in sc.get("copy", []):
        ref, text, until = line[0], line[1], line[2]
        opts = line[3] if len(line) > 3 else {}
        n = opts.get("words", words_in(text))
        start = cues.get(ref, ref if isinstance(ref, (int, float)) else 0) * B + LAG
        end = (cues[until] * B if until is not None else length - cover)
        shown = end - start
        if opts.get("flurry"):
            verdict = "flurry"
        else:
            ok = shown >= need(n) - 0.05
            verdict = "ok" if ok else "SHORT"
            short += not ok
            if until is None:
                min_len = max(min_len, start + need(n) + cover)
        label = (text[:43] + "…") if len(text) > 44 else text
        print(f"{sc['id']:10s} {label:46s} {need(n):5.2f}s {shown:6.2f}s  {verdict}")
    if "beats" in sc and min_len > 0:
        min_beats = math.ceil(min_len / B - 1e-9)
        flag = "  ← lengthen" if min_beats > sc["beats"] else ("  (slack: could shorten)" if min_beats < sc["beats"] - 2 else "")
        print(f"{'':10s} {'→ scene needs at least':46s} {min_beats:3d} beats (has {sc['beats']}){flag}")
    print()

print(f"lines too short to read: {short}")
sys.exit(1 if short else 0)
