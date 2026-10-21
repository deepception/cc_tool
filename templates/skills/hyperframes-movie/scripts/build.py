#!/usr/bin/env python3
"""Build a hyperframes-movie project from its scripts/schedule.py.

Writes index.html, then rewrites, in place, every generated region inside compositions/*.html:
  /*@bpm*/128                 tempo
  /*@beats*/12                the scene's length in beats (seconds for a "seconds" scene)
  /*@cue:name*/7              a cue's beat, from the scene's "cues"
  /*@brand*/ … /*@end*/       @font-face rules + --c-<name> colour tokens (inside the scene's <style>)
  /*@data:NAME*/[…]/*@end*/   shader-layer data (aura-bg.html, fx-top.html)
  /*@total*/56.6              total length; data-duration="…" on aura-bg / fx-top roots
Numbers stay literal in the HTML so `hyperframes lint` can still read every tween position.

usage: python3 build.py <project_dir>
"""

import json
import re
import sys
from pathlib import Path

proj = Path(sys.argv[1] if len(sys.argv) > 1 else ".").resolve()
sys.path.insert(0, str(proj / "scripts"))
import schedule as S  # noqa: E402

B = 60 / S.BPM
W, H = getattr(S, "WIDTH", 1920), getattr(S, "HEIGHT", 1080)
SC = {sc["id"]: sc for sc in S.SCENES}
errors = []


def length(sc):
    return sc["beats"] * B if "beats" in sc else float(sc["seconds"])


START, _t = {}, 0.0
for _sc in S.SCENES:
    START[_sc["id"]] = _t
    _t += length(_sc)
TOTAL = _t


def beat_of(sc, ref):
    if isinstance(ref, (int, float)):
        return float(ref)
    if ref not in sc.get("cues", {}):
        errors.append(f"scene {sc['id']}: unknown cue {ref!r}")
        return 0.0
    return float(sc["cues"][ref])


def at(scene_id, ref):
    return START[scene_id] + beat_of(SC[scene_id], ref) * B


def fmt(x):
    return f"{x:.5f}".rstrip("0").rstrip(".")


def next_scene(sc):
    i = S.SCENES.index(sc)
    return S.SCENES[i + 1] if i + 1 < len(S.SCENES) else None


# ---------------------------------------------------------------- validation
for sc in S.SCENES:
    if not (proj / "compositions" / f"{sc['file']}.html").exists():
        errors.append(f"scene {sc['id']}: compositions/{sc['file']}.html missing")
    for line in sc.get("copy", []):
        beat_of(sc, line[0])
        if line[2] is not None:
            beat_of(sc, line[2])
    for kind, ref in sc.get("events", []):
        beat_of(sc, ref)
for f in S.FONTS:
    if not (proj / "assets" / "fonts" / f[1]).exists():
        errors.append(f"font assets/fonts/{f[1]} missing")
if errors:
    sys.exit("schedule errors:\n  " + "\n  ".join(errors))

# ---------------------------------------------------------------- generated blocks
brand_css = "".join(
    f'@font-face {{ font-family: "{fam}"; src: url("assets/fonts/{file}") format("truetype"); font-weight: {w}; font-style: {style}; }}\n'
    for fam, file, w, style in S.FONTS
) + "#root { " + " ".join(f"--c-{k}: {v};" for k, v in S.COLORS.items()) + " }\n"

DIRS = [(0.7, 0.7), (1.0, 0.15), (-1.0, 0.2), (0.0, 1.0), (-0.6, 0.8), (0.9, -0.4)]
floods, flashes, aura_keys = [], [], []
for i, sc in enumerate(S.SCENES):
    t = START[sc["id"]]
    if sc.get("enter") == "flood" and i > 0:
        a, b, c = sc.get("flood", S.FLOOD)
        dx, dy = DIRS[len(floods) % len(DIRS)]
        floods.append([round(t, 5), a, b, c, dx, dy, round(1.3 + 2.7 * len(floods), 2)])
    if sc.get("enter") == "flash" and i > 0:
        flashes.append([round(t, 5), 0.35])
    pal = sc.get("aura", S.AURA)
    amp = 0.95 if sc.get("ground") == "aura" else 0.25
    aura_keys.append([round(max(0.0, t - 0.15), 5), amp, *pal])
for scene_id, ref, amount in getattr(S, "FLASHES", []):
    flashes.append([round(at(scene_id, ref), 5), amount])
flashes.sort()
DATA = {"FLOODS": floods, "FLASHES": flashes, "AURA": aura_keys}


def rewrite(path, sc=None):
    s = path.read_text()
    s = re.sub(r"/\*@bpm\*/\s*[\d.]+", f"/*@bpm*/{S.BPM}", s)
    s = re.sub(r"/\*@total\*/\s*[\d.]+", f"/*@total*/{fmt(TOTAL)}", s)
    s = re.sub(r"/\*@brand\*/.*?/\*@end\*/", lambda m: f"/*@brand*/\n{brand_css}/*@end*/", s, flags=re.S)
    s = re.sub(r"/\*@data:(\w+)\*/.*?/\*@end\*/", lambda m: f"/*@data:{m.group(1)}*/{json.dumps(DATA[m.group(1)])}/*@end*/", s, flags=re.S)
    if sc is not None:
        n = sc["beats"] if "beats" in sc else sc["seconds"]
        s = re.sub(r"/\*@beats\*/\s*[\d.]+", f"/*@beats*/{fmt(n)}", s)

        def cue(m):
            name = m.group(1)
            if name not in sc.get("cues", {}):
                errors.append(f"{path.name}: /*@cue:{name}*/ is not a cue of scene {sc['id']}")
                return m.group(0)
            return f"/*@cue:{name}*/{fmt(sc['cues'][name])}"

        s = re.sub(r"/\*@cue:([\w-]+)\*/\s*[\d.]+", cue, s)
    else:
        s = re.sub(r'(data-composition-id="(?:aura-bg|fx-top)"[^>]*?data-duration=")[\d.]+(")', rf"\g<1>{fmt(TOTAL)}\2", s)
    path.write_text(s)


for sc in S.SCENES:
    rewrite(proj / "compositions" / f"{sc['file']}.html", sc)
for layer in ("aura-bg", "fx-top"):
    if (proj / "compositions" / f"{layer}.html").exists():
        rewrite(proj / "compositions" / f"{layer}.html")
if errors:
    sys.exit("cue errors:\n  " + "\n  ".join(errors))

# ---------------------------------------------------------------- index.html


def host(cid, src, start, dur, track):
    return (
        f'      <div id="el-{cid}" data-composition-id="{cid}" data-composition-src="compositions/{src}.html" '
        f'data-start="{fmt(start)}" data-duration="{fmt(dur)}" data-track-index="{track}" data-width="{W}" data-height="{H}"></div>'
    )


rows = []
if (proj / "compositions" / "aura-bg.html").exists():
    rows.append(host("aura-bg", "aura-bg", 0, TOTAL, 0))
rows += [host(sc["id"], sc["file"], START[sc["id"]], length(sc), 1) for sc in S.SCENES]
if (proj / "compositions" / "fx-top.html").exists():
    rows.append(host("fx-top", "fx-top", 0, TOTAL, 5))

audio, lanes = [], {}
if (proj / "assets" / "audio" / "score.wav").exists():
    audio.append(f'      <audio id="a-score" src="assets/audio/score.wav" data-start="0" data-duration="{fmt(TOTAL)}" data-track-index="10" data-volume="0.9"></audio>')
for sid, stem, scene_id, ref, dur, vol in sorted(getattr(S, "SAMPLE_SFX", []), key=lambda x: at(x[2], x[3])):
    start = at(scene_id, ref)
    lane = 11
    while lanes.get(lane, -1) > start + 1e-6:
        lane += 1
    lanes[lane] = start + dur
    audio.append(f'      <audio id="a-{sid}" src="assets/audio/{stem}.mp3" data-start="{fmt(start)}" data-duration="{dur}" data-track-index="{lane}" data-volume="{vol}"></audio>')

ink = S.COLORS.get("ink", "#000000")
html = f"""<!doctype html>
<html lang="en">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width={W}, height={H}" />
    <title>{S.TITLE}</title>
    <script src="https://cdn.jsdelivr.net/npm/gsap@3.14.2/dist/gsap.min.js"></script>
    <style>
      * {{ margin: 0; padding: 0; box-sizing: border-box; }}
      html, body {{ margin: 0; width: {W}px; height: {H}px; overflow: hidden; background: {ink}; }}
      #root {{ position: relative; width: 100%; height: 100%; overflow: hidden; background: {ink}; }}
      #root > div[data-composition-src] {{ position: absolute; inset: 0; }}
      #grain {{
        position: absolute; inset: 0; pointer-events: none; opacity: 0.06; mix-blend-mode: overlay;
        background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='160' height='160'%3E%3Cfilter id='n'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='0.9' numOctaves='2'/%3E%3C/filter%3E%3Crect width='160' height='160' filter='url(%23n)' opacity='0.55'/%3E%3C/svg%3E");
      }}
    </style>
  </head>
  <body>
    <!-- Generated by build.py from scripts/schedule.py ({S.BPM} BPM). Edit the schedule, not this file. -->
    <div id="root" data-composition-id="main" data-start="0" data-duration="{fmt(TOTAL)}" data-width="{W}" data-height="{H}">
{chr(10).join(rows)}
      <div id="grain" class="clip" data-start="0" data-duration="{fmt(TOTAL)}" data-track-index="6"></div>
{chr(10).join(audio)}
    </div>
    <script>
      const tl = gsap.timeline({{ paused: true }});
      tl.fromTo("#grain", {{ backgroundPosition: "0px 0px" }}, {{ backgroundPosition: "7919px 6007px", duration: {fmt(TOTAL)}, ease: "steps({int(TOTAL * 12)})" }}, 0);
      window.__timelines["main"] = tl;
    </script>
  </body>
</html>
"""
(proj / "index.html").write_text(html)

print(f"{S.BPM} BPM · beat {B:.4f}s · total {TOTAL:.3f}s")
for sc in S.SCENES:
    n = f"{sc['beats']} beats" if "beats" in sc else f"{sc['seconds']}s"
    print(f"  {sc['id']:12s} {START[sc['id']]:7.3f}s  {n:9s} enter={sc.get('enter', 'cut'):5s} ground={sc.get('ground', 'opaque')}")
print(f"floods {len(floods)} · flashes {len(flashes)} · sample SFX {len(audio) - (1 if audio and 'a-score' in audio[0] else 0)}")
if not any(sc.get("energy") == "drop" for sc in S.SCENES):
    print("note: no scene has energy 'drop'; the score will have no big downbeat")
