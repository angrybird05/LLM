"""Permission system: nothing risky runs without consent unless a mode allows it.

Modes (like Claude Code):
  default   - bash / write / edit each ask before running
  auto-edit - file edits run freely; bash still asks
  full-auto - everything runs; dangerous bash patterns still ask

The manager is UI-agnostic: callers inject `prompt_fn`, an async callable that
receives a PermissionRequest and returns "yes" | "no" | "always".
"""
import difflib
import re
import time as time_mod
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

PromptFn = Callable[["PermissionRequest"], Awaitable[str]]

RISKY_TOOLS = {"bash", "write", "edit"}
WRITE_TOOLS = {"write", "edit"}

# Commands that ask for confirmation in EVERY mode — one wrong call here can
# destroy a machine or a repo, so convenience never overrides them.
DANGEROUS_BASH_PATTERNS = [
    r"\bsudo\b",
    r"\bgit\s+push\s+(?:--force|-f)\b",
    r"\bgit\s+clean\b[^|;&]*\s-[a-z]*f",          # git clean -f/-fd/-fdx …
    r"\bdrop\s+(?:table|database)\b",
    r"\b(?:shutdown|reboot)\b",
    r"\bdel\s+/[sq]",
    r"\b(?:rd|rmdir)\s+/s\b",
    r"\bfind\b[^|;&]*\s-delete\b",
    r"\bremove-item\b[^|;&]*-recurse",
    r"\bformat\b\s+[a-z]:",
    r">\s*/dev/sd[a-z]",
]

_COMPILED_DANGER = [re.compile(p, re.IGNORECASE) for p in DANGEROUS_BASH_PATTERNS]


def _rm_recursive_force(command: str) -> str | None:
    """Detect rm with BOTH recursive and force flags in any spelling:
    `-rf`, `-fr`, `-Rf`, `-f -r`, `--recursive --force`, `--recursive -F`…
    Flag letters are collected as a set across all tokens so combined,
    split and long-form variants all match."""
    tokens = command.split()
    for i, tok in enumerate(tokens):
        base = tok.strip("\"'").lower().rstrip("/")
        if base not in ("rm", "rm.exe"):
            continue
        letters: set[str] = set()
        long_flags: set[str] = set()
        for nxt in tokens[i + 1:]:
            if nxt.startswith("--"):
                long_flags.add(nxt[2:].split("=", 1)[0].lower())
            elif len(nxt) > 1 and nxt.startswith("-"):
                letters |= set(nxt[1:].lower())
            elif nxt.startswith("-"):
                continue  # bare "-" argument, not a flag cluster
        recursive = bool(letters & {"r"}) or "recursive" in long_flags
        forced = "f" in letters or "force" in long_flags
        if recursive and forced:
            return tok
    return None


def matches_dangerous_bash(command: str) -> str | None:
    """Return the matched pattern text if the command is on the danger list."""
    hit = _rm_recursive_force(command)
    if hit:
        return hit
    for pattern in _COMPILED_DANGER:
        m = pattern.search(command)
        if m:
            return m.group(0)
    return None


class PermissionMode(StrEnum):
    DEFAULT = "default"
    AUTO_EDIT = "auto-edit"
    FULL_AUTO = "full-auto"


def parse_mode(value: str | None) -> PermissionMode:
    if not value:
        return PermissionMode.DEFAULT
    v = value.strip().lower().replace("_", "-")
    aliases = {
        "default": PermissionMode.DEFAULT,
        "ask": PermissionMode.DEFAULT,
        "auto-edit": PermissionMode.AUTO_EDIT,
        "autoedit": PermissionMode.AUTO_EDIT,
        "full-auto": PermissionMode.FULL_AUTO,
        "fullauto": PermissionMode.FULL_AUTO,
        "yolo": PermissionMode.FULL_AUTO,
    }
    if v not in aliases:
        raise ValueError(f"Unknown permission mode '{value}'. Use: default | auto-edit | full-auto")
    return aliases[v]


@dataclass
class PermissionRequest:
    tool_name: str
    arguments: dict[str, Any]
    preview: str          # human-readable diff/command preview
    danger_reason: str | None = None


@dataclass
class Decision:
    allowed: bool
    reason: str = ""


# ------------------------------------------------------------------- previews

def build_preview(tool_name: str, arguments: dict[str, Any]) -> str:
    """Render what this call would do, so the user can judge before it happens."""
    if tool_name == "bash":
        return f"$ {arguments.get('command', '')}"

    path = arguments.get("path", "?")
    if tool_name == "edit":
        old = arguments.get("old_string", "")
        new = arguments.get("new_string", "")
        return f"{path}\n{preview_diff(old, new)}"
    if tool_name == "write":
        new_content = arguments.get("content", "")
        old_content = _read_if_exists(path)
        if old_content is None:
            lines = new_content.splitlines()
            head = "\n".join(f"+ {ln}" for ln in lines[:20])
            more = f"\n… ({len(lines)} lines total)" if len(lines) > 20 else ""
            return f"{path} [new file]\n{head}{more}"
        return f"{path}\n{unified_diff(old_content, new_content)}"
    return str(arguments)[:500]


def _read_if_exists(path_str: str) -> str | None:
    """Read a file for the write-preview diff — ONLY inside the workspace.
    The tools themselves reject out-of-root paths, so the preview must not
    render arbitrary absolute-path contents either."""
    from coding_agent.core.tools.paths import resolve_in_workspace
    try:
        p = resolve_in_workspace(path_str)
    except ValueError:
        return None
    try:
        return p.read_text(encoding="utf-8", errors="replace") if p.is_file() else None
    except OSError:
        return None


def preview_diff(old: str, new: str) -> str:
    out = [f"- {line}" for line in old.splitlines()]
    out += [f"+ {line}" for line in new.splitlines()]
    shown = out[:40]
    if len(out) > 40:
        shown.append(f"… ({len(out) - 40} more lines)")
    return "\n".join(shown)


def unified_diff(old: str, new: str, context: int = 2) -> str:
    diff = difflib.unified_diff(
        old.splitlines(), new.splitlines(),
        fromfile="current", tofile="proposed", lineterm="", n=context,
    )
    lines = list(diff)
    shown = lines[:60]
    if len(lines) > 60:
        shown.append(f"… ({len(lines) - 60} more diff lines)")
    return "\n".join(shown)


# ------------------------------------------------------------------- records

@dataclass(frozen=True)
class PermissionRecord:
    """One risky-tool decision, kept for the UI's recent-activity log."""

    ts: str                # wall-clock HH:MM:SS at decision time
    tool_name: str
    verdict: str           # allowed | denied | asked-yes/no/always | revoked
    reason: str = ""       # mode name / session grant / danger-list / user choice


@dataclass
class PermissionManager:
    mode: PermissionMode = PermissionMode.DEFAULT
    prompt_fn: PromptFn | None = None
    history: deque[PermissionRecord] = field(
        default_factory=lambda: deque(maxlen=60))
    # Action-scoped "always" grants. Keys look like `bash:<command>` or
    # `edit:<abs-path>` so approving one benign command NEVER silences every
    # future bash prompt for the session (the old whole-tool grants did).
    session_allow: set[str] = field(default_factory=set)

    @classmethod
    def from_mode(cls, mode: str | None,
                  prompt_fn: PromptFn | None = None) -> "PermissionManager":
        return cls(mode=parse_mode(mode), prompt_fn=prompt_fn)

    def reset_session(self) -> None:
        """'always' approvals live only for the current session."""
        self.session_allow.clear()
        self.history.clear()

    # ------------------------------------------------------------ recording

    def _record(self, tool_name: str, verdict: str, reason: str = "") -> None:
        self.history.append(PermissionRecord(
            ts=time_mod.strftime("%H:%M:%S"),
            tool_name=tool_name,
            verdict=verdict,
            reason=reason,
        ))

    def _grant_key(self, tool_name: str, arguments: dict[str, Any]) -> str:
        """Action key an 'always' grant is stored under. bash grants cover ONE
        exact command; write/edit grants cover ONE path — never whole tools."""
        if tool_name == "bash":
            cmd = " ".join(str(arguments.get("command", "")).split())
            return f"bash:{cmd}"
        if tool_name in WRITE_TOOLS:
            try:
                from coding_agent.core.tools.paths import resolve_in_workspace
                p = str(resolve_in_workspace(str(arguments.get("path", ""))))
            except ValueError:
                p = str(arguments.get("path", ""))
            return f"{tool_name}:{p}"
        return tool_name

    def grants_for(self, tool_name: str) -> list[str]:
        """Active 'always' grant keys for one tool (UI display / revocation)."""
        prefix = f"{tool_name}:"
        return sorted(k[len(prefix):] for k in self.session_allow if k.startswith(prefix))

    def tool_policy(self, tool_name: str) -> str:
        """What happens for this tool under the CURRENT mode (UI table rows)."""
        if tool_name not in RISKY_TOOLS:
            return "runs freely (read-only)"
        n_grants = len(self.grants_for(tool_name))
        grant_note = f" · {n_grants} action grant(s)" if n_grants else ""
        if self.mode == PermissionMode.FULL_AUTO:
            note = " (danger list still asks)" if tool_name == "bash" else ""
            return f"auto-allowed{note}{grant_note}"
        if self.mode == PermissionMode.AUTO_EDIT and tool_name in WRITE_TOOLS:
            return f"auto-allowed{grant_note}"
        if n_grants:
            return "granted for specific action(s) this session"
        return "asks every call"

    def revoke(self, tool_name: str) -> bool:
        """Drop every 'always' grant for one tool; True if any existed."""
        keys = [k for k in self.session_allow if k.startswith(f"{tool_name}:")]
        for k in keys:
            self.session_allow.discard(k)
            self._record(tool_name, "revoked", f"user revoked: {k}")
        return bool(keys)

    def revoke_all(self) -> int:
        """Revoke every session grant; returns how many were dropped."""
        n = len(self.session_allow)
        for k in list(self.session_allow):
            self._record(k.split(":", 1)[0], "revoked", f"user revoked: {k}")
        self.session_allow.clear()
        return n

    async def check(self, tool_name: str, arguments: dict[str, Any]) -> Decision:
        if tool_name not in RISKY_TOOLS:
            return Decision(allowed=True)

        danger = None
        if tool_name == "bash":
            danger = matches_dangerous_bash(str(arguments.get("command", "")))

        # Mode shortcuts never bypass the danger list.
        if not danger:
            if self.mode == PermissionMode.FULL_AUTO:
                self._record(tool_name, "allowed", "mode full-auto")
                return Decision(allowed=True)
            if self.mode == PermissionMode.AUTO_EDIT and tool_name in WRITE_TOOLS:
                self._record(tool_name, "allowed", "mode auto-edit")
                return Decision(allowed=True)
            if self._grant_key(tool_name, arguments) in self.session_allow:
                self._record(tool_name, "allowed", "action grant")
                return Decision(allowed=True)

        if self.prompt_fn is None:
            self._record(tool_name, "denied", "no approval UI connected (headless)")
            return Decision(
                allowed=False,
                reason="No approval UI is connected, so risky tools are blocked. "
                       "Switch the permission mode to full-auto to allow them.",
            )

        request = PermissionRequest(
            tool_name=tool_name,
            arguments=arguments,
            preview=build_preview(tool_name, arguments),
            danger_reason=f"matches dangerous pattern: {danger}" if danger else None,
        )
        answer = await self.prompt_fn(request)

        if answer == "always":
            key = self._grant_key(tool_name, arguments)
            self.session_allow.add(key)
            self._record(tool_name, "asked-always", f"granted for this action: {key}")
            return Decision(allowed=True)
        if answer == "yes":
            self._record(tool_name, "asked-yes", "user approved once")
            return Decision(allowed=True)
        self._record(tool_name, "denied", "user denied")
        return Decision(allowed=False, reason="User declined this action.")
