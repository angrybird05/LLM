"""Animation primitives: tiny, subtle, terminal-native.

Frame cycles for spinners and pulse dots. The rule of the house: motion only
where work is happening (the status pill, streaming text), never decoration
for its own sake.
"""
from itertools import cycle

# Braille dots — the standard quiet spinner.
BRAILLE = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
# Orbit glyphs — used by the state pill while a turn runs.
ORBITS = "◐◓◑◒"
# Pulse — connection / activity dot.
PULSE = ["●", "◉", "○", "◉"]
# Progress bar halves drawn with block chars (used by mini gauges).
BAR_FULL = "▰"
BAR_EMPTY = "▱"


class Frames:
    """A restartable frame cycler; `next()` is safe from any timer."""

    def __init__(self, frames: str | list[str] = BRAILLE):
        self._cycle = cycle(frames)
        self._frames = frames

    def next(self) -> str:
        return next(self._cycle)

    def reset(self) -> None:
        self._cycle = cycle(self._frames)


def bar(pct: float, width: int = 10, full: str = BAR_FULL, empty: str = BAR_EMPTY) -> str:
    """Render a mini progress gauge: bar(0.4, 10) -> '▰▰▰▰▱▱▱▱▱▱'."""
    pct = max(0.0, min(1.0, pct))
    filled = round(pct * width)
    return full * filled + empty * (width - filled)
