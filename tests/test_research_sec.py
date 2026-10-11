"""SEC provider: client fair access, identity, period selection, mappings, derivations,
debt policy, normalization, provenance, and market price. No network access."""

import socket
import urllib.error
from decimal import Decimal

import pytest

from research.errors import (
    AmbiguousCompanyError,
    CompanyNotFoundError,
    MarketPriceError,
    NormalizationError,
    ProviderRequestError,
    ResearchConfigurationError,
)
from research.market_data import StooqMarketPriceProvider, quote_to_value
from research.models import CompanyCandidate, ResearchStatus
from research.normalizer import normalize
from research.providers.sec import SECClient, SECProvider
from research.providers.sec.client import COMPANY_FACTS_URL, SUBMISSIONS_URL, TICKERS_URL
from research_fixtures import (
    UA,
    FakeMarketFetch,
    FakeSECFetch,
    annual,
    company_facts,
    instant,
    sec_provider,
)

ALPHA = COMPANY_FACTS_URL.format(cik="0000000101")


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def blocked(*args, **kwargs):
        raise AssertionError("real network access attempted")

    monkeypatch.setattr(socket.socket, "connect", blocked)
    monkeypatch.setattr("urllib.request.urlopen", blocked)


def research_facts(facts: dict):
    """Run the SEC field builder over an in-memory CompanyFacts document."""
    fetch = FakeSECFetch(overrides={
        SUBMISSIONS_URL.format(cik="0000000999"): {"name": "TEST CO", "fiscalYearEnd": "1231"},
        COMPANY_FACTS_URL.format(cik="0000000999"): facts,
    })
    provider = sec_provider(fetch)
    candidate = CompanyCandidate("0000000999", "TEST CO", "TST", "NYSE", "US", "CIK")
    return provider.research_company(candidate).fields


# --- Client: fair access ------------------------------------------------------------------------

def test_user_agent_required_for_uncached_request(monkeypatch):
    monkeypatch.delenv("SEC_USER_AGENT", raising=False)
    client = SECClient(cache_dir=None, fetch=FakeSECFetch())
    with pytest.raises(ResearchConfigurationError, match="SEC_USER_AGENT is not set"):
        client.company_tickers()


def test_user_agent_read_from_environment_and_sent(monkeypatch):
    monkeypatch.setenv("SEC_USER_AGENT", UA)
    fetch = FakeSECFetch()
    SECClient(cache_dir=None, fetch=fetch).company_tickers()
    (url, headers), = fetch.calls
    assert url == TICKERS_URL
    assert headers["User-Agent"] == UA and "gzip" in headers["Accept-Encoding"]


def test_cached_response_needs_no_user_agent_or_network(tmp_path, monkeypatch):
    SECClient(UA, cache_dir=tmp_path, fetch=FakeSECFetch()).company_tickers()
    monkeypatch.delenv("SEC_USER_AGENT", raising=False)
    offline = FakeSECFetch()
    data = SECClient(cache_dir=tmp_path, fetch=offline).company_tickers()
    assert data["data"][0][2] == "ALPH" and offline.calls == []


def test_force_refresh_bypasses_cache(tmp_path):
    fetch = FakeSECFetch()
    client = SECClient(UA, cache_dir=tmp_path, fetch=fetch)
    client.company_tickers()
    SECClient(UA, cache_dir=tmp_path, fetch=fetch).company_tickers(force_refresh=True)
    assert len(fetch.calls) == 2


def test_two_company_lookup_makes_few_requests():
    fetch = FakeSECFetch()
    provider = sec_provider(fetch)
    for ticker in ("ALPH", "BETA"):
        provider.research_company(provider.resolve_company(ticker))
    assert len(fetch.calls) == 5  # ticker table once, then submissions + facts per company


def test_retries_transient_errors_with_backoff():
    error = urllib.error.HTTPError(TICKERS_URL, 503, "Unavailable", {}, None)
    calls, sleeps = [], []

    def flaky(url, headers, timeout):
        calls.append(url)
        if len(calls) < 3:
            raise error
        return FakeSECFetch()(url, headers, timeout)

    client = SECClient(UA, cache_dir=None, fetch=flaky, sleep=sleeps.append, min_interval=0)
    assert client.company_tickers()["fields"][2] == "ticker"
    assert len(calls) == 3 and sleeps == [1.0, 2.0]


def test_retries_are_bounded():
    error = urllib.error.HTTPError(TICKERS_URL, 429, "Too Many", {}, None)
    fetch = FakeSECFetch(overrides={TICKERS_URL: error})
    client = SECClient(UA, cache_dir=None, fetch=fetch, sleep=lambda s: None, max_retries=2)
    with pytest.raises(ProviderRequestError, match="HTTP 429"):
        client.company_tickers()
    assert len(fetch.calls) == 3


def test_permanent_errors_not_retried():
    fetch = FakeSECFetch()
    client = SECClient(UA, cache_dir=None, fetch=fetch, sleep=lambda s: None)
    with pytest.raises(ProviderRequestError, match="HTTP 404"):
        client.company_facts("0000000555")
    assert len(fetch.calls) == 1


def test_rate_limit_spaces_requests():
    now = [0.0]
    sleeps = []

    def sleep(seconds):
        sleeps.append(seconds)
        now[0] += seconds

    client = SECClient(UA, cache_dir=None, fetch=FakeSECFetch(), sleep=sleep,
                       clock=lambda: now[0], min_interval=0.25)
    client.company_tickers()
    client.submissions("101")
    client.company_facts("101")
    assert sleeps == [0.25, 0.25]


# --- Identity -----------------------------------------------------------------------------------

def test_exact_ticker_resolves():
    candidate = sec_provider().resolve_company("alph")
    assert (candidate.name, candidate.ticker, candidate.provider_company_id) == (
        "ALPHA SOFTWARE CORP", "ALPH", "0000000101")
    assert candidate.display == "ALPHA SOFTWARE CORP · Ticker: ALPH · CIK: 0000000101"


@pytest.mark.parametrize("query", ["acme", "ACM"])
def test_name_or_partial_ticker_search_returns_candidates(query):
    names = [c.name for c in sec_provider().search_company(query)]
    assert names == ["ACME CLOUD CORP", "ACME HOLDINGS INC"]


def test_ambiguous_match_requires_selection():
    with pytest.raises(AmbiguousCompanyError) as exc_info:
        sec_provider().resolve_company("ACME")
    assert [c.ticker for c in exc_info.value.candidates] == ["ACMC", "ACMH"]


def test_single_name_match_still_requires_confirmation():
    with pytest.raises(AmbiguousCompanyError) as exc_info:
        sec_provider().resolve_company("gamma data")
    assert [c.ticker for c in exc_info.value.candidates] == ["GAMD"]


def test_company_not_found():
    with pytest.raises(CompanyNotFoundError):
        sec_provider().resolve_company("ZZZZ NOTHING")


# --- Period selection ---------------------------------------------------------------------------

def test_latest_annual_revenue_ignores_quarterly_and_q4_facts():
    fields = research_facts(company_facts({"Revenues": [
        annual("2023-01-01", "2023-12-31", 100, "2024-02-01"),
        annual("2024-01-01", "2024-12-31", 120, "2025-02-01"),
        annual("2024-10-01", "2024-12-31", 35, "2025-02-01"),                 # 10-K Q4 (3 months)
        annual("2025-01-01", "2025-03-31", 40, "2025-05-01", form="10-Q", fp="Q1"),
        annual("2025-01-01", "2025-06-30", 80, "2025-08-01", form="10-Q", fp="Q2"),  # YTD
    ]}))
    revenue = fields["revenue"]
    assert revenue.raw_value == "120" and revenue.period_end == "2024-12-31"
    assert revenue.period_start == "2024-01-01" and revenue.report_type == "10-K"


def test_amendment_supersedes_original_for_same_period():
    fields = research_facts(company_facts({"Revenues": [
        annual("2024-01-01", "2024-12-31", 120, "2025-02-01", accn="0000000999-25-000001"),
        annual("2024-01-01", "2024-12-31", 121, "2025-04-01", accn="0000000999-25-000009",
               form="10-K/A"),
    ]}))
    assert fields["revenue"].raw_value == "121"
    assert fields["revenue"].report_type == "10-K/A"
    assert fields["revenue"].source_document_id == "0000000999-25-000009"


def test_priority_concept_used_when_it_reports_the_latest_year():
    fields = research_facts(company_facts({
        "RevenueFromContractWithCustomerExcludingAssessedTax": [annual("2024-01-01", "2024-12-31", 118, "2025-02-01")],
        "Revenues": [annual("2024-01-01", "2024-12-31", 120, "2025-02-01")],
    }))
    assert fields["revenue"].concept == "RevenueFromContractWithCustomerExcludingAssessedTax"


def test_stale_priority_concept_does_not_win_over_latest_year():
    fields = research_facts(company_facts({
        "RevenueFromContractWithCustomerExcludingAssessedTax": [annual("2022-01-01", "2022-12-31", 90, "2023-02-01")],
        "Revenues": [annual("2024-01-01", "2024-12-31", 120, "2025-02-01")],
    }))
    assert fields["revenue"].concept == "Revenues" and fields["revenue"].period_end == "2024-12-31"


def test_latest_balance_sheet_instant_selected():
    fields = research_facts(company_facts({"CashAndCashEquivalentsAtCarryingValue": [
        instant("2024-12-31", 50, "2025-02-01", form="10-K", fp="FY"),
        instant("2025-03-31", 55, "2025-05-01"),
        instant("2025-06-30", 60, "2025-08-01", form="8-K"),  # not a 10-K/10-Q: ignored
    ]}))
    assert fields["cash"].raw_value == "55" and fields["cash"].period_label == "as of 2025-03-31"


# --- Mappings -------------------------------------------------------------------------------------

def test_restricted_cash_is_not_treated_as_cash():
    fields = research_facts(company_facts({
        "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents": [instant("2025-03-31", 99, "2025-05-01")]}))
    assert fields["cash"].status == ResearchStatus.MISSING


def test_net_income_shares_and_cash_flow_concepts():
    fields = research_facts(company_facts(
        {"NetIncomeLoss": [annual("2024-01-01", "2024-12-31", 30, "2025-02-01")],
         "NetCashProvidedByUsedInOperatingActivities": [annual("2024-01-01", "2024-12-31", 50, "2025-02-01")],
         "PaymentsToAcquirePropertyPlantAndEquipment": [annual("2024-01-01", "2024-12-31", 20, "2025-02-01")],
         "CommonStockSharesOutstanding": []},
        dei={"EntityCommonStockSharesOutstanding": [instant("2025-04-20", 800000000, "2025-05-01")]}))
    assert fields["net_income"].concept == "NetIncomeLoss"
    assert fields["shares_outstanding"].concept == "EntityCommonStockSharesOutstanding"
    assert fields["shares_outstanding"].normalized_value == 800.0
    assert fields["shares_outstanding"].normalized_unit == "shares (millions)"
    assert [i.concept for i in fields["free_cash_flow"].ingredients] == [
        "NetCashProvidedByUsedInOperatingActivities", "PaymentsToAcquirePropertyPlantAndEquipment"]


# --- EBITDA -----------------------------------------------------------------------------------------

def test_ebitda_reported_when_exact_concept_exists():
    fields = research_facts(company_facts(
        {"OperatingIncomeLoss": [annual("2024-01-01", "2024-12-31", 40, "2025-02-01")]},
        tst={"EBITDA": [annual("2024-01-01", "2024-12-31", 55, "2025-02-01")]}))
    ebitda = fields["ebitda"]
    assert ebitda.status == ResearchStatus.REPORTED
    assert (ebitda.taxonomy, ebitda.concept, ebitda.raw_value) == ("tst", "EBITDA", "55")


def test_adjusted_ebitda_is_not_accepted_as_reported():
    fields = research_facts(company_facts(
        {}, tst={"AdjustedEBITDA": [annual("2024-01-01", "2024-12-31", 70, "2025-02-01")]}))
    assert fields["ebitda"].status == ResearchStatus.MISSING


def test_ebitda_derived_from_same_period_ingredients():
    fields = research_facts(company_facts({
        "OperatingIncomeLoss": [annual("2024-01-01", "2024-12-31", 40000000, "2025-02-01")],
        "DepreciationAndAmortization": [annual("2024-01-01", "2024-12-31", 12500000, "2025-02-01")],
    }))
    ebitda = fields["ebitda"]
    assert ebitda.status == ResearchStatus.DERIVED and ebitda.normalized_value == 52.5
    assert ebitda.derivation.startswith("EBITDA = operating income (us-gaap:OperatingIncomeLoss) + "
                                        "depreciation & amortization (us-gaap:DepreciationAndAmortization)")
    assert [i.raw_value for i in ebitda.ingredients] == ["40000000", "12500000"]
    assert "not company-reported EBITDA" in ebitda.notes


def test_ebitda_missing_when_da_period_does_not_match():
    fields = research_facts(company_facts({
        "OperatingIncomeLoss": [annual("2024-01-01", "2024-12-31", 40, "2025-02-01")],
        "DepreciationAndAmortization": [annual("2023-01-01", "2023-12-31", 12, "2024-02-01")],
    }))
    assert fields["ebitda"].status == ResearchStatus.MISSING
    assert "Enter EBITDA manually before building the deal" in fields["ebitda"].notes


def test_ebitda_missing_without_operating_income():
    assert research_facts(company_facts({}))["ebitda"].status == ResearchStatus.MISSING


# --- FCF ----------------------------------------------------------------------------------------------

def test_fcf_derived():
    fields = research_facts(company_facts({
        "NetCashProvidedByUsedInOperatingActivities": [annual("2024-01-01", "2024-12-31", 118548000000, "2025-02-01")],
        "PaymentsToAcquirePropertyPlantAndEquipment": [annual("2024-01-01", "2024-12-31", 44477000000, "2025-02-01")],
    }))
    fcf = fields["free_cash_flow"]
    assert fcf.status == ResearchStatus.DERIVED and fcf.normalized_value == 74071.0
    assert "operating cash flow" in fcf.derivation and "capital expenditures" in fcf.derivation


@pytest.mark.parametrize("facts", [
    {"NetCashProvidedByUsedInOperatingActivities": [annual("2024-01-01", "2024-12-31", 50, "2025-02-01")]},
    {"PaymentsToAcquirePropertyPlantAndEquipment": [annual("2024-01-01", "2024-12-31", 20, "2025-02-01")]},
])
def test_fcf_missing_when_an_ingredient_is_missing(facts):
    fcf = research_facts(company_facts(facts))["free_cash_flow"]
    assert fcf.status == ResearchStatus.MISSING and fcf.normalized_value is None


# --- Debt ------------------------------------------------------------------------------------------------

def debt(us_gaap):
    return research_facts(company_facts(us_gaap))["debt"]


D = "2024-12-31"


def test_total_debt_preferred_over_overlapping_components():
    value = debt({"DebtLongtermAndShorttermCombinedAmount": [instant(D, 900, "2025-02-01")],
                  "LongTermDebt": [instant(D, 800, "2025-02-01")],
                  "ShortTermBorrowings": [instant(D, 100, "2025-02-01")],
                  "LongTermDebtCurrent": [instant(D, 50, "2025-02-01")]})
    assert value.status == ResearchStatus.REPORTED and value.raw_value == "900"


def test_noncurrent_plus_all_current_debt():
    value = debt({"LongTermDebtNoncurrent": [instant(D, 700, "2025-02-01")],
                  "DebtCurrent": [instant(D, 150, "2025-02-01")],
                  "LongTermDebtCurrent": [instant(D, 50, "2025-02-01")],
                  "ShortTermBorrowings": [instant(D, 100, "2025-02-01")]})
    assert value.status == ResearchStatus.DERIVED and value.raw_value == "850"
    assert [i.concept for i in value.ingredients] == ["LongTermDebtNoncurrent", "DebtCurrent"]


def test_long_term_debt_including_current_is_not_added_to_its_parts():
    value = debt({"LongTermDebt": [instant(D, 750, "2025-02-01")],
                  "LongTermDebtNoncurrent": [instant(D, 700, "2025-02-01")],
                  "LongTermDebtCurrent": [instant(D, 50, "2025-02-01")]})
    assert value.raw_value == "750"  # rule 5 (700 + 50) ties rule 6; never 1500


def test_components_at_different_dates_are_not_combined():
    value = debt({"LongTermDebtNoncurrent": [instant("2025-03-31", 700, "2025-05-01")],
                  "DebtCurrent": [instant(D, 150, "2025-02-01")]})
    assert value.status == ResearchStatus.MISSING


def test_most_recent_date_wins_across_rules():
    value = debt({"LongTermDebt": [instant(D, 750, "2025-02-01", form="10-K", fp="FY")],
                  "LongTermDebtNoncurrent": [instant("2025-03-31", 690, "2025-05-01")],
                  "LongTermDebtCurrent": [instant("2025-03-31", 40, "2025-05-01")]})
    assert value.raw_value == "730" and value.period_end == "2025-03-31"


def test_commercial_paper_is_not_added_separately():
    value = debt({"LongTermDebt": [instant(D, 750, "2025-02-01")],
                  "ShortTermBorrowings": [instant(D, 100, "2025-02-01")],
                  "CommercialPaper": [instant(D, 100, "2025-02-01")]})
    assert value.raw_value == "850"


def test_noncurrent_alone_is_missing_not_understated():
    assert debt({"LongTermDebtNoncurrent": [instant(D, 700, "2025-02-01")]}).status == ResearchStatus.MISSING


# --- Normalization --------------------------------------------------------------------------------------

@pytest.mark.parametrize("raw, units, expected", [
    ("211915000000", "millions", 211915.0), ("1234567", "millions", 1.234567),
    ("7433038381", "millions", 7433.038381), ("1500", "thousands", 1.5), ("5", "units", 5.0),
])
def test_normalization_is_exact(raw, units, expected):
    value = normalize(Decimal(raw), units)
    assert value == expected and repr(value) == repr(expected)


def test_unsupported_units_rejected():
    with pytest.raises(NormalizationError):
        normalize(Decimal(1), "lakhs")


def test_derived_sums_use_exact_decimals():
    fields = research_facts(company_facts({
        "OperatingIncomeLoss": [annual("2024-01-01", "2024-12-31", 100000, "2025-02-01")],
        "DepreciationAndAmortization": [annual("2024-01-01", "2024-12-31", 200000, "2025-02-01")],
    }))
    assert repr(fields["ebitda"].normalized_value) == "0.3"  # not 0.30000000000000004


# --- Provenance on the fixture companies --------------------------------------------------------------------

def test_reported_value_provenance():
    provider = sec_provider()
    result = provider.research_company(provider.resolve_company("ALPH"))
    revenue = result.fields["revenue"]
    assert revenue.status == ResearchStatus.REPORTED
    assert (revenue.raw_value, revenue.raw_unit) == ("245122000000", "USD")
    assert (revenue.normalized_value, revenue.normalized_unit) == (245122.0, "USD millions")
    assert revenue.taxonomy == "us-gaap"
    assert revenue.concept == "RevenueFromContractWithCustomerExcludingAssessedTax"
    assert (revenue.report_type, revenue.filed_date) == ("10-K", "2024-07-30")
    assert revenue.source_document_id == "0000000101-24-000010"
    assert revenue.source_url == "https://www.sec.gov/Archives/edgar/data/101/000000010124000010/"
    assert ("CIK", "0000000101") in revenue.provider_metadata
    assert "USD ÷ 1,000,000 → millions" in revenue.notes
    assert result.fiscal_year_end == "06-30 (MM-DD)" and result.exchange == "Nasdaq"
    assert {d.document_id for d in result.source_documents} >= {"0000000101-24-000010"}


def test_fixture_companies_summary():
    provider = sec_provider()
    alpha = provider.research_company(provider.resolve_company("ALPH")).fields
    beta = provider.research_company(provider.resolve_company("BETA")).fields
    assert alpha["ebitda"].status == ResearchStatus.DERIVED and alpha["ebitda"].normalized_value == 131720.0
    assert alpha["debt"].normalized_value == 45117.0 and alpha["cash"].normalized_value == 20840.0
    assert beta["revenue"].normalized_value == 34860.0   # amendment
    assert beta["ebitda"].status == ResearchStatus.MISSING
    assert beta["free_cash_flow"].status == ResearchStatus.MISSING
    assert beta["debt"].normalized_value == 9429.0       # total, not total + components
    assert beta["shares_outstanding"].concept == "CommonStockSharesOutstanding"


# --- Market price -------------------------------------------------------------------------------------------

def test_market_price_success():
    fetch = FakeMarketFetch()
    quote = StooqMarketPriceProvider(fetch=fetch).get_quote("ALPH", "US")
    assert quote.price == Decimal("412.37") and quote.as_of == "2025-09-26 22:00:08"
    assert fetch.calls == ["https://stooq.com/q/l/?s=alph.us&f=sd2t2c&h&e=csv"]
    value = quote_to_value(quote)
    assert value.status == ResearchStatus.MARKET and value.normalized_value == 412.37
    assert value.source_provider == "Stooq (third-party market data)"
    assert "not real-time" in value.notes and "not SEC accounting data" in value.notes


@pytest.mark.parametrize("body", [
    "Symbol,Date,Time,Close\nZZZZ.US,N/D,N/D,N/D\n", "", "Symbol,Date,Time,Close\nX.US,2025-09-26,22:00,abc\n",
    urllib.error.URLError("offline"),
])
def test_market_price_unavailable(body):
    with pytest.raises(MarketPriceError):
        StooqMarketPriceProvider(fetch=FakeMarketFetch(body)).get_quote("ZZZZ", "US")


def test_market_price_not_configured_for_other_jurisdictions():
    with pytest.raises(MarketPriceError, match="not configured"):
        StooqMarketPriceProvider(fetch=FakeMarketFetch()).get_quote("AIR", "FR")


def test_class_share_ticker_symbol():
    fetch = FakeMarketFetch("Symbol,Date,Time,Close\nBRK-B.US,2025-09-26,22:00:08,480.10\n")
    StooqMarketPriceProvider(fetch=fetch).get_quote("BRK.B", "US")
    assert "s=brk-b.us" in fetch.calls[0]


def test_provider_is_constructed_from_client():
    assert isinstance(sec_provider(), SECProvider)
