"""File-local scope and symbol reference resolver.

Resolves symbol bindings within a single file: identifying definitions, usages,
calls, assignments, and respecting lexical scope boundaries (e.g. shadowed names,
local vs outer variables).
"""
from __future__ import annotations

from pathlib import Path

from coding_agent.core.semantic.index.scopes import (
    build_scope_tree,
    find_enclosing_scope,
)
from coding_agent.core.semantic.index.symbols import extract_symbols
from coding_agent.core.semantic.models import (
    ConfidenceLevel,
    ReferenceKind,
    Scope,
    ScopeKind,
    Span,
    Symbol,
    SymbolKind,
    SymbolReference,
)
from coding_agent.core.semantic.parser.base import CSTNode, ParseResult


def resolve_local_references(
    parse_result: ParseResult,
    target_name: str,
    target_line: int | None = None,
    target_span: Span | None = None,
) -> list[SymbolReference]:
    """Find all references to a target symbol within a single file.

    Respects lexical scope:
    - If target is a local variable in function `foo`, occurrences in other functions
      or outside `foo` are excluded.
    - If target is a module-level function/class/variable, occurrences inside
      functions that shadow that name are excluded.
    """
    root_scope = build_scope_tree(parse_result)
    root_node = parse_result.root
    file_path = parse_result.file_path
    lang = parse_result.language_id

    # 1. Determine target's binding scope
    target_scope: Scope | None = None
    if target_span:
        target_scope = find_enclosing_scope(root_scope, target_span.start_byte)
    elif target_line is not None:
        # Approximate byte from line
        lines = parse_result.source_code.splitlines(keepends=True)
        if 0 <= target_line < len(lines):
            byte_off = sum(len(lines[i].encode("utf-8")) for i in range(target_line))
            target_scope = find_enclosing_scope(root_scope, byte_off)

    # 2. Collect identifier nodes across the AST
    all_refs: list[SymbolReference] = []
    _traverse_identifiers(
        node=root_node,
        target_name=target_name,
        root_scope=root_scope,
        target_scope=target_scope,
        file_path=file_path,
        lang=lang,
        out_refs=all_refs,
    )
    return all_refs


def _traverse_identifiers(
    node: CSTNode,
    target_name: str,
    root_scope: Scope,
    target_scope: Scope | None,
    file_path: Path,
    lang: str,
    out_refs: list[SymbolReference],
) -> None:
    # Check if this node is an identifier matching target_name
    is_id = False
    if lang in ("python", "go", "rust"):
        is_id = node.node_type == "identifier"
    elif lang in ("typescript", "tsx", "javascript"):
        is_id = node.node_type in ("identifier", "property_identifier", "shorthand_property_identifier")
    else:
        is_id = node.node_type in ("identifier", "name", "field_identifier")

    if is_id and node.text == target_name:
        # Check scope compatibility
        enclosing = find_enclosing_scope(root_scope, node.span.start_byte)
        if _is_same_binding_scope(enclosing, target_scope, target_name):
            ref_kind = _classify_reference(node, lang)
            out_refs.append(SymbolReference(
                name=target_name,
                span=node.span,
                file_path=file_path,
                kind=ref_kind,
                confidence=ConfidenceLevel.DEFINITE,
            ))

    # Recurse into children
    for child in node.children:
        _traverse_identifiers(
            node=child,
            target_name=target_name,
            root_scope=root_scope,
            target_scope=target_scope,
            file_path=file_path,
            lang=lang,
            out_refs=out_refs,
        )


def _is_same_binding_scope(
    enclosing_scope: Scope,
    target_scope: Scope | None,
    target_name: str,
) -> bool:
    """Check if an occurrence at enclosing_scope refers to the same binding as target_scope."""
    if target_scope is None:
        # No target scope constraint: check if it's the module scope or unshadowed
        return True

    # If the target is defined in a function or block scope:
    # Any reference MUST be inside that same function/block scope (or its children),
    # unless it was shadowed in a child scope.
    if target_scope.kind in (ScopeKind.FUNCTION, ScopeKind.BLOCK, ScopeKind.COMPREHENSION):
        # Is enclosing_scope a descendant of or equal to target_scope?
        curr: Scope | None = enclosing_scope
        while curr is not None:
            if curr is target_scope:
                # Check for shadowing between target_scope and enclosing_scope
                check = enclosing_scope
                while check is not None and check is not target_scope:
                    if target_name in check.bindings:
                        # Inner scope re-bound target_name! It's shadowed.
                        return False
                    check = check.parent
                return True
            curr = curr.parent
        return False

    # If target is module-level / global:
    # Enclosing scope must resolve target_name to the module level (not shadowed by local var)
    curr = enclosing_scope
    while curr is not None and curr.kind != ScopeKind.MODULE:
        if target_name in curr.bindings:
            # Shadowed by a local binding
            return False
        curr = curr.parent

    return True


def _classify_reference(node: CSTNode, lang: str) -> ReferenceKind:
    """Determine the kind of reference based on parent node context."""
    parent = node.parent
    if not parent:
        return ReferenceKind.USAGE

    ptype = parent.node_type

    # Definition contexts
    if ptype in (
        "function_definition", "async_function_definition",
        "function_declaration", "function_item",
        "class_definition", "class_declaration",
        "method_definition", "method_declaration",
    ):
        name_node = parent.child_by_field_name("name")
        if name_node and name_node.span == node.span:
            return ReferenceKind.DEFINITION

    # Import contexts
    if "import" in ptype:
        return ReferenceKind.IMPORT

    # Call contexts
    if ptype in ("call", "call_expression"):
        func_node = parent.child_by_field_name("function")
        if func_node and func_node.span == node.span:
            return ReferenceKind.CALL

    # Assignment contexts
    if ptype in ("assignment", "augmented_assignment"):
        left = parent.child_by_field_name("left")
        if left and left.span == node.span:
            return ReferenceKind.ASSIGNMENT

    if ptype == "variable_declarator":
        name_node = parent.child_by_field_name("name")
        if name_node and name_node.span == node.span:
            return ReferenceKind.DEFINITION

    # Type annotation
    if ptype in ("type", "type_annotation", "type_identifier"):
        return ReferenceKind.TYPE_ANNOTATION

    return ReferenceKind.USAGE
