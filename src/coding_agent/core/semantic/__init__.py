"""AST-based Semantic Editing Engine.

Provides syntax-tree, symbol-identity, scope-hierarchy, and reference-aware
refactoring capabilities across multiple programming languages.
"""
from __future__ import annotations

from coding_agent.core.semantic.detector import detect_language
from coding_agent.core.semantic.index import FileIndex, ProjectIndex, index_file
from coding_agent.core.semantic.models import (
    Capability,
    FileEdit,
    LanguageInfo,
    RefactorOperation,
    RefactorPlan,
    Scope,
    ScopeKind,
    Span,
    Symbol,
    SymbolKind,
    SymbolReference,
)
from coding_agent.core.semantic.parser import parse_file, parse_source
from coding_agent.core.semantic.refactor import RefactoringCoordinator
from coding_agent.core.semantic.resolver import SemanticResolver
from coding_agent.core.semantic.router import EditRouter
from coding_agent.core.semantic.transaction import TransactionManager
from coding_agent.core.semantic.validation import ValidationEngine

__all__ = [
    "Capability",
    "EditRouter",
    "FileEdit",
    "FileIndex",
    "LanguageInfo",
    "ProjectIndex",
    "RefactorOperation",
    "RefactorPlan",
    "RefactoringCoordinator",
    "Scope",
    "ScopeKind",
    "SemanticResolver",
    "Span",
    "Symbol",
    "SymbolKind",
    "SymbolReference",
    "TransactionManager",
    "ValidationEngine",
    "detect_language",
    "index_file",
    "parse_file",
    "parse_source",
]
