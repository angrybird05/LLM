"""AiCore: the center panel - the conversation itself.

Blocks: user messages (cyan left bar), assistant text (streamed into a block
with throttled repaints), tool cards, and one-line notes. The view follows the
bottom only while the user is actually there - scrolling up to read freezes
the position; scrolling back down re-arms follow mode.
"""
import time

from rich.markdown import Markdown as RichMarkdown
from rich.markup import escape as rich_escape
from rich.text import Text
from textual.containers import VerticalScroll
from textual.widgets import Static

from coding_agent.ui.widgets.tool_card import ToolCard

FLUSH_INTERVAL_S = 0.10


def _plain(text: str) -> str:
    """Escape rich markup so arbitrary text renders literally."""
    return rich_escape(str(text))


def _flush_interval(text_len: int) -> float:
    """Repaint cadence scales with message size: markdown parsing is O(n),
    so long streams repaint less often instead of burning the loop."""
    if text_len > 60_000:
        return 0.50
    if text_len > 20_000:
        return 0.25
    return FLUSH_INTERVAL_S


class UserBlock(Static):
    DEFAULT_CSS = """
    UserBlock {
        height: auto;
        margin-top: 1;
        padding: 0 1;
        border-left: thick #22d3ee;
        color: #c7d5e8;
    }
    """


class AssistantBlock(Static):
    DEFAULT_CSS = """
    AssistantBlock {
        height: auto;
        margin-left: 1;
        color: #c7d5e8;
    }
    AssistantBlock.streaming {
        opacity: 85%;
        border-left: thick #22d3ee;
    }
    """

    def __init__(self):
        super().__init__("")
        self._text = ""
        self._painted: str | None = None  # skip re-parsing unchanged text

    def paint(self, tokens: str) -> None:
        self._text = tokens
        if tokens == self._painted:
            return  # identical repaint: skip the full markdown re-parse
        self._painted = tokens
        if tokens.strip():
            shown = (tokens + "\n\n▍" if self.has_class("streaming")
                     else tokens)
            self.update(RichMarkdown(shown))

    def finish(self, final_text: str) -> str:
        """Freeze the block; returns the final full text. Forces a repaint so
        the streaming caret never survives into the frozen block."""
        self.remove_class("streaming")
        self._painted = None
        self.paint(final_text)
        return final_text


class NoteBlock(Static):
    DEFAULT_CSS = """
    NoteBlock {
        height: auto;
        margin-left: 1;
    }
    """


class SystemLine(Static):
    DEFAULT_CSS = """
    SystemLine {
        height: auto;
        margin-left: 1;
        color: #5d7290;
    }
    """


class AiCore(VerticalScroll):
    """Scrollable transcript container: mounts typed blocks, follows bottom."""

    DEFAULT_CSS = """
    AiCore {
        height: 1fr;
        padding: 0 1;
        overflow-y: auto;
    }
    """

    def __init__(self):
        super().__init__()
        self._stream_block: AssistantBlock | None = None
        self._last_paint = 0.0
        self._deferred = ""
        self._flush_scheduled = False

    # ------------------------------------------------------------ scrolling

    def _at_bottom(self) -> bool:
        # Nothing overflows yet -> "at bottom", so the view pins itself as
        # soon as content starts to grow.
        return self.max_scroll_y == 0 or self.scroll_y >= self.max_scroll_y - 2

    def _mount(self, widget) -> None:
        at_bottom = self._at_bottom()
        self.mount(widget)
        if at_bottom:
            self.call_after_refresh(self._follow_scroll)

    def _follow_scroll(self) -> None:
        """Pin the newest content in view - but only while the user is
        actually at the bottom. Scrolling up to read freezes the position;
        scrolling back down re-arms follow mode (re-checked on every event)."""
        if self._at_bottom():
            self.scroll_end(animate=False)

    # ---------------------------------------------------------------- blocks

    def post_user(self, text: str) -> UserBlock:
        body = Text()
        body.append("› ", style="bold #22d3ee")
        body.append(_plain(text))
        block = UserBlock(body)
        self._mount(block)
        return block

    def begin_stream(self) -> AssistantBlock:
        self.end_stream()
        block = AssistantBlock()
        block.add_class("streaming")
        self._mount(block)
        self._stream_block = block
        return block

    def feed_stream(self, chunk: str) -> None:
        """Buffer a stream delta; repaint at an adaptive interval (longer for
        bigger messages - markdown re-parsing is O(n))."""
        if self._stream_block is None:
            self.begin_stream()
        now = time.monotonic()
        pending = self._stream_block._text + chunk
        interval = _flush_interval(len(pending))
        if now - self._last_paint >= interval:
            self._last_paint = now
            self._stream_block.paint(pending)
            self._follow_scroll()
        else:
            # stash for the next flush via a one-shot timer on this widget
            self._deferred += chunk
            if not self._flush_scheduled:
                self._flush_scheduled = True
                self.set_timer(interval, self._do_deferred_flush)

    def _do_deferred_flush(self) -> None:
        self._flush_scheduled = False
        text = (self._stream_block._text if self._stream_block else "") \
            + self._deferred
        self._deferred = ""
        if self._stream_block is not None:
            self._stream_block.paint(text)
            self._follow_scroll()

    def end_stream(self, final_text: str | None = None) -> str:
        """Freeze the streaming block; returns the accumulated final text."""
        if self._stream_block is None:
            return ""
        text = self._stream_block._text
        if self._deferred:
            text += self._deferred
            self._deferred = ""
        if final_text is not None and len(final_text) >= len(text):
            text = final_text  # authoritative tail from turn_done
        result = self._stream_block.finish(text)
        self._stream_block = None
        return result

    def note(self, level: str, text: str) -> None:
        glyphs = {"info": "◆", "success": "✔", "warn": "⚠", "error": "✖"}
        colors = {"info": "#60a5fa", "success": "#34d399",
                  "warn": "#fbbf24", "error": "#f87171"}
        line = Text()
        level_glyph = glyphs.get(level, "*")
        line.append(" ")
        line.append(level_glyph, style=f"bold {colors.get(level, '#c7d5e8')}")
        line.append(" ")
        line.append(_plain(text))
        self._mount(NoteBlock(line))

    def meta(self, text: str) -> SystemLine:
        line = SystemLine(Text("  " + _plain(text), style="#5d7290"))
        self._mount(line)
        return line

    def system_divider(self, text: str) -> SystemLine:
        """Dim rule used as a section header (e.g. an opened chat's identity
        line), styled like the per-turn summary dividers."""
        line = Text()
        line.append("  ── ", style="#31435e")
        line.append(_plain(text), style="#5d7290")
        line.append(" ──", style="#31435e")
        block = SystemLine(line)
        self._mount(block)
        return block

    def turn_summary(self, steps: int, tok_in: int | None,
                     tok_out: int | None, elapsed_s: float) -> None:
        """Dim divider after each finished turn: steps · tokens · seconds."""
        bits = [f"{max(steps, 1)} step{'s' if steps != 1 else ''}"]
        if tok_in:
            bits.append(f"↑{tok_in:,}")
        if tok_out:
            bits.append(f"↓{tok_out:,}")
        bits.append(f"{elapsed_s:.1f}s")
        line = Text()
        line.append("  ── ", style="#31435e")
        line.append(" · ".join(bits), style="#31435e")
        self._mount(SystemLine(line))

    def add_tool_card(self, name: str, arguments: dict,
                      repeat: bool = False) -> ToolCard:
        card = ToolCard(name, arguments, repeat=repeat)
        self._mount(card)
        return card

    def add_finished_assistant(self, text: str) -> AssistantBlock:
        """Mount an already-complete assistant message (history restore)."""
        block = AssistantBlock()
        block.paint(text)
        block.finish(text)
        self._mount(block)
        return block

    def clear_all(self) -> None:
        self._stream_block = None
        self._deferred = ""
        self._flush_scheduled = False
        for child in list(self.children):
            child.remove()
