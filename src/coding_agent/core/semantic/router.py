"""EditRouter: selects between text editing and semantic AST refactoring.

Routes editing operations to the optimal tool based on language support, file health,
and the structural complexity of the requested modification.
"""
from __future__ import annotations

from pathlib import Path

from coding_agent.core.semantic.detector import detect_language
from coding_agent.core.semantic.models import Capability, LanguageInfo
from coding_agent.core.semantic.parser import parse_file


class EditRouter:
    """Routes code edits to text-based or AST-based editing tools."""

    def __init__(self) -> None:
        pass

    def can_semantic_edit(self, file_path: Path) -> tuple[bool, str]:
        """Check if a file supports semantic AST editing.

        Returns (is_supported, reason).
        """
        lang_info: LanguageInfo = detect_language(file_path)
        if not (lang_info.capabilities & Capability.PARSE_CST):
            return False, f"File type {file_path.suffix} does not support AST parsing."

        # Verify file can be parsed without fatal syntax errors
        parse_res = parse_file(file_path)
        if not parse_res:
            return False, f"Failed to parse {file_path.name} into a syntax tree."

        if parse_res.has_errors:
            return False, f"{file_path.name} contains syntax errors. Fix errors or use standard 'edit' tool."

        return True, "Supported"

    def can_refactor(self, file_path: Path) -> tuple[bool, str]:
        """Check if a file supports structural refactorings (rename, extract, etc.)."""
        lang_info: LanguageInfo = detect_language(file_path)
        if not (lang_info.capabilities & (Capability.SCOPE_ANALYSIS | Capability.STRUCTURAL_REFACTOR)):
            return False, f"Language {lang_info.language_id} does not support scope-aware refactoring."

        return self.can_semantic_edit(file_path)

    def recommend_tool(self, file_path: Path, operation_intent: str) -> str:
        """Recommend the best tool ('ast_refactor', 'ast_edit', or 'edit').

        operation_intent can be: 'rename', 'extract', 'replace_node', 'text_tweak', etc.
        """
        if operation_intent in ("rename", "extract", "extract_function", "change_signature", "move"):
            can_ref, _ = self.can_refactor(file_path)
            return "ast_refactor" if can_ref else "edit"

        if operation_intent in ("replace_body", "replace_node", "insert_before", "insert_after", "delete_node"):
            can_ast, _ = self.can_semantic_edit(file_path)
            return "ast_edit" if can_ast else "edit"

        return "edit"
