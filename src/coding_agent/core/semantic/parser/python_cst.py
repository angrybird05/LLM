"""LibCST parser backend for high-fidelity Python CST manipulation.

Uses LibCST when available for 100% comment- and whitespace-preserving
Python transformations. Falls back to tree-sitter for parsing if LibCST
is not installed.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from loguru import logger

from coding_agent.core.semantic.models import Span
from coding_agent.core.semantic.parser.base import CSTNode, ParseResult, ParserInterface


def _libcst_node_to_cst(node: Any, source: str, byte_offset: int = 0) -> CSTNode:
    """Convert a LibCST node to our CSTNode wrapper.

    LibCST doesn't track byte/line positions natively, so we use the
    code generator to get the text and compute spans from there.
    """
    import libcst as cst

    try:
        text = cst.parse_module("").code_for_node(node)
    except Exception:
        text = ""

    source_bytes = source.encode("utf-8")
    # Find position of this node's text in source
    start_byte = source_bytes.find(text.encode("utf-8"), byte_offset)
    if start_byte < 0:
        start_byte = byte_offset
    end_byte = start_byte + len(text.encode("utf-8"))

    # Compute line/column from byte offsets
    before = source_bytes[:start_byte].decode("utf-8", errors="replace")
    start_line = before.count("\n")
    last_nl = before.rfind("\n")
    start_col = len(before) - last_nl - 1 if last_nl >= 0 else len(before)

    before_end = source_bytes[:end_byte].decode("utf-8", errors="replace")
    end_line = before_end.count("\n")
    last_nl_end = before_end.rfind("\n")
    end_col = len(before_end) - last_nl_end - 1 if last_nl_end >= 0 else len(before_end)

    span = Span(
        start_byte=start_byte,
        end_byte=end_byte,
        start_line=start_line,
        start_column=start_col,
        end_line=end_line,
        end_column=end_col,
    )

    # Determine node type
    node_type = type(node).__name__

    return CSTNode(
        node_type=node_type,
        text=text,
        span=span,
        children=[],
        is_named=True,
        has_error=False,
        _raw=node,
    )


class PythonCSTParser(ParserInterface):
    """LibCST-based parser for Python files.

    Provides higher-fidelity CST than tree-sitter for Python specifically,
    preserving comments, whitespace, and formatting details.
    """

    def parse(self, source: str | bytes, language_id: str,
              file_path: Path | None = None,
              old_tree: Any | None = None) -> ParseResult:
        """Parse Python source using LibCST."""
        if language_id != "python":
            raise ValueError(f"PythonCSTParser only supports Python, got '{language_id}'")

        import libcst as cst

        if isinstance(source, bytes):
            source_str = source.decode("utf-8")
            source_bytes = source
        else:
            source_str = source
            source_bytes = source.encode("utf-8")

        try:
            module = cst.parse_module(source_str)
        except cst.ParserSyntaxError as e:
            logger.warning(f"LibCST parse error in {file_path}: {e}")
            # Return a parse result with error info
            error_span = Span(
                start_byte=0, end_byte=len(source_bytes),
                start_line=e.lines[0] if hasattr(e, 'lines') else 0,
                start_column=0,
                end_line=e.lines[0] if hasattr(e, 'lines') else 0,
                end_column=0,
            )
            root = CSTNode(
                node_type="module",
                text=source_str,
                span=Span(0, len(source_bytes), 0, 0,
                          source_str.count("\n"), 0),
                has_error=True,
                _raw=None,
            )
            return ParseResult(
                root=root,
                source=source_bytes,
                language_id="python",
                file_path=file_path or Path("<string>"),
                has_errors=True,
                error_ranges=[error_span],
                _raw_tree=None,
            )

        # Build root CSTNode
        root = CSTNode(
            node_type="module",
            text=source_str,
            span=Span(0, len(source_bytes), 0, 0,
                      source_str.count("\n"), len(source_str.rsplit("\n", 1)[-1])),
            has_error=False,
            _raw=module,
        )

        return ParseResult(
            root=root,
            source=source_bytes,
            language_id="python",
            file_path=file_path or Path("<string>"),
            has_errors=False,
            error_ranges=[],
            _raw_tree=module,
        )

    def supports_language(self, language_id: str) -> bool:
        return language_id == "python"

    def supported_languages(self) -> list[str]:
        return ["python"]
