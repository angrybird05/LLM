import json
import re
from collections.abc import AsyncGenerator

from loguru import logger
from openai import AsyncOpenAI

from coding_agent.config import settings
from coding_agent.core.llm.base import LLMMessage, LLMProvider, LLMResponse, ToolDefinition


class OpenAIProvider(LLMProvider):
    """OpenAI-compatible client. `via="openrouter"` pins the endpoint and key
    to OpenRouter relays regardless of which env keys are set; `via="openai"`
    keeps the legacy behavior (OpenRouter only as a fallback when no OpenAI
    key)."""

    # Shown when a live fetch fails or hasn't run; every entry is a real,
    # currently-served id. Any other id the provider recognizes is still
    # accepted — these are suggestions, not a whitelist.
    FALLBACK_MODELS = {
        "openai": ["gpt-4o", "gpt-4o-mini", "gpt-4.1", "gpt-4.1-mini"],
        "openrouter": [
            "stealth/ox-alpha",
            "anthropic/claude-sonnet-5",
            "openai/gpt-4.1",
            "google/gemini-2.5-pro",
            "meta-llama/llama-4-maverick",
        ],
    }

    def __init__(self, via: str = "openai"):
        self.via = via
        self._models_cache: list[str] | None = None
        if via == "openrouter":
            api_key = settings.OPENROUTER_API_KEY or settings.OPENAI_API_KEY
            base_url = "https://openrouter.ai/api/v1"
        else:
            api_key = settings.OPENAI_API_KEY or settings.OPENROUTER_API_KEY
            base_url = ("https://openrouter.ai/api/v1"
                        if not settings.OPENAI_API_KEY
                        and settings.OPENROUTER_API_KEY else None)
        self.client = AsyncOpenAI(
            api_key=api_key,
            base_url=base_url,
            # Explicit ceiling: a dead connection must not hang a turn for the
            # library default of 10 minutes. A plain float sidesteps this env's
            # dual httpx/httpx2 split. Retries stay at the default 2.
            timeout=300.0,
        )

    @property
    def name(self) -> str:
        return self.via

    @property
    def models(self) -> list[str]:
        return self.FALLBACK_MODELS.get(self.via, [])

    async def list_models(self) -> list[str]:
        """Fetch the live model catalog (hundreds of ids on OpenRouter) once
        per process, falling back to the static list when offline."""
        if self._models_cache is not None:
            return self._models_cache
        try:
            ids = [m.id async for m in self.client.models.list()]
            if ids:
                self._models_cache = sorted(ids)
                return self._models_cache
        except Exception as e:  # offline/bad key — suggestions still work
            logger.debug(f"live model fetch failed for {self.via}: {e}")
        return self.models

    def _convert_messages(self, messages: list[LLMMessage]) -> list[dict]:
        result = []
        for m in messages:
            if m.role == "tool":
                result.append({
                    "role": "tool", "content": m.content,
                    "tool_call_id": m.tool_call_id,
                })
            elif m.tool_calls:
                # Internal tool_calls ({id,name,arguments}) must be reshaped to
                # the OpenAI wire format before resending history — passing the
                # internal dicts through verbatim yields requests some upstream
                # providers reject with an opaque "Provider returned error".
                # Empty content stays omitted for the same reason.
                msg: dict = {
                    "role": "assistant",
                    "tool_calls": [{
                        "id": tc["id"],
                        "type": "function",
                        "function": {
                            "name": tc["name"],
                            "arguments": json.dumps(tc.get("arguments", {})),
                        },
                    } for tc in m.tool_calls],
                }
                if m.content:
                    msg["content"] = m.content
                result.append(msg)
            else:
                result.append({"role": m.role, "content": m.content})
        return result

    def _convert_tools(self, tools: list[ToolDefinition] | None) -> list[dict] | None:
        if not tools:
            return None
        return [{
            "type": "function",
            "function": {"name": t.name, "description": t.description, "parameters": t.parameters}
        } for t in tools]

    @staticmethod
    def _credit_affordance(exc: Exception) -> int | None:
        """OpenRouter 402s state what the balance still affords ('...can only
        afford 122.'). Parse it so the call can be retried under that cap."""
        m = re.search(r"can only afford (\d+)", str(exc))
        return int(m.group(1)) if m else None

    def _request_kwargs(self, messages, tools, model, temperature, max_tokens):
        return {
            "model": model or settings.DEFAULT_MODEL,
            "messages": self._convert_messages(messages),
            "tools": self._convert_tools(tools),
            "temperature": temperature,
            "max_tokens": max_tokens or 4096,
        }

    async def complete(
        self,
        messages: list[LLMMessage],
        tools: list[ToolDefinition] | None = None,
        model: str | None = None,
        temperature: float = 0.7,
        max_tokens: int | None = None,
    ) -> LLMResponse:
        kwargs = self._request_kwargs(messages, tools, model, temperature, max_tokens)
        try:
            response = await self.client.chat.completions.create(**kwargs)
        except Exception as e:
            afford = self._credit_affordance(e)
            if not afford or afford >= kwargs["max_tokens"]:
                raise
            # Balance can't reserve max_tokens — retry with exactly what it
            # can afford (the 402's own suggestion) instead of failing.
            logger.warning(f"{self.via}: retrying with max_tokens={afford} after credit 402")
            response = await self.client.chat.completions.create(
                **{**kwargs, "max_tokens": afford})

        msg = response.choices[0].message
        tool_calls = None
        if msg.tool_calls:
            tool_calls = [{
                "id": tc.id,
                "name": tc.function.name,
                "arguments": json.loads(tc.function.arguments),
            } for tc in msg.tool_calls]

        return LLMResponse(
            content=msg.content or "",
            tool_calls=tool_calls,
            usage={
                "input": response.usage.prompt_tokens,
                "output": response.usage.completion_tokens,
            },
        )

    async def stream_complete(
        self,
        messages: list[LLMMessage],
        tools: list[ToolDefinition] | None = None,
        model: str | None = None,
        temperature: float = 0.7,
        max_tokens: int | None = None,
    ) -> AsyncGenerator[dict, None]:
        kwargs = self._request_kwargs(messages, tools, model, temperature, max_tokens)
        try:
            stream = await self.client.chat.completions.create(**kwargs, stream=True)
        except Exception as e:
            afford = self._credit_affordance(e)
            if not afford or afford >= kwargs["max_tokens"] or afford < 16:
                raise
            # Same adaptive retry as complete(): honor the 402's "fewer
            # max_tokens" suggestion so near-zero balances still get short
            # replies instead of a hard failure.
            logger.warning(f"{self.via}: retrying stream with max_tokens={afford} after credit 402")
            stream = await self.client.chat.completions.create(
                **{**kwargs, "max_tokens": afford}, stream=True)
        usage = None
        stop_reason = None
        # index -> {"id", "name", "started"} for tool calls assembled from deltas
        pending: dict[int, dict] = {}
        async for chunk in stream:
            if getattr(chunk, "usage", None):
                usage = {
                    "input": getattr(chunk.usage, "prompt_tokens", 0) or 0,
                    "output": getattr(chunk.usage, "completion_tokens", 0) or 0,
                }
            if not chunk.choices:
                continue
            choice = chunk.choices[0]
            if choice.finish_reason:
                stop_reason = choice.finish_reason
            delta = choice.delta
            if delta is None:
                continue
            if delta.content:
                yield {"type": "text", "text": delta.content}
            for tc in delta.tool_calls or []:
                slot = pending.setdefault(tc.index, {"id": None, "name": "", "started": False})
                if tc.id:
                    slot["id"] = tc.id
                if tc.function and tc.function.name and not slot["name"]:
                    slot["name"] = tc.function.name
                if slot["id"] and slot["name"] and not slot["started"]:
                    slot["started"] = True
                    yield {"type": "tool_use_start", "id": slot["id"], "name": slot["name"]}
                if tc.function and tc.function.arguments:
                    yield {
                        "type": "tool_input_delta", "id": slot.get("id"),
                        "delta": tc.function.arguments,
                    }
        yield {"type": "message_done", "usage": usage, "stop_reason": stop_reason}
