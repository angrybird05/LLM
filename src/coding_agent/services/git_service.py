"""Git integration & session checkpoints.

GitService wraps GitPython for the REPL's /diff, /commit, /git commands.
CheckpointStore is an in-memory undo stack of the files the agent changed this
session (write/edit), filled by the pre-tool hook wired into ChatService —
session-scoped by design, so it deliberately does not survive a restart.
Bash-driven file changes are outside checkpoint coverage; git itself is the
safety net there.
"""
from dataclasses import dataclass
from pathlib import Path

from git import GitCommandError, InvalidGitRepositoryError, Repo
from loguru import logger

from coding_agent.config import settings
from coding_agent.core.llm.base import LLMMessage, LLMProvider
from coding_agent.core.tools.paths import ALWAYS_IGNORE_DIRS, resolve_in_workspace

# Diff base for repos that have no commits yet
EMPTY_TREE = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"

# Directories ignored in git tracking (workspace ignored dirs + internal .agent dir)
GIT_IGNORE_DIRS = ALWAYS_IGNORE_DIRS | {".agent"}

FILE_CHANGING_TOOLS = {"write", "edit", "ast_edit", "ast_refactor"}

DIFF_DISPLAY_LIMIT = 8000


COMMIT_PROMPT = """\
Write a conventional-commit subject line for the changes described by the diff \
below (format: `type: summary` with type one of feat|fix|refactor|chore|docs|\
test|style). Base it ONLY on the diff. Reply with the subject line alone, \
max 72 chars, no quotes, no code fences.

Diff stat:
{stat}

Diff excerpt:
{excerpt}
"""


class GitService:
    """Thin GitPython facade rooted at the workspace."""

    def __init__(self, root: Path | str | None = None):
        self.root = Path(root) if root else settings.workspace_path
        self._repo: Repo | None = None
        self._checked = False

    @property
    def repo(self) -> Repo | None:
        if not self._checked:  # lazy: don't probe the filesystem until asked
            self._checked = True
            try:
                self._repo = Repo(self.root, search_parent_directories=True)
            except InvalidGitRepositoryError:
                self._repo = None
        return self._repo

    def _reset_cache(self):
        self._repo = None
        self._checked = False

    def is_repo(self) -> bool:
        return self.repo is not None

    def init(self) -> None:
        Repo.init(self.root)
        self._reset_cache()

    def branch(self) -> str:
        try:
            return self.repo.active_branch.name
        except (TypeError, ValueError):
            return "(detached)"

    def has_commits(self) -> bool:
        try:
            return self.repo.head.commit is not None
        except Exception:
            return False

    def _diff_base(self) -> list[str]:
        return ["HEAD"] if self.has_commits() else [EMPTY_TREE]

    def diff_stat(self, paths: list[str] | None = None) -> str:
        try:
            return self.repo.git.diff(*self._diff_base(), "--stat", "--", *(paths or []))
        except GitCommandError as e:
            logger.debug(f"diff-stat failed: {e}")
            return ""

    def diff_text(self, paths: list[str] | None = None, limit: int = DIFF_DISPLAY_LIMIT) -> str:
        try:
            text = self.repo.git.diff(*self._diff_base(), "--", *(paths or []))
        except GitCommandError as e:
            logger.debug(f"diff failed: {e}")
            return ""
        if len(text) > limit:
            text = text[:limit] + f"\n… [{len(text) - limit} more chars]"
        return text

    def untracked_files(self) -> list[str]:
        return [
            f for f in self.repo.untracked_files
            if f.rstrip("/").split("/")[0] not in GIT_IGNORE_DIRS
        ]

    def changed_paths(self) -> list[str]:
        """All working-tree changes vs HEAD (porcelain), staged or not."""
        try:
            out = self.repo.git.status("--porcelain").splitlines()
        except GitCommandError:
            return []
        paths = []
        for line in out:
            if not line.strip():
                continue
            name = line[3:].strip().strip('"')
            if "->" in name:  # renames: track the new side
                name = name.split("->")[-1].strip()
            if name.rstrip("/").split("/")[0] in GIT_IGNORE_DIRS:
                continue
            paths.append(name)
        return paths

    def stage(self, paths: list[str]) -> None:
        if paths:
            self.repo.git.add("--", *paths)

    def unstage_all(self) -> None:
        try:
            self.repo.git.reset()
        except GitCommandError as e:
            logger.debug(f"reset failed: {e}")

    def commit(self, message: str) -> str:
        self.repo.git.commit("-m", message)
        return str(self.repo.head.commit.hexsha)[:8]

    def latest_commit_info(self) -> dict | None:
        """Metadata for HEAD commit, or None if no commits/not a repo."""
        if not self.is_repo() or not self.has_commits():
            return None
        try:
            c = self.repo.head.commit
            time_str = ""
            if getattr(c, "committed_datetime", None):
                time_str = c.committed_datetime.strftime("%b %d %H:%M")
            return {
                "hexsha": str(c.hexsha)[:7],
                "summary": str(c.summary),
                "author": str(getattr(c.author, "name", "")),
                "time": time_str,
            }
        except Exception as e:
            logger.debug(f"latest_commit_info failed: {e}")
            return None

    def upstream_status(self) -> str | None:
        """Check tracking status (e.g. synced, ahead, behind)."""
        if not self.is_repo() or not self.has_commits():
            return None
        try:
            branch = self.repo.active_branch
            tracking = branch.tracking_branch()
            if tracking:
                ahead = len(list(self.repo.iter_commits(f"{tracking.name}..HEAD")))
                behind = len(list(self.repo.iter_commits(f"HEAD..{tracking.name}")))
                if ahead and behind:
                    return f"↑{ahead} ↓{behind}"
                if ahead:
                    return f"↑{ahead}"
                if behind:
                    return f"↓{behind}"
                return "synced"
        except Exception:
            return None
        return None


@dataclass
class ChangeRecord:
    path: Path
    before: bytes | None  # None = the file did not exist yet (agent-created)
    action: str


class CheckpointStore:
    """LIFO stack of agent file changes; /undo pops the last one."""

    def __init__(self):
        self.records: list[ChangeRecord] = []

    def record(self, path: Path, before: bytes | None, action: str) -> None:
        self.records.append(ChangeRecord(path=path, before=before, action=action))

    def touched(self) -> list[str]:
        """Unique paths changed this session, most recent first."""
        seen: dict[str, None] = {}
        for rec in reversed(self.records):
            seen.setdefault(str(rec.path), "")
        return list(seen)

    def undo_last(self) -> ChangeRecord | None:
        while self.records:
            rec = self.records.pop()
            try:
                if rec.before is None:
                    rec.path.unlink(missing_ok=True)
                else:
                    rec.path.parent.mkdir(parents=True, exist_ok=True)
                    rec.path.write_bytes(rec.before)
                return rec
            except OSError as e:
                logger.warning(f"could not undo {rec.path}: {e}")
        return None


def make_snapshot_hook(store: CheckpointStore):
    """ChatService pre-tool hook: snapshot a file's bytes before write/edit runs.

    Wired to fire only AFTER permission approval, so declined calls are never
    recorded — /undo would otherwise "restore" files nothing ever touched.
    """

    async def hook(tool_name: str, arguments: dict) -> None:
        if tool_name not in FILE_CHANGING_TOOLS:
            return
        raw = arguments.get("path")
        if not raw:
            return
        try:
            path = resolve_in_workspace(str(raw))
        except ValueError:
            return  # tool itself will reject it too
        try:
            before = path.read_bytes() if path.exists() else None
        except OSError as e:
            logger.warning(f"checkpoint skipped for {path}: {e}")
            return
        store.record(path, before, action=tool_name)

    return hook


async def generate_commit_message(
    provider: LLMProvider, stat: str, excerpt: str, model: str | None = None
) -> str:
    """One small LLM call -> conventional-commit subject. Empty string on any
    failure; callers fall back to a deterministic message."""
    try:
        response = await provider.complete(
            messages=[LLMMessage(role="user", content=COMMIT_PROMPT.format(
                stat=stat or "(no stat)", excerpt=(excerpt or "(no diff)")[:4000],
            ))],
            tools=None,
            model=model,
            temperature=0.2,
            max_tokens=120,
        )
    except Exception as e:
        logger.warning(f"commit-message generation failed: {e}")
        return ""
    text = (response.content or "").strip().strip("`").strip('"')
    line = next((ln.strip() for ln in text.splitlines() if ln.strip()), "")
    return line[:72]
