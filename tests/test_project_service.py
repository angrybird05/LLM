import tempfile
from pathlib import Path
from unittest.mock import patch

from coding_agent.services.project_service import ProjectEntry, ProjectRegistry


def test_project_registry_crud():
    with tempfile.TemporaryDirectory() as tmpdir:
        reg_path = Path(tmpdir) / "projects.json"
        with patch("coding_agent.services.project_service.REGISTRY_PATH", reg_path):
            reg = ProjectRegistry.load()
            assert len(reg.projects) == 0
            assert reg.last_project is None

            # Add projects
            reg.add_or_update("proj-1", "/path/1")
            reg.add_or_update("proj-2", "/path/2")
            reg.set_last("/path/1")
            reg.save()

            assert reg_path.exists()

            # Reload and verify
            reg2 = ProjectRegistry.load()
            assert reg2.last_project == "/path/1"
            assert len(reg2.projects) == 2
            assert reg2.projects[0].name == "proj-1"
            assert reg2.projects[1].name == "proj-2"

            # Remove project
            reg2.remove("/path/1")
            assert len(reg2.projects) == 1
            # Falls back to remaining project
            assert reg2.last_project == "/path/2"
            reg2.save()

            reg3 = ProjectRegistry.load()
            assert len(reg3.projects) == 1
            assert reg3.projects[0].name == "proj-2"
            assert reg3.last_project == "/path/2"

            # Remove last project
            reg3.remove("/path/2")
            assert len(reg3.projects) == 0
            assert reg3.last_project is None


def test_new_project_directory_resolution():
    with tempfile.TemporaryDirectory() as tmpdir:
        parent = Path(tmpdir)

        # 1. Address is parent folder and name is "calculator"
        raw_path = str(parent)
        name = "calculator"
        p = Path(raw_path).expanduser().resolve()
        project_dir = p if p.name == name else p / name
        assert project_dir == parent / "calculator"

        project_dir.mkdir(parents=True, exist_ok=True)
        assert project_dir.is_dir()
        # Verify the parent directory was not used as the project root
        assert project_dir != parent

        # 2. Address already includes project name
        raw_path2 = str(parent / "calculator")
        p2 = Path(raw_path2).expanduser().resolve()
        project_dir2 = p2 if p2.name == name else p2 / name
        assert project_dir2 == parent / "calculator"
