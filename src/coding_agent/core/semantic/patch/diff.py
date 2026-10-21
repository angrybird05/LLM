"""Minimal unified diff generator for semantic code modifications.

Produces standard unified diffs for terminal display, permission reviews,
and user previews across single or multiple files.
"""
from __future__ import annotations

import difflib
from pathlib import Path


def generate_unified_diff(
    original_text: str,
    modified_text: str,
    file_path: Path | str,
    context_lines: int = 3,
) -> str:
    """Generate a unified diff between original and modified text."""
    path_str = str(file_path)
    from_file = f"a/{path_str}"
    to_file = f"b/{path_str}"

    orig_lines = original_text.splitlines(keepends=True)
    mod_lines = modified_text.splitlines(keepends=True)

    diff = difflib.unified_diff(
        orig_lines,
        mod_lines,
        fromfile=from_file,
        tofile=to_file,
        n=context_lines,
    )
    return "".join(diff)


def generate_multi_file_diff(
    modifications: dict[Path, tuple[str, str]],
    context_lines: int = 3,
) -> str:
    """Generate a combined multi-file diff for a set of file changes.

    `modifications` maps Path -> (original_text, modified_text).
    """
    diff_parts: list[str] = []
    for path, (orig, mod) in modifications.items():
        if orig == mod:
            continue
        d = generate_unified_diff(orig, mod, path, context_lines=context_lines)
        if d:
            diff_parts.append(d)

    return "\n".join(diff_parts)
