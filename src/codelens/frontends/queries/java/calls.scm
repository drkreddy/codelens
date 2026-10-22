; Method calls and object creation.
(method_invocation
  object: (_)? @call.receiver
  name: (identifier) @call.name) @call
(object_creation_expression type: (_) @new.type) @new
