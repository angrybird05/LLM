"""Syntax tree validation for post-transformation verification.

Checks modified code for CST error nodes (tree-sitter ERROR/MISSING nodes)
and language-specific syntax errors before committing changes to disk.
"""
from __future__ import annotations

from pathlib import Path

from coding_agent.core.semantic.detector import detect_language
from coding_agent.core.semantic.parser.tree_sitter import TreeSitterParser


def validate_syntax(code: str, file_path: Path) -> list[str]:
    """Validate syntax of transformed code.

    Returns a list of error descriptions (empty list if syntax is valid).
    """
    errors: list[str] = []
    lang_info = detect_language(file_path)

    # 1. Python-specific exact compilation check
    if lang_info.language_id == "python":
        try:
            compile(code, str(file_path), "exec")
        except SyntaxError as e:
            errors.append(f"SyntaxError in {file_path.name} at line {e.lineno}:{e.offset}: {e.msg}")
            return errors

    # 2. Universal Tree-sitter error node detection
    parser = TreeSitterParser()
    try:
        parse_res = parser.parse(code, file_path=file_path, language_id=lang_info.language_id)
        if parse_res.has_errors:
            # Locate error nodes
            _collect_error_nodes(parse_res.root, file_path, errors)
    except Exception as e:
        errors.append(f"Parsing error in {file_path.name}: {e}")

    return errors


def _collect_error_nodes(node, file_path: Path, out_errors: list[str]) -> None:
    if node.node_type in ("ERROR", "MISSING") or node.is_missing:
        out_errors.append(
            f"Syntax error ({node.node_type}) in {file_path.name} at line {node.span.start_line + 1}, column {node.span.start_column}"
        )
    for child in node.children:
        if child.has_error:
            _collect_error_nodes(child, file_path, out_errors)
