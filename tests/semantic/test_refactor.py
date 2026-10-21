"""Tests for cross-file and structural refactorings."""
import tempfile
from pathlib import Path
import pytest

from coding_agent.core.semantic.refactor import (
    RefactoringCoordinator,
    plan_rename,
    plan_extract_function,
    plan_change_signature,
)
from coding_agent.core.semantic.models import RefactorOperation


def test_rename_across_files():
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        mod_a = root / "mod_a.py"
        mod_b = root / "mod_b.py"

        mod_a.write_text("def calculate_tax(amount):\n    return amount * 0.1\n")
        mod_b.write_text("from mod_a import calculate_tax\n\ntotal = calculate_tax(100)\n")

        plan = plan_rename(
            workspace_root=root,
            file_path=mod_a,
            symbol_name="calculate_tax",
            new_name="compute_tax",
        )

        assert plan.operation == RefactorOperation.RENAME
        assert len(plan.affected_files) == 2
        assert len(plan.edits) >= 2


def test_extract_function():
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        source_file = root / "service.py"
        source_file.write_text(
            "def handle_request():\n"
            "    user = 'Alice'\n"
            "    print('Validating', user)\n"
            "    return True\n"
        )

        plan = plan_extract_function(
            file_path=source_file,
            range_str="3:3",
            new_function_name="validate_user",
        )

        assert plan.operation == RefactorOperation.EXTRACT_FUNCTION
        assert len(plan.edits) == 2


def test_change_signature():
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        source_file = root / "api.py"
        source_file.write_text("def fetch_user(user_id):\n    pass\n")

        plan = plan_change_signature(
            file_path=source_file,
            function_name="fetch_user",
            new_parameters=["user_id: int", "include_details: bool = False"],
        )

        assert plan.operation == RefactorOperation.CHANGE_SIGNATURE
        assert len(plan.edits) == 1
        assert "include_details" in plan.edits[0].replacement_text
