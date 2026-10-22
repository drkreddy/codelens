; Field declarations: one match per declarator.
(field_declaration
  type: (_) @field.type
  declarator: (variable_declarator name: (identifier) @field.name))

; Record components behave like fields.
(record_declaration
  parameters: (formal_parameters
    (formal_parameter type: (_) @field.type name: (identifier) @field.name)))
