"""ToolCard: one tool execution as an expandable card.

Collapsed it is a single dense line — glyph, tool name, argument summary,
duration. Expanded (click / Enter when focused) it reveals detail: diffs for
edits (+/- colored), command output for bash, result previews otherwise.
"""
import json
import time
from typing import Any

from rich.console import Group
from rich.syntax import Syntax
from rich.text import Text
from textual.binding import Binding
from textual.containers import Vertical, VerticalScroll
from textual.widgets import Static

from coding_agent.ui import theme as T
from coding_agent.ui.animations import BRAILLE, Frames

DETAIL_LIMIT = 4000  # chars of expanded output shown


def _summarize_args(name: str, args: dict) -> str:
    """Short inline summary for the title row."""
    if name == "bash":
        return str(args.get("command", ""))
    if "path" in args:
        return str(args["path"])
    if name == "glob":
        return str(args.get("pattern", ""))
    if name == "grep":
        return f'"{args.get("pattern", "")}"' + (
            f" in {args['path']}" if args.get("path") else "")
    if name == "web_search":
        return f'"{args.get("query", "")}"'
    if name == "web_fetch":
        return str(args.get("url", ""))
    parts = []
    for k, v in list(args.items())[:2]:
        text = v if isinstance(v, str) else json.dumps(v, default=str)
        parts.append(f"{k}={text[:40]}")
    return " ".join(parts)


class ToolCard(Vertical, can_focus=True):
    DEFAULT_CSS = f"""
    ToolCard {{
        height: auto;
        margin: 0 0 0 1;
        padding: 0;
        border-left: thick {T.BORDER};
    }}
    ToolCard:hover {{ border-left: thick {T.CYAN}; }}
    ToolCard:focus {{ border-left: thick {T.CYAN}; background: {T.SURFACE_RAISED}; }}
    ToolCard.running {{ border-left: thick {T.AMBER}; }}
    ToolCard.failed {{ border-left: thick {T.RED}; }}
    ToolCard.declined {{ border-left: thick {T.AMBER}; }}
    ToolCard > #trow {{
        height: 1;
    }}
    ToolCard > #tdetail-wrap {{
        display: none;
        height: auto;
        max-height: 22;
        padding: 0 1;
        overflow-y: auto;
    }}
    ToolCard.expanded > #tdetail-wrap {{ display: block; }}
    """

    BINDINGS = [
        Binding("enter,space", "expand_toggle", "expand/collapse", show=False),
    ]

    def __init__(self, name: str, arguments: dict[str, Any], repeat: bool = False):
        super().__init__()
        self.tool_name = name
        self.args = arguments
        self.repeat = repeat
        self.duration_s: float | None = None
        self.result: dict[str, Any] | None = None
        self.ok: bool | None = None
        self._started_at = time.monotonic()
        self._live_timer = None

    # ------------------------------------------------------------- lifecycle

    def compose(self):
        yield Static("", id="trow")
        yield VerticalScroll(Static("", id="tdetail"), id="tdetail-wrap")

    def on_mount(self) -> None:
        # A fast tool_result can beat the mount: don't resurrect "running"
        # on a card that already finished.
        if self.ok is None and not self.repeat:
            self.add_class("running")
            self._live_timer = self.set_interval(0.2, self._live_tick)
        self._paint()

    def finish(self, result: dict[str, Any], ok: bool, duration_s: float) -> None:
        """Record the outcome. May arrive before compose() ran (fast turns);
        _paint no-ops until children exist, and on_mount repaints."""
        self.result = result
        self.ok = ok
        self.duration_s = duration_s
        self._stop_live()
        self.remove_class("running")
        if not ok:
            declined = bool(result.get("declined"))
            self.add_class("declined" if declined else "failed")
        self._paint()

    # ------------------------------------------------------------- live tick

    def _live_tick(self) -> None:
        """Repaint the title while running: spinner frame + elapsed seconds.
        Stops itself once the card is finished or unmounted."""
        if self.ok is not None or not self.has_class("running"):
            self._stop_live()
            return
        try:
            self.query_one("#trow", Static).update(self._title_line())
        except Exception:  # noqa: BLE001 - children gone during teardown
            self._stop_live()

    def _stop_live(self) -> None:
        timer = getattr(self, "_live_timer", None)
        if timer is not None:
            timer.stop()
            self._live_timer = None

    # ---------------------------------------------------------------- toggle

    def action_expand_toggle(self) -> None:
        self.toggle_class("expanded")

    def on_click(self) -> None:
        self.toggle_class("expanded")

    def _expanded(self) -> bool:
        return self.has_class("expanded")

    # --------------------------------------------------------------- painting

    def _paint(self) -> None:
        """Paint title + detail; no-op until compose() created the children
        (a fast tool_result can beat the mount), on_mount repaints."""
        if not self.is_mounted:
            return
        try:
            self.query_one("#trow", Static).update(self._title_line())
            detail = self.query_one("#tdetail", Static)
        except Exception:  # noqa: BLE001 - children not composed yet
            return
        body = self._detail_renderable()
        detail.update(body)
        wrap = self.query_one("#tdetail-wrap", VerticalScroll)
        if isinstance(body, Syntax):
            wrap.styles.height = min(22, str(body.code).count("\n") + 2)
        else:
            wrap.styles.height = None

    def _title_line(self) -> Text:
        line = Text()
        running = self.has_class("running")
        if running:
            if getattr(self, "_frames", None) is None:
                self._frames = Frames(BRAILLE)
            glyph, style = self._frames.next(), T.AMBER
        elif self.ok is False:
            declined = bool((self.result or {}).get("declined"))
            glyph, style = ("×", T.RED) if not declined else ("×", T.AMBER)
        else:
            glyph, style = ("↻", T.FAINT) if self.repeat else ("✓", T.GREEN)
        line.append(f" {glyph} ", style=f"bold {style}")
        line.append(self.tool_name, style=f"bold {T.CYAN}")
        line.append(f"({_summarize_args(self.tool_name, self.args)})", style=T.MUTED)

        bits = []
        if running and self._started_at is not None:
            bits.append(f"{time.monotonic() - self._started_at:.1f}s")
        elif self.duration_s is not None:
            bits.append(f"{self.duration_s:.1f}s")
        if self.ok is False and (self.result or {}).get("declined"):
            bits.append("declined")
        elif self.ok is False:
            bits.append("error")
        bits.append("▾" if self._expanded() else "▸")
        right = " ".join(bits)
        width = max(20, self.size.width or 60)
        pad = max(1, width - line.cell_len - len(right) - 2)
        line.append(" " * pad + right + " ", style=T.FAINT)
        return line

    def refresh_title(self) -> None:
        self._paint()

    # ---------------------------------------------------------------- detail

    def _detail_renderable(self):
        r = self.result or {}
        if "error" in r:
            return Text(f"error: {r['error']}", style=T.RED)
        if self.tool_name == "bash":
            body = Text()
            body.append(f"$ {self.args.get('command', '')}\n", style=f"bold {T.TEXT}")
            tail = r.get("stdout") or ""
            err = str(r.get("stderr") or "")[-1000:]
            code = r.get("returncode")
            if tail.strip():
                body.append_text(Text.from_ansi(str(tail)))
            if err.strip():
                body.append(err.rstrip() + "\n", style=T.RED)
            if code not in (None, 0):
                body.append(f"exit {code}", style=f"bold {T.RED}")
            if not tail.strip() and not err.strip():
                body.append("(no output)", style=T.FAINT)
            return body
        if self.tool_name == "edit":
            diff = "\n".join(
                ["- " + ln for ln in str(self.args.get("old_string", "")).splitlines()]
                + ["+ " + ln for ln in str(self.args.get("new_string", "")).splitlines()],
            )
            return Syntax(diff, "diff", theme="ansi_dark", word_wrap=True)
        if self.tool_name == "write":
            lines = str(self.args.get("content", "")).splitlines()
            shown = lines[:30]
            more = f"\n… ({len(lines)} lines total)" if len(lines) > 30 else ""
            diff = "\n".join(f"+ {ln}" for ln in shown) + more
            label = "[new file]" if r.get("created") else "(overwritten)"
            return Group(
                Text(f"{self.args.get('path', '?')} {label}\n", style=T.MUTED),
                Syntax(diff, "diff", theme="ansi_dark", word_wrap=True),
            )
        content = r.get("content")
        if self.tool_name == "read" and content is not None:
            return Text(str(content)[:DETAIL_LIMIT])
        if isinstance(r.get("results"), list):
            body = Text()
            for item in r["results"][:20]:
                body.append(f"· {str(item)[:200]}\n", style=T.TEXT)
            if r.get("truncated"):
                body.append("(truncated)", style=T.FAINT)
            return body
        if isinstance(r.get("items"), list):
            body = Text()
            for item in r["items"][:50]:
                body.append(str(item) + "\n")
            return body
        pretty = json.dumps(r, indent=2, default=str)[:DETAIL_LIMIT]
        return Syntax(pretty or "{}", "json", theme="ansi_dark", word_wrap=True)
