"""CodeAgentApp: the NEXUS console - five regions, one event stream.

Layout: header (identity/model/connection/context/host) | left PROJECT panel |
center AI CORE (transcript + animated state bar) | right SYSTEM panel | bottom
COMMAND CENTER (prompt, suggestions, mode line, hints).

The app owns NO agent logic: it builds an AgentController over the backend
services, feeds its events to the widgets, and answers permission prompts
with a modal. Everything else (loop, tools, models, context) stays in the
existing packages.
"""
import asyncio
import time
import uuid as uuid_mod
from collections import deque

from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.message import Message

from coding_agent.agent import (
    PLAN_SUFFIX,
    AgentController,
    AgentStatus,
    TurnCancelled,
    TurnFailed,
    TurnFinished,
    TurnStarted,
    derive_status,
)
from coding_agent.agent.events import (
    Notice,
    SessionResolved,
    StreamDelta,
    ToolFinished,
    ToolStarted,
    UsageUpdated,
)
from coding_agent.config import settings
from coding_agent.core.hooks import HookRunner
from coding_agent.core.llm.factory import get_provider
from coding_agent.core.permissions import (
    PermissionManager,
    build_preview,
    parse_mode,
)
from coding_agent.core.tools import READ_ONLY_TOOL_NAMES, registry
from coding_agent.models.database import async_session_maker, init_db
from coding_agent.services.git_service import (
    CheckpointStore,
    GitService,
    generate_commit_message,
)
from coding_agent.services.session_service import SessionService
from coding_agent.ui import theme as T
from coding_agent.ui.commands import COMMAND_LIST, AgentCommandProvider
from coding_agent.ui.metrics import SystemMetrics, TokenTracker
from coding_agent.ui.widgets.ai_core import AiCore
from coding_agent.ui.widgets.command_dock import (
    HINTS,
    CommandDock,
    PromptArea,
)
from coding_agent.ui.widgets.header import NexusHeader
from coding_agent.ui.widgets.modals import (
    ConfirmModal,
    DiffOverlay,
    FileViewerOverlay,
    HelpOverlay,
    NewProjectModal,
    OpenDirectoryModal,
    PermissionModal,
    PermissionsOverlay,
    ProjectSwitcherOverlay,
    SessionsOverlay,
)
from coding_agent.services.project_service import ProjectRegistry
from coding_agent.services.project_switcher import switch_project
from coding_agent.ui.widgets.project_panel import ProjectPanel
from coding_agent.ui.widgets.state_bar import StateBar
from coding_agent.ui.widgets.system_panel import SystemPanel


class AgentEventMsg(Message):
    """Module-level envelope so the handler name stays `on_agent_event_msg`
    (messages nested inside a widget class would be auto-namespaced by
    Textual and never reach it)."""

    def __init__(self, event):
        super().__init__()
        self.event = event


class CodeAgentApp(App):
    TITLE = "NEXUS - coding-agent"
    COMMANDS = {AgentCommandProvider}

    CSS = """
    #main { height: 1fr; }
    #center { height: 1fr; padding: 0 1; }
    .hidden { display: none; }
    """

    BINDINGS = [
        Binding("ctrl+q", "quit", "Quit", priority=True),
        Binding("ctrl+c", "interrupt", "Interrupt", priority=True),
        Binding("ctrl+l", "clear_view", "Clear view", show=False, priority=True),
        Binding("f2", "toggle_project", "Project panel", show=False),
        Binding("f3", "toggle_system", "System panel", show=False),
        Binding("f5", "refresh_all", "Refresh tree/git/sessions", show=False),
        # f4 opens the session picker. ctrl+h is ALSO registered, but on
        # Windows consoles it is unreachable: the win32 driver reports Ctrl+H
        # as UnicodeChar \x08 and Textual maps that to Backspace (see
        # _ansi_sequences.py), so the binding never fires there. f4 works
        # everywhere; /history is the command equivalent.
        Binding("f4", "cmd_history", "Session history", show=False),
        Binding("ctrl+h", "cmd_history", "Session history", show=False),
        Binding("/", "command_mode", "Command mode", show=False),
        Binding("escape", "dismiss_overlay", "Close", show=False),
    ]

    def __init__(self, permissions: PermissionManager,
                 initial_session_id=None, provider_name: str | None = None,
                 model: str | None = None):
        super().__init__()
        self.permissions = permissions
        self.session_id: uuid_mod.UUID | None = initial_session_id
        self.provider_name = provider_name   # None -> DEFAULT_PROVIDER
        self.model = model                   # None -> provider default
        self.tracker = TokenTracker()
        self.metrics = SystemMetrics()
        self.git = GitService()
        self.checkpoints = CheckpointStore()
        self.hook_runner = HookRunner.load()
        self.controller: AgentController | None = None
        self.busy = False
        self.planning_turn = False
        self.last_plan_text = ""
        self._pending_cards: deque = deque()
        self._queue: deque = deque()   # messages typed while a turn runs
        self._panel_pref = {"project": True, "system": True}
        self.conn_state = "BOOT"
        # bare --resume opens the picker once the app is actually running
        # (workers cannot be created before App.run starts the message pump)
        self._open_picker_on_mount = False

    # -------------------------------------------------------------- layout

    def compose(self) -> ComposeResult:
        yield NexusHeader()
        with Horizontal(id="main"):
            yield ProjectPanel()
            with Vertical(id="center"):
                yield StateBar()
                yield AiCore()
            yield SystemPanel(self.tracker, self.metrics)
        yield CommandDock(COMMAND_LIST)

    async def on_mount(self) -> None:
        self.register_theme(T.NEXUS_THEME)
        self.theme = "nexus"

        # The approval UI is a modal; the manager calls back into the controller's
        # prompt_permission so cancel() can resolve a pending prompt.
        self.controller = AgentController(
            sink=self._emit_to_ui,
            permissions=self.permissions,
            prompt_fn=self._prompt_permission_modal,
            checkpoints=self.checkpoints,
            hook_runner=self.hook_runner,
            provider_name=self.provider_name,
            model=self.model,
        )
        self.permissions.prompt_fn = self.controller.prompt_permission

        dock = self.query_one(CommandDock)
        dock.load_history()
        dock.set_hints(HINTS)
        self.refresh_mode_line()

        panel = self.query_one(ProjectPanel)
        panel.on_open_file = self._open_file_preview

        header = self.query_one(NexusHeader)
        project = str(settings.workspace_path)
        header.update_info(
            model=self.model or settings.DEFAULT_MODEL,
            provider=(self.provider_name or settings.DEFAULT_PROVIDER),
            conn="READY" if self._provider_ready() else "OFFLINE",
            project=project.replace("\\", "/").rstrip("/").rsplit("/", 1)[-1],
        )

        # Persist the current workspace as the last active project.
        from coding_agent.services.project_service import ProjectRegistry as _PR
        _reg = _PR.load()
        _reg.set_last(str(settings.workspace_path))
        _reg.save()

        core = self.query_one(AiCore)
        core.meta(f"NEXUS console online - {settings.workspace_path}")
        core.meta(f"provider {self.provider_name or settings.DEFAULT_PROVIDER}"
                  f" - model {self.model or settings.DEFAULT_MODEL}"
                  f" - permission mode {self.permissions.mode.value}")
        if not self.git.is_repo():
            core.note("info", "not a git repository - /git init to start tracking")

        self.set_interval(2.0, self._sample_host)
        self.refresh_project()
        if not self._provider_ready():
            core.note("warn", "no API key found for the provider - set it in .env")

        # A previous chat handed in via --continue/--resume opens as its own
        # chat right away: its transcript renders instead of an empty view.
        if self.session_id is not None:
            self._restore_transcript()

        # Bare --resume: the picker was deferred to here (see __init__).
        if self._open_picker_on_mount:
            self._open_picker_on_mount = False
            self._load_sessions()

        self.query_one("#prompt").focus()

    def _provider_ready(self) -> bool:
        try:
            get_provider(self.provider_name)
            import os

            prov = (self.provider_name or settings.DEFAULT_PROVIDER).lower()
            if prov == "anthropic":
                return bool(os.environ.get("ANTHROPIC_API_KEY")
                            or settings.ANTHROPIC_API_KEY)
            return True  # openai-compatible endpoints may need no key locally
        except Exception:
            return False

    # ------------------------------------------------------- event plumbing

    async def _emit_to_ui(self, event) -> None:
        """Controller sink: runs inside the turn worker; post to the pump."""
        self.post_message(AgentEventMsg(event))

    def on_agent_event_msg(self, msg: AgentEventMsg) -> None:
        self._handle_event(msg.event)

    def _set_status(self, status: AgentStatus) -> None:
        self.query_one(StateBar).status = status

    def _set_conn(self, state: str) -> None:
        self.conn_state = state
        self.query_one(NexusHeader).update_info(conn=state)

    def _handle_event(self, ev) -> None:
        core = self.query_one(AiCore)
        bar = self.query_one(StateBar)

        if isinstance(ev, TurnStarted):
            self.busy = True
            self.planning_turn = ev.planning
            self.last_plan_text = ""
            self._set_status(derive_status(ev))
            bar.task = ev.text.splitlines()[0][:60]
            bar.step = 0
            bar.begin()
            core.post_user(("(plan) " if ev.planning else "") + ev.text)
            self.tracker.reset_turn_counters()
            self.query_one(CommandDock).set_busy(True)
            self.refresh_mode_line()

        elif isinstance(ev, SessionResolved):
            try:
                self.session_id = uuid_mod.UUID(ev.session_id)
            except ValueError:
                pass

        elif isinstance(ev, StreamDelta):
            core.feed_stream(ev.text)
            if not self.planning_turn:
                pass  # status stays whatever tool phase we were in
            self._set_conn("LIVE")

        elif isinstance(ev, ToolStarted):
            card = core.add_tool_card(ev.name, ev.arguments, repeat=ev.repeat)
            self._pending_cards.append(card)
            self.tracker.record_tool_start()
            self._set_status(derive_status(ev, planning=self.planning_turn))
            self.query_one(SystemPanel).refresh_data_rows()

        elif isinstance(ev, ToolFinished):
            if self._pending_cards:
                card = self._pending_cards.popleft()
                card.finish(ev.result, ev.ok, ev.duration_s)
            else:
                core.add_tool_card(ev.name, ev.arguments).finish(
                    ev.result, ev.ok, ev.duration_s)
            self.tracker.record_tool_end()
            self._set_status(derive_status(ev, planning=self.planning_turn))
            self.query_one(SystemPanel).refresh_data_rows()
            if ev.name in ("write", "edit", "bash"):
                # the tree/git panel stays honest DURING the turn, not only
                # at TurnFinished - edits show up as they land
                self.refresh_project()

        elif isinstance(ev, Notice):
            core.note(ev.level, ev.text)

        elif isinstance(ev, UsageUpdated):
            self.tracker.record_usage(
                ev.input_tokens, ev.output_tokens, ev.context_tokens)
            if ev.iterations:
                bar.step = ev.iterations
            header_ctx = f"~{ev.context_tokens:,}" if ev.context_tokens else "-"
            self.query_one(NexusHeader).update_info(
                ctx_pct=self.tracker.context_pct(self.context_token_limit),
                ctx_label=header_ctx,
            )
            self.query_one(SystemPanel).refresh_data_rows()

        elif isinstance(ev, TurnFinished):
            final = core.end_stream(ev.message)
            self.busy = False
            self.query_one(CommandDock).set_busy(False)
            bar.end()
            usage = ev.usage or {}
            self.tracker.record_turn(
                usage, ev.context_tokens, ev.elapsed_s, ev.ttft_s)
            self.last_plan_text = final if self.planning_turn else ""
            self.planning_turn = False
            self._set_status(AgentStatus.COMPLETE)
            bar.step = ev.iterations
            self._set_conn("LIVE")
            self.tracker.last_iterations = ev.iterations
            header_ctx = f"~{ev.context_tokens:,}" if ev.context_tokens else "-"
            self.query_one(NexusHeader).update_info(
                ctx_pct=self.tracker.context_pct(self.context_token_limit),
                ctx_label=header_ctx,
            )
            self.query_one(SystemPanel).refresh_data_rows()
            self.refresh_project()
            self.refresh_mode_line()
            core.turn_summary(ev.iterations,
                              (ev.usage or {}).get("input"),
                              (ev.usage or {}).get("output"),
                              ev.elapsed_s)
            self._flush_queue()
            if self.last_plan_text.strip() and not self._queue:
                self.run_worker(self._offer_plan_execution(), exclusive=False,
                                group="plan-offer")

        elif isinstance(ev, TurnFailed):
            self.busy = False
            bar.end()
            self.planning_turn = False
            core.end_stream(None)
            glyph_kind = {
                "credits": "provider credits/quota exhausted",
                "too_long": "prompt too long for the context window",
                "provider": "provider error",
            }.get(ev.kind, "error")
            core.note("error", f"{glyph_kind}: {ev.detail[:400]}")
            self._set_status(AgentStatus.ERROR)
            self._set_conn("ERR")

        elif isinstance(ev, TurnCancelled):
            self.busy = False
            bar.end()
            self.planning_turn = False
            core.end_stream(None)
            core.note("warn", f"interrupted after {ev.elapsed_s:.1f}s")
            self._set_status(AgentStatus.ONLINE)
            while self._pending_cards:
                stale = self._pending_cards.popleft()
                stale.finish({"declined": True,
                              "error": "cancelled by user"}, False, 0.0)
            self.tracker.record_tool_end()

    @property
    def context_token_limit(self) -> float:
        return settings.CONTEXT_TOKEN_LIMIT * settings.COMPACT_THRESHOLD

    # ------------------------------------------------------------ turn flow

    def _flush_queue(self) -> None:
        """Send the next queued message once a turn FINISHES. Kept after a
        user interrupt or a provider failure - never auto-send on those."""
        if self._queue and not self.busy:
            text = self._queue.popleft()
            self.start_turn(text)

    def start_turn(self, text: str, *, planning: bool = False,
                   tool_names: list[str] | None = None,
                   system_suffix: str = "") -> None:
        if self.controller is None or self.controller.running:
            self.query_one(AiCore).note(
                "warn", "agent is already working - ctrl+c to interrupt")
            return
        self._turn_worker = self.run_worker(
            self.controller.run(
                text, session_id=self.session_id, planning=planning,
                tool_names=tool_names, system_suffix=system_suffix,
            ),
            group="turn", exclusive=True, exit_on_error=False,
        )

    _turn_worker = None

    def action_interrupt(self) -> None:
        """Cooperative cancel (checked between events) plus a hard cancel of
        the worker task, so even a stalled stream/interruptible await stops."""
        ctrl = self.controller
        if ctrl is not None and ctrl.running:
            ctrl.cancel()
            if self._turn_worker is not None:
                self._turn_worker.cancel()
            self.query_one(AiCore).note("info", "interrupting...")
        else:
            self.query_one(AiCore).meta("nothing to interrupt - ctrl+q quits")

    async def _offer_plan_execution(self) -> None:
        plan = self.last_plan_text.strip()
        if not plan:
            return
        answer = await self._push_for_result(ConfirmModal(
            "Execute this plan now?",
            plan.splitlines()[:8] + ["…"],
        ))
        if answer:
            self.start_turn(
                "The plan below was researched and approved. Implement it "
                "exactly; you may use all tools now.\n\n" + plan,
            )
        else:
            self.query_one(AiCore).note(
                "info", "plan kept in the transcript - run it later with a normal message")

    async def _push_for_result(self, screen):
        """Push a screen and await its dismiss result.

        Textual 8: plain `await push_screen(s)` waits for MOUNT only and
        resolves None - callers that need the dismissal value must go through
        the callback. Wrapping it in a future works from workers (the turn
        worker asks permission) and from plain action handlers alike.
        """
        loop = asyncio.get_running_loop()
        fut: asyncio.Future = loop.create_future()

        def _on_dismiss(result) -> None:
            if not fut.done():
                fut.set_result(result)

        self.push_screen(screen, callback=_on_dismiss)
        return await fut

    async def _prompt_permission_modal(self, request) -> str:
        """prompt_fn for the PermissionManager: shows the approval overlay."""
        preview = build_preview(request.tool_name, request.arguments)
        modal = PermissionModal(request.tool_name, preview, request.danger_reason)
        answer: str = await self._push_for_result(modal)
        if not answer:
            return "no"
        return answer

    # ------------------------------------------------------------- prompting

    def action_submit_prompt(self) -> None:
        dock = self.query_one(CommandDock)
        area = dock.query_one("#prompt", PromptArea)
        if dock.suggestions_open and dock.should_complete_on_enter():
            dock.apply_selected_suggestion()
            return
        text = area.text.strip()
        if not text:
            return
        if text.startswith("/"):
            area.load_text("")
            self.handle_slash(text)
            return
        if self.busy:
            self._queue.append(text)
            area.load_text("")
            dock.remember(text)
            dock.hide_suggestions()
            core = self.query_one(AiCore)
            core.meta(f"queued: {text[:60]}"
                      + ("…" if len(text) > 60 else ""))
            self.refresh_mode_line()
            return
        area.load_text("")
        dock.remember(text)
        dock.hide_suggestions()
        self.start_turn(text)

    def handle_slash(self, line: str) -> None:
        from coding_agent.ui.commands import find_command

        spec, arg = find_command(line)
        if spec is None:
            self.query_one(AiCore).note(
                "warn", f"unknown command '{line.split()[0]}' - try /help")
            return
        getattr(self, f"action_cmd_{spec.key}")(arg)

    # ---------------------------------------------------------------- actions

    def action_clear_view(self) -> None:
        core = self.query_one(AiCore)
        core.clear_all()
        core.meta("view cleared - session kept (/clear starts a fresh one)")

    def action_command_mode(self) -> None:
        area = self.query_one("#prompt", PromptArea)
        if not area.has_focus:
            area.focus()
            if not area.text:
                area.insert("/")
        elif not area.text:
            area.insert("/")

    def action_dismiss_overlay(self) -> None:
        if len(self.screen_stack) > 1:
            self.pop_screen()
            return
        self.query_one(CommandDock).hide_suggestions()

    def action_toggle_project(self) -> None:
        self._panel_pref["project"] = not self._panel_pref["project"]
        self._apply_panels()

    def action_toggle_system(self) -> None:
        self._panel_pref["system"] = not self._panel_pref["system"]
        self._apply_panels()

    def action_focus_cycle(self) -> None:
        self.screen.focus_next()

    def action_focus_cycle_back(self) -> None:
        self.screen.focus_previous()

    def on_resize(self, event) -> None:
        self._apply_panels(event.size.width)

    def _apply_panels(self, width: int | None = None) -> None:
        w = width or (self.size.width or 120)

        def _set(widget, visible: bool) -> None:
            if visible:
                widget.remove_class("hidden")
            else:
                widget.add_class("hidden")

        _set(self.query_one(ProjectPanel),
             self._panel_pref["project"] and w >= 84)
        _set(self.query_one(SystemPanel),
             self._panel_pref["system"] and w >= 110)

    # ----------------------------------------------------------------- timers

    def _sample_host(self) -> None:
        # The 2s timer can fire during shutdown when the DOM is already gone.
        try:
            s = self.metrics.sample()
            panel = self.query_one(SystemPanel)
            self.query_one(NexusHeader)
        except Exception:  # noqa: BLE001 - tearing down, nothing to sample for
            return
        panel.refresh_data_rows()
        panel.refresh_host_rows()
        self.query_one(NexusHeader).update_info(
            cpu=s["cpu"], mem=f"{s['mem_pct']:.0f}%")

    @work(thread=True, group="io")
    def refresh_project(self) -> None:
        branch = self.git.branch() if self.git.is_repo() else None
        changed = self.git.changed_paths() if self.git.is_repo() else []
        untracked = len(self.git.untracked_files()) if self.git.is_repo() else 0
        latest_commit = self.git.latest_commit_info() if self.git.is_repo() else None
        upstream = self.git.upstream_status() if self.git.is_repo() else None
        from coding_agent.ui.widgets.project_panel import count_workspace_files

        n_files = count_workspace_files(settings.workspace_path)
        touched = [self._rel(p) for p in self.checkpoints.touched()]
        self.call_from_thread(self._apply_project_info,
                              branch, changed + touched, untracked,
                              self.git.is_repo(), n_files,
                              latest_commit, upstream)

    def _apply_project_info(self, branch, changed, untracked, is_repo, n_files,
                            latest_commit=None, upstream=None):
        panel = self.query_one(ProjectPanel)
        panel.apply_git_info(branch, changed, untracked, is_repo)
        cwd_text = str(settings.workspace_path)
        if len(cwd_text) > 38:
            cwd_text = "..." + cwd_text[-35:]
        panel.apply_stats(n_files, cwd_text)

        try:
            sys_panel = self.query_one(SystemPanel)
            sys_panel.apply_git_info(branch, changed, untracked, is_repo, latest_commit, upstream)
        except Exception:
            pass

    def _rel(self, path) -> str:
        from pathlib import Path

        try:
            return str(Path(path).relative_to(settings.workspace_path))
        except ValueError:
            return str(path)

    def action_refresh_all(self) -> None:
        """f5: rebuild the tree and re-pull git/stats."""
        self.query_one(ProjectPanel).rebuild_tree()
        self.refresh_project()
        self.cmd_note("workspace refreshed", "info")

    # ------------------------------------------------------- slash commands

    def cmd_note(self, text: str, level: str = "info") -> None:
        self.query_one(AiCore).note(level, text)

    def action_cmd_help(self, arg: str = "") -> None:
        self.push_screen(HelpOverlay(COMMAND_LIST))

    def action_cmd_exit(self, arg: str = "") -> None:
        self.exit()

    def action_cmd_model(self, arg: str = "") -> None:
        provider = get_provider(self.provider_name)
        if arg:
            self.model = arg
            if self.controller:
                self.controller.model = arg
            self.query_one(NexusHeader).update_info(model=arg)
            self.cmd_note(f"model set to {arg}", "success")
            return
        core = self.query_one(AiCore)

        async def _fetch() -> list[str]:
            try:
                return await provider.list_models()
            except Exception:
                return provider.models

        async def _show() -> None:
            options = await _fetch()
            core.meta(f"current model: {self.model or settings.DEFAULT_MODEL}")
            shown = options[:40]
            core.meta(f"{len(options)} models on {provider.name}: " + ", ".join(shown)
                      + (f" … (+{len(options) - 40} more)" if len(options) > 40 else ""))
            core.note("info",
                      "switch with /model <name> or the ctrl+p palette - any id is accepted")

        self.run_worker(_show(), exclusive=False)

    def action_cmd_provider(self, arg: str = "") -> None:
        providers = ("anthropic", "openai", "openrouter")
        if arg:
            if arg not in providers:
                self.cmd_note(f"unknown provider '{arg}' - one of {providers}", "warn")
                return
            self.provider_name = arg
            self.model = None
            if self.controller:
                self.controller.provider_name = arg
                self.controller.model = None
            self.query_one(NexusHeader).update_info(
                provider=arg, model=settings.DEFAULT_MODEL,
                conn="READY" if self._provider_ready() else "OFFLINE")
            self.cmd_note(f"provider set to {arg}; model reset to default", "success")
            return
        self.cmd_note(f"current: {self.provider_name or settings.DEFAULT_PROVIDER}"
                      f" - available: {', '.join(providers)}")

    def action_cmd_permissions(self, arg: str = "") -> None:
        if arg:
            try:
                self.permissions.mode = parse_mode(arg)
            except ValueError as e:
                self.cmd_note(str(e), "warn")
                return
            self.cmd_note(f"permission mode set to {self.permissions.mode.value}",
                          "success")
            self.refresh_mode_line()
            return
        self.push_screen(PermissionsOverlay(
            self.permissions, on_change=self.refresh_mode_line))

    def action_cmd_context(self, arg: str = "") -> None:
        t = self.tracker
        pct = t.context_pct(self.context_token_limit)
        used = f"~{t.context_tokens:,}" if t.context_tokens else "no turn yet"
        self.cmd_note(f"context {used} tokens - {pct * 100:.0f}% of the "
                      f"auto-compact line ({self.context_token_limit:,.0f})")

    def action_cmd_tokens(self, arg: str = "") -> None:
        t = self.tracker
        msg = f"session tokens: in {t.input_tokens:,} - out {t.output_tokens:,}"
        if t.context_tokens is not None:
            msg += f" - context ~{t.context_tokens:,}"
        self.cmd_note(msg)

    def action_cmd_files(self, arg: str = "") -> None:
        panel = self.query_one(ProjectPanel)
        panel.rebuild_tree()
        self.refresh_project()
        panel.query_one("#ptree").focus()

    @work(thread=True, group="io")
    def _open_file_preview(self, path) -> None:
        """Read a workspace file in this worker; the UI thread pushes the
        read-only viewer overlay. Capped so huge files never flood it."""
        from pathlib import Path as PathLib

        from coding_agent.config import settings as _settings

        try:
            text = PathLib(path).read_text(encoding="utf-8", errors="replace")
        except (OSError, ValueError) as e:
            self.call_from_thread(
                self.cmd_note, f"cannot open {path}: {e}", "warn")
            return
        MAX_CHARS = 100_000
        truncated = len(text) > MAX_CHARS
        if truncated:
            text = text[:MAX_CHARS]
        shown_lines = text.count("\n") + 1
        if shown_lines > 600:
            lines = text.splitlines()[:600]
            text = "\n".join(lines)
            truncated = True
        rel = path
        try:
            rel = PathLib(path).relative_to(_settings.workspace_path).as_posix()
        except ValueError:
            pass
        self.call_from_thread(
            self._push_file_viewer, str(rel), text, truncated)

    def _push_file_viewer(self, rel: str, text: str, truncated: bool) -> None:
        self.push_screen(FileViewerOverlay(rel, text, truncated))

    def action_cmd_git(self, arg: str = "") -> None:
        if arg == "init":
            if self.git.is_repo():
                self.cmd_note("already a git repository")
                return
            self.git.init()
            self.refresh_project()
            self.cmd_note(f"initialized git repository in {settings.workspace_path}",
                          "success")
            return
        if self.git.is_repo():
            n = len(self.git.changed_paths())
            self.cmd_note(f"branch {self.git.branch()} - {n} changed file(s)")
        else:
            self.cmd_note("not a git repository - /git init to start tracking")

    def action_cmd_undo(self, arg: str = "") -> None:
        record = self.checkpoints.undo_last()
        if not record:
            self.cmd_note("nothing to undo - no agent file changes this session")
            return
        fate = "removed" if record.before is None else "restored"
        self.cmd_note(f"undid {record.action} of {self._rel(record.path)} ({fate})",
                      "success")
        self.refresh_project()

    def action_cmd_diff(self, arg: str = "") -> None:
        if self.git.is_repo():
            base = "HEAD" if self.git.has_commits() else "initial state"
            stat = self.git.diff_stat()
            text = self.git.diff_text()
        else:
            base, stat, text = "(not a repo)", "", ""
        touched = [self._rel(p) for p in self.checkpoints.touched()]
        footer = "agent-touched this session: " + (", ".join(touched) or "none")
        title = f"{self.git.branch()} vs {base}" + (f"\n{stat}" if stat else "")
        self.push_screen(DiffOverlay(title.strip(), text, footer))

    def action_cmd_search(self, arg: str = "") -> None:
        if not arg:
            self.cmd_note("usage: /search <pattern>")
            return
        self.run_worker(self._direct_tool(
            "grep", {"pattern": arg, "path": "."}), group="tool", exclusive=False)

    def action_cmd_run(self, arg: str = "") -> None:
        if not arg:
            self.cmd_note("usage: /run <command>  (permission mode still applies)")
            return
        self.run_worker(self._direct_tool(
            "bash", {"command": arg}), group="tool", exclusive=False)

    async def _direct_tool(self, name: str, arguments: dict) -> None:
        """Execute one tool outside the LLM loop (for /run and /search)."""
        from coding_agent.services.git_service import make_snapshot_hook

        core = self.query_one(AiCore)
        decision = await self.permissions.check(name, arguments)
        if not decision.allowed:
            core.note("warn", f"{name} declined")
            return
        try:
            await (make_snapshot_hook(self.checkpoints))(name, arguments)
        except Exception:
            pass
        tool = registry.get(name)
        if tool is None:
            core.note("error", f"unknown tool {name}")
            return
        card = core.add_tool_card(name, arguments)
        started = time.monotonic()
        try:
            result = await tool.execute(arguments, {
                "session_id": str(self.session_id or ""),
                "workspace_root": str(settings.workspace_path),
                "provider": get_provider(self.provider_name),
                "model": self.model,
                "permission_gate": None,
            })
        except Exception as e:
            result = {"error": f"{type(e).__name__}: {e}"}
        card.finish(result, "error" not in result, time.monotonic() - started)
        self.refresh_project()

    @work(thread=True, group="io")
    def _commit_worker(self) -> None:
        paths = self.checkpoints.touched() or (
            self.git.changed_paths() if self.git.is_repo() else [])
        if not paths:
            self.call_from_thread(self.cmd_note,
                                  "nothing to commit - working tree clean")
            return
        names = [self._rel(p) for p in paths]
        self.git.stage(paths)
        stat = self.git.diff_stat() or "(new files only)"
        provider = get_provider(self.provider_name)
        message = asyncio.run(generate_commit_message(
            provider, stat, self.git.diff_text(limit=4000), model=self.model))
        if not message:
            message = f"chore: update {', '.join(names[:3])}" + (
                "..." if len(names) > 3 else "")
        self.call_from_thread(self._confirm_commit, message, names)

    def _confirm_commit(self, message: str, names: list[str]) -> None:
        self.run_worker(self._ask_commit(message, names), exclusive=False)

    async def _ask_commit(self, message: str, names: list[str]) -> None:
        body = [f"{message}", "", *names[:8], "",
                f"({len(names)} file(s)) will be committed"]
        answer = await self._push_for_result(ConfirmModal("Commit", body))
        if answer:
            sha = self.git.commit(message)
            self.cmd_note(f"committed {sha}: {message}", "success")
            self.refresh_project()
        else:
            self.git.unstage_all()
            self.cmd_note("commit cancelled - staged files unstaged")

    def action_cmd_commit(self, arg: str = "") -> None:
        if not self.git.is_repo():
            self.cmd_note("not a git repository - /git init first", "warn")
            return
        self._commit_worker()

    def action_cmd_compact(self, arg: str = "") -> None:
        if not self.session_id:
            self.cmd_note("nothing to compact yet - no session in progress")
            return
        self.run_worker(self._compact_now(), group="io", exclusive=True)

    async def _compact_now(self) -> None:
        from coding_agent.services.chat_service import ChatService

        core = self.query_one(AiCore)
        sid = self.session_id
        if sid is None:
            core.note("info", "nothing to compact yet - no session")
            return
        core.meta("compacting...")
        async with async_session_maker() as db:
            chat = ChatService(
                SessionService(db),
                pre_tool_hook=None,
                hook_runner=self.hook_runner,
            )
            stats = await chat.compact(
                sid,
                provider_name=self.provider_name,
                model=self.model,
                system_prompt=None,
            )
        if not stats:
            core.note("info", "nothing compactable (history too short)")
            return
        saved = stats["before_tokens"] - stats["after_tokens"]
        self.tracker.context_tokens = stats["after_tokens"]
        core.note("success",
                  f"compacted {stats['summarized_messages']} messages - "
                  f"~{stats['before_tokens']:,} to ~{stats['after_tokens']:,} "
                  f"(saved ~{saved:,})")

    def action_cmd_plan(self, arg: str = "") -> None:
        if not arg:
            self.cmd_note("usage: /plan <what you want done>")
            return
        self.start_turn(arg, planning=True,
                        tool_names=sorted(READ_ONLY_TOOL_NAMES),
                        system_suffix=PLAN_SUFFIX)

    def action_cmd_memory(self, arg: str = "") -> None:
        from coding_agent.core.context_builder import (
            GLOBAL_MEMORY_PATH,
            find_project_memory,
        )

        ws = settings.workspace_path
        proj_path, proj_label = find_project_memory(ws)
        lines = []
        lines.append(f"project ({proj_label or 'AGENT.md / CLAUDE.md'}): "
                     f"{'found' if proj_path else 'absent'}"
                     + (f" - {proj_path}" if proj_path else ""))
        found_global = GLOBAL_MEMORY_PATH.is_file()
        lines.append(f"global: {'found' if found_global else 'absent'}"
                     f" - {GLOBAL_MEMORY_PATH}")
        for ln in lines:
            core = self.query_one(AiCore)
            core.meta(ln)
        core.note("info", "edit these files to give the agent standing instructions")

    # ------------------------------------------------- sessions / settings

    @work(thread=True, group="io")
    def _load_sessions(self, limit: int = 50) -> None:
        """Fetch sessions newest-first and open the picker overlay (the single
        session-history surface since the project-panel box was removed)."""
        async def _list():
            async with async_session_maker() as db:
                sessions = await SessionService(db).list(limit=limit)
                out = []
                for s in sessions:
                    n_msgs = len(s.messages)
                    updated = getattr(s, "updated_at", None)
                    title = s.title
                    sid = s.id
                    out.append({"id": sid, "title": title,
                                "messages": n_msgs, "updated_at": updated})
                return out

        rows = asyncio.run(_list())
        self.call_from_thread(self._sessions_ready, rows)

    def _sessions_ready(self, rows: list[dict]) -> None:
        """Rows landed: push the picker overlay (the single session-history
        surface since the project-panel box was removed)."""
        if not rows:
            self.cmd_note("no sessions yet")
            return
        overlay = SessionsOverlay(rows, current_id=self.session_id)
        self.push_screen(overlay, callback=self._on_history_dismiss)

    def _on_history_dismiss(self, result) -> None:
        """Picker dismissed with a row (resume) or {"delete": row}."""
        if not result:
            return
        if isinstance(result, dict) and result.get("delete"):
            self.run_worker(self._delete_picked(result["delete"]),
                            group="io", exclusive=True)
            return
        self._adopt_session_row(result)

    def _adopt_session_row(self, row: dict | None) -> None:
        """Open a previous chat as its own chat: the view is cleared and that
        session's transcript renders alone - never appended under whatever
        conversation was already on screen. Refuses while a turn runs and
        no-ops on re-picking the chat already open."""
        if not row:
            return
        core = self.query_one(AiCore)
        if self.controller is not None and self.controller.running:
            core.note("warn",
                      "agent is working - ctrl+c interrupts before switching")
            return
        sid = row["id"]
        if sid == self.session_id:
            core.note("info", "already viewing this session")
            return
        # A different chat: swap every per-chat surface to the new session.
        core.clear_all()
        self._pending_cards.clear()
        self.checkpoints = CheckpointStore()
        if self.controller:
            self.controller.checkpoints = self.checkpoints
        self.tracker.reset()
        self.query_one(NexusHeader).update_info(ctx_pct=0.0, ctx_label="-")
        self.session_id = sid
        self.refresh_mode_line()
        self._restore_transcript()

    @work(thread=True, group="io")
    def _restore_transcript(self, max_messages: int = 40) -> None:
        """Load a session's recent history into the transcript as real blocks
        (user/assistant/tool cards). The view was already cleared by
        _adopt_session_row, so this IS the chat's own rendering - and a
        stale worker from a previous switch can never paint into it (the
        target id is checked at render time)."""
        target = self.session_id
        if target is None:
            return

        async def _collect():
            async with async_session_maker() as db:
                session = await SessionService(db).get(target)
                if not session:
                    return None
                items: list = []
                for m in session.messages[-max_messages:]:
                    role = m.role.value
                    if role == "system":
                        items.append(("note", "context briefing present"
                                      " (compacted history)"))
                        continue
                    if role == "user":
                        text = (m.content or "").strip()
                        if text:
                            items.append(("user", text))
                    elif role == "assistant":
                        for tc in m.tool_calls or []:
                            ok = tc.status is not None \
                                and tc.status.value == "success"
                            items.append(("tool", {
                                "name": tc.name,
                                "args": tc.arguments or {},
                                "result": tc.result or {},
                                "ok": ok,
                            }))
                        text = (m.content or "").strip()
                        if text:
                            items.append(("assistant", text))
                hidden = max(0, len(session.messages) - max_messages)
                return (items, hidden,
                        session.title, len(session.messages))

        collected = asyncio.run(_collect())
        if collected is None:
            self.cmd_note("session not found", "warn")
            return
        self.call_from_thread(self._render_restored, target, *collected)

    def _render_restored(self, target, items, hidden, title, total):
        """Paint the switched-to chat: header divider, then its blocks."""
        if self.session_id != target:
            return  # another switch already happened; this paint is stale
        core = self.query_one(AiCore)
        core.system_divider(
            f"session {str(target)[:8]} - {(title or 'untitled')[:40]}"
            f" - {total} message(s)")
        if hidden:
            core.meta(f"{hidden} older message(s) not shown"
                      " - /export for the full transcript")
        for kind, payload in items:
            if kind == "user":
                core.post_user(payload)
            elif kind == "assistant":
                core.add_finished_assistant(payload)
            elif kind == "tool":
                card = core.add_tool_card(
                    payload["name"], payload["args"])
                card.finish(payload["result"], payload["ok"], 0.0)
            else:
                core.meta(str(payload))

        def _pin() -> None:
            if core._at_bottom():
                core.scroll_end(animate=False)
        core.call_after_refresh(_pin)

    async def _delete_picked(self, row: dict) -> None:
        """Confirm + delete a session chosen for deletion in the picker."""
        sid = row["id"]
        answer = await self._push_for_result(ConfirmModal(
            "Delete session",
            [f"id {str(sid)[:8]} - {(row.get('title') or '(untitled)')[:40]}",
             "", "this cannot be undone"],
        ))
        if not answer:
            await self._reopen_history()
            return
        async with async_session_maker() as db:
            ok = await SessionService(db).delete(sid)
        core = self.query_one(AiCore)
        if ok:
            if self.session_id == sid:
                self._fresh_session_state()
                core.clear_all()
                core.meta("deleted the current session - fresh session started")
            else:
                core.note("success", f"deleted {str(sid)[:8]}")
        else:
            core.note("warn", "delete failed")
        await self._reopen_history()

    async def _reopen_history(self) -> None:
        self._load_sessions()

    def action_cmd_history(self, arg: str = "") -> None:
        self._load_sessions()

    def action_cmd_export(self, arg: str = "") -> None:
        from coding_agent.tui.commands import export_markdown

        sid = self.session_id
        if not sid:
            self.cmd_note("nothing to export yet - no session in progress")
            return

        async def _export():
            async with async_session_maker() as db:
                session = await SessionService(db).get(sid)
                if not session:
                    return None, "session not found"
                try:
                    out = export_markdown(session, arg or None)
                    return out, None
                except OSError as e:
                    return None, str(e)

        async def _run():
            out, err = await _export()
            core = self.query_one(AiCore)
            if err:
                core.note("warn", f"export failed: {err}")
            elif out:
                core.note("success", f"exported to {out}")

        self.run_worker(_run(), group="io", exclusive=True)

    def action_cmd_rename(self, arg: str = "") -> None:
        if not arg:
            self.cmd_note("usage: /rename <new title>")
            return
        if not self.session_id:
            self.cmd_note("no session in progress to rename")
            return

        async def _rename():
            async with async_session_maker() as db:
                await SessionService(db).set_title(self.session_id, arg)

        async def _run():
            await _rename()
            self.cmd_note(f"renamed current session to: {arg}", "success")

        self.run_worker(_run(), group="io", exclusive=True)

    def action_cmd_delete(self, arg: str = "") -> None:
        if not self.session_id:
            self.cmd_note("no session in progress - /delete needs a session")
            return
        self.run_worker(self._delete_current(), group="io", exclusive=True)

    async def _delete_current(self) -> None:
        sid = self.session_id
        if sid is None:
            self.cmd_note("no session in progress", "warn")
            return
        answer = await self._push_for_result(ConfirmModal(
            "Delete session",
            [f"id {str(sid)[:8]} and all its messages",
             "", "this cannot be undone"],
        ))
        if not answer:
            self.cmd_note("kept")
            return
        async with async_session_maker() as db:
            ok = await SessionService(db).delete(sid)
        if ok:
            self.cmd_note(f"deleted {str(self.session_id)[:8]}", "success")
            self._fresh_session_state()
        else:
            self.cmd_note("delete failed", "warn")

    def action_cmd_tools(self, arg: str = "") -> None:
        names = ", ".join(t.name for t in registry.list())
        self.query_one(AiCore).meta(f"tools: {names}")

    def action_cmd_settings(self, arg: str = "") -> None:
        core = self.query_one(AiCore)
        core.meta(f"permission mode : {self.permissions.mode.value}")
        core.meta(f"provider/model  : {self.provider_name or settings.DEFAULT_PROVIDER}"
                  f" / {self.model or settings.DEFAULT_MODEL}")
        core.meta(f"workspace       : {settings.workspace_path}")
        core.meta(f"context limit   : {settings.CONTEXT_TOKEN_LIMIT:,} "
                  f"(compact at {settings.COMPACT_THRESHOLD:.0%})")
        core.meta(f"max iterations  : {settings.AGENT_MAX_ITERATIONS}")
        core.meta(f"hooks           : {'loaded' if self.hook_runner else 'none'}")

    def action_cmd_clear(self, arg: str = "") -> None:
        self._fresh_session_state()
        core = self.query_one(AiCore)
        core.clear_all()
        core.meta("started a fresh session - approvals reset")
        self.refresh_mode_line()

    def _fresh_session_state(self) -> None:
        self.session_id = None
        self.checkpoints = CheckpointStore()
        if self.controller:
            self.controller.checkpoints = self.checkpoints
        self.permissions.reset_session()
        self.tracker.reset()
        bar = self.query_one(StateBar)
        bar.step = 0
        bar.end()

    # ------------------------------------------------------------- chrome

    def refresh_mode_line(self) -> None:
        dock = self.query_one(CommandDock)
        parts = [
            ("MODE", self.permissions.mode.value),
            ("SESSION", str(self.session_id)[:8] if self.session_id else "new"),
            ("AGENT", "busy" if self.busy else "idle"),
        ]
        if self._queue:
            parts.append(("QUEUE", str(len(self._queue))))
        dock.set_mode_line(parts)

    def action_cmd_new_project(self, arg: str = "") -> None:
        """Open the New Project modal → register + switch."""
        self.run_worker(self._new_project_flow(), exclusive=False)

    async def _new_project_flow(self) -> None:
        result = await self._push_for_result(NewProjectModal())
        if not result:
            return
        name = result["name"].strip()
        raw_path = result.get("path", "").strip()
        if not name:
            self.cmd_note("project name cannot be empty", "warn")
            return

        from pathlib import Path as _P
        if not raw_path:
            raw_path = str(settings.workspace_path.parent)

        p = _P(raw_path).expanduser().resolve()

        # If user specified a parent directory (its name != project name),
        # create a new subfolder with the project name.
        if p.name == name:
            project_dir = p
        else:
            project_dir = p / name

        try:
            project_dir.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            self.cmd_note(f"failed to create project directory '{project_dir}': {e}", "warn")
            return

        final_path = str(project_dir.resolve())
        registry = ProjectRegistry.load()
        registry.add_or_update(name, final_path)
        registry.save()
        self.cmd_note(f"project '{name}' created at {final_path} — switching…", "success")
        if self.busy:
            self.cmd_note("interrupting turn before project switch…", "warn")
            self.action_interrupt()
        self.set_timer(0.3, lambda: switch_project(final_path))

    def action_cmd_open_project(self, arg: str = "") -> None:
        """Open the project switcher overlay or prompt for directory if none registered."""
        registry = ProjectRegistry.load()
        projects = [{"name": p.name, "path": p.path} for p in registry.projects]
        if not projects:
            self.run_worker(self._open_dir_flow(), exclusive=False)
            return
        current = str(settings.workspace_path)
        overlay = ProjectSwitcherOverlay(projects, current_path=current)
        self.push_screen(overlay, callback=self._on_project_switch)

    async def _open_dir_flow(self) -> None:
        result = await self._push_for_result(OpenDirectoryModal())
        if not result:
            return
        from pathlib import Path as _P
        p = _P(result).expanduser().resolve()
        if not p.is_dir():
            self.cmd_note(f"'{p}' is not an existing directory", "warn")
            return
        final_path = str(p)
        name = p.name or final_path
        registry = ProjectRegistry.load()
        registry.add_or_update(name, final_path)
        registry.save()
        self.cmd_note(f"opening project '{name}' at {final_path} — switching…", "success")
        if self.busy:
            self.cmd_note("interrupting turn before project switch…", "warn")
            self.action_interrupt()
        self.set_timer(0.3, lambda: switch_project(final_path))

    def _on_project_switch(self, result) -> None:
        if not result:
            return
        if isinstance(result, dict) and result.get("remove"):
            path = result["remove"]
            registry = ProjectRegistry.load()
            registry.remove(path)
            registry.save()
            self.cmd_note(f"removed '{path}' from project list", "success")
            projects = [{"name": p.name, "path": p.path} for p in registry.projects]
            if projects:
                current = str(settings.workspace_path)
                overlay = ProjectSwitcherOverlay(projects, current_path=current)
                self.push_screen(overlay, callback=self._on_project_switch)
            return
        if isinstance(result, dict) and result.get("open_other"):
            self.run_worker(self._open_dir_flow(), exclusive=False)
            return
        # result is the new path string
        if result == str(settings.workspace_path):
            self.cmd_note("already in this project")
            return
        if self.busy:
            self.cmd_note("interrupting turn before project switch…", "warn")
            self.action_interrupt()
        self.set_timer(0.3, lambda: switch_project(result))


def run_tui(mode: str | None = None, continue_recent: bool = False,
            resume_prefix: str | None = None) -> None:
    """Launch the NEXUS console. Mirrors run_repl's startup hygiene: the TUI
    owns the whole screen, so log noise must go to a file, the DB must exist,
    and --continue/--resume resolve before the first frame paints."""
    import logging
    from pathlib import Path

    logging.getLogger("sqlalchemy.engine.Engine").disabled = True
    from loguru import logger as _logger

    _logger.remove()
    try:
        log_dir = Path.home() / ".agent"
        log_dir.mkdir(exist_ok=True)
        _logger.add(log_dir / "agent.log", rotation="1 MB", retention=3,
                    level="DEBUG")
    except OSError:
        pass

    asyncio.run(init_db())

    # ── Project auto-resume ───────────────────────────────────────────────────
    # If the user has a last_project recorded and WORKSPACE_ROOT is not already
    # pointing there (e.g. they launched from a different CWD), re-exec into it.
    import os as _os
    from coding_agent.services.project_service import ProjectRegistry as _PR2
    from coding_agent.services.project_switcher import switch_project as _sw

    _registry = _PR2.load()
    _last = _registry.last_project
    _current_ws = str(settings.workspace_path)
    if _last and _last != _current_ws and Path(_last).is_dir() \
            and not _os.environ.get("_AGT_NO_AUTORESUME"):
        # Set a guard to prevent infinite re-exec loops.
        _os.environ["_AGT_NO_AUTORESUME"] = "1"
        _sw(_last)   # does not return — re-execs
        return       # unreachable, but satisfies type checkers

    initial: uuid_mod.UUID | None = None
    pick_at_start = False
    if resume_prefix is not None:
        if resume_prefix == "":
            pick_at_start = True  # bare --resume opens the picker on boot
        else:
            from coding_agent.tui.commands import resolve_target

            async def _find():
                async with async_session_maker() as db:
                    sessions = await SessionService(db).list(limit=50)
                    target = resolve_target(sessions, resume_prefix)
                    return target.id if target else None

            initial = asyncio.run(_find())
            if not initial:
                print(f"no session matching '{resume_prefix}' - starting fresh")
    elif continue_recent:
        async def _recent():
            async with async_session_maker() as db:
                s = await SessionService(db).most_recent()
                return s.id if s else None

        initial = asyncio.run(_recent())
        if not initial:
            print("no previous session to continue - starting fresh")

    permissions = PermissionManager.from_mode(
        mode if mode is not None else settings.PERMISSION_MODE,
        prompt_fn=None,
    )
    app = CodeAgentApp(permissions=permissions, initial_session_id=initial)
    if pick_at_start:
        # NOT app._load_sessions() here: workers need the running message
        # pump, which only exists once App.run() starts. on_mount consumes it.
        app._open_picker_on_mount = True
    app.run()
