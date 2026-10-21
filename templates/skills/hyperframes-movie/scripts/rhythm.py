#!/usr/bin/env python3
"""Gate: is the film alive without being frantic? Snapshots every STEP s, measures frame-to-frame change.

  hold   = a run ≥ 1 s where mean |Δluma| (240×135 greyscale) stays < 1.0 per step: nothing moves
  event  = a step with change > 12: a cut, a slam, a big move
Targets (calibrated: 54 % holds + an event every 4.5 s read "boring"; 6 % holds with 22 unreadable
lines read "too fast"; 18 % holds, an event every 1.9 s and zero unreadable lines read "right"):
  holds ≤ 25 % of runtime · no hold > 2.5 s except on the final card · an event at least every 2.2 s

Writes contact sheets (one frame per 0.5 s, 20 per sheet) for the visual review.
usage: uv run --with numpy --with pillow python rhythm.py <project_dir> <out_dir> [step=0.25]
"""

import glob
import json
import re
import subprocess
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

proj = Path(sys.argv[1]).resolve()
out = Path(sys.argv[2]).resolve()
step = float(sys.argv[3]) if len(sys.argv) > 3 else 0.25

total = float(re.search(r'data-composition-id="main"[^>]*data-duration="([\d.]+)"', (proj / "index.html").read_text()).group(1))
pin = re.search(r"hyperframes@([\d.]+)", json.loads((proj / "package.json").read_text())["scripts"]["check"]).group(1)
times = [round(i * step, 3) for i in range(int(total / step))]
if not list(out.glob("frame-*.png")):
    subprocess.run(
        ["npx", "--yes", f"hyperframes@{pin}", "snapshot", "--describe", "false", "--no-end", "--timeout", "30000",
         "-o", str(out), "--at", ",".join(f"{t:g}" for t in times)],
        cwd=proj, check=True, stdout=subprocess.DEVNULL,
    )

files = sorted(glob.glob(f"{out}/frame-*.png"), key=lambda f: int(re.search(r"frame-(\d+)", f).group(1)))
ts = [float(re.search(r"at-([\d.]+)s", f).group(1)) for f in files]
frames = [np.asarray(Image.open(f).convert("L").resize((240, 135)), dtype=np.float32) for f in files]
diff = [0.0] + [float(np.mean(np.abs(frames[i] - frames[i - 1]))) for i in range(1, len(frames))]

holds, start = [], None
for t, c in zip(ts + [ts[-1] + step], diff + [99.0]):
    if c < 1.0:
        start = t if start is None else start
    else:
        if start is not None and t - start >= 1.0:
            holds.append((start, t))
        start = None
held = sum(b - a for a, b in holds)
events = sum(1 for c in diff if c > 12)
every = ts[-1] / max(1, events)
last_card = total - 6.5
long_holds = [(a, b) for a, b in holds if b - a > 2.5 and a < last_card]

print(f"{len(files)} frames over {ts[-1]:.1f}s")
print(f"holds: {held:.1f}s = {100 * held / ts[-1]:.0f}% (target ≤ 25%)  " + " ".join(f"{a:.2f}–{b:.2f}" for a, b in holds))
print(f"long holds before the final card (> 2.5 s): {len(long_holds)} " + " ".join(f"{a:.2f}–{b:.2f}" for a, b in long_holds))
print(f"events: {events}, one every {every:.1f}s (target ≤ 2.2 s) · mean change {np.mean(diff):.2f}")
ok = held / ts[-1] <= 0.25 and not long_holds and every <= 2.2
print("RHYTHM: PASS" if ok else "RHYTHM: FAIL")

sel = [(t, f) for t, f in zip(ts, files) if abs(t * 2 - round(t * 2)) < 1e-6]
W, H, cols, rows = 480, 270, 5, 4
for s in range(0, len(sel), cols * rows):
    sheet = Image.new("RGB", (cols * W, rows * (H + 22)), "#111")
    dr = ImageDraw.Draw(sheet)
    for k, (t, f) in enumerate(sel[s : s + cols * rows]):
        x, y = (k % cols) * W, (k // cols) * (H + 22)
        sheet.paste(Image.open(f).convert("RGB").resize((W, H)), (x, y + 22))
        dr.text((x + 6, y + 4), f"{t:.1f}s", fill="white")
    sheet.save(out / f"sheet-{s // (cols * rows) + 1}.jpg", quality=82)
print(f"contact sheets: {out}/sheet-*.jpg")
sys.exit(0 if ok else 1)
