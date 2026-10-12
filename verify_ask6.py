"""Offline verification of Ask 6: context & token management (no API calls)."""
import asyncio
import json
import os
import sys
from pathlib import Path

os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///./test_compact.db"
sys.path.insert(0, str(Path(__file__).parent / "src"))

from coding_agent.config import settings  # noqa: E402
from coding_agent.core.llm.base import LLMMessage, LLMProvider, LLMResponse  # noqa: E402
from coding_agent.models.database import async_session_maker, engine, init_db  # noqa: E402
from coding_agent.models.orm import Role, ToolStatus  # noqa: E402
from coding_agent.models.schemas import (  # noqa: E402
    MessageCreate,
    SessionCreate,
    ToolCallCreate,
)
from coding_agent.services.chat_service import ChatService  # noqa: E402
from coding_agent.services.compactor import (  # noqa: E402
    estimate_messages_tokens,
    estimate_tokens,
    split_history,
)
from coding_agent.services.session_service import SessionService  # noqa: E402


class ScriptedProvider(LLMProvider):
    """complete(): returns the queued tool-call turns, then a final text turn.
    If the prompt asks for a summary (no tools passed), returns SUMMARY-TEXT.
    Records every history and counts summarize calls."""

    def __init__(self):
        self.queue: list = []          # list[dict | None]; None -> plain text turn
        self.histories: list[list[LLMMessage]] = []
        self.summarize_calls = 0
        self.last_summarize_prompt = ""

    @property
    def name(self): return "compact-fake"

    @property
    def models(self): return ["fake-1"]

    async def complete(self, messages, tools=None, model=None, temperature=0.7,
                       max_tokens=None):
        if tools is None or not tools:  # summarizer call
            self.summarize_calls += 1
            self.last_summarize_prompt = messages[-1].content
            return LLMResponse(content="SUMMARY-TEXT", usage={"input": 50, "output": 30})
        self.histories.append(list(messages))
        if self.queue:
            item = self.queue.pop(0)
            if item is None:
                return LLMResponse(content="final answer", usage={"input": 10, "output": 5})
            return LLMResponse(
                content="",
                tool_calls=[{"id": f"t{len(self.queue)}", "name": item["name"],
                             "arguments": item["arguments"]}],
                usage={"input": 10, "output": 5},
            )
        return LLMResponse(content="final answer", usage={"input": 10, "output": 5})

    async def stream_complete(self, messages, tools=None, model=None, temperature=0.7,
                              max_tokens=None):
        yield {"type": "text", "text": "stream-final"}
        yield {"type": "message_done", "usage": {"input": 7, "output": 3},
               "stop_reason": "end_turn"}


async def main():
    await init_db()
    import coding_agent.core.llm.factory as factory

    provider = ScriptedProvider()
    factory._providers["compact-fake"] = provider

    # ---- 1. estimator sanity
    assert estimate_tokens("") == 1 and estimate_tokens("x") == 1
    assert estimate_tokens("a" * 400) == 100
    h = [LLMMessage(role="user", content="u" * 80),
         LLMMessage(role="assistant", content="a" * 40)]
    est = estimate_messages_tokens(h)
    assert 25 <= est <= 40, est
    print("PASS token estimation (chars/4 heuristic + overhead)")

    # ---- 2. safe split: never starts kept on tool/assistant(tool_calls) rows
    body = [
        LLMMessage(role="user", content="q1"),
        LLMMessage(role="assistant", content="", tool_calls=[{"id": "1", "name": "read",
                                                              "arguments": {}}]),
        LLMMessage(role="tool", content="r1", tool_call_id="1"),
        LLMMessage(role="user", content="q2"),
        LLMMessage(role="assistant", content="a2"),
    ]
    sp = split_history(body, keep_recent=2)
    assert sp is not None
    older, kept = sp
    assert kept[0].role == "user" and kept[0].content == "q2"
    assert [m.content for m in older if m.role == "user"] == ["q1"]
    assert split_history(body[:3], keep_recent=5) is None  # nothing older to compact
    # pathological: no user boundary available at all
    no_user = [LLMMessage(role="assistant", content="x"),
               LLMMessage(role="user", content="y"),
               LLMMessage(role="assistant", content="", tool_calls=[
                   {"id": "9", "name": "read", "arguments": {}}]),
               LLMMessage(role="tool", content="z", tool_call_id="9")]
    sp2 = split_history(no_user, keep_recent=2)
    assert sp2 is None or sp2[1][0].role == "user"
    print("PASS split_history keeps pairs intact (walks back to a user boundary)")

    # ---- 3. tail truncation on rebuild; DB keeps the full result
    async with async_session_maker() as db:
        svc = SessionService(db)
        chat = ChatService(svc)
        session = await svc.create(SessionCreate())
        big_result = {"stdout": "L" * 50000, "returncode": 0}
        msg = await svc.add_message(session.id, MessageCreate(role=Role.ASSISTANT, content=""))
        tc = await svc.add_tool_call(msg.id, ToolCallCreate(
            name="bash", arguments={"command": "big"}, tool_use_id="tu_big"))
        await svc.update_tool_call(tc.id, big_result, ToolStatus.SUCCESS)
        rebuilt = chat._build_history(await svc.get(session.id))
        tool_msgs = [m for m in rebuilt if m.role == "tool"]
        assert len(tool_msgs) == 1
        limit = settings.HISTORY_OUTPUT_LIMIT
        assert len(tool_msgs[0].content) < limit + 200
        assert tool_msgs[0].content.startswith("[truncated head")
        assert big_result["stdout"][-100:] in tool_msgs[0].content, "tail must be kept"
        fresh_tc = (await svc.get(session.id)).messages[0].tool_calls[0]
        assert len(fresh_tc.result["stdout"]) == 50000, "full result must stay in DB"
    print("PASS history resend: tail-truncated for LLM, full result intact in DB")

    # ---- 4. auto-compaction: fires once enough history exists, persists summary
    settings.CONTEXT_TOKEN_LIMIT = 1000     # tiny window to force compaction
    settings.COMPACT_THRESHOLD = 0.75
    settings.COMPACT_KEEP_RECENT = 4

    async with async_session_maker() as db:
        svc = SessionService(db)
        chat = ChatService(svc)
        session = await svc.create(SessionCreate())
        sid = session.id
        filler = ("word " * 300).strip()      # ~1500 chars ~ ~375 tokens each turn

        calls_per_turn = []
        for i in range(6):
            provider.queue = [None]
            await chat.process_chat(message=filler + f" t{i}", session_id=sid,
                                    provider_name="compact-fake")
            calls_per_turn.append(provider.summarize_calls)

        assert all(c == 0 for c in calls_per_turn[:3]), \
            f"early turns must not compact: {calls_per_turn}"
        assert calls_per_turn[-1] >= 1, f"compaction must have fired by now: {calls_per_turn}"

        hist = provider.histories[-1]
        summary_sys = [m for m in hist if m.role == "system"][-1]
        assert "SUMMARY-TEXT" in summary_sys.content
        assert summary_sys.content.startswith("[Earlier conversation summary")

        meta = (await svc.get(sid)).session_metadata
        assert meta.get("compact_summary") == "SUMMARY-TEXT"
        assert meta.get("compact_summarized", 0) > 0
    print(f"PASS auto-compaction: fired at {calls_per_turn}, summary persisted & injected")

    # ---- 4b. suppression guard + prior-summary roll-forward (deterministic)
    base_calls = provider.summarize_calls   # counter carries over from check 4
    async with async_session_maker() as db:
        svc = SessionService(db)
        chat = ChatService(svc)
        session = await svc.create(SessionCreate())
        sid = session.id
        await svc.set_metadata(sid, {"compact_summary": "PRIOR-BRIEFING",
                                     "compact_summarized": 10})
        big = ("x" * 2000 + " ")               # ~500 tokens per message
        for i in range(12):                    # body=12 <= 10+keep -> guarded
            await svc.add_message(sid, MessageCreate(
                role=Role.USER if i % 2 == 0 else Role.ASSISTANT,
                content=f"{big} m{i}"))
        loaded = await svc.get(sid)
        prepared = chat._prepare_history(loaded, None)
        _, stats = await chat._maybe_compact(loaded, prepared, provider)
        assert stats is None and provider.summarize_calls == base_calls, \
            "guard must skip while few new messages since last summary"

        for i in range(6):                     # body=18 > 10+4 -> must run
            await svc.add_message(sid, MessageCreate(
                role=Role.USER if i % 2 == 0 else Role.ASSISTANT,
                content=f"{big} n{i}"))
        loaded = await svc.get(sid)
        prepared = chat._prepare_history(loaded, None)
        new_hist, stats = await chat._maybe_compact(loaded, prepared, provider)
        assert stats is not None and provider.summarize_calls == base_calls + 1
        assert "PRIOR-BRIEFING" in provider.last_summarize_prompt, \
            "previous briefing must be rolled into the summarizer input"
        assert any(m.role == "system" and "SUMMARY-TEXT" in m.content
                   for m in new_hist), "fresh summary replaces the old one"
    print("PASS suppression guard skips near-duplicate work; prior briefing rolled forward")

    # ---- 5. manual compact(): force path returns stats and shrinks context
    async with async_session_maker() as db:
        svc = SessionService(db)
        chat = ChatService(svc)
        session = await svc.create(SessionCreate())
        sid = session.id
        for i in range(6):
            provider.queue = [None]
            await chat.process_chat(message=("filler " * 200) + str(i), session_id=sid,
                                    provider_name="compact-fake")
        before_calls = provider.summarize_calls
        stats = await chat.compact(sid, provider_name="compact-fake")
        assert stats and stats["before_tokens"] > stats["after_tokens"], stats
        assert stats["summarized_messages"] > 0
        assert provider.summarize_calls > before_calls
        again = await chat.compact(sid, provider_name="compact-fake")
        assert again is None or again["after_tokens"] <= again["before_tokens"]
    print("PASS manual compact(): forced summarization with coherent stats")

    # ---- 6. streaming path emits context_tokens
    async with async_session_maker() as db:
        svc = SessionService(db)
        chat = ChatService(svc)
        events = [e async for e in chat.stream_chat(
            message="hello stream", provider_name="compact-fake")]
        td = [e for e in events if e["type"] == "turn_done"][-1]
        assert td["context_tokens"] > 0
    print("PASS streaming turn_done carries context tokens")

    print("\nALL ASK 6 CHECKS PASSED")


asyncio.run(main())
asyncio.run(engine.dispose())
Path("test_compact.db").unlink(missing_ok=True)
