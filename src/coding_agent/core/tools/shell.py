import asyncio
from typing import Any

from coding_agent.core.sandbox.docker import get_sandbox
from coding_agent.core.tools.base import Tool, ToolParameter, ToolSchema


class ShellTool(Tool):
    @property
    def name(self) -> str: return "bash"
    @property
    def description(self) -> str: return "Execute shell command in Docker sandbox"

    @property
    def parameters(self) -> ToolSchema:
        return ToolSchema(properties={
            "command": ToolParameter(type="string", description="Command to execute"),
            "cwd": ToolParameter(type="string", description="Working directory"),
            "timeout": ToolParameter(type="integer", description="Timeout in seconds"),
            "files": ToolParameter(type="object", description="Files to write before execution"),
        }, required=["command"])

    async def execute(self, arguments: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(
            None,
            lambda: get_sandbox().run_command(
                command=arguments["command"],
                cwd=arguments.get("cwd", "/workspace"),
                files=arguments.get("files"),
                timeout=arguments.get("timeout"),
            )
        )
        return result