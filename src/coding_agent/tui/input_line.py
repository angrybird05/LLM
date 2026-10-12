"""REPL input: multi-line entry + persistent command history.

On an interactive terminal this uses a prompt_toolkit session:

- Enter submits. A trailing ``\\`` continues onto the next line (the
  backslash itself is dropped); Alt+Enter always opens a new line too.
- Pasted text keeps its newlines literally (bracketed paste), so a multi-line
  block can be edited before submitting with one Enter.
- Up/Down walk history persisted at ``~/.agent/history`` (FileHistory format),
  shared across launches — including multi-line entries replayed intact.

When stdin is not a TTY (piped scripts and smoke tests) or prompt_toolkit is
missing, falls back to plain ``console.input`` lines with the same
trailing-backslash rule, so scripted sessions behave identically.
"""
import sys
from pathlib import Path

HISTORY_PATH = Path.home() / ".agent" / "history"
PROMPT_HTML = "<ansicyan><b>› </b></ansicyan>"


def _make_history(path: Path):
    """FileHistory whose entries are readable immediately.

    Stock FileHistory parses its file asynchronously inside the running app;
    a keystroke arriving in the first tick (fast typists, scripted input)
    could outrun it and find an empty ↑. Pre-loading the small text file
    synchronously closes that race."""
    from prompt_toolkit.history import FileHistory

    class _SyncFileHistory(FileHistory):
        def __init__(self, filename):
            super().__init__(filename)
            try:
                self._loaded_strings = list(self.load_history_strings())
                self._loaded = True
            except OSError:
                pass  # unreadable file -> empty history, never a crash

    return _SyncFileHistory(str(path))


def _key_bindings():
    from prompt_toolkit.key_binding import KeyBindings

    kb = KeyBindings()

    @kb.add("enter")
    def _(event):  # pragma: no cover - exercised via pipe-input tests
        buf = event.current_buffer
        doc = buf.document
        # A backslash right before the cursor means "continue on the next
        # line": remove it and open a new line instead of submitting.
        if doc.text_after_cursor == "" and doc.char_before_cursor == "\\":
            buf.delete_before_cursor(1)
            buf.insert_text("\n")
            return
        buf.validate_and_handle()

    @kb.add("escape", "enter")
    def _(event):  # pragma: no cover - Alt+Enter always continues
        event.current_buffer.insert_text("\n")

    # multiline=True makes Up/Down move the cursor between lines; keep that
    # inside a multi-line draft, but recall history when the cursor sits on
    # the outer edge (the common one-liner + up-arrow case).
    @kb.add("up")
    def _(event):
        buf = event.current_buffer
        if buf.document.on_first_line:
            buf.history_backward()
        else:
            buf.cursor_up()

    @kb.add("down")
    def _(event):
        buf = event.current_buffer
        if buf.document.on_last_line:
            buf.history_forward()
        else:
            buf.cursor_down()

    return kb


class LineReader:
    """Reads one logical command per call, multi-line aware."""

    def __init__(self, history_path: Path | None = None, *,
                 use_prompt_toolkit: bool = True, input=None, output=None):
        self.history_path = history_path or HISTORY_PATH
        self.use_prompt_toolkit = use_prompt_toolkit
        # input/output overrides are for tests (pipe input); providing them
        # forces the interactive path regardless of TTY detection.
        self._input = input
        self._output = output
        self._degraded = False  # set when prompt_toolkit can't drive the terminal
        self._session = None
        self._history = None
        # Whether the last successful read() already showed the input on
        # screen (interactive editor). Callers use this to avoid echoing the
        # message twice; the plain fallback shows nothing, so callers echo.
        self.rendered_prompt = False

    @property
    def interactive(self) -> bool:
        if self._input is not None:
            return True
        try:
            return sys.stdin.isatty()
        except Exception:  # e.g. stdin closed oddly
            return False

    def read(self, console) -> str | None:
        """Return one command (possibly multi-line), or None on EOF / Ctrl+C."""
        if not self._degraded and self.use_prompt_toolkit and self.interactive:
            try:
                line = self._read_interactive()
                if line is not None:
                    self.rendered_prompt = True
                return line
            except (EOFError, KeyboardInterrupt):
                return None
            except Exception:
                # A terminal prompt_toolkit can't drive (e.g. mintty without
                # winpty): degrade permanently to plain lines, never crash.
                self._degraded = True
                console.print(
                    "[dim]terminal not supported by the fancy editor — "
                    "using simple input (no ↑/↓ history)[/]")
        self.rendered_prompt = False
        return self._read_fallback(console)

    # ------------------------------------------------------------------ paths

    def _read_interactive(self) -> str | None:
        try:
            return self._get_session().prompt(PROMPT_HTML).strip()
        except (EOFError, KeyboardInterrupt):
            return None

    def _read_fallback(self, console) -> str | None:
        # No prompt glyphs here: piped stdin doesn't echo typed text, so the
        # caller's echo_user() is the single place the message gets shown.
        lines = []
        try:
            lines.append(console.input(""))
            while lines[-1].endswith("\\"):
                lines[-1] = lines[-1][:-1]
                lines.append(console.input(""))
        except (EOFError, KeyboardInterrupt):
            if not lines or (len(lines) == 1 and not lines[0]):
                return None
            # Interrupt during a continuation: keep what was typed so far.
        return "\n".join(lines).strip()

    # ---------------------------------------------------------------- session

    def _get_session(self):
        if self._session is None:
            from prompt_toolkit.shortcuts import PromptSession
            from prompt_toolkit.styles import Style

            try:
                self.history_path.parent.mkdir(parents=True, exist_ok=True)
                self._history = _make_history(self.history_path)
            except OSError:
                self._history = None  # unwritable home — history just won't persist
            kwargs = {}
            if self._input is not None:  # test seam: headless pipe-driven input
                from prompt_toolkit.output import DummyOutput

                kwargs = {"input": self._input, "output": self._output or DummyOutput()}
            self._session = PromptSession(
                history=self._history,
                multiline=True,
                key_bindings=_key_bindings(),
                style=Style.from_dict({"": "ansicyan bold"}),
                **kwargs,
            )
        return self._session
