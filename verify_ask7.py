"""Offline verification of Ask 7: sessions, resume & history (no API calls)."""
import asyncio
import os
import shutil
import sys
import tempfile
import uuid
from datetime import datetime, timedelta
from pathlib import Path

os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///./test_sessions.db"
sys.path.insert(0, str(Path(__file__).parent / "src"))

from sqlalchemy import select  # noqa: E402

from coding_agent.core.llm.base import LLMProvider, LLMResponse  # noqa: E402
from coding_agent.models.database import async_session_maker, engine, init_db  # noqa: E402
from coding_agent.models.orm import Role, Session, ToolStatus  # noqa: E402
from coding_agent.models.schemas import (  # noqa: E402
    MessageCreate,
    SessionCreate,
    ToolCallCreate,
)
from coding_agent.services.chat_service import ChatService  # noqa: E402
from coding_agent.services.session_service import (  # noqa: E402
    SessionService,
    derive_title,
)
from coding_agent.tui.commands import export_markdown, resolve_target  # noqa: E402


class ScriptedProvider(LLMProvider):
    """Returns queued tool-call turns, then plain text."""

    def __init__(self):
        self.queue: list = []

    @property
    def name(self): return "sessions-fake"

    @property
    def models(self): return ["fake-1"]

    async def complete(self, messages, tools=None, model=None, temperature=0.7,
                       max_tokens=None):
        if self.queue:
            item = self.queue.pop(0)
            if item is not None:  # dict -> tool-call turn; None -> text turn
                return LLMResponse(
                    content="",
                    tool_calls=[{"id": "t1", "name": item["name"],
                                 "arguments": item["arguments"]}],
                    usage={"input": 10, "output": 5},
                )
        return LLMResponse(content="done", usage={"input": 10, "output": 5})

    async def stream_complete(self, messages, tools=None, model=None, temperature=0.7,
                              max_tokens=None):
        yield {"type": "text", "text": "ok"}
        yield {"type": "message_done", "usage": {"input": 7, "output": 3},
               "stop_reason": "end_turn"}


Path("test_sessions.db").unlink(missing_ok=True)  # a crashed earlier run leaves it


async def main():
    await init_db()
    import coding_agent.core.llm.factory as factory

    provider = ScriptedProvider()
    factory._providers["sessions-fake"] = provider

    async with async_session_maker() as db:
        svc = SessionService(db)

        # ---- 1. derive_title: deterministic fallback titling
        assert derive_title("hello world") == "hello world"
        assert derive_title("line one\nline two\ttabs") == "line one line two tabs"
        long = "fix the login bug where users randomly get logged out after a refresh"
        t = derive_title(long)
        assert t.endswith("…") and len(t) <= 41, t
        assert t[:-1] in long, "cut must not invent words"
        assert derive_title("   ") == "(untitled)"
        print(f"PASS derive_title fallback (e.g. {t!r})")

        # ---- 2. auto-title on first user message, both paths, set once
        chat = ChatService(svc)
        provider.queue = [None]
        res = await chat.process_chat(message=long, session_id=None,
                                      provider_name="sessions-fake")
        sid1 = res["session_id"]
        loaded = await svc.get(sid1)
        assert loaded.title == derive_title(long), loaded.title
        provider.queue = [None]
        await chat.process_chat(message="a completely different follow-up question",
                                session_id=sid1, provider_name="sessions-fake")
        assert (await svc.get(sid1)).title == derive_title(long), \
            "second turn must not overwrite the title"

        events = [e async for e in chat.stream_chat(
            message="explain the streaming design briefly",
            provider_name="sessions-fake")]
        sid2 = uuid.UUID([e for e in events if e["type"] == "session"][-1]["session_id"])
        assert (await svc.get(sid2)).title == "explain the streaming design briefly"
        print("PASS auto-titles from first user message (process + stream paths)")

        # ---- 3. most_recent() + deterministic recency ordering
        s_a = await svc.create(SessionCreate())
        s_b = await svc.create(SessionCreate())
        await svc.add_message(s_a.id, MessageCreate(role=Role.USER, content="in A"))
        await svc.add_message(s_b.id, MessageCreate(role=Role.USER, content="in B"))
        # SQLite timestamps have ~1s resolution; force an unambiguous order
        result = await db.execute(select(Session).where(Session.id == s_b.id))
        s_b_row = result.scalar_one()
        s_b_row.updated_at = datetime.utcnow() + timedelta(hours=1)
        await db.commit()

        mr = await svc.most_recent()
        assert mr.id == s_b.id, f"most_recent should be the bumped session: {mr.id}"
        assert mr.messages and mr.messages[0].content == "in B", \
            "most_recent must come back fully loaded"

        listed = await svc.list(limit=10)
        assert listed[0].id == s_b.id
        assert resolve_target(listed, "1").id == s_b.id, "row #1 = newest"
        assert resolve_target(listed, str(str(s_a.id))[:8]).id == s_a.id
        # an all-digit 8-char id prefix must resolve as an ID, not a row number
        digit_prefix = next((str(s.id)[:8] for s in listed if str(s.id)[:8].isdigit()), None)
        if digit_prefix:
            assert resolve_target(listed, digit_prefix) is not None, \
                "8-digit id prefixes are ids, not row numbers"
        assert resolve_target(listed, "99") is None   # out-of-range row number
        assert resolve_target(listed, "zzzzzzzz") is None  # 'z' is not hex
        assert resolve_target(listed, "") is None
    print("PASS most_recent + /sessions row-number & id-prefix resolution")

    # ---- 4. markdown export incl. tool calls and clipped results
    async with async_session_maker() as db:
        svc = SessionService(db)
        sid = sid1
        await svc.set_title(sid, "Export demo session")
        msg = await svc.add_message(sid, MessageCreate(
            role=Role.ASSISTANT, content="Running the failing test now."))
        tc = await svc.add_tool_call(msg.id, ToolCallCreate(
            name="bash", arguments={"command": "pytest -k NEEDLE_ARG"},
            tool_use_id="tu_x"))
        await svc.update_tool_call(tc.id, {"stdout": "R" * 90000, "returncode": 1},
                                   ToolStatus.ERROR)
        loaded = await svc.get(sid)

        outdir = tempfile.mkdtemp(prefix="agent_export_")
        try:
            path = export_markdown(loaded, outdir)          # dir -> auto name
            text = path.read_text(encoding="utf-8")
            assert "# Export demo session" in text
            assert "👤 You" in text and "🤖 Agent" in text
            assert "pytest -k NEEDLE_ARG" in text
            assert "chars clipped]" in text, "90k-char result must be clipped"
            named = export_markdown(loaded, str(Path(outdir) / "explicit.md"))
            assert named.name == "explicit.md" and named.exists()
            print(f"PASS export_markdown -> {path.name} (+ explicit path)")
        finally:
            shutil.rmtree(outdir, ignore_errors=True)

        # ---- 5. rename + delete round-trip
        await svc.set_title(s_a.id, "renamed-title")
        assert (await svc.get(s_a.id)).title == "renamed-title"
        assert await svc.delete(s_a.id) is True
        assert await svc.get(s_a.id) is None
        assert await svc.delete(s_a.id) is False  # second delete: nothing there
    print("PASS set_title + delete round-trip")

    print("\nALL ASK 7 CHECKS PASSED")


asyncio.run(main())
asyncio.run(engine.dispose())
Path("test_sessions.db").unlink(missing_ok=True)
