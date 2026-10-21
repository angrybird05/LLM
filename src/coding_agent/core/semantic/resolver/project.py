"""Cross-file import and symbol reference resolver.

Traces symbol definitions and usages across multiple files in a workspace,
mapping module imports (Python, TypeScript/JS, Go, Rust) and finding all
call sites and references across the project.
"""
from __future__ import annotations

import os
from pathlib import Path

from coding_agent.core.semantic.index import FileIndex, ProjectIndex, index_file
from coding_agent.core.semantic.models import (
    ConfidenceLevel,
    ReferenceKind,
    Span,
    Symbol,
    SymbolKind,
    SymbolReference,
)
from coding_agent.core.semantic.parser import parse_file
from coding_agent.core.semantic.resolver.local import resolve_local_references


class ProjectResolver:
    """Resolves symbols and cross-file references across a workspace."""

    def __init__(self, workspace_root: Path, project_index: ProjectIndex | None = None) -> None:
        self.workspace_root = workspace_root.resolve()
        self.index = project_index or ProjectIndex()

    def resolve_definition_and_references(
        self,
        symbol_name: str,
        primary_file: Path,
        line_number: int | None = None,
        candidate_files: list[Path] | None = None,
    ) -> list[SymbolReference]:
        """Find definition and all cross-file references for a symbol.

        Returns all occurrences across the primary file and any importing files.
        """
        primary_file = primary_file.resolve()
        parse_res = parse_file(primary_file)
        if not parse_res:
            return []

        # 1. Local references in primary file
        all_refs = resolve_local_references(
            parse_res,
            target_name=symbol_name,
            target_line=line_number,
        )

        # 2. Check if the symbol is exported / module-level
        is_module_level = any(
            r.kind == ReferenceKind.DEFINITION and r.span.start_line == line_number
            for r in all_refs
        ) if line_number is not None else True

        # If it's strictly a local variable inside a function, no need to check other files
        if line_number is not None:
            # Check if target is inside a function scope
            from coding_agent.core.semantic.index.scopes import build_scope_tree, find_enclosing_scope
            root_s = build_scope_tree(parse_res)
            # Find definition ref
            def_refs = [r for r in all_refs if r.kind == ReferenceKind.DEFINITION]
            if def_refs:
                enc = find_enclosing_scope(root_s, def_refs[0].span.start_byte)
                from coding_agent.core.semantic.models import ScopeKind
                if enc.kind in (ScopeKind.FUNCTION, ScopeKind.BLOCK, ScopeKind.COMPREHENSION):
                    # Local variable: does not escape to other files
                    return all_refs

        # 3. Discover workspace files to check for cross-file references
        files_to_check = candidate_files or self._find_candidate_files(primary_file)

        # 4. For each file, check if it imports primary_file or references symbol_name
        rel_mod_name = self._compute_module_name(primary_file)
        file_stem = primary_file.stem

        for fpath in files_to_check:
            fpath = fpath.resolve()
            if fpath == primary_file:
                continue

            try:
                # Fast substring pre-check before expensive parsing
                content = fpath.read_text(encoding="utf-8", errors="ignore")
                if symbol_name not in content and file_stem not in content:
                    continue

                other_parse = parse_file(fpath)
                if not other_parse:
                    continue

                # Check if this file imports primary_file
                if self._imports_module(other_parse, file_stem, rel_mod_name):
                    # Find references in this file
                    other_refs = resolve_local_references(
                        other_parse,
                        target_name=symbol_name,
                    )
                    all_refs.extend(other_refs)
            except Exception:
                continue

        return all_refs

    def _find_candidate_files(self, primary_file: Path) -> list[Path]:
        """Collect source files in workspace of the same or compatible language."""
        suffix = primary_file.suffix.lower()
        candidates: list[Path] = []
        for root, dirs, files in os.walk(self.workspace_root):
            # Skip common ignore dirs
            dirs[:] = [d for d in dirs if not d.startswith(".") and d not in (
                "venv", "env", "node_modules", "__pycache__", "target", "build", "dist"
            )]
            for fname in files:
                p = Path(root) / fname
                if p.suffix.lower() == suffix:
                    candidates.append(p)
        return candidates

    def _compute_module_name(self, file_path: Path) -> str:
        """Compute the dotted Python module name or relative path."""
        try:
            rel = file_path.resolve().relative_to(self.workspace_root)
            parts = list(rel.parts)
            if parts[-1].endswith(".py"):
                parts[-1] = parts[-1][:-3]
            if parts[-1] == "__init__":
                parts.pop()
            return ".".join(parts)
        except ValueError:
            return file_path.stem

    def _imports_module(self, parse_result: ParseResult, file_stem: str, dotted_mod: str) -> bool:
        """Check if parse_result imports the target module."""
        source = parse_result.source_code
        if file_stem in source or dotted_mod in source:
            return True
        return False
