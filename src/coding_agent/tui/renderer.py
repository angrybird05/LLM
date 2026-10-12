"""Rendering helpers for the terminal REPL (rich-based)."""
import re
import sys
import threading
import time
from pathlib import Path

from rich.console import Console, Group
from rich.live import Live
from rich.markdown import Markdown
from rich.markup import escape
from rich.panel import Panel
from rich.syntax import Syntax
from rich.text import Text

from coding_agent.config import settings


def _force_utf8_streams():
    """Windows consoles/pipes default to cp1252; our UI glyphs need UTF-8."""
    for stream in (sys.stdout, sys.stderr):
        if stream is not None and hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass


_force_utf8_streams()
console = Console()

MAX_TOOL_OUTPUT_LINES = 12


def _tilde(path: str, max_len: int = 44) -> str:
    """Tilde-abbreviate home; collapse long middles so the banner stays one line."""
    prefix = ""
    home = str(Path.home())
    if path.startswith(home):
        path = path[len(home):]  # keeps its leading separator
        prefix = "~"
    if len(prefix) + len(path) <= max_len:
        return prefix + path
    sep = "\\" if "\\" in path else "/"
    segments = [s for s in path.split(sep) if s]
    if len(segments) < 2:
        return prefix + path
    return prefix + sep + segments[0] + sep + "…" + sep + segments[-1]


def banner(version: str, model: str, provider: str, mode: str, workspace: str) -> None:
    console.print()
    console.print(Panel(
        f"[bold cyan]◆[/] [bold]coding-agent[/] [dim]v{version}[/]",
        border_style="cyan",
        subtitle=f"{provider} · {model} · {mode}",
        subtitle_align="left",
    ))
    ws = _tilde(workspace, max_len=30)
    console.print(f"  [dim]{ws}[/]")
    console.print(
        "  [dim]/help commands · \\ continues a line · ↑ history · ctrl+c interrupts[/]"
    )
    console.print()


def echo_user(text: str) -> None:
    """Re-render the user's message so the transcript reads like a dialogue."""
    lines = text.splitlines() or [""]
    console.print(Panel(
        f"[bold cyan]›[/] [bold]{escape(lines[0])}[/]",
        border_style="cyan",
        subtitle=" ".join(lines[1:]) if len(lines) > 1 else "",
        subtitle_align="left",
    ))


class Spinner:
    """'thinking… Ns' indicator shown only when the wait is real.

    Appears after DELAY of silence (fast tool-to-tool hops never flicker),
    ticks elapsed seconds, and is skipped entirely when stdout is not a
    terminal (pipes/smokes keep clean output). The start/stop churn around
    tool events is race-proofed: every cycle carries a generation number and
    its own stop Event under a lock, so a sleeper waking after a quick
    stop→start can never resurrect its spinner next to the fresh one.
    """

    DELAY = 0.35

    def __init__(self, label: str = "thinking"):
        self.label = label
        self._lock = threading.Lock()
        self._generation = 0
        self._stop_event: threading.Event | None = None
        self._thread: threading.Thread | None = None
        self._status = None

    def maybe_start(self) -> None:
        with self._lock:
            if self._thread is not None or not console.is_interactive:
                return
            self._generation += 1
            stop = threading.Event()
            self._stop_event = stop
            gen = self._generation
            self._thread = threading.Thread(
                target=self._run, args=(gen, stop), daemon=True)
            self._thread.start()

    def stop(self) -> None:
        with self._lock:
            self._generation += 1          # any sleeper waking now is stale
            ev, self._stop_event = self._stop_event, None
            status, self._status = self._status, None
            self._thread = None
        if ev is not None:
            ev.set()
        if status is not None:
            status.stop()

    def _run(self, gen: int, stop: threading.Event) -> None:
        if stop.wait(self.DELAY):
            return                          # stopped before the delay elapsed
        with self._lock:
            if gen != self._generation or stop.is_set():
                return                      # a newer cycle replaced this one
            status = console.status(f"[dim]{self.label}…[/]", spinner="dots")
            self._status = status
            status.start()
        t0 = time.monotonic()
        while gen == self._generation and not stop.is_set():
            status.update(f"[dim]{self.label}… {int(time.monotonic() - t0)}s[/]")
            time.sleep(1)


def render_permission_request(req) -> None:
    """Show what a tool wants to do before the user decides."""
    title = f"⚠ permission · {req.tool_name}"
    if req.danger_reason:
        title += f" · [red]{req.danger_reason}[/]"
    body = Group(
        Text(req.preview, justify="left"),
        Text("\nthis action will run on your machine", style="dim"),
    )
    console.print(Panel(body, title=title, border_style="red", expand=False))


class StreamingText:
    """Renders one streamed LLM message segment exactly once.

    While text streams, interactive terminals show only an ephemeral preview
    of the last few raw lines (a transient Live region — it leaves nothing
    behind when stopped). finish() then prints the complete rendered message
    ONCE, append-only into the scrollback. Two earlier designs both duplicated
    content and are gone:

    * a Live region redrawing a GROWING Markdown document re-prints the whole
      text on every refresh — once it outgrows the terminal, overflow scrolls
      and the same paragraphs visibly stack up;
    * the REPL reuses one StreamingText across the segments between tool
      calls, so an unreset buffer re-printed earlier narration inside every
      later segment. finish() therefore resets the buffer (and is idempotent).

    Whitespace-only segments still print nothing, and non-interactive consoles
    skip the live region entirely (pipes get exactly one paint).
    """

    PREVIEW_LINES = 6

    def __init__(self):
        self._parts: list[str] = []
        self._live: Live | None = None
        self._last_update = 0.0

    def start(self):
        """No-op kept for call-site symmetry; rendering starts in append()."""

    def append(self, text: str):
        self._parts.append(text)
        if not "".join(self._parts).strip() or not console.is_interactive:
            return
        if self._live is None:
            self._live = Live(console=console, refresh_per_second=10, transient=True)
            self._live.start()
            self._paint(force=True)
            return
        self._paint()

    def finish(self):
        body = "".join(self._parts)
        self._parts = []  # segment-scoped: later segments must not replay this one
        if self._live:
            self._live.update(Text(""))  # drop any pending preview frame
            self._live.stop()
            self._live = None
        if body.strip():
            console.print(Markdown(body))

    def _paint(self, force: bool = False):
        live = self._live
        if live is None:
            return
        now = time.monotonic()
        if not force and now - self._last_update <= 0.08:
            return
        self._last_update = now
        lines = "".join(self._parts).splitlines() or [""]
        tail = Text("\n".join(lines[-self.PREVIEW_LINES:]), style="dim")
        live.update(
            Group(Text("…", style="dim"), tail)
            if len(lines) > self.PREVIEW_LINES else tail
        )


def tool_start(name: str, arguments: dict) -> None:
    """One-line summary of a tool call, Claude Code style."""
    brief = _brief_args(name, arguments)
    console.print(f"  [magenta]●[/] [bold]{name}[/][dim]({brief})[/]")


def tool_repeat(name: str, arguments: dict) -> None:
    """A call the model already made this turn — keep the record, lose the
    visual weight so re-reads don't read as fresh activity."""
    brief = _brief_args(name, arguments)
    console.print(f"  [dim]↻ {name}({brief})[/]")


def tool_result(result: dict, arguments: dict | None = None) -> None:
    if result.get("declined"):
        console.print("    [yellow]⏎ user declined[/]")
        return
    if "error" in result:
        console.print(f"    [red]⏎ error:[/] {result['error'][:300]}")
        return

    # Diffs for file-changing tools
    if arguments and "old_string" in arguments:
        from coding_agent.core.permissions import preview_diff

        diff = Syntax(
            preview_diff(arguments.get("old_string", ""), arguments.get("new_string", "")),
            "diff", theme="ansi_dark", word_wrap=True,
        )
        console.print(_indent(Group(diff)))
        return

    body = _summarize(result)
    if body:
        console.print(f"    [dim]{body}[/]")


def _indent(renderable) -> Group:
    return Group(Text("    ", style="dim"), renderable)


def _brief_args(name: str, args: dict) -> str:
    if name == "bash":
        return str(args.get("command", ""))[:120]
    if name in ("read", "write", "edit"):
        parts = [str(args.get("path", ""))]
        if name == "read":
            if args.get("offset"):
                parts.append(f"+{args['offset']}")
            if args.get("limit"):
                parts.append(f"{args['limit']} lines")
        return ", ".join(parts)
    if name in ("glob", "grep"):
        return f"{args.get('pattern', '')} in {args.get('path', '.')}"
    if name == "web_fetch":
        return str(args.get("url", ""))
    if name == "web_search":
        return str(args.get("query", ""))
    return str(args)[:120]


def _summarize(result: dict) -> str:
    lines: list[str] = []
    if "stdout" in result or "stderr" in result:
        out = (result.get("stdout") or "").rstrip()
        err = (result.get("stderr") or "").rstrip()
        code = result.get("returncode")
        src = out or err or "(no output)"
        shown = "\n".join(src.splitlines()[:MAX_TOOL_OUTPUT_LINES])
        if len(src.splitlines()) > MAX_TOOL_OUTPUT_LINES:
            shown += "\n…"
        lines.append(shown)
        if code not in (None, 0):
            lines.append(f"exit code {code}")
    elif "content" in result:  # read
        shown_lines = result.get("shown_lines")
        lines.append(f"({shown_lines} lines)" if shown_lines else "")
    elif "matches" in result:  # glob
        lines.append(f"{result.get('count', 0)} matches")
    elif "results" in result:  # grep / web_search
        n = len(result["results"])
        lines.append(f"{n} matches" if n else "(none)")
    elif "items" in result:  # list
        lines.append(f"{len(result['items'])} entries")
    elif result.get("success") is True:
        lines.append("done")
    return "\n".join(x for x in lines if x)


def status_line(usage: dict, iterations: int, elapsed: float,
                context_tokens: int | None = None) -> None:
    parts = [
        f"↑{usage.get('input', 0):,} ↓{usage.get('output', 0):,} tokens",
        f"{iterations} step{'s' if iterations != 1 else ''}",
        f"{elapsed:.1f}s",
    ]
    base = "  [dim]" + " · ".join(parts)
    if context_tokens is None:
        console.print(base + "[/]")
        return
    pct = min(int(100 * context_tokens / max(settings.CONTEXT_TOKEN_LIMIT, 1)), 100)
    # Auto-compaction kicks in at COMPACT_THRESHOLD — flag the approach.
    style = ("red" if pct >= int(100 * settings.COMPACT_THRESHOLD)
             else "yellow" if pct >= 50 else "dim")
    # Claude Code style: ctx percentage with color-coded urgency
    ctx_display = f"[{style}]ctx {pct}%[/]"
    console.print(f"{base} · {ctx_display}")


def info(message: str) -> None:
    console.print(f"[dim]{message}[/]")


def warn(message: str) -> None:
    console.print(f"[yellow]{message}[/]")


def provider_error(kind: str, detail: str) -> None:
    """One clean, actionable card for a failed LLM request.

    Provider exceptions arrive as a wall of nested JSON (often with the same
    failure repeated across fallback attempts inside); this shows a single
    headline, one extracted reason line, and what to do — never the raw dump.
    """
    m = re.search(r"['\"]?message['\"]?:\s*['\"]([^'\"]+)", detail)
    reason = m.group(1)[:220] if m else detail[:220]
    console.print()
    if kind == "credits":
        console.print("[bold red]⚠ provider out of credits[/] [dim]— turn stopped[/]")
        console.print(f"  [dim]{reason}[/]")
        console.print(
            "  [dim]top up at openrouter.ai/settings/credits · "
            "or /model <cheaper-model> · /compact frees context[/]"
        )
    elif kind == "too_long":
        console.print("[yellow]⚠ prompt too large for the provider[/]")
        console.print(f"  [dim]{reason}[/]")
        console.print("  [dim]/compact summarizes older history · /clear starts fresh[/]")
    else:
        console.print(f"[red]error:[/] {reason}")
    console.print()
