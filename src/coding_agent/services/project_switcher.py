"""project_switcher: clean-restart project switching via os.execv."""
import os
import sys
from pathlib import Path

from coding_agent.services.project_service import ProjectRegistry


def switch_project(new_path: str) -> None:
    """Save the new project as last_project and re-exec this process.

    The re-exec inherits the full environment with WORKSPACE_ROOT overridden,
    which takes priority over the .env file value in pydantic-settings.
    """
    abs_path = str(Path(new_path).expanduser().resolve())
    registry = ProjectRegistry.load()
    registry.set_last(abs_path)
    registry.save()

    env = dict(os.environ)
    env["WORKSPACE_ROOT"] = abs_path
    # Remove any cached DATABASE_URL so the new project's DB is used.
    env.pop("DATABASE_URL", None)

    # Re-exec: replace this process image with a fresh one.
    os.execve(sys.executable, [sys.executable] + sys.argv, env)
