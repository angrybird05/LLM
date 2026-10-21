"""Symbol definition extractor using tree-sitter CST traversal.

Walks a parsed CST to discover classes, functions, methods, interfaces,
variables, imports, and their spans, docstrings, decorators, and parameters.
"""
from __future__ import annotations

from pathlib import Path

from coding_agent.core.semantic.models import Span, Symbol, SymbolKind
from coding_agent.core.semantic.parser.base import CSTNode, ParseResult


def extract_symbols(parse_result: ParseResult) -> list[Symbol]:
    """Extract all top-level and nested symbol definitions from a parse result.

    Returns a flat list of Symbol objects; nesting is expressed via parent/children
    relationships.
    """
    root = parse_result.root
    file_path = parse_result.file_path
    language_id = parse_result.language_id

    if language_id == "python":
        return _extract_python(root, file_path)
    elif language_id in ("typescript", "tsx"):
        return _extract_typescript(root, file_path)
    elif language_id == "javascript":
        return _extract_javascript(root, file_path)
    elif language_id == "go":
        return _extract_go(root, file_path)
    elif language_id == "rust":
        return _extract_rust(root, file_path)
    elif language_id in ("java", "c", "cpp", "c_sharp"):
        return _extract_c_family(root, file_path, language_id)
    else:
        return _extract_generic(root, file_path)


# ------------------------------------------------------------------ Python

def _extract_python(root: CSTNode, file_path: Path) -> list[Symbol]:
    """Extract symbols from Python source."""
    symbols: list[Symbol] = []
    _walk_python(root, file_path, symbols, parent=None, prefix="")
    return symbols


def _walk_python(node: CSTNode, file_path: Path, symbols: list[Symbol],
                 parent: Symbol | None, prefix: str) -> None:
    for child in node.children:
        if not child.is_named:
            continue

        if child.node_type == "function_definition":
            sym = _python_function(child, file_path, parent, prefix)
            symbols.append(sym)
            if parent:
                parent.children.append(sym)
            # Walk body for nested definitions
            body = child.child_by_type("block")
            if body:
                _walk_python(body, file_path, symbols, sym,
                             prefix=sym.qualified_name + ".")

        elif child.node_type == "class_definition":
            sym = _python_class(child, file_path, parent, prefix)
            symbols.append(sym)
            if parent:
                parent.children.append(sym)
            body = child.child_by_type("block")
            if body:
                _walk_python(body, file_path, symbols, sym,
                             prefix=sym.qualified_name + ".")

        elif child.node_type == "decorated_definition":
            # The actual def/class is inside the decorated_definition
            _walk_python(child, file_path, symbols, parent, prefix)

        elif child.node_type == "expression_statement":
            # Module-level assignments
            assign = child.child_by_type("assignment")
            if assign:
                left = assign.child_by_type("identifier")
                if left:
                    sym = Symbol(
                        name=left.text,
                        kind=SymbolKind.VARIABLE,
                        span=child.span,
                        file_path=file_path,
                        qualified_name=prefix + left.text,
                        parent=parent,
                    )
                    symbols.append(sym)
                    if parent:
                        parent.children.append(sym)

        elif child.node_type in ("import_statement", "import_from_statement"):
            sym = Symbol(
                name=child.text.strip(),
                kind=SymbolKind.IMPORT,
                span=child.span,
                file_path=file_path,
                qualified_name=child.text.strip(),
                parent=parent,
            )
            symbols.append(sym)

        else:
            _walk_python(child, file_path, symbols, parent, prefix)


def _python_function(node: CSTNode, file_path: Path,
                     parent: Symbol | None, prefix: str) -> Symbol:
    name_node = node.child_by_type("identifier")
    name = name_node.text if name_node else "<anonymous>"

    kind = SymbolKind.METHOD if parent and parent.kind == SymbolKind.CLASS else SymbolKind.FUNCTION

    # Extract parameters
    params: list[str] = []
    params_node = node.child_by_type("parameters")
    if params_node:
        for p in params_node.children:
            if p.is_named and p.node_type in (
                "identifier", "typed_parameter", "default_parameter",
                "typed_default_parameter", "list_splat_pattern",
                "dictionary_splat_pattern",
            ):
                # Get the parameter name
                id_node = p.child_by_type("identifier") if p.node_type != "identifier" else p
                if id_node:
                    params.append(id_node.text)

    # Extract decorators
    decorators: list[str] = []
    # Check if parent is decorated_definition
    # (decorators are siblings in tree-sitter)

    # Extract docstring
    docstring = None
    body = node.child_by_type("block")
    if body and body.children:
        first_stmt = None
        for c in body.children:
            if c.is_named:
                first_stmt = c
                break
        if first_stmt and first_stmt.node_type == "expression_statement":
            string_node = first_stmt.child_by_type("string")
            if string_node:
                docstring = string_node.text.strip("\"'")

    # Return type
    return_type = None
    ret_node = node.child_by_type("type")
    if ret_node:
        return_type = ret_node.text

    return Symbol(
        name=name,
        kind=kind,
        span=node.span,
        file_path=file_path,
        qualified_name=prefix + name,
        parent=parent,
        docstring=docstring,
        parameters=params,
        return_type=return_type,
        decorators=decorators,
    )


def _python_class(node: CSTNode, file_path: Path,
                  parent: Symbol | None, prefix: str) -> Symbol:
    name_node = node.child_by_type("identifier")
    name = name_node.text if name_node else "<anonymous>"

    # Extract docstring
    docstring = None
    body = node.child_by_type("block")
    if body and body.children:
        first_stmt = None
        for c in body.children:
            if c.is_named:
                first_stmt = c
                break
        if first_stmt and first_stmt.node_type == "expression_statement":
            string_node = first_stmt.child_by_type("string")
            if string_node:
                docstring = string_node.text.strip("\"'")

    return Symbol(
        name=name,
        kind=SymbolKind.CLASS,
        span=node.span,
        file_path=file_path,
        qualified_name=prefix + name,
        parent=parent,
        docstring=docstring,
    )


# ------------------------------------------------------------------ TypeScript / JavaScript

def _extract_typescript(root: CSTNode, file_path: Path) -> list[Symbol]:
    return _extract_js_family(root, file_path, is_typescript=True)


def _extract_javascript(root: CSTNode, file_path: Path) -> list[Symbol]:
    return _extract_js_family(root, file_path, is_typescript=False)


def _extract_js_family(root: CSTNode, file_path: Path,
                       is_typescript: bool) -> list[Symbol]:
    symbols: list[Symbol] = []
    _walk_js(root, file_path, symbols, parent=None, prefix="")
    return symbols


def _walk_js(node: CSTNode, file_path: Path, symbols: list[Symbol],
             parent: Symbol | None, prefix: str) -> None:
    for child in node.children:
        if not child.is_named:
            continue

        if child.node_type == "function_declaration":
            name_node = child.child_by_type("identifier")
            name = name_node.text if name_node else "<anonymous>"
            sym = Symbol(
                name=name,
                kind=SymbolKind.FUNCTION,
                span=child.span,
                file_path=file_path,
                qualified_name=prefix + name,
                parent=parent,
            )
            symbols.append(sym)
            if parent:
                parent.children.append(sym)
            body = child.child_by_type("statement_block")
            if body:
                _walk_js(body, file_path, symbols, sym, prefix=sym.qualified_name + ".")

        elif child.node_type == "class_declaration":
            name_node = child.child_by_type("type_identifier") or child.child_by_type("identifier")
            name = name_node.text if name_node else "<anonymous>"
            sym = Symbol(
                name=name,
                kind=SymbolKind.CLASS,
                span=child.span,
                file_path=file_path,
                qualified_name=prefix + name,
                parent=parent,
            )
            symbols.append(sym)
            if parent:
                parent.children.append(sym)
            body = child.child_by_type("class_body")
            if body:
                _walk_js(body, file_path, symbols, sym, prefix=sym.qualified_name + ".")

        elif child.node_type == "method_definition":
            name_node = child.child_by_type("property_identifier")
            name = name_node.text if name_node else "<anonymous>"
            sym = Symbol(
                name=name,
                kind=SymbolKind.METHOD,
                span=child.span,
                file_path=file_path,
                qualified_name=prefix + name,
                parent=parent,
            )
            symbols.append(sym)
            if parent:
                parent.children.append(sym)

        elif child.node_type == "interface_declaration":
            name_node = child.child_by_type("type_identifier")
            name = name_node.text if name_node else "<anonymous>"
            sym = Symbol(
                name=name,
                kind=SymbolKind.INTERFACE,
                span=child.span,
                file_path=file_path,
                qualified_name=prefix + name,
                parent=parent,
            )
            symbols.append(sym)
            if parent:
                parent.children.append(sym)

        elif child.node_type == "type_alias_declaration":
            name_node = child.child_by_type("type_identifier")
            name = name_node.text if name_node else "<anonymous>"
            sym = Symbol(
                name=name,
                kind=SymbolKind.TYPE_ALIAS,
                span=child.span,
                file_path=file_path,
                qualified_name=prefix + name,
                parent=parent,
            )
            symbols.append(sym)
            if parent:
                parent.children.append(sym)

        elif child.node_type in ("lexical_declaration", "variable_declaration"):
            for declarator in child.children_by_type("variable_declarator"):
                name_node = declarator.child_by_type("identifier")
                if name_node:
                    # Check if it's an arrow function
                    arrow = declarator.child_by_type("arrow_function")
                    kind = SymbolKind.FUNCTION if arrow else SymbolKind.VARIABLE
                    sym = Symbol(
                        name=name_node.text,
                        kind=kind,
                        span=child.span,
                        file_path=file_path,
                        qualified_name=prefix + name_node.text,
                        parent=parent,
                    )
                    symbols.append(sym)
                    if parent:
                        parent.children.append(sym)

        elif child.node_type in ("import_statement", "export_statement"):
            sym = Symbol(
                name=child.text.strip()[:80],
                kind=SymbolKind.IMPORT,
                span=child.span,
                file_path=file_path,
                qualified_name=child.text.strip()[:80],
                parent=parent,
            )
            symbols.append(sym)

        else:
            _walk_js(child, file_path, symbols, parent, prefix)


# ------------------------------------------------------------------ Go

def _extract_go(root: CSTNode, file_path: Path) -> list[Symbol]:
    symbols: list[Symbol] = []
    _walk_go(root, file_path, symbols)
    return symbols


def _walk_go(node: CSTNode, file_path: Path, symbols: list[Symbol]) -> None:
    for child in node.children:
        if not child.is_named:
            continue

        if child.node_type == "function_declaration":
            name_node = child.child_by_type("identifier")
            if name_node:
                symbols.append(Symbol(
                    name=name_node.text,
                    kind=SymbolKind.FUNCTION,
                    span=child.span,
                    file_path=file_path,
                    qualified_name=name_node.text,
                ))

        elif child.node_type == "method_declaration":
            name_node = child.child_by_type("field_identifier")
            if name_node:
                symbols.append(Symbol(
                    name=name_node.text,
                    kind=SymbolKind.METHOD,
                    span=child.span,
                    file_path=file_path,
                    qualified_name=name_node.text,
                ))

        elif child.node_type == "type_declaration":
            for spec in child.children_by_type("type_spec"):
                name_node = spec.child_by_type("type_identifier")
                if name_node:
                    struct_type = spec.child_by_type("struct_type")
                    iface_type = spec.child_by_type("interface_type")
                    kind = (
                        SymbolKind.STRUCT if struct_type
                        else SymbolKind.INTERFACE if iface_type
                        else SymbolKind.TYPE_ALIAS
                    )
                    symbols.append(Symbol(
                        name=name_node.text,
                        kind=kind,
                        span=child.span,
                        file_path=file_path,
                        qualified_name=name_node.text,
                    ))

        elif child.node_type in ("var_declaration", "const_declaration"):
            for spec in child.children:
                if spec.is_named and spec.node_type in ("var_spec", "const_spec"):
                    name_node = spec.child_by_type("identifier")
                    if name_node:
                        kind = (SymbolKind.CONSTANT if child.node_type == "const_declaration"
                                else SymbolKind.VARIABLE)
                        symbols.append(Symbol(
                            name=name_node.text,
                            kind=kind,
                            span=child.span,
                            file_path=file_path,
                            qualified_name=name_node.text,
                        ))

        elif child.node_type == "import_declaration":
            symbols.append(Symbol(
                name=child.text.strip()[:80],
                kind=SymbolKind.IMPORT,
                span=child.span,
                file_path=file_path,
                qualified_name=child.text.strip()[:80],
            ))

        else:
            _walk_go(child, file_path, symbols)


# ------------------------------------------------------------------ Rust

def _extract_rust(root: CSTNode, file_path: Path) -> list[Symbol]:
    symbols: list[Symbol] = []
    _walk_rust(root, file_path, symbols)
    return symbols


def _walk_rust(node: CSTNode, file_path: Path, symbols: list[Symbol]) -> None:
    for child in node.children:
        if not child.is_named:
            continue

        if child.node_type == "function_item":
            name_node = child.child_by_type("identifier")
            if name_node:
                symbols.append(Symbol(
                    name=name_node.text, kind=SymbolKind.FUNCTION,
                    span=child.span, file_path=file_path,
                    qualified_name=name_node.text,
                ))

        elif child.node_type == "struct_item":
            name_node = child.child_by_type("type_identifier")
            if name_node:
                symbols.append(Symbol(
                    name=name_node.text, kind=SymbolKind.STRUCT,
                    span=child.span, file_path=file_path,
                    qualified_name=name_node.text,
                ))

        elif child.node_type == "enum_item":
            name_node = child.child_by_type("type_identifier")
            if name_node:
                symbols.append(Symbol(
                    name=name_node.text, kind=SymbolKind.CLASS,
                    span=child.span, file_path=file_path,
                    qualified_name=name_node.text,
                ))

        elif child.node_type == "trait_item":
            name_node = child.child_by_type("type_identifier")
            if name_node:
                symbols.append(Symbol(
                    name=name_node.text, kind=SymbolKind.TRAIT,
                    span=child.span, file_path=file_path,
                    qualified_name=name_node.text,
                ))

        elif child.node_type == "impl_item":
            name_node = child.child_by_type("type_identifier")
            if name_node:
                # Walk impl body for methods
                body = child.child_by_type("declaration_list")
                if body:
                    for item in body.children:
                        if item.is_named and item.node_type == "function_item":
                            fn_name = item.child_by_type("identifier")
                            if fn_name:
                                symbols.append(Symbol(
                                    name=fn_name.text, kind=SymbolKind.METHOD,
                                    span=item.span, file_path=file_path,
                                    qualified_name=f"{name_node.text}.{fn_name.text}",
                                ))

        elif child.node_type in ("const_item", "static_item"):
            name_node = child.child_by_type("identifier")
            if name_node:
                symbols.append(Symbol(
                    name=name_node.text, kind=SymbolKind.CONSTANT,
                    span=child.span, file_path=file_path,
                    qualified_name=name_node.text,
                ))

        elif child.node_type == "mod_item":
            name_node = child.child_by_type("identifier")
            if name_node:
                symbols.append(Symbol(
                    name=name_node.text, kind=SymbolKind.MODULE,
                    span=child.span, file_path=file_path,
                    qualified_name=name_node.text,
                ))

        elif child.node_type == "use_declaration":
            symbols.append(Symbol(
                name=child.text.strip()[:80], kind=SymbolKind.IMPORT,
                span=child.span, file_path=file_path,
                qualified_name=child.text.strip()[:80],
            ))

        else:
            _walk_rust(child, file_path, symbols)


# ------------------------------------------------------------------ C-family

def _extract_c_family(root: CSTNode, file_path: Path,
                      language_id: str) -> list[Symbol]:
    symbols: list[Symbol] = []
    _walk_c_family(root, file_path, symbols, language_id)
    return symbols


def _walk_c_family(node: CSTNode, file_path: Path, symbols: list[Symbol],
                   language_id: str) -> None:
    for child in node.children:
        if not child.is_named:
            continue

        if child.node_type == "function_definition":
            # C/C++ function
            declarator = child.child_by_type("function_declarator")
            if declarator:
                name_node = declarator.child_by_type("identifier")
                if name_node:
                    symbols.append(Symbol(
                        name=name_node.text, kind=SymbolKind.FUNCTION,
                        span=child.span, file_path=file_path,
                        qualified_name=name_node.text,
                    ))

        elif child.node_type == "class_specifier":
            name_node = child.child_by_type("type_identifier")
            if name_node:
                symbols.append(Symbol(
                    name=name_node.text, kind=SymbolKind.CLASS,
                    span=child.span, file_path=file_path,
                    qualified_name=name_node.text,
                ))

        elif child.node_type == "struct_specifier":
            name_node = child.child_by_type("type_identifier")
            if name_node:
                symbols.append(Symbol(
                    name=name_node.text, kind=SymbolKind.STRUCT,
                    span=child.span, file_path=file_path,
                    qualified_name=name_node.text,
                ))

        elif child.node_type in ("preproc_include", "preproc_import"):
            symbols.append(Symbol(
                name=child.text.strip()[:80], kind=SymbolKind.IMPORT,
                span=child.span, file_path=file_path,
                qualified_name=child.text.strip()[:80],
            ))

        else:
            _walk_c_family(child, file_path, symbols, language_id)


# ------------------------------------------------------------------ Generic fallback

def _extract_generic(root: CSTNode, file_path: Path) -> list[Symbol]:
    """Minimal symbol extraction for unknown languages."""
    symbols: list[Symbol] = []
    for child in root.children:
        if child.is_named and "definition" in child.node_type:
            name_node = (
                child.child_by_type("identifier")
                or child.child_by_type("type_identifier")
                or child.child_by_type("name")
            )
            if name_node:
                symbols.append(Symbol(
                    name=name_node.text,
                    kind=SymbolKind.FUNCTION,
                    span=child.span,
                    file_path=file_path,
                    qualified_name=name_node.text,
                ))
    return symbols
