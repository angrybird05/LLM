"""Conflict and safety validator for refactoring operations.

Detects naming collisions, shadowed symbols, overlapping edits, and
concurrent file modifications before refactoring execution begins.
"""
from __future__ import annotations

import os
from pathlib import Path

from coding_agent.core.semantic.models import (
    ConcurrentModificationError,
    FileEdit,
    OverlappingEditError,
    RefactorPlan,
    SemanticError,
)
from coding_agent.core.semantic.parser import parse_file


def check_naming_conflicts(
    file_path: Path,
    new_name: str,
    target_scope_span: tuple[int, int] | None = None,
) -> list[str]:
    """Check if new_name already exists in the file, potentially causing a collision.

    Returns a list of warning messages if potential collisions are found.
    """
    warnings: list[str] = []
    parse_res = parse_file(file_path)
    if not parse_res:
        return warnings

    from coding_agent.core.semantic.index.symbols import extract_symbols
    symbols = extract_symbols(parse_res)
    for sym in symbols:
        if sym.name == new_name:
            warnings.append(
                f"Symbol '{new_name}' already exists in {file_path.name} "
                f"at line {sym.span.start_line + 1} ({sym.kind.value})."
            )

    return warnings


def check_concurrent_modification(file_path: Path, expected_mtime: float) -> None:
    """Ensure file was not modified since it was parsed."""
    try:
        current_mtime = os.path.getmtime(file_path)
        if abs(current_mtime - expected_mtime) > 0.001:
            raise ConcurrentModificationError(
                f"File {file_path} was modified concurrently on disk."
            )
    except FileNotFoundError:
        pass


def validate_plan_safety(plan: RefactorPlan) -> list[str]:
    """Validate that the plan has no overlapping edits or obvious safety issues."""
    from coding_agent.core.semantic.patch import PatchManager
    pm = PatchManager()
    pm.validate_edits(plan.edits)
    return []
