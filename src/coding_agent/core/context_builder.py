"""Builds the system prompt the agent sees every turn.

Sections: identity, environment (OS/shell/cwd), workspace tree, tool guidance,
project memory (AGENT.md / CLAUDE.md), global memory (~/.agent/AGENT.md) and a
git snapshot. Everything is gathered fresh per user turn so the prompt reflects
reality, while staying byte-identical across the iterations of one agentic loop
(provider prompt caches like stable prefixes).
"""
import platform
import sys
from pathlib import Path

from coding_agent.config import settings
from coding_agent.core.tools.paths import ALWAYS_IGNORE_DIRS

# Project-level memory files, in priority order
PROJECT_MEMORY_FILES = ("AGENT.md", "CLAUDE.md")
GLOBAL_MEMORY_PATH = Path.home() / ".agent" / "AGENT.md"

MEMORY_CHAR_LIMIT = 8000      # per memory file — protect the context window
TREE_MAX_ENTRIES = 40         # per directory level
TREE_DEPTH = 2


def find_project_memory(workspace: Path) -> tuple[Path | None, str]:
    """Return (path, label) of the first project memory file that exists."""
    for name in PROJECT_MEMORY_FILES:
        p = workspace / name
        if p.is_file():
            return p, name
    return None, ""


def read_memory_file(path: Path) -> str:
    """Read a memory file, capped; returns '' when unreadable/empty."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return ""
    if not text:
        return ""
    if len(text) > MEMORY_CHAR_LIMIT:
        text = text[:MEMORY_CHAR_LIMIT] + "\n... [memory truncated]"
    return text


def git_snapshot(workspace: Path) -> str:
    """One-line-per-fact git summary; degrades gracefully outside a repo."""
    try:
        import git  # GitPython

        repo = git.Repo(workspace, search_parent_directories=True)
    except Exception:
        return "(not a git repository)"
    try:
        lines: list[str] = []
        try:
            lines.append(f"branch: {repo.active_branch.name}")
        except TypeError:  # detached HEAD
            lines.append(f"branch: (detached at {repo.head.commit.hexsha[:8]})")
        dirty = repo.is_dirty()
        untracked = len(repo.untracked_files)
        state = "dirty" if dirty or untracked else "clean"
        details = []
        if dirty:
            details.append("modified files")
        if untracked:
            details.append(f"{untracked} untracked")
        lines.append("status: " + state + (f" ({', '.join(details)})" if details else ""))
        subjects = [c.summary.strip() for c in repo.iter_commits(max_count=5)]
        if subjects:
            lines.append("recent commits:")
            lines.extend(f"  - {s}" for s in subjects)
        return "\n".join(lines)
    except Exception as e:
        return f"(git snapshot unavailable: {e})"


def workspace_tree(workspace: Path, depth: int = TREE_DEPTH) -> str:
    """Shallow tree of top-level dirs/files, skipping ignore-listed entries."""
    if not workspace.is_dir():
        return "(workspace directory not found)"

    def render(dir_path: Path, level: int, prefix: str) -> list[str]:
        try:
            entries = sorted(
                dir_path.iterdir(),
                key=lambda e: (e.is_file(), e.name.lower()),
            )
        except OSError:
            return []
        out: list[str] = []
        shown = 0
        for entry in entries:
            if entry.name.startswith(".") or entry.name in ALWAYS_IGNORE_DIRS:
                continue
            if shown >= TREE_MAX_ENTRIES:
                out.append(f"{prefix}...")
                break
            marker = "/" if entry.is_dir() else ""
            out.append(f"{prefix}{entry.name}{marker}")
            shown += 1
            if entry.is_dir() and level < depth:
                out.extend(render(entry, level + 1, prefix + "  "))
        return out

    lines = [workspace.name + "/"]
    lines.extend(render(workspace, 1, "  "))
    return "\n".join(lines)


def build_system_prompt(workspace_root: str | Path | None = None,
                        global_memory_path: Path | None = None) -> str:
    """Assemble the full system prompt from live environment facts."""
    ws = Path(workspace_root) if workspace_root else settings.workspace_path
    gmem = global_memory_path or GLOBAL_MEMORY_PATH

    shell_desc = settings.SHELL_EXECUTABLE or (
        "bash" if settings.SHELL_MODE == "local" else settings.SHELL_MODE)

    sections: list[str] = []

    sections.append(
        "You are coding-agent, an interactive terminal coding agent. You help the "
        "user work on the codebase in your working directory: reading, searching, "
        "editing files and running shell commands via your tools.\n\n"
        "Guidelines:\n"
        "- Read a file before editing it; prefer `edit` over rewriting whole files with `write`.\n"
        "- Use `ast_refactor` for renaming symbols, moving code, or updating signatures across files.\n"
        "- Use `ast_edit` to replace or insert syntax nodes (methods, classes, function bodies) with structural safety.\n"
        "- Use `edit` for minor text/docstring/comment tweaks where AST parsing is unnecessary.\n"
        "- Use `find_symbols` to inspect symbols or locate references across the codebase.\n"
        "- Keep responses concise and technical; show diffs/code instead of describing them.\n"
        "- When a tool returns an error, adjust your approach instead of retrying blindly.\n"
        "- The user can see tool output in their terminal; don't repeat it verbatim."
    )

    sections.append(
        "# Environment\n"
        f"- OS: {platform.system()} {platform.release()} ({platform.machine()})\n"
        f"- Shell backend: {shell_desc}\n"
        f"- Python: {sys.version.split()[0]}\n"
        f"- Working directory: {ws}\n"
        f"- Date: {_today()}"
    )

    sections.append(f"# Workspace layout\n```\n{workspace_tree(ws)}\n```")

    tools = _tool_summary()
    if tools:
        sections.append("# Available tools\n" + tools)

    mem_parts: list[str] = []
    proj_path, proj_label = find_project_memory(ws)
    proj_mem = read_memory_file(proj_path) if proj_path else ""
    if proj_mem:
        mem_parts.append(f"## Project memory ({proj_label})\n{proj_mem}")
    glob_mem = read_memory_file(gmem)
    if glob_mem:
        mem_parts.append(f"## Global memory ({gmem})\n{glob_mem}")
    if mem_parts:
        sections.append(
            "# Project memory\nInstructions below come from the user's memory files. "
            "Follow them; they override your defaults where they conflict.\n"
            + "\n\n".join(mem_parts)
        )

    snap = git_snapshot(ws)
    if snap:
        sections.append(f"# Git\n{snap}")

    return "\n\n".join(sections)


def _tool_summary() -> str:
    from coding_agent.core.tools import registry

    lines = []
    for t in registry.list():
        desc = " ".join(t.description.split())
        lines.append(f"- {t.name}: {desc}")
    return "\n".join(lines)


def _today() -> str:
    import datetime

    return datetime.date.today().isoformat()
