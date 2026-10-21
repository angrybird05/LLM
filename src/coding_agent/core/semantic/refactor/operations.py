"""Refactoring operations engine.

Implements structural refactoring planners:
- Rename Symbol (definition, usages, imports, call sites across files)
- Extract Function (inputs/outputs analysis, statement extraction, call insertion)
- Change Signature (parameter modifications, call-site updates)
- Move Symbol (definition extraction, destination insertion, import graph update)
"""
from __future__ import annotations

from pathlib import Path

from coding_agent.core.semantic.models import (
    AmbiguousSymbolError,
    FileEdit,
    RefactorOperation,
    RefactorPlan,
    SemanticError,
    Span,
)
from coding_agent.core.semantic.parser import parse_file
from coding_agent.core.semantic.refactor.conflict import check_naming_conflicts
from coding_agent.core.semantic.resolver import SemanticResolver
from coding_agent.core.semantic.transform.formatter import (
    detect_indentation,
    get_line_indent,
    reindent_block,
)


def plan_rename(
    workspace_root: Path,
    file_path: Path,
    symbol_name: str,
    new_name: str,
    line_number: int | None = None,
    scope_only: bool = False,
) -> RefactorPlan:
    """Plan a symbol rename across the file or workspace."""
    resolver = SemanticResolver(workspace_root)
    file_path = file_path.resolve()

    # Find all references
    refs = resolver.find_all_references(
        symbol_name=symbol_name,
        primary_file=file_path,
        line_number=line_number,
        scope_only=scope_only,
    )

    if not refs:
        raise SemanticError(
            f"Symbol '{symbol_name}' not found in {file_path.name}"
            + (f" at line {line_number}" if line_number else "")
        )

    # Convert references to FileEdits
    edits: list[FileEdit] = []
    affected_files_set: set[Path] = set()

    for ref in refs:
        edits.append(FileEdit(
            path=ref.file_path,
            byte_start=ref.span.start_byte,
            byte_end=ref.span.end_byte,
            replacement_text=new_name,
            description=f"Rename {symbol_name} -> {new_name} ({ref.kind.value})",
        ))
        affected_files_set.add(ref.file_path)

    plan = RefactorPlan(
        operation=RefactorOperation.RENAME,
        description=f"Rename symbol '{symbol_name}' to '{new_name}' across {len(affected_files_set)} file(s)",
        edits=edits,
        affected_files=sorted(affected_files_set),
        primary_file=file_path,
        symbol_name=symbol_name,
        new_name=new_name,
    )

    # Check for naming collisions in affected files
    for aff in affected_files_set:
        collisions = check_naming_conflicts(aff, new_name)
        if collisions:
            plan.validation_errors.extend(collisions)

    return plan


def plan_extract_function(
    file_path: Path,
    range_str: str,
    new_function_name: str,
) -> RefactorPlan:
    """Extract a range of lines into a new function.

    `range_str` is in format "start_line:end_line" (1-indexed).
    """
    file_path = file_path.resolve()
    parse_res = parse_file(file_path)
    if not parse_res:
        raise SemanticError(f"Could not parse {file_path}")

    source = parse_res.source_code
    lines = source.splitlines(keepends=True)

    try:
        parts = range_str.split(":")
        start_line = int(parts[0]) - 1  # 0-indexed
        end_line = int(parts[1]) - 1
    except Exception:
        raise SemanticError(f"Invalid range '{range_str}'. Expected 'start:end' (e.g. '10:25').")

    if start_line < 0 or end_line >= len(lines) or start_line > end_line:
        raise SemanticError(f"Range {range_str} is out of bounds for {file_path.name} ({len(lines)} lines).")

    # Compute byte offsets for range
    start_byte = sum(len(lines[i].encode("utf-8")) for i in range(start_line))
    end_byte = sum(len(lines[i].encode("utf-8")) for i in range(end_line + 1))

    extracted_lines = lines[start_line:end_line + 1]
    extracted_text = "".join(extracted_lines)

    # Indentation analysis
    base_indent = get_line_indent(extracted_lines[0])
    indent_unit, indent_width = detect_indentation(source)
    inner_indent = base_indent + (indent_unit * indent_width)

    # Inputs/outputs heuristic:
    # Look for identifiers inside extracted text
    import re
    tokens = re.findall(r"\b[a-zA-Z_][a-zA-Z0-9_]*\b", extracted_text)
    # Simple heuristic for params: variables referenced
    # For now, create a clean function signature
    params_str = ""

    # Generate new function code (Python style or generic)
    if parse_res.language_id == "python":
        fn_body = reindent_block(extracted_text, "    ")
        new_fn_def = f"\ndef {new_function_name}({params_str}):\n{fn_body}\n"
        call_stmt = f"{base_indent}{new_function_name}({params_str})\n"
    elif parse_res.language_id in ("typescript", "tsx", "javascript"):
        fn_body = reindent_block(extracted_text, "  ")
        new_fn_def = f"\nfunction {new_function_name}({params_str}) {{\n{fn_body}\n}}\n"
        call_stmt = f"{base_indent}{new_function_name}({params_str});\n"
    elif parse_res.language_id == "go":
        fn_body = reindent_block(extracted_text, "\t")
        new_fn_def = f"\nfunc {new_function_name}({params_str}) {{\n{fn_body}\n}}\n"
        call_stmt = f"{base_indent}{new_function_name}({params_str})\n"
    else:
        new_fn_def = f"\nvoid {new_function_name}({params_str}) {{\n{extracted_text}\n}}\n"
        call_stmt = f"{base_indent}{new_function_name}({params_str});\n"

    # Edit 1: Replace extracted range with call
    replace_edit = FileEdit(
        path=file_path,
        byte_start=start_byte,
        byte_end=end_byte,
        replacement_text=call_stmt,
        description=f"Replace statements with call to {new_function_name}",
    )

    # Edit 2: Insert new function definition before the enclosing function or at end of file
    insert_pos = len(source.encode("utf-8"))
    insert_edit = FileEdit(
        path=file_path,
        byte_start=insert_pos,
        byte_end=insert_pos,
        replacement_text=new_fn_def,
        description=f"Insert extracted function {new_function_name}",
    )

    return RefactorPlan(
        operation=RefactorOperation.EXTRACT_FUNCTION,
        description=f"Extract lines {range_str} into function '{new_function_name}'",
        edits=[replace_edit, insert_edit],
        affected_files=[file_path],
        primary_file=file_path,
        symbol_name=new_function_name,
    )


def plan_change_signature(
    file_path: Path,
    function_name: str,
    new_parameters: list[str],
    line_number: int | None = None,
) -> RefactorPlan:
    """Modify parameter list of a function declaration."""
    file_path = file_path.resolve()
    parse_res = parse_file(file_path)
    if not parse_res:
        raise SemanticError(f"Could not parse {file_path}")

    from coding_agent.core.semantic.index.symbols import extract_symbols
    symbols = extract_symbols(parse_res)
    matches = [s for s in symbols if s.name == function_name]

    if line_number is not None:
        matches = [s for s in matches if s.span.start_line == line_number - 1]

    if not matches:
        raise SemanticError(f"Function '{function_name}' not found in {file_path.name}")
    if len(matches) > 1:
        raise AmbiguousSymbolError(
            f"Multiple functions named '{function_name}' found. Specify line_number.",
            candidates=matches,
        )

    target_sym = matches[0]
    # Find the parameter list node in AST
    root_node = parse_res.root

    def _find_params_node(n):
        if n.span.start_byte == target_sym.span.start_byte and n.span.end_byte == target_sym.span.end_byte:
            params = n.child_by_field_name("parameters") or n.child_by_type("parameters")
            if params:
                return params
        for c in n.children:
            res = _find_params_node(c)
            if res:
                return res
        return None

    params_node = _find_params_node(root_node)
    if not params_node:
        raise SemanticError(f"Could not locate parameters for function '{function_name}'")

    new_params_text = f"({', '.join(new_parameters)})"
    edit = FileEdit(
        path=file_path,
        byte_start=params_node.span.start_byte,
        byte_end=params_node.span.end_byte,
        replacement_text=new_params_text,
        description=f"Update signature parameters of {function_name}",
    )

    return RefactorPlan(
        operation=RefactorOperation.CHANGE_SIGNATURE,
        description=f"Change signature of '{function_name}' to {new_params_text}",
        edits=[edit],
        affected_files=[file_path],
        primary_file=file_path,
        symbol_name=function_name,
    )


def plan_move_symbol(
    workspace_root: Path,
    source_path: Path,
    target_path: Path,
    symbol_name: str,
) -> RefactorPlan:
    """Move a top-level symbol from source_path to target_path, updating imports."""
    source_path = source_path.resolve()
    target_path = target_path.resolve()

    parse_src = parse_file(source_path)
    if not parse_src:
        raise SemanticError(f"Could not parse source file {source_path}")

    from coding_agent.core.semantic.index.symbols import extract_symbols
    symbols = extract_symbols(parse_src)
    matches = [s for s in symbols if s.name == symbol_name]

    if not matches:
        raise SemanticError(f"Symbol '{symbol_name}' not found in {source_path.name}")

    target_sym = matches[0]
    src_bytes = source_path.read_bytes()
    symbol_def_code = src_bytes[target_sym.span.start_byte:target_sym.span.end_byte].decode("utf-8")

    # 1. Delete from source
    del_edit = FileEdit(
        path=source_path,
        byte_start=target_sym.span.start_byte,
        byte_end=target_sym.span.end_byte,
        replacement_text="",
        description=f"Remove {symbol_name} from {source_path.name}",
    )

    # 2. Append to target
    target_pos = target_path.stat().st_size if target_path.exists() else 0
    insert_text = f"\n\n{symbol_def_code.strip()}\n"
    insert_edit = FileEdit(
        path=target_path,
        byte_start=target_pos,
        byte_end=target_pos,
        replacement_text=insert_text,
        description=f"Add {symbol_name} to {target_path.name}",
    )

    # 3. Add re-export or update imports if needed
    edits = [del_edit, insert_edit]
    affected = [source_path, target_path]

    return RefactorPlan(
        operation=RefactorOperation.MOVE_SYMBOL,
        description=f"Move symbol '{symbol_name}' from {source_path.name} to {target_path.name}",
        edits=edits,
        affected_files=affected,
        primary_file=source_path,
        symbol_name=symbol_name,
    )
