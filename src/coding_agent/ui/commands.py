"""Command registry + Textual command-palette provider.

One list drives three surfaces: the Ctrl+P palette, the "/" dropdown in the
prompt, and the help overlay - so they can never drift apart. Each entry maps
to an `app.action_cmd_<key>` handler.
"""
from textual.command import DiscoveryHit, Hit, Hits, Provider


class CommandSpec:
    def __init__(self, key: str, name: str, desc: str, takes_arg: bool = False):
        self.key = key              # action suffix: action_cmd_<key>
        self.name = name            # "/model"
        self.desc = desc
        self.takes_arg = takes_arg


COMMANDS: list[CommandSpec] = [
    CommandSpec("model", "/model", "show or switch the model"),
    CommandSpec("provider", "/provider", "show or switch provider"),
    CommandSpec("permissions", "/permissions", "review & control permissions (modes, grants, log)"),
    CommandSpec("context", "/context", "context window usage"),
    CommandSpec("tokens", "/tokens", "session token totals"),
    CommandSpec("files", "/files", "refresh + focus project tree"),
    CommandSpec("search", "/search", "grep the workspace", takes_arg=True),
    CommandSpec("run", "/run", "run a shell command (approval applies)", takes_arg=True),
    CommandSpec("git", "/git", "git status (/git init to start tracking)", takes_arg=True),
    CommandSpec("diff", "/diff", "working-tree diff viewer"),
    CommandSpec("undo", "/undo", "revert last agent file change"),
    CommandSpec("commit", "/commit", "stage + commit session changes"),
    CommandSpec("compact", "/compact", "summarize older history now"),
    CommandSpec("plan", "/plan", "read-only research turn -> plan", takes_arg=True),
    CommandSpec("memory", "/memory", "show AGENT.md / CLAUDE.md memory"),
    CommandSpec("history", "/history", "browse & resume past sessions (f4)"),
    CommandSpec("export", "/export", "export transcript to markdown"),
    CommandSpec("rename", "/rename", "rename current session", takes_arg=True),
    CommandSpec("delete", "/delete", "delete current session"),
    CommandSpec("tools", "/tools", "list agent tools"),
    CommandSpec("settings", "/settings", "session settings overview"),
    CommandSpec("clear", "/clear", "start a fresh session"),
    CommandSpec("new-project", "/new-project", "register & switch to a new project directory"),
    CommandSpec("open-project", "/open-project", "browse and switch registered projects"),
    CommandSpec("help", "/help", "keys and commands"),
    CommandSpec("exit", "/exit", "quit the console"),
]

COMMAND_LIST: list[tuple[str, str]] = [(c.name, c.desc) for c in COMMANDS]


def find_command(line: str) -> tuple[CommandSpec | None, str]:
    """'/model gpt' -> (spec, 'gpt'); unknown -> (None, line)."""
    parts = line.split(maxsplit=1)
    if not parts:
        return None, ""
    token = parts[0].lower()
    arg = parts[1].strip() if len(parts) > 1 else ""
    for spec in COMMANDS:
        if spec.name == token:
            return spec, arg
    return None, line.strip()


class AgentCommandProvider(Provider):
    """Ctrl+P palette backed by COMMANDS."""

    @property
    def _commands(self) -> list[CommandSpec]:
        return COMMANDS

    async def discover(self) -> Hits:
        for spec in self._commands:
            yield DiscoveryHit(
                f"{spec.name}  {spec.desc}",
                lambda k=spec.key: self.app.run_action(f"cmd_{k}"),
                help=spec.desc,
            )

    async def search(self, query: str) -> Hits:
        matcher = self.fuzzy_matcher
        lowered = query.lower().lstrip("/")
        scored = []
        for spec in self._commands:
            hay = f"{spec.name} {spec.desc}"
            score = matcher(query, hay.lower())
            if score > 0 or lowered and lowered in hay.lower():
                scored.append((score or 1, spec))
        for score, spec in sorted(scored, key=lambda pair: -pair[0]):
            display = f"{spec.name}  {spec.desc}"
            yield Hit(
                score,
                display,
                lambda k=spec.key: self.app.run_action(f"cmd_{k}"),
                help=spec.desc,
            )
