; Python symbol queries for tree-sitter
; Extracts functions, classes, methods, variables, imports, and decorators

; --- Functions ---
(function_definition
  name: (identifier) @function.name) @function.def

; --- Classes ---
(class_definition
  name: (identifier) @class.name) @class.def

; --- Methods (inside class body) ---
(class_definition
  body: (block
    (function_definition
      name: (identifier) @method.name) @method.def))

; --- Decorated definitions ---
(decorated_definition
  (decorator) @decorator
  definition: (_) @decorated.def)

; --- Assignments (module level variables) ---
(expression_statement
  (assignment
    left: (identifier) @variable.name)) @variable.def

; --- Type-annotated assignments ---
(expression_statement
  (assignment
    left: (identifier) @typed_variable.name
    type: (_) @typed_variable.type)) @typed_variable.def

; --- Import statements ---
(import_statement) @import.def
(import_from_statement) @import_from.def

; --- Import names ---
(import_from_statement
  name: (dotted_name) @import.module
  (aliased_import
    name: (dotted_name) @import.name)?)

; --- Parameters ---
(parameters
  (identifier) @parameter.name)
(parameters
  (typed_parameter
    (identifier) @parameter.name))
(parameters
  (default_parameter
    name: (identifier) @parameter.name))
(parameters
  (typed_default_parameter
    name: (identifier) @parameter.name))
