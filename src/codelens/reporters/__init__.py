from codelens.reporters.dot_out import render_dot
from codelens.reporters.json_out import render_json
from codelens.reporters.markdown_out import render_markdown

RENDERERS = {"json": (render_json, "codelens.json"),
             "md": (render_markdown, "codelens.md"),
             "dot": (render_dot, "modules.dot")}

__all__ = ["RENDERERS", "render_dot", "render_json", "render_markdown"]
