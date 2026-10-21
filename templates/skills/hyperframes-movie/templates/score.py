"""Synthesize the movie's score from scripts/schedule.py: original, seeded (identical every run), licence-free.

Arrangement follows each scene's "energy"; every (kind, cue) in a scene's "events" lands on its cue.
Automatic: a breath + riser into the first "drop" scene and a boom on its downbeat, a crash on every
flood cut, a kick on every flash cut, and a resolving chord under the "outro" scene with a fade-out.

Run from the project: uv run --with numpy --with scipy python scripts/score.py  →  assets/audio/score.wav
You cannot hear it; check the per-scene loudness it prints and ask the user to listen.
"""

import sys
from pathlib import Path

import numpy as np
from scipy.io import wavfile
from scipy.signal import butter, fftconvolve, sosfilt

sys.path.insert(0, str(Path(__file__).resolve().parent))
import schedule as S  # noqa: E402

SR = 44100
B = 60 / S.BPM
BAR = 4 * B
START, _t = {}, 0.0
for _sc in S.SCENES:
    START[_sc["id"]] = _t
    _t += _sc["beats"] * B if "beats" in _sc else float(_sc["seconds"])
TOTAL = _t
N = int(TOTAL * SR)
rng = np.random.default_rng(11)
OUT = Path(__file__).resolve().parent.parent / "assets" / "audio" / "score.wav"


def scene_at(t):
    cur = S.SCENES[0]
    for sc in S.SCENES:
        if START[sc["id"]] <= t + 1e-9:
            cur = sc
    return cur


def at(sc, ref):
    return START[sc["id"]] + (float(ref) if isinstance(ref, (int, float)) else float(sc["cues"][ref])) * B


# ---------------------------------------------------------------- voices
def hz(m):
    return 440.0 * 2 ** ((m - 69) / 12)


def tt(dur):
    return np.arange(int(dur * SR)) / SR


def filt(x, kind, f, order=2):
    return sosfilt(butter(order, f, kind, fs=SR, output="sos"), x)


def env(n, a, r, total):
    t = np.arange(n) / SR
    return np.minimum(1, t / max(a, 1e-4)) * np.clip((total - t) / max(r, 1e-4), 0, 1)


def saw(f, dur, harm=9):
    t = tt(dur)
    return sum(np.sin(2 * np.pi * f * h * t + h) / h**1.2 for h in range(1, harm) if f * h < 12000)


def pad(f, dur):
    x = sum(saw(f * 2 ** (d / 12), dur) for d in (-0.1, 0, 0.09))
    return x * env(len(x), 0.08, 0.3, dur) / 7


def pluck(f, dur=0.3):
    t = tt(dur)
    return sum(np.sin(2 * np.pi * f * h * t) * np.exp(-t * (12 + h * 3.5)) / h for h in range(1, 9)) * np.minimum(1, t / 0.002)


def bell(f, dur=1.6):
    t = tt(dur)
    return np.sin(2 * np.pi * f * t + 2.0 * np.exp(-t * 3) * np.sin(2 * np.pi * f * 3.5 * t)) * np.exp(-t * 2.2) * np.minimum(1, t / 0.002)


def bass(f, dur):
    t = tt(dur)
    x = np.sin(2 * np.pi * f * t) + 0.45 * np.sin(4 * np.pi * f * t) + 0.2 * np.sin(6 * np.pi * f * t)
    return np.tanh(1.8 * x) * env(len(t), 0.004, 0.04, dur)


def kick(punch=1.0):
    t = tt(0.42)
    body = np.sin(2 * np.pi * np.cumsum(46 + 140 * np.exp(-t * 34)) / SR) * np.exp(-t * 8)
    return np.tanh(1.6 * punch * (body + filt(rng.standard_normal(len(t)), "low", 5000) * np.exp(-t * 320) * 0.35))


def clap():
    t = tt(0.32)
    e = sum((t >= o) * np.exp(-np.maximum(t - o, 0) * (70 if o < 0.02 else 13)) for o in (0, 0.01, 0.021))
    return filt(rng.standard_normal(len(t)), "band", [900, 3200]) * e * 0.55


def hat(open_=False):
    t = tt(0.18 if open_ else 0.045)
    return filt(rng.standard_normal(len(t)), "high", 8000) * np.exp(-t * (20 if open_ else 90)) * 0.5


def crash():
    t = tt(1.6)
    return filt(rng.standard_normal(len(t)), "high", 4500) * np.exp(-t * 2.4) * 0.5


def tick():
    t = tt(0.05)
    return np.sin(2 * np.pi * 1900 * t) * np.exp(-t * 120) * 0.6


def stamp():
    t = tt(0.6)
    body = np.sin(2 * np.pi * np.cumsum(70 + 160 * np.exp(-t * 30)) / SR) * np.exp(-t * 9)
    return np.tanh(2.2 * (body + filt(rng.standard_normal(len(t)), "band", [300, 2500]) * np.exp(-t * 40) * 0.9))


def boom(dur=2.4):
    t = tt(dur)
    sub = np.sin(2 * np.pi * np.cumsum(32 + 45 * np.exp(-t * 6)) / SR) * np.exp(-t * 1.7)
    return np.tanh(1.6 * (sub + filt(rng.standard_normal(len(t)), "low", 200) * np.exp(-t * 9) * 0.8))


def riser(dur):
    t = tt(dur)
    x, out, seg = rng.standard_normal(len(t)), np.zeros(len(t)), 2048
    for s in range(0, len(x), seg):
        c = 400 * 20 ** (s / len(x))
        out[s : s + seg] = filt(x[s : s + seg], "band", [c * 0.7, min(c * 1.4, 19000)], 1)
    return (out + np.sin(2 * np.pi * np.cumsum(200 * 4 ** (t / dur)) / SR) * 0.25) * (t / dur) ** 2.4


def boop(up=True):
    t = tt(0.22)
    f = (300 if up else 700) * (2.2 if up else 0.45) ** (t / 0.22)
    return np.sin(2 * np.pi * np.cumsum(f) / SR) * np.exp(-t * 9) * 0.8


def swish(dur=0.28):
    t = tt(dur)
    x, out, seg = rng.standard_normal(len(t)), np.zeros(len(t)), 1024
    for s in range(0, len(x), seg):
        c = 600 + 5000 * np.sin(np.pi * s / len(x))
        out[s : s + seg] = filt(x[s : s + seg], "band", [c * 0.6, c * 1.5], 1)
    return out * np.sin(np.pi * t / dur) ** 2


def reverb(x, decay=2.0, mix=0.25):
    n = int(decay * SR)
    t = np.arange(n) / SR
    out = np.empty_like(x)
    for ch in range(2):
        ir = filt(rng.standard_normal(n) * np.exp(-t * 3.2 / decay), "low", 7000)
        out[ch] = fftconvolve(x[ch], ir / np.sqrt(np.sum(ir**2)))[: x.shape[1]]
    return x * (1 - mix) + out * mix


# ---------------------------------------------------------------- arrangement
music, drums, fx, bells = (np.zeros((2, N)) for _ in range(4))
side = np.ones(N)


def place(buf, sig, t, gain=1.0, pan=0.0):
    i = int(round(t * SR))
    if i >= N or i < 0:
        return
    if sig.ndim == 1:
        sig = np.vstack([sig * np.cos((pan + 1) * np.pi / 4), sig * np.sin((pan + 1) * np.pi / 4)]) * 1.414
    n = min(sig.shape[1], N - i)
    buf[:, i : i + n] += sig[:, :n] * gain


def duck(t):
    i, k = int(t * SR), np.arange(int(0.3 * SR))
    seg = side[i : i + len(k)]
    seg *= 1 - 0.6 * np.exp(-k[: len(seg)] / SR * 10)


PROG = [([50, 53, 57, 60, 64], 38), ([46, 50, 53, 57, 62], 34), ([53, 57, 60, 64, 67], 41), ([48, 52, 55, 57, 62], 36)]
for bar in range(int(np.ceil(TOTAL / BAR))):
    t0 = bar * BAR
    notes, root = PROG[bar % 4]
    e = scene_at(t0).get("energy", "full")
    if e in ("drop", "full", "break"):
        for k, m in enumerate(notes):
            place(music, pad(hz(m), BAR + 0.2), t0, 0.26, pan=(k - 2) * 0.3)
        arp = notes[1:] + [notes[2] + 12]
        for i in range(16):
            place(music, pluck(hz(arp[(i * 3) % len(arp)] + 12)), t0 + i * B / 4, 0.12 if e == "break" else 0.15, pan=0.4 if i % 2 else -0.4)
        for i in range(8):
            if i % 2:
                place(music, bass(hz(root), B / 2 - 0.02), t0 + i * B / 2, 0.38)
            elif e != "break":
                place(music, bass(hz(root - 12), B / 2 - 0.02), t0 + i * B / 2, 0.22)
    for b in range(4):
        tb = t0 + b * B
        eb = scene_at(tb).get("energy", "full")
        if eb == "outro" or tb >= TOTAL:
            continue
        if eb != "break" or b in (0, 2):
            place(drums, kick(0.9 if eb == "intro" else 1.15), tb, 0.85)
            duck(tb)
        if b in (1, 3) and eb != "intro":
            place(drums, clap(), tb, 0.5)
        place(drums, hat(), tb + B / 2, 0.28, pan=-0.2)
        if eb in ("drop", "full"):
            place(drums, hat(), tb + B / 4, 0.12, pan=0.3)
            place(drums, hat(), tb + 3 * B / 4, 0.12, pan=0.3)

breath = None
for i, sc in enumerate(S.SCENES):
    t = START[sc["id"]]
    if sc.get("energy") == "drop" and breath is None:
        breath = t
        place(fx, riser(0.7), t - 0.7, 0.12)
        place(fx, boom(), t, 0.8)
        place(drums, crash(), t, 0.6)
    elif sc.get("enter") == "flood" and i > 0 and sc.get("energy") != "outro":
        place(drums, crash(), t, 0.45)
    elif sc.get("enter") == "flash" and i > 0:
        place(fx, kick(1.3), t, 0.35)
    if sc.get("energy") == "outro":
        length = TOTAL - t
        place(fx, boom(3.0), t, 0.85)
        place(drums, crash(), t, 0.6)
        for k, m in enumerate([53, 57, 60, 64, 67, 72]):
            place(music, pad(hz(m), length), t, 0.3, pan=(k - 2.5) * 0.3)
        place(music, bass(hz(29), min(3.5, length)), t, 0.4)
        for k, m in enumerate([77, 81, 84, 88, 91]):
            place(bells, bell(hz(m), 2.5), t + k * 0.09, 0.14, pan=(k - 2) * 0.3)

DING = [72, 76, 79, 84, 88]
for sc in S.SCENES:
    count = {}
    for kind, ref in sc.get("events", []):
        t = at(sc, ref)
        k = count.get(kind, 0)
        count[kind] = k + 1
        if kind == "ding":
            place(bells, bell(hz(DING[k % 5]), 0.9), t, 0.28, pan=(k % 4 - 1.5) * 0.3)
        elif kind == "stamp":
            place(fx, stamp(), t, 0.6)
        elif kind == "tick":
            place(drums, tick(), t, 0.35)
        elif kind == "boop":
            place(fx, boop(k % 2 == 0), t, 0.3)
        elif kind == "swish":
            place(fx, swish(), t, 0.35, pan=0.6 if k % 2 == 0 else -0.6)
        elif kind == "hit":
            place(drums, crash() * 0.6, t, 0.35)
            place(fx, kick(1.3), t, 0.4)
        elif kind == "chord":
            place(fx, boom(2.0), t, 0.5)
            place(bells, sum(bell(hz(m), 2.0) for m in (74, 77, 81, 86)), t, 0.12)
        elif kind == "sparkle":
            for j, m in enumerate([77, 81, 84, 88, 91]):
                place(bells, bell(hz(m), 2.0), t + j * 0.09, 0.14, pan=(j - 2) * 0.3)
        elif kind == "riser":
            place(fx, riser(1.4), t - 1.4, 0.12)
        else:
            sys.exit(f"scene {sc['id']}: unknown event kind {kind!r}")

# ---------------------------------------------------------------- mix
tl = np.arange(N) / SR
for sc in S.SCENES:
    if sc.get("energy") == "break":
        a = START[sc["id"]]
        b = a + (sc["beats"] * B if "beats" in sc else float(sc["seconds"]))
        mask = (tl >= a) & (tl < b)
        music[:, mask] = filt(music[:, mask], "low", 1800)
mix = reverb(music * side, 1.8, 0.22) + reverb(bells, 2.6, 0.4) + drums + reverb(fx, 1.2, 0.15)
mix *= np.minimum(1, tl / 0.02) * np.clip((TOTAL - tl) / 2.5, 0, 1)
if breath:
    gap = (tl > breath - 0.15) & (tl < breath)
    mix[:, gap] *= np.linspace(1, 0.2, gap.sum())
mix = np.tanh(mix * 1.15)
mix /= np.max(np.abs(mix)) / 0.89
OUT.parent.mkdir(parents=True, exist_ok=True)
wavfile.write(OUT, SR, (mix.T * 32767).astype(np.int16))
print(f"wrote {OUT.name} · {TOTAL:.2f}s · RMS {20 * np.log10(np.sqrt(np.mean(mix**2))):.1f} dBFS")
m = mix.mean(0)
for sc in S.SCENES:
    a = int(START[sc["id"]] * SR)
    seg = m[a : a + int(1.5 * SR)]
    print(f"  {sc['id']:12s} {sc.get('energy', 'full'):6s} RMS {20 * np.log10(np.sqrt(np.mean(seg**2)) + 1e-9):6.1f} dBFS")
