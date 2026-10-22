from pathlib import Path

import pytest

from codelens.config_files import MASK, parse_properties, parse_yaml, resolve_placeholders
from codelens.journeys import Workspace
from codelens.reporters.journeys_out import render_journeys_json, render_journeys_markdown
from codelens.systems import Catalog

BANK = Path(__file__).parent.parent / "examples" / "bank-poc"
SECRETS = ["s3cr3t-do-not-print", "MBL-LK-123456", "FSS-KEY-998877", "esb-secret-xyz"]


@pytest.fixture(scope="module")
def ws():
    return Workspace(BANK, Catalog.load(BANK / "systems.toml"))


@pytest.fixture(scope="module")
def journeys(ws):
    return ws.journeys()


def find(journeys, entry, system):
    return [j for j in journeys if j.entry == entry and j.outbound.system == system]


def test_config_parsing_and_masking():
    yml = parse_yaml("a:\n  url: http://u:p@h/x\n  password: hunter2\nb: 'q'\n---\nc:\n  d: 1\n")
    assert yml == {"a.url": f"http://{MASK}@h/x", "a.password": MASK, "b": "q", "c.d": "1"}
    assert parse_properties("x.api-key=abc\ny = 2\n# c\n") == {"x.api-key": MASK, "y": "2"}
    assert resolve_placeholders("${a}/p/${b:dflt}", {"a": "http://h"}) == ("http://h/p/dflt", ["a", "b"])


def test_repos_and_services(ws):
    assert {r.service for r in ws.repos.values()} == {"mobile-banking-api", "card-adapter", "payments-service"}


def test_every_outbound_call_is_classified_and_reached(ws, journeys):
    calls = [o for r in ws.repos.values() for c in r.outbound.values() for o in c]
    assert len(calls) == 8
    assert not [o for o in calls if o.category == "unclassified"]
    assert ws.unreached_outbound(journeys) == []


def test_interface_dispatch_into_infrastructure(journeys):
    [j] = find(journeys, "endpoint:GET /accounts/{id}/balance", "Finacle (CBS)")
    assert [h.name for h in j.hops] == ["AccountController.balance", "AccountService.balance",
                                        "CoreBankingGateway.getBalance", "FinacleGateway.getBalance"]
    assert [h.layer for h in j.hops] == ["web", "domain", "domain", "infrastructure"]
    assert j.hops[-1].dispatch
    assert j.outbound.protocol == "SOAP" and j.outbound.host == "finacle-uat.bank.internal"


def test_cross_repo_journey_via_feign(journeys):
    js = find(journeys, "endpoint:POST /cards/{id}/block", "DCMS")
    assert len(js) == 1
    j = js[0]
    assert j.repos() == ["mobile-banking-api", "card-adapter"]
    bridge = next(h.outbound for h in j.hops if h.outbound)
    assert bridge.category == "internal-service" and bridge.operation == "POST /api/cards/{cardId}/block"
    assert j.outbound.operation == "MTI 0302"
    assert j.outbound.url == "tcp://dcms-switch.bank.internal:7002"


def test_middleware_is_the_hop_not_the_destination(journeys):
    [j] = find(journeys, "endpoint:POST /upi/pay", "Finacle (CBS)")
    assert j.outbound.via == "ESB" and j.outbound.reason == "marker"
    assert j.outbound.operation == "POST /gateway/FIN_DEBIT_TXN"


def test_third_party_systems(journeys):
    assert {j.outbound.system for j in journeys if j.outbound.category == "third-party"} == {
        "FSS", "NPCI (UPI)", "UIDAI (Aadhaar)"}
    [kafka] = find(journeys, "endpoint:KAFKA upi.callback", "Finacle (CBS)")
    assert kafka.hops[-1].name == "EsbCoreBankingAdapter.credit"


def test_reports_never_contain_secrets(ws, journeys):
    md = render_journeys_markdown(ws, journeys)
    js = render_journeys_json(ws, journeys)
    for secret in SECRETS:
        assert secret not in md and secret not in js
    assert "| `CardService` (mobile-banking-api) | DCMS | mobile-banking-api" not in md
    assert "| `CardService` (mobile-banking-api) | DCMS | card-adapter | FSS |" in md


def test_unknown_system_is_reported_for_labelling(tmp_path):
    repo = tmp_path / "svc" / "src" / "a"
    repo.mkdir(parents=True)
    (repo / "C.java").write_text(
        "package a; import org.springframework.web.client.RestTemplate;\n"
        "import org.springframework.web.bind.annotation.*;\n"
        "@RestController class C { RestTemplate r; @GetMapping(\"/x\") void x() {"
        " r.getForObject(\"https://mystery.example/v1\", String.class); } }\n")
    w = Workspace(tmp_path, Catalog())
    [j] = w.journeys()
    assert j.outbound.category == "unclassified" and j.outbound.host == "mystery.example"
    assert "Needs a label" in render_journeys_markdown(w, [j])
