"""Tests for LibCST and byte-range AST transformations."""
from pathlib import Path
import pytest

from coding_agent.core.semantic.transform.cst_rewrite import (
    python_rename_symbol,
    python_replace_function_body,
    python_add_import,
)
from coding_agent.core.semantic.transform.range_rewrite import (
    apply_byte_edits,
    replace_span,
    replace_node_body,
)
from coding_agent.core.semantic.models import FileEdit, Span, OverlappingEditError


def test_python_cst_rename_preserves_comments():
    source = """
# Crucial setup comment
def old_function(x: int) -> int:
    # inline math explanation
    return x + 42  # trailing note
"""
    updated = python_rename_symbol(source, "old_function", "new_function")
    assert "def new_function(" in updated
    assert "# Crucial setup comment" in updated
    assert "# inline math explanation" in updated
    assert "# trailing note" in updated


def test_python_cst_replace_function_body():
    source = """
def calculate():
    x = 1
    return x
"""
    new_body = "    y = 100\n    return y * 2\n"
    updated = python_replace_function_body(source, "calculate", new_body)
    assert "y = 100" in updated
    assert "x = 1" not in updated


def test_python_cst_add_import():
    source = """\"\"\"Module docstring.\"\"\"
def run():
    pass
"""
    updated = python_add_import(source, "from os import path")
    assert "from os import path" in updated
    assert '"""Module docstring."""' in updated


def test_range_rewrite_reverse_order():
    source = "abcdefghij"
    # Edit 1 at [2:4] ("cd" -> "XX"), Edit 2 at [6:8] ("gh" -> "YY")
    edit1 = FileEdit(Path("temp"), byte_start=2, byte_end=4, replacement_text="XX")
    edit2 = FileEdit(Path("temp"), byte_start=6, byte_end=8, replacement_text="YY")

    # Pass in forward order, should still be correctly applied
    result = apply_byte_edits(source, [edit1, edit2])
    assert result == "abXXefYYij"


def test_range_rewrite_overlap_rejection():
    source = "abcdefghij"
    edit1 = FileEdit(Path("temp"), byte_start=2, byte_end=6, replacement_text="XX")
    edit2 = FileEdit(Path("temp"), byte_start=4, byte_end=8, replacement_text="YY")

    with pytest.raises(OverlappingEditError):
        apply_byte_edits(source, [edit1, edit2])
