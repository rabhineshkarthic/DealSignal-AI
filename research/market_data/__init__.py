"""Market-price providers (separate from accounting-fact research).

Active: AlphaVantageMarketPriceProvider (selected by research.registry for the US).
Inactive: StooqMarketPriceProvider, kept for reference only; Stooq's public CSV quote
endpoint is no longer available and the registry never selects it.
"""

from research.market_data.alpha_vantage import AlphaVantageMarketPriceProvider
from research.market_data.base import MarketPriceProvider, MarketQuote, missing_price, quote_to_value
from research.market_data.provider import StooqMarketPriceProvider

__all__ = ["AlphaVantageMarketPriceProvider", "MarketPriceProvider", "MarketQuote",
           "StooqMarketPriceProvider", "missing_price", "quote_to_value"]
