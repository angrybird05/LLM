"""Offline verification of the chat duplication hotfix (no API calls).

The bug: the user message was persisted, then history was rebuilt from the DB
(already containing it), and THEN the message was appended by hand again — so
every request carried the prompt twice and the model echoed/printed the same
context repeatedly. The provider here records exactly what it receives.
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

from coding_agent.core.llm.base import LLMMessage, LLMProvider, LLMResponse  # noqa: E402
from coding_agent.models.database import async_session_maker, init_db  # noqa: E402
from coding_agent.services.chat_service import ChatService  # noqa: E402
from coding_agent.services.session_service import SessionService  # noqa: E402

P1 = "hotfix probe message ONE"
P2 = "hotfix probe message TWO"


class CountingProvider(LLMProvider):
    """Records the user-role contents of every request; answers plainly."""

    def __init__(self):
        self.seen: list[list[str]] = []

    @property
    def name(self): return "counting"

    @property
    def models(self): return ["fake-1"]

    def _record(self, messages: list[LLMMessage]) -> LLMResponse:
        self.seen.append([m.content for m in messages if m.role == "user"])
        return LLMResponse(content="ok", tool_calls=None,
                           usage={"input": 1, "output": 1})

    async def complete(self, messages, tools=None, model=None,
                       temperature=0.7, max_tokens=None):
        return self._record(messages)

    async def stream_complete(self, messages, tools=None, model=None,
                              temperature=0.7, max_tokens=None):
        self._record(messages)
        yield {"type": "text", "text": "ok"}
        yield {"type": "message_done", "usage": {"input": 1, "output": 1},
               "stop_reason": "end_turn"}


def occurrences(prompt: str, users: list[str]) -> int:
    return sum(1 for c in users if c == prompt)


async def main():
    await init_db()
    provider = CountingProvider()
    import coding_agent.core.llm.factory as factory
    factory._providers["counting"] = provider

    async with async_session_maker() as db:
        chat = ChatService(SessionService(db))

        # ---- process_chat, fresh session: exactly one copy of the prompt
        provider.seen = []
        r1 = await chat.process_chat(message=P1, provider_name="counting")
        assert len(provider.seen) == 1, provider.seen
        assert occurrences(P1, provider.seen[0]) == 1, \
            f"prompt duplicated in request: {provider.seen[0]!r}"
        print("PASS process_chat fresh: prompt reaches the model exactly once")

        # ---- resume turn: old prompt once (DB rebuild), new prompt once
        provider.seen = []
        await chat.process_chat(message=P2, session_id=r1["session_id"],
                                provider_name="counting")
        users = provider.seen[0]
        assert occurrences(P1, users) == 1, f"old prompt duplicated: {users!r}"
        assert occurrences(P2, users) == 1, f"new prompt duplicated: {users!r}"
        print("PASS process_chat resume: both prompts appear exactly once")

        # ---- streaming path, fresh + resume
        import uuid as uuid_mod  # noqa: E402

        provider.seen = []
        sid = None
        async for ev in chat.stream_chat(message=P1, session_id=None,
                                         provider_name="counting"):
            if ev["type"] == "session":
                sid = uuid_mod.UUID(ev["session_id"])  # same as the TUI does
        assert occurrences(P1, provider.seen[0]) == 1, provider.seen[0]

        provider.seen = []
        async for _ in chat.stream_chat(message=P2, session_id=sid,
                                        provider_name="counting"):
            pass
        users = provider.seen[0]
        assert occurrences(P1, users) == 1 and occurrences(P2, users) == 1, users
        print("PASS stream_chat fresh+resume: prompts appear exactly once")

    print("\nALL DUPLICATION CHECKS PASSED")


asyncio.run(main())
