"""ast_edit tool: targeted syntax-node replacement, insertion, and deletion.

Modifies code by AST node identity rather than brittle exact string matching.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from coding_agent.config import settings
from coding_agent.core.semantic.index.symbols import extract_symbols
from coding_agent.core.semantic.models import FileEdit, Span
from coding_agent.core.semantic.parser import parse_file
from coding_agent.core.semantic.patch.diff import generate_unified_diff
from coding_agent.core.semantic.transform.formatter import get_line_indent, reindent_block
from coding_agent.core.semantic.transform.range_rewrite import (
    apply_byte_edits,
    delete_span,
    insert_after_span,
    insert_before_span,
    replace_node_body,
    replace_span,
)
from coding_agent.core.semantic.transaction import TransactionManager
from coding_agent.core.tools.base import Tool, ToolParameter, ToolSchema
from coding_agent.core.tools.paths import resolve_in_workspace


class ASTEditTool(Tool):
    """Perform structural syntax-node edits on a single file."""

    @property
    def name(self) -> str:
        return "ast_edit"

    @property
    def description(self) -> str:
        return (
            "Modify, replace, or insert a specific syntax node (function body, class method, "
            "decorator, statement) based on AST node identity rather than brittle string matching."
        )

    @property
    def parameters(self) -> ToolSchema:
        return ToolSchema(
            properties={
                "path": ToolParameter(type="string", description="File path to modify"),
                "action": ToolParameter(
                    type="string",
                    description="AST manipulation action",
                    enum=["replace_body", "replace_node", "insert_before", "insert_after", "delete_node"],
                ),
                "content": ToolParameter(
                    type="string",
                    description="New code content to insert or replace with",
                ),
                "target_symbol": ToolParameter(
                    type="string",
                    description="Target symbol or qualified name (e.g. 'ClassName.method_name' or 'function_name')",
                ),
                "node_type": ToolParameter(
                    type="string",
                    description="Syntax node type filter (e.g. 'function_definition', 'class_definition')",
                ),
                "line_number": ToolParameter(
                    type="integer",
                    description="Line number of target node (1-based)",
                ),
            },
            required=["path", "action", "content"],
        )

    async def execute(self, arguments: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
        try:
            path = resolve_in_workspace(arguments["path"])
        except ValueError as e:
            return {"error": str(e)}

        if not path.is_file():
            return {"error": f"File not found: {path}"}

        action = arguments["action"]
        content = arguments.get("content", "")
        target_symbol = arguments.get("target_symbol")
        node_type = arguments.get("node_type")
        line_number = arguments.get("line_number")

        parse_res = parse_file(path)
        if not parse_res:
            return {"error": f"Failed to parse {path.name}"}

        orig_source = parse_res.source_code

        # Locate target node span
        target_span = self._locate_target_span(
            parse_res,
            target_symbol=target_symbol,
            node_type=node_type,
            line_number=line_number,
        )

        if not target_span:
            return {
                "error": (
                    f"Could not locate target node in {path.name} "
                    f"(target_symbol={target_symbol}, line={line_number}, type={node_type})"
                )
            }

        # Apply the requested action
        try:
            if action == "replace_body":
                # Find body node of function/class/method
                body_span = self._locate_body_span(parse_res.root, target_span)
                if not body_span:
                    return {"error": f"Could not find body for node at line {target_span.start_line + 1}"}
                new_source = replace_node_body(orig_source, body_span, content, file_path=path)

            elif action == "replace_node":
                new_source = replace_span(orig_source, target_span, content, file_path=path)

            elif action == "insert_before":
                # Ensure indentation matches target node
                lines = orig_source.splitlines()
                indent = get_line_indent(lines[target_span.start_line]) if target_span.start_line < len(lines) else ""
                formatted = reindent_block(content, indent)
                if not formatted.endswith("\n"):
                    formatted += "\n"
                new_source = insert_before_span(orig_source, target_span, formatted, file_path=path)

            elif action == "insert_after":
                lines = orig_source.splitlines()
                indent = get_line_indent(lines[target_span.start_line]) if target_span.start_line < len(lines) else ""
                formatted = reindent_block(content, indent)
                if not formatted.startswith("\n"):
                    formatted = "\n" + formatted
                if not formatted.endswith("\n"):
                    formatted += "\n"
                new_source = insert_after_span(orig_source, target_span, formatted, file_path=path)

            elif action == "delete_node":
                new_source = delete_span(orig_source, target_span, file_path=path)

            else:
                return {"error": f"Unknown action: {action}"}

            # Generate diff
            diff = generate_unified_diff(orig_source, new_source, path)

            # Apply transaction atomically with validation
            checkpoint_store = context.get("checkpoint_store")
            tx_mgr = TransactionManager(checkpoint_store=checkpoint_store)
            tx_mgr.apply_file_contents({path: new_source})

            return {
                "success": True,
                "action": action,
                "file": str(path.relative_to(settings.workspace_path)),
                "diff": diff,
            }

        except Exception as e:
            return {"error": f"AST edit failed: {e}"}

    def _locate_target_span(
        self,
        parse_res,
        target_symbol: str | None,
        node_type: str | None,
        line_number: int | None,
    ) -> Span | None:
        """Find the span of the target node."""
        if target_symbol:
            symbols = extract_symbols(parse_res)
            for s in symbols:
                if s.name == target_symbol or s.qualified_name == target_symbol:
                    if line_number is None or s.span.start_line == line_number - 1:
                        return s.span

        # If line number provided, look for node at that line
        if line_number is not None:
            target_0 = line_number - 1

            def _find_by_line(n):
                if n.span.start_line == target_0:
                    if node_type is None or n.node_type == node_type:
                        return n.span
                for c in n.children:
                    res = _find_by_line(c)
                    if res:
                        return res
                return None

            return _find_by_line(parse_res.root)

        return None

    def _locate_body_span(self, root_node, target_span: Span) -> Span | None:
        """Find the body child span of the target node."""
        def _walk(n):
            if n.span.start_byte == target_span.start_byte and n.span.end_byte == target_span.end_byte:
                body = n.child_by_field_name("body") or n.child_by_type("block") or n.child_by_type("statement_block")
                if body:
                    return body.span
                # Check for block/compound_statement child
                for c in n.children:
                    if c.node_type in ("block", "statement_block", "compound_statement"):
                        return c.span
                return None
            for c in n.children:
                res = _walk(c)
                if res:
                    return res
            return None

        return _walk(root_node)
