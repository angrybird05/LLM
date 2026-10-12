"""Exact string replacement editing — the workhorse tool for code changes.

Audit group B hardening: CRLF-preserving (B1), refuses non-UTF-8 files instead
of mojibaking them (B2), rejects empty old_string (B3), whitespace near-miss
hint fixed to actually match (B4). Writes are atomic (B5).
"""
from pathlib import Path
from typing import Any

from coding_agent.core.tools.base import Tool, ToolParameter, ToolSchema, registry
from coding_agent.core.tools.paths import (
    atomic_write_text,
    detect_eol,
    resolve_in_workspace,
)


def _normalize(text: str) -> str:
    """Internal working form: LF-only, so matching is EOL-independent."""
    return text.replace("\r\n", "\n").replace("\r", "\n")


class EditTool(Tool):
    @property
    def name(self) -> str: return "edit"

    @property
    def description(self) -> str:
        return (
            "Replace an exact occurrence of old_string with new_string in a file. "
            "old_string must match the file content exactly (including whitespace) and be "
            "unique unless replace_all is true. Read the file first to get exact text."
        )

    @property
    def parameters(self) -> ToolSchema:
        return ToolSchema(properties={
            "path": ToolParameter(type="string", description="File path to edit"),
            "old_string": ToolParameter(type="string", description="Exact text to find"),
            "new_string": ToolParameter(type="string", description="Replacement text"),
            "replace_all": ToolParameter(type="boolean", description="Replace every occurrence"),
        }, required=["path", "old_string", "new_string"])

    async def execute(self, arguments: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
        path = resolve_in_workspace(arguments["path"])
        old = arguments["old_string"]
        new = arguments["new_string"]
        replace_all = bool(arguments.get("replace_all", False))

        if not old:
            return {"error": "old_string must not be empty"}
        if old == new:
            return {"error": "old_string and new_string are identical"}
        if not path.exists():
            return {"error": f"File not found: {path}"}

        # B2: read STRICTLY — a latin-1/cp1252 file must be refused, not
        # rewritten with every undecodable byte turned into U+FFFD.
        try:
            raw = path.read_bytes()
            content = _normalize(raw.decode("utf-8"))
        except UnicodeDecodeError:
            return {"error": (
                f"{path.name} is not valid UTF-8; refusing to edit it to avoid "
                "corruption. Convert the file to UTF-8 first."
            )}
        except OSError as e:
            return {"error": f"Cannot read {path}: {e}"}
        eol = detect_eol(raw.decode("utf-8", errors="replace"))

        old_n = _normalize(old)
        new_n = _normalize(new)
        count = content.count(old_n)

        if count == 0:
            # B4: near-miss hint compares whitespace-squashed forms of BOTH
            # sides so it can genuinely fire on indentation/EOL mismatches.
            def squash(s: str) -> str:
                return "".join(ln.strip() for ln in s.splitlines())

            if squash(content).count(squash(old_n)) and "\n" in old_n:
                hint = (" A whitespace-insensitive match exists; "
                        "check indentation/line endings.")
            else:
                hint = ""
            return {"error": f"old_string not found in {path}.{hint}"}

        if count > 1 and not replace_all:
            return {
                "error": (
                    f"old_string matches {count} locations in {path}. "
                    "Add surrounding context to make it unique, or set replace_all=true."
                )
            }

        updated = content.replace(old_n, new_n) if replace_all else content.replace(old_n, new_n, 1)
        atomic_write_text(path, updated, eol=eol)
        return {
            "success": True,
            "path": str(path),
            "replacements": count if replace_all else 1,
            "bytes": Path(path).stat().st_size,
        }


registry.register(EditTool())


