"""Overlays: permission approvals, diff viewer, help, session picker, confirm.

Every modal is a ModalScreen dismissed with Esc; the permission modal also
answers y/n/a and is resolved by the controller when a turn is cancelled.
"""
from datetime import UTC

from rich.syntax import Syntax
from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Static

from coding_agent.ui import theme as T


def relative_time(dt) -> str:
    """Compact age string: 'now', '5m', '2h', '3d', else a short date.
    Naive datetimes are treated as UTC (SQLite CURRENT_TIMESTAMP)."""
    from datetime import datetime

    if dt is None:
        return "?"
    try:
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        delta = datetime.now(UTC) - dt
        s = int(delta.total_seconds())
        if s < 60:
            return "now"
        if s < 3600:
            return f"{s // 60}m"
        if s < 86400:
            return f"{s // 3600}h"
        if s < 7 * 86400:
            return f"{s // 86400}d"
        return dt.strftime("%b %d")
    except (TypeError, ValueError, OverflowError):
        return "?"


class Overlay(ModalScreen):
    """Shared chrome for all overlays."""

    DEFAULT_CSS = """
    Overlay {
        align: center middle;
        background: #060a12d0;
    }
    .box {
        width: 80%;
        max-width: 90;
        height: auto;
        max-height: 85%;
        border: round #22d3ee;
        background: #0b1220;
        padding: 1 2;
    }
    .box-title {
        text-style: bold;
        color: #22d3ee;
        margin-bottom: 1;
    }
    .btnrow {
        height: auto;
        margin-top: 1;
    }
    .btnrow Button { margin-right: 1; }
    """

    BINDINGS = [Binding("escape", "close", "close", show=False, priority=True)]

    def action_close(self) -> None:
        self.dismiss(None)


class ConfirmModal(Overlay):
    """Generic y/N question. dismiss(True/False).
    Keyboard-first: y/enter confirm, n cancels - buttons stay clickable."""

    BINDINGS = [
        Binding("y,enter", "confirm", "confirm", show=False),
        Binding("n", "cancel", "cancel", show=False),
    ]

    def __init__(self, title: str, body_markup_lines: list[str]):
        super().__init__()
        self._title = title
        self._body = body_markup_lines

    def compose(self) -> ComposeResult:
        with Vertical(classes="box"):
            yield Static(self._title, classes="box-title")
            for ln in self._body:
                yield Static(ln)
            with Horizontal(classes="btnrow"):
                yield Button("Confirm", variant="primary", id="yes")
                yield Button("cancel", id="no")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "yes")

    def action_confirm(self) -> None:
        self.dismiss(True)

    def action_cancel(self) -> None:
        self.dismiss(False)


class PermissionModal(Overlay):
    """Approve/deny one risky tool call. dismiss("yes"|"no"|"always"|None)."""

    BINDINGS = [
        Binding("y", "approve", "allow once"),
        Binding("a", "always", "always this session"),
        Binding("n,escape", "deny", "deny"),
    ]

    def __init__(self, tool_name: str, preview: str, danger_reason: str | None):
        super().__init__()
        self.tool_name = tool_name
        self.preview = preview
        self.danger_reason = danger_reason

    def compose(self) -> ComposeResult:
        yield Vertical(classes="box")  # populated in on_mount (per-danger styling)

    def on_mount(self) -> None:
        box = self.query_one(".box", Vertical)
        title_color = T.RED if self.danger_reason else "#fbbf24"
        box.mount(Static(
            Text(f"{self.tool_name.upper()}  ", style=f"bold {T.CYAN}")
            + Text("DANGEROUS PATTERN", style=f"bold {title_color}")
            if self.danger_reason
            else Text(f"{self.tool_name.upper()} wants to act",
                      style=f"bold {title_color}"),
            classes="box-title",
        ))
        if self.preview:
            is_diff = "\n" in self.preview and ("+ " in self.preview or "- " in self.preview)
            body = Syntax(self.preview, "diff" if is_diff else "text",
                          theme="ansi_dark", word_wrap=True) \
                if is_diff else Text(self.preview)
            holder = Static(body)
            holder.styles.margin = (0, 0, 1, 0)
            box.mount(holder)
        hint = Text()
        hint.append("[y]", style=f"bold {T.GREEN}")
        hint.append(" allow once   ")
        hint.append("[a]", style=f"bold {T.CYAN}")
        hint.append(" always this session   ")
        hint.append("[n/esc]", style=f"bold {T.RED}")
        hint.append(" deny")
        box.mount(Static(hint))

    def action_approve(self) -> None:
        self.dismiss("yes")

    def action_always(self) -> None:
        self.dismiss("always")

    def action_deny(self) -> None:
        self.dismiss("no")


class DiffOverlay(Overlay):
    """Full-screen-ish diff viewer for /diff and file-change inspection."""

    def __init__(self, title: str, diff_text: str, footer: str = ""):
        super().__init__()
        self._title = title
        self._diff = diff_text or "(no changes)"
        self._footer = footer

    def compose(self) -> ComposeResult:
        box = Vertical(classes="box")
        box.styles.max_height = "90%"
        yield box

    def on_mount(self) -> None:
        from textual.containers import VerticalScroll

        box = self.query_one(".box", Vertical)
        box.mount(Static(Text(self._title, style=f"bold {T.CYAN}"), classes="box-title"))
        scroll = VerticalScroll(Static(Syntax(
            self._diff, "diff", theme="ansi_dark", word_wrap=True,
        )))
        scroll.styles.height = "1fr"
        box.mount(scroll)
        if self._footer:
            box.mount(Static(Text(self._footer, style="#5d7290")))


class HelpOverlay(Overlay):
    """Keybinding cheatsheet + command reference."""

    KEYS = [
        ("enter", "send prompt"),
        ("alt+enter / trailing \\", "newline inside the prompt"),
        ("up/down", "input history (when suggestions hidden)"),
        ("tab / shift+tab", "cycle panel focus"),
        ("ctrl+p", "command palette"),
        ("ctrl+c", "interrupt the running turn"),
        ("ctrl+l", "clear the transcript view"),
        ("ctrl+q", "quit"),
        ("esc", "close overlay / hide suggestions"),
        ("/", "command mode (focus prompt with slash)"),
        ("f2 / f3", "toggle project / system panels"),
        ("f5", "refresh tree, git and stats data"),
        ("f4 / ctrl+h", "session history: browse, resume, delete"),
        ("enter on file in PROJECT panel", "read-only file preview"),
        ("click or enter on tool card", "expand output & diffs"),
    ]

    def __init__(self, command_list: list[tuple[str, str]]):
        super().__init__()
        self._commands = command_list

    def compose(self) -> ComposeResult:
        with Vertical(classes="box"):
            yield Static("NEXUS - keys", classes="box-title")
            for key, what in self.KEYS:
                yield Static(Text.assemble(
                    ("  ", ""), (f"{key:<28}", f"bold {T.CYAN}"), (what, "#c7d5e8"),
                ))
            yield Static(Text(""), classes="box-title")
            yield Static(Text("/commands", style=f"bold {T.CYAN}"))
            for name, desc in self._commands:
                yield Static(Text.assemble(
                    ("  ", ""), (f"{name:<14}", f"bold {T.VIOLET}"), (desc, "#c7d5e8"),
                ))


class SessionsOverlay(Overlay):
    """Browse past sessions: Enter resumes, d deletes.
    dismiss(row) resumes; dismiss({"delete": row}) asks the app to delete."""

    BINDINGS = [Binding("d", "delete_row", "delete highlighted session")]

    def __init__(self, rows: list[dict], current_id=None):
        super().__init__()
        self._rows = rows
        self._current_id = current_id

    def compose(self) -> ComposeResult:
        from textual.widgets import OptionList
        from textual.widgets.option_list import Option

        with Vertical(classes="box"):
            yield Static("SESSIONS - newest first · enter resume · d delete",
                         classes="box-title")
            ol = OptionList(id="session-list")
            for i, r in enumerate(self._rows, start=1):
                line = Text()
                cur = r.get("current", False)
                line.append("● " if cur else "  ", style=f"bold {T.GREEN}")
                line.append(f"{i:>2} ", style=f"bold {T.CYAN}")
                title = (r["title"] or "(untitled)")[:30]
                line.append(f"{title:<32}", style="#c7d5e8")
                age = relative_time(r.get("updated_at"))
                line.append(f"{age:>5}", style=T.AMBER)
                line.append(f" {r['messages']:<3} msgs", style="#5d7290")
                line.append(" " + str(r["id"])[:8], style="#5d7290")
                ol.add_option(Option(line, id=str(i)))
            yield ol
            hint = Text()
            hint.append(" enter", style=f"bold {T.GREEN}")
            hint.append(" resume · ")
            hint.append("d", style=f"bold {T.RED}")
            hint.append(" delete · esc cancel")
            yield Static(hint)

    def on_mount(self) -> None:
        """Pre-highlight the newest row so Enter works without any arrows."""
        from textual.widgets import OptionList

        ol = self.query_one("#session-list", OptionList)
        if ol.option_count:
            ol.highlighted = 0

    def on_option_list_option_selected(self, event) -> None:
        idx = int(str(event.option.id)) - 1
        row = dict(self._rows[idx])
        row.pop("current", None)
        self.dismiss(row)

    def action_delete_row(self) -> None:
        from textual.widgets import OptionList

        ol = self.query_one("#session-list", OptionList)
        idx = ol.highlighted
        if idx is None or idx >= len(self._rows):
            return
        row = dict(self._rows[idx])
        row.pop("current", None)
        self.dismiss({"delete": row})


class PermissionsOverlay(Overlay):
    """Live permission console: switch modes, inspect per-tool policy, review
    session grants, the danger list and recent decisions. dismiss(None)."""

    BINDINGS = [
        Binding("1", "mode_default", "default mode"),
        Binding("2", "mode_auto_edit", "auto-edit mode"),
        Binding("3", "mode_full_auto", "full-auto mode"),
        Binding("r", "revoke_grants", "revoke session grants"),
    ]

    MODES = [
        ("default", "asks before every risky action"),
        ("auto-edit", "edits run freely - bash still asks"),
        ("full-auto", "everything runs; dangerous patterns still ask"),
    ]

    def __init__(self, manager, on_change=None):
        super().__init__()
        self.manager = manager
        self.on_change = on_change  # app callback (refreshes the mode line)

    def compose(self) -> ComposeResult:
        box = Vertical(classes="box")
        box.styles.max_height = "92%"
        yield box

    async def on_mount(self) -> None:
        from textual.containers import VerticalScroll

        box = self.query_one(".box", Vertical)
        scroll = VerticalScroll(Static(id="perms-body"))
        scroll.styles.height = "1fr"
        await box.mount(scroll)
        self._render_body()

    # ------------------------------------------------------------- rendering

    def _render_body(self) -> None:
        from rich.console import Group

        m = self.manager
        current_mode = m.mode.value

        # -- mode selector row
        modes_line = Text()
        for i, (value, desc) in enumerate(self.MODES, start=1):
            if i > 1:
                modes_line.append("   ")
            active = value == current_mode
            glyph = "●" if active else "○"
            color = T.GREEN if active else T.FAINT
            modes_line.append(f"[{i}] ", style=f"bold {T.CYAN}")
            modes_line.append(f"{glyph} {value}", style=f"bold {color}" if active else color)
            modes_line.append(f"  {desc}", style=T.FAINT)

        # -- per-tool policy table
        policy = Text()
        policy.append("\nPOLICY UNDER CURRENT MODE\n", style=f"bold {T.CYAN}")
        tools_with_grants = sorted({k.split(":", 1)[0] for k in m.session_allow})
        for tool in sorted(set(tools_with_grants) | {"bash", "write", "edit"}):
            marker = "●" if tool in tools_with_grants else " "
            style = f"bold {T.GREEN}" if tool in tools_with_grants else "#c7d5e8"
            policy.append(f" {marker} {tool:<6}", style=style)
            policy.append("  " + m.tool_policy(tool) + "\n", style="#5d7290")

        ungated = (
            " read · list · glob · grep · web_fetch · web_search · task"
            "   run freely (never gated)"
        )
        policy.append(Text(ungated + "\n", style=T.FAINT))

        # -- session grants (action-scoped: exact command or exact path)
        grants = Text()
        grants.append('\nSESSION GRANTS ("always" approvals, per action)\n', style=f"bold {T.CYAN}")
        if m.session_allow:
            for k in sorted(m.session_allow):
                grants.append(f" ● {k}\n", style=f"bold {T.GREEN}")
            grants.append(" [r] revoke all grants\n", style=f"bold {T.AMBER}")
        else:
            grants.append(" (none)\n", style=T.FAINT)

        # -- danger list
        danger = Text()
        danger.append("\nDANGEROUS BASH PATTERNS (always require approval)\n",
                      style=f"bold {T.RED}")
        labels = ["rm -r+f (any spelling)", "sudo", "git push --force",
                  "git clean -f…", "drop table/database", "shutdown/reboot",
                  "del|rd|rmdir /s", "find … -delete", "Remove-Item -Recurse",
                  "format X:", "> /dev/sdX"]
        danger.append(" " + " · ".join(labels) + "\n", style="#f87171")

        # -- recent activity log
        log = Text()
        log.append("\nRECENT ACTIVITY (newest first)\n", style=f"bold {T.CYAN}")
        records = list(m.history)
        if records:
            for rec in reversed(records[-10:]):
                verdict_style, mark = {
                    "allowed": ("#34d399", "✔ auto"),
                    "asked-yes": ("#34d399", "✔ approved once"),
                    "asked-always": ("#34d399", "✔ granted for session"),
                    "denied": ("#f87171", "✖ denied"),
                    "revoked": ("#fbbf24", "↺ revoked"),
                }.get(rec.verdict, ("#c7d5e8", rec.verdict))
                line = Text()
                line.append(f" {rec.ts}  ", style=T.FAINT)
                line.append(f"{rec.tool_name:<6}", style=f"bold {T.VIOLET}")
                line.append(mark, style=verdict_style)
                if rec.reason:
                    line.append(f"  ({rec.reason})", style=T.FAINT)
                line.append("\n")
                log.append(line)
        else:
            no_log = Text(" (no risky calls yet this session)\n", style=T.FAINT)
            log.append(no_log)

        holder = self.query_one("#perms-body", Static)
        holder.update(Group(modes_line, policy, grants, danger, log))

    # ------------------------------------------------------------- actions

    def _apply_mode(self, value: str) -> None:
        from coding_agent.core.permissions import parse_mode

        try:
            self.manager.mode = parse_mode(value)
        except ValueError:
            return
        if self.on_change:
            self.on_change()
        self._render_body()

    def action_mode_default(self) -> None:
        self._apply_mode("default")

    def action_mode_auto_edit(self) -> None:
        self._apply_mode("auto-edit")

    def action_mode_full_auto(self) -> None:
        self._apply_mode("full-auto")

    def action_revoke_grants(self) -> None:
        _dropped = self.manager.revoke_all()
        if self.on_change:
            self.on_change()
        self._render_body()


class FileViewerOverlay(Overlay):
    """Read-only preview of a workspace file, syntax-highlighted by suffix."""

    def __init__(self, path: str, content: str, truncated: bool = False):
        super().__init__()
        self._path = path
        self._content = content or "(empty file)"
        self._truncated = truncated

    def compose(self) -> ComposeResult:
        box = Vertical(classes="box")
        box.styles.max_height = "92%"
        yield box

    def on_mount(self) -> None:
        from textual.containers import VerticalScroll

        box = self.query_one(".box", Vertical)
        title = Text()
        title.append("FILE  ", style=f"bold {T.MUTED}")
        title.append(self._path, style=f"bold {T.CYAN}")
        if self._truncated:
            title.append("  (first N lines shown)", style=T.AMBER)
        box.mount(Static(title, classes="box-title"))
        scroll = VerticalScroll(Static(Syntax(
            self._content,
            "text",
            theme="ansi_dark",
            word_wrap=True,
        )))
        scroll.styles.height = "1fr"
        box.mount(scroll)
        footer = Text(" esc close", style="#31435e")
        box.mount(Static(footer))
