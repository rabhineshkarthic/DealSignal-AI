"""Bridge from a loaded Deal to deterministic valuation and synergy results for agents.

This module only wires DEAL DATA into valuation.py and arranges the outputs. It
contains no financial formulas: every figure is either read from the Deal or
returned by a valuation.py function. (A test enforces that no arithmetic
operators appear in this file.)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from models import Deal
from valuation import (
    accretion_dilution,
    calculate_net_debt,
    calculate_synergy_value,
    classify_sign,
    comparable_ebitda_method,
    compare_to_reference,
    dcf_valuation,
    enterprise_to_equity_value,
    offer_positioning,
    precedent_ebitda_method,
    pro_forma_ownership,
    select_valuation_methods,
    summarize_valuation,
    supplied_revenue_methods,
    valuation_basis,
)

DEFAULT_DISPLAY_DECIMALS = 4

DEFAULT_SHARE_BASIS = "Acquirer shares outstanding as supplied in the deal inputs"


@dataclass(frozen=True)
class InputLineage:
    """Metadata about the effective deal inputs (never changes a number).

    share_basis: definition of the acquirer share count the transaction model uses
      (Deal.acquirer.shares_outstanding), e.g. point-in-time shares outstanding or an
      approved weighted-average modelling proxy.
    data_quality_notes: deterministic warnings such as mixed balance-sheet vintages."""

    share_basis: str = DEFAULT_SHARE_BASIS
    data_quality_notes: tuple[str, ...] = ()


SHARE_BASIS_DEFINITION_KEY = "shares_outstanding_definition"


def annotate_deal_context(deal_context: dict[str, Any],
                          lineage: InputLineage | None = None) -> dict[str, Any]:
    """DEAL DATA for agents with the acquirer share-basis definition placed beside
    acquirer.shares_outstanding, so the count is never described with a generic label.
    Adds text only; no value is changed."""
    lineage = lineage or InputLineage()
    acquirer = {**deal_context["acquirer"], SHARE_BASIS_DEFINITION_KEY: (
        "acquirer.shares_outstanding is the acquirer share basis used in the transaction "
        f"model. Definition: {lineage.share_basis}.")}
    return {**deal_context, "acquirer": acquirer}


def share_basis_from_contexts(contexts: dict[str, dict[str, Any]]) -> str:
    """The share-basis definition recorded in deterministic contexts."""
    notes = contexts.get("transaction_structure", {}).get("calculation_notes", {})
    return notes.get("acquirer_share_basis_definition", DEFAULT_SHARE_BASIS)


LINEAGE_NOTE_KEYS = ("acquirer_share_basis", "acquirer_share_basis_definition", "data_quality_notes")


def strip_input_lineage(context: dict[str, Any]) -> dict[str, Any]:
    """A copy of one deterministic context without input-lineage notes, for comparing
    calculated content only."""
    notes = {k: v for k, v in context.get("calculation_notes", {}).items()
             if k not in LINEAGE_NOTE_KEYS}
    return {**context, "calculation_notes": notes} if "calculation_notes" in context else context


def lineage_from_contexts(contexts: dict[str, dict[str, Any]]) -> tuple[str | None, tuple[str, ...]]:
    """(share-basis note, data-quality notes) recorded in deterministic contexts."""
    tx = contexts.get("transaction_structure", {}).get("calculation_notes", {})
    return tx.get("acquirer_share_basis"), tuple(tx.get("data_quality_notes", ()))


def annotate_input_lineage(contexts: dict[str, dict[str, Any]],
                           lineage: InputLineage | None = None) -> dict[str, dict[str, Any]]:
    """Attach input-lineage notes to deterministic contexts. Only calculation_notes are
    added; every calculated value is left exactly as computed."""
    lineage = lineage or InputLineage()
    tx_notes = contexts["transaction_structure"]["calculation_notes"]
    tx_notes["acquirer_share_basis"] = (
        "transaction_terms.acquirer_shares_outstanding (DEAL DATA acquirer.shares_outstanding) "
        "is the acquirer share basis used for new shares issued, pro forma shares, seller "
        f"ownership and EPS. Definition: {lineage.share_basis}.")
    tx_notes["acquirer_share_basis_definition"] = lineage.share_basis
    if lineage.data_quality_notes:
        for name in ("valuation", "transaction_structure"):
            contexts[name]["calculation_notes"]["data_quality_notes"] = list(
                lineage.data_quality_notes)
    return contexts


def _round_floats(obj: Any, ndigits: int) -> Any:
    """Round every float in a nested structure, for readable prompts."""
    if isinstance(obj, float):
        return round(obj, ndigits)
    if isinstance(obj, dict):
        return {k: _round_floats(v, ndigits) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_round_floats(v, ndigits) for v in obj]
    return obj


def build_valuation_context(
    deal: Deal, ndigits: int | None = DEFAULT_DISPLAY_DECIMALS
) -> dict[str, Any]:
    """Run the valuation functions on a deal and return structured results.

    EV/EBITDA methods are recorded as not applicable when target EBITDA is non-positive;
    EV/Revenue methods are included when their multiples are supplied. The range uses
    only applicable, included methods (see valuation.select_valuation_methods). Labels
    are classified at full precision before display rounding. ndigits=None: no rounding.
    """
    target = deal.target
    market = deal.market_data
    dcf_inputs = deal.dcf_assumptions
    offer_ev = deal.transaction_assumptions.offer_enterprise_value

    net_debt = calculate_net_debt(target.debt, target.cash)
    comparable = comparable_ebitda_method(target.ebitda, market.comparable_ev_ebitda_multiple)
    precedent = precedent_ebitda_method(target.ebitda, market.precedent_ev_ebitda_multiple)
    revenue_methods = supplied_revenue_methods(
        target.revenue, market.comparable_ev_revenue_multiple,
        market.precedent_ev_revenue_multiple)
    dcf = dcf_valuation(
        base_fcf=target.free_cash_flow,
        growth_rate=dcf_inputs.fcf_growth_rate,
        wacc=dcf_inputs.wacc,
        terminal_growth_rate=dcf_inputs.terminal_growth_rate,
        forecast_years=dcf_inputs.forecast_years,
    )
    methods = select_valuation_methods(target.ebitda, [comparable, precedent],
                                       list(revenue_methods.values()), dcf)
    summary = summarize_valuation(methods)
    offer = enterprise_to_equity_value(offer_ev, target.debt, target.cash)

    context = {
        "calculation_notes": {
            "source": "valuation.py (deterministic Python)",
            "currency": deal.currency,
            "units": deal.units,
            "value_basis": (
                "All method values and the valuation range are ENTERPRISE values. "
                "Only transaction.offer_equity_value is an EQUITY value."
            ),
            "method_applicability": (
                "Methods marked applicable=false were deliberately not calculated (reason "
                "given). The valuation range uses only applicable, included methods."
            ),
            "display_rounding_decimals": ndigits,
        },
        "valuation_basis": valuation_basis(target.ebitda, bool(revenue_methods)),
        "target_net_debt": net_debt["net_debt"],
        "comparable_company": comparable,
        "precedent_transaction": precedent,
        **revenue_methods,
        "dcf": dcf,
        "valuation_summary": summary,
        "transaction": {
            "offer_enterprise_value": offer["enterprise_value"],
            "target_net_debt": offer["net_debt"],
            "offer_equity_value": offer["equity_value"],
            **offer_positioning(offer_ev, summary),
        },
    }
    return context if ndigits is None else _round_floats(context, ndigits)


def build_synergy_context(
    deal: Deal, ndigits: int | None = DEFAULT_DISPLAY_DECIMALS
) -> dict[str, Any]:
    """Run valuation.calculate_synergy_value on the deal's synergy assumptions.

    Relationship labels are classified at full precision before any display rounding.
    Pass ndigits=None to skip rounding.
    """
    s = deal.synergy_assumptions
    calculation = calculate_synergy_value(
        annual_cost_synergy=s.annual_cost_synergy,
        annual_revenue_synergy_ebitda=s.annual_revenue_synergy_ebitda,
        tax_rate=s.tax_rate,
        discount_rate=s.discount_rate,
        realization_years=s.realization_years,
        integration_cost=s.integration_cost,
    )

    context = {
        "calculation_notes": {
            "source": "valuation.py calculate_synergy_value (deterministic Python)",
            "currency": deal.currency,
            "units": deal.units,
            "modelling_conventions": [
                "Run-rate synergies are counted in full from year 1 (no ramp-up).",
                "Synergies last only for the realization period; no terminal value.",
                "Synergies are taxed at the supplied tax rate and discounted at year end.",
                "Integration cost is a one-off cost at close, undiscounted and not "
                "tax-deductible.",
            ],
            "display_rounding_decimals": ndigits,
        },
        "assumptions": {
            "annual_cost_synergy": s.annual_cost_synergy,
            "annual_revenue_synergy_ebitda": s.annual_revenue_synergy_ebitda,
            "tax_rate": s.tax_rate,
            "discount_rate": s.discount_rate,
            "realization_years": s.realization_years,
            "integration_cost": s.integration_cost,
        },
        "calculation": calculation,
        "labels": {
            "net_synergy_value_status": classify_sign(calculation["net_synergy_value"]),
            "integration_cost_vs_pv_synergies": compare_to_reference(
                calculation["integration_cost"], calculation["pv_synergies"]
            ),
        },
    }
    return context if ndigits is None else _round_floats(context, ndigits)


def build_transaction_structure_context(
    deal: Deal, ndigits: int | None = DEFAULT_DISPLAY_DECIMALS
) -> dict[str, Any]:
    """Organize transaction mechanics from existing valuation.py functions.

    Financing convention (inherited from valuation.accretion_dilution, not new): the
    cash portion of the equity purchase price is funded entirely with new debt at the
    supplied new-debt interest rate. Every convention is stated in the output.
    Relationship labels are classified at full precision before display rounding.
    """
    acquirer = deal.acquirer
    target = deal.target
    terms = deal.transaction_assumptions

    offer = enterprise_to_equity_value(terms.offer_enterprise_value, target.debt, target.cash)
    synergy = build_synergy_context(deal, ndigits=None)["calculation"]

    def run_accretion(annual_after_tax_synergies: float) -> dict[str, Any]:
        return accretion_dilution(
            acquirer_net_income=acquirer.net_income,
            acquirer_shares=acquirer.shares_outstanding,
            acquirer_share_price=acquirer.share_price,
            target_net_income=target.net_income,
            purchase_equity_value=offer["equity_value"],
            cash_percentage=terms.cash_percentage,
            stock_percentage=terms.stock_percentage,
            interest_rate=terms.new_debt_interest_rate,
            tax_rate=terms.tax_rate,
            annual_after_tax_synergies=annual_after_tax_synergies,
        )

    excluding = run_accretion(0.0)
    including = run_accretion(synergy["annual_after_tax_synergies"])
    ownership = pro_forma_ownership(excluding["new_shares_issued"], excluding["pro_forma_shares"])

    context = {
        "calculation_notes": {
            "source": (
                "valuation.py enterprise_to_equity_value, accretion_dilution, "
                "pro_forma_ownership, calculate_synergy_value (deterministic Python)"
            ),
            "currency": deal.currency,
            "units": deal.units,
            "share_units": "shares are in the same units as acquirer shares_outstanding",
            "modelling_conventions": [
                "Offer equity value is the offer enterprise value less target net debt.",
                "FINANCING CONVENTION (existing model, not a confirmed financing plan): the "
                "cash portion of the equity purchase price is funded entirely with new "
                "acquisition debt at the supplied new-debt interest rate. No other "
                "financing source, cash on hand, or debt repayment is modelled.",
                "Interest on the new debt is tax-deductible at the supplied transaction "
                "tax rate.",
                "The stock portion is paid by issuing new acquirer shares at the supplied "
                "acquirer share price, with no issuance discount or fees.",
                "The target's existing net debt is treated as assumed or refinanced at its "
                "current cost, already reflected in target net income.",
                "Accretion/dilution is a single-year, run-rate EPS view: acquirer net "
                "income plus target net income less after-tax new-debt interest, plus "
                "annual after-tax synergies in the including-synergies case only.",
                "The including-synergies case uses full run-rate annual after-tax "
                "synergies from calculate_synergy_value (no ramp-up, no integration cost).",
                "Not modelled: transaction and financing fees, purchase price allocation, "
                "intangible amortization, working-capital or purchase-price adjustments, "
                "debt capacity, covenants, or credit rating effects.",
                "Ownership values are decimals of pro forma shares outstanding.",
            ],
            "display_rounding_decimals": ndigits,
        },
        "transaction_terms": {
            "offer_enterprise_value": terms.offer_enterprise_value,
            "cash_percentage": terms.cash_percentage,
            "stock_percentage": terms.stock_percentage,
            "new_debt_interest_rate": terms.new_debt_interest_rate,
            "tax_rate": terms.tax_rate,
            "acquirer_share_price": acquirer.share_price,
            "acquirer_shares_outstanding": acquirer.shares_outstanding,
            "acquirer_net_income": acquirer.net_income,
            "target_net_income": target.net_income,
        },
        "calculation": {
            "target_net_debt": offer["net_debt"],
            "offer_equity_value": offer["equity_value"],
            "cash_consideration": excluding["cash_consideration"],
            "stock_consideration": excluding["stock_consideration"],
            "new_debt_under_financing_convention": excluding["new_debt"],
            "annual_pre_tax_interest_expense": excluding["pre_tax_interest_expense"],
            "annual_after_tax_interest_expense": excluding["after_tax_interest_expense"],
            "new_shares_issued": excluding["new_shares_issued"],
            "pro_forma_shares_outstanding": excluding["pro_forma_shares"],
            "seller_ownership_of_pro_forma_shares": ownership[
                "seller_ownership_of_pro_forma_shares"
            ],
            "existing_acquirer_holders_ownership_of_pro_forma_shares": ownership[
                "existing_acquirer_holders_ownership_of_pro_forma_shares"
            ],
            "accretion_dilution_excluding_synergies": excluding,
            "accretion_dilution_including_run_rate_synergies": including,
        },
        "labels": {
            "eps_impact_excluding_synergies": excluding["result"],
            "eps_impact_including_run_rate_synergies": including["result"],
            "cash_percentage_vs_stock_percentage": compare_to_reference(
                terms.cash_percentage, terms.stock_percentage
            ),
        },
    }
    return context if ndigits is None else _round_floats(context, ndigits)
