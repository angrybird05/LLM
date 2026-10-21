"""Tests for symbol extraction and lexical scope hierarchy."""
from pathlib import Path
import pytest

from coding_agent.core.semantic.index import index_file, FileIndex
from coding_agent.core.semantic.index.symbols import extract_symbols
from coding_agent.core.semantic.index.scopes import build_scope_tree, find_shadowed_bindings
from coding_agent.core.semantic.models import SymbolKind, ScopeKind
from coding_agent.core.semantic.parser import parse_source


def test_python_symbol_extraction():
    code = """
class Calculator:
    \"\"\"A simple math helper.\"\"\"
    def add(self, a: int, b: int) -> int:
        return a + b

def standalone(x):
    return x * 2
"""
    res = parse_source(code, language_id="python")
    symbols = extract_symbols(res)

    names = {s.name: s for s in symbols}
    assert "Calculator" in names
    assert names["Calculator"].kind == SymbolKind.CLASS
    assert "add" in names
    assert names["add"].kind == SymbolKind.METHOD
    assert "standalone" in names
    assert names["standalone"].kind == SymbolKind.FUNCTION


def test_scope_tree_and_shadowing():
    code = """
x = 10

def outer():
    x = 20  # Shadows global x
    def inner():
        x = 30  # Shadows outer x
        return x
    return inner()
"""
    res = parse_source(code, language_id="python")
    scope = build_scope_tree(res)
    assert scope.kind == ScopeKind.MODULE

    shadows = find_shadowed_bindings(scope)
    assert len(shadows) >= 1
    shadowed_names = [inner.name for inner, outer in shadows]
    assert "x" in shadowed_names


def test_file_index_facade():
    code = """
def process_data(data):
    return [d for d in data]
"""
    res = parse_source(code, language_id="python")
    findex = index_file(res)
    syms = findex.find_symbol("process_data")
    assert len(syms) == 1
    assert syms[0].kind == SymbolKind.FUNCTION
