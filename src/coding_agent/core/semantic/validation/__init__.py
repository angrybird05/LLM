"""Validation engine facade.

Combines AST syntax error-node checks with optional fast linter verification.
"""
from __future__ import annotations

from pathlib import Path

from coding_agent.core.semantic.models import ValidationError
from coding_agent.core.semantic.validation.linter_check import run_linter_check
from coding_agent.core.semantic.validation.syntax_check import validate_syntax


class ValidationEngine:
    """Verifies that transformed code is syntactically and structurally sound."""

    def __init__(self, run_linters: bool = False) -> None:
        self.run_linters = run_linters

    def validate_code(self, code: str, file_path: Path) -> None:
        """Validate code string for a given file path.

        Raises ValidationError if syntax check fails.
        """
        errors = validate_syntax(code, file_path)
        if errors:
            raise ValidationError(
                f"Syntax validation failed for {file_path.name}",
                errors=errors,
            )

        if self.run_linters:
            lint_errors = run_linter_check(file_path, code)
            if lint_errors:
                # Linters can produce warnings; record if critical
                pass

    def validate_files(self, file_contents: dict[Path, str]) -> None:
        """Validate multiple modified files.

        Raises ValidationError on the first file that fails syntax check.
        """
        for path, code in file_contents.items():
            self.validate_code(code, path)


__all__ = [
    "ValidationEngine",
    "run_linter_check",
    "validate_syntax",
]
