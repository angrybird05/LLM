"""ast_refactor tool: safe multi-file structural refactoring.

Performs semantic refactorings (rename, extract_function, change_signature, move_symbol)
updating definitions, references, and imports across workspace files atomically.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from coding_agent.config import settings
from coding_agent.core.semantic.refactor import RefactoringCoordinator
from coding_agent.core.semantic.transaction import TransactionManager
from coding_agent.core.tools.base import Tool, ToolParameter, ToolSchema
from coding_agent.core.tools.paths import resolve_in_workspace


class ASTRefactorTool(Tool):
    """Perform multi-file structural refactoring operations."""

    @property
    def name(self) -> str:
        return "ast_refactor"

    @property
    def description(self) -> str:
        return (
            "Perform semantic code refactoring (rename symbol, extract function, change signature, "
            "move symbol). Updates definitions, references, and imports across files atomically."
        )

    @property
    def parameters(self) -> ToolSchema:
        return ToolSchema(
            properties={
                "operation": ToolParameter(
                    type="string",
                    description="Refactoring operation to perform",
                    enum=["rename", "extract_function", "change_signature", "move_symbol"],
                ),
                "path": ToolParameter(
                    type="string",
                    description="Primary file path containing the target definition",
                ),
                "symbol_name": ToolParameter(
                    type="string",
                    description="Target symbol identifier (function, class, method, or variable name)",
                ),
                "new_name": ToolParameter(
                    type="string",
                    description="New symbol identifier (required for 'rename')",
                ),
                "line_number": ToolParameter(
                    type="integer",
                    description="Line number of symbol definition to resolve ambiguities (1-based)",
                ),
                "range": ToolParameter(
                    type="string",
                    description="Line range 'start_line:end_line' (required for 'extract_function')",
                ),
                "target_path": ToolParameter(
                    type="string",
                    description="Destination file path for 'move_symbol'",
                ),
                "scope_only": ToolParameter(
                    type="boolean",
                    description="If true, restrict rename to local scope/file only without touching cross-file imports",
                ),
            },
            required=["operation", "path"],
        )

    async def execute(self, arguments: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
        try:
            path = resolve_in_workspace(arguments["path"])
        except ValueError as e:
            return {"error": str(e)}

        operation = arguments["operation"]
        symbol_name = arguments.get("symbol_name", "")
        new_name = arguments.get("new_name", "")
        line_number = arguments.get("line_number")
        range_str = arguments.get("range", "")
        target_path_str = arguments.get("target_path")
        scope_only = bool(arguments.get("scope_only", False))

        workspace_root = settings.workspace_path
        coordinator = RefactoringCoordinator(workspace_root)

        try:
            if operation == "rename":
                if not symbol_name or not new_name:
                    return {"error": "Both 'symbol_name' and 'new_name' are required for operation='rename'"}
                plan, diff_text = coordinator.rename(
                    file_path=path,
                    symbol_name=symbol_name,
                    new_name=new_name,
                    line_number=line_number,
                    scope_only=scope_only,
                )

            elif operation == "extract_function":
                if not range_str or not new_name:
                    fn_name = new_name or symbol_name
                    if not range_str or not fn_name:
                        return {"error": "'range' (e.g. '10:25') and 'new_name' are required for operation='extract_function'"}
                else:
                    fn_name = new_name
                plan, diff_text = coordinator.extract_function(
                    file_path=path,
                    range_str=range_str,
                    new_function_name=fn_name,
                )

            elif operation == "change_signature":
                if not symbol_name:
                    return {"error": "'symbol_name' is required for operation='change_signature'"}
                new_params = arguments.get("parameters", [])
                if isinstance(new_params, str):
                    new_params = [p.strip() for p in new_params.split(",") if p.strip()]
                plan, diff_text = coordinator.change_signature(
                    file_path=path,
                    function_name=symbol_name,
                    new_parameters=new_params,
                    line_number=line_number,
                )

            elif operation == "move_symbol":
                if not symbol_name or not target_path_str:
                    return {"error": "Both 'symbol_name' and 'target_path' are required for operation='move_symbol'"}
                try:
                    target_path = resolve_in_workspace(target_path_str)
                except ValueError as e:
                    return {"error": str(e)}
                plan, diff_text = coordinator.move_symbol(
                    source_path=path,
                    target_path=target_path,
                    symbol_name=symbol_name,
                )

            else:
                return {"error": f"Unknown operation '{operation}'"}

            # Execute transaction atomically
            checkpoint_store = context.get("checkpoint_store")
            tx_mgr = TransactionManager(checkpoint_store=checkpoint_store)
            tx_mgr.apply_plan(plan)

            rel_files = [
                str(f.relative_to(workspace_root)) if f.is_relative_to(workspace_root) else str(f)
                for f in plan.affected_files
            ]

            return {
                "success": True,
                "operation": operation,
                "description": plan.description,
                "affected_files": rel_files,
                "diff": diff_text,
            }

        except Exception as e:
            return {"error": f"Refactoring failed: {e}"}
