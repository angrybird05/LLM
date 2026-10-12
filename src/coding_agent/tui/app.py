"""Interactive terminal REPL — the Claude Code-style front door.

Run with no subcommand:  coding-agent
"""
import asyncio
import json
import time
import uuid as uuid_mod
from pathlib import Path

from rich.console import Group
from rich.markdown import Markdown
from rich.panel import Panel
from rich.syntax import Syntax
from rich.table import Table
from rich.text import Text

from coding_agent.config import settings
from coding_agent.core.context_builder import (
    GLOBAL_MEMORY_PATH,
    build_system_prompt,
    find_project_memory,
)
from coding_agent.core.hooks import HookRunner
from coding_agent.core.llm.factory import get_provider
from coding_agent.core.permissions import (
    RISKY_TOOLS,
    PermissionManager,
    parse_mode,
)
from coding_agent.core.tools import READ_ONLY_TOOL_NAMES, registry
from coding_agent.models.database import async_session_maker, init_db
from coding_agent.services.chat_service import ChatService, ProviderTurnError
from coding_agent.services.git_service import (
    CheckpointStore,
    GitService,
    generate_commit_message,
    make_snapshot_hook,
)
from coding_agent.services.session_service import SessionService
from coding_agent.tui import renderer
from coding_agent.tui.commands import (
    custom_command_hint,
    export_markdown,
    load_custom_commands,
    message_preview,
    pick_session,
    print_session_table,
    render_custom_command,
    resolve_target,
)
from coding_agent.tui.input_line import LineReader
from coding_agent.tui.renderer import console

# Appended to the system prompt for /plan turns; combined with restricting the
# turn to READ_ONLY_TOOL_NAMES this yields research + a plan, zero mutations.
PLAN_SUFFIX = """

PLANNING MODE: the user asked for a plan, not changes. Research with read-only
tools as needed, then reply with a concise numbered step-by-step implementation
plan: files to touch, what changes in each, and how to verify. Do NOT write,
edit, or run anything that mutates the workspace."""

HELP_TEXT = """\
  /help                 Show this help
  /clear                Start a fresh session
  /model [name]         Show or switch the model
  /provider [name]      Show or switch the provider (anthropic / openai)
  /permissions [mode]   Show or switch permissions: default | auto-edit | full-auto
  /memory               Show the memory files the agent reads (AGENT.md / CLAUDE.md)
  /compact              Summarize older history to free context (auto near the limit)
  /plan <goal>          Research read-only, propose steps; approve to execute
  /undo                 Revert the last agent-made file change (this session)
  /diff                 Show changes vs git + files the agent touched this session
  /commit               Stage session changes, propose a commit message, ask first
  /git init             Initialize a git repository in the workspace
  /sessions [n]         List recent sessions; /sessions <n> resumes row n
  /resume [target]      Resume by row # or id-prefix; bare /resume shows a picker
  /export [path]        Export this session's transcript to markdown
  /rename <title>       Rename the current session
  /delete [target]      Delete a session (row # / id-prefix; default: current)
  /tools                List available tools
  /tokens               Token usage for this session
  /exit                 Quit  (also Ctrl+D)

Tips:
  Ctrl+C interrupts the agent mid-turn without quitting.
  End a line with \\ to continue on the next line; pasted text keeps its newlines.
  ↑/↓ recall previous input (persisted across launches in ~/.agent/history).
  bash, write and edit ask before running ([y]es / [n]o / [a]lways this session);
  dangerous commands (rm -rf, sudo, force-push…) ask in every mode.
  /undo reverts the last write/edit; checkpoints cover this session only.
  The agent never pushes — pushing stays a manual git command.
  Put standing instructions in AGENT.md at the workspace root (or ~/.agent/AGENT.md).\
"""


class Repl:
    def __init__(self, mode: str | None = None, initial_session_id=None,
                 pick_at_start: bool = False):
        self.session_id = initial_session_id
        self.pick_at_start = pick_at_start  # bare `--resume`: picker before the loop
        self.provider_name: str | None = None  # None -> DEFAULT_PROVIDER
        self.model: str | None = None          # None -> provider default
        self.total_usage = {"input": 0, "output": 0}
        self.last_context_tokens: int | None = None
        self.checkpoints = CheckpointStore()   # /undo stack, this session only
        self.git = GitService()
        self.hook_runner = HookRunner.load()   # .agent/settings.json pre/post hooks
        self._interrupted = False
        try:
            self.permissions = PermissionManager.from_mode(
                mode if mode is not None else settings.PERMISSION_MODE,
                prompt_fn=self._prompt_permission,
            )
        except ValueError as e:
            raise SystemExit(str(e)) from None

    # ------------------------------------------------------------- permissions

    async def _prompt_permission(self, req) -> str:
        """Ask the user to approve a risky action; returns yes|no|always."""
        renderer.render_permission_request(req)
        while True:
            try:
                raw = console.input(
                    "[bold yellow]Allow? [/][dim][y]es / [n]o / [a]lways this session:[/] "
                ).strip().lower()
            except (EOFError, KeyboardInterrupt):
                return "no"
            if raw in ("y", "yes"):
                return "yes"
            if raw in ("n", "no"):
                return "no"
            if raw in ("a", "always"):
                return "always"
            renderer.warn("answer y, n, or a")

    # ------------------------------------------------------------------ turn

    async def _turn(self, text: str, system_prompt_extra: str | None = None,
                    tool_names: list[str] | None = None) -> str:
        """One streaming agentic turn; returns the final assistant text."""
        async with async_session_maker() as db:
            async def gate(name, arguments) -> bool:
                decision = await self.permissions.check(name, arguments)
                return decision.allowed

            chat = ChatService(
                SessionService(db),
                permission_gate=gate,
                pre_tool_hook=make_snapshot_hook(self.checkpoints),
                hook_runner=self.hook_runner,
            )
            started = time.monotonic()
            turn_usage = {"input": 0, "output": 0}
            iterations = 0
            final_content = ""
            seen_calls: set[str] = set()  # de-noise repeated identical calls
            stream = renderer.StreamingText()
            in_stream = False
            system_prompt = build_system_prompt() + (system_prompt_extra or "")
            spinner = renderer.Spinner()

            turn_failed: ProviderTurnError | None = None
            try:
                spinner.maybe_start()
                async for event in chat.stream_chat(
                    message=text,
                    session_id=self.session_id,
                    provider_name=self.provider_name,
                    model=self.model,
                    system_prompt=system_prompt,
                    tool_names=tool_names,
                ):
                    spinner.stop()
                    etype = event["type"]
                    if etype == "session":
                        self.session_id = uuid_mod.UUID(event["session_id"])
                    elif etype == "text":
                        if not in_stream:
                            stream.start()
                            in_stream = True
                            console.print()
                        stream.append(event["text"])
                    elif etype == "tool_use_start":
                        pass  # detailed view printed on tool_start
                    elif etype == "tool_start":
                        if in_stream:
                            stream.finish()
                            in_stream = False
                        key = json.dumps([event["name"], event["arguments"]],
                                         sort_keys=True, default=str)
                        if key in seen_calls:
                            renderer.tool_repeat(event["name"], event["arguments"])
                        else:
                            seen_calls.add(key)
                            renderer.tool_start(event["name"], event["arguments"])
                    elif etype == "tool_result":
                        renderer.tool_result(event["result"], event.get("arguments"))
                        # The model is working again — cover the gap.
                        spinner.maybe_start()
                    elif etype == "info":
                        renderer.info(event.get("message") or "")
                    elif etype == "turn_done":
                        turn_usage = event.get("usage") or turn_usage
                        iterations = event.get("iterations", 0)
                        final_content = event.get("message") or ""
                        self.last_context_tokens = event.get("context_tokens")
            except ProviderTurnError as e:
                turn_failed = e
            finally:
                spinner.stop()
                if in_stream:
                    stream.finish()

            if turn_failed is not None:
                # One clean, actionable card instead of a repeated raw dump.
                renderer.provider_error(turn_failed.kind, turn_failed.detail)
                return ""

            self.total_usage["input"] += turn_usage["input"]
            self.total_usage["output"] += turn_usage["output"]
            renderer.status_line(turn_usage, iterations, time.monotonic() - started,
                                 context_tokens=self.last_context_tokens)
            return final_content

    def _run_turn(self, text: str, **kwargs) -> str:
        try:
            return asyncio.run(self._turn(text, **kwargs))
        except KeyboardInterrupt:
            console.print("\n[yellow]⏹ interrupted[/]")
        except Exception as e:
            console.print(f"\n[red]error:[/] {e}")
        return ""

    # ------------------------------------------------------------------- plan

    def _cmd_plan(self, goal: str = "") -> None:
        """Read-only research turn -> numbered plan -> optional execute."""
        if not goal:
            renderer.warn("usage: /plan <what you want done>")
            return
        renderer.info("planning (read-only research)…")
        plan = self._run_turn(
            goal,
            system_prompt_extra=PLAN_SUFFIX,
            tool_names=sorted(READ_ONLY_TOOL_NAMES),
        ).strip()
        if not plan:
            renderer.warn("the planner returned nothing — nothing to approve")
            return
        try:
            raw = console.input(
                "[bold yellow]Execute this plan now? [/][dim][y]es / [n]o:[/] "
            ).strip().lower()
        except (EOFError, KeyboardInterrupt):
            raw = "n"
        if raw not in ("y", "yes"):
            renderer.info("plan kept in the transcript — run it later with a normal message")
            return
        self._run_turn(
            f"The plan below was researched and approved. Implement it exactly; "
            f"you may use all tools now.\n\n{plan}",
        )

    # -------------------------------------------------------------- commands

    def _cmd_model(self, arg: str) -> None:
        provider = get_provider(self.provider_name)
        if arg:
            self.model = arg
            renderer.info(f"model set to {arg}")
            return
        async def _fetch() -> list[str]:
            try:
                return await provider.list_models()
            except Exception:
                return provider.models

        table = Table.grid(padding=(0, 2))
        table.add_row("[dim]current:[/]", self.model or settings.DEFAULT_MODEL)
        options = asyncio.run(_fetch())
        shown = options if len(options) <= 30 else options[:29] + [f"… +{len(options) - 29} more"]
        for m in shown:
            marker = " [green](default)[/]" if m == settings.DEFAULT_MODEL else ""
            table.add_row("[dim]option:[/]", m + marker)
        console.print(table)
        renderer.info(
            f"{len(options)} models on {provider.name}; switch with /model <name> "
            "(any id is accepted)"
        )

    def _cmd_provider(self, arg: str) -> None:
        providers = ("anthropic", "openai", "openrouter")

        if arg:
            if arg not in providers:
                renderer.warn(f"unknown provider '{arg}'. Available: {', '.join(providers)}")
                return
            self.provider_name = arg
            self.model = None
            renderer.info(f"provider set to {arg}; model reset to default")
            return
        renderer.info(f"current: {self.provider_name or settings.DEFAULT_PROVIDER}; "
                      f"available: {', '.join(providers)}")

    def _cmd_permissions(self, arg: str) -> None:
        if arg:
            try:
                self.permissions.mode = parse_mode(arg)
            except ValueError as e:
                renderer.warn(str(e))
                return
            renderer.info(f"permission mode set to {self.permissions.mode.value}")
        renderer.info(
            f"mode: [bold]{self.permissions.mode.value}[/] · "
            f"approved for this session: {sorted(self.permissions.session_allow) or 'none'} · "
            f"risky tools: {', '.join(sorted(RISKY_TOOLS))}"
        )

    # ------------------------------------------------------- session plumbing

    def _list_sessions(self, limit: int = 10):
        async def _list():
            async with async_session_maker() as db:
                return await SessionService(db).list(limit=limit)

        return asyncio.run(_list())

    def _load_session(self, session_id):
        async def _load():
            async with async_session_maker() as db:
                return await SessionService(db).get(session_id)

        return asyncio.run(_load())

    def _adopt(self, session) -> None:
        """Make a session active and show its recent turns."""
        self.session_id = session.id
        console.print(
            f"[dim]resumed {session.id} ({len(session.messages)} messages)"
            f" — {(session.title or '(untitled)')[:44]}[/]"
        )
        for label, content in message_preview(session):
            console.print(f"  [bold]{label}:[/] {content[:160]}")

    def _cmd_sessions(self, arg: str = "") -> None:
        sessions = self._list_sessions()
        if not sessions:
            renderer.info("no sessions yet")
            return
        print_session_table(sessions, current_id=self.session_id)
        if arg:
            target = resolve_target(sessions, arg)
            if not target:
                renderer.warn(f"no session matching '{arg}'")
                return
            self._adopt(target)
        else:
            renderer.info("resume with /resume <#|id-prefix>, or /sessions <n>")

    def _cmd_resume(self, target: str = "") -> None:
        sessions = self._list_sessions(limit=25)
        if not target:
            session = pick_session(sessions, current_id=self.session_id)
        else:
            session = resolve_target(sessions, target)
            if not session:
                renderer.warn(f"no session matching '{target}'")
                return
        if session:
            self._adopt(session)

    def _cmd_export(self, path: str = "") -> None:
        """Dump the full transcript (incl. tool calls/results) as markdown."""
        if not self.session_id:
            renderer.info("nothing to export yet — no session in progress")
            return
        session = self._load_session(self.session_id)
        if not session:
            renderer.warn("session not found")
            return
        try:
            out = export_markdown(session, path or None)
        except OSError as e:
            renderer.warn(f"export failed: {e}")
            return
        renderer.info(f"exported {len(session.messages)} messages → {out}")

    def _cmd_rename(self, title: str = "") -> None:
        if not title:
            renderer.warn("usage: /rename <new title>  (renames the current session)")
            return
        if not self.session_id:
            renderer.info("no session in progress to rename")
            return

        async def _rename():
            async with async_session_maker() as db:
                await SessionService(db).set_title(self.session_id, title)

        asyncio.run(_rename())
        renderer.info(f"renamed current session to: {title}")

    def _cmd_delete(self, target: str = "") -> None:
        if target:
            session = resolve_target(self._list_sessions(), target)
            if not session:
                renderer.warn(f"no session matching '{target}'")
                return
        elif self.session_id:
            session = self._load_session(self.session_id)
            if not session:
                renderer.warn("current session no longer exists")
                return
        else:
            renderer.info("no session in progress — use /delete <#|id-prefix>")
            return

        label = (session.title or "(untitled)")[:40]
        try:
            raw = console.input(
                f"[yellow]delete session {str(session.id)[:8]} '{label}' "
                "and all its messages? [y/N]:[/] "
            ).strip().lower()
        except (EOFError, KeyboardInterrupt):
            raw = "n"
        if raw not in ("y", "yes"):
            renderer.info("kept")
            return

        async def _del():
            async with async_session_maker() as db:
                return await SessionService(db).delete(session.id)

        if not asyncio.run(_del()):
            renderer.warn("delete failed")
            return
        renderer.info(f"deleted {str(session.id)[:8]}")
        if self.session_id == session.id:
            self.session_id = None

    def _cmd_tools(self) -> None:
        for t in registry.list():
            console.print(f"  [bold]{t.name}[/][dim] — {t.description}[/]")

    def _cmd_compact(self) -> None:
        """Summarize older history now; auto-compaction does this near the limit."""
        if not self.session_id:
            renderer.info("nothing to compact yet — no session in progress")
            return

        async def _compact():
            async with async_session_maker() as db:
                async def gate(name, arguments) -> bool:
                    decision = await self.permissions.check(name, arguments)
                    return decision.allowed

                chat = ChatService(
                SessionService(db),
                permission_gate=gate,
                pre_tool_hook=make_snapshot_hook(self.checkpoints),
                hook_runner=self.hook_runner,
            )
                return await chat.compact(
                    self.session_id,
                    provider_name=self.provider_name,
                    model=self.model,
                    system_prompt=build_system_prompt(),
                )

        renderer.info("compacting…")
        stats = asyncio.run(_compact())
        if not stats:
            renderer.info("nothing compactable (history too short or no safe boundary)")
            return
        saved = stats["before_tokens"] - stats["after_tokens"]
        self.last_context_tokens = stats["after_tokens"]
        renderer.info(
            f"compacted {stats['summarized_messages']} messages into a summary · "
            f"~{stats['before_tokens']:,} → ~{stats['after_tokens']:,} tokens "
            f"(saved ~{saved:,})"
        )

    # ------------------------------------------------------------------- git

    def _rel(self, path) -> str:
        try:
            return str(Path(path).relative_to(settings.workspace_path))
        except ValueError:
            return str(path)

    def _cmd_undo(self) -> None:
        record = self.checkpoints.undo_last()
        if not record:
            renderer.info("nothing to undo — no agent file changes this session")
            return
        fate = "file removed" if record.before is None else "previous content restored"
        renderer.info(f"undid {record.action} of {self._rel(record.path)} ({fate})")

    def _cmd_diff(self) -> None:
        touched = self.checkpoints.touched()
        if self.git.is_repo():
            stat = self.git.diff_stat()
            text = self.git.diff_text()
            if not stat and not text and not touched:
                renderer.info("working tree clean — no changes yet")
                return
            base = "HEAD" if self.git.has_commits() else "initial state (no commits yet)"
            console.print(f"[bold]branch:[/] {self.git.branch()}  [dim]vs {base}[/]")
            if stat:
                console.print(f"[dim]{stat}[/]")
            if text:
                console.print(Syntax(text, "diff", theme="ansi_dark", word_wrap=True))
        elif not touched:
            renderer.warn("not a git repository — /git init to start tracking changes")
            return
        if touched:
            names = ", ".join(self._rel(p) for p in touched)
            console.print(f"[dim]agent-touched this session: {names}[/]")

    def _cmd_commit(self) -> None:
        if not self.git.is_repo():
            renderer.warn("not a git repository — /git init first")
            return

        paths = self.checkpoints.touched() or self.git.changed_paths()
        if not paths:
            renderer.info("nothing to commit — working tree clean")
            return
        names = [self._rel(p) for p in paths]

        self.git.stage(paths)
        stat = self.git.diff_stat() or "(new files only)"

        renderer.info("generating commit message…")

        async def _gen():
            provider = get_provider(self.provider_name)
            return await generate_commit_message(
                provider, stat, self.git.diff_text(limit=4000), model=self.model,
            )

        message = asyncio.run(_gen())
        if not message:
            message = f"chore: update {', '.join(names[:3])}" + ("…" if len(names) > 3 else "")

        body = Group(
            Text(message, style="bold green"),
            Text("\n" + "\n".join(names), style="dim"),
            Text("\nthis will create a git commit", style="dim"),
        )
        console.print(Panel(body, title="⚠ commit", border_style="yellow"))
        try:
            raw = console.input("[bold yellow]Commit? [/][dim][y]es / [n]o:[/] ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            raw = "n"
        if raw not in ("y", "yes"):
            self.git.unstage_all()
            renderer.info("commit cancelled (staged files unstaged)")
            return
        sha = self.git.commit(message)
        renderer.info(f"committed {sha}: {message}")

    def _cmd_git(self, arg: str = "") -> None:
        if arg == "init":
            if self.git.is_repo():
                renderer.info("already a git repository")
                return
            self.git.init()
            renderer.info(f"initialized empty git repository in {settings.workspace_path}")
            return
        if self.git.is_repo():
            n_changes = len(self.git.changed_paths())
            renderer.info(
                f"git repository · branch {self.git.branch()} · "
                f"{n_changes} changed file(s)"
            )
        else:
            renderer.info("not a git repository — /git init to start tracking")

    def _cmd_memory(self) -> None:
        """Show the memory files injected into the system prompt."""
        ws = settings.workspace_path
        proj_path, proj_label = find_project_memory(ws)
        rows: list[tuple[str, Path, bool]] = [
            (f"project ({proj_label or 'AGENT.md / CLAUDE.md'})",
             proj_path or ws / "AGENT.md", proj_path is not None),
            ("global", GLOBAL_MEMORY_PATH, GLOBAL_MEMORY_PATH.is_file()),
        ]
        table = Table(show_header=False, box=None, padding=(0, 2))
        for label, path, found in rows:
            mark = "[green]found[/]" if found else "[dim]absent[/]"
            table.add_row(f"[bold]{label}[/]", mark, str(path))
        console.print(table)

        for _, path, found in rows:
            if not found:
                continue
            try:
                content = path.read_text(encoding="utf-8", errors="replace").strip()
            except OSError:
                continue
            console.print(f"\n[bold]{path.name}[/]")
            console.print(Panel(
                Markdown(content[:2000] + ("\n…" if len(content) > 2000 else "")),
                border_style="dim",
            ))
        renderer.info("edit these files to give the agent standing instructions")

    def _dispatch(self, line: str) -> bool:
        """Handle a slash command; return False when the REPL should exit."""
        parts = line.split(maxsplit=1)
        cmd = parts[0].lower()
        arg = parts[1].strip() if len(parts) > 1 else ""

        if cmd in ("/exit", "/quit", "/q"):
            return False
        if cmd == "/help":
            # Body prints with markup off: literal [name]/[y]es brackets in the
            # text must survive (rich would eat them as style tags).
            console.print("[bold]Commands[/]")
            console.print(HELP_TEXT, markup=False)
            hint = custom_command_hint(load_custom_commands())
            if hint:
                console.print(hint)
        elif cmd == "/clear":
            self.session_id = None
            self.total_usage = {"input": 0, "output": 0}
            self.last_context_tokens = None
            self.checkpoints = CheckpointStore()
            self.permissions.reset_session()
            renderer.info("started a fresh session (session approvals reset)")
        elif cmd == "/model":
            self._cmd_model(arg)
        elif cmd == "/provider":
            self._cmd_provider(arg)
        elif cmd == "/permissions":
            self._cmd_permissions(arg)
        elif cmd == "/memory":
            self._cmd_memory()
        elif cmd == "/plan":
            self._cmd_plan(arg)
        elif cmd == "/compact":
            self._cmd_compact()
        elif cmd == "/undo":
            self._cmd_undo()
        elif cmd == "/diff":
            self._cmd_diff()
        elif cmd == "/commit":
            self._cmd_commit()
        elif cmd == "/git":
            self._cmd_git(arg)
        elif cmd == "/sessions":
            self._cmd_sessions(arg)
        elif cmd == "/resume":
            self._cmd_resume(arg)
        elif cmd == "/export":
            self._cmd_export(arg)
        elif cmd == "/rename":
            self._cmd_rename(arg)
        elif cmd == "/delete":
            self._cmd_delete(arg)
        elif cmd == "/tools":
            self._cmd_tools()
        elif cmd == "/tokens":
            u = self.total_usage
            msg = f"session tokens: ↑{u['input']:,} in / ↓{u['output']:,} out"
            if self.last_context_tokens is not None:
                msg += f" · context ~{self.last_context_tokens:,}"
            renderer.info(msg)
        else:
            custom = load_custom_commands()
            name = cmd[1:].lower()
            if name in custom:
                rendered = render_custom_command(custom[name], arg)
                console.print(f"[dim]custom command [bold]/{name}[/][/]")
                self._run_turn(rendered)
            else:
                hint = " — or a custom command from .agent/commands/" if custom else ""
                renderer.warn(f"unknown command {cmd}{hint} (see /help)")
        return True

    # ------------------------------------------------------------------- run

    def run(self) -> None:
        provider = get_provider(self.provider_name)
        renderer.banner("0.1.0", self.model or settings.DEFAULT_MODEL,
                        provider.name, self.permissions.mode.value, str(settings.workspace_path))

        if self.pick_at_start:
            target = pick_session(self._list_sessions(), current_id=self.session_id)
            if target:
                self._adopt(target)
        elif self.session_id:
            session = self._load_session(self.session_id)
            if session:
                self._adopt(session)
            else:
                renderer.warn("session no longer exists — starting fresh")
                self.session_id = None
        if not self.git.is_repo():
            renderer.info("tip: not a git repository — /git init to start tracking")

        reader = LineReader()
        while True:
            line = reader.read(console)
            if line is None:
                console.print("\n[dim]bye[/]")
                break
            if not line:
                continue
            if line.startswith("/"):
                if not self._dispatch(line):
                    console.print("[dim]bye[/]")
                    break
                continue
            if not reader.rendered_prompt:
                renderer.echo_user(line)  # fallback input showed nothing
            self._run_turn(line)


def run_repl(mode: str | None = None, continue_recent: bool = False,
             resume_prefix: str | None = None) -> None:
    import logging

    # The interactive UI must not drown in SQLAlchemy echo noise. Note:
    # setLevel alone does not silence an engine created with echo=True — its
    # writes bypass level checks — so disable the logger outright.
    logging.getLogger("sqlalchemy.engine.Engine").disabled = True

    # Loguru's default sink writes raw timestamped warnings to stderr —
    # straight over the chat, doubling every error the renderer already
    # shows (and repeating per model retry). Route diagnostics to a file;
    # the user-facing copy of an error is the renderer's, exactly once.
    from loguru import logger as _logger

    _logger.remove()
    try:
        log_dir = Path.home() / ".agent"
        log_dir.mkdir(exist_ok=True)
        _logger.add(log_dir / "agent.log", rotation="1 MB", retention=3,
                    level="DEBUG")
    except OSError:
        pass  # unwritable home — drop logs rather than spam the chat

    # First launch on a fresh DATABASE_URL: create tables so the very first
    # command or turn works without a separate `coding-agent init-db`.
    asyncio.run(init_db())

    initial: uuid_mod.UUID | None = None
    pick_at_start = False
    if resume_prefix is not None:
        # "" (bare --resume) -> interactive picker; a prefix resumes directly
        if resume_prefix == "":
            pick_at_start = True
        else:
            initial = _find_session_by_prefix(resume_prefix)
            if not initial:
                renderer.warn(f"no session matching '{resume_prefix}' — starting fresh")
    elif continue_recent:
        initial = _most_recent_session_id()
        if not initial:
            renderer.info("no previous session to continue — starting fresh")

    Repl(mode=mode, initial_session_id=initial, pick_at_start=pick_at_start).run()


def _most_recent_session_id():
    async def _load():
        async with async_session_maker() as db:
            s = await SessionService(db).most_recent()
            return s.id if s else None

    return asyncio.run(_load())


def _find_session_by_prefix(prefix: str):
    async def _load():
        async with async_session_maker() as db:
            sessions = await SessionService(db).list(limit=50)
            target = resolve_target(sessions, prefix)
            return target.id if target else None

    return asyncio.run(_load())
