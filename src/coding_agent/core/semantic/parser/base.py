"""Abstract parser interface and node wrapper types.

Every language backend (Tree-sitter, LibCST) implements ParserInterface so
that upper layers work identically regardless of the underlying parse engine.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from coding_agent.core.semantic.models import Span


@dataclass
class CSTNode:
    """Unified wrapper around a concrete syntax tree node.

    Normalises differences between tree-sitter Node objects and LibCST nodes
    into a single interface for indexing and querying.
    """
    node_type: str           # e.g. "function_definition", "class_definition"
    text: str                # source text of this node
    span: Span
    children: list[CSTNode] = field(default_factory=list)
    is_named: bool = True    # tree-sitter named nodes vs anonymous tokens
    has_error: bool = False  # True when the parse tree contains error nodes
    parent: CSTNode | None = field(default=None, repr=False)

    # The raw backend node for backend-specific operations
    _raw: Any = field(default=None, repr=False)

    def child_by_field_name(self, name: str) -> CSTNode | None:
        """Find the child node corresponding to a tree-sitter field name."""
        if self._raw and hasattr(self._raw, "child_by_field_name"):
            raw_child = self._raw.child_by_field_name(name)
            if raw_child:
                for c in self.children:
                    if (c.span.start_byte == raw_child.start_byte
                            and c.span.end_byte == raw_child.end_byte
                            and c.node_type == raw_child.type):
                        return c
        return None

    def children_by_field_name(self, name: str) -> list[CSTNode]:
        """Find all child nodes corresponding to a tree-sitter field name."""
        if self._raw and hasattr(self._raw, "children_by_field_name"):
            raw_children = self._raw.children_by_field_name(name)
            res = []
            for rc in raw_children:
                for c in self.children:
                    if (c.span.start_byte == rc.start_byte
                            and c.span.end_byte == rc.end_byte
                            and c.node_type == rc.type):
                        res.append(c)
                        break
            return res
        return []

    def named_children(self) -> list[CSTNode]:
        """Return only named (non-anonymous) child nodes."""
        return [c for c in self.children if c.is_named]

    def child_by_type(self, node_type: str) -> CSTNode | None:
        """Find the first named child of a given type."""
        for c in self.children:
            if c.is_named and c.node_type == node_type:
                return c
        return None

    def children_by_type(self, node_type: str) -> list[CSTNode]:
        """Find all named children of a given type."""
        return [c for c in self.children if c.is_named and c.node_type == node_type]

    def find_descendant(self, node_type: str) -> CSTNode | None:
        """DFS to find the first descendant of a given type."""
        for c in self.children:
            if c.is_named and c.node_type == node_type:
                return c
            found = c.find_descendant(node_type)
            if found:
                return found
        return None

    def find_all_descendants(self, node_type: str) -> list[CSTNode]:
        """DFS to find all descendants of a given type."""
        results: list[CSTNode] = []
        for c in self.children:
            if c.is_named and c.node_type == node_type:
                results.append(c)
            results.extend(c.find_all_descendants(node_type))
        return results

    def node_at_line(self, line: int) -> CSTNode | None:
        """Find the deepest named node that contains a given line (0-indexed)."""
        if not (self.span.start_line <= line <= self.span.end_line):
            return None
        for c in self.children:
            if c.is_named:
                found = c.node_at_line(line)
                if found:
                    return found
        return self

    def node_at_byte(self, byte_offset: int) -> CSTNode | None:
        """Find the deepest named node containing a byte offset."""
        if not (self.span.start_byte <= byte_offset < self.span.end_byte):
            return None
        for c in self.children:
            if c.is_named:
                found = c.node_at_byte(byte_offset)
                if found:
                    return found
        return self


@dataclass
class ParseResult:
    """Result of parsing a source file."""
    root: CSTNode
    source: bytes                 # original source bytes
    language_id: str
    file_path: Path
    has_errors: bool = False      # True if the tree contains error nodes
    error_ranges: list[Span] = field(default_factory=list)

    # For incremental re-parsing (tree-sitter)
    _raw_tree: Any = field(default=None, repr=False)

    @property
    def source_code(self) -> str:
        """Return the decoded source code string."""
        return self.source.decode("utf-8", errors="replace")

    @property
    def raw_tree(self) -> Any:
        """Return the underlying tree-sitter or LibCST tree object."""
        return self._raw_tree


class ParserInterface(ABC):
    """Abstract interface for syntax parsers."""

    @abstractmethod
    def parse(self, source: str | bytes, language_id: str,
              file_path: Path | None = None,
              old_tree: Any | None = None) -> ParseResult:
        """Parse source code into a CST.

        Parameters:
            source: Source code as string or bytes.
            language_id: Language identifier (e.g. "python").
            file_path: Optional file path for diagnostics.
            old_tree: Previous parse tree for incremental re-parsing.

        Returns:
            ParseResult with the root CSTNode.
        """
        ...

    @abstractmethod
    def supports_language(self, language_id: str) -> bool:
        """Check if this parser backend supports a given language."""
        ...

    @abstractmethod
    def supported_languages(self) -> list[str]:
        """Return list of supported language IDs."""
        ...
