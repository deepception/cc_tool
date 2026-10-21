"""The movie in one file: tempo, scene lengths, cue beats, on-screen copy and scored events.

Brief (fill in before anything else; every claim on screen must trace to a source):
  product:   <name> — <one-line value proposition, in your own words>
  audience:  <who>
  promise:   <the tagline the end card carries>
  sources:   <docs / code paths / URLs each claim was checked against>

Everything timed in the movie is derived from this file by `build.py` (index.html, the cue numbers
inside every scene, the shader layers), `score.py` (music) and `reading_audit.py` (readability).
Change speed here, never by editing numbers inside compositions/*.html.

Knobs, from coarse to fine:
  BPM             global speed; 120–128 for launch energy, 100–110 for calm/luxury
  scene "beats"   how long a section holds; reading_audit.py prints the minimum each section needs
  cue beats       when each element lands inside its scene (half beats are fine: 9.5)
  copy            fewer words is the cheapest way to make a section shorter
"""

BPM = 128
WIDTH, HEIGHT = 1920, 1080  # 1080×1920 for Shorts/Reels/TikTok, 1080×1080 for feeds
TITLE = "Product — Launch"

# Brand tokens: injected into every scene as CSS custom properties (--c-<name>) and @font-face rules.
COLORS = {
    "ink": "#1a1a2e",
    "paper": "#f3f0f3",
    "accent": "#c77ba8",
    "accent-deep": "#8e3a6f",
    "pop1": "#00d4ff",
    "pop2": "#e040fb",
}
# (CSS family name, file in assets/fonts/, weight or weight range, style)
FONTS = [
    ("Display", "Display-VF.ttf", "100 900", "normal"),
    ("Display", "Display-Italic-VF.ttf", "100 900", "italic"),
    ("Sans", "Sans-Regular.ttf", "400", "normal"),
    ("Sans", "Sans-SemiBold.ttf", "600", "normal"),
]
AURA = ("#5a2350", "#b0679a", "#3fa9c9")  # default palette of the live background shader
FLOOD = ("#8e3a6f", "#c77ba8", "#5fd4ee")  # default palette of the liquid flood transition

# One dict per scene, in order. Keys:
#   id, file        composition id and compositions/<file>.html
#   beats | seconds length (beats for everything but the end card)
#   ground          "opaque" (scene paints its own background) or "aura" (the live shader shows through)
#   aura            optional palette override while this scene is on screen
#   enter           how the cut INTO the scene looks: "cut", "flash" or "flood" (+ optional "flood" palette)
#   energy          score layer: "intro", "drop" (first full section: boom on its downbeat), "full",
#                   "break" (filtered, half-time) or "outro" (the end card)
#   cues            name → beat inside the scene; scenes reference them as /*@cue:name*/<n>
#   copy            (cue, text, until-cue or None = scene end[, {"flurry": True} | {"words": n}])
#   events          (kind, cue-or-beat) scored by score.py: ding stamp tick boop swish hit chord sparkle riser
SCENES = [
    {
        "id": "hook", "file": "01-hook", "beats": 12, "ground": "opaque", "enter": "cut", "energy": "intro",
        "cues": {"setup": 0, "turn": 4, "punch": 7},
        "copy": [("setup", "Looks perfect online.", "turn"), ("turn", "Arrives wrong.", "punch"), ("punch", "Sound familiar?", None)],
        "events": [("ding", 0), ("ding", 1), ("ding", 2), ("ding", 3), ("stamp", "turn"), ("stamp", 5), ("stamp", 6)],
    },
    {
        "id": "brand", "file": "02-brand", "beats": 10, "ground": "aura", "enter": "flood", "energy": "drop",
        "cues": {"mark": 0, "wordmark": 1, "w1": 2, "w2": 3, "w3": 4, "eyebrow": 5, "dive": 8.5},
        "copy": [("w3", "Your promise here.", "dive"), ("eyebrow", "The product category", "dive")],
        "events": [("sparkle", "mark")],
    },
    {
        "id": "feature", "file": "03-feature", "beats": 14, "ground": "opaque", "enter": "flash", "energy": "full",
        "cues": {"title": 0, "flurry": 1, "land": 6, "detail": 7},
        "copy": [
            ("title", "One big idea.", None),
            ("flurry", "options flipping past", "land", {"flurry": True}),
            ("land", "The answer, landed. One short supporting line", None),
            ("detail", "One proof point", None),
        ],
        "events": [("tick", 1), ("tick", 2), ("tick", 3), ("tick", 4), ("tick", 5), ("chord", "land")],
    },
    {
        "id": "montage", "file": "04-montage", "beats": 12, "ground": "opaque", "enter": "flood", "energy": "full",
        "cues": {"w1": 0, "w2": 2, "w3": 4, "w4": 6, "w5": 8},
        "copy": [("w1", "Fast.", "w2"), ("w2", "Clear.", "w3"), ("w3", "Yours.", "w4"), ("w4", "Everywhere.", "w5"), ("w5", "15 languages.", None)],
        "events": [("hit", "w1"), ("hit", "w2"), ("hit", "w3"), ("hit", "w4"), ("hit", "w5"), ("riser", 12)],
    },
    {
        "id": "end", "file": "09-end", "seconds": 6.0, "ground": "aura", "enter": "flood", "energy": "outro",
        "cues": {"lockup": 0, "tagline": 1, "cta": 4, "platforms": 5},
        "copy": [("tagline", "Your promise here.", None), ("cta", "Start free · product.dev", None)],
        "events": [],
    },
]

# Extra white flashes on big landings: (scene id, cue or beat, strength 0–1)
FLASHES = [("feature", "land", 0.45)]

# Optional sample SFX (Pixabay files bundled with HyperFrames, copied into assets/audio/):
# (id, file stem, scene id, cue or beat, duration s, volume)
SAMPLE_SFX = []
