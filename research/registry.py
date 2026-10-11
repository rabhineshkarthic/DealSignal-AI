"""Jurisdiction -> provider selection (deterministic).

Only jurisdictions with an implemented provider are available. Others are listed as
"coming later" and cannot be selected; nothing is silently routed through another
jurisdiction's provider.
"""

from __future__ import annotations

from typing import Callable

from research.errors import ResearchError
from research.market_data.base import MarketPriceProvider
from research.models import ResearchJurisdiction
from research.provider import CompanyResearchProvider

JURISDICTIONS: tuple[ResearchJurisdiction, ...] = (
    ResearchJurisdiction("US", "United States", "sec", "SEC", True),
    ResearchJurisdiction("FR", "France", None, "coming later", False),
    ResearchJurisdiction("DE", "Germany", None, "coming later", False),
)


def _sec_provider() -> CompanyResearchProvider:
    from dotenv import load_dotenv

    from config import DEFAULT_ENV_FILE
    from research.providers.sec import SECProvider

    load_dotenv(DEFAULT_ENV_FILE, override=False)  # SEC_USER_AGENT may live in .env
    return SECProvider()


def _alpha_vantage() -> MarketPriceProvider:
    from dotenv import load_dotenv

    from config import DEFAULT_ENV_FILE
    from research.market_data.alpha_vantage import AlphaVantageMarketPriceProvider

    load_dotenv(DEFAULT_ENV_FILE, override=False)  # ALPHA_VANTAGE_API_KEY may live in .env
    return AlphaVantageMarketPriceProvider()


PROVIDER_FACTORIES: dict[str, Callable[[], CompanyResearchProvider]] = {"sec": _sec_provider}
# Active market-price source per jurisdiction. There is deliberately no fallback: the
# Stooq adapter (research/market_data/provider.py) is inactive because its public CSV
# quote endpoint is no longer available.
MARKET_PRICE_FACTORIES: dict[str, Callable[[], MarketPriceProvider]] = {"US": _alpha_vantage}


def available_jurisdictions() -> tuple[ResearchJurisdiction, ...]:
    return tuple(j for j in JURISDICTIONS if j.available)


def planned_jurisdictions() -> tuple[ResearchJurisdiction, ...]:
    return tuple(j for j in JURISDICTIONS if not j.available)


def get_jurisdiction(code: str) -> ResearchJurisdiction:
    for jurisdiction in JURISDICTIONS:
        if jurisdiction.code == code:
            return jurisdiction
    raise ResearchError(f"Unknown research jurisdiction {code!r}.")


def create_provider(code: str) -> CompanyResearchProvider:
    jurisdiction = get_jurisdiction(code)
    if not jurisdiction.available or jurisdiction.provider_key not in PROVIDER_FACTORIES:
        raise ResearchError(f"Company research for {jurisdiction.name} is not supported yet.")
    return PROVIDER_FACTORIES[jurisdiction.provider_key]()


def create_market_price_provider(code: str) -> MarketPriceProvider | None:
    factory = MARKET_PRICE_FACTORIES.get(code)
    return factory() if factory else None
