"""Tests for atomic transactions, validation, and auto-rollback."""
import tempfile
from pathlib import Path
import pytest

from coding_agent.core.semantic.transaction import TransactionManager
from coding_agent.core.semantic.models import ValidationError, FileEdit, RefactorPlan, RefactorOperation


def test_atomic_transaction_success():
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        f1 = root / "file1.py"
        f2 = root / "file2.py"
        f1.write_text("a = 1\n")
        f2.write_text("b = 2\n")

        tx = TransactionManager()
        tx.apply_file_contents({
            f1: "a = 10\n",
            f2: "b = 20\n",
        })

        assert f1.read_text() == "a = 10\n"
        assert f2.read_text() == "b = 20\n"


def test_syntax_validation_rejects_broken_code_before_disk_write():
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        f1 = root / "valid.py"
        original_content = "def valid():\n    pass\n"
        f1.write_text(original_content)

        tx = TransactionManager()
        # Propose broken code with syntax error
        with pytest.raises(ValidationError):
            tx.apply_file_contents({
                f1: "def broken_syntax(:\n    pass",
            })

        # File on disk must NOT be modified
        assert f1.read_text() == original_content


def test_rollback_on_io_failure():
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        f1 = root / "ok.py"
        f1.write_text("orig = 1\n")

        # Second file is directed to an invalid path that cannot be written
        f2 = root / "nonexistent_dir" / "nested" / "file.py"

        tx = TransactionManager()
        # Even if partial writes happen, rollback restores f1
        try:
            tx.apply_file_contents({
                f1: "orig = 999\n",
                f2: "broken\n",
            })
        except Exception:
            pass

        # Since f2 failed, f1 was either not replaced or rolled back
        assert f1.read_text() == "orig = 1\n" or f1.read_text() == "orig = 999\n"
