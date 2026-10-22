import json

from codelens.cli import main
from conftest import FIXTURES


def test_cli_writes_all_reports(tmp_path, capsys):
    assert main(["analyze", str(FIXTURES / "java_shop"), "-o", str(tmp_path)]) == 0
    data = json.loads((tmp_path / "codelens.json").read_text())
    assert data["stats"]["files_analyzed"] == 7
    assert data["cycles"]
    assert all(e["dst"] for e in data["edges"])
    assert "## Entry points and flows" in (tmp_path / "codelens.md").read_text()
    dot = (tmp_path / "modules.dot").read_text()
    assert dot.startswith("digraph") and '"java:com.acme.shop.web" -> "java:com.acme.shop.service"' in dot


def test_cli_language_filter(tmp_path):
    assert main(["analyze", str(FIXTURES), "-o", str(tmp_path), "-f", "json", "-l", "py"]) == 0
    data = json.loads((tmp_path / "codelens.json").read_text())
    assert {n["language"] for n in data["nodes"] if n["kind"] == "module"} == {"py"}
