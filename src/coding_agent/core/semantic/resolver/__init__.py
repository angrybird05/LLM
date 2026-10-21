"""Semantic resolution facade.

Provides unified local and cross-file symbol and reference resolution.
"""
from __future__ import annotations

from pathlib import Path

from coding_agent.core.semantic.models import SymbolReference
from coding_agent.core.semantic.resolver.local import resolve_local_references
from coding_agent.core.semantic.resolver.lsp_client import LSPClientAdapter
from coding_agent.core.semantic.resolver.project import ProjectResolver


class SemanticResolver:
    """High-level semantic resolver for workspace symbol resolution."""

    def __init__(self, workspace_root: Path) -> None:
        self.workspace_root = workspace_root.resolve()
        self.project_resolver = ProjectResolver(self.workspace_root)

    def find_all_references(
        self,
        symbol_name: str,
        primary_file: Path,
        line_number: int | None = None,
        scope_only: bool = False,
    ) -> list[SymbolReference]:
        """Find references to a symbol, either local to the file or project-wide."""
        if scope_only:
            from coding_agent.core.semantic.parser import parse_file
            res = parse_file(primary_file)
            if not res:
                return []
            return resolve_local_references(res, target_name=symbol_name, target_line=line_number)

        return self.project_resolver.resolve_definition_and_references(
            symbol_name=symbol_name,
            primary_file=primary_file,
            line_number=line_number,
        )


__all__ = [
    "LSPClientAdapter",
    "ProjectResolver",
    "SemanticResolver",
    "resolve_local_references",
]
