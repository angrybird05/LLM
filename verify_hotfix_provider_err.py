"""Offline verification of provider-error handling (no API calls, no network).

The bug this locks down: an OpenRouter 402/context-limit failure used to
surface as the raw nested-JSON dump, once per turn, with no recovery. Now the
service classifies the failure, tries ONE force-compaction retry, and raises
a typed ProviderTurnError that the TUI renders as a single actionable card.
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

from coding_agent.config import settings  # noqa: E402

# Small keep-recent so force-compaction has something to summarize offline.
settings.COMPACT_KEEP_RECENT = 2

from coding_agent.core.llm.base import LLMMessage, LLMProvider, LLMResponse  # noqa: E402
from coding_agent.models.database import async_session_maker, init_db  # noqa: E402
from coding_agent.services.chat_service import (  # noqa: E402
    ChatService,
    ProviderTurnError,
)
from coding_agent.services.session_service import SessionService  # noqa: E402
from coding_agent.tui import renderer  # noqa: E402
import rich.console  # noqa: E402

TOO_LONG = Exception(
    "Error code: 400 - {'error': {'message': 'Prompt tokens limit exceeded: "
    "14350 > 13783. To increase, visit openrouter.ai/settings/credits', "
    "'code': 400}, 'provider_name': None}"
)
CREDITS = Exception(
    "Error code: 402 - {'error': {'message': 'This request requires more "
    "credits, or fewer max_tokens. You requested up to 4096 tokens, but can "
    "only afford 122.', 'code': 402}}"
)


class ScriptedProvider(LLMProvider):
    """stream_complete pops scripted behaviors; complete() is the summarizer."""

    def __init__(self):
        self.stream_behaviors: list[tuple] = []
        self.complete_calls = 0

    @property
    def name(self): return "scripted"

    @property
    def models(self): return ["fake-1"]

    async def complete(self, messages, tools=None, model=None,
                       temperature=0.7, max_tokens=None):
        self.complete_calls += 1
        return LLMResponse(content="briefing of the earlier conversation",
                           tool_calls=None, usage={"input": 10, "output": 10})

    async def stream_complete(self, messages, tools=None, model=None,
                              temperature=0.7, max_tokens=None):
        behavior = self.stream_behaviors.pop(0)
        if behavior[0] == "raise":
            raise behavior[1]
        yield {"type": "text", "text": behavior[1]}
        yield {"type": "message_done", "usage": {"input": 10, "output": 5},
               "stop_reason": "end_turn"}


async def collect(chat, message, session_id):
    events = []
    async for ev in chat.stream_chat(message=message, session_id=session_id,
                                     provider_name="scripted"):
        events.append(ev)
    return events


async def main():
    import uuid as uuid_mod

    def sid_of(events):
        for ev in events:
            if ev["type"] == "session":
                return uuid_mod.UUID(ev["session_id"])  # same as the TUI does
        return None

    await init_db()
    provider = ScriptedProvider()
    import coding_agent.core.llm.factory as factory
    factory._providers["scripted"] = provider

    # ---- classification sanity
    from coding_agent.services.chat_service import classify_provider_error
    assert classify_provider_error(TOO_LONG)[0] == "too_long"
    assert classify_provider_error(CREDITS)[0] == "credits"
    assert classify_provider_error(Exception("connection reset"))[0] == "provider"
    print("PASS classify: 402/credit wording, prompt-limit wording, fallback")

    async with async_session_maker() as db:
        chat = ChatService(SessionService(db))

        # ---- seed a session with enough BULKY history that summarizing it
        # genuinely shrinks the token estimate (the rescue refuses a retry
        # when compaction wouldn't help)
        provider.stream_behaviors = [("ok", "first"), ("ok", "second")]
        bulky = "design decision and file detail " * 120  # ~3.9k chars each
        sid = None
        for msg in (f"seed one: {bulky}", f"seed two: {bulky}"):
            events = await collect(chat, msg, sid)
            sid = sid_of(events) or sid
        assert sid is not None

        # ---- scenario A: oversized request -> auto-compaction -> retry wins
        provider.complete_calls = 0
        provider.stream_behaviors = [("raise", TOO_LONG), ("ok", "rescued answer")]
        events = await collect(chat, "make the prompt smaller", sid)
        kinds = [e["type"] for e in events]
        assert "info" in kinds, f"retry notice missing: {kinds}"
        texts = [e["text"] for e in events if e["type"] == "text"]
        assert texts[-1] == "rescued answer", texts
        assert provider.complete_calls >= 1, "summarizer never ran"
        done = [e for e in events if e["type"] == "turn_done"][0]
        assert done["message"] == "rescued answer"
        print("PASS rescue: too-long request auto-compacted and retried once")

        # ---- scenario B: dead credits, nothing to compact -> clean typed fail
        provider.stream_behaviors = [("raise", CREDITS)]
        try:
            await collect(chat, "this will fail", None)
            raise AssertionError("credits exhaustion must raise ProviderTurnError")
        except ProviderTurnError as e:
            assert e.kind == "credits", e.kind
            assert "afford" in e.detail
        print("PASS credits: typed ProviderTurnError, no pointless retry")

        # ---- scenario C: rescue fires but the retry fails too -> still clean
        provider.stream_behaviors = [("ok", "c1"), ("ok", "c2")]
        sid_c = None
        for msg in (f"seed c1: {bulky}", f"seed c2: {bulky}"):
            events = await collect(chat, msg, sid_c)
            sid_c = sid_of(events) or sid_c
        provider.stream_behaviors = [("raise", TOO_LONG), ("raise", CREDITS)]
        before_complete = provider.complete_calls
        try:
            await collect(chat, "double failure", sid_c)
            raise AssertionError("second failure must raise")
        except ProviderTurnError as e:
            assert e.kind == "credits", e.kind  # the RETRY's classification
        assert provider.complete_calls == before_complete + 1, \
            "exactly one summarizer call — never a rescue loop"
        print("PASS retry-once: second failure surfaces cleanly, no rescue loop")

    # ---- renderer card: single headline + reason, no JSON wall
    renderer.console = rich.console.Console(force_interactive=False)
    _ANSI = __import__("re").compile(r"\x1b\[[0-9;]*m")

    def grab(fn, *a):
        with renderer.console.capture() as cap:
            fn(*a)
        return _ANSI.sub("", cap.get())

    out = grab(renderer.provider_error, "credits", str(CREDITS))
    assert "out of credits" in out
    assert "only afford 122" in out, "reason line missing"
    assert "top up" in out and "/compact" in out, "actionable hints missing"
    assert out.count("out of credits") == 1, "headline repeated"
    out = grab(renderer.provider_error, "too_long", str(TOO_LONG))
    assert "too large" in out and "13783" in out
    print("PASS renderer card: one headline, extracted reason, hints")

    print("\nALL PROVIDER-ERROR CHECKS PASSED")


asyncio.run(main())
