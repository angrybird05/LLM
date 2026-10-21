"""Parser registry and LRU CST cache.

Provides a unified entry point for parsing any supported language, with
an in-memory cache keyed by (file_path, mtime, content_hash) to avoid
redundant re-parsing.
"""
from __future__ import annotations

import hashlib
import threading
from collections import OrderedDict
from pathlib import Path
from typing import Any

from loguru import logger

from coding_agent.core.semantic.parser.base import CSTNode, ParseResult, ParserInterface
from coding_agent.core.semantic.parser.tree_sitter import TreeSitterParser

# Re-export for convenience
__all__ = [
    "CSTNode",
    "ParseResult",
    "ParserInterface",
    "ParserRegistry",
    "get_parser_registry",
    "parse_file",
    "parse_source",
]

# Default cache size (number of parse trees to keep)
DEFAULT_CACHE_SIZE = 500


class _CacheKey:
    __slots__ = ("path", "mtime", "content_hash")

    def __init__(self, path: Path, mtime: float, content_hash: str):
        self.path = path
        self.mtime = mtime
        self.content_hash = content_hash

    def __hash__(self) -> int:
        return hash((str(self.path), self.mtime, self.content_hash))

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, _CacheKey):
            return NotImplemented
        return (
            str(self.path) == str(other.path)
            and self.mtime == other.mtime
            and self.content_hash == other.content_hash
        )


class ParserRegistry:
    """Singleton parser registry with LRU caching.

    Routes parse requests to the appropriate backend (tree-sitter or LibCST)
    and caches results for fast re-access.
    """

    def __init__(self, cache_size: int = DEFAULT_CACHE_SIZE):
        self._tree_sitter = TreeSitterParser()
        self._python_cst: ParserInterface | None = None
        self._cache: OrderedDict[_CacheKey, ParseResult] = OrderedDict()
        self._cache_size = cache_size
        self._lock = threading.Lock()

    @property
    def python_cst(self) -> ParserInterface | None:
        """Lazily load the LibCST parser."""
        if self._python_cst is None:
            try:
                from coding_agent.core.semantic.parser.python_cst import PythonCSTParser
                self._python_cst = PythonCSTParser()
            except ImportError:
                logger.debug("LibCST not available; Python CST transforms disabled")
        return self._python_cst

    def parse_file(self, file_path: Path, language_id: str,
                   use_cache: bool = True) -> ParseResult:
        """Parse a file, using cache when possible.

        Args:
            file_path: Path to the source file.
            language_id: Language identifier (e.g. "python").
            use_cache: Whether to check/update the cache.

        Returns:
            ParseResult with the root CSTNode.
        """
        file_path = Path(file_path).resolve()

        if not file_path.exists():
            raise FileNotFoundError(f"File not found: {file_path}")

        try:
            source = file_path.read_bytes()
        except OSError as e:
            raise OSError(f"Cannot read {file_path}: {e}") from e

        stat = file_path.stat()
        mtime = stat.st_mtime

        if use_cache:
            content_hash = hashlib.sha256(source[:4096]).hexdigest()[:16]
            key = _CacheKey(file_path, mtime, content_hash)

            with self._lock:
                if key in self._cache:
                    self._cache.move_to_end(key)
                    return self._cache[key]

        result = self.parse_source(source, language_id, file_path)

        if use_cache:
            with self._lock:
                self._cache[key] = result
                while len(self._cache) > self._cache_size:
                    self._cache.popitem(last=False)

        return result

    def parse_source(self, source: str | bytes, language_id: str,
                     file_path: Path | None = None,
                     old_tree: Any | None = None) -> ParseResult:
        """Parse source code directly (no caching).

        Args:
            source: Source code as string or bytes.
            language_id: Language identifier.
            file_path: Optional file path for diagnostics.
            old_tree: Previous tree for incremental re-parsing (tree-sitter only).
        """
        parser = self._select_parser(language_id)
        return parser.parse(source, language_id, file_path, old_tree)

    def _select_parser(self, language_id: str) -> ParserInterface:
        """Choose the best parser for a language."""
        # Tree-sitter is the universal parser
        if self._tree_sitter.supports_language(language_id):
            return self._tree_sitter
        raise ValueError(f"No parser available for language '{language_id}'")

    def get_python_cst_parser(self) -> ParserInterface | None:
        """Get the LibCST parser for Python-specific high-fidelity transforms."""
        return self.python_cst

    def invalidate_cache(self, file_path: Path | None = None) -> int:
        """Invalidate cached parse results.

        Args:
            file_path: If given, only invalidate cache for this file.
                       If None, clear the entire cache.

        Returns:
            Number of entries invalidated.
        """
        with self._lock:
            if file_path is None:
                count = len(self._cache)
                self._cache.clear()
                return count

            file_path = Path(file_path).resolve()
            keys_to_remove = [
                k for k in self._cache if str(k.path) == str(file_path)
            ]
            for k in keys_to_remove:
                del self._cache[k]
            return len(keys_to_remove)

    @property
    def cache_info(self) -> dict[str, int]:
        """Return cache statistics."""
        with self._lock:
            return {
                "size": len(self._cache),
                "max_size": self._cache_size,
            }

    def supports_language(self, language_id: str) -> bool:
        return self._tree_sitter.supports_language(language_id)

    def supported_languages(self) -> list[str]:
        return self._tree_sitter.supported_languages()


# Module-level singleton
_registry: ParserRegistry | None = None


def get_parser_registry() -> ParserRegistry:
    """Get the global parser registry singleton."""
    global _registry
    if _registry is None:
        _registry = ParserRegistry()
    return _registry


def parse_file(
    file_path: Path | str,
    language_id: str | None = None,
    use_cache: bool = True,
) -> ParseResult:
    """Parse a file from disk, automatically detecting language if not specified."""
    p = Path(file_path)
    if language_id is None:
        from coding_agent.core.semantic.detector import detect_language
        language_id = detect_language(p).language_id
    return get_parser_registry().parse_file(p, language_id, use_cache=use_cache)


def parse_source(
    source: str | bytes,
    language_id: str = "python",
    file_path: Path | None = None,
    old_tree: Any | None = None,
) -> ParseResult:
    """Parse source code string or bytes."""
    return get_parser_registry().parse_source(source, language_id, file_path=file_path, old_tree=old_tree)
