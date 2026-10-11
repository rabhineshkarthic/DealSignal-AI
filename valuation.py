"""Deterministic financial valuation engine for the AI M&A Deal Team.

Every figure produced here is computed by plain Python arithmetic from explicit
inputs. No LLM is involved in any calculation.

Conventions
-----------
- All monetary amounts share one currency and unit (e.g. USD millions).
- Rates are decimals: 0.10 means 10%.
- Net Debt = Debt - Cash
- Equity Value = Enterprise Value - Net Debt
- Enterprise value (EV) and equity value are always returned under distinct keys.
"""

from __future__ import annotations

import math
from typing import Any


# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------

def _require_finite(name: str, value: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{name} must be a number, got {type(value).__name__}")
    if not math.isfinite(value):
        raise ValueError(f"{name} must be finite, got {value}")
    return float(value)


def _require_non_negative(name: str, value: float) -> float:
    value = _require_finite(name, value)
    if value < 0:
        raise ValueError(f"{name} must be >= 0, got {value}")
    return value


def _require_positive(name: str, value: float) -> float:
    value = _require_finite(name, value)
    if value <= 0:
        raise ValueError(f"{name} must be > 0, got {value}")
    return value


def _require_rate(name: str, value: float, *, allow_zero: bool = True) -> float:
    """A rate expressed as a decimal in [0, 1) (or (0, 1) if allow_zero is False)."""
    value = _require_finite(name, value)
    lower_ok = value >= 0 if allow_zero else value > 0
    if not lower_ok or value >= 1:
        bound = "[0, 1)" if allow_zero else "(0, 1)"
        raise ValueError(f"{name} must be a decimal rate in {bound}, got {value}")
    return value


def _require_positive_int(name: str, value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{name} must be an integer, got {type(value).__name__}")
    if value < 1:
        raise ValueError(f"{name} must be >= 1, got {value}")
    return value


# ---------------------------------------------------------------------------
# Capital structure bridge
# ---------------------------------------------------------------------------

def calculate_net_debt(debt: float, cash: float) -> dict[str, float]:
    """Compute net debt as Debt - Cash.

    Net debt may be negative (a net cash position); that is valid.
    """
    debt = _require_non_negative("debt", debt)
    cash = _require_non_negative("cash", cash)
    return {"debt": debt, "cash": cash, "net_debt": debt - cash}


def enterprise_to_equity_value(
    enterprise_value: float, debt: float, cash: float
) -> dict[str, float]:
    """Bridge enterprise value to equity value: Equity = EV - (Debt - Cash).

    Raises ValueError if the resulting equity value is negative, since a
    negative purchase price for equity is not a meaningful result.
    """
    enterprise_value = _require_positive("enterprise_value", enterprise_value)
    net_debt = calculate_net_debt(debt, cash)["net_debt"]
    equity_value = enterprise_value - net_debt
    if equity_value < 0:
        raise ValueError(
            f"Net debt ({net_debt}) exceeds enterprise value ({enterprise_value}); "
            "equity value would be negative"
        )
    return {
        "enterprise_value": enterprise_value,
        "debt": float(debt),
        "cash": float(cash),
        "net_debt": net_debt,
        "equity_value": equity_value,
    }


# ---------------------------------------------------------------------------
# Relative valuation
# ---------------------------------------------------------------------------

def _multiple_valuation(method: str, ebitda: float, multiple: float) -> dict[str, Any]:
    ebitda = _require_positive("ebitda", ebitda)
    multiple = _require_positive("ev_ebitda_multiple", multiple)
    return {
        "method": method,
        "ebitda": ebitda,
        "multiple": multiple,
        "enterprise_value": ebitda * multiple,
    }


def comparable_company_valuation(ebitda: float, ev_ebitda_multiple: float) -> dict[str, Any]:
    """Enterprise value implied by trading comparables: EV = EBITDA x peer EV/EBITDA."""
    return _multiple_valuation("Comparable Companies", ebitda, ev_ebitda_multiple)


def precedent_transaction_valuation(
    ebitda: float, ev_ebitda_multiple: float
) -> dict[str, Any]:
    """Enterprise value implied by precedent deals: EV = EBITDA x transaction EV/EBITDA."""
    return _multiple_valuation("Precedent Transactions", ebitda, ev_ebitda_multiple)


# ---------------------------------------------------------------------------
# Intrinsic valuation
# ---------------------------------------------------------------------------

def dcf_valuation(
    base_fcf: float,
    growth_rate: float,
    wacc: float,
    terminal_growth_rate: float,
    forecast_years: int = 5,
) -> dict[str, Any]:
    """Unlevered DCF using a constant-growth forecast and Gordon growth terminal value.

    FCF_t            = base_fcf * (1 + growth_rate) ** t, for t = 1..forecast_years
    Terminal value   = FCF_final * (1 + terminal_growth_rate) / (wacc - terminal_growth_rate)
    PV of a cash flow at year t = CF / (1 + wacc) ** t   (end-of-year discounting)

    The result is an enterprise value (cash flows to all capital providers).

    Raises ValueError if wacc <= terminal_growth_rate, since the Gordon growth
    formula is undefined or negative in that case.
    """
    base_fcf = _require_positive("base_fcf", base_fcf)
    growth_rate = _require_finite("growth_rate", growth_rate)
    if growth_rate <= -1:
        raise ValueError(f"growth_rate must be > -1, got {growth_rate}")
    wacc = _require_rate("wacc", wacc, allow_zero=False)
    terminal_growth_rate = _require_finite("terminal_growth_rate", terminal_growth_rate)
    forecast_years = _require_positive_int("forecast_years", forecast_years)

    if wacc <= terminal_growth_rate:
        raise ValueError(
            f"wacc ({wacc}) must exceed terminal_growth_rate ({terminal_growth_rate}); "
            "the terminal value is undefined otherwise"
        )

    years = list(range(1, forecast_years + 1))
    forecast_fcfs = [base_fcf * (1 + growth_rate) ** t for t in years]
    discount_factors = [1 / (1 + wacc) ** t for t in years]
    discounted_fcfs = [fcf * df for fcf, df in zip(forecast_fcfs, discount_factors)]
    pv_forecast = sum(discounted_fcfs)

    terminal_value = forecast_fcfs[-1] * (1 + terminal_growth_rate) / (wacc - terminal_growth_rate)
    discounted_terminal_value = terminal_value * discount_factors[-1]

    enterprise_value = pv_forecast + discounted_terminal_value
    terminal_value_share = (
        discounted_terminal_value / enterprise_value
        if enterprise_value != 0
        else None
    )

    return {
        "method": "Discounted Cash Flow",
        "base_fcf": base_fcf,
        "growth_rate": growth_rate,
        "wacc": wacc,
        "terminal_growth_rate": terminal_growth_rate,
        "forecast_years": forecast_years,
        "years": years,
        "forecast_fcfs": forecast_fcfs,
        "discount_factors": discount_factors,
        "discounted_fcfs": discounted_fcfs,
        "pv_forecast_fcfs": pv_forecast,
        "terminal_value": terminal_value,
        "discounted_terminal_value": discounted_terminal_value,
        "terminal_value_share_of_ev": terminal_value_share,
        "enterprise_value": enterprise_value,
    }


# ---------------------------------------------------------------------------
# Synergies
# ---------------------------------------------------------------------------

def calculate_synergy_value(
    annual_cost_synergy: float,
    annual_revenue_synergy_ebitda: float,
    tax_rate: float,
    discount_rate: float,
    realization_years: int,
    integration_cost: float,
) -> dict[str, Any]:
    """Net present value of synergies over a finite realization period.

    Simplifying assumptions (deliberately conservative and explicit):
    - Run-rate synergies are achieved in full from year 1.
    - Synergies last only `realization_years` years; no terminal value is given.
    - Synergies are taxed at `tax_rate` and discounted at end of each year.
    - `integration_cost` is a one-off cost incurred at close (t = 0), undiscounted,
      and treated as not tax-deductible.

    NPV = sum_{t=1..N} after_tax_synergy / (1 + r)^t - integration_cost
    """
    cost = _require_non_negative("annual_cost_synergy", annual_cost_synergy)
    revenue = _require_non_negative("annual_revenue_synergy_ebitda", annual_revenue_synergy_ebitda)
    tax_rate = _require_rate("tax_rate", tax_rate)
    discount_rate = _require_rate("discount_rate", discount_rate, allow_zero=False)
    realization_years = _require_positive_int("realization_years", realization_years)
    integration_cost = _require_non_negative("integration_cost", integration_cost)

    pre_tax = cost + revenue
    after_tax = pre_tax * (1 - tax_rate)
    years = list(range(1, realization_years + 1))
    discounted = [after_tax / (1 + discount_rate) ** t for t in years]
    pv_synergies = sum(discounted)

    return {
        "annual_cost_synergy": cost,
        "annual_revenue_synergy_ebitda": revenue,
        "annual_pre_tax_synergies": pre_tax,
        "annual_after_tax_synergies": after_tax,
        "tax_rate": tax_rate,
        "discount_rate": discount_rate,
        "realization_years": realization_years,
        "discounted_synergies": discounted,
        "pv_synergies": pv_synergies,
        "integration_cost": integration_cost,
        "net_synergy_value": pv_synergies - integration_cost,
    }


# ---------------------------------------------------------------------------
# Accretion / dilution
# ---------------------------------------------------------------------------

def accretion_dilution(
    acquirer_net_income: float,
    acquirer_shares: float,
    acquirer_share_price: float,
    target_net_income: float,
    purchase_equity_value: float,
    cash_percentage: float,
    stock_percentage: float,
    interest_rate: float,
    tax_rate: float,
    annual_after_tax_synergies: float = 0.0,
) -> dict[str, Any]:
    """Year-one EPS accretion/dilution for a cash-and-stock acquisition.

    Assumptions:
    - The cash portion of the equity purchase price is funded entirely with new
      debt at `interest_rate`; interest is tax-deductible at `tax_rate`.
    - The stock portion is paid by issuing new acquirer shares at
      `acquirer_share_price`.
    - The target's existing net debt is assumed refinanced/assumed at the same
      cost it already bears, so it is already reflected in target net income.

    Pro forma net income = acquirer NI + target NI - after-tax new interest
                           + annual after-tax synergies
    Pro forma shares     = acquirer shares + new shares issued
    """
    acquirer_net_income = _require_finite("acquirer_net_income", acquirer_net_income)
    acquirer_shares = _require_positive("acquirer_shares", acquirer_shares)
    acquirer_share_price = _require_positive("acquirer_share_price", acquirer_share_price)
    target_net_income = _require_finite("target_net_income", target_net_income)
    purchase_equity_value = _require_positive("purchase_equity_value", purchase_equity_value)
    cash_percentage = _require_finite("cash_percentage", cash_percentage)
    stock_percentage = _require_finite("stock_percentage", stock_percentage)
    interest_rate = _require_rate("interest_rate", interest_rate)
    tax_rate = _require_rate("tax_rate", tax_rate)
    annual_after_tax_synergies = _require_finite(
        "annual_after_tax_synergies", annual_after_tax_synergies
    )

    for name, pct in (("cash_percentage", cash_percentage), ("stock_percentage", stock_percentage)):
        if not 0 <= pct <= 1:
            raise ValueError(f"{name} must be in [0, 1], got {pct}")
    if not math.isclose(cash_percentage + stock_percentage, 1.0, abs_tol=1e-9):
        raise ValueError(
            f"cash_percentage + stock_percentage must equal 1.0, "
            f"got {cash_percentage + stock_percentage}"
        )

    cash_consideration = purchase_equity_value * cash_percentage
    stock_consideration = purchase_equity_value * stock_percentage

    new_debt = cash_consideration
    pre_tax_interest = new_debt * interest_rate
    after_tax_interest = pre_tax_interest * (1 - tax_rate)
    new_shares_issued = stock_consideration / acquirer_share_price

    standalone_eps = acquirer_net_income / acquirer_shares
    pro_forma_net_income = (
        acquirer_net_income + target_net_income - after_tax_interest + annual_after_tax_synergies
    )
    pro_forma_shares = acquirer_shares + new_shares_issued
    pro_forma_eps = pro_forma_net_income / pro_forma_shares

    eps_change = pro_forma_eps - standalone_eps
    if standalone_eps == 0:
        eps_change_pct = None
    else:
        eps_change_pct = eps_change / abs(standalone_eps)

    if math.isclose(eps_change, 0.0, abs_tol=1e-12):
        result = "Neutral"
    elif eps_change > 0:
        result = "Accretive"
    else:
        result = "Dilutive"

    return {
        "purchase_equity_value": purchase_equity_value,
        "cash_consideration": cash_consideration,
        "stock_consideration": stock_consideration,
        "new_debt": new_debt,
        "pre_tax_interest_expense": pre_tax_interest,
        "after_tax_interest_expense": after_tax_interest,
        "new_shares_issued": new_shares_issued,
        "annual_after_tax_synergies": annual_after_tax_synergies,
        "standalone_net_income": acquirer_net_income,
        "standalone_shares": acquirer_shares,
        "standalone_eps": standalone_eps,
        "pro_forma_net_income": pro_forma_net_income,
        "pro_forma_shares": pro_forma_shares,
        "pro_forma_eps": pro_forma_eps,
        "eps_change": eps_change,
        "eps_change_pct": eps_change_pct,
        "result": result,
    }


# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------

def valuation_summary(
    comparable_ev: float, precedent_ev: float, dcf_ev: float
) -> dict[str, Any]:
    """Summarise the enterprise value range implied by the three methods.

    All inputs and outputs are enterprise values, not equity values.
    """
    by_method = {
        "Comparable Companies": _require_positive("comparable_ev", comparable_ev),
        "Precedent Transactions": _require_positive("precedent_ev", precedent_ev),
        "Discounted Cash Flow": _require_positive("dcf_ev", dcf_ev),
    }
    values = list(by_method.values())
    low = min(values)
    high = max(values)
    return {
        "basis": "enterprise_value",
        "by_method": by_method,
        "low": low,
        "high": high,
        "low_method": min(by_method, key=by_method.get),
        "high_method": max(by_method, key=by_method.get),
        "midpoint": (low + high) / 2,
        "average": sum(values) / len(values),
    }


# ---------------------------------------------------------------------------
# Deterministic relationship labels
# ---------------------------------------------------------------------------

def classify_against_range(value: float, low: float, high: float) -> str:
    """Return "below", "within", or "above" for `value` relative to [low, high].

    Bounds are inclusive. This lets agents describe positioning from a label
    instead of comparing numbers themselves.
    """
    value = _require_finite("value", value)
    low = _require_finite("low", low)
    high = _require_finite("high", high)
    if low > high:
        raise ValueError(f"low ({low}) must be <= high ({high})")
    if value < low:
        return "below"
    if value > high:
        return "above"
    return "within"


def compare_to_reference(value: float, reference: float) -> str:
    """Return "above", "equal", or "below" for `value` relative to `reference`."""
    value = _require_finite("value", value)
    reference = _require_finite("reference", reference)
    if math.isclose(value, reference, rel_tol=1e-12, abs_tol=1e-12):
        return "equal"
    return "above" if value > reference else "below"


_SIGN_LABELS = {"above": "positive", "equal": "zero", "below": "negative"}


def classify_sign(value: float) -> str:
    """Return "positive", "zero", or "negative" for `value` (via compare_to_reference)."""
    return _SIGN_LABELS[compare_to_reference(value, 0.0)]


# ---------------------------------------------------------------------------
# Pro forma ownership
# ---------------------------------------------------------------------------

def pro_forma_ownership(new_shares_issued: float, pro_forma_shares: float) -> dict[str, float]:
    """Ownership split of the pro forma share count, as decimals.

    Takes the share figures produced by accretion_dilution so share issuance is not
    recomputed. Seller ownership = new shares issued / pro forma shares.
    """
    new_shares_issued = _require_non_negative("new_shares_issued", new_shares_issued)
    pro_forma_shares = _require_positive("pro_forma_shares", pro_forma_shares)
    if new_shares_issued > pro_forma_shares:
        raise ValueError(
            f"new_shares_issued ({new_shares_issued}) cannot exceed "
            f"pro_forma_shares ({pro_forma_shares})"
        )
    seller_ownership = new_shares_issued / pro_forma_shares
    return {
        "new_shares_issued": new_shares_issued,
        "pro_forma_shares": pro_forma_shares,
        "seller_ownership_of_pro_forma_shares": seller_ownership,
        "existing_acquirer_holders_ownership_of_pro_forma_shares": 1 - seller_ownership,
    }


# ---------------------------------------------------------------------------
# Method applicability, EV/Revenue multiples, and the applicable-method summary
# (added for loss-making targets; the formulas above are unchanged)
# ---------------------------------------------------------------------------

NON_POSITIVE_EBITDA_REASON = "Target EBITDA is non-positive; EV/EBITDA is not meaningful."
SUPPLEMENTARY_REASON = ("Supplementary: target EBITDA is positive, so EV/EBITDA is the primary "
                        "multiple basis; not included in the valuation range.")
TOO_FEW_METHODS_NOTE = ("Fewer than two applicable valuation methods; a valuation range cannot "
                        "be formed.")


def _not_applicable(method: str, basis: str, reason: str, **inputs: Any) -> dict[str, Any]:
    return {"method": method, **inputs, "enterprise_value": None, "basis": basis,
            "applicable": False, "reason": reason}


def _ebitda_method(method: str, calculate, ebitda: float, multiple: float | None) -> dict[str, Any]:
    ebitda = _require_finite("ebitda", ebitda)
    if ebitda <= 0:  # never produce a negative or zero EV from an EBITDA multiple
        return _not_applicable(method, "EV/EBITDA", NON_POSITIVE_EBITDA_REASON,
                               ebitda=ebitda, multiple=multiple)
    if multiple is None:
        return _not_applicable(method, "EV/EBITDA", "No EV/EBITDA multiple was supplied.",
                               ebitda=ebitda, multiple=None)
    return {**calculate(ebitda, multiple), "basis": "EV/EBITDA", "applicable": True, "reason": None}


def comparable_ebitda_method(ebitda: float, ev_ebitda_multiple: float | None) -> dict[str, Any]:
    """comparable_company_valuation when meaningful; otherwise a not-applicable record."""
    return _ebitda_method("Comparable Companies", comparable_company_valuation, ebitda,
                          ev_ebitda_multiple)


def precedent_ebitda_method(ebitda: float, ev_ebitda_multiple: float | None) -> dict[str, Any]:
    """precedent_transaction_valuation when meaningful; otherwise a not-applicable record."""
    return _ebitda_method("Precedent Transactions", precedent_transaction_valuation, ebitda,
                          ev_ebitda_multiple)


def _revenue_multiple_valuation(method: str, revenue: float, multiple: float) -> dict[str, Any]:
    revenue = _require_positive("revenue", revenue)
    multiple = _require_positive("ev_revenue_multiple", multiple)
    return {"method": method, "revenue": revenue, "multiple": multiple,
            "enterprise_value": revenue * multiple, "basis": "EV/Revenue", "applicable": True,
            "reason": None}


def comparable_company_revenue_valuation(revenue: float, ev_revenue_multiple: float) -> dict[str, Any]:
    """Enterprise value implied by trading comparables: EV = revenue x peer EV/Revenue."""
    return _revenue_multiple_valuation("Comparable Companies (EV/Revenue)", revenue,
                                       ev_revenue_multiple)


def precedent_transaction_revenue_valuation(revenue: float, ev_revenue_multiple: float) -> dict[str, Any]:
    """Enterprise value implied by precedent deals: EV = revenue x transaction EV/Revenue."""
    return _revenue_multiple_valuation("Precedent Transactions (EV/Revenue)", revenue,
                                       ev_revenue_multiple)


def supplied_revenue_methods(revenue: float, comparable_multiple: float | None,
                             precedent_multiple: float | None) -> dict[str, dict[str, Any]]:
    """EV/Revenue results for the multiples that were supplied, keyed by context name."""
    methods = {}
    if comparable_multiple is not None:
        methods["comparable_company_revenue"] = comparable_company_revenue_valuation(
            revenue, comparable_multiple)
    if precedent_multiple is not None:
        methods["precedent_transaction_revenue"] = precedent_transaction_revenue_valuation(
            revenue, precedent_multiple)
    return methods


def select_valuation_methods(target_ebitda: float, ebitda_methods: list[dict[str, Any]],
                             revenue_methods: list[dict[str, Any]],
                             dcf: dict[str, Any]) -> list[dict[str, Any]]:
    """Which methods form the valuation range.

    Positive target EBITDA: applicable EV/EBITDA methods + DCF (EV/Revenue methods, if
    supplied, are shown as supplementary). Non-positive EBITDA: EV/Revenue methods + DCF
    (EV/EBITDA methods are recorded as not applicable, with the reason).
    """
    positive = _require_finite("target_ebitda", target_ebitda) > 0

    def record(m: dict[str, Any], included: bool, reason: str | None) -> dict[str, Any]:
        return {"method": m["method"], "basis": m["basis"], "applicable": m["applicable"],
                "included_in_range": included, "enterprise_value": m["enterprise_value"],
                "reason": reason}

    records = [record(m, m["applicable"], m["reason"]) for m in ebitda_methods]
    records += [record(m, not positive, None if not positive else SUPPLEMENTARY_REASON)
                for m in revenue_methods]
    records.append({"method": "Discounted Cash Flow", "basis": "DCF", "applicable": True,
                    "included_in_range": True, "enterprise_value": dcf["enterprise_value"],
                    "reason": None})
    return records


def summarize_valuation(methods: list[dict[str, Any]]) -> dict[str, Any]:
    """Valuation range from applicable, included methods only (same formulas as
    valuation_summary). With fewer than two, no range is fabricated."""
    included = [m for m in methods if m["applicable"] and m["included_in_range"]]
    by_method = {m["method"]: _require_positive(m["method"], m["enterprise_value"])
                 for m in included}
    base = {"basis": "enterprise_value", "by_method": by_method}
    if len(by_method) < 2:
        return {**base, "low": None, "high": None, "low_method": None, "high_method": None,
                "midpoint": None, "average": None, "methods": methods,
                "range_available": False, "range_note": TOO_FEW_METHODS_NOTE}
    values = list(by_method.values())
    low, high = min(values), max(values)
    return {**base, "low": low, "high": high,
            "low_method": min(by_method, key=by_method.get),
            "high_method": max(by_method, key=by_method.get),
            "midpoint": (low + high) / 2, "average": sum(values) / len(values),
            "methods": methods, "range_available": True, "range_note": None}


def offer_positioning(offer_ev: float, summary: dict[str, Any]) -> dict[str, Any]:
    """Offer vs range and vs each method in the range, as deterministic labels."""
    vs_range = (classify_against_range(offer_ev, summary["low"], summary["high"])
                if summary["range_available"] else "not available")
    return {"offer_vs_range": vs_range,
            "offer_vs_method": {m: compare_to_reference(offer_ev, ev)
                                for m, ev in summary["by_method"].items()}}


def valuation_basis(target_ebitda: float, revenue_multiples_supplied: bool) -> dict[str, Any]:
    """States which multiple basis applies and why (for agents and the UI)."""
    status = classify_sign(target_ebitda)
    if status == "positive":
        return {"target_ebitda": target_ebitda, "target_ebitda_status": status,
                "primary_multiple_basis": "EV/EBITDA",
                "note": "Target EBITDA is positive; EV/EBITDA comparable and precedent methods "
                        "are the primary multiple basis."}
    used = ("EV/Revenue multiples are used instead" if revenue_multiples_supplied
            else "no EV/Revenue multiples were supplied")
    return {"target_ebitda": target_ebitda, "target_ebitda_status": status,
            "primary_multiple_basis": "EV/Revenue",
            "note": f"Target EBITDA is {status}, so EV/EBITDA methods are not applicable; "
                    f"{used}. This is a deterministic method-applicability result, not missing "
                    "evidence or a calculation failure."}
