"""Core domain dataclasses for the Semantic Editing Engine.

These models are used across every layer — from parsing and indexing through
refactoring and patch generation.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum, Flag, auto
from pathlib import Path


# ------------------------------------------------------------------ capabilities

class Capability(Flag):
    """Flags describing what operations a language backend supports."""
    PARSE_CST = auto()
    SYMBOL_OUTLINE = auto()
    SCOPE_ANALYSIS = auto()
    CROSS_FILE_REF = auto()
    PRESERVE_FORMAT = auto()
    STRUCTURAL_REFACTOR = auto()


# ------------------------------------------------------------------ language info

@dataclass(frozen=True)
class LanguageInfo:
    """Result of language detection for a single file."""
    language_id: str          # e.g. "python", "typescript", "go"
    capabilities: Capability
    parser_backend: str       # e.g. "tree_sitter", "libcst"
    transform_backend: str    # e.g. "libcst", "range_rewrite"
    file_extension: str       # e.g. ".py", ".ts"


# ------------------------------------------------------------------ source span

@dataclass(frozen=True)
class Span:
    """Byte and line/column offsets for a node in source code."""
    start_byte: int
    end_byte: int
    start_line: int     # 0-indexed
    start_column: int
    end_line: int       # 0-indexed
    end_column: int

    @property
    def start_point(self) -> tuple[int, int]:
        return (self.start_line, self.start_column)

    @property
    def end_point(self) -> tuple[int, int]:
        return (self.end_line, self.end_column)


# ------------------------------------------------------------------ symbols

class SymbolKind(str, Enum):
    """The kind of a discovered symbol."""
    FUNCTION = "function"
    CLASS = "class"
    METHOD = "method"
    VARIABLE = "variable"
    INTERFACE = "interface"
    STRUCT = "struct"
    TRAIT = "trait"
    TYPE_ALIAS = "type_alias"
    IMPORT = "import"
    MODULE = "module"
    PARAMETER = "parameter"
    FIELD = "field"
    DECORATOR = "decorator"
    PROPERTY = "property"
    CONSTANT = "constant"


@dataclass
class Symbol:
    """A resolved symbol extracted from source code."""
    name: str
    kind: SymbolKind
    span: Span
    file_path: Path
    qualified_name: str = ""      # e.g. "ClassName.method_name"
    parent: Symbol | None = None
    children: list[Symbol] = field(default_factory=list)
    docstring: str | None = None
    decorators: list[str] = field(default_factory=list)
    parameters: list[str] = field(default_factory=list)
    return_type: str | None = None

    def __repr__(self) -> str:
        loc = f"{self.file_path.name}:{self.span.start_line + 1}"
        return f"Symbol({self.kind.value} {self.qualified_name or self.name} @ {loc})"


# ------------------------------------------------------------------ scopes

class ScopeKind(str, Enum):
    GLOBAL = "global"
    MODULE = "module"
    CLASS = "class"
    FUNCTION = "function"
    BLOCK = "block"
    COMPREHENSION = "comprehension"


@dataclass
class Binding:
    """A name bound in a scope: variable, parameter, import, etc."""
    name: str
    span: Span
    kind: SymbolKind
    is_definition: bool = True


@dataclass
class Scope:
    """A lexical scope containing bindings and child scopes."""
    kind: ScopeKind
    span: Span
    bindings: dict[str, list[Binding]] = field(default_factory=dict)
    children: list[Scope] = field(default_factory=list)
    parent: Scope | None = field(default=None, repr=False)

    def lookup(self, name: str) -> list[Binding]:
        """Walk up the scope chain to find all bindings for a name."""
        if name in self.bindings:
            return self.bindings[name]
        if self.parent:
            return self.parent.lookup(name)
        return []

    def add_binding(self, binding: Binding) -> None:
        self.bindings.setdefault(binding.name, []).append(binding)


# ------------------------------------------------------------------ symbol references

class ReferenceKind(str, Enum):
    DEFINITION = "definition"
    USAGE = "usage"
    IMPORT = "import"
    CALL = "call"
    TYPE_ANNOTATION = "type_annotation"
    ASSIGNMENT = "assignment"


class ConfidenceLevel(str, Enum):
    """How confident we are about a resolved reference."""
    DEFINITE = "definite"
    PROBABLE = "probable"
    POTENTIAL = "potential"


@dataclass
class SymbolReference:
    """An occurrence of a symbol (definition, usage, import, call)."""
    name: str
    span: Span
    file_path: Path
    kind: ReferenceKind
    confidence: ConfidenceLevel = ConfidenceLevel.DEFINITE


# ------------------------------------------------------------------ file edits

@dataclass
class FileEdit:
    """A single text replacement within a file (byte-range based)."""
    path: Path
    byte_start: int
    byte_end: int
    replacement_text: str
    description: str = ""

    @property
    def byte_range(self) -> tuple[int, int]:
        return (self.byte_start, self.byte_end)


# ------------------------------------------------------------------ refactoring

class RefactorOperation(str, Enum):
    RENAME = "rename"
    EXTRACT_FUNCTION = "extract_function"
    CHANGE_SIGNATURE = "change_signature"
    MOVE_SYMBOL = "move_symbol"
    ADD_IMPORT = "add_import"
    REMOVE_IMPORT = "remove_import"


@dataclass
class RefactorPlan:
    """A planned multi-file refactoring operation."""
    operation: RefactorOperation
    description: str
    edits: list[FileEdit] = field(default_factory=list)
    affected_files: list[Path] = field(default_factory=list)

    # Provenance
    primary_file: Path | None = None
    symbol_name: str = ""
    new_name: str = ""

    # Validation
    is_validated: bool = False
    validation_errors: list[str] = field(default_factory=list)

    @property
    def file_count(self) -> int:
        return len(set(e.path for e in self.edits))

    @property
    def edit_count(self) -> int:
        return len(self.edits)


# ------------------------------------------------------------------ errors

class SemanticError(Exception):
    """Base exception for semantic editing errors."""
    pass


class UnsupportedLanguageError(SemanticError):
    """Language is not supported for semantic operations."""
    pass


class AmbiguousSymbolError(SemanticError):
    """Symbol could not be uniquely resolved."""
    def __init__(self, message: str, candidates: list[Symbol] | None = None):
        super().__init__(message)
        self.candidates = candidates or []


class OverlappingEditError(SemanticError):
    """Two edits have overlapping byte ranges within the same file."""
    def __init__(self, edit_a: FileEdit, edit_b: FileEdit):
        super().__init__(
            f"Edits overlap in {edit_a.path}: "
            f"[{edit_a.byte_start}:{edit_a.byte_end}] vs "
            f"[{edit_b.byte_start}:{edit_b.byte_end}]"
        )
        self.edit_a = edit_a
        self.edit_b = edit_b


class ValidationError(SemanticError):
    """Post-transformation validation failed."""
    def __init__(self, message: str, errors: list[str] | None = None):
        super().__init__(message)
        self.errors = errors or []


class ConcurrentModificationError(SemanticError):
    """File was modified externally during refactoring."""
    pass
