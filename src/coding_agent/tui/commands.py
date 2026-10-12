"""Session-command helpers behind the REPL slash commands (/sessions, /resume,
/export, /delete) and the --continue/--resume CLI flags.

Kept free of REPL I/O where possible so the logic is unit-testable; only the
pickers themselves touch the console.
"""
import json
from datetime import datetime
from pathlib import Path

from rich.table import Table

from coding_agent.models.orm import Session

RESULT_SNIPPET_LIMIT = 1500


# ------------------------------------------------------------------ listing

def print_session_table(sessions: list[Session], current_id=None) -> None:
    """Numbered, newest-first table. The row number doubles as a /resume token."""
    table = Table(show_header=True, header_style="bold", box=None, padding=(0, 2))
    table.add_column("#", justify="right", style="dim")
    table.add_column("id")
    table.add_column("title")
    table.add_column("msgs", justify="right")
    table.add_column("updated")
    for i, s in enumerate(sessions, start=1):
        mark = "*" if current_id and s.id == current_id else ""
        table.add_row(
            str(i),
            str(s.id)[:8] + mark,
            (s.title or "(untitled)")[:44],
            str(len(s.messages)),
            s.updated_at.strftime("%m-%d %H:%M"),
        )
    from coding_agent.tui.renderer import console

    console.print(table)


def resolve_target(sessions: list[Session], token: str) -> Session | None:
    """Map a user token to a session: a row number from /sessions ("3") or an
    id prefix ("a1b2c3d4"). Returns None when nothing matches.

    Disambiguation: row numbers are short (1-2 digits — at most 25 rows are
    listed) while ids display as 8 hex chars, so a long all-digit token is an
    id prefix, not a row."""
    token = (token or "").strip()
    if not token:
        return None
    if token.isdigit() and len(token) <= 2:
        idx = int(token) - 1
        if 0 <= idx < len(sessions):
            return sessions[idx]
        return None
    for s in sessions:
        if str(s.id).startswith(token.lower()):
            return s
    return None


def pick_session(sessions: list[Session], current_id=None) -> Session | None:
    """Interactive picker used by --resume and bare `/resume`. Returns the
    chosen session, or None to start fresh."""
    from coding_agent.tui.renderer import console

    if not sessions:
        console.print("[dim]no sessions yet[/]")
        return None
    print_session_table(sessions, current_id=current_id)
    try:
        raw = console.input(
            "[bold cyan]resume #[/] [dim](number or id-prefix, Enter = fresh)[/] "
        ).strip()
    except (EOFError, KeyboardInterrupt):
        return None
    if not raw:
        return None
    target = resolve_target(sessions, raw)
    if not target:
        console.print(f"[yellow]no session matching '{raw}' — starting fresh[/]")
    return target


# ------------------------------------------------------------------- export

def _fmt(ts: datetime | None) -> str:
    return ts.strftime("%Y-%m-%d %H:%M:%S") if ts else "?"


def render_markdown(session: Session) -> str:
    """Full transcript as markdown: messages in order, tool calls with their
    arguments and results (long results clipped)."""
    lines = [
        f"# {session.title or 'Agent session'}",
        "",
        f"- **Session:** `{session.id}`",
        f"- **Created:** {_fmt(getattr(session, 'created_at', None))} · "
        f"**Updated:** {_fmt(session.updated_at)}",
        f"- **Messages:** {len(session.messages)}",
        "",
        "---",
        "",
    ]
    role_names = {"user": "👤 You", "assistant": "🤖 Agent", "system": "⚙️ System"}
    for m in session.messages:
        name = role_names.get(m.role.value, m.role.value)
        body = (m.content or "").strip()
        lines.append(f"## {name} · {_fmt(m.created_at)}")
        lines.append("")
        if body:
            lines.append(body)
            lines.append("")
        for tc in m.tool_calls:
            status = tc.status.value if tc.status else "?"
            lines.append(f"**Tool `{tc.name}`** ({status})")
            lines.append("")
            lines.append("```json")
            lines.append(json.dumps(tc.arguments, indent=2, default=str)[:800])
            lines.append("```")
            lines.append("")
            if tc.result is not None:
                result_text = json.dumps(tc.result, indent=2, default=str)
                if len(result_text) > RESULT_SNIPPET_LIMIT:
                    result_text = (
                        result_text[:RESULT_SNIPPET_LIMIT]
                        + f"\n… [{len(result_text) - RESULT_SNIPPET_LIMIT} chars clipped]"
                    )
                lines.append("<details><summary>result</summary>")
                lines.append("")
                lines.append("```json")
                lines.append(result_text)
                lines.append("```")
                lines.append("")
                lines.append("</details>")
                lines.append("")
        lines.append("---")
        lines.append("")
    return "\n".join(lines)


def export_markdown(session: Session, out_path: str | None = None) -> Path:
    """Write the transcript to out_path, or auto-name next to the workspace.
    Returns the path written."""
    if out_path:
        path = Path(out_path).expanduser()
        if path.is_dir():
            path = path / default_export_name(session)
    else:
        path = default_export_name(session)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_markdown(session), encoding="utf-8")
    return path


def default_export_name(session: Session) -> Path:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    name = f"agent-session-{str(session.id)[:8]}-{stamp}.md"
    # Default destination: the workspace root (imported lazily to avoid a
    # config import at module load in tests that patch settings).
    from coding_agent.config import settings

    return settings.workspace_path / name


# ------------------------------------------------------------------ helpers

def message_preview(session: Session, n: int = 6) -> list[tuple[str, str]]:
    """(role_label, content) pairs for the resume tail display."""
    previews = []
    for m in session.messages[-n:]:
        label = {"user": "you", "assistant": "agent"}.get(m.role.value, m.role.value)
        content = (m.content or "").strip()
        if content:
            previews.append((label, content))
    return previews


# ------------------------------------------------------- custom slash commands

def load_custom_commands(root=None) -> dict[str, str]:
    """*.md files under <workspace>/.agent/commands become custom slash
    commands: /<filename-stem> sends the file's content as the user prompt.
    Rescanned on every use so edits apply immediately."""
    from coding_agent.config import settings

    directory = (root or settings.workspace_path) / ".agent" / "commands"
    try:
        files = sorted(directory.glob("*.md"))
    except OSError:
        return {}
    commands: dict[str, str] = {}
    for f in files:
        try:
            body = f.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if body.strip():
            commands[f.stem.lower()] = body
    return commands


def render_custom_command(template: str, args: str) -> str:
    """Substitute $ARGS (and the $ARGUMENTS alias) into a command template."""
    return template.replace("$ARGS", args).replace("$ARGUMENTS", args)


def custom_command_hint(commands: dict[str, str]) -> str:
    """One help-line summarizing available custom commands."""
    if not commands:
        return ""
    names = sorted(commands)
    shown = ", ".join(f"/{n}" for n in names[:5])
    if len(names) > 5:
        shown += f" (+{len(names) - 5} more)"
    return (f"[bold]Custom commands[/]  {shown}\n"
            "\n"
            "  [dim]from .agent/commands/*.md — $ARGS becomes your arguments[/]\n")
