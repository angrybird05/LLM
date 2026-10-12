"""Shared helpers for tool implementations: workspace scoping, ignore rules, truncation,
and atomic EOL-aware file writing (audit group B)."""
import os
import tempfile
from pathlib import Path

import pathspec

from coding_agent.config import settings

# Directories never worth searching or listing
ALWAYS_IGNORE_DIRS = {".git", ".venv", "venv", "__pycache__", "node_modules", ".mypy_cache",
                      ".pytest_cache", ".ruff_cache", ".idea", ".vscode"}


def resolve_in_workspace(path_str: str) -> Path:
    """Resolve a tool-supplied path inside the workspace root.

    Absolute paths are allowed when they land inside the workspace; everything
    else is rejected so the agent can't wander outside the project. Bare root
    references ("/", ".", "./", "") mean the workspace root itself — Unix-
    trained models reach for "/" when they mean "the top of the project".
    """
    root = settings.workspace_path
    if str(path_str).strip() in {"", ".", "./", ".\\", "/", "\\"}:
        return root
    p = Path(path_str)
    resolved = (p if p.is_absolute() else root / p).resolve()
    if resolved != root and root not in resolved.parents:
        raise ValueError(
            f"Path escapes the workspace root ({root}): {path_str}. "
            "Use paths relative to the workspace."
        )
    return resolved


def load_gitignore(root: Path) -> pathspec.PathSpec | None:
    """Load <root>/.gitignore patterns, if present."""
    gi = root / ".gitignore"
    try:
        if gi.is_file():
            return pathspec.PathSpec.from_lines("gitwildmatch", gi.read_text().splitlines())
    except OSError:
        pass
    return None


def iter_workspace_files(base: Path):
    """Yield files under base, skipping ignored dirs and .gitignore matches."""
    spec = load_gitignore(settings.workspace_path)
    for path in base.rglob("*"):
        if not path.is_file():
            continue
        rel = path.relative_to(settings.workspace_path)
        if any(part in ALWAYS_IGNORE_DIRS for part in rel.parts):
            continue
        if spec and spec.match_file(rel.as_posix()):
            continue
        yield path


def truncate_output(text: str) -> str:
    limit = settings.TOOL_OUTPUT_LIMIT
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n... [truncated, {len(text) - limit} more chars]"


# ------------------------------------------------------------------ atomic IO
# Audit B1/B2/B5: one shared EOL/encoding-preserving read/write layer used by
# write+edit so the file-corruption class dies at the root.

def detect_eol(text: str) -> str:
    """The dominant line ending of a sample: '\r\n' when CRLF lines outnumber
    lone-LF ones, else '\n'."""
    crlf = text.count("\r\n")
    lf = text.count("\n") - crlf
    return "\r\n" if crlf > lf else "\n"


def atomic_write_bytes(path: Path, data: bytes) -> None:
    """Write via temp file + os.replace: a failed or degenerate write can never
    destroy the original (audit B5 / long-output degeneration mitigation).
    Creates parent dirs."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as tmp:
            tmp.write(data)
            tmp.flush()
            os.fsync(tmp.fileno())
        os.replace(tmp_name, path)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def atomic_write_text(path: Path, text: str, eol: str = "\n") -> None:
    """Atomic UTF-8 text write. `text` must contain only '\n'; it is written
    back in the file's own convention via `eol` (audit B1)."""
    atomic_write_bytes(path, text.replace("\n", eol).encode("utf-8"))
