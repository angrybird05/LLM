"""Offline verification of the OpenRouter provider fixes (no network).

Locks down two behaviors:
1. History reshaping: internal tool_calls ({id,name,arguments}) are converted
   to the OpenAI wire format ({id,type,function:{name,arguments-as-json-string}})
   before resending history. Passing internal dicts through verbatim made every
   follow-up LLM call fail with an opaque upstream 400 ("Provider returned
   error") — first calls worked because they carry no assistant tool_calls yet.
2. Credit 402s that state what the balance can afford ('...can only afford N.')
   are parsed so the call retries adaptively under that cap instead of failing.
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

from coding_agent.config import settings  # noqa: E402
settings.DEFAULT_MODEL = "test-model"

from coding_agent.core.llm.base import LLMMessage  # noqa: E402
from coding_agent.core.llm.openai import OpenAIProvider  # noqa: E402


class RecordingClient:
    """Stands in for AsyncOpenAI; records create() kwargs."""

    def __init__(self):
        self.calls: list[dict] = []
        self.fail_times = 0  # raise a credit 402 this many times first

        class Completions:
            async def create(comp_self, **kwargs):
                client = comp_self._client
                client.calls.append(kwargs)
                if client.fail_times > 0:
                    client.fail_times -= 1
                    raise Exception(
                        "Error code: 402 - {'error': {'message': 'This request "
                        "requires more credits, or fewer max_tokens. You "
                        f"requested up to {kwargs['max_tokens']} tokens, but can "
                        f"only afford {client.fail_times * 100 + 100}.', 'code': 402}}")
                class _Msg:
                    content = "ok"
                    tool_calls = None
                class _Choice:
                    message = _Msg()
                class _Usage:
                    prompt_tokens = 5
                    completion_tokens = 3
                class _Resp:
                    choices = [_Choice()]
                    usage = _Usage()
                return _Resp()

        self.chat = type("Chat", (), {"completions": Completions()})()
        self.chat.completions._client = self


def check(label: str, cond: bool) -> bool:
    print(("PASS " if cond else "FAIL ") + label)
    return cond


async def main() -> int:
    ok = True
    p = OpenAIProvider(via="openrouter")

    # --- wire-format conversion of resent history --------------------------
    tc = {"id": "abc-123", "name": "list", "arguments": {"path": "."}}
    msgs = [
        LLMMessage(role="user", content="hi"),
        LLMMessage(role="assistant", content="", tool_calls=[tc]),
        LLMMessage(role="tool", content='{"items":[]}', tool_call_id="abc-123"),
    ]
    converted = p._convert_messages(msgs)
    asst = converted[1]
    ok &= check("assistant tool_calls reshaped to wire format",
                asst["tool_calls"][0] == {
                    "id": "abc-123", "type": "function",
                    "function": {"name": "list", "arguments": '{"path": "."}'}})
    ok &= check("empty assistant content omitted",
                "content" not in asst)
    ok &= check("plain messages pass through unchanged",
                converted[0] == {"role": "user", "content": "hi"})
    ok &= check("tool result keeps id + content",
                converted[2] == {"role": "tool", "content": '{"items":[]}',
                                 "tool_call_id": "abc-123"})

    # --- credit affordance parsing + adaptive retry ------------------------
    m = OpenAIProvider._credit_affordance(Exception(
        "Error code: 402 - ... You requested up to 4096 tokens, "
        "but can only afford 122."))
    ok &= check("affordance parsed from real 402 text", m == 122)
    m2 = OpenAIProvider._credit_affordance(Exception("...can only afford 274."))
    ok &= check("affordance value extracted", m2 == 274)

    client = RecordingClient()
    p.client = client
    client.fail_times = 1  # fail once with afford=100, then succeed
    await p.complete(messages=[LLMMessage(role="user", content="x")], max_tokens=4096)
    ok &= check("402 triggers exactly one adaptive retry",
                len(client.calls) == 2 and client.calls[1]["max_tokens"] == 100)

    client.fail_times = 3  # exhausts retries -> error propagates
    try:
        await p.complete(messages=[LLMMessage(role="user", content="x")])
        ok &= check("persistent 402 still raises", False)
    except Exception:
        ok &= check("persistent 402 still raises", True)

    # --- list_models falls back offline ------------------------------------
    p._models_cache = None

    class FailingModels:
        def __aiter__(self):
            raise ConnectionError("offline")

    class OfflineClient(RecordingClient):
        def __init__(self):
            super().__init__()
            self.models = FailingModels()

    po = OpenAIProvider(via="openrouter")
    po.client = OfflineClient()
    fallback = await po.list_models()
    ok &= check("list_models falls back to static suggestions when offline",
                fallback == po.models and len(fallback) > 0)

    print("\nALL PASS" if ok else "\nFAILURES PRESENT")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
