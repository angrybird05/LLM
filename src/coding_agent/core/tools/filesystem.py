from typing import Any

import aiofiles

from coding_agent.config import settings
from coding_agent.core.tools.base import Tool, ToolParameter, ToolSchema, registry
from coding_agent.core.tools.paths import (
    ALWAYS_IGNORE_DIRS,
    atomic_write_bytes,
    iter_workspace_files,
    resolve_in_workspace,
    truncate_output,
)


class ReadTool(Tool):
    @property
    def name(self) -> str: return "read"
    @property
    def description(self) -> str: return "Read file contents (optionally a line range)"

    @property
    def parameters(self) -> ToolSchema:
        return ToolSchema(properties={
            "path": ToolParameter(type="string", description="File path to read"),
            "offset": ToolParameter(type="integer", description="Line offset (0-based)"),
            "limit": ToolParameter(type="integer", description="Max lines to read"),
        }, required=["path"])

    async def execute(self, arguments: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
        if not arguments.get("path"):
            return {"error": "Missing required argument 'path' (file path to read)."}
        path = resolve_in_workspace(arguments["path"])
        if not path.is_file():
            return {"error": f"File not found: {path}"}
        offset = max(int(arguments.get("offset") or 0), 0)
        limit = arguments.get("limit")
        async with aiofiles.open(path, encoding="utf-8", errors="replace") as f:
            lines = await f.readlines()
        selected = lines[offset:offset + int(limit)] if limit else lines[offset:]
        truncated = False
        if len(selected) > settings.READ_LINE_LIMIT:
            selected = selected[:settings.READ_LINE_LIMIT]
            truncated = True
        content = "".join(selected)
        result = {
            "content": truncate_output(content),
            "total_lines": len(lines),
            "shown_lines": len(selected),
        }
        if truncated:
            result["note"] = (
                f"Output capped at {settings.READ_LINE_LIMIT} lines; use offset/limit to page."
            )
        return result


class WriteTool(Tool):
    @property
    def name(self) -> str: return "write"
    @property
    def description(self) -> str: return "Write content to a file (overwrites; creates parent dirs)"

    @property
    def parameters(self) -> ToolSchema:
        return ToolSchema(properties={
            "path": ToolParameter(type="string", description="File path to write"),
            "content": ToolParameter(type="string", description="Content to write"),
        }, required=["path", "content"])

    async def execute(self, arguments: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
        path = resolve_in_workspace(arguments["path"])
        existed = path.exists()
        atomic_write_bytes(path, str(arguments.get("content", "")).encode("utf-8"))
        return {"success": True, "path": str(path), "created": not existed,
                "bytes": path.stat().st_size}


class ListTool(Tool):
    @property
    def name(self) -> str: return "list"
    @property
    def description(self) -> str: return "List directory contents"

    @property
    def parameters(self) -> ToolSchema:
        return ToolSchema(properties={
            "path": ToolParameter(type="string", description="Directory path (default: root)"),
        }, required=[])

    async def execute(self, arguments: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
        path = resolve_in_workspace(arguments.get("path") or ".")
        if not path.is_dir():
            return {"error": f"Not a directory: {path}"}
        items = []
        for item in sorted(path.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower())):
            if item.name in ALWAYS_IGNORE_DIRS or item.name.startswith(".git"):
                continue
            entry: dict[str, Any] = {"name": item.name, "type": "dir" if item.is_dir() else "file"}
            if item.is_file():
                entry["size"] = item.stat().st_size
            items.append(entry)
            if len(items) >= 1000:
                break
        return {"items": items, "path": str(path)}


class GlobTool(Tool):
    @property
    def name(self) -> str: return "glob"
    @property
    def description(self) -> str: return "Find files matching a glob pattern (respects .gitignore)"

    @property
    def parameters(self) -> ToolSchema:
        return ToolSchema(properties={
            "pattern": ToolParameter(type="string", description="Glob pattern, e.g. src/**/*.py"),
            "path": ToolParameter(type="string", description="Base directory (default: root)"),
        }, required=["pattern"])

    async def execute(self, arguments: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
        base = resolve_in_workspace(arguments.get("path") or ".")
        if not base.is_dir():
            return {"error": f"Not a directory: {base}"}
        matches = [p for p in iter_workspace_files(base) if p.match(arguments["pattern"])]
        matches.sort(key=lambda p: str(p))
        limited = matches[:500]
        return {
            "matches": [str(p.relative_to(settings.workspace_path)) for p in limited],
            "count": len(matches),
            "truncated": len(matches) > len(limited),
        }


class GrepTool(Tool):
    MAX_MATCHES = 200

    @property
    def name(self) -> str: return "grep"
    @property
    def description(self) -> str: return "Search file contents with a regex (respects .gitignore)"

    @property
    def parameters(self) -> ToolSchema:
        return ToolSchema(properties={
            "pattern": ToolParameter(type="string", description="Regex pattern"),
            "path": ToolParameter(type="string", description="Directory to search (default: root)"),
            "include": ToolParameter(type="string", description="Filename glob, e.g. *.py"),
        }, required=["pattern"])

    async def execute(self, arguments: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
        import re
        base = resolve_in_workspace(arguments.get("path") or ".")
        if not base.is_dir():
            return {"error": f"Not a directory: {base}"}
        try:
            pattern = re.compile(arguments["pattern"])
        except re.error as e:
            return {"error": f"Invalid regex: {e}"}
        include = arguments.get("include", "*")
        results = []
        for file in iter_workspace_files(base):
            if not file.match(include):
                continue
            try:
                async with aiofiles.open(file, encoding="utf-8", errors="replace") as f:
                    content = await f.read()
                for i, line in enumerate(content.splitlines(), 1):
                    if pattern.search(line):
                        results.append({
                            "file": str(file.relative_to(settings.workspace_path)),
                            "line": i,
                            "content": line.strip()[:300],
                        })
                        if len(results) >= self.MAX_MATCHES:
                            return {"results": results, "truncated": True}
            except OSError:
                continue
        return {"results": results, "truncated": False}


for tool_class in [ReadTool, WriteTool, ListTool, GlobTool, GrepTool]:
    registry.register(tool_class())
