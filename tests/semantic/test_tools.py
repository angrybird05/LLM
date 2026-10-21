"""Tests for ast_edit, ast_refactor, and find_symbols tools."""
import tempfile
from pathlib import Path
import pytest

from coding_agent.config import settings
from coding_agent.core.semantic.tools.ast_edit import ASTEditTool
from coding_agent.core.semantic.tools.ast_refactor import ASTRefactorTool
from coding_agent.core.semantic.tools.find_symbols import FindSymbolsTool
from coding_agent.core.tools.base import registry, READ_ONLY_TOOL_NAMES, SEMANTIC_TOOL_NAMES


@pytest.mark.asyncio
async def test_find_symbols_tool():
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        test_file = root / "sample.py"
        test_file.write_text("class MyService:\n    def execute(self):\n        return 42\n")

        orig_ws = settings.WORKSPACE_ROOT
        try:
            settings.WORKSPACE_ROOT = str(root)
            tool = FindSymbolsTool()
            res = await tool.execute({"action": "outline", "path": "sample.py"}, {})

            assert "symbols" in res
            names = [s["name"] for s in res["symbols"]]
            assert "MyService" in names
            assert "execute" in names
        finally:
            settings.WORKSPACE_ROOT = orig_ws


@pytest.mark.asyncio
async def test_ast_edit_tool_replace_body():
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        test_file = root / "math_ops.py"
        test_file.write_text(
            "def add(a, b):\n"
            "    return a - b  # bug\n"
        )

        orig_ws = settings.WORKSPACE_ROOT
        try:
            settings.WORKSPACE_ROOT = str(root)
            tool = ASTEditTool()
            res = await tool.execute({
                "path": "math_ops.py",
                "action": "replace_body",
                "target_symbol": "add",
                "content": "    return a + b\n",
            }, {})

            assert res.get("success") is True
            assert "return a + b" in test_file.read_text()
        finally:
            settings.WORKSPACE_ROOT = orig_ws


@pytest.mark.asyncio
async def test_ast_refactor_rename_tool():
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        test_file = root / "app.py"
        test_file.write_text("def old_runner():\n    pass\n\nold_runner()\n")

        orig_ws = settings.WORKSPACE_ROOT
        try:
            settings.WORKSPACE_ROOT = str(root)
            tool = ASTRefactorTool()
            res = await tool.execute({
                "operation": "rename",
                "path": "app.py",
                "symbol_name": "old_runner",
                "new_name": "new_runner",
            }, {})

            assert res.get("success") is True
            content = test_file.read_text()
            assert "def new_runner():" in content
            assert "new_runner()" in content
            assert "old_runner" not in content
        finally:
            settings.WORKSPACE_ROOT = orig_ws


def test_tool_registrations():
    assert "ast_edit" in registry._tools
    assert "ast_refactor" in registry._tools
    assert "find_symbols" in registry._tools
    assert "find_symbols" in READ_ONLY_TOOL_NAMES
    assert "ast_edit" in SEMANTIC_TOOL_NAMES
    assert "ast_refactor" in SEMANTIC_TOOL_NAMES
