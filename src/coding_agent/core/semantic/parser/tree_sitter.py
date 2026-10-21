"""Tree-sitter multi-language parser backend.

Wraps the tree-sitter Python bindings to parse source code into our unified
CSTNode representation. Supports incremental re-parsing via old_tree.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from loguru import logger

from coding_agent.core.semantic.models import Span
from coding_agent.core.semantic.parser.base import CSTNode, ParseResult, ParserInterface

# Grammar import mapping — each tree-sitter-<lang> package exposes a language() fn
_GRAMMAR_LOADERS: dict[str, tuple[str, str | None]] = {
    # language_id: (module_path, attr_or_sublang)
    "python": ("tree_sitter_python", None),
    "javascript": ("tree_sitter_javascript", None),
    "typescript": ("tree_sitter_typescript", "language_typescript"),
    "tsx": ("tree_sitter_typescript", "language_tsx"),
    "go": ("tree_sitter_go", None),
    "rust": ("tree_sitter_rust", None),
    "java": ("tree_sitter_java", None),
    "c": ("tree_sitter_c", None),
    "cpp": ("tree_sitter_cpp", None),
    "json": ("tree_sitter_json", None),
}


def _load_ts_language(language_id: str) -> Any:
    """Dynamically load a tree-sitter Language object for the given language_id."""
    import importlib
    import tree_sitter

    if language_id not in _GRAMMAR_LOADERS:
        raise ValueError(f"No tree-sitter grammar registered for '{language_id}'")

    module_path, sublang = _GRAMMAR_LOADERS[language_id]
    try:
        mod = importlib.import_module(module_path)
    except ImportError:
        raise ImportError(
            f"tree-sitter grammar package '{module_path}' is not installed. "
            f"Install it with: pip install {module_path.replace('_', '-')}"
        )

    if sublang:
        lang_fn = getattr(mod, sublang, None) or getattr(mod, "language", None)
    else:
        lang_fn = getattr(mod, "language", None)

    if lang_fn is None:
        raise AttributeError(f"Cannot find language() in {module_path}")

    raw_lang = lang_fn()
    return tree_sitter.Language(raw_lang)


def _ts_node_to_cst(node: Any, source: bytes, parent: CSTNode | None = None) -> CSTNode:
    """Recursively convert a tree-sitter Node into our CSTNode wrapper."""
    has_error = node.type in ("ERROR", "MISSING") or node.has_error

    span = Span(
        start_byte=node.start_byte,
        end_byte=node.end_byte,
        start_line=node.start_point[0],
        start_column=node.start_point[1],
        end_line=node.end_point[0],
        end_column=node.end_point[1],
    )

    text = source[node.start_byte:node.end_byte].decode("utf-8", errors="replace")

    cst_node = CSTNode(
        node_type=node.type,
        text=text,
        span=span,
        children=[],
        is_named=node.is_named,
        has_error=has_error,
        parent=parent,
        _raw=node,
    )
    cst_node.children = [_ts_node_to_cst(child, source, parent=cst_node) for child in node.children]
    return cst_node


class TreeSitterParser(ParserInterface):
    """Multi-language parser using tree-sitter."""

    def __init__(self) -> None:
        import tree_sitter
        self._parsers: dict[str, tree_sitter.Parser] = {}
        self._languages: dict[str, Any] = {}

    def _get_parser(self, language_id: str) -> Any:
        """Get or create a tree-sitter Parser for a language."""
        import tree_sitter

        if language_id not in self._parsers:
            lang = _load_ts_language(language_id)
            self._languages[language_id] = lang
            parser = tree_sitter.Parser(lang)
            self._parsers[language_id] = parser
        return self._parsers[language_id]

    def parse(self, source: str | bytes, language_id: str,
              file_path: Path | None = None,
              old_tree: Any | None = None) -> ParseResult:
        """Parse source code using tree-sitter."""
        if isinstance(source, str):
            source_bytes = source.encode("utf-8")
        else:
            source_bytes = source

        parser = self._get_parser(language_id)

        try:
            if old_tree is not None:
                tree = parser.parse(source_bytes, old_tree=old_tree)
            else:
                tree = parser.parse(source_bytes)
        except Exception as e:
            logger.error(f"Tree-sitter parse failed for {file_path}: {e}")
            raise

        root_node = tree.root_node
        root = _ts_node_to_cst(root_node, source_bytes)

        # Collect error ranges
        error_ranges: list[Span] = []
        _collect_errors(root, error_ranges)

        return ParseResult(
            root=root,
            source=source_bytes,
            language_id=language_id,
            file_path=file_path or Path("<string>"),
            has_errors=root.has_error,
            error_ranges=error_ranges,
            _raw_tree=tree,
        )

    def supports_language(self, language_id: str) -> bool:
        return language_id in _GRAMMAR_LOADERS

    def supported_languages(self) -> list[str]:
        return list(_GRAMMAR_LOADERS.keys())


def _collect_errors(node: CSTNode, errors: list[Span]) -> None:
    """Recursively collect error node spans."""
    if node.node_type in ("ERROR", "MISSING"):
        errors.append(node.span)
    for child in node.children:
        _collect_errors(child, errors)
