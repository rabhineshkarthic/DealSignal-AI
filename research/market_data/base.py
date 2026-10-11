"""Market-price abstraction, deliberately separate from accounting research."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol

from research.models import ResearchStatus, ResearchValue, missing_value


@dataclass(frozen=True)
class MarketQuote:
    symbol: str
    price: Decimal
    currency: str
    as_of: str              # market date/time reported by the provider
    provider_name: str      # e.g. "Alpha Vantage"
    source_url: str
    description: str        # e.g. "Delayed / end-of-day quote; not real-time"


class MarketPriceProvider(Protocol):
    name: str

    def get_quote(self, ticker: str, jurisdiction: str) -> MarketQuote:
        """Latest available quote, or raise MarketPriceError. Never fatal to research."""


def quote_to_value(quote: MarketQuote) -> ResearchValue:
    return ResearchValue(
        field_name="share_price", status=ResearchStatus.MARKET,
        normalized_value=float(quote.price), normalized_unit=f"{quote.currency} per share",
        raw_value=str(quote.price), raw_unit=f"{quote.currency} per share",
        source_provider=f"{quote.provider_name} (third-party market data)",
        source_name="Market quote", source_url=quote.source_url, period_end=quote.as_of,
        notes=f"{quote.description} Market data is not SEC accounting data.",
        provider_metadata=(("Symbol", quote.symbol),),
    )


def missing_price(reason: str) -> ResearchValue:
    return missing_value("share_price", f"{reason} Enter the share price manually.")
