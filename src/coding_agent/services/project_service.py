"""ProjectRegistry: manages ~/.agent/projects.json.

Schema:
{
  "last_project": "/abs/path/to/project",
  "projects": [
    {"name": "my-app", "path": "/abs/path/to/my-app", "added_at": "ISO-8601"},
    ...
  ]
}
"""
import json
import os
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

REGISTRY_PATH = Path.home() / ".agent" / "projects.json"


@dataclass
class ProjectEntry:
    name: str
    path: str
    added_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


@dataclass
class ProjectRegistry:
    last_project: Optional[str] = None
    projects: list[ProjectEntry] = field(default_factory=list)

    # ── persistence ──────────────────────────────────────────────────────────

    @classmethod
    def load(cls) -> "ProjectRegistry":
        if not REGISTRY_PATH.exists():
            return cls()
        try:
            data = json.loads(REGISTRY_PATH.read_text())
            entries = [ProjectEntry(**e) for e in data.get("projects", [])]
            return cls(last_project=data.get("last_project"), projects=entries)
        except Exception:
            return cls()

    def save(self) -> None:
        REGISTRY_PATH.parent.mkdir(parents=True, exist_ok=True)
        REGISTRY_PATH.write_text(json.dumps({
            "last_project": self.last_project,
            "projects": [asdict(p) for p in self.projects],
        }, indent=2))

    # ── mutations ────────────────────────────────────────────────────────────

    def add_or_update(self, name: str, path: str) -> ProjectEntry:
        """Register a project. If the path already exists, update its name."""
        abs_path = str(Path(path).expanduser().resolve())
        for entry in self.projects:
            if entry.path == abs_path:
                entry.name = name
                return entry
        entry = ProjectEntry(name=name, path=abs_path)
        self.projects.append(entry)
        return entry

    def set_last(self, path: str) -> None:
        self.last_project = str(Path(path).expanduser().resolve())

    def remove(self, path: str) -> None:
        abs_path = str(Path(path).expanduser().resolve())
        self.projects = [p for p in self.projects if p.path != abs_path]
        if self.last_project == abs_path:
            self.last_project = self.projects[-1].path if self.projects else None

    def find_by_path(self, path: str) -> Optional[ProjectEntry]:
        abs_path = str(Path(path).expanduser().resolve())
        return next((p for p in self.projects if p.path == abs_path), None)
