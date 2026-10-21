"""Language detection by file extension, shebang, and project context.

Maps file paths to LanguageInfo objects describing the parser/transform backends
and the capability flags for that language.
"""
from __future__ import annotations

from pathlib import Path

from coding_agent.core.semantic.models import Capability, LanguageInfo

# Capability sets per language tier
_FULL = (
    Capability.PARSE_CST | Capability.SYMBOL_OUTLINE | Capability.SCOPE_ANALYSIS
    | Capability.CROSS_FILE_REF | Capability.PRESERVE_FORMAT | Capability.STRUCTURAL_REFACTOR
)
_STANDARD = (
    Capability.PARSE_CST | Capability.SYMBOL_OUTLINE | Capability.SCOPE_ANALYSIS
    | Capability.CROSS_FILE_REF | Capability.PRESERVE_FORMAT
)
_BASIC = (
    Capability.PARSE_CST | Capability.SYMBOL_OUTLINE | Capability.SCOPE_ANALYSIS
)
_PARSE_ONLY = Capability.PARSE_CST | Capability.SYMBOL_OUTLINE


# Extension -> LanguageInfo registry
_EXTENSION_MAP: dict[str, LanguageInfo] = {
    ".py": LanguageInfo(
        language_id="python",
        capabilities=_FULL,
        parser_backend="tree_sitter",
        transform_backend="libcst",
        file_extension=".py",
    ),
    ".pyi": LanguageInfo(
        language_id="python",
        capabilities=_FULL,
        parser_backend="tree_sitter",
        transform_backend="libcst",
        file_extension=".pyi",
    ),
    ".ts": LanguageInfo(
        language_id="typescript",
        capabilities=_STANDARD,
        parser_backend="tree_sitter",
        transform_backend="range_rewrite",
        file_extension=".ts",
    ),
    ".tsx": LanguageInfo(
        language_id="tsx",
        capabilities=_STANDARD,
        parser_backend="tree_sitter",
        transform_backend="range_rewrite",
        file_extension=".tsx",
    ),
    ".js": LanguageInfo(
        language_id="javascript",
        capabilities=_STANDARD,
        parser_backend="tree_sitter",
        transform_backend="range_rewrite",
        file_extension=".js",
    ),
    ".jsx": LanguageInfo(
        language_id="javascript",
        capabilities=_STANDARD,
        parser_backend="tree_sitter",
        transform_backend="range_rewrite",
        file_extension=".jsx",
    ),
    ".mjs": LanguageInfo(
        language_id="javascript",
        capabilities=_STANDARD,
        parser_backend="tree_sitter",
        transform_backend="range_rewrite",
        file_extension=".mjs",
    ),
    ".go": LanguageInfo(
        language_id="go",
        capabilities=_STANDARD,
        parser_backend="tree_sitter",
        transform_backend="range_rewrite",
        file_extension=".go",
    ),
    ".rs": LanguageInfo(
        language_id="rust",
        capabilities=_STANDARD & ~Capability.CROSS_FILE_REF,
        parser_backend="tree_sitter",
        transform_backend="range_rewrite",
        file_extension=".rs",
    ),
    ".java": LanguageInfo(
        language_id="java",
        capabilities=_BASIC,
        parser_backend="tree_sitter",
        transform_backend="range_rewrite",
        file_extension=".java",
    ),
    ".cs": LanguageInfo(
        language_id="c_sharp",
        capabilities=_BASIC,
        parser_backend="tree_sitter",
        transform_backend="range_rewrite",
        file_extension=".cs",
    ),
    ".c": LanguageInfo(
        language_id="c",
        capabilities=_BASIC,
        parser_backend="tree_sitter",
        transform_backend="range_rewrite",
        file_extension=".c",
    ),
    ".h": LanguageInfo(
        language_id="c",
        capabilities=_BASIC,
        parser_backend="tree_sitter",
        transform_backend="range_rewrite",
        file_extension=".h",
    ),
    ".cpp": LanguageInfo(
        language_id="cpp",
        capabilities=_BASIC,
        parser_backend="tree_sitter",
        transform_backend="range_rewrite",
        file_extension=".cpp",
    ),
    ".hpp": LanguageInfo(
        language_id="cpp",
        capabilities=_BASIC,
        parser_backend="tree_sitter",
        transform_backend="range_rewrite",
        file_extension=".hpp",
    ),
    ".cc": LanguageInfo(
        language_id="cpp",
        capabilities=_BASIC,
        parser_backend="tree_sitter",
        transform_backend="range_rewrite",
        file_extension=".cc",
    ),
    ".json": LanguageInfo(
        language_id="json",
        capabilities=_PARSE_ONLY,
        parser_backend="tree_sitter",
        transform_backend="range_rewrite",
        file_extension=".json",
    ),
}

# Shebang to language id mapping
_SHEBANG_MAP: dict[str, str] = {
    "python": "python",
    "python3": "python",
    "node": "javascript",
    "deno": "typescript",
    "bash": "bash",
    "sh": "bash",
    "ruby": "ruby",
    "perl": "perl",
}


def detect_language(path: Path | str) -> LanguageInfo | None:
    """Detect the programming language from file extension and shebang.

    Returns None if the language is not supported for semantic operations.
    """
    path = Path(path)
    ext = path.suffix.lower()

    # Try extension first
    if ext in _EXTENSION_MAP:
        return _EXTENSION_MAP[ext]

    # Try shebang for extensionless files
    if not ext or ext not in _EXTENSION_MAP:
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                first_line = f.readline(256)
            if first_line.startswith("#!"):
                for key, lang_id in _SHEBANG_MAP.items():
                    if key in first_line:
                        # Find the lang_id in _EXTENSION_MAP values
                        for info in _EXTENSION_MAP.values():
                            if info.language_id == lang_id:
                                return info
                        break
        except OSError:
            pass

    return None


def get_supported_extensions() -> list[str]:
    """Return all file extensions that have semantic support."""
    return sorted(_EXTENSION_MAP.keys())


def is_supported(path: Path | str) -> bool:
    """Check if a file has a supported language for semantic operations."""
    return detect_language(path) is not None


# Language ID to tree-sitter language name mapping (for grammar loading)
LANGUAGE_GRAMMAR_MAP: dict[str, str] = {
    "python": "python",
    "typescript": "typescript",
    "tsx": "tsx",
    "javascript": "javascript",
    "go": "go",
    "rust": "rust",
    "java": "java",
    "c_sharp": "c_sharp",
    "c": "c",
    "cpp": "cpp",
    "json": "json",
}
