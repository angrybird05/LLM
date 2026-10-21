"""Atomic transaction and rollback manager for multi-file semantic modifications.

Guarantees all-or-nothing execution across multiple files:
- Pre-write snapshotting of all affected files
- In-memory pre-commit syntax validation
- Two-phase staged writing via atomic os.replace
- Immediate auto-rollback on any I/O error or syntax failure
"""
from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from coding_agent.core.semantic.models import (
    RefactorPlan,
    SemanticError,
    ValidationError,
)
from coding_agent.core.semantic.patch import PatchManager
from coding_agent.core.semantic.validation import ValidationEngine


@dataclass
class FileSnapshot:
    path: Path
    original_bytes: bytes | None  # None if file didn't exist prior to transaction


class TransactionManager:
    """Ensures atomic execution and rollback for multi-file edits."""

    def __init__(
        self,
        checkpoint_store: Any | None = None,
        validation_engine: ValidationEngine | None = None,
    ) -> None:
        self.checkpoint_store = checkpoint_store
        self.validator = validation_engine or ValidationEngine()
        self.patch_manager = PatchManager()

    def apply_plan(
        self,
        plan: RefactorPlan,
        file_contents: dict[Path, str] | None = None,
    ) -> dict[Path, str]:
        """Apply a RefactorPlan atomically to disk after validation.

        Returns the dictionary of {Path: new_content_str}.
        Raises ValidationError or SemanticError if validation or writing fails,
        with guaranteed full rollback.
        """
        # 1. Simulate edits in-memory to get new contents
        new_contents, _ = self.patch_manager.preview_plan(plan, file_contents)

        # 2. Apply the transaction on new_contents
        return self.apply_file_contents(new_contents)

    def apply_file_contents(self, file_contents: dict[Path, str]) -> dict[Path, str]:
        """Atomically commit a dictionary of {Path: new_content} to disk."""
        # 1. In-memory validation before touching disk
        self.validator.validate_files(file_contents)

        # 2. Snapshot current state of all target files
        snapshots: list[FileSnapshot] = []
        for path in file_contents.keys():
            path = path.resolve()
            original_bytes = path.read_bytes() if path.exists() else None
            snapshots.append(FileSnapshot(path=path, original_bytes=original_bytes))

        # 3. Stage writes to temporary files
        staged_files: list[tuple[str, Path]] = []
        try:
            for path, content in file_contents.items():
                path = path.resolve()
                path.parent.mkdir(parents=True, exist_ok=True)
                fd, tmp_name = tempfile.mkstemp(
                    dir=str(path.parent),
                    prefix=f".{path.name}.",
                    suffix=".tmp",
                )
                with os.fdopen(fd, "wb") as tmp:
                    data = content.encode("utf-8")
                    tmp.write(data)
                    tmp.flush()
                    os.fsync(tmp.fileno())
                staged_files.append((tmp_name, path))

            # 4. Commit all staged files via atomic replacement
            committed_paths: list[Path] = []
            try:
                for tmp_name, target_path in staged_files:
                    os.replace(tmp_name, target_path)
                    committed_paths.append(target_path)
            except Exception as commit_err:
                # Rollback committed files
                self._rollback(snapshots)
                raise SemanticError(f"Atomic commit failed: {commit_err}") from commit_err

            # 5. Record to CheckpointStore if available
            if self.checkpoint_store is not None:
                for snap in snapshots:
                    try:
                        self.checkpoint_store.record(
                            path=snap.path,
                            before=snap.original_bytes,
                            action="ast_transaction",
                        )
                    except Exception:
                        pass

            return file_contents

        except Exception:
            # Clean up any remaining staged tmp files
            for tmp_name, _ in staged_files:
                try:
                    if os.path.exists(tmp_name):
                        os.unlink(tmp_name)
                except OSError:
                    pass
            raise

    def _rollback(self, snapshots: list[FileSnapshot]) -> None:
        """Restore all files from snapshots."""
        for snap in snapshots:
            try:
                if snap.original_bytes is None:
                    snap.path.unlink(missing_ok=True)
                else:
                    snap.path.parent.mkdir(parents=True, exist_ok=True)
                    snap.path.write_bytes(snap.original_bytes)
            except OSError:
                pass


__all__ = [
    "FileSnapshot",
    "TransactionManager",
]
