"""Indentation and formatting adjustment utilities for code transformation.

Ensures inserted or replaced syntax nodes match the indentation and style
of the surrounding file without disturbing untouched code.
"""
from __future__ import annotations

import re


def detect_indentation(source: str) -> tuple[str, int]:
    """Detect indent unit (e.g. ' ' or '\t') and width (e.g. 4 or 2).

    Defaults to (' ', 4) if detection is inconclusive.
    """
    lines = source.splitlines()
    indent_diffs: list[int] = []
    prev_indent = 0
    uses_tabs = False

    for line in lines:
        stripped = line.lstrip()
        if not stripped or stripped.startswith(("#", "//", "/*", "*")):
            continue

        indent_str = line[:len(line) - len(stripped)]
        if "\t" in indent_str:
            uses_tabs = True

        current_indent = len(indent_str)
        if current_indent > prev_indent:
            indent_diffs.append(current_indent - prev_indent)
        prev_indent = current_indent

    if uses_tabs:
        return ("\t", 1)

    if indent_diffs:
        # Find most common non-zero diff
        counts: dict[int, int] = {}
        for d in indent_diffs:
            if d > 0:
                counts[d] = counts.get(d, 0) + 1
        if counts:
            best_width = max(counts.items(), key=lambda kv: kv[1])[0]
            if 1 <= best_width <= 8:
                return (" ", best_width)

    return (" ", 4)


def get_line_indent(line: str) -> str:
    """Return the whitespace prefix of a line."""
    match = re.match(r"^([ \t]*)", line)
    return match.group(1) if match else ""


def reindent_block(code: str, target_indent: str) -> str:
    """Reindent a multi-line block so its base indentation matches target_indent.

    Relative indentation between lines in the block is preserved.
    """
    lines = code.splitlines(keepends=True)
    if not lines:
        return ""

    # Find minimum indentation among non-empty lines
    min_indent: int | None = None
    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        indent = len(get_line_indent(line))
        if min_indent is None or indent < min_indent:
            min_indent = indent

    min_indent = min_indent or 0

    reindented: list[str] = []
    for line in lines:
        stripped = line.strip()
        if not stripped:
            reindented.append(line)
            continue
        line_indent = get_line_indent(line)
        # Strip the base indent and add target_indent
        relative_indent = line_indent[min_indent:] if len(line_indent) >= min_indent else ""
        rest_of_line = line[len(line_indent):]
        reindented.append(f"{target_indent}{relative_indent}{rest_of_line}")

    return "".join(reindented)


def ensure_trailing_newline(text: str) -> str:
    """Ensure the text ends with a newline."""
    if not text.endswith("\n"):
        return text + "\n"
    return text
