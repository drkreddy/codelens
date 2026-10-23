import sys
from pathlib import Path

import anyio
import pytest

from codelens.journeys import Workspace
from codelens.query import QueryEngine, mask_line
from codelens.systems import Catalog

BANK = Path(__file__).parent.parent / "examples" / "bank-poc"


@pytest.fixture(scope="module")
def q():
    return QueryEngine(Workspace(BANK, Catalog.load(BANK / "systems.toml")))


def test_impact_of_finacle(q):
    r = q.impact("Finacle")
    assert {e["entry"] for e in r["entry_points_affected"]} == {
        "GET /accounts/{id}/balance", "POST /upi/pay", "KAFKA upi.callback"}
    assert all(c["at"] and ":" in c["at"] for c in r["direct_calls"])


def test_callers_cross_repos(q):
    r = q.callers("DcmsClient.blockCard", depth=6)
    names = [c["symbol"] for c in r["callers"]]
    assert names[:2] == ["CardBlockService.block", "CardBlockController.block"]
    assert "CardAdapterClient.blockCard" in names and "CardController.block" in names
    assert any(c.get("across_repos") for c in r["callers"])


def test_callees_reach_outbound(q):
    r = q.callees("AccountService.balance", depth=3)
    systems = [c["outbound"]["system"] for c in r["callees"] if "outbound" in c]
    assert systems == ["Finacle (CBS)"]
    assert any(c.get("via_interface") for c in r["callees"])


def test_ambiguous_and_missing_symbols(q):
    assert len(q.callers("block")["matches"]) == 4
    assert "No class or method" in q.source("Nope.nothing")["error"]


def test_source_shows_condition(q):
    r = q.source("CardBlockService.block")
    assert r["lines"] == "17-22" and '"LOST".equals(reason)' in r["code"]


def test_search_maps_hits_to_methods(q):
    r = q.search_code("STOLEN")
    assert r["hits"][0]["in"] == "CardBlockService.block" and r["hits"][0]["layer"] == "domain"


def test_search_and_source_mask_secrets(q):
    hits = q.search_code("password")["hits"]
    assert hits and all("s3cr3t" not in h["line"] for h in hits)
    assert mask_line('String apiKey = "abc123";') == 'String apiKey = "***";'
    assert "esb-secret-xyz" not in str(q.search_code("client-secret"))


def test_outbound_calls_filter(q):
    r = q.outbound_calls(category="third-party")
    assert {c["system"] for c in r["outbound_calls"]} == {"FSS", "NPCI (UPI)", "UIDAI (Aadhaar)"}
    assert all(c["reached_from_entry_point"] for c in r["outbound_calls"])


def test_mcp_server_over_stdio():
    """Start `codelens mcp` as a subprocess and call a tool through the protocol."""
    from mcp.client.session import ClientSession
    from mcp.client.stdio import StdioServerParameters, stdio_client

    async def run():
        params = StdioServerParameters(command=sys.executable,
                                       args=["-m", "codelens", "mcp", str(BANK)])
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                tools = {t.name for t in (await session.list_tools()).tools}
                result = await session.call_tool("impact", {"system": "NPCI"})
                return tools, result

    tools, result = anyio.run(run)
    assert tools == {"overview", "find_journeys", "impact", "callers", "callees",
                     "domain_dependencies", "outbound_calls", "source", "search_code",
                     "graph_schema", "graph_query"}
    assert not result.is_error
    assert "POST /upi/pay" in str(result.structured_content or result.content)
