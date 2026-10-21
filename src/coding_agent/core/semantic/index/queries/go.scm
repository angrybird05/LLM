; Go symbol queries for tree-sitter

; --- Functions ---
(function_declaration
  name: (identifier) @function.name) @function.def

; --- Methods ---
(method_declaration
  name: (field_identifier) @method.name) @method.def

; --- Structs ---
(type_declaration
  (type_spec
    name: (type_identifier) @struct.name
    type: (struct_type))) @struct.def

; --- Interfaces ---
(type_declaration
  (type_spec
    name: (type_identifier) @interface.name
    type: (interface_type))) @interface.def

; --- Type aliases ---
(type_declaration
  (type_spec
    name: (type_identifier) @type.name)) @type.def

; --- Package-level variables ---
(var_declaration
  (var_spec
    name: (identifier) @variable.name)) @variable.def

; --- Constants ---
(const_declaration
  (const_spec
    name: (identifier) @constant.name)) @constant.def

; --- Imports ---
(import_declaration) @import.def
