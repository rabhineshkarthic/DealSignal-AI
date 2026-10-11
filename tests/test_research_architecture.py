"""Architecture guarantees for the research layer: network boundary, provider
neutrality (Europe-ready), and extension by a non-SEC provider."""

import ast
from dataclasses import replace
from pathlib import Path

import pytest

from deal_input import form_from_deal
from models import Deal, load_deal
from orchestrator import DealTeamOrchestrator
from research import registry
from research.draft import create_draft
from research.errors import AmbiguousCompanyError
from research.market_data import missing_price
from research.models import (
    CompanyCandidate,
    CompanyResearchResult,
    ResearchJurisdiction,
    ResearchStatus,
    ResearchValue,
    SourceDocument,
)
from research.provenance import ASSUMPTION_PATHS, approve_draft, build_researched_deal
from research.provider import RESEARCH_FIELDS
from research.providers.sec import SECProvider

ROOT = Path(__file__).resolve().parent.parent
NETWORK_MODULES = {"urllib", "http", "socket", "requests", "httpx", "aiohttp", "ssl"}
CORE_FILES = ["valuation.py", "deal_analysis.py", "models.py", "orchestrator.py", "memo.py",
              "excel_export.py", "excel_formulas.py", *[str(p.relative_to(ROOT)) for p in
                                                        (ROOT / "agents").rglob("*.py")]]


def imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text())
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module.split(".")[0])
    return names


# --- Network boundary ------------------------------------------------------------------------

@pytest.mark.parametrize("rel", CORE_FILES)
def test_core_modules_have_no_network_or_research_imports(rel):
    found = imports(ROOT / rel)
    assert not found & NETWORK_MODULES, (rel, found & NETWORK_MODULES)
    assert "research" not in found, rel


def test_only_provider_clients_touch_the_network():
    networked = {str(p.relative_to(ROOT)) for p in (ROOT / "research").rglob("*.py")
                 if imports(p) & NETWORK_MODULES}
    assert networked == {"research/providers/sec/client.py", "research/market_data/provider.py",
                         "research/market_data/alpha_vantage.py"}


@pytest.mark.parametrize("rel", ["deal_input.py", "ui_helpers.py", "ui_components.py",
                                 "research_ui.py", "app.py", "demo_state.py"])
def test_app_layer_has_no_direct_network_imports(rel):
    assert not imports(ROOT / rel) & NETWORK_MODULES


# --- Provider neutrality ------------------------------------------------------------------------------

GENERIC_FILES = ["research/__init__.py", "research/models.py", "research/provider.py",
                 "research/normalizer.py", "research/draft.py", "research/provenance.py",
                 "research/errors.py", "research/market_data/base.py", "research_ui.py",
                 "ui_helpers.py", "app.py"]
SEC_TERMS = ["us-gaap", "10-K", "10-Q", "accession", "CIK", "EDGAR", "companyfacts",
             "providers.sec", "SECProvider", "SECClient"]


@pytest.mark.parametrize("rel", GENERIC_FILES)
def test_generic_code_has_no_sec_specific_terms(rel):
    source = (ROOT / rel).read_text()
    found = [t for t in SEC_TERMS if t.lower() in source.lower()]
    assert found == [], (rel, found)


def test_sec_package_is_only_wired_through_the_registry():
    users = {str(p.relative_to(ROOT)) for p in ROOT.rglob("*.py")
             if ".venv" not in p.parts and "tests" not in p.parts
             and "research/providers/sec" not in str(p) and "providers.sec" in p.read_text()}
    assert users == {"research/registry.py"}


def test_sec_provider_satisfies_the_generic_interface():
    for method in ("search_company", "resolve_company", "research_company"):
        assert callable(getattr(SECProvider, method))
    assert (SECProvider.jurisdiction_code, SECProvider.id_label) == ("US", "CIK")


# --- Extension by a non-SEC provider --------------------------------------------------------------------

class FakeFranceProvider:
    """A stand-in for a future provider: different identifiers, currency, and reports."""

    name = "Registre FR (fake)"
    jurisdiction_code = "FR"
    id_label = "SIREN"

    def search_company(self, query):
        return [CompanyCandidate("552081317", "SOCIETE EXEMPLE SA", "EXM", "Euronext Paris",
                                 "FR", "SIREN")]

    def resolve_company(self, query):
        raise AmbiguousCompanyError("confirm", self.search_company(query))

    def research_company(self, candidate, *, units="millions", force_refresh=False):
        def reported(field, value):
            return ResearchValue(field, ResearchStatus.REPORTED, value, f"EUR {units}",
                                 raw_value=str(int(value * 1_000_000)), raw_unit="EUR",
                                 source_provider=self.name, report_type="Universal Registration Document",
                                 taxonomy="ifrs-full", concept="Revenue", period_end="2024-12-31",
                                 source_document_id="URD-2024")
        fields = {f: reported(f, v) for f, v in zip(
            RESEARCH_FIELDS, (5000.0, 900.0, 400.0, 350.0, 600.0, 1200.0, 250.0))}
        return CompanyResearchResult(candidate.name, candidate.name, candidate.ticker,
                                     candidate.provider_company_id, self.id_label, "FR",
                                     candidate.exchange, "12-31", "EUR", units, self.name, fields,
                                     (SourceDocument("URD 2024", None, "URD", "URD-2024", None),))


def test_future_provider_plugs_into_registry_and_flow(monkeypatch):
    france = ResearchJurisdiction("FR", "France", "fr", "Registre (fake)", True)
    monkeypatch.setattr(registry, "JURISDICTIONS", (*registry.JURISDICTIONS[:1], france))
    monkeypatch.setitem(registry.PROVIDER_FACTORIES, "fr", FakeFranceProvider)
    provider = registry.create_provider("FR")
    assert isinstance(provider, FakeFranceProvider)

    candidate = provider.search_company("exemple")[0]
    company = provider.research_company(candidate)
    target = replace(company, company_name="CIBLE SAS", provider_company_id="123456789")
    draft = create_draft(france, company, target, missing_price("No FR price provider yet."))
    provenance = approve_draft(draft, {"acquirer.share_price": 42.5})
    values = {p: v for p, v in form_from_deal(load_deal(ROOT / "data" / "sample_deal.json")).items()
              if p in ASSUMPTION_PATHS}
    values["transaction_assumptions.offer_enterprise_value"] = 4000.0
    deal = build_researched_deal(draft, provenance, values)

    assert type(deal) is Deal and deal.currency == "EUR"
    assert DealTeamOrchestrator.build_deterministic_contexts(deal)["valuation"]["comparable_company"]
    sources = provenance.excel_sources()
    assert sources["acquirer.name"] == "Registre FR (fake) — registrant name (SIREN 552081317)"
    assert "Universal Registration Document" in sources["acquirer.revenue"]
