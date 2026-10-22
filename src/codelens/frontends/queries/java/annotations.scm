; Annotations, with or without arguments.
(marker_annotation name: (_) @annotation.name) @annotation
(annotation
  name: (_) @annotation.name
  arguments: (annotation_argument_list) @annotation.args) @annotation
