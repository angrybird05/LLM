import builtins
from abc import ABC, abstractmethod
from typing import Any

from pydantic import BaseModel


class ToolParameter(BaseModel):
    type: str
    description: str
    enum: list[str] | None = None


class ToolSchema(BaseModel):
    type: str = "object"
    properties: dict[str, ToolParameter]
    required: list[str] = []


class Tool(ABC):
    @property
    @abstractmethod
    def name(self) -> str:
        pass

    @property
    @abstractmethod
    def description(self) -> str:
        pass

    @property
    @abstractmethod
    def parameters(self) -> ToolSchema:
        pass

    @abstractmethod
    async def execute(self, arguments: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
        pass


# Tools that cannot modify the workspace. Plan mode and subagents restrict
# the model to exactly this set.
READ_ONLY_TOOL_NAMES = {"read", "list", "glob", "grep", "web_fetch", "web_search", "find_symbols"}

# Semantic AST editing and refactoring tools
SEMANTIC_TOOL_NAMES = {"ast_edit", "ast_refactor", "find_symbols"}


class ToolRegistry:
    def __init__(self):
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool):
        self._tools[tool.name] = tool

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def list(self) -> list[Tool]:
        return list(self._tools.values())

    def get_schemas(self, tool_names: builtins.list[str] | None = None) -> builtins.list[dict]:
        tools = self._tools.values()
        if tool_names:
            tools = [t for t in tools if t.name in tool_names]
        return [{
            "name": t.name,
            "description": t.description,
            "parameters": t.parameters.model_dump()
        } for t in tools]


registry = ToolRegistry()
