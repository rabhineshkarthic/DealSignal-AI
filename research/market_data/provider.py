"""INACTIVE — Stooq market-price provider (kept for reference; not selected by the
registry). The active US source is research.market_data.alpha_vantage.

Third-party market data, clearly labelled as such, and never described as real-time.
Any failure raises MarketPriceError, which callers treat as non-fatal (the share price
becomes MISSING and the user enters it).

Status (verified manually): Stooq's CSV quote endpoint (/q/l/) now returns HTTP 404
("the page you requested does not exist or has been moved") for valid US symbols such
as msft.us and crm.us, and Stooq's other pages return a JavaScript browser-verification
challenge instead of data. Lookups therefore currently fail, with a clear message, and
research continues without a price. This adapter does not attempt to bypass that check.
"""

from __future__ import annotations

import csv
import io
import urllib.error
import urllib.request
from decimal import Decimal, InvalidOperation
from typing import Callable

from research.errors import MarketPriceError
from research.market_data.base import MarketQuote

QUOTE_URL = "https://stooq.com/q/l/?s={symbol}&f=sd2t2c&h&e=csv"
SUFFIX = {"US": ".us"}  # jurisdictions this provider can quote
EXPECTED_COLUMNS = ("Symbol", "Date", "Time", "Close")


def stooq_symbol(ticker: str, jurisdiction: str) -> str:
    """Stooq convention: lower-case ticker, '.' -> '-', market suffix (MSFT -> msft.us)."""
    if jurisdiction not in SUFFIX:
        raise MarketPriceError(f"Stooq quotes are not configured for {jurisdiction}.")
    cleaned = ticker.strip().lower().replace(".", "-")
    if not cleaned:
        raise MarketPriceError("No ticker was provided for the market-price lookup.")
    return cleaned + SUFFIX[jurisdiction]


def quote_url(symbol: str) -> str:
    return QUOTE_URL.format(symbol=symbol)

Fetch = Callable[[str, float], bytes]


def _urllib_fetch(url: str, timeout: float) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "ai-ma-deal-team"})
    with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 (fixed host)
        return response.read()


class StooqMarketPriceProvider:
    name = "Stooq"

    def __init__(self, fetch: Fetch = _urllib_fetch, timeout: float = 10.0) -> None:
        self._fetch = fetch
        self.timeout = timeout

    def get_quote(self, ticker: str, jurisdiction: str) -> MarketQuote:
        symbol = stooq_symbol(ticker, jurisdiction)
        url = quote_url(symbol)
        try:
            text = self._fetch(url, self.timeout).decode("utf-8", errors="replace")
        except urllib.error.HTTPError as exc:
            detail = (" — the Stooq CSV quote endpoint appears to be unavailable"
                      if exc.code == 404 else "")
            raise MarketPriceError(
                f"{self.name} returned HTTP {exc.code} for {symbol}{detail}.") from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise MarketPriceError(f"Market price request failed: {exc}") from exc
        if text.lstrip()[:1] == "<":
            raise MarketPriceError(
                f"{self.name} returned an HTML page (e.g. a browser-verification check) "
                f"instead of CSV for {symbol}.")
        rows = list(csv.DictReader(io.StringIO(text)))
        if not rows or not set(EXPECTED_COLUMNS) <= set(rows[0]):
            raise MarketPriceError(f"{self.name} returned an unexpected response for {symbol}.")
        row = rows[0]
        close, day = (row.get("Close") or "").strip(), (row.get("Date") or "").strip()
        if not close or close.upper() == "N/D" or not day or day.upper() == "N/D":
            raise MarketPriceError(f"No market quote available for {ticker}.")
        try:
            price = Decimal(close)
        except InvalidOperation as exc:
            raise MarketPriceError(f"Unreadable market price for {ticker}: {close!r}") from exc
        if price <= 0:
            raise MarketPriceError(f"Invalid market price for {ticker}: {close}")
        time_part = (row.get("Time") or "").strip()
        return MarketQuote(
            symbol=symbol.upper(), price=price, currency="USD",
            as_of=f"{day} {time_part}".strip(), provider_name=self.name, source_url=url,
            description="Delayed / end-of-day quote from a third-party source; not real-time.",
        )
