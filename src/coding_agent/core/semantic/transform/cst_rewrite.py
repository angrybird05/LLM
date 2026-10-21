"""LibCST-based high-fidelity transformer for Python code.

Provides comment- and whitespace-preserving transformations for Python files:
- Renaming symbols (functions, classes, methods, variables, parameters)
- Replacing function or class bodies
- Modifying function signatures (parameters, return types)
- Adding or updating imports
"""
from __future__ import annotations

import libcst as cst
from libcst import matchers as m


class RenameTransformer(cst.CSTTransformer):
    """Renames occurrences of a target identifier."""

    def __init__(self, old_name: str, new_name: str, scope_filter: str | None = None) -> None:
        self.old_name = old_name
        self.new_name = new_name
        self.scope_filter = scope_filter
        self._current_scope: list[str] = []

    def visit_FunctionDef(self, node: cst.FunctionDef) -> bool:
        self._current_scope.append(node.name.value)
        return True

    def leave_FunctionDef(self, original_node: cst.FunctionDef, updated_node: cst.FunctionDef) -> cst.FunctionDef:
        self._current_scope.pop()
        if original_node.name.value == self.old_name:
            return updated_node.with_changes(name=cst.Name(self.new_name))
        return updated_node

    def visit_ClassDef(self, node: cst.ClassDef) -> bool:
        self._current_scope.append(node.name.value)
        return True

    def leave_ClassDef(self, original_node: cst.ClassDef, updated_node: cst.ClassDef) -> cst.ClassDef:
        self._current_scope.pop()
        if original_node.name.value == self.old_name:
            return updated_node.with_changes(name=cst.Name(self.new_name))
        return updated_node

    def leave_Name(self, original_node: cst.Name, updated_node: cst.Name) -> cst.Name:
        if original_node.value == self.old_name:
            return updated_node.with_changes(value=self.new_name)
        return updated_node


class ReplaceFunctionBodyTransformer(cst.CSTTransformer):
    """Replaces the body of a specific function or method."""

    def __init__(self, target_name: str, new_body_cst: cst.BaseSuite) -> None:
        self.target_name = target_name
        self.new_body_cst = new_body_cst

    def leave_FunctionDef(self, original_node: cst.FunctionDef, updated_node: cst.FunctionDef) -> cst.FunctionDef:
        if original_node.name.value == self.target_name:
            return updated_node.with_changes(body=self.new_body_cst)
        return updated_node


class AddImportTransformer(cst.CSTTransformer):
    """Adds an import statement to the module header."""

    def __init__(self, import_node: cst.SimpleStatementLine) -> None:
        self.import_node = import_node
        self._added = False

    def leave_Module(self, original_node: cst.Module, updated_node: cst.Module) -> cst.Module:
        if self._added:
            return updated_node
        body = list(updated_node.body)
        # Find position after existing docstring or initial comments
        insert_idx = 0
        if body and isinstance(body[0], cst.SimpleStatementLine):
            first_stmt = body[0].body[0]
            if isinstance(first_stmt, cst.Expr) and isinstance(first_stmt.value, (cst.SimpleString, cst.FormattedString)):
                insert_idx = 1
        body.insert(insert_idx, self.import_node)
        self._added = True
        return updated_node.with_changes(body=body)


def python_rename_symbol(source_code: str, old_name: str, new_name: str) -> str:
    """Rename a symbol in Python source code with full comment and whitespace preservation."""
    tree = cst.parse_module(source_code)
    transformer = RenameTransformer(old_name, new_name)
    modified_tree = tree.visit(transformer)
    return modified_tree.code


def python_replace_function_body(source_code: str, function_name: str, new_body_code: str) -> str:
    """Replace the body of a Python function with new body statements."""
    tree = cst.parse_module(source_code)
    # Parse new body as a module, then extract suite statements
    # Indent new_body_code if necessary
    body_wrapped = f"def __dummy__():\n{new_body_code}"
    dummy_tree = cst.parse_module(body_wrapped)
    dummy_func = dummy_tree.body[0]
    assert isinstance(dummy_func, cst.FunctionDef)
    new_suite = dummy_func.body

    transformer = ReplaceFunctionBodyTransformer(function_name, new_suite)
    modified_tree = tree.visit(transformer)
    return modified_tree.code


def python_add_import(source_code: str, import_code: str) -> str:
    """Insert an import statement near the top of a Python file."""
    tree = cst.parse_module(source_code)
    parsed_import = cst.parse_statement(import_code.strip() + "\n")
    if not isinstance(parsed_import, cst.SimpleStatementLine):
        return source_code
    transformer = AddImportTransformer(parsed_import)
    modified_tree = tree.visit(transformer)
    return modified_tree.code
