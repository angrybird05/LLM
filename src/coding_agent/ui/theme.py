"""NEXUS theme: the visual identity of the console.

A restrained dark palette — deep space surfaces, one cyan accent carrying the
identity, violet for cognition, semantic green/amber/red reserved for meaning.
Neon comes from contrast and a few glowing glyphs, not from saturation
everywhere. Registered as a Textual theme so every widget inherits consistent
$primary/$surface/... variables, with extra tokens for the custom chrome.
"""
from textual.theme import Theme

# Extra tokens used directly by widgets (Textual themes carry the core set;
# these live alongside as plain constants).
BG = "#060a12"          # app background — near-black blue
SURFACE = "#0b1220"     # panels
SURFACE_RAISED = "#101a2c"
BORDER = "#1a2942"      # quiet structural borders
GRID = "#0f1a2e"

TEXT = "#c7d5e8"        # primary foreground
MUTED = "#5d7290"       # secondary text
FAINT = "#31435e"       # hints, rules

CYAN = "#22d3ee"        # identity / accents / streaming
VIOLET = "#a78bfa"      # reasoning / planning
GREEN = "#34d399"       # success / complete
AMBER = "#fbbf24"       # warnings / searching / executing
RED = "#f87171"         # errors
BLUE = "#60a5fa"        # info / links
PINK = "#f472b6"        # editing

NEXUS_THEME = Theme(
    name="nexus",
    primary=CYAN,
    secondary=VIOLET,
    accent=GREEN,
    foreground=TEXT,
    background=BG,
    surface=SURFACE,
    panel=SURFACE_RAISED,
    success=GREEN,
    warning=AMBER,
    error=RED,
    dark=True,
    variables={
        "block-cursor-background": CYAN,
        "block-cursor-foreground": BG,
    },
)

STATUS_COLORS: dict[str, str] = {
    "online": MUTED,
    "analyzing": VIOLET,
    "planning": VIOLET,
    "reading": CYAN,
    "searching": BLUE,
    "executing": AMBER,
    "editing": PINK,
    "complete": GREEN,
    "error": RED,
}

LAYOUT_CSS = f"""
Screen {{
    background: {BG};
}}
#main {{
    height: 1fr;
}}
.panel {{
    border: round {BORDER};
    background: {SURFACE};
    padding: 0 1;
}}
.panel-title {{
    color: {MUTED};
    text-style: bold;
}}
.hidden {{ display: none; }}
"""
