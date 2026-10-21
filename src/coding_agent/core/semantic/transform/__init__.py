"""Transformation engine facade.

Combines high-fidelity LibCST transforms (Python) with universal
byte-range slice/splice rewriting (all languages).
"""
from __future__ import annotations

from coding_agent.core.semantic.transform.cst_rewrite import (
    python_add_import,
    python_rename_symbol,
    python_replace_function_body,
)
from coding_agent.core.semantic.transform.formatter import (
    detect_indentation,
    get_line_indent,
    reindent_block,
)
from coding_agent.core.semantic.transform.range_rewrite import (
    apply_byte_edits,
    delete_span,
    insert_after_span,
    insert_before_span,
    replace_node_body,
    replace_span,
)

__all__ = [
    "apply_byte_edits",
    "delete_span",
    "detect_indentation",
    "get_line_indent",
    "insert_after_span",
    "insert_before_span",
    "python_add_import",
    "python_rename_symbol",
    "python_replace_function_body",
    "reindent_block",
    "replace_node_body",
    "replace_span",
]
