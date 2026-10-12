"""CommandDock: the bottom command center - mode line, prompt, suggestions.

The TextArea submits on Enter (Alt+Enter / trailing backslash insert a
newline instead), recalls input history on arrow keys, and shows a filtered
slash-command dropdown while the first line starts with "/". Tab moves focus
between panels; Esc hides the dropdown first.
"""
import json
from pathlib import Path

from rich.text import Text
from textual.binding import Binding
from textual.containers import Vertical
from textual.widgets import OptionList, Static, TextArea
from textual.widgets.option_list import Option

from coding_agent.ui import theme as T

HISTORY_FILE = Path.home() / ".agent" / "tui_history.json"

# Idle hint bar text; set_busy() swaps to a queueing hint while a turn runs.
HINTS = ("enter send   alt+enter newline   f4 sessions   ctrl+p palette   "
         "ctrl+c interrupt   ctrl+l clear view   tab panels   esc close")


class PromptArea(TextArea):
    """TextArea tuned for chat: Enter submits via the app action."""

    BINDINGS = [
        Binding("enter", "app.submit_prompt", "send", show=False, priority=True),
        Binding("alt+enter", "new_line", "newline", show=False),
        Binding("ctrl+l", "app.clear_view", "clear view", show=False, priority=True),
        Binding("tab", "app.focus_cycle", "next panel", show=False, priority=True),
        Binding("shift+tab", "app.focus_cycle_back", "prev panel",
                show=False, priority=True),
        Binding("up", "hist_or_up", "history/cursor up", show=False),
        Binding("down", "hist_or_down", "history/cursor down", show=False),
    ]

    class Submitted(TextArea.Changed):
        pass

    # ------------------------------------------------- history / navigation

    def _dock(self):
        return self.parent

    def action_hist_or_up(self) -> None:
        dock = self._dock()
        if dock is not None and dock.suggestions_open and dock.move_suggestion(-1):
            return
        if self.cursor_location[0] == 0 and dock is not None:
            if dock.history_prev(self):
                return
        self.action_cursor_up()

    def action_hist_or_down(self) -> None:
        dock = self._dock()
        if dock is not None and dock.suggestions_open and dock.move_suggestion(1):
            return
        last_row = self.document.line_count - 1
        if self.cursor_location[0] == last_row and dock is not None:
            if dock.history_next(self):
                return
        self.action_cursor_down()

    def action_new_line(self) -> None:
        self.insert("\n")


class CommandDock(Vertical):
    DEFAULT_CSS = """
    CommandDock {
        dock: bottom;
        height: auto;
        max-height: 40%;
        border-top: heavy #1a2942;
        background: #0b1220;
        padding: 0 1 1 1;
    }
    #modeline {
        height: 1;
        color: #5d7290;
    }
    #suggestions {
        display: none;
        height: auto;
        max-height: 12;
        margin-bottom: 0;
        background: #101a2c;
        border: round #1a2942;
    }
    #suggestions.visible { display: block; }
    #prompt {
        height: 3;
        border: round #22d3ee;
        background: #060a12;
    }
    #prompt:focus {
        border: round #a78bfa;
    }
    CommandDock.busy #prompt {
        border: round #fbbf24;
    }
    #hints {
        height: 1;
        color: #31435e;
    }
    """

    def __init__(self, commands: list[tuple[str, str]]):
        super().__init__()
        self.commands = commands  # [(name, description)]
        self.history: list[str] = []
        self._hist_pos: int | None = None
        self._draft: str = ""
        self._suggestions_open = False
        self._busy_hints: str | None = None

    def compose(self):
        yield Static("", id="modeline")
        yield OptionList(id="suggestions")
        yield PromptArea("", id="prompt")
        yield Static("", id="hints")

    # ---------------------------------------------------------------- history

    def load_history(self) -> None:
        try:
            self.history = json.loads(HISTORY_FILE.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self.history = []

    def save_history(self) -> None:
        try:
            HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
            HISTORY_FILE.write_text(
                json.dumps(self.history[-200:]), encoding="utf-8"
            )
        except OSError:
            pass

    def remember(self, text: str) -> None:
        text = text.strip()
        if not text:
            return
        if self.history and self.history[-1] == text:
            return
        self.history.append(text)
        self.save_history()

    # ------------------------------------------------------- history recall

    def history_prev(self, area) -> bool:
        """Load the previous entry into the prompt; True when handled."""
        if not self.history:
            return False
        if self._hist_pos is None:
            self._draft = area.text
            self._hist_pos = len(self.history) - 1
        elif self._hist_pos > 0:
            self._hist_pos -= 1
        else:
            return True  # already at the oldest entry
        self._load_hist_entry(area)
        return True

    def history_next(self, area) -> bool:
        """Move forward through history / restore the draft; True when handled."""
        if self._hist_pos is None:
            return False
        if self._hist_pos < len(self.history) - 1:
            self._hist_pos += 1
            self._load_hist_entry(area)
        else:
            self._hist_pos = None
            area.load_text(self._draft)
            area.move_cursor(area.document.end)
            self.hide_suggestions()
        return True

    def _load_hist_entry(self, area) -> None:
        pos = self._hist_pos
        if pos is None:
            return
        area.load_text(self.history[pos])
        area.move_cursor(area.document.end)
        self.hide_suggestions()

    # ------------------------------------------------------- event plumbing

    def on_text_area_changed(self, event) -> None:
        """Any prompt edit refreshes the slash-command dropdown. (This wiring
        was missing entirely - update_suggestions existed but nothing called
        it, so the '/' menu never opened while typing.)"""
        try:
            self.update_suggestions()
        except Exception:  # noqa: BLE001 - dropdown is cosmetic, never fatal
            pass

    # ------------------------------------------------------------ suggestions

    def update_suggestions(self) -> None:
        """Show filtered slash-command options while line 1 starts with /.
        A bare '/' lists every command; beyond that prefix matches rank first
        and substring matches follow ('/ctx' still finds /context)."""
        box = self.query_one("#suggestions", OptionList)
        area = self.query_one("#prompt", PromptArea)
        first_line = area.text.splitlines()[0] if area.text else ""
        if first_line.startswith("/") and "\n" not in area.text.strip():
            query = first_line.lower()
            if query == "/":
                matches = list(self.commands)
            else:
                body = query[1:]
                prefix = [cd for cd in self.commands
                          if cd[0].startswith(query)]
                seen = {id(cd) for cd in prefix}
                contains = [cd for cd in self.commands
                            if id(cd) not in seen and body in cd[0][1:]]
                matches = prefix + contains
            if matches:
                box.clear_options()
                for name, desc in matches:
                    label = Text()
                    label.append(f" {name:<12}", style=f"bold {T.CYAN}")
                    label.append(f" {desc}", style=T.MUTED)
                    box.add_option(Option(label))
                box.add_class("visible")
                box.highlighted = 0
                self._suggestions_open = True
                return
        self.hide_suggestions()

    def hide_suggestions(self) -> None:
        box = self.query_one("#suggestions", OptionList)
        box.remove_class("visible")
        self._suggestions_open = False

    @property
    def suggestions_open(self) -> bool:
        return self._suggestions_open

    def should_complete_on_enter(self) -> bool:
        """Enter completes the highlighted suggestion ONLY while the typed
        first token is still an incomplete command prefix ('/mo' -> '/model').
        Once the token IS a full command name, Enter must submit it."""
        if not self._suggestions_open:
            return False
        area = self.query_one("#prompt", PromptArea)
        line = area.text.split("\n")[0]
        if " " in line or "\n" in area.text:
            return False  # arguments present -> user is done, let Enter send
        token = line.lower()
        return any(name != token and name.startswith(token)
                   for name, _desc in self.commands)

    def apply_selected_suggestion(self) -> bool:
        """Complete with the highlighted option; returns True if one applied."""
        box = self.query_one("#suggestions", OptionList)
        area = self.query_one("#prompt", PromptArea)
        idx = box.highlighted
        options = [o for o in self.commands
                   if o[0].startswith(area.text.split("\n")[0].lower())]
        if not self._suggestions_open or idx is None or idx >= len(options):
            return False
        name = options[idx][0]
        area.load_text(name + " ")
        area.move_cursor(area.document.end)
        self.hide_suggestions()
        return True

    def move_suggestion(self, delta: int) -> bool:
        if not self._suggestions_open:
            return False
        box = self.query_one("#suggestions", OptionList)
        n = box.option_count
        if not n:
            return False
        box.highlighted = ((box.highlighted or 0) + delta) % n
        return True

    # -------------------------------------------------------------- mode line

    def set_mode_line(self, parts: list[tuple[str, str]]) -> None:
        """Render label/value pairs: [('MODE', 'default'), ('PLAN', 'off')]."""
        line = Text()
        for i, (label, value) in enumerate(parts):
            if i:
                line.append("  |  ", style="#31435e")
            line.append(label + " ", style="bold #5d7290")
            line.append(value, style=value_style(value))
        self.query_one("#modeline", Static).update(line)

    # ------------------------------------------------------------- hints row

    def set_hints(self, hints: str) -> None:
        self.query_one("#hints", Static).update(Text(" " + hints, style="#31435e"))

    # ------------------------------------------------------------- busy chrome

    def set_busy(self, busy: bool) -> None:
        """While the agent works: amber prompt border + queueing hint swap."""
        try:
            if busy:
                self.add_class("busy")
                self.set_hints(
                    "agent working - type + enter to queue · ctrl+c interrupt")
            else:
                self.remove_class("busy")
                self.set_hints(HINTS)
        except Exception:  # noqa: BLE001 - not mounted yet / teardown
            pass
        self.refresh()


def value_style(value: str) -> str:
    low = value.lower()
    if low in ("default",):
        return "#fbbf24"
    if low in ("auto-edit", "auto_edit"):
        return "#22d3ee"
    if low in ("full-auto", "full_auto"):
        return "#f87171"
    if low == "on":
        return "#a78bfa"
    return "#c7d5e8"
