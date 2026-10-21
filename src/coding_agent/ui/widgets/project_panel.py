"""ProjectPanel: the left column - file tree, git state, workspace stats.

The tree populates directories lazily as they are expanded, skipping the same
junk the agent's own tools skip (ALWAYS_IGNORE_DIRS + .gitignore via pathspec).
Enter on a file opens a read-only preview overlay. Git/stats refreshes run in
thread workers in the app so the UI never blocks. Session history deliberately
lives only behind f4 /history - one surface, not two.
"""
from pathlib import Path

from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import Button, Static, Tree
from textual.widgets.tree import TreeNode

from coding_agent.config import settings
from coding_agent.core.tools.paths import ALWAYS_IGNORE_DIRS
from coding_agent.ui import theme as T

MAX_ENTRIES_PER_DIR = 400

# Extension -> foreground color for tree entries (dirs stay bold default).
FILE_COLORS = {
    ".py": "#34d399",
    ".pyw": "#34d399",
    ".md": "#22d3ee",
    ".rst": "#22d3ee",
    ".txt": "#c7d5e8",
    ".json": "#a78bfa",
    ".yaml": "#a78bfa",
    ".yml": "#a78bfa",
    ".toml": "#a78bfa",
    ".cfg": "#a78bfa",
    ".ini": "#a78bfa",
    ".js": "#fbbf24",
    ".ts": "#fbbf24",
    ".tsx": "#fbbf24",
    ".jsx": "#fbbf24",
    ".html": "#fb923c",
    ".css": "#38bdf8",
    ".sh": "#34d399",
    ".ps1": "#38bdf8",
    ".bat": "#38bdf8",
    ".sql": "#f87171",
    ".env": "#fbbf24",
}


def file_color(name: str) -> str | None:
    return FILE_COLORS.get(Path(name).suffix.lower())


def count_workspace_files(root: Path) -> int:
    """File count honoring ALWAYS_IGNORE_DIRS (pruned walk - .venv and
    node_modules never descend), so the number is honest on real repos."""
    import os

    n = 0
    for _dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in ALWAYS_IGNORE_DIRS]
        n += len(filenames)
    return n


def _gitignore_spec(root: Path):
    try:
        import pathspec

        gi = root / ".gitignore"
        if gi.is_file():
            return pathspec.PathSpec.from_lines(
                "gitignore", gi.read_text(encoding="utf-8", errors="replace").splitlines()
            )
    except Exception:
        return None
    return None


class ProjectPanel(Vertical):
    DEFAULT_CSS = f"""
    ProjectPanel {{
        width: 30;
        min-width: 26;
        border: round {T.BORDER};
        background: {T.SURFACE};
        padding: 0 1;
    }}
    #ptree {{
        height: 1fr;
        background: transparent;
        padding-top: 1;
    }}
    .pbox {{
        height: auto;
        border-top: solid {T.BORDER};
        color: {T.MUTED};
        padding: 0 0;
        margin-top: 0;
    }}
    #proj-btn-row {{
        height: 1;
        margin-top: 1;
        margin-bottom: 0;
        padding: 0 0;
        align: center middle;
    }}
    #proj-btn-row Button {{
        height: 1;
        min-height: 1;
        max-height: 1;
        border: none;
        padding: 0;
        margin: 0 1;
        width: 1fr;
        min-width: 6;
    }}
    """

    def __init__(self):
        super().__init__()
        self.root_path = settings.workspace_path
        self._spec = _gitignore_spec(self.root_path)
        self.touched_files: list[str] = []  # agent-touched this session (rel)
        # Wired by the app: on_open_file(path) -> preview overlay.
        self.on_open_file = None  # type: ignore[assignment]

    def compose(self) -> ComposeResult:
        yield Static(Text("PROJECT", style=f"bold {T.MUTED}"), id="ptitle")
        yield Tree("workspace", id="ptree")
        yield Static("", id="pstats", classes="pbox")
        with Horizontal(id="proj-btn-row"):
            yield Button("＋ New", id="btn-new-project", variant="primary")
            yield Button("⏏ Open", id="btn-open-project")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-new-project":
            self.app.action_cmd_new_project("")
        elif event.button.id == "btn-open-project":
            self.app.action_cmd_open_project("")

    def on_mount(self) -> None:
        self.rebuild_tree()

    # ------------------------------------------------------------------ tree

    def rebuild_tree(self) -> None:
        tree = self.query_one("#ptree", Tree)
        tree.clear()
        name = self.root_path.name or str(self.root_path)
        node = tree.root
        node.set_label(name + "/")
        self._populate(node)
        node.expand()

    def _populate(self, node: TreeNode) -> None:
        """Fill a directory node's children (dirs first, junk skipped)."""
        base = node.data or self.root_path
        if not isinstance(base, Path):
            base = self.root_path
        try:
            entries = sorted(
                [e for e in base.iterdir()
                 if e.name not in ALWAYS_IGNORE_DIRS],
                key=lambda p: (not p.is_dir(), p.name.lower()),
            )[:MAX_ENTRIES_PER_DIR]
        except OSError:
            return
        for entry in entries:
            rel = entry.relative_to(self.root_path).as_posix()
            ignored = bool(self._spec) and (
                self._spec.match_file(rel)
                or (entry.is_dir() and self._spec.match_file(rel + "/"))
            )
            if ignored:
                continue
            if entry.is_dir():
                child = node.add(entry.name + "/", data=entry)
                child.allow_expand = True
            else:
                node.add_leaf(
                    Text(entry.name, style=file_color(entry.name) or T.TEXT),
                    data=entry,
                )

    def on_tree_node_expanded(self, event: Tree.NodeExpanded) -> None:
        """Lazy population: a freshly expanded dir with no children yet gets
        filled here (root is pre-filled at rebuild; deeper levels load as
        they are opened)."""
        data = event.node.data
        if isinstance(data, Path) and data.is_dir() and not event.node.children:
            self._populate(event.node)
            if not event.node.children:   # truly empty -> not expandable
                event.node.allow_expand = False
                event.node.collapse()

    def on_tree_node_selected(self, event: Tree.NodeSelected) -> None:
        """Enter/click on a file leaf opens the read-only preview overlay."""
        data = getattr(event.node, "data", None)
        if isinstance(data, Path) and data.is_file() and self.on_open_file:
            self.on_open_file(data)

    # ------------------------------------------------------------------- git

    def apply_git_info(self, branch: str | None, changed: list[str],
                       untracked: int, is_repo: bool) -> None:
        """Git indicators have moved to the right panel (SystemPanel)."""
        pass

    def apply_stats(self, n_files: int | None, cwd_text: str) -> None:
        box = self.query_one("#pstats", Static)
        body = Text()
        body.append(" STATS ", style=f"bold {T.MUTED}")
        body.append(f"{n_files if n_files is not None else '?'} files\n", style=T.TEXT)
        body.append(" CWD   ", style=f"bold {T.MUTED}")
        body.append(cwd_text, style=T.FAINT)
        box.update(body)


def _plain_rel(rel: str) -> str:
    return rel
