"""Symbol and scope indexing facade.

Provides unified file-level and project-level indexers combining symbol discovery
and lexical scope hierarchy.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from coding_agent.core.semantic.index.scopes import (
    build_scope_tree,
    find_enclosing_scope,
    find_shadowed_bindings,
)
from coding_agent.core.semantic.index.symbols import extract_symbols
from coding_agent.core.semantic.models import (
    Binding,
    Scope,
    Symbol,
    SymbolKind,
)
from coding_agent.core.semantic.parser.base import ParseResult


@dataclass
class FileIndex:
    """Complete semantic index for a single source file."""
    file_path: Path
    language_id: str
    symbols: list[Symbol]
    root_scope: Scope
    _by_name: dict[str, list[Symbol]] = field(default_factory=dict, init=False, repr=False)
    _by_kind: dict[SymbolKind, list[Symbol]] = field(default_factory=dict, init=False, repr=False)

    def __post_init__(self) -> None:
        self._build_lookups()

    def _build_lookups(self) -> None:
        self._by_name.clear()
        self._by_kind.clear()
        for sym in self.symbols:
            self._by_name.setdefault(sym.name, []).append(sym)
            if sym.qualified_name and sym.qualified_name != sym.name:
                self._by_name.setdefault(sym.qualified_name, []).append(sym)
            self._by_kind.setdefault(sym.kind, []).append(sym)

    def find_symbol(self, name_or_qualified: str, kind: SymbolKind | None = None) -> list[Symbol]:
        """Find symbols by simple or qualified name, optionally filtering by kind."""
        matches = self._by_name.get(name_or_qualified, [])
        if kind is not None:
            matches = [s for s in matches if s.kind == kind]
        return matches

    def symbols_of_kind(self, kind: SymbolKind) -> list[Symbol]:
        """Return all symbols of a given kind."""
        return self._by_kind.get(kind, [])

    def scope_at(self, byte_offset: int) -> Scope:
        """Find the innermost scope at a byte offset."""
        return find_enclosing_scope(self.root_scope, byte_offset)

    def shadowed_bindings(self) -> list[tuple[Binding, Binding]]:
        """Return all shadowed variable/parameter bindings."""
        return find_shadowed_bindings(self.root_scope)


def index_file(parse_result: ParseResult) -> FileIndex:
    """Index symbols and scopes for a parsed file."""
    symbols = extract_symbols(parse_result)
    root_scope = build_scope_tree(parse_result)
    return FileIndex(
        file_path=parse_result.file_path,
        language_id=parse_result.language_id,
        symbols=symbols,
        root_scope=root_scope,
    )


class ProjectIndex:
    """In-memory cache of FileIndex objects across a project/workspace."""

    def __init__(self) -> None:
        self._files: dict[Path, FileIndex] = {}

    def update_file(self, file_index: FileIndex) -> None:
        self._files[file_index.file_path.resolve()] = file_index

    def remove_file(self, path: Path) -> None:
        self._files.pop(path.resolve(), None)

    def get_file(self, path: Path) -> FileIndex | None:
        return self._files.get(path.resolve())

    def find_symbols_globally(
        self,
        name: str,
        kind: SymbolKind | None = None,
    ) -> list[tuple[Path, Symbol]]:
        """Search for a symbol by name across all indexed files."""
        results: list[tuple[Path, Symbol]] = []
        for path, file_idx in self._files.items():
            for sym in file_idx.find_symbol(name, kind=kind):
                results.append((path, sym))
        return results

    def all_symbols(self) -> list[tuple[Path, Symbol]]:
        """Return all symbols in all indexed files."""
        results: list[tuple[Path, Symbol]] = []
        for path, file_idx in self._files.items():
            for sym in file_idx.symbols:
                results.append((path, sym))
        return results

    def clear(self) -> None:
        self._files.clear()


__all__ = [
    "FileIndex",
    "ProjectIndex",
    "build_scope_tree",
    "extract_symbols",
    "find_enclosing_scope",
    "find_shadowed_bindings",
    "index_file",
]
