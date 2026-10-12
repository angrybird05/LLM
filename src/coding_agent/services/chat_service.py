"""Chat orchestration: the agentic loop.

The loop is the core of the agent: call the LLM -> if it asks for tools, run them,
feed results back -> repeat until the model produces a final answer (or the
iteration cap trips). Both process_chat and stream_chat drive the same loop;
streaming just yields events along the way.
"""
import asyncio
import json
import uuid
from collections.abc import AsyncGenerator, Awaitable, Callable
from typing import Any

from loguru import logger

from coding_agent.config import settings
from coding_agent.core.llm.base import LLMMessage, ToolDefinition
from coding_agent.core.llm.factory import get_provider
from coding_agent.core.permissions import RISKY_TOOLS
from coding_agent.core.tools.base import registry
from coding_agent.models.orm import Role, Session, ToolStatus
from coding_agent.models.schemas import MessageCreate, SessionCreate, ToolCallCreate
from coding_agent.services.compactor import (
    estimate_messages_tokens,
    split_history,
    summarize,
    summary_message,
)
from coding_agent.services.session_service import SessionService, derive_title

PermissionGate = Callable[[str, dict], Awaitable[bool]]


class ProviderTurnError(Exception):
    """A provider request failed the whole turn; `kind` drives the UI card."""

    def __init__(self, kind: str, detail: str):
        super().__init__(detail)
        self.kind = kind
        self.detail = detail


def classify_provider_error(exc: Exception) -> tuple[str, str]:
    """Map a provider exception to (kind, raw_detail).

    kinds: "credits" (balance/quota exhausted), "too_long" (prompt over the
    provider/context limit), "provider" (everything else).
    """
    text = str(exc) or exc.__class__.__name__
    low = text.lower()
    # Prompt-limit wording wins over credit wording: real OpenRouter 402s mix
    # both ("Prompt tokens limit exceeded … visit …/credits") and a smaller
    # prompt is something we can actually fix via compaction.
    if ("prompt tokens limit" in low or "context length" in low
            or "context_length_exceeded" in low or "maximum context" in low
            or "reduce the length" in low or "too large" in low):
        return "too_long", text
    if "402" in low or "credit" in low or "insufficient" in low or "quota" in low:
        return "credits", text
    return "provider", text


class ChatService:
    def __init__(
        self,
        session_service: SessionService,
        permission_gate: PermissionGate | None = None,
        pre_tool_hook: Callable[[str, dict], Awaitable[None]] | None = None,
        hook_runner=None,
    ):
        self.session_service = session_service
        # Optional async gate: (tool_name, arguments) -> allowed?
        # Used by the TUI/API layers to require approval before risky tools.
        self.permission_gate = permission_gate
        # Optional async observer: (tool_name, arguments) -> None, awaited after
        # approval but before execution (checkpoints use it to snapshot files).
        self.pre_tool_hook = pre_tool_hook
        # Optional core.hooks.HookRunner: user-configured shell commands around
        # every tool call; a failing pre hook vetoes the call.
        self.hook_runner = hook_runner

    # ------------------------------------------------------------------ helpers

    def _get_tool_definitions(self, tool_names: list[str] | None = None) -> list[ToolDefinition]:
        return [
            ToolDefinition(
                name=t.name, description=t.description,
                parameters=t.parameters.model_dump(),
            )
            for t in registry.list()
            if not tool_names or t.name in tool_names
        ]

    async def _get_or_create_session(self, session_id: uuid.UUID | None) -> Session:
        if session_id:
            session = await self.session_service.get(session_id)
            if not session:
                raise ValueError("Session not found")
            return session
        return await self.session_service.create(SessionCreate())

    @staticmethod
    def _truncate_tail(text: str, limit: int | None = None) -> str:
        """Keep the END of long output — errors/summaries live at the bottom."""
        limit = limit or settings.TOOL_OUTPUT_LIMIT
        if len(text) <= limit:
            return text
        return f"[truncated head, {len(text) - limit} chars omitted]\n" + text[-limit:]

    def _prepare_history(self, session: Session, system_prompt: str | None) -> list[LLMMessage]:
        """DB rows -> provider history, plus injected system prompt and any
        compaction summary persisted for this session."""
        history = self._build_history(session)
        if system_prompt:
            # Injected per turn, never persisted — rebuilt fresh next turn
            history.insert(0, LLMMessage(role="system", content=system_prompt))
        meta = session.session_metadata or {}
        prior = meta.get("compact_summary")
        if prior:
            history.insert(
                1 if system_prompt else 0,
                summary_message(prior, meta.get("compact_summarized") or 0),
            )
        return history

    async def _maybe_compact(
        self,
        session: Session,
        history: list[LLMMessage],
        provider,
        model: str | None = None,
        force: bool = False,
    ) -> tuple[list[LLMMessage], dict | None]:
        """Summarize older history when the context estimate crosses the
        threshold (or always, with force). Returns (history, stats|None)."""
        est = estimate_messages_tokens(history)
        # System messages are always a prefix here; they stay untouched while
        # the conversation body gets split at a safe user-message boundary.
        lead = 0
        while lead < len(history) and history[lead].role == "system":
            lead += 1
        body_len = len(history) - lead

        if not force:
            if est <= settings.CONTEXT_TOKEN_LIMIT * settings.COMPACT_THRESHOLD:
                return history, None
            # Full history is rebuilt from the DB every turn, so the estimate
            # alone would re-summarize on every turn once over the limit.
            # Only compact again after enough NEW material has piled up.
            prev = session.session_metadata or {}
            if body_len <= (prev.get("compact_summarized") or 0) + settings.COMPACT_KEEP_RECENT:
                return history, None

        sp = split_history(history[lead:])
        if not sp:
            return history, None
        older, kept = sp

        # `older` re-includes everything already covered by a previous summary
        # (history is rebuilt from the DB each turn), so the count is simply
        # its length; roll the prior briefing forward so information survives
        # successive compactions.
        n_summarized = sum(1 for m in older if m.role != "system")
        prior_text = (session.session_metadata or {}).get("compact_summary")
        if prior_text:
            older = [LLMMessage(
                role="system",
                content=f"Earlier briefing to merge and update:\n{prior_text}",
            )] + older

        text = await summarize(older, provider, model=model)
        if not text:
            return history, None
        await self.session_service.set_metadata(session.id, {
            "compact_summary": text,
            "compact_summarized": n_summarized,
        })
        # Replaces any previous summary message in the prefix.
        new_history = history[:lead] + [summary_message(text, n_summarized)] + kept
        return new_history, {
            "before_tokens": est,
            "after_tokens": estimate_messages_tokens(new_history),
            "summarized_messages": n_summarized,
        }

    async def compact(
        self,
        session_id: uuid.UUID,
        provider_name: str | None = None,
        model: str | None = None,
        system_prompt: str | None = None,
    ) -> dict | None:
        """Manual /compact entry point. Returns stats, or None when there was
        nothing to compact."""
        session = await self.session_service.get(session_id)
        if not session:
            return None
        provider = get_provider(provider_name)
        prepared = self._prepare_history(session, system_prompt)
        _, stats = await self._maybe_compact(
            session, prepared, provider, model=model, force=True,
        )
        return stats

    def _build_history(self, session: Session) -> list[LLMMessage]:
        """Rebuild provider-ready history from DB rows.

        Assistant turns carry their tool_use blocks; each persisted ToolCall row
        becomes the following `tool` message so providers see a valid sequence.
        """
        history: list[LLMMessage] = []
        for m in session.messages:
            if m.role == Role.USER:
                history.append(LLMMessage(role="user", content=m.content))
            elif m.role == Role.SYSTEM:
                history.append(LLMMessage(role="system", content=m.content))
            elif m.role == Role.ASSISTANT:
                tool_calls = [
                    {
                        "id": tc.tool_use_id or str(tc.id),
                        "name": tc.name,
                        "arguments": tc.arguments,
                    }
                    for tc in m.tool_calls
                ]
                if tool_calls:
                    history.append(LLMMessage(
                        role="assistant",
                        content=m.content or "",
                        tool_calls=tool_calls,
                    ))
                    for tc in m.tool_calls:
                        if tc.result is not None:
                            payload = json.dumps(tc.result, default=str)
                        elif tc.status == ToolStatus.ERROR:
                            payload = json.dumps({"error": "Tool execution failed"})
                        else:
                            payload = json.dumps({"error": "No result recorded"})
                        # Full result stays in the DB; only a tail is resent
                        # to the model on later turns.
                        history.append(LLMMessage(
                            role="tool",
                            content=self._truncate_tail(payload, settings.HISTORY_OUTPUT_LIMIT),
                            tool_call_id=tc.tool_use_id or str(tc.id),
                        ))
                elif m.content:
                    history.append(LLMMessage(role="assistant", content=m.content))
        return history

    def _is_parallel_safe(self, tc: dict) -> bool:
        """True when a call may run alongside its neighbours: read-only tools
        never prompt (the gate auto-allows them) and have no ordering needs.
        Risky tools and user hooks stay strictly sequential."""
        return (
            self.hook_runner is None
            and isinstance(tc, dict)
            and tc.get("name") not in RISKY_TOOLS
        )

    async def _run_tool(
        self, name: str, arguments: dict, session_id: uuid.UUID,
        provider=None, model: str | None = None,
        allowed_tools: frozenset[str] | None = None,
    ) -> dict:
        tool = registry.get(name)
        if not tool:
            available = [t.name for t in registry.list()]
            return {"error": f"Unknown tool: {name}. Available: {available}"}
        # Audit A4: the advertised tool list is a real clearance — a call for a
        # tool outside it is rejected at execution time, not just hidden from
        # the schemas (plan-mode turns feed untrusted web content to the LLM).
        if allowed_tools is not None and name not in allowed_tools:
            return {
                "error": (
                    f"Tool '{name}' is outside this turn's clearance. "
                    f"Allowed tools: {sorted(allowed_tools)}"
                ),
                "blocked_by_clearance": True,
            }
        if self.permission_gate and not await self.permission_gate(name, arguments):
            return {
                "error": "User declined this action. Do not retry it; adjust your approach.",
                "declined": True,
            }
        # Shared execution context; subagents reuse the provider, model and gate.
        context = {
            "session_id": str(session_id),
            "workspace_root": str(settings.workspace_path),
            "provider": provider,
            "model": model,
            "permission_gate": self.permission_gate,
        }
        if self.hook_runner:
            veto = await self.hook_runner.run_pre(name, arguments, context)
            if veto:
                return {"error": veto, "blocked_by_hook": True}
        if self.pre_tool_hook:
            try:
                await self.pre_tool_hook(name, arguments)
            except Exception:
                logger.exception(f"pre-tool hook failed for {name}")
        try:
            result = await tool.execute(arguments, context)
        except Exception as e:
            logger.exception(f"Tool {name} failed")
            result = {"error": f"{type(e).__name__}: {e}"}
        if self.hook_runner:
            status = "error" if "error" in result else "ok"
            try:
                await self.hook_runner.run_post(name, arguments, context, result, status)
            except Exception:
                logger.exception(f"post-tool hook failed for {name}")
        return result

    async def _record_assistant_turn(
        self, session_id: uuid.UUID, content: str, tool_calls: list[dict] | None
    ):
        """Persist an assistant message plus its tool_use blocks (with provider ids).

        Returns (message_row, row_ids): row_ids maps each provider tool_use id
        to its persisted ToolCall row id, so result updates skip the
        recent-pending SELECT entirely."""
        msg = await self.session_service.add_message(
            session_id, MessageCreate(role=Role.ASSISTANT, content=content)
        )
        row_ids: dict[str, uuid.UUID] = {}
        if tool_calls:
            for tc in tool_calls:
                row = await self.session_service.add_tool_call(msg.id, ToolCallCreate(
                    name=tc["name"],
                    arguments=tc.get("arguments", {}),
                    tool_use_id=tc.get("id"),
                ))
                key = tc.get("id") or str(row.id)
                row_ids[key] = row.id
        return msg, row_ids

    def _accumulate_usage(self, total: dict[str, int], usage: dict | None):
        if usage:
            total["input"] += usage.get("input") or 0
            total["output"] += usage.get("output") or 0

    async def _compact_and_rebuild(
        self, session_id: uuid.UUID, system_prompt: str | None,
        provider, model: str | None,
    ) -> list[LLMMessage] | None:
        """Force-compaction for error recovery: rebuild history from the DB
        (it already contains everything persisted this turn) and summarize the
        older part. Returns the shrunken history, or None when compaction is
        impossible / saved nothing — the caller then fails the turn cleanly."""
        fresh = await self.session_service.get(session_id)
        if not fresh:
            return None
        prepared = self._prepare_history(fresh, system_prompt)
        before = estimate_messages_tokens(prepared)
        history, stats = await self._maybe_compact(
            fresh, prepared, provider, model=model, force=True,
        )
        if not stats or estimate_messages_tokens(history) >= before:
            return None
        return history

    async def _ensure_title(self, session: Session, first_user_message: str):
        """Auto-title untitled sessions from their (new) first user message."""
        if not session.title and first_user_message.strip():
            await self.session_service.set_title(
                session.id, derive_title(first_user_message)
            )

    # ------------------------------------------------------- non-streaming path

    async def process_chat(
        self,
        message: str,
        session_id: uuid.UUID | None = None,
        provider_name: str | None = None,
        model: str | None = None,
        temperature: float = 0.7,
        max_tokens: int | None = None,
        tool_names: list[str] | None = None,
        system_prompt: str | None = None,
    ) -> dict[str, Any]:
        session = await self._get_or_create_session(session_id)

        # History is built from what is ALREADY persisted; the new user message
        # is stored after compaction and appended exactly once below. (It used
        # to be persisted first and then also appended by hand while the DB
        # rebuild already contained it — every request carried the prompt
        # twice and the model echoed the same context back.)
        provider = get_provider(provider_name)
        tools = self._get_tool_definitions(tool_names)
        # A4: execution-time enforcement of the turn's tool clearance.
        clearance = frozenset(tool_names) if tool_names else None
        history, _compact_stats = await self._maybe_compact(
            session,
            self._prepare_history(session, system_prompt),
            provider, model=model,
        )
        await self._ensure_title(session, message)
        await self.session_service.add_message(
            session.id, MessageCreate(role=Role.USER, content=message)
        )
        history.append(LLMMessage(role="user", content=message))

        total_usage = {"input": 0, "output": 0}
        all_tool_results: list[dict] = []
        final_content = ""
        iterations = 0
        rescued = False  # one compaction-retry for oversized/credit-limited calls

        while iterations < settings.AGENT_MAX_ITERATIONS:
            iterations += 1
            try:
                response = await provider.complete(
                    messages=history, tools=tools, model=model,
                    temperature=temperature, max_tokens=max_tokens,
                )
            except Exception as e:
                kind, detail = classify_provider_error(e)
                if not rescued and kind in ("credits", "too_long"):
                    # The prompt outgrew what the account/context allows —
                    # shrink it (summarize older history) and retry once.
                    try:
                        rebuilt = await self._compact_and_rebuild(
                            session.id, system_prompt, provider, model)
                    except Exception:
                        rebuilt = None
                    if rebuilt is not None:
                        history = rebuilt
                        rescued = True
                        continue
                raise ProviderTurnError(kind, detail) from e
            self._accumulate_usage(total_usage, response.usage)

            if not response.tool_calls:
                final_content = response.content
                await self._record_assistant_turn(session.id, final_content, None)
                break

            _, row_ids = await self._record_assistant_turn(
                session.id, response.content, response.tool_calls)
            history.append(LLMMessage(
                role="assistant", content=response.content, tool_calls=response.tool_calls,
            ))

            results: list[dict] = []
            i = 0
            while i < len(response.tool_calls):
                calls = response.tool_calls
                if self._is_parallel_safe(calls[i]):
                    j = i
                    while j < len(calls) and self._is_parallel_safe(calls[j]):
                        j += 1
                    batch = calls[i:j]
                    results.extend(await asyncio.gather(*[
                        self._run_tool(tc["name"], tc["arguments"], session.id,
                                       provider=provider, model=model,
                                       allowed_tools=clearance)
                        for tc in batch
                    ]))
                    i = j
                else:
                    results.append(await self._run_tool(
                        calls[i]["name"], calls[i]["arguments"], session.id,
                        provider=provider, model=model,
                        allowed_tools=clearance,
                    ))
                    i += 1

            for tc, result in zip(response.tool_calls, results, strict=True):
                all_tool_results.append({
                    "name": tc["name"], "arguments": tc["arguments"], "result": result,
                })
                status = ToolStatus.ERROR if "error" in result else ToolStatus.SUCCESS
                # Attach result to the persisted row created this turn
                await self._update_tool_call_row(
                    session.id, tc.get("id"), result, status, row_ids=row_ids)
                history.append(LLMMessage(
                    role="tool",
                    content=self._truncate_tail(json.dumps(result, default=str)),
                    tool_call_id=tc["id"],
                ))

        else:
            final_content = (
                f"[stopped] Reached the maximum of {settings.AGENT_MAX_ITERATIONS} tool iterations."
            )
            await self._record_assistant_turn(session.id, final_content, None)

        return {
            "session_id": session.id,
            "message": final_content,
            "tool_calls": all_tool_results,
            "iterations": iterations,
            "usage": total_usage,
            "context_tokens": estimate_messages_tokens(history),
        }

    async def _update_tool_call_row(
        self, session_id: uuid.UUID, tool_use_id: str | None, result: dict,
        status: ToolStatus, row_ids: dict[str, uuid.UUID] | None = None,
    ):
        """Attach a result to its persisted tool_call row. Fast path: the
        provider-id -> row-id map captured at creation; falls back to the
        recent-pending scan when the map is missing (older callers)."""
        rid = (row_ids or {}).get(tool_use_id) if tool_use_id is not None else None
        if rid is not None:
            await self.session_service.update_tool_call(rid, result, status)
            return
        rows = await self.session_service.recent_pending_tool_calls(session_id)
        for row in rows:
            if tool_use_id is None or row.tool_use_id == tool_use_id:
                await self.session_service.update_tool_call(row.id, result, status)
                return

    # ----------------------------------------------------------- streaming path

    async def stream_chat(
        self,
        message: str,
        session_id: uuid.UUID | None = None,
        provider_name: str | None = None,
        model: str | None = None,
        temperature: float = 0.7,
        max_tokens: int | None = None,
        tool_names: list[str] | None = None,
        system_prompt: str | None = None,
    ) -> AsyncGenerator[dict, None]:
        session = await self._get_or_create_session(session_id)
        yield {"type": "session", "session_id": str(session.id)}

        # Same ordering as process_chat: history comes from already-persisted
        # messages, then the user message is stored and appended exactly once.
        provider = get_provider(provider_name)
        tools = self._get_tool_definitions(tool_names)
        # A4: execution-time enforcement of the turn's tool clearance.
        clearance = frozenset(tool_names) if tool_names else None
        history, _compact_stats = await self._maybe_compact(
            session,
            self._prepare_history(session, system_prompt),
            provider, model=model,
        )
        await self._ensure_title(session, message)
        await self.session_service.add_message(
            session.id, MessageCreate(role=Role.USER, content=message)
        )
        history.append(LLMMessage(role="user", content=message))

        total_usage = {"input": 0, "output": 0}
        all_tool_results: list[dict] = []
        final_content = ""
        iterations = 0
        rescued = False  # one compaction-retry for oversized/credit-limited calls

        while iterations < settings.AGENT_MAX_ITERATIONS:
            iterations += 1
            text_parts: list[str] = []
            call_order: list[str] = []
            meta_by_id: dict[str, dict] = {}
            input_json_by_id: dict[str, str] = {}

            try:
                async for event in provider.stream_complete(
                    messages=history, tools=tools, model=model,
                    temperature=temperature, max_tokens=max_tokens,
                ):
                    etype = event["type"]
                    if etype == "text":
                        text_parts.append(event["text"])
                        yield event
                    elif etype == "tool_use_start":
                        call_order.append(event["id"])
                        meta_by_id[event["id"]] = {"id": event["id"], "name": event["name"]}
                        input_json_by_id[event["id"]] = ""
                        yield event
                    elif etype == "tool_input_delta" and event.get("id") in input_json_by_id:
                        input_json_by_id[event["id"]] += event["delta"]
                    elif etype == "message_done":
                        self._accumulate_usage(total_usage, event.get("usage"))
                        # Live metering: the UI sees tokens move after every
                        # LLM call, not only at turn_done. The context
                        # estimate lags by the in-flight response; turn_done
                        # remains authoritative.
                        yield {
                            "type": "usage",
                            "usage": dict(total_usage),
                            "context_tokens": estimate_messages_tokens(history),
                            "iterations": iterations,
                        }
            except Exception as e:
                kind, detail = classify_provider_error(e)
                if not rescued and kind in ("credits", "too_long"):
                    # Prompt outgrew the account/context allowance — compact
                    # the persisted history and retry the call exactly once.
                    try:
                        rebuilt = await self._compact_and_rebuild(
                            session.id, system_prompt, provider, model)
                    except Exception:
                        rebuilt = None
                    if rebuilt is not None:
                        history = rebuilt
                        rescued = True
                        yield {
                            "type": "info",
                            "message": "context shrank (auto-compacted) — retrying…",
                        }
                        continue
                raise ProviderTurnError(kind, detail) from e

            content = "".join(text_parts)
            tool_calls = []
            for tc_id in call_order:
                raw = input_json_by_id[tc_id].strip() or "{}"
                try:
                    arguments = json.loads(raw)
                except json.JSONDecodeError:
                    arguments = {"_raw": raw, "_error": "malformed tool arguments"}
                tool_calls.append({**meta_by_id[tc_id], "arguments": arguments})

            if not tool_calls:
                final_content = content
                await self._record_assistant_turn(session.id, final_content, None)
                break

            _, row_ids = await self._record_assistant_turn(session.id, content, tool_calls)
            history.append(LLMMessage(role="assistant", content=content, tool_calls=tool_calls))

            # Announce every call up front so tool cards appear in model order,
            # then execute. Consecutive read-only calls run CONCURRENTLY (their
            # gate check auto-allows without prompting); risky tools (bash /
            # write / edit) stay one-at-a-time so approval modals queue cleanly.
            # Results are consumed strictly in call order afterwards, keeping
            # card pairing and history order deterministic.
            for tc in tool_calls:
                yield {"type": "tool_start", "name": tc["name"], "arguments": tc["arguments"]}

            results: list[dict] = []
            i = 0
            while i < len(tool_calls):
                if self._is_parallel_safe(tool_calls[i]):
                    j = i
                    while j < len(tool_calls) and self._is_parallel_safe(tool_calls[j]):
                        j += 1
                    batch = tool_calls[i:j]
                    results.extend(await asyncio.gather(*[
                        self._run_tool(tc["name"], tc["arguments"], session.id,
                                       provider=provider, model=model,
                                       allowed_tools=clearance)
                        for tc in batch
                    ]))
                    i = j
                else:
                    results.append(await self._run_tool(
                        tool_calls[i]["name"], tool_calls[i]["arguments"], session.id,
                        provider=provider, model=model,
                        allowed_tools=clearance,
                    ))
                    i += 1

            for tc, result in zip(tool_calls, results, strict=True):
                all_tool_results.append({
                    "name": tc["name"], "arguments": tc["arguments"], "result": result,
                })
                status = ToolStatus.ERROR if "error" in result else ToolStatus.SUCCESS
                await self._update_tool_call_row(
                    session.id, tc.get("id"), result, status, row_ids=row_ids)
                yield {
                    "type": "tool_result",
                    "name": tc["name"],
                    "arguments": tc["arguments"],
                    "result": result,
                }
                history.append(LLMMessage(
                    role="tool",
                    content=self._truncate_tail(json.dumps(result, default=str)),
                    tool_call_id=tc["id"],
                ))

        else:
            final_content = (
                f"[stopped] Reached the maximum of {settings.AGENT_MAX_ITERATIONS} tool iterations."
            )
            await self._record_assistant_turn(session.id, final_content, None)

        yield {
            "type": "turn_done",
            "session_id": str(session.id),
            "message": final_content,
            "iterations": iterations,
            "usage": total_usage,
            "context_tokens": estimate_messages_tokens(history),
        }
