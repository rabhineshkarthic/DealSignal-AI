"""Alpha Vantage market-price provider (official GLOBAL_QUOTE API).

Third-party market data, separate from accounting research. The quote is treated as
the latest available / end-of-day price: GLOBAL_QUOTE reports a latest trading day
but no intraday timestamp, so no time is invented and the value is never described
as real-time. Every failure raises MarketPriceError, which callers treat as non-fatal
(the share price becomes MISSING and the user enters it).

The API key comes only from ALPHA_VANTAGE_API_KEY. It is sent in the request and
never included in error messages, provenance, or stored URLs.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from decimal import Decimal, InvalidOperation
from typing import Callable

from research.errors import MarketPriceError
from research.market_data.base import MarketQuote

ENDPOINT = "https://www.alphavantage.co/query"
API_KEY_ENV = "ALPHA_VANTAGE_API_KEY"
SUPPORTED = {"US": "USD"}  # jurisdiction -> quote currency for its listings
RETRYABLE_HTTP = frozenset({429, 500, 502, 503, 504})
DESCRIPTION = ("Latest available market quote. Default Alpha Vantage quote data may be "
               "end-of-day; not represented as real-time.")

Fetch = Callable[[str, float], bytes]


def _urllib_fetch(url: str, timeout: float) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "ai-ma-deal-team"})
    with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 (fixed host)
        return response.read()


def request_url(symbol: str, api_key: str) -> str:
    return ENDPOINT + "?" + urllib.parse.urlencode(
        {"function": "GLOBAL_QUOTE", "symbol": symbol, "apikey": api_key})


def public_url(symbol: str) -> str:
    """The request URL without the API key, safe to show and store."""
    return ENDPOINT + "?" + urllib.parse.urlencode({"function": "GLOBAL_QUOTE", "symbol": symbol})


def alpha_vantage_symbol(ticker: str) -> str:
    symbol = ticker.strip().upper()
    if not symbol:
        raise MarketPriceError("No ticker was provided for the market-price lookup.")
    return symbol


class AlphaVantageMarketPriceProvider:
    name = "Alpha Vantage"

    def __init__(self, api_key: str | None = None, *, fetch: Fetch = _urllib_fetch,
                 timeout: float = 10.0, max_retries: int = 2,
                 sleep: Callable[[float], None] = time.sleep) -> None:
        self._api_key = api_key
        self._fetch = fetch
        self.timeout = timeout
        self.max_retries = max_retries
        self._sleep = sleep

    def __repr__(self) -> str:  # never expose the key
        return f"{type(self).__name__}(timeout={self.timeout}, max_retries={self.max_retries})"

    def _key(self) -> str:
        key = (self._api_key if self._api_key is not None else os.getenv(API_KEY_ENV, "")).strip()
        if not key:
            raise MarketPriceError(
                f"{API_KEY_ENV} is not set, so no market price was retrieved. Add a free "
                "Alpha Vantage key to .env, or enter the share price manually.")
        return key

    def _get(self, url: str) -> bytes:
        attempts = self.max_retries + 1
        for attempt in range(1, attempts + 1):
            try:
                return self._fetch(url, self.timeout)
            except urllib.error.HTTPError as exc:
                if exc.code not in RETRYABLE_HTTP or attempt == attempts:
                    raise MarketPriceError(
                        f"{self.name} request failed with HTTP {exc.code}.") from None
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                if attempt == attempts:
                    reason = getattr(exc, "reason", exc)
                    raise MarketPriceError(
                        f"{self.name} request failed (network error: {reason}).") from None
            self._sleep(2.0 ** (attempt - 1))
        raise AssertionError("unreachable")

    def get_quote(self, ticker: str, jurisdiction: str) -> MarketQuote:
        if jurisdiction not in SUPPORTED:
            raise MarketPriceError(f"{self.name} quotes are not configured for {jurisdiction}.")
        symbol = alpha_vantage_symbol(ticker)
        key = self._key()
        raw = self._get(request_url(symbol, key))
        try:
            data = json.loads(raw.decode("utf-8", errors="replace"))
        except json.JSONDecodeError:
            raise MarketPriceError(f"{self.name} returned malformed JSON for {symbol}.") from None
        if not isinstance(data, dict):
            raise MarketPriceError(f"{self.name} returned an unexpected response for {symbol}.")

        # Documented non-quote responses. Their text is summarized, never echoed, in
        # case a provider message ever repeats request details.
        if "Error Message" in data:
            text = str(data["Error Message"]).lower()
            if "apikey" in text or "api key" in text:
                raise MarketPriceError(f"{self.name} rejected the API key (invalid or missing).")
            raise MarketPriceError(f"{self.name} returned an error for {symbol} (ticker not "
                                   "found or invalid request).")
        if "Information" in data or "Note" in data:
            raise MarketPriceError(f"{self.name} did not return a quote (API rate limit or "
                                   "usage notice). Try again later or enter the price manually.")

        quote = data.get("Global Quote")
        if not isinstance(quote, dict):
            raise MarketPriceError(f"{self.name} returned an unexpected response for {symbol}.")
        if not quote:
            raise MarketPriceError(f"{self.name} returned no quote for {symbol} (ticker not "
                                   "found or no recent trading data).")
        price_text = str(quote.get("05. price") or "").strip()
        if not price_text:
            raise MarketPriceError(f"{self.name} quote for {symbol} has no price.")
        try:
            price = Decimal(price_text)
        except InvalidOperation:
            raise MarketPriceError(f"{self.name} returned a non-numeric price for {symbol}.") from None
        if not price.is_finite() or price <= 0:
            raise MarketPriceError(f"{self.name} returned an invalid price for {symbol}.")
        returned_symbol = str(quote.get("01. symbol") or symbol).strip().upper()
        if returned_symbol != symbol:
            raise MarketPriceError(f"{self.name} returned a quote for {returned_symbol}, "
                                   f"not {symbol}.")
        trading_day = str(quote.get("07. latest trading day") or "").strip()
        return MarketQuote(
            symbol=symbol, price=price, currency=SUPPORTED[jurisdiction],
            as_of=(f"latest trading day {trading_day}" if trading_day
                   else "latest trading day not reported"),
            provider_name=self.name, source_url=public_url(symbol), description=DESCRIPTION,
        )
