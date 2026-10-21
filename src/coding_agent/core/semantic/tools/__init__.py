"""Semantic editing tools registration.

Registers ast_edit, ast_refactor, and find_symbols into the global tool registry.
"""
from __future__ import annotations

from coding_agent.core.semantic.tools.ast_edit import ASTEditTool
from coding_agent.core.semantic.tools.ast_refactor import ASTRefactorTool
from coding_agent.core.semantic.tools.find_symbols import FindSymbolsTool
from coding_agent.core.tools.base import registry


def register_semantic_tools() -> None:
    """Register all semantic tools in the global registry."""
    registry.register(FindSymbolsTool())
    registry.register(ASTEditTool())
    registry.register(ASTRefactorTool())


# Auto-register on import
register_semantic_tools()

__all__ = [
    "ASTEditTool",
    "ASTRefactorTool",
    "FindSymbolsTool",
    "register_semantic_tools",
]
