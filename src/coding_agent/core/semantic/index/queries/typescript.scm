; TypeScript symbol queries for tree-sitter

; --- Functions ---
(function_declaration
  name: (identifier) @function.name) @function.def

; --- Arrow functions assigned to variables ---
(lexical_declaration
  (variable_declarator
    name: (identifier) @function.name
    value: (arrow_function) @function.arrow)) @function.def

; --- Classes ---
(class_declaration
  name: (type_identifier) @class.name) @class.def

; --- Methods ---
(method_definition
  name: (property_identifier) @method.name) @method.def

; --- Interfaces ---
(interface_declaration
  name: (type_identifier) @interface.name) @interface.def

; --- Type aliases ---
(type_alias_declaration
  name: (type_identifier) @type.name) @type.def

; --- Variable declarations ---
(lexical_declaration
  (variable_declarator
    name: (identifier) @variable.name)) @variable.def

(variable_declaration
  (variable_declarator
    name: (identifier) @variable.name)) @variable.def

; --- Exports ---
(export_statement) @export.def

; --- Imports ---
(import_statement) @import.def
