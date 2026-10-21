"""find_symbols tool: semantic symbol discovery and reference search.

Safe read-only tool available in normal mode, plan mode, and subagents.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from coding_agent.config import settings
from coding_agent.core.semantic.index import index_file
from coding_agent.core.semantic.models import SymbolKind
from coding_agent.core.semantic.parser import parse_file
from coding_agent.core.semantic.resolver import SemanticResolver
from coding_agent.core.tools.base import Tool, ToolParameter, ToolSchema
from coding_agent.core.tools.paths import resolve_in_workspace, truncate_output


class FindSymbolsTool(Tool):
    """Discover symbols and references across the workspace."""

    @property
    def name(self) -> str:
        return "find_symbols"

    @property
    def description(self) -> str:
        return (
            "Discover symbols (classes, functions, methods, interfaces, types) or find all "
            "references to a symbol across the workspace. Safe for plan-mode and subagents."
        )

    @property
    def parameters(self) -> ToolSchema:
        return ToolSchema(
            properties={
                "action": ToolParameter(
                    type="string",
                    description="Search action: 'outline' (file symbols), 'find_definitions', 'find_references'",
                    enum=["outline", "find_definitions", "find_references"],
                ),
                "query": ToolParameter(
                    type="string",
                    description="Symbol name or substring to search for",
                ),
                "path": ToolParameter(
                    type="string",
                    description="Optional file path or directory to limit search",
                ),
                "symbol_type": ToolParameter(
                    type="string",
                    description="Filter by symbol type",
                    enum=["all", "function", "class", "method", "variable", "interface"],
                ),
            },
            required=["action"],
        )

    async def execute(self, arguments: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
        action = arguments.get("action", "outline")
        query = arguments.get("query", "").strip()
        path_str = arguments.get("path")
        sym_type_filter = arguments.get("symbol_type", "all")

        workspace_root = settings.workspace_path

        # 1. OUTLINE action: extract symbol outline for a file
        if action == "outline":
            if not path_str:
                return {"error": "Path argument is required for action='outline'"}
            try:
                target_path = resolve_in_workspace(path_str)
            except ValueError as e:
                return {"error": str(e)}

            if not target_path.is_file():
                return {"error": f"File not found: {target_path}"}

            parse_res = parse_file(target_path)
            if not parse_res:
                return {"error": f"Failed to parse {target_path.name}"}

            file_idx = index_file(parse_res)
            symbols = file_idx.symbols

            if sym_type_filter and sym_type_filter != "all":
                symbols = [s for s in symbols if s.kind.value == sym_type_filter]

            results = []
            for s in symbols:
                item = {
                    "name": s.name,
                    "kind": s.kind.value,
                    "line": s.span.start_line + 1,
                    "qualified_name": s.qualified_name or s.name,
                }
                if s.parameters:
                    item["parameters"] = s.parameters
                if s.docstring:
                    item["docstring"] = s.docstring.strip()[:100]
                results.append(item)

            return {
                "file": str(target_path.relative_to(workspace_root)),
                "language": parse_res.language_id,
                "symbols_count": len(results),
                "symbols": results,
            }

        # 2. FIND_DEFINITIONS action
        elif action == "find_definitions":
            if not query:
                return {"error": "Query argument is required for action='find_definitions'"}

            # Search either specified path or all workspace files
            search_base = resolve_in_workspace(path_str) if path_str else workspace_root
            resolver = SemanticResolver(workspace_root)

            candidate_paths: list[Path] = []
            if search_base.is_file():
                candidate_paths = [search_base]
            else:
                for ext in (".py", ".ts", ".tsx", ".js", ".jsx", ".go", ".rs", ".java", ".c", ".cpp"):
                    candidate_paths.extend(search_base.rglob(f"*{ext}"))

            matches = []
            for cp in candidate_paths:
                # Fast check
                try:
                    content = cp.read_text(encoding="utf-8", errors="ignore")
                    if query not in content:
                        continue
                    pres = parse_file(cp)
                    if not pres:
                        continue
                    fidx = index_file(pres)
                    found = fidx.find_symbol(query)
                    for s in found:
                        if sym_type_filter != "all" and s.kind.value != sym_type_filter:
                            continue
                        matches.append({
                            "name": s.name,
                            "kind": s.kind.value,
                            "file": str(cp.relative_to(workspace_root)),
                            "line": s.span.start_line + 1,
                            "qualified_name": s.qualified_name or s.name,
                            "parameters": s.parameters,
                        })
                except Exception:
                    continue

            return {
                "query": query,
                "matches_count": len(matches),
                "definitions": matches,
            }

        # 3. FIND_REFERENCES action
        elif action == "find_references":
            if not query:
                return {"error": "Query argument is required for action='find_references'"}
            if not path_str:
                return {"error": "Path argument is required for action='find_references' to establish context"}

            try:
                target_path = resolve_in_workspace(path_str)
            except ValueError as e:
                return {"error": str(e)}

            resolver = SemanticResolver(workspace_root)
            refs = resolver.find_all_references(
                symbol_name=query,
                primary_file=target_path,
            )

            results = []
            for r in refs:
                results.append({
                    "name": r.name,
                    "kind": r.kind.value,
                    "file": str(r.file_path.relative_to(workspace_root)) if r.file_path.is_relative_to(workspace_root) else str(r.file_path),
                    "line": r.span.start_line + 1,
                    "column": r.span.start_column + 1,
                    "confidence": r.confidence.value,
                })

            return {
                "symbol": query,
                "references_count": len(results),
                "references": results,
            }

        return {"error": f"Unknown action '{action}'"}
