"""Byte-range slicing and splicing rewriter for multi-language AST edits.

Applies replacements to source code using exact byte ranges derived from
Tree-sitter CST nodes, preserving formatting and comments elsewhere in the file.
"""
from __future__ import annotations

from pathlib import Path

from coding_agent.core.semantic.models import FileEdit, OverlappingEditError, Span
from coding_agent.core.semantic.transform.formatter import (
    detect_indentation,
    get_line_indent,
    reindent_block,
)


def apply_byte_edits(source: str, edits: list[FileEdit]) -> str:
    """Apply a sequence of byte-offset edits to source code.

    Edits must not overlap. They are applied in reverse byte order so
    that earlier byte offsets remain valid.
    """
    if not edits:
        return source

    # Validate for overlaps
    sorted_forward = sorted(edits, key=lambda e: (e.byte_start, e.byte_end))
    for i in range(len(sorted_forward) - 1):
        curr = sorted_forward[i]
        nxt = sorted_forward[i + 1]
        if curr.byte_end > nxt.byte_start:
            raise OverlappingEditError(curr, nxt)

    # Encode to bytes for accurate slicing
    raw_bytes = bytearray(source.encode("utf-8"))

    # Apply in reverse byte order
    sorted_reverse = sorted(edits, key=lambda e: e.byte_start, reverse=True)
    for edit in sorted_reverse:
        replacement_bytes = edit.replacement_text.encode("utf-8")
        raw_bytes[edit.byte_start:edit.byte_end] = replacement_bytes

    return raw_bytes.decode("utf-8")


def replace_span(source: str, span: Span, replacement: str, file_path: Path | None = None) -> str:
    """Replace a single span in source code."""
    edit = FileEdit(
        path=file_path or Path("temp"),
        byte_start=span.start_byte,
        byte_end=span.end_byte,
        replacement_text=replacement,
    )
    return apply_byte_edits(source, [edit])


def insert_before_span(source: str, span: Span, code: str, file_path: Path | None = None) -> str:
    """Insert code immediately before a span."""
    edit = FileEdit(
        path=file_path or Path("temp"),
        byte_start=span.start_byte,
        byte_end=span.start_byte,
        replacement_text=code,
    )
    return apply_byte_edits(source, [edit])


def insert_after_span(source: str, span: Span, code: str, file_path: Path | None = None) -> str:
    """Insert code immediately after a span."""
    edit = FileEdit(
        path=file_path or Path("temp"),
        byte_start=span.end_byte,
        byte_end=span.end_byte,
        replacement_text=code,
    )
    return apply_byte_edits(source, [edit])


def delete_span(source: str, span: Span, file_path: Path | None = None) -> str:
    """Delete the code within a span."""
    return replace_span(source, span, "", file_path)


def replace_node_body(
    source: str,
    body_span: Span,
    new_body_code: str,
    target_indent: str | None = None,
    file_path: Path | None = None,
) -> str:
    """Replace the body of a function, method, or class, ensuring proper indentation."""
    if target_indent is None:
        lines = source.splitlines()
        if 0 <= body_span.start_line < len(lines):
            target_indent = get_line_indent(lines[body_span.start_line])
        else:
            target_indent = "    "

    formatted_body = reindent_block(new_body_code, target_indent)
    return replace_span(source, body_span, formatted_body, file_path)
