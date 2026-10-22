from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """codelens must work fully offline: any socket connection fails the test."""
    import socket

    def refuse(*args, **kwargs):
        raise AssertionError("network access attempted")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)


@pytest.fixture(scope="session")
def java_graph():
    from codelens.pipeline import analyze
    return analyze(FIXTURES / "java_shop")


@pytest.fixture(scope="session")
def py_graph():
    from codelens.pipeline import analyze
    return analyze(FIXTURES / "py_app")
