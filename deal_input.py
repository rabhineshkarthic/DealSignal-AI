"""Custom deal input: turn form values into the existing models.Deal.

Pure Python (no Streamlit), so it is fully testable. Percent fields are entered as
human-readable percentages (10.0 means 10%) and converted with Decimal arithmetic to
the decimals the model expects (0.1), so stored values are clean decimals such as
0.123 rather than 0.12300000000000001 (the numeric provenance validator compares
exact values). No network access, no LLM.

Validation has two layers:
1. Input checks here: friendly, field-level messages for obvious problems.
2. The existing model: the Deal is built with models.deal_from_dict and then run
   through the deterministic context builders, so every rule valuation.py enforces
   (e.g. WACC > terminal growth, net debt not exceeding EV) still applies.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Mapping

from models import Deal, deal_from_dict
from orchestrator import DealTeamOrchestrator

PERCENT_NOTE = ("Percentages are shown in human-readable form and stored internally as "
                "decimals.")
MULTIPLES_NOTE = ("If target EBITDA is positive, enter EV/EBITDA multiples (EV/Revenue is "
                  "optional and supplementary). If target EBITDA is zero or negative, EV/EBITDA "
                  "multiples are not meaningful: enter EV/Revenue multiples instead.")
EBITDA_MULTIPLES = ("market_data.comparable_ev_ebitda_multiple",
                    "market_data.precedent_ev_ebitda_multiple")
REVENUE_MULTIPLES = ("market_data.comparable_ev_revenue_multiple",
                     "market_data.precedent_ev_revenue_multiple")


@dataclass(frozen=True)
class Field:
    path: str      # dotted path in the deal JSON, e.g. "dcf_assumptions.wacc"
    label: str
    kind: str      # text | money | shares | price | multiple | int | pct
    optional: bool = False  # may be left empty (conditionally required multiples)


SECTIONS: tuple[tuple[str, tuple[Field, ...]], ...] = (
    ("Deal information", (
        Field("deal_name", "Deal name", "text"),
        Field("acquirer.name", "Acquirer name", "text"),
        Field("target.name", "Target name", "text"),
        Field("currency", "Currency", "text"),
        Field("units", "Units", "text"),
    )),
    ("Acquirer financials", (
        Field("acquirer.revenue", "Revenue", "money"),
        Field("acquirer.ebitda", "EBITDA", "money"),
        Field("acquirer.net_income", "Net income", "money"),
        Field("acquirer.cash", "Cash", "money"),
        Field("acquirer.debt", "Debt", "money"),
        Field("acquirer.shares_outstanding", "Acquirer share basis", "shares"),
        Field("acquirer.share_price", "Share price", "price"),
    )),
    ("Target financials", (
        Field("target.revenue", "Revenue", "money"),
        Field("target.ebitda", "EBITDA", "money"),
        Field("target.net_income", "Net income", "money"),
        Field("target.free_cash_flow", "Free cash flow", "money"),
        Field("target.cash", "Cash", "money"),
        Field("target.debt", "Debt", "money"),
    )),
    ("Market valuation", (
        Field("market_data.comparable_ev_ebitda_multiple", "Comparable EV / EBITDA (x)",
              "multiple", optional=True),
        Field("market_data.precedent_ev_ebitda_multiple", "Precedent transaction EV / EBITDA (x)",
              "multiple", optional=True),
        Field("market_data.comparable_ev_revenue_multiple", "Comparable EV / Revenue (x)",
              "multiple", optional=True),
        Field("market_data.precedent_ev_revenue_multiple", "Precedent transaction EV / Revenue (x)",
              "multiple", optional=True),
    )),
    ("DCF assumptions", (
        Field("dcf_assumptions.forecast_years", "Forecast years", "int"),
        Field("dcf_assumptions.fcf_growth_rate", "FCF growth rate (%)", "pct"),
        Field("dcf_assumptions.wacc", "WACC (%)", "pct"),
        Field("dcf_assumptions.terminal_growth_rate", "Terminal growth rate (%)", "pct"),
    )),
    ("Synergy assumptions", (
        Field("synergy_assumptions.annual_cost_synergy", "Annual cost synergy", "money"),
        Field("synergy_assumptions.annual_revenue_synergy_ebitda",
              "Annual revenue synergy EBITDA", "money"),
        Field("synergy_assumptions.realization_years", "Realization years", "int"),
        Field("synergy_assumptions.tax_rate", "Tax rate (%)", "pct"),
        Field("synergy_assumptions.discount_rate", "Discount rate (%)", "pct"),
        Field("synergy_assumptions.integration_cost", "Integration cost", "money"),
    )),
    ("Transaction assumptions", (
        Field("transaction_assumptions.offer_enterprise_value", "Offer enterprise value", "money"),
        Field("transaction_assumptions.cash_percentage", "Cash percentage (%)", "pct"),
        Field("transaction_assumptions.stock_percentage", "Stock percentage (%)", "pct"),
        Field("transaction_assumptions.new_debt_interest_rate", "New debt interest rate (%)", "pct"),
        Field("transaction_assumptions.tax_rate", "Transaction tax rate (%)", "pct"),
    )),
)
FIELDS: dict[str, Field] = {f.path: f for _, fields in SECTIONS for f in fields}


class DealInputError(ValueError):
    """The form values cannot produce a valid Deal. `errors` lists every problem."""

    def __init__(self, errors: list[str]):
        self.errors = errors
        super().__init__("; ".join(errors))


# --- Percent conversion ------------------------------------------------------------------

def pct_to_decimal(percent: float | int) -> float:
    """10.0 -> 0.1 and 12.3 -> 0.123 exactly (Decimal arithmetic, no float residue)."""
    return float(Decimal(repr(float(percent))) / Decimal(100))


def decimal_to_pct(value: float | int) -> float:
    """0.1 -> 10.0 and 0.055 -> 5.5 exactly."""
    return float(Decimal(repr(float(value))) * Decimal(100))


# --- Form <-> Deal ---------------------------------------------------------------------------

def form_from_deal(deal: Deal) -> dict[str, Any]:
    """Form values (percentages as percents) for an existing Deal, e.g. to prefill."""
    data = {
        "deal_name": deal.deal_name, "currency": deal.currency, "units": deal.units,
        "acquirer": vars(deal.acquirer), "target": vars(deal.target),
        "market_data": vars(deal.market_data), "dcf_assumptions": vars(deal.dcf_assumptions),
        "synergy_assumptions": vars(deal.synergy_assumptions),
        "transaction_assumptions": vars(deal.transaction_assumptions),
    }
    values = {}
    for path, spec in FIELDS.items():
        head, _, tail = path.partition(".")
        value = data[head][tail] if tail else data[head]
        values[path] = decimal_to_pct(value) if spec.kind == "pct" else value
    return values


def _is_number(value: Any) -> bool:
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(value))


def validate_form(values: Mapping[str, Any]) -> list[str]:
    """Friendly field-level checks. Deeper finance rules are enforced by the model."""
    errors: list[str] = []
    missing = [FIELDS[p].label for p in FIELDS if p not in values and not FIELDS[p].optional]
    if missing:
        return [f"Missing fields: {', '.join(missing)}"]
    values = {**{p: None for p in FIELDS if FIELDS[p].optional}, **values}

    def label(path: str) -> str:
        section = next(name for name, fields in SECTIONS if any(f.path == path for f in fields))
        return f"{section} — {FIELDS[path].label}"

    type_errors = False
    for path, spec in FIELDS.items():
        value = values[path]
        if spec.kind == "text":
            if not isinstance(value, str) or not value.strip():
                errors.append(f"{label(path)} must not be empty.")
            continue
        if spec.kind == "int":
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                errors.append(f"{label(path)} must be a whole number of at least 1.")
            continue
        if value is None and spec.optional:
            continue  # requirement depends on target EBITDA (checked below)
        if not _is_number(value):
            errors.append(f"{label(path)} must be a finite number.")
            type_errors = True

    if type_errors:  # range checks below need numbers; text errors don't block them
        return errors

    # EBITDA (target and acquirer) may be positive, zero, or negative.
    positive = ["acquirer.shares_outstanding", "acquirer.share_price", "target.free_cash_flow"]
    for path in positive:
        if values[path] <= 0:
            errors.append(f"{label(path)} must be greater than zero.")
    for path in (*EBITDA_MULTIPLES, *REVENUE_MULTIPLES):
        if values[path] is not None and values[path] <= 0:
            errors.append(f"{label(path)} must be greater than zero.")
    if values["target.ebitda"] > 0:
        for path in EBITDA_MULTIPLES:
            if values[path] is None:
                errors.append(f"{label(path)} is required because target EBITDA is positive.")
    else:
        for path in REVENUE_MULTIPLES:
            if values[path] is None:
                errors.append(f"{label(path)} is required: target EBITDA is non-positive, so "
                              "EV/EBITDA multiples are not meaningful.")
        if values["target.revenue"] <= 0:
            errors.append(f"{label('target.revenue')} must be greater than zero for EV/Revenue "
                          "valuation.")
    non_negative = ["acquirer.cash", "acquirer.debt", "target.cash", "target.debt",
                    "synergy_assumptions.annual_cost_synergy",
                    "synergy_assumptions.annual_revenue_synergy_ebitda",
                    "synergy_assumptions.integration_cost",
                    "transaction_assumptions.offer_enterprise_value"]
    for path in non_negative:
        if values[path] < 0:
            errors.append(f"{label(path)} must not be negative.")

    def pct_range(path: str, low: float, high: float, *, low_inclusive=True,
                  high_inclusive=False) -> None:
        v = values[path]
        ok_low = v >= low if low_inclusive else v > low
        ok_high = v <= high if high_inclusive else v < high
        if not (ok_low and ok_high):
            lo, hi = ("[" if low_inclusive else "("), ("]" if high_inclusive else ")")
            errors.append(f"{label(path)} must be in {lo}{low:g}%, {high:g}%{hi}.")

    pct_range("dcf_assumptions.wacc", 0, 100, low_inclusive=False)
    pct_range("synergy_assumptions.discount_rate", 0, 100, low_inclusive=False)
    for path in ("synergy_assumptions.tax_rate", "transaction_assumptions.tax_rate",
                 "transaction_assumptions.new_debt_interest_rate"):
        pct_range(path, 0, 100)
    for path in ("transaction_assumptions.cash_percentage",
                 "transaction_assumptions.stock_percentage"):
        pct_range(path, 0, 100, high_inclusive=True)
    if values["dcf_assumptions.fcf_growth_rate"] <= -100:
        errors.append(f"{label('dcf_assumptions.fcf_growth_rate')} must be greater than -100%.")
    if values["dcf_assumptions.wacc"] <= values["dcf_assumptions.terminal_growth_rate"]:
        errors.append("WACC must be greater than the terminal growth rate.")
    mix = (Decimal(repr(float(values["transaction_assumptions.cash_percentage"])))
           + Decimal(repr(float(values["transaction_assumptions.stock_percentage"]))))
    if mix != 100:
        errors.append(f"Cash % + Stock % must equal 100% (currently {mix:g}%).")
    return errors


def _to_json(values: Mapping[str, Any]) -> dict[str, Any]:
    data: dict[str, Any] = {}
    for path, spec in FIELDS.items():
        value = values.get(path)
        if value is None and spec.optional:
            pass  # stays None (not supplied)
        elif spec.kind == "text":
            value = value.strip()
        elif spec.kind == "pct":
            value = pct_to_decimal(value)
        elif spec.kind != "int":
            value = float(value)
        head, _, tail = path.partition(".")
        if tail:
            data.setdefault(head, {})[tail] = value
        else:
            data[head] = value
    return data


def build_deal(values: Mapping[str, Any]) -> Deal:
    """Build and fully validate a models.Deal from form values, or raise DealInputError.

    Uses models.deal_from_dict (the same loader as the JSON file) and then the existing
    deterministic context builders, so the model's own finance validation applies."""
    errors = validate_form(values)
    if errors:
        raise DealInputError(errors)
    try:
        deal = deal_from_dict(_to_json(values))
        DealTeamOrchestrator.build_deterministic_contexts(deal)
    except (ValueError, TypeError) as exc:
        raise DealInputError([f"The model rejected these inputs: {exc}"]) from exc
    return deal
