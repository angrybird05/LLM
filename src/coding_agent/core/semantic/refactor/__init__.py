"""Refactoring engine facade.

Coordinates multi-file refactoring operations, safety checks, and diff generation.
"""
from __future__ import annotations

from pathlib import Path

from coding_agent.core.semantic.models import RefactorPlan
from coding_agent.core.semantic.patch import PatchManager
from coding_agent.core.semantic.refactor.conflict import (
    check_concurrent_modification,
    check_naming_conflicts,
    validate_plan_safety,
)
from coding_agent.core.semantic.refactor.operations import (
    plan_change_signature,
    plan_extract_function,
    plan_move_symbol,
    plan_rename,
)


class RefactoringCoordinator:
    """High-level coordinator for refactoring operations."""

    def __init__(self, workspace_root: Path) -> None:
        self.workspace_root = workspace_root.resolve()
        self.patch_manager = PatchManager()

    def rename(
        self,
        file_path: Path,
        symbol_name: str,
        new_name: str,
        line_number: int | None = None,
        scope_only: bool = False,
    ) -> tuple[RefactorPlan, str]:
        """Plan a symbol rename and generate unified diff preview."""
        plan = plan_rename(
            workspace_root=self.workspace_root,
            file_path=file_path,
            symbol_name=symbol_name,
            new_name=new_name,
            line_number=line_number,
            scope_only=scope_only,
        )
        _, diff_text = self.patch_manager.preview_plan(plan)
        return plan, diff_text

    def extract_function(
        self,
        file_path: Path,
        range_str: str,
        new_function_name: str,
    ) -> tuple[RefactorPlan, str]:
        """Plan a function extraction and generate unified diff preview."""
        plan = plan_extract_function(
            file_path=file_path,
            range_str=range_str,
            new_function_name=new_function_name,
        )
        _, diff_text = self.patch_manager.preview_plan(plan)
        return plan, diff_text

    def change_signature(
        self,
        file_path: Path,
        function_name: str,
        new_parameters: list[str],
        line_number: int | None = None,
    ) -> tuple[RefactorPlan, str]:
        """Plan a signature change and generate unified diff preview."""
        plan = plan_change_signature(
            file_path=file_path,
            function_name=function_name,
            new_parameters=new_parameters,
            line_number=line_number,
        )
        _, diff_text = self.patch_manager.preview_plan(plan)
        return plan, diff_text

    def move_symbol(
        self,
        source_path: Path,
        target_path: Path,
        symbol_name: str,
    ) -> tuple[RefactorPlan, str]:
        """Plan a symbol move and generate unified diff preview."""
        plan = plan_move_symbol(
            workspace_root=self.workspace_root,
            source_path=source_path,
            target_path=target_path,
            symbol_name=symbol_name,
        )
        _, diff_text = self.patch_manager.preview_plan(plan)
        return plan, diff_text


__all__ = [
    "RefactoringCoordinator",
    "check_concurrent_modification",
    "check_naming_conflicts",
    "plan_change_signature",
    "plan_extract_function",
    "plan_move_symbol",
    "plan_rename",
    "validate_plan_safety",
]
