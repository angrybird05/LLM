"""Center-panel state bar: the animated agent state pill + current task.

States render exactly per spec: [ ◉ ANALYZING ] … [ ✓ COMPLETE ], [ × ERROR ].
Active states animate the glyph and tick elapsed seconds; terminal states are
steady. Motion exists only while work is in flight.
"""
import time

from rich.text import Text
from textual.reactive import reactive
from textual.widget import Widget

from coding_agent.agent.state_manager import ACTIVE_STATES, AgentStatus, label
from coding_agent.ui import theme as T
from coding_agent.ui.animations import ORBITS, Frames


class StateBar(Widget):
    DEFAULT_CSS = """
    StateBar {
        height: 1;
        dock: top;
        background: transparent;
        padding: 0 1;
    }
    """

    status: reactive[AgentStatus] = reactive(AgentStatus.ONLINE, init=False)
    task: reactive[str] = reactive("", init=False)
    step: reactive[int] = reactive(0, init=False)

    def __init__(self):
        super().__init__()
        self._frames = Frames(ORBITS)
        self._anim = None          # Timer while an active state animates the pill
        self._started_at: float | None = None   # turn start, for elapsed ticker

    def watch_status(self, status: AgentStatus) -> None:
        if status in ACTIVE_STATES:
            self._frames.reset()
            if self._anim is None:
                self._anim = self.set_interval(0.12, self._tick,
                                               name="pill_anim")
        elif self._anim is not None:  # motion only while work is in flight
            self._anim.stop()
            self._anim = None
        self.refresh()

    def _tick(self) -> None:
        self.refresh()

    def begin(self) -> None:
        """Arm the elapsed-seconds ticker at turn start."""
        self._started_at = time.monotonic()
        self.refresh()

    def end(self) -> None:
        """Stop the elapsed-seconds ticker."""
        self._started_at = None
        self.refresh()

    def watch_task(self, _t: str) -> None:
        self.refresh()

    def watch_step(self, _n: int) -> None:
        self.refresh()

    # ---------------------------------------------------------------- render
    def render(self) -> Text:
        line = Text()
        color = T.STATUS_COLORS[self.status.value]
        if self.status in ACTIVE_STATES:
            glyph = f"{self._frames.next()}"
            line.append(f"[ {glyph} ", style=f"bold {color}")
            line.append(self.status.name, style=f"bold {color}")
            line.append("]", style=f"bold {color}")
        else:
            line.append(f" {label(self.status)} ", style=f"bold {color}")
        if self.task:
            shown = self.task if len(self.task) <= 60 else self.task[:57] + "…"
            line.append("  task: ", style=T.MUTED)
            line.append(shown, style=T.TEXT)
        if self.step:
            line.append(f"   · step {self.step}", style=T.FAINT)
        if self.status in ACTIVE_STATES and self._started_at is not None:
            elapsed = time.monotonic() - self._started_at
            line.append(f"   · {elapsed:.0f}s", style=T.FAINT)
        return line
