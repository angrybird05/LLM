"""Lexical scope hierarchy and binding analyzer.

Builds scope trees from parsed CSTs, discovers bindings (parameters, variables,
imports, declarations), and detects shadowing across lexical scopes.
"""
from __future__ import annotations

from pathlib import Path

from coding_agent.core.semantic.models import (
    Binding,
    Scope,
    ScopeKind,
    Span,
    SymbolKind,
)
from coding_agent.core.semantic.parser.base import CSTNode, ParseResult


def build_scope_tree(parse_result: ParseResult) -> Scope:
    """Build the full lexical scope tree for a parsed file."""
    root = parse_result.root
    file_path = parse_result.file_path
    lang = parse_result.language_id

    root_scope = Scope(
        kind=ScopeKind.MODULE,
        span=root.span,
        bindings={},
        children=[],
        parent=None,
    )

    if lang == "python":
        _build_python_scopes(root, root_scope, file_path)
    elif lang in ("typescript", "tsx", "javascript"):
        _build_js_ts_scopes(root, root_scope, file_path)
    elif lang == "go":
        _build_go_scopes(root, root_scope, file_path)
    elif lang == "rust":
        _build_rust_scopes(root, root_scope, file_path)
    else:
        _build_generic_scopes(root, root_scope, file_path)

    return root_scope


def find_enclosing_scope(scope: Scope, byte_offset: int) -> Scope:
    """Find the most deeply nested scope enclosing the given byte offset."""
    for child in scope.children:
        if child.span.start_byte <= byte_offset <= child.span.end_byte:
            return find_enclosing_scope(child, byte_offset)
    return scope


def find_shadowed_bindings(scope: Scope) -> list[tuple[Binding, Binding]]:
    """Find bindings in this scope that shadow bindings in an ancestor scope.

    Returns pairs of (inner_binding, shadowed_outer_binding).
    """
    shadows: list[tuple[Binding, Binding]] = []

    def _check(s: Scope) -> None:
        if s.parent:
            for name, bindings in s.bindings.items():
                outer = s.parent.lookup(name)
                if outer:
                    for b in bindings:
                        for ob in outer:
                            shadows.append((b, ob))
        for child in s.children:
            _check(child)

    _check(scope)
    return shadows


# ------------------------------------------------------------------ Python Scopes

def _build_python_scopes(node: CSTNode, current_scope: Scope, file_path: Path) -> None:
    for child in node.children:
        if not child.is_named:
            continue

        ntype = child.node_type

        # Function definition creates a new FUNCTION scope
        if ntype in ("function_definition", "async_function_definition"):
            name_node = child.child_by_field_name("name")
            if name_node:
                current_scope.add_binding(Binding(
                    name=name_node.text,
                    span=name_node.span,
                    kind=SymbolKind.FUNCTION,
                    is_definition=True,
                ))

            fn_scope = Scope(
                kind=ScopeKind.FUNCTION,
                span=child.span,
                bindings={},
                children=[],
                parent=current_scope,
            )
            current_scope.children.append(fn_scope)

            # Add parameters as bindings to the function scope
            params_node = child.child_by_field_name("parameters")
            if params_node:
                _extract_python_params(params_node, fn_scope)

            # Traverse body
            body_node = child.child_by_field_name("body")
            if body_node:
                _build_python_scopes(body_node, fn_scope, file_path)

        # Class definition creates a CLASS scope
        elif ntype == "class_definition":
            name_node = child.child_by_field_name("name")
            if name_node:
                current_scope.add_binding(Binding(
                    name=name_node.text,
                    span=name_node.span,
                    kind=SymbolKind.CLASS,
                    is_definition=True,
                ))

            class_scope = Scope(
                kind=ScopeKind.CLASS,
                span=child.span,
                bindings={},
                children=[],
                parent=current_scope,
            )
            current_scope.children.append(class_scope)

            body_node = child.child_by_field_name("body")
            if body_node:
                _build_python_scopes(body_node, class_scope, file_path)

        # Assignments create bindings in current scope
        elif ntype in ("assignment", "augmented_assignment"):
            left = child.child_by_field_name("left")
            if left:
                _extract_python_targets(left, current_scope)

        # Imports create bindings
        elif ntype == "import_statement":
            for import_name in child.children_by_field_name("name"):
                name = import_name.text.split(".")[-1]
                alias = child.child_by_field_name("alias")
                if alias:
                    name = alias.text
                current_scope.add_binding(Binding(
                    name=name,
                    span=import_name.span,
                    kind=SymbolKind.IMPORT,
                ))

        elif ntype == "import_from_statement":
            for imp in child.children:
                if imp.node_type == "dotted_name" or not imp.is_named:
                    continue
                if imp.node_type == "import_from_targets":
                    for tgt in imp.children:
                        if tgt.is_named:
                            _add_import_binding(tgt, current_scope)
                elif imp.node_type in ("identifier", "aliased_import"):
                    _add_import_binding(imp, current_scope)

        # For statement binds target in current scope
        elif ntype == "for_statement":
            left = child.child_by_field_name("left")
            if left:
                _extract_python_targets(left, current_scope)
            body = child.child_by_field_name("body")
            if body:
                _build_python_scopes(body, current_scope, file_path)

        # With statement binds items
        elif ntype == "with_statement":
            for item in child.children:
                if item.node_type == "with_item":
                    alias = item.child_by_field_name("alias")
                    if alias:
                        _extract_python_targets(alias, current_scope)
            body = child.child_by_field_name("body")
            if body:
                _build_python_scopes(body, current_scope, file_path)

        # Comprehensions create nested scope
        elif "comprehension" in ntype:
            comp_scope = Scope(
                kind=ScopeKind.COMPREHENSION,
                span=child.span,
                bindings={},
                children=[],
                parent=current_scope,
            )
            current_scope.children.append(comp_scope)
            _build_python_scopes(child, comp_scope, file_path)

        else:
            # Recurse into blocks/suites
            _build_python_scopes(child, current_scope, file_path)


def _add_import_binding(node: CSTNode, scope: Scope) -> None:
    if node.node_type == "aliased_import":
        alias = node.child_by_field_name("alias")
        if alias:
            scope.add_binding(Binding(name=alias.text, span=alias.span, kind=SymbolKind.IMPORT))
            return
        name = node.child_by_field_name("name")
        if name:
            scope.add_binding(Binding(name=name.text, span=name.span, kind=SymbolKind.IMPORT))
    elif node.node_type == "identifier":
        scope.add_binding(Binding(name=node.text, span=node.span, kind=SymbolKind.IMPORT))


def _extract_python_params(params_node: CSTNode, scope: Scope) -> None:
    for child in params_node.children:
        if not child.is_named:
            continue
        if child.node_type == "identifier":
            scope.add_binding(Binding(name=child.text, span=child.span, kind=SymbolKind.PARAMETER))
        elif child.node_type in ("typed_parameter", "default_parameter", "typed_default_parameter"):
            name = child.child_by_field_name("name")
            if name:
                scope.add_binding(Binding(name=name.text, span=name.span, kind=SymbolKind.PARAMETER))
            elif child.children and child.children[0].node_type == "identifier":
                scope.add_binding(Binding(name=child.children[0].text, span=child.children[0].span, kind=SymbolKind.PARAMETER))
        elif child.node_type in ("positional_separator", "keyword_separator"):
            continue
        elif child.node_type in ("splat_parameter", "dictionary_splat_parameter"):
            for inner in child.children:
                if inner.node_type == "identifier":
                    scope.add_binding(Binding(name=inner.text, span=inner.span, kind=SymbolKind.PARAMETER))


def _extract_python_targets(node: CSTNode, scope: Scope) -> None:
    if node.node_type == "identifier":
        scope.add_binding(Binding(name=node.text, span=node.span, kind=SymbolKind.VARIABLE))
    elif node.node_type in ("tuple", "list", "pattern_list"):
        for child in node.children:
            if child.is_named:
                _extract_python_targets(child, scope)


# ------------------------------------------------------------------ JS / TS Scopes

def _build_js_ts_scopes(node: CSTNode, current_scope: Scope, file_path: Path) -> None:
    for child in node.children:
        if not child.is_named:
            continue

        ntype = child.node_type

        if ntype in ("function_declaration", "function", "generator_function_declaration"):
            name_node = child.child_by_field_name("name")
            if name_node:
                current_scope.add_binding(Binding(
                    name=name_node.text,
                    span=name_node.span,
                    kind=SymbolKind.FUNCTION,
                ))

            fn_scope = Scope(
                kind=ScopeKind.FUNCTION,
                span=child.span,
                bindings={},
                children=[],
                parent=current_scope,
            )
            current_scope.children.append(fn_scope)

            params = child.child_by_field_name("parameters")
            if params:
                for p in params.children:
                    if p.is_named:
                        pname = p.child_by_field_name("name") or (p if p.node_type == "identifier" else None)
                        if pname:
                            fn_scope.add_binding(Binding(name=pname.text, span=pname.span, kind=SymbolKind.PARAMETER))

            body = child.child_by_field_name("body")
            if body:
                _build_js_ts_scopes(body, fn_scope, file_path)

        elif ntype in ("arrow_function",):
            fn_scope = Scope(
                kind=ScopeKind.FUNCTION,
                span=child.span,
                bindings={},
                children=[],
                parent=current_scope,
            )
            current_scope.children.append(fn_scope)

            params = child.child_by_field_name("parameters") or child.child_by_field_name("parameter")
            if params:
                if params.node_type == "identifier":
                    fn_scope.add_binding(Binding(name=params.text, span=params.span, kind=SymbolKind.PARAMETER))
                else:
                    for p in params.children:
                        if p.is_named:
                            pname = p.child_by_field_name("name") or (p if p.node_type == "identifier" else None)
                            if pname:
                                fn_scope.add_binding(Binding(name=pname.text, span=pname.span, kind=SymbolKind.PARAMETER))

            body = child.child_by_field_name("body")
            if body:
                _build_js_ts_scopes(body, fn_scope, file_path)

        elif ntype in ("class_declaration", "class"):
            name_node = child.child_by_field_name("name")
            if name_node:
                current_scope.add_binding(Binding(
                    name=name_node.text,
                    span=name_node.span,
                    kind=SymbolKind.CLASS,
                ))

            class_scope = Scope(
                kind=ScopeKind.CLASS,
                span=child.span,
                bindings={},
                children=[],
                parent=current_scope,
            )
            current_scope.children.append(class_scope)

            body = child.child_by_field_name("body")
            if body:
                _build_js_ts_scopes(body, class_scope, file_path)

        elif ntype in ("lexical_declaration", "variable_declaration"):
            for decl in child.children:
                if decl.node_type == "variable_declarator":
                    name_n = decl.child_by_field_name("name")
                    if name_n and name_n.node_type == "identifier":
                        current_scope.add_binding(Binding(
                            name=name_n.text,
                            span=name_n.span,
                            kind=SymbolKind.VARIABLE,
                        ))
                    value_n = decl.child_by_field_name("value")
                    if value_n and value_n.node_type == "arrow_function":
                        _build_js_ts_scopes(value_n, current_scope, file_path)

        elif ntype == "statement_block":
            block_scope = Scope(
                kind=ScopeKind.BLOCK,
                span=child.span,
                bindings={},
                children=[],
                parent=current_scope,
            )
            current_scope.children.append(block_scope)
            _build_js_ts_scopes(child, block_scope, file_path)

        else:
            _build_js_ts_scopes(child, current_scope, file_path)


# ------------------------------------------------------------------ Go Scopes

def _build_go_scopes(node: CSTNode, current_scope: Scope, file_path: Path) -> None:
    for child in node.children:
        if not child.is_named:
            continue
        ntype = child.node_type

        if ntype in ("function_declaration", "method_declaration"):
            name_node = child.child_by_field_name("name")
            if name_node:
                current_scope.add_binding(Binding(
                    name=name_node.text,
                    span=name_node.span,
                    kind=SymbolKind.FUNCTION if ntype == "function_declaration" else SymbolKind.METHOD,
                ))

            fn_scope = Scope(
                kind=ScopeKind.FUNCTION,
                span=child.span,
                bindings={},
                children=[],
                parent=current_scope,
            )
            current_scope.children.append(fn_scope)

            body = child.child_by_field_name("body")
            if body:
                _build_go_scopes(body, fn_scope, file_path)

        elif ntype == "block":
            block_scope = Scope(
                kind=ScopeKind.BLOCK,
                span=child.span,
                bindings={},
                children=[],
                parent=current_scope,
            )
            current_scope.children.append(block_scope)
            _build_go_scopes(child, block_scope, file_path)

        elif ntype in ("short_var_declaration", "var_declaration"):
            left = child.child_by_field_name("left")
            if left:
                for id_node in left.children:
                    if id_node.node_type == "identifier":
                        current_scope.add_binding(Binding(name=id_node.text, span=id_node.span, kind=SymbolKind.VARIABLE))
            _build_go_scopes(child, current_scope, file_path)

        else:
            _build_go_scopes(child, current_scope, file_path)


# ------------------------------------------------------------------ Rust Scopes

def _build_rust_scopes(node: CSTNode, current_scope: Scope, file_path: Path) -> None:
    for child in node.children:
        if not child.is_named:
            continue
        ntype = child.node_type

        if ntype == "function_item":
            name_node = child.child_by_field_name("name")
            if name_node:
                current_scope.add_binding(Binding(
                    name=name_node.text,
                    span=name_node.span,
                    kind=SymbolKind.FUNCTION,
                ))

            fn_scope = Scope(
                kind=ScopeKind.FUNCTION,
                span=child.span,
                bindings={},
                children=[],
                parent=current_scope,
            )
            current_scope.children.append(fn_scope)

            body = child.child_by_field_name("body")
            if body:
                _build_rust_scopes(body, fn_scope, file_path)

        elif ntype == "block":
            block_scope = Scope(
                kind=ScopeKind.BLOCK,
                span=child.span,
                bindings={},
                children=[],
                parent=current_scope,
            )
            current_scope.children.append(block_scope)
            _build_rust_scopes(child, block_scope, file_path)

        elif ntype == "let_declaration":
            pattern = child.child_by_field_name("pattern")
            if pattern and pattern.node_type == "identifier":
                current_scope.add_binding(Binding(
                    name=pattern.text,
                    span=pattern.span,
                    kind=SymbolKind.VARIABLE,
                ))
            _build_rust_scopes(child, current_scope, file_path)

        else:
            _build_rust_scopes(child, current_scope, file_path)


# ------------------------------------------------------------------ Generic Scopes

def _build_generic_scopes(node: CSTNode, current_scope: Scope, file_path: Path) -> None:
    for child in node.children:
        if not child.is_named:
            continue
        if child.node_type in ("compound_statement", "block", "block_statement"):
            block_scope = Scope(
                kind=ScopeKind.BLOCK,
                span=child.span,
                bindings={},
                children=[],
                parent=current_scope,
            )
            current_scope.children.append(block_scope)
            _build_generic_scopes(child, block_scope, file_path)
        else:
            _build_generic_scopes(child, current_scope, file_path)
