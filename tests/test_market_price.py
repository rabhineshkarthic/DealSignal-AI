"""Stooq market-price adapter. Mocked responses only; no real network access.

Manual investigation (not part of the test run) found that Stooq's /q/l/ CSV quote
endpoint returns HTTP 404 for valid symbols (msft.us, crm.us) and other Stooq pages
return a JavaScript verification challenge. These tests pin the adapter's behaviour
in exactly those situations: clear, non-fatal MarketPriceErrors.
"""

import socket
import urllib.error
from decimal import Decimal
from pathlib import Path

import pytest

import ui_helpers as ui
from research import registry
from research.errors import MarketPriceError
from research.market_data import StooqMarketPriceProvider, quote_to_value
from research.market_data.provider import quote_url, stooq_symbol
from research.models import ResearchStatus
from research_fixtures import FakeMarketFetch, sec_provider

ROOT = Path(__file__).resolve().parent.parent
MSFT_URL = "https://stooq.com/q/l/?s=msft.us&f=sd2t2c&h&e=csv"
CRM_URL = "https://stooq.com/q/l/?s=crm.us&f=sd2t2c&h&e=csv"
NOT_FOUND_PAGE = ("<meta charset=utf-8><title>Stooq</title><center><p style=font-size:x-large>"
                  "The page you requested does not exist<br>or has been moved")
CHALLENGE_PAGE = ('<!DOCTYPE html><html><head><meta charset="utf-8"></head><body><noscript>'
                  "This site requires JavaScript to verify your browser.</noscript>")


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def blocked(*args, **kwargs):
        raise AssertionError("real network access attempted")

    monkeypatch.setattr(socket.socket, "connect", blocked)
    monkeypatch.setattr("urllib.request.urlopen", blocked)


def http_error(url, code=404):
    return urllib.error.HTTPError(url, code, "Not Found", {}, None)


def provider(body):
    fetch = FakeMarketFetch(body)
    return StooqMarketPriceProvider(fetch=fetch), fetch


# --- Symbols and URLs -------------------------------------------------------------------------

@pytest.mark.parametrize("ticker, symbol", [("MSFT", "msft.us"), ("CRM", "crm.us"),
                                            (" msft ", "msft.us"), ("BRK.B", "brk-b.us")])
def test_symbol_transformation(ticker, symbol):
    assert stooq_symbol(ticker, "US") == symbol


@pytest.mark.parametrize("symbol, url", [("msft.us", MSFT_URL), ("crm.us", CRM_URL)])
def test_generated_url(symbol, url):
    assert quote_url(symbol) == url


@pytest.mark.parametrize("ticker, url", [("MSFT", MSFT_URL), ("CRM", CRM_URL)])
def test_provider_requests_the_generated_url(ticker, url):
    p, fetch = provider(f"Symbol,Date,Time,Close\n{ticker}.US,2025-09-26,22:00:08,100.5\n")
    p.get_quote(ticker, "US")
    assert fetch.calls == [url]


@pytest.mark.parametrize("ticker, jurisdiction, message", [
    ("MSFT", "FR", "not configured for FR"), ("  ", "US", "No ticker")])
def test_invalid_symbol_inputs(ticker, jurisdiction, message):
    with pytest.raises(MarketPriceError, match=message):
        stooq_symbol(ticker, jurisdiction)


# --- Responses --------------------------------------------------------------------------------

def test_valid_csv_response():
    p, _ = provider("Symbol,Date,Time,Close\nMSFT.US,2025-09-26,22:00:08,511.46\n")
    quote = p.get_quote("MSFT", "US")
    assert quote.price == Decimal("511.46") and quote.symbol == "MSFT.US"
    assert quote.as_of == "2025-09-26 22:00:08" and quote.currency == "USD"
    value = quote_to_value(quote)
    assert value.status == ResearchStatus.MARKET and value.normalized_value == 511.46
    assert "third-party market data" in value.source_provider and "not real-time" in value.notes


def test_http_404_is_a_clear_market_price_error():
    p, _ = provider(http_error(MSFT_URL))
    with pytest.raises(MarketPriceError, match=r"HTTP 404 for msft\.us — the Stooq CSV quote "
                                               r"endpoint appears to be unavailable"):
        p.get_quote("MSFT", "US")


def test_other_http_errors_reported_with_status():
    p, _ = provider(http_error(CRM_URL, 503))
    with pytest.raises(MarketPriceError, match=r"HTTP 503 for crm\.us\.$"):
        p.get_quote("CRM", "US")


@pytest.mark.parametrize("page", [NOT_FOUND_PAGE, CHALLENGE_PAGE])
def test_html_pages_are_not_parsed_as_prices(page):
    p, _ = provider(page)
    with pytest.raises(MarketPriceError, match="HTML page .* instead of CSV"):
        p.get_quote("MSFT", "US")


@pytest.mark.parametrize("body", [
    "", "just some text\n", "Symbol,Date\nMSFT.US,2025-09-26\n",
    "Symbol,Date,Time,Close\nMSFT.US,2025-09-26,22:00:08,abc\n",
    "Symbol,Date,Time,Close\nMSFT.US,2025-09-26,22:00:08,-3\n",
])
def test_malformed_responses(body):
    p, _ = provider(body)
    with pytest.raises(MarketPriceError):
        p.get_quote("MSFT", "US")


@pytest.mark.parametrize("body", ["Symbol,Date,Time,Close\nMSFT.US,N/D,N/D,N/D\n",
                                  "Symbol,Date,Time,Close\nMSFT.US,2025-09-26,22:00:08,\n"])
def test_missing_price(body):
    p, _ = provider(body)
    with pytest.raises(MarketPriceError, match="No market quote available"):
        p.get_quote("MSFT", "US")


def test_network_error():
    p, _ = provider(urllib.error.URLError("connection refused"))
    with pytest.raises(MarketPriceError, match="Market price request failed"):
        p.get_quote("CRM", "US")


# --- Non-fatal: SEC research unaffected -------------------------------------------------------

def test_sec_research_succeeds_when_market_price_returns_404(monkeypatch):
    """Runs the real research UI path with the SEC fixtures and a 404 price response."""
    monkeypatch.setitem(registry.PROVIDER_FACTORIES, "sec", lambda: sec_provider())
    monkeypatch.setitem(registry.MARKET_PRICE_FACTORIES, "US", lambda: StooqMarketPriceProvider(
        fetch=FakeMarketFetch(http_error(MSFT_URL))))
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
    assert "HTTP 404" in draft.share_price.notes
    assert "Enter the share price manually" in draft.share_price.notes
    assert draft.acquirer.fields["revenue"].normalized_value == 245122.0
    assert draft.target.fields["debt"].normalized_value == 9429.0
    assert "acquirer.share_price" in draft.missing_paths()
