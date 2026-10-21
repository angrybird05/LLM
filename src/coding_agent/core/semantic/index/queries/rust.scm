; Rust symbol queries for tree-sitter

; --- Functions ---
(function_item
  name: (identifier) @function.name) @function.def

; --- Structs ---
(struct_item
  name: (type_identifier) @struct.name) @struct.def

; --- Enums ---
(enum_item
  name: (type_identifier) @enum.name) @enum.def

; --- Traits ---
(trait_item
  name: (type_identifier) @trait.name) @trait.def

; --- Impl blocks ---
(impl_item
  type: (type_identifier) @impl.name) @impl.def

; --- Type aliases ---
(type_item
  name: (type_identifier) @type.name) @type.def

; --- Constants ---
(const_item
  name: (identifier) @constant.name) @constant.def

; --- Static variables ---
(static_item
  name: (identifier) @static.name) @static.def

; --- Modules ---
(mod_item
  name: (identifier) @module.name) @module.def

; --- Use statements ---
(use_declaration) @import.def
