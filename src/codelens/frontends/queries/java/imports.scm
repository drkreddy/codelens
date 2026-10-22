; Single-type, wildcard and static imports.
; `import static` is detected from the declaration node in Python.
(import_declaration
  [(scoped_identifier) (identifier)] @import.name
  (asterisk)? @import.wildcard) @import
