from abc import ABC, abstractmethod
from collections.abc import AsyncGenerator
from typing import Any

from pydantic import BaseModel


class ToolDefinition(BaseModel):
    name: str
    description: str
    parameters: dict[str, Any]


class LLMMessage(BaseModel):
    role: str
    content: str
    tool_calls: list[dict] | None = None
    tool_call_id: str | None = None


class LLMResponse(BaseModel):
    content: str
    tool_calls: list[dict] | None = None
    usage: dict[str, int] | None = None


# Streaming contract: providers yield plain dicts so the agent loop and the TUI can
# react to text and tool calls as they arrive. Shapes:
#   {"type": "text", "text": str}
#   {"type": "tool_use_start", "id": str, "name": str}
#   {"type": "tool_input_delta", "id": str, "delta": str}   # raw JSON fragments
#   {"type": "message_done", "usage": dict|None, "stop_reason": str|None}


class LLMProvider(ABC):
    @abstractmethod
    async def complete(
        self,
        messages: list[LLMMessage],
        tools: list[ToolDefinition] | None = None,
        model: str | None = None,
        temperature: float = 0.7,
        max_tokens: int | None = None,
    ) -> LLMResponse:
        pass

    @abstractmethod
    async def stream_complete(
        self,
        messages: list[LLMMessage],
        tools: list[ToolDefinition] | None = None,
        model: str | None = None,
        temperature: float = 0.7,
        max_tokens: int | None = None,
    ) -> AsyncGenerator[dict, None]:
        """Yield StreamEvent dicts; must emit exactly one message_done at the end."""

    @property
    @abstractmethod
    def name(self) -> str:
        pass

    @property
    @abstractmethod
    def models(self) -> list[str]:
        """Static fallback list — used when a live fetch fails or hasn't run."""

    async def list_models(self) -> list[str]:
        """Model ids currently offered by the provider, fetched live when the
        provider supports it. Any id returned here is valid to pass as `model`
        to complete/stream_complete; unknown ids are never rejected locally."""
        return self.models
