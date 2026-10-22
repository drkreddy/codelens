import os

from codelens.discovery import DiscoveryConfig, discover
from codelens.pipeline import analyze


def test_ignores_dirs_symlinks_large_and_binary(tmp_path):
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "ok.py").write_text("x = 1\n")
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "skip.py").write_text("x = 1\n")
    (tmp_path / "big.py").write_text("x = 1\n" * 100)
    (tmp_path / "bin.py").write_bytes(b"\x00\x01binary")
    outside = tmp_path.parent / f"{tmp_path.name}-outside"
    outside.mkdir()
    (outside / "secret.py").write_text("x = 1\n")
    os.symlink(outside, tmp_path / "linkdir")
    os.symlink(tmp_path / "pkg" / "ok.py", tmp_path / "link.py")

    found = discover(tmp_path, DiscoveryConfig(extensions=frozenset({".py"}), max_file_bytes=100))
    assert sorted(f.rel_path for f in found.files) == ["bin.py", "pkg/ok.py"]
    reasons = dict(found.skipped)
    assert reasons["link.py"] == "symlink" and "too large" in reasons["big.py"]

    graph = analyze(tmp_path, max_file_bytes=100)
    assert ("bin.py", "binary content") in graph.skipped
    assert graph.stats["files_analyzed"] == 1
