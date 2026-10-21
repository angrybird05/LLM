"""Tests for multi-language AST/CST parser and cache."""
from pathlib import Path
import pytest

from coding_agent.core.semantic.detector import detect_language
from coding_agent.core.semantic.parser import parse_source, parse_file
from coding_agent.core.semantic.parser.tree_sitter import TreeSitterParser


def test_detect_language():
    py = detect_language(Path("test.py"))
    assert py.language_id == "python"
    assert py.parser_backend in ("tree_sitter", "libcst")

    ts = detect_language(Path("app.tsx"))
    assert ts.language_id in ("typescript", "tsx")

    go = detect_language(Path("main.go"))
    assert go.language_id == "go"

    rs = detect_language(Path("lib.rs"))
    assert rs.language_id == "rust"


def test_parse_python_tree_sitter():
    code = """
def hello(name: str) -> str:
    \"\"\"Say hello.\"\"\"
    return f"Hello {name}"
"""
    res = parse_source(code, language_id="python")
    assert res is not None
    assert not res.has_errors
    assert res.root.node_type == "module"


def test_parse_typescript():
    code = """
interface User {
    id: number;
    name: string;
}

function getUser(id: number): User {
    return { id, name: "Alice" };
}
"""
    res = parse_source(code, language_id="typescript")
    assert res is not None
    assert not res.has_errors


def test_parse_error_tolerance():
    # Incomplete / broken syntax should not crash the parser
    broken_code = """
def broken_fn(:
    print("missing param"
"""
    res = parse_source(broken_code, language_id="python")
    assert res is not None
    assert res.has_errors


def test_incremental_parse():
    parser = TreeSitterParser()
    initial_code = "def foo():\n    pass\n"
    res1 = parser.parse(initial_code, language_id="python")
    assert not res1.has_errors

    # Incremental parse with edited code
    edited_code = "def foo_bar():\n    pass\n"
    res2 = parser.parse(edited_code, language_id="python", old_tree=res1.raw_tree)
    assert not res2.has_errors
    assert "foo_bar" in res2.source_code
