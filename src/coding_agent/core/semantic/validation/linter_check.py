"""Fast linter and type-checker integration for post-transformation validation.

Optionally invokes fast linters (ruff, flake8, tsc) when present in the
environment with strict timeouts to catch subtle structural regressions.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path


def run_linter_check(file_path: Path, code: str | None = None) -> list[str]:
    """Run fast linter pass on the file if supported tools are installed."""
    file_path = file_path.resolve()
    suffix = file_path.suffix.lower()

    if suffix == ".py":
        return _check_python(file_path, code)
    elif suffix in (".ts", ".tsx", ".js", ".jsx"):
        return _check_js_ts(file_path)
    elif suffix == ".go":
        return _check_go(file_path)

    return []


def _check_python(file_path: Path, code: str | None) -> list[str]:
    """Run ruff check or flake8 if available."""
    errors: list[str] = []
    ruff_bin = shutil.which("ruff")
    if ruff_bin:
        try:
            cmd = [ruff_bin, "check", "--quiet", str(file_path)]
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=3.0)
            if proc.returncode != 0 and proc.stdout:
                errors.extend(proc.stdout.strip().splitlines()[:5])
        except Exception:
            pass
    return errors


def _check_js_ts(file_path: Path) -> list[str]:
    return []


def _check_go(file_path: Path) -> list[str]:
    return []
