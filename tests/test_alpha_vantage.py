"""Alpha Vantage GLOBAL_QUOTE provider. Mocked responses only: no real request is made.

Response shapes follow Alpha Vantage's documented GLOBAL_QUOTE JSON, error message,
and rate-limit ("Information" / "Note") responses.
"""

import json
import socket
import urllib.error
import urllib.parse
from decimal import Decimal
from pathlib import Path

import pytest

import ui_helpers as ui
from research import approve_draft, registry
from research.draft import create_draft
from research.errors import MarketPriceError
from research.market_data import AlphaVantageMarketPriceProvider, StooqMarketPriceProvider, quote_to_value
from research.market_data.alpha_vantage import API_KEY_ENV, public_url, request_url
from research.models import ResearchStatus
from research_fixtures import sec_provider

ROOT = Path(__file__).resolve().parent.parent
KEY = "TESTKEY-never-shown-123"


def global_quote(symbol="MSFT", price="511.4600", day="2025-09-26"):
    return {"Global Quote": {
        "01. symbol": symbol, "02. open": "509.0000", "03. high": "512.5000",
        "04. low": "507.1000", "05. price": price, "06. volume": "18000000",
        "07. latest trading day": day, "08. previous close": "508.4500",
        "09. change": "3.0100", "10. change percent": "0.5920%"}}


class FakeFetch:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def __call__(self, url, timeout):
        self.calls.append(url)
        response = self.responses.pop(0) if len(self.responses) > 1 else self.responses[0]
        if isinstance(response, Exception):
            raise response
        if isinstance(response, (dict, list)):
            return json.dumps(response).encode()
        return response.encode() if isinstance(response, str) else response


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def blocked(*args, **kwargs):
        raise AssertionError("real network access attempted")

    monkeypatch.setattr(socket.socket, "connect", blocked)
    monkeypatch.setattr("urllib.request.urlopen", blocked)
    monkeypatch.delenv(API_KEY_ENV, raising=False)


def provider(*responses, key=KEY):
    fetch = FakeFetch(*responses)
    return AlphaVantageMarketPriceProvider(key, fetch=fetch, sleep=lambda s: None), fetch


def fails(match, *responses, key=KEY, ticker="MSFT"):
    p, fetch = provider(*responses, key=key)
    with pytest.raises(MarketPriceError, match=match) as exc_info:
        p.get_quote(ticker, "US")
    assert KEY not in str(exc_info.value)
    return fetch


# --- Request -----------------------------------------------------------------------------------

@pytest.mark.parametrize("ticker", ["MSFT", "CRM"])
def test_request_url_and_parameters(ticker):
    p, fetch = provider(global_quote(ticker))
    p.get_quote(ticker.lower(), "US")
    (url,) = fetch.calls
    parsed = urllib.parse.urlparse(url)
    assert (parsed.scheme, parsed.netloc, parsed.path) == ("https", "www.alphavantage.co", "/query")
    assert urllib.parse.parse_qs(parsed.query) == {
        "function": ["GLOBAL_QUOTE"], "symbol": [ticker], "apikey": [KEY]}
    assert url == request_url(ticker, KEY)


def test_api_key_read_from_environment(monkeypatch):
    monkeypatch.setenv(API_KEY_ENV, KEY)
    fetch = FakeFetch(global_quote("CRM", "243.1000"))
    AlphaVantageMarketPriceProvider(fetch=fetch).get_quote("CRM", "US")
    assert f"apikey={KEY}" in fetch.calls[0]


# --- Valid responses -----------------------------------------------------------------------------

@pytest.mark.parametrize("ticker, price", [("MSFT", "511.4600"), ("CRM", "243.1000")])
def test_valid_global_quote(ticker, price):
    p, _ = provider(global_quote(ticker, price))
    quote = p.get_quote(ticker, "US")
    assert quote.symbol == ticker and quote.price == Decimal(price) and quote.currency == "USD"
    assert quote.as_of == "latest trading day 2025-09-26"
    assert quote.provider_name == "Alpha Vantage"
    assert quote.source_url == public_url(ticker) and KEY not in quote.source_url
    assert "not represented as real-time" in quote.description


def test_market_value_status_and_labels():
    p, _ = provider(global_quote())
    value = quote_to_value(p.get_quote("MSFT", "US"))
    assert value.status == ResearchStatus.MARKET and value.normalized_value == 511.46
    assert value.normalized_unit == "USD per share"
    assert value.source_provider == "Alpha Vantage (third-party market data)"
    assert value.period_end == "latest trading day 2025-09-26"
    assert "Latest available market quote" in value.notes and "not SEC accounting data" in value.notes
    assert "real-time" in value.notes and "represented as real-time" in value.notes
    assert KEY not in repr(value)
    assert ui.STATUS_TEXT[value.status.value] == "Market data"


def test_no_timestamp_is_invented():
    body = global_quote()
    del body["Global Quote"]["07. latest trading day"]
    p, _ = provider(body)
    assert p.get_quote("MSFT", "US").as_of == "latest trading day not reported"


# --- Failures (all MarketPriceError, never zero, never leaking the key) -----------------------------

def test_missing_api_key():
    fetch = fails(f"{API_KEY_ENV} is not set", global_quote(), key="")
    assert fetch.calls == []  # no request without a key


def test_invalid_api_key_response():
    fails("rejected the API key", {"Error Message": "the parameter apikey is invalid or missing. "
                                                    "Please claim your free API key"})


def test_ticker_not_found_error_message():
    fails("ticker not found or invalid request",
          {"Error Message": "Invalid API call. Please retry or visit the documentation"})


@pytest.mark.parametrize("key", ["Information", "Note"])
def test_rate_limit_response(key):
    fails("rate limit", {key: "Thank you for using Alpha Vantage! Our standard API rate limit "
                              "is 25 requests per day."})


def test_empty_global_quote():
    fails("no quote for ZZZZ", {"Global Quote": {}}, ticker="ZZZZ")


@pytest.mark.parametrize("body", ["<html>oops</html>", "{not json", ""])
def test_malformed_json(body):
    fails("malformed JSON", body)


@pytest.mark.parametrize("body", [[], {"Something": 1}, {"Global Quote": "text"}])
def test_unexpected_shapes(body):
    fails("unexpected response", body)


def test_missing_price_field():
    body = global_quote()
    del body["Global Quote"]["05. price"]
    fails("has no price", body)


@pytest.mark.parametrize("price, match", [("abc", "non-numeric"), ("0.0000", "invalid price"),
                                          ("-5.00", "invalid price"), ("NaN", "invalid price")])
def test_invalid_price(price, match):
    fails(match, global_quote(price=price))


def test_quote_for_a_different_symbol_is_rejected():
    fails("returned a quote for CRM, not MSFT", global_quote("CRM"))


def test_http_failure_not_retried_when_permanent():
    fetch = fails("HTTP 403", urllib.error.HTTPError(request_url("MSFT", KEY), 403, "Forbidden", {}, None))
    assert len(fetch.calls) == 1


def test_transient_http_failure_retried_then_succeeds():
    error = urllib.error.HTTPError(request_url("MSFT", KEY), 503, "Unavailable", {}, None)
    sleeps = []
    fetch = FakeFetch(error, global_quote())
    quote = AlphaVantageMarketPriceProvider(KEY, fetch=fetch, sleep=sleeps.append).get_quote("MSFT", "US")
    assert quote.price == Decimal("511.4600") and len(fetch.calls) == 2 and sleeps == [1.0]


def test_retries_are_bounded():
    error = urllib.error.HTTPError(request_url("MSFT", KEY), 500, "Error", {}, None)
    fetch = fails("HTTP 500", error)
    assert len(fetch.calls) == 3


@pytest.mark.parametrize("error", [urllib.error.URLError("timed out"), TimeoutError("timed out")])
def test_network_failure(error):
    fetch = fails("network error", error)
    assert len(fetch.calls) == 3


def test_unsupported_jurisdiction_and_blank_ticker():
    p, fetch = provider(global_quote())
    with pytest.raises(MarketPriceError, match="not configured for DE"):
        p.get_quote("SAP", "DE")
    with pytest.raises(MarketPriceError, match="No ticker"):
        p.get_quote("  ", "US")
    assert fetch.calls == []


def test_provider_repr_hides_key():
    assert KEY not in repr(AlphaVantageMarketPriceProvider(KEY))


# --- Registry ------------------------------------------------------------------------------------

def test_registry_selects_alpha_vantage_for_us(monkeypatch):
    monkeypatch.setattr("dotenv.load_dotenv", lambda *a, **k: False)  # ignore any local .env
    selected = registry.create_market_price_provider("US")
    assert isinstance(selected, AlphaVantageMarketPriceProvider)
    assert not isinstance(selected, StooqMarketPriceProvider)
    assert registry.create_market_price_provider("FR") is None


def test_stooq_is_not_referenced_by_the_registry():
    source = (ROOT / "research" / "registry.py").read_text()
    assert "StooqMarketPriceProvider" not in source and "_stooq" not in source


# --- Non-fatal to SEC research; overrides preserve the original price ----------------------------------

def test_sec_research_succeeds_when_key_is_missing(monkeypatch):
    """The real research UI path, with SEC fixtures and no Alpha Vantage key."""
    monkeypatch.setitem(registry.PROVIDER_FACTORIES, "sec", lambda: sec_provider())
    monkeypatch.setitem(registry.MARKET_PRICE_FACTORIES, "US",
                        lambda: AlphaVantageMarketPriceProvider("", fetch=FakeFetch(global_quote())))
    from config import load_settings
    monkeypatch.setattr(ui, "load_settings", lambda: load_settings(env_file=None))
    from streamlit.testing.v1 import AppTest

    at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=120).run()
    at.sidebar.radio(key="deal_source").set_value(ui.RESEARCH_DEAL).run()
    at.text_input(key="research_q_acquirer").set_value("ALPH")
    at.text_input(key="research_q_target").set_value("BETA")
    next(b for b in at.button if b.label == "Research Companies").click().run()

    assert not at.exception and not at.session_state["research_errors"]
    draft = at.session_state["research_draft"]
    assert draft.share_price.status == ResearchStatus.MISSING
    assert f"{API_KEY_ENV} is not set" in draft.share_price.notes
    assert draft.acquirer.fields["revenue"].normalized_value == 245122.0


def test_app_shows_market_data_badge_for_alpha_vantage_price(monkeypatch):
    monkeypatch.setitem(registry.PROVIDER_FACTORIES, "sec", lambda: sec_provider())
    monkeypatch.setitem(registry.MARKET_PRICE_FACTORIES, "US", lambda: AlphaVantageMarketPriceProvider(
        KEY, fetch=FakeFetch(global_quote("ALPH", "412.3700"))))
    from config import load_settings
    monkeypatch.setattr(ui, "load_settings", lambda: load_settings(env_file=None))
    from streamlit.testing.v1 import AppTest

    at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=120).run()
    at.sidebar.radio(key="deal_source").set_value(ui.RESEARCH_DEAL).run()
    at.text_input(key="research_q_acquirer").set_value("ALPH")
    at.text_input(key="research_q_target").set_value("BETA")
    next(b for b in at.button if b.label == "Research Companies").click().run()
    draft = at.session_state["research_draft"]
    assert draft.share_price.status == ResearchStatus.MARKET
    page = " ".join(m.value for m in at.markdown)
    assert "Market data" in page and "Alpha Vantage (third-party market data)" in page
    assert KEY not in page


def test_user_override_preserves_original_alpha_vantage_price():
    p = sec_provider()
    acquirer = p.research_company(p.resolve_company("ALPH"))
    target = p.research_company(p.resolve_company("BETA"))
    av, _ = provider(global_quote("ALPH", "412.3700"))
    price = quote_to_value(av.get_quote("ALPH", "US"))
    draft = create_draft(registry.get_jurisdiction("US"), acquirer, target, price)
    provenance = approve_draft(draft, {"target.ebitda": 8900.0, "target.free_cash_flow": 7000.0,
                                       "acquirer.share_price": 400.0},
                               {"acquirer.share_price": "Undisturbed price before announcement"})
    approved = provenance.approved["acquirer.share_price"]
    assert approved.status == ResearchStatus.USER_OVERRIDE and approved.value == 400.0
    assert approved.original.status == ResearchStatus.MARKET
    assert approved.original.normalized_value == 412.37
    assert approved.original.source_provider == "Alpha Vantage (third-party market data)"
    source = provenance.excel_sources()["acquirer.share_price"]
    assert source.startswith("EFFECTIVE VALUE 400.0 — user override — reason: Undisturbed price")
    assert ("Original researched value (kept for provenance, not used): 412.37 USD per share — "
            "MARKET — Alpha Vantage (third-party market data)") in source
