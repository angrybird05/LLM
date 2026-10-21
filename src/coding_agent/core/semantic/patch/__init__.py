"""Patch manager and overlap detector.

Collects, validates, and simulates FileEdit sequences across multiple files,
generating diff previews and ensuring edits do not collide.
"""
from __future__ import annotations

from pathlib import Path

from coding_agent.core.semantic.models import (
    FileEdit,
    OverlappingEditError,
    RefactorPlan,
)
from coding_agent.core.semantic.patch.diff import (
    generate_multi_file_diff,
    generate_unified_diff,
)
from coding_agent.core.semantic.transform.range_rewrite import apply_byte_edits


class PatchManager:
    """Manages and validates file edits across a refactoring plan."""

    def __init__(self) -> None:
        pass

    def validate_edits(self, edits: list[FileEdit]) -> None:
        """Validate that edits within the same file do not overlap."""
        by_file: dict[Path, list[FileEdit]] = {}
        for edit in edits:
            by_file.setdefault(edit.path.resolve(), []).append(edit)

        for path, file_edits in by_file.items():
            sorted_edits = sorted(file_edits, key=lambda e: (e.byte_start, e.byte_end))
            for i in range(len(sorted_edits) - 1):
                curr = sorted_edits[i]
                nxt = sorted_edits[i + 1]
                if curr.byte_end > nxt.byte_start:
                    raise OverlappingEditError(curr, nxt)

    def preview_plan(
        self,
        plan: RefactorPlan,
        file_contents: dict[Path, str] | None = None,
    ) -> tuple[dict[Path, str], str]:
        """Simulate applying the plan to file contents.

        Returns (dict of Path -> modified_content, unified_diff_string).
        """
        self.validate_edits(plan.edits)

        # Group edits by file
        by_file: dict[Path, list[FileEdit]] = {}
        for edit in plan.edits:
            by_file.setdefault(edit.path.resolve(), []).append(edit)

        modified_files: dict[Path, str] = {}
        diff_map: dict[Path, tuple[str, str]] = {}

        for path, edits in by_file.items():
            orig_text = ""
            if file_contents and path in file_contents:
                orig_text = file_contents[path]
            elif path.exists():
                orig_text = path.read_text(encoding="utf-8")

            new_text = apply_byte_edits(orig_text, edits)
            modified_files[path] = new_text
            diff_map[path] = (orig_text, new_text)

        combined_diff = generate_multi_file_diff(diff_map)
        return modified_files, combined_diff


__all__ = [
    "PatchManager",
    "generate_multi_file_diff",
    "generate_unified_diff",
]
