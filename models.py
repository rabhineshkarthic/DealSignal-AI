"""Typed containers for deal input data.

These dataclasses give the deal JSON a fixed, readable shape and fail loudly on
missing or unexpected fields. Financial validation (e.g. WACC > terminal growth)
lives in valuation.py, next to the formulas it protects.
"""

from __future__ import annotations

import json
from dataclasses import MISSING, asdict, dataclass, fields
from pathlib import Path
from typing import Any, TypeVar

T = TypeVar("T")


@dataclass(frozen=True)
class Acquirer:
    name: str
    revenue: float
    ebitda: float
    net_income: float
    cash: float
    debt: float
    shares_outstanding: float
    share_price: float


@dataclass(frozen=True)
class Target:
    name: str
    revenue: float
    ebitda: float
    net_income: float
    free_cash_flow: float
    cash: float
    debt: float


@dataclass(frozen=True)
class MarketData:
    # EV/EBITDA multiples may be null when target EBITDA is non-positive (not meaningful).
    comparable_ev_ebitda_multiple: float | None
    precedent_ev_ebitda_multiple: float | None
    # Optional EV/Revenue multiples (used as the primary multiple basis for targets with
    # non-positive EBITDA; supplementary otherwise).
    comparable_ev_revenue_multiple: float | None = None
    precedent_ev_revenue_multiple: float | None = None


@dataclass(frozen=True)
class DCFAssumptions:
    forecast_years: int
    fcf_growth_rate: float
    wacc: float
    terminal_growth_rate: float


@dataclass(frozen=True)
class SynergyAssumptions:
    annual_cost_synergy: float
    annual_revenue_synergy_ebitda: float
    realization_years: int
    tax_rate: float
    discount_rate: float
    integration_cost: float


@dataclass(frozen=True)
class TransactionAssumptions:
    offer_enterprise_value: float
    cash_percentage: float
    stock_percentage: float
    new_debt_interest_rate: float
    tax_rate: float


@dataclass(frozen=True)
class Deal:
    deal_name: str
    currency: str
    units: str
    acquirer: Acquirer
    target: Target
    market_data: MarketData
    dcf_assumptions: DCFAssumptions
    synergy_assumptions: SynergyAssumptions
    transaction_assumptions: TransactionAssumptions


def _build(cls: type[T], data: dict[str, Any], section: str) -> T:
    expected = {f.name for f in fields(cls)}
    required = {f.name for f in fields(cls)
                if f.default is MISSING and f.default_factory is MISSING}
    missing = required - data.keys()
    unexpected = data.keys() - expected
    if missing:
        raise ValueError(f"{section}: missing fields {sorted(missing)}")
    if unexpected:
        raise ValueError(f"{section}: unexpected fields {sorted(unexpected)}")
    return cls(**data)


def deal_from_dict(data: dict[str, Any]) -> Deal:
    """Build a Deal from a parsed JSON dict. Top-level metadata such as a disclaimer is ignored."""
    return Deal(
        deal_name=data["deal_name"],
        currency=data["currency"],
        units=data["units"],
        acquirer=_build(Acquirer, data["acquirer"], "acquirer"),
        target=_build(Target, data["target"], "target"),
        market_data=_build(MarketData, data["market_data"], "market_data"),
        dcf_assumptions=_build(DCFAssumptions, data["dcf_assumptions"], "dcf_assumptions"),
        synergy_assumptions=_build(
            SynergyAssumptions, data["synergy_assumptions"], "synergy_assumptions"
        ),
        transaction_assumptions=_build(
            TransactionAssumptions, data["transaction_assumptions"], "transaction_assumptions"
        ),
    )


def load_deal(path: str | Path) -> Deal:
    """Load and structurally validate a deal JSON file."""
    with open(path, encoding="utf-8") as f:
        return deal_from_dict(json.load(f))


OPTIONAL_MARKET_FIELDS = ("comparable_ev_revenue_multiple", "precedent_ev_revenue_multiple")


def deal_to_context(deal: Deal) -> dict[str, Any]:
    """Convert a Deal into a plain nested dict suitable for agent prompts. Optional
    market multiples that were not supplied are omitted rather than shown as null."""
    data = asdict(deal)
    for name in OPTIONAL_MARKET_FIELDS:
        if data["market_data"].get(name) is None:
            data["market_data"].pop(name, None)
    return data
