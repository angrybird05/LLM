"""AgentController: drives one agentic turn and emits AgentEvents.

The bridge between the backend loop and any frontend:

    controller = AgentController(sink=ui_callback, permissions=..., ...)
    worker = asyncio.create_task(controller.run("fix the auth bug"))
    ...
    controller.cancel()          # cooperative interrupt

Design notes:
- The loop itself stays in ChatService (verified across asks 1-10). This class
  only translates its stream_chat dict events into immutable dataclasses,
  measures turn metrics and implements cancellation.
- Cancellation is cooperative: the flag is checked at every yielded event, so
  it lands within milliseconds during streaming. While a permission modal is
  open, cancel() also resolves that prompt as "declined" so the gate returns
  promptly instead of blocking until the user answers.
- The sink receives every event; sink failures are logged, never fatal.
"""
import asyncio
import time
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

from loguru import logger

from coding_agent.agent.events import (
    AgentEvent,
    Notice,
    SessionResolved,
    StreamDelta,
    ToolFinished,
    ToolStarted,
    TurnCancelled,
    TurnFailed,
    TurnFinished,
    TurnStarted,
    UsageUpdated,
)
from coding_agent.config import settings
from coding_agent.core.context_builder import build_system_prompt
from coding_agent.core.hooks import HookRunner
from coding_agent.core.permissions import PermissionManager
from coding_agent.models.database import async_session_maker
from coding_agent.services.chat_service import ChatService, ProviderTurnError
from coding_agent.services.git_service import CheckpointStore, make_snapshot_hook
from coding_agent.services.session_service import SessionService

# Appended to the system prompt for /plan turns; combined with restricting the
# turn to READ_ONLY_TOOL_NAMES this yields research + a plan, zero mutations.
PLAN_SUFFIX = """

PLANNING MODE: the user asked for a plan, not changes. Research with read-only
tools as needed, then reply with a concise numbered step-by-step implementation
plan: files to touch, what changes in each, and how to verify. Do NOT write,
edit, or run anything that mutates the workspace."""

Sink = Callable[[AgentEvent], Awaitable[None]]


class _TurnAborted(Exception):
    """Internal: the cooperative-cancel flag fired."""


class AgentController:
    """Runs turns against ChatService and publishes AgentEvents to a sink."""

    def __init__(
        self,
        *,
        sink: Sink,
        permissions: PermissionManager,
        prompt_fn: Callable[[Any], Awaitable[str]] | None = None,
        checkpoints: CheckpointStore | None = None,
        hook_runner: HookRunner | None = None,
        provider_name: str | None = None,
        model: str | None = None,
    ):
        self.sink = sink
        self.permissions = permissions
        # The approval UI callable: PermissionRequest -> "yes"|"no"|"always".
        # Wire it into the manager too: PermissionManager(prompt_fn=ctrl.prompt_permission).
        self.prompt_fn = prompt_fn
        self.checkpoints = checkpoints or CheckpointStore()
        self.hook_runner = hook_runner
        self.provider_name = provider_name  # None -> DEFAULT_PROVIDER
        self.model = model                  # None -> provider default
        self.running = False
        self._cancelled = asyncio.Event()
        self._pending_prompt: asyncio.Future[str] | None = None

    # ------------------------------------------------------------- control

    def cancel(self) -> None:
        """Interrupt the running turn (and any pending approval prompt)."""
        self._cancelled.set()
        self._resolve_pending_prompt("no")

    def _resolve_pending_prompt(self, answer: str) -> None:
        fut = self._pending_prompt
        if fut is not None and not fut.done():
            fut.set_result(answer)
        self._pending_prompt = None

    async def emit(self, event: AgentEvent) -> None:
        try:
            await self.sink(event)
        except Exception:
            logger.exception("event sink failed")

    def _check_cancel(self) -> None:
        if self._cancelled.is_set():
            raise _TurnAborted()

    # ------------------------------------------------------------ approval

    async def _gate(self, name: str, arguments: dict) -> bool:
        """ChatService permission gate backed by the manager's prompt_fn
        (the UI supplies one that shows a modal)."""
        decision = await self.permissions.check(name, arguments)
        return decision.allowed

    async def prompt_permission(self, request) -> str:
        """Ask the UI via prompt_fn while remembering the pending future, so
        cancel() can resolve it. Returns yes|no|always."""
        prompt_fn = self.prompt_fn
        if prompt_fn is None:  # headless fail-safe, mirrors the REPL
            return "no"
        loop = asyncio.get_running_loop()
        fut: asyncio.Future[str] = loop.create_future()
        self._pending_prompt = fut
        try:
            answer = await prompt_fn(request)
        except (asyncio.CancelledError, EOFError, KeyboardInterrupt):
            answer = "no"
        finally:
            if self._pending_prompt is fut:
                self._pending_prompt = None
        if not fut.done():
            fut.set_result(answer)
        return answer

    # ---------------------------------------------------------------- turn

    async def run(
        self,
        text: str,
        session_id: uuid.UUID | None = None,
        *,
        planning: bool = False,
        tool_names: list[str] | None = None,
        system_suffix: str = "",
    ) -> None:
        """One full agentic turn. Events flow to the sink; raises nothing in
        normal operation (failures arrive as TurnFailed)."""
        if self.running:
            raise RuntimeError("a turn is already running")
        self.running = True
        self._cancelled.clear()
        started = time.monotonic()
        first_event_at: float | None = None
        terminal_sent = False
        final_message = ""
        iterations = 0
        usage: dict[str, int] = {"input": 0, "output": 0}
        context_tokens: int | None = None
        resolved_session: str | None = str(session_id) if session_id else None
        seen_calls: set[str] = set()
        tool_started_at: float | None = None
        pending_tool: tuple[str, dict] | None = None

        try:
            await self.emit(TurnStarted(text=text, planning=planning))
            async with async_session_maker() as db:
                chat = ChatService(
                    SessionService(db),
                    permission_gate=self._gate,
                    pre_tool_hook=make_snapshot_hook(self.checkpoints),
                    hook_runner=self.hook_runner,
                )
                # Built off the event loop: workspace walk + git snapshot cost
                # ~300ms, enough to visibly stall the UI at every turn start.
                system_prompt = await asyncio.to_thread(
                    build_system_prompt) + system_suffix
                async for ev in chat.stream_chat(
                    message=text,
                    session_id=session_id,
                    provider_name=self.provider_name,
                    model=self.model,
                    system_prompt=system_prompt,
                    tool_names=tool_names,
                ):
                    if first_event_at is None:
                        first_event_at = time.monotonic()
                    etype = ev["type"]
                    if etype == "session":
                        resolved_session = ev["session_id"]
                        await self.emit(SessionResolved(session_id=resolved_session))
                    elif etype == "text":
                        self._check_cancel()
                        await self.emit(StreamDelta(text=ev["text"]))
                    elif etype == "tool_use_start":
                        pass  # full detail arrives on tool_start
                    elif etype == "tool_start":
                        self._check_cancel()
                        key = repr((ev["name"], ev["arguments"]))
                        repeat = key in seen_calls
                        seen_calls.add(key)
                        tool_started_at = time.monotonic()
                        pending_tool = (ev["name"], ev["arguments"])
                        await self.emit(ToolStarted(
                            name=ev["name"], arguments=ev["arguments"], repeat=repeat,
                        ))
                    elif etype == "tool_result":
                        duration = (time.monotonic() - tool_started_at
                                    if tool_started_at else 0.0)
                        name, args = pending_tool or (ev["name"], ev.get("arguments", {}))
                        result: dict[str, Any] = ev["result"]
                        ok = "error" not in result
                        await self.emit(ToolFinished(
                            name=name, arguments=args, result=result,
                            ok=ok, duration_s=duration,
                        ))
                        pending_tool = None
                        tool_started_at = None
                        self._check_cancel()
                    elif etype == "info":
                        await self.emit(Notice(level="info", text=ev.get("message") or ""))
                    elif etype == "usage":
                        u = ev.get("usage") or {}
                        await self.emit(UsageUpdated(
                            input_tokens=u.get("input") or 0,
                            output_tokens=u.get("output") or 0,
                            context_tokens=ev.get("context_tokens"),
                            iterations=ev.get("iterations", 0),
                        ))
                    elif etype == "turn_done":
                        usage = ev.get("usage") or usage
                        iterations = ev.get("iterations", 0)
                        final_message = ev.get("message") or ""
                        context_tokens = ev.get("context_tokens")
        except _TurnAborted:
            terminal_sent = True
            await self.emit(TurnCancelled(elapsed_s=time.monotonic() - started))
        except ProviderTurnError as e:
            terminal_sent = True
            await self.emit(TurnFailed(
                kind=e.kind, detail=e.detail, elapsed_s=time.monotonic() - started,
            ))
        except asyncio.CancelledError:
            terminal_sent = True
            await self.emit(TurnCancelled(elapsed_s=time.monotonic() - started))
        except Exception as e:
            logger.exception("turn crashed")
            terminal_sent = True
            await self.emit(TurnFailed(
                kind="internal", detail=f"{type(e).__name__}: {e}",
                elapsed_s=time.monotonic() - started,
            ))
        finally:
            self.running = False
            self._cancelled.clear()

        if not terminal_sent:
            await self.emit(TurnFinished(
                session_id=resolved_session,
                message=final_message,
                iterations=iterations,
                usage=usage,
                context_tokens=context_tokens,
                elapsed_s=time.monotonic() - started,
                ttft_s=(first_event_at - started) if first_event_at else None,
            ))

    @property
    def settings_snapshot(self) -> dict[str, Any]:
        """Small header/status facts for the UI."""
        return {
            "provider": self.provider_name or settings.DEFAULT_PROVIDER,
            "model": self.model or settings.DEFAULT_MODEL,
            "workspace": str(settings.workspace_path),
        }
