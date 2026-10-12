import time
from collections.abc import AsyncGenerator

from anthropic import AsyncAnthropic
from loguru import logger

from coding_agent.config import settings
from coding_agent.core.llm.base import LLMMessage, LLMProvider, LLMResponse, ToolDefinition


class AnthropicProvider(LLMProvider):
    FALLBACK_MODELS = ["claude-sonnet-5", "claude-haiku-4-5-20251001", "claude-opus-5"]

    def __init__(self):
        self.client = AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY)
        self._models_cache: tuple[float, list[str]] | None = None

    @property
    def name(self) -> str:
        return "anthropic"

    @property
    def models(self) -> list[str]:
        cached = self._models_cache
        if cached and time.monotonic() - cached[0] < 300:
            return cached[1]
        return self.FALLBACK_MODELS

    async def list_models(self) -> list[str]:
        """Live model ids from the Anthropic API, cached for 5 minutes."""
        cached = self._models_cache
        if cached and time.monotonic() - cached[0] < 300:
            return cached[1]
        try:
            ids = [m.id async for m in await self.client.models.list(limit=100)]
            if ids:
                self._models_cache = (time.monotonic(), ids)
                return ids
        except Exception as e:  # offline/bad key — suggestions still work
            logger.debug(f"live model fetch failed for anthropic: {e}")
        return self.models

    def _convert_messages(self, messages: list[LLMMessage]) -> tuple[list[dict], str]:
        """Returns (messages, system) — system-role messages become the top-level
        `system` param the Anthropic API expects instead of a message."""
        result = []
        system_parts: list[str] = []
        for m in messages:
            if m.role == "system":
                system_parts.append(m.content)
            elif m.role == "tool":
                result.append({"role": "user", "content": [
                    {"type": "tool_result", "tool_use_id": m.tool_call_id, "content": m.content}
                ]})
            elif m.tool_calls:
                blocks = []
                if m.content:
                    blocks.append({"type": "text", "text": m.content})
                blocks.extend(
                    {"type": "tool_use", "id": tc["id"], "name": tc["name"],
                     "input": tc["arguments"]}
                    for tc in m.tool_calls
                )
                result.append({"role": "assistant", "content": blocks})
            else:
                result.append({"role": m.role, "content": m.content})
        return result, "\n\n".join(system_parts)

    def _convert_tools(self, tools: list[ToolDefinition] | None) -> list[dict] | None:
        if not tools:
            return None
        return [{
            "name": t.name,
            "description": t.description,
            "input_schema": t.parameters
        } for t in tools]

    async def complete(
        self,
        messages: list[LLMMessage],
        tools: list[ToolDefinition] | None = None,
        model: str | None = None,
        temperature: float = 0.7,
        max_tokens: int | None = None,
    ) -> LLMResponse:
        converted, system = self._convert_messages(messages)
        # Note: the current Anthropic Messages API has no `temperature` param;
        # the interface keeps it for other providers. Empty tools must be
        # omitted entirely rather than sent as null.
        kwargs: dict = {
            "model": model or settings.DEFAULT_MODEL,
            "messages": converted,
            "max_tokens": max_tokens or 4096,
        }
        if system:
            kwargs["system"] = system
        converted_tools = self._convert_tools(tools)
        if converted_tools:
            kwargs["tools"] = converted_tools
        response = await self.client.messages.create(**kwargs)

        content = ""
        tool_calls = []
        for block in response.content:
            if block.type == "text":
                content += block.text
            elif block.type == "tool_use":
                tool_calls.append({"id": block.id, "name": block.name, "arguments": block.input})

        return LLMResponse(
            content=content,
            tool_calls=tool_calls if tool_calls else None,
            usage={"input": response.usage.input_tokens, "output": response.usage.output_tokens}
        )

    async def stream_complete(
        self,
        messages: list[LLMMessage],
        tools: list[ToolDefinition] | None = None,
        model: str | None = None,
        temperature: float = 0.7,
        max_tokens: int | None = None,
    ) -> AsyncGenerator[dict, None]:
        converted, system = self._convert_messages(messages)
        kwargs: dict = {
            "model": model or settings.DEFAULT_MODEL,
            "messages": converted,
            "max_tokens": max_tokens or 4096,
        }
        if system:
            kwargs["system"] = system
        converted_tools = self._convert_tools(tools)
        if converted_tools:
            kwargs["tools"] = converted_tools
        async with self.client.messages.stream(**kwargs) as stream:
            tool_ids: dict[int, str] = {}  # block index -> tool_use id
            async for event in stream:
                etype = event.type
                if etype == "content_block_start":
                    block = event.content_block
                    if block.type == "tool_use":
                        tool_ids[event.index] = block.id
                        yield {"type": "tool_use_start", "id": block.id, "name": block.name}
                elif etype == "content_block_delta":
                    delta = event.delta
                    if delta.type == "text_delta":
                        yield {"type": "text", "text": delta.text}
                    elif delta.type == "input_json_delta":
                        # The delta carries no id itself — attribute it to the
                        # block it belongs to so the loop can assemble arguments.
                        yield {
                            "type": "tool_input_delta",
                            "id": tool_ids.get(event.index),
                            "delta": delta.partial_json,
                        }
                # message_start / content_block_stop / message_delta handled below via final message

            final = await stream.get_final_message()
            usage = {
                "input": final.usage.input_tokens,
                "output": final.usage.output_tokens,
            }
            yield {"type": "message_done", "usage": usage, "stop_reason": final.stop_reason}
