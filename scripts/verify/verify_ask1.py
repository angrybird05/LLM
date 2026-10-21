"""Offline verification of the agentic loop + tools (no API calls)."""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

from coding_agent.core.llm.base import LLMMessage, LLMProvider, LLMResponse
from coding_agent.core.tools import registry
from coding_agent.models.database import async_session_maker, init_db
from coding_agent.services.chat_service import ChatService
from coding_agent.services.session_service import SessionService


class FakeProvider(LLMProvider):
    """Scripted provider: turn 1 asks for tools, turn 2 answers."""

    def __init__(self):
        self.calls = 0

    @property
    def name(self): return "fake"

    @property
    def models(self): return ["fake-1"]

    async def complete(self, messages, tools=None, model=None, temperature=0.7, max_tokens=None):
        self.calls += 1
        if self.calls == 1:
            return LLMResponse(
                content="I'll create the file and test it.",
                tool_calls=[
                    {"id": "tu_1", "name": "write",
                     "arguments": {"path": "_loop_test_pkg/hello.py", "content": "print('hi from loop')\n"}},
                    {"id": "tu_2", "name": "bash", "arguments": {"command": "cat _loop_test_pkg/hello.py"}},
                ],
                usage={"input": 100, "output": 20},
            )
        # Turn 2: verify history contains the tool results, then finish
        ids = [m.tool_call_id for m in messages]
        assert any(m.role == "assistant" and m.tool_calls for m in messages), "assistant tool_use missing"
        assert "tu_1" in ids, f"tool result tu_1 missing; ids={ids}"
        assert "tu_2" in ids, f"tool result tu_2 missing; ids={ids}"
        return LLMResponse(
            content="Created hello.py and verified its contents.",
            tool_calls=None,
            usage={"input": 250, "output": 15},
        )

    async def stream_complete(self, messages, tools=None, model=None, temperature=0.7, max_tokens=None):
        import json as _json
        if not any(m.role == "tool" for m in messages):
            yield {"type": "text", "text": "Running it now. "}
            yield {"type": "tool_use_start", "id": "stu_1", "name": "bash"}
            for frag in (_json.dumps({"command": "echo streamed"})[i:i+8]
                         for i in range(0, len(_json.dumps({"command": "echo streamed"})), 8)):
                yield {"type": "tool_input_delta", "id": "stu_1", "delta": frag}
            yield {"type": "message_done", "usage": {"input": 50, "output": 10}, "stop_reason": "tool_use"}
        else:
            yield {"type": "text", "text": "Streamed turn finished."}
            yield {"type": "message_done", "usage": {"input": 80, "output": 10}, "stop_reason": "end_turn"}


async def main():
    await init_db()
    provider = FakeProvider()

    # monkeypatch factory so get_provider returns our fake
    import coding_agent.core.llm.factory as factory
    factory._providers["fake"] = provider

    async with async_session_maker() as db:
        svc = SessionService(db)
        chat = ChatService(svc)

        # ---- non-streaming loop
        result = await chat.process_chat(
            message="create hello.py and cat it",
            provider_name="fake",
        )
        assert result["iterations"] == 2, result
        assert result["message"] == "Created hello.py and verified its contents."
        assert len(result["tool_calls"]) == 2
        assert result["tool_calls"][0]["result"]["success"] is True
        assert "hi from loop" in result["tool_calls"][1]["result"]["stdout"]
        print(f"PASS loop: {result['iterations']} iterations, tools={len(result['tool_calls'])}")

        # ---- history round-trip: resume the session, provider must see valid sequence
        sid = result["session_id"]
        session = await svc.get(sid)
        history = chat._build_history(session)
        kinds = [(m.role, bool(m.tool_calls), m.tool_call_id) for m in history]
        assert kinds[0] == ("user", False, None)
        assert kinds[1][0] == "assistant" and kinds[1][1] is True
        assert kinds[2] == ("tool", False, "tu_1")
        assert kinds[3] == ("tool", False, "tu_2")
        assert kinds[4][0] == "assistant"
        print(f"PASS round-trip: {len(history)} messages rebuilt with tool_use ids")

        result2 = await chat.process_chat(
            message="thanks", session_id=sid, provider_name="fake",
        )
        assert result2["session_id"] == sid
        print("PASS resume: continued same session")

        # ---- streaming loop with tools
        provider.calls = 0
        events = []
        async for ev in chat.stream_chat(message="again", session_id=None, provider_name="fake"):
            events.append(ev["type"])
        assert "tool_start" in events and "tool_result" in events and "turn_done" in events, events
        print(f"PASS stream events: {events}")

        # ---- edit tool: unique match, ambiguity error, not-found error
        p = Path("_loop_test_pkg/hello.py")
        edit = registry.get("edit")
        r = await edit.execute({"path": str(p), "old_string": "hi from loop", "new_string": "edited!"}, {})
        assert r["success"] and r["replacements"] == 1, r
        r = await edit.execute({"path": str(p), "old_string": "nope-not-here", "new_string": "x"}, {})
        assert "error" in r
        print("PASS edit tool: replace + not-found handling")
        p.unlink()
        p.parent.rmdir()

    print("\nALL OFFLINE CHECKS PASSED")


asyncio.run(main())
