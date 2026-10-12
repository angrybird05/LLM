"""Task tool: spawn a child agent for focused, read-only sub-work.

Delegation keeps bulky exploration (grep sweeps, file tours, doc dives) out of
the main conversation — the child burns its own context and returns only a
final report. Children run a mini agentic loop restricted to READ_ONLY tools,
share the parent's permission gate, and never touch the database.
"""
import json
from typing import Any

from loguru import logger

from coding_agent.core.llm.base import LLMMessage, ToolDefinition
from coding_agent.core.tools.base import (
    READ_ONLY_TOOL_NAMES,
    Tool,
    ToolParameter,
    ToolSchema,
    registry,
)

MAX_SUBAGENT_ITERATIONS = 8
REPORT_CHAR_LIMIT = 6000

SUBAGENT_SYSTEM = """\
You are a focused research subagent inside a coding agent. You have ONLY
read-only tools: look things up, then report. Be concise and factual — the
parent agent will act on your report without seeing what you saw. Prefer
concrete answers (paths, line numbers, names) over narration."""

# Returned to the parent when a child asks for a tool outside its clearance
_SUBAGENT_TOOL_DENIED = {
    "error": "subagents may only use read-only tools; record this limitation in your report",
}


class SubagentTool(Tool):
    @property
    def name(self) -> str:
        return "task"

    @property
    def description(self) -> str:
        return (
            "Delegate a self-contained research or exploration sub-task to a child "
            "agent (e.g. 'find every caller of X', 'summarize how config is loaded'). "
            "The child has read-only tools and returns a final written report; use it "
            "to keep broad searches out of the main conversation."
        )

    @property
    def parameters(self) -> ToolSchema:
        return ToolSchema(
            properties={
                "prompt": ToolParameter(
                    type="string",
                    description="Full instructions for the child agent, including "
                    "what the final report should contain.",
                ),
            },
            required=["prompt"],
        )

    async def execute(self, arguments: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
        prompt = (arguments.get("prompt") or "").strip()
        if not prompt:
            return {"error": "Missing required argument 'prompt' (instructions for the subagent)."}

        provider = context.get("provider")
        if provider is None:
            from coding_agent.core.llm.factory import get_provider

            provider = get_provider(None)
        model = context.get("model")
        gate = context.get("permission_gate")

        defs = [
            ToolDefinition(name=t.name, description=t.description,
                           parameters=t.parameters.model_dump())
            for t in registry.list() if t.name in READ_ONLY_TOOL_NAMES
        ]
        history = [
            LLMMessage(role="system", content=SUBAGENT_SYSTEM),
            LLMMessage(role="user", content=prompt),
        ]

        final = ""
        iterations = 0
        while iterations < MAX_SUBAGENT_ITERATIONS:
            iterations += 1
            response = await provider.complete(messages=history, tools=defs, model=model)
            if not response.tool_calls:
                final = response.content
                break
            history.append(LLMMessage(
                role="assistant", content=response.content, tool_calls=response.tool_calls,
            ))
            for tc in response.tool_calls:
                result = await self._run_child_tool(tc, gate, context)
                history.append(LLMMessage(
                    role="tool",
                    content=json.dumps(result, default=str)[:512000],
                    tool_call_id=tc["id"],
                ))
        else:
            final = "[subagent stopped at the iteration cap without a final report]"

        if len(final) > REPORT_CHAR_LIMIT:
            logger.debug(f"subagent report clipped from {len(final)} chars")
            final = final[:REPORT_CHAR_LIMIT] + f"\n… [report clipped, {len(final)} chars total]"
        return {"report": final, "iterations": iterations}

    async def _run_child_tool(self, tc: dict, gate, context: dict) -> dict:
        name = tc["name"]
        arguments = tc.get("arguments", {})
        if name not in READ_ONLY_TOOL_NAMES:
            return dict(_SUBAGENT_TOOL_DENIED)
        if gate and not await gate(name, arguments):
            return {"error": "User declined this action.", "declined": True}
        tool = registry.get(name)
        if not tool:
            return {"error": f"Unknown tool: {name}"}
        try:
            return await tool.execute(arguments, context)
        except Exception as e:
            logger.warning(f"subagent tool {name} failed: {e}")
            return {"error": f"{type(e).__name__}: {e}"}


registry.register(SubagentTool())
