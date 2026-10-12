"""Local shell execution: the `bash` tool runs on the host inside the workspace.

Docker sandbox mode is still available via SHELL_MODE=docker for untrusted code.
"""
import asyncio
import shutil
import subprocess
from pathlib import Path
from typing import Any

from coding_agent.config import settings
from coding_agent.core.tools.base import Tool, ToolParameter, ToolSchema, registry
from coding_agent.core.tools.paths import resolve_in_workspace, truncate_output


def _answers_ok(argv: list[str]) -> bool:
    """Health probe: can this shell echo back a sentinel? Catches the WSL
    launcher stub (System32\\bash.exe), which exits 1 when no distro exists."""
    try:
        out = subprocess.run(argv + ["echo __shell_ok__"],
                             capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return out.returncode == 0 and "__shell_ok__" in (out.stdout or "")


def _discover_shell() -> list[str] | None:
    """Find a WORKING bash: explicit Git Bash install locations first (its bin
    is often not on PATH), then whatever `which` reports - each verified once.
    """
    seen: set[Path] = set()
    candidates: list[Path] = []
    for raw in (
        r"C:\Program Files\Git\bin\bash.exe",
        r"C:\Program Files (x86)\Git\bin\bash.exe",
        shutil.which("bash"),
    ):
        if raw:
            p = Path(raw)
            if p not in seen:
                seen.add(p)
                candidates.append(p)
    for p in candidates:
        argv = [str(p), "-c"]
        if _answers_ok(argv):
            return argv
    return None


_SHELL_ARGV: list[str] | None = None
_SHELL_SEARCHED = False


def _pick_shell() -> list[str] | None:
    """Prefer a working bash; fall back to the system shell (COMSPEC on Windows).

    The discovery result is cached after one search: candidate probing spawns
    processes, and the answer cannot change mid-session in practice.
    """
    global _SHELL_ARGV, _SHELL_SEARCHED
    if settings.SHELL_EXECUTABLE:
        return [settings.SHELL_EXECUTABLE, "-c"]
    if not _SHELL_SEARCHED:
        _SHELL_SEARCHED = True
        _SHELL_ARGV = _discover_shell()
    return _SHELL_ARGV  # None -> create_subprocess_shell uses COMSPEC/cmd.exe


class LocalShellTool(Tool):
    @property
    def name(self) -> str: return "bash"

    @property
    def description(self) -> str:
        return (
            "Execute a shell command in the project workspace on the host machine. "
            "Use for running tests, builds, git, and inspecting files. "
            "Long-running or destructive commands need user approval."
        )

    @property
    def parameters(self) -> ToolSchema:
        return ToolSchema(properties={
            "command": ToolParameter(type="string", description="Command line to execute"),
            "cwd": ToolParameter(type="string",
                                 description="Working directory relative to the workspace"),
            "timeout": ToolParameter(type="integer", description="Timeout in seconds"),
        }, required=["command"])

    async def execute(self, arguments: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
        command = arguments["command"]
        timeout = int(arguments.get("timeout") or settings.SHELL_TIMEOUT)

        cwd = settings.workspace_path
        if cwd_arg := arguments.get("cwd"):
            cwd = resolve_in_workspace(cwd_arg)
            if not cwd.is_dir():
                return {"error": f"Working directory not found: {cwd}"}

        shell_argv = _pick_shell()
        try:
            if shell_argv:
                proc = await asyncio.create_subprocess_exec(
                    *shell_argv, command,
                    cwd=str(cwd),
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
            else:
                proc = await asyncio.create_subprocess_shell(
                    command,
                    cwd=str(cwd),
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
        except OSError as e:
            return {"error": f"Failed to start shell: {e}"}

        try:
            stdout_b, stderr_b = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except TimeoutError:
            proc.kill()
            return {
                "error": f"Command timed out after {timeout}s",
                "stdout": "", "stderr": "", "returncode": None, "success": False,
            }

        stdout = stdout_b.decode("utf-8", errors="replace")
        stderr = stderr_b.decode("utf-8", errors="replace")
        return {
            "stdout": truncate_output(stdout),
            "stderr": truncate_output(stderr),
            "returncode": proc.returncode,
            "success": proc.returncode == 0,
            "cwd": str(cwd),
        }


registry.register(LocalShellTool())
