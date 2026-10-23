import shutil
from pathlib import Path

import pytest

pytest.importorskip("kuzu")

from codelens import store
from codelens.journeys import Workspace
from codelens.reporters.journeys_out import render_journeys_json
from codelens.systems import Catalog

BANK = Path(__file__).parent.parent / "examples" / "bank-poc"
SECRETS = ["s3cr3t-do-not-print", "MBL-LK-123456", "FSS-KEY-998877", "esb-secret-xyz"]


@pytest.fixture(scope="module")
def catalog():
    return Catalog.load(BANK / "systems.toml")


@pytest.fixture(scope="module")
def ws(catalog):
    return Workspace(BANK, catalog)


@pytest.fixture(scope="module")
def db(ws, tmp_path_factory):
    path = tmp_path_factory.mktemp("store") / "graph.kuzu"
    store.save(ws, path)
    return path


def test_round_trip_gives_identical_journeys(ws, db, catalog):
    loaded = store.load(BANK, catalog, db)
    assert render_journeys_json(loaded, loaded.journeys()) == render_journeys_json(ws, ws.journeys())
    for name, repo in ws.repos.items():
        other = loaded.repos[name].graph
        assert other.summary() == repo.graph.summary()
        assert list(other.symbols) == list(repo.graph.symbols)
        assert [e.to_dict() for e in other.edges] == [e.to_dict() for e in repo.graph.edges]


def test_card_block_journey_survives(db, catalog):
    loaded = store.load(BANK, catalog, db)
    reached = {(j.entry, j.outbound.system) for j in loaded.journeys()}
    for entry in ("endpoint:POST /cards/{id}/block", "endpoint:POST /api/cards/{cardId}/block"):
        assert {(entry, "DCMS"), (entry, "FSS")} <= reached


def test_systems_toml_edits_apply_without_reparsing(db, tmp_path):
    systems = tmp_path / "systems.toml"
    systems.write_text((BANK / "systems.toml").read_text().replace("DCMS", "CARDSWITCH"))
    loaded = store.load(BANK, Catalog.load(systems), db)
    assert "CARDSWITCH" in {j.outbound.system for j in loaded.journeys()}


def test_cypher_query_and_read_only(db):
    rows = store.query(db, "MATCH (s:Symbol {kind: 'endpoint'}) RETURN s.id AS id")
    assert any("block" in r["id"] for r in rows)
    with pytest.raises(RuntimeError):
        store.query(db, "MATCH (s:Symbol) SET s.name = 'x'")


def test_store_never_contains_secrets(db):
    blob = repr(store.query(db, "MATCH (r:Repo) RETURN r.config AS c")) + repr(
        store.query(db, "MATCH (s:Symbol) RETURN s.attrs AS a"))
    for secret in SECRETS:
        assert secret not in blob


def test_fingerprint_detects_source_changes(ws, catalog, tmp_path):
    root = tmp_path / "bank"
    shutil.copytree(BANK, root, ignore=shutil.ignore_patterns(".codelens"))
    path = tmp_path / "g.kuzu"
    store.save(Workspace(root, catalog), path)
    assert store.is_fresh(root, path)
    java = next(root.rglob("CardController.java"))
    java.write_text(java.read_text() + "\n// changed\n")
    assert not store.is_fresh(root, path)
    # a config change counts too
    store.save(Workspace(root, catalog), path)
    conf = next(root.rglob("application*.y*ml"))
    conf.write_text(conf.read_text() + "\n# changed\n")
    assert not store.is_fresh(root, path)


def test_open_workspace_reuses_fresh_store_and_reindexes_stale(catalog, tmp_path, monkeypatch):
    root = tmp_path / "bank"
    shutil.copytree(BANK, root, ignore=shutil.ignore_patterns(".codelens"))
    first = store.open_workspace(root, catalog)  # parses, then saves
    assert (root / store.DEFAULT_DB).is_file()

    def boom(*a, **k):
        raise AssertionError("parsed again although the store was fresh")
    monkeypatch.setattr("codelens.journeys.analyze", boom)
    again = store.open_workspace(root, catalog)
    assert render_journeys_json(again, again.journeys()) == render_journeys_json(first, first.journeys())


def test_query_limited_truncates_and_describe_lists_tables(db):
    out = store.query_limited(db, "MATCH (s:Symbol) RETURN s.id AS id", limit=3)
    assert len(out["rows"]) == 3 and out["truncated"] and out["columns"] == ["id"]
    tables = {t["name"]: t for t in store.describe(db)["tables"]}
    assert {"Repo", "Symbol", "Edge", "Outbound", "HAS_OUTBOUND"} <= set(tables)
    assert tables["Edge"]["from"] == "Symbol" and tables["Edge"]["to"] == "Symbol"
    assert any(c["name"] == "uid" and c["primary_key"] for c in tables["Symbol"]["columns"])


def test_graph_tools_are_read_only(db):
    from codelens.mcp_server import build_server
    import asyncio
    server = build_server(BANK, db=db)
    names = {t.name for t in asyncio.run(server.list_tools())}
    assert {"graph_schema", "graph_query"} <= names
    with pytest.raises(Exception):
        store.query_limited(db, "MATCH (s:Symbol) DETACH DELETE s")
