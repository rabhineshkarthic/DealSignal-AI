"""Tests for the deterministic bridge from DEAL DATA to valuation.py results."""

import ast
import math
from pathlib import Path

import pytest

import deal_analysis
import valuation
from deal_analysis import build_synergy_context, build_valuation_context
from models import load_deal

ROOT = Path(__file__).resolve().parent.parent
SAMPLE_DEAL_PATH = ROOT / "data" / "sample_deal.json"
approx = pytest.approx

VALUATION_FUNCTIONS = [
    "calculate_net_debt",
    "comparable_ebitda_method",
    "dcf_valuation",
    "enterprise_to_equity_value",
    "offer_positioning",
    "precedent_ebitda_method",
    "select_valuation_methods",
    "summarize_valuation",
    "supplied_revenue_methods",
    "valuation_basis",
]


@pytest.fixture(scope="module")
def deal():
    return load_deal(SAMPLE_DEAL_PATH)


@pytest.fixture(scope="module")
def ctx(deal):
    return build_valuation_context(deal)


# --- Values on the sample deal ------------------------------------------------

def test_sample_values(ctx):
    assert ctx["target_net_debt"] == approx(20.0)
    assert ctx["comparable_company"]["enterprise_value"] == approx(189.0)
    assert ctx["precedent_transaction"]["enterprise_value"] == approx(216.0)
    assert ctx["dcf"]["enterprise_value"] == approx(172.024, abs=1e-3)
    assert ctx["transaction"]["offer_enterprise_value"] == approx(205.0)
    assert ctx["transaction"]["offer_equity_value"] == approx(185.0)
    assert ctx["valuation_summary"]["low"] == approx(172.024, abs=1e-3)
    assert ctx["valuation_summary"]["high"] == approx(216.0)


def test_relationship_labels(ctx):
    assert ctx["transaction"]["offer_vs_range"] == "within"
    assert ctx["valuation_summary"]["low_method"] == "Discounted Cash Flow"
    assert ctx["valuation_summary"]["high_method"] == "Precedent Transactions"
    assert ctx["transaction"]["offer_vs_method"] == {
        "Comparable Companies": "above",
        "Precedent Transactions": "below",
        "Discounted Cash Flow": "above",
    }


def test_structure(ctx):
    assert set(ctx) == {
        "calculation_notes", "valuation_basis", "target_net_debt", "comparable_company",
        "precedent_transaction", "dcf", "valuation_summary", "transaction",
    }  # no EV/Revenue methods: the sample supplies no EV/Revenue multiples
    assert ctx["calculation_notes"]["currency"] == "USD"
    assert ctx["calculation_notes"]["units"] == "millions"
    assert "ENTERPRISE" in ctx["calculation_notes"]["value_basis"]


def test_display_rounding(deal):
    full = build_valuation_context(deal, ndigits=None)
    rounded = build_valuation_context(deal, ndigits=4)
    assert rounded["dcf"]["enterprise_value"] == round(full["dcf"]["enterprise_value"], 4)
    assert rounded["dcf"]["forecast_fcfs"] == [round(v, 4) for v in full["dcf"]["forecast_fcfs"]]
    # rates survive rounding intact
    assert rounded["dcf"]["terminal_growth_rate"] == 0.025


def test_outputs_match_direct_valuation_calls(deal):
    """Every result equals what valuation.py returns when called directly."""
    ctx = build_valuation_context(deal, ndigits=None)
    t, m, d = deal.target, deal.market_data, deal.dcf_assumptions
    assert ctx["comparable_company"] == valuation.comparable_ebitda_method(
        t.ebitda, m.comparable_ev_ebitda_multiple)
    # ...which is exactly the original EV/EBITDA formula result, plus status fields
    original = valuation.comparable_company_valuation(t.ebitda, m.comparable_ev_ebitda_multiple)
    assert {k: ctx["comparable_company"][k] for k in original} == original
    original = valuation.precedent_transaction_valuation(t.ebitda, m.precedent_ev_ebitda_multiple)
    assert {k: ctx["precedent_transaction"][k] for k in original} == original
    assert ctx["dcf"] == valuation.dcf_valuation(
        t.free_cash_flow, d.fcf_growth_rate, d.wacc, d.terminal_growth_rate, d.forecast_years)


# --- Uses valuation.py rather than duplicating it ----------------------------------

def test_calls_every_valuation_function(deal, monkeypatch):
    called = []
    for name in VALUATION_FUNCTIONS:
        original = getattr(deal_analysis, name)

        def spy(*args, _name=name, _original=original, **kwargs):
            called.append(_name)
            return _original(*args, **kwargs)

        monkeypatch.setattr(deal_analysis, name, spy)

    build_valuation_context(deal)
    assert set(called) == set(VALUATION_FUNCTIONS)


def test_results_come_from_valuation_functions_not_local_math(deal, monkeypatch):
    """If valuation.py returned a different number, the context would reflect it."""
    def fake_comps(ebitda, multiple):
        return {"method": "Comparable Companies", "ebitda": ebitda, "multiple": multiple,
                "enterprise_value": 999.0, "basis": "EV/EBITDA", "applicable": True,
                "reason": None}

    monkeypatch.setattr(deal_analysis, "comparable_ebitda_method", fake_comps)
    ctx = build_valuation_context(deal)
    assert ctx["comparable_company"]["enterprise_value"] == 999.0
    assert ctx["valuation_summary"]["high"] == 999.0
    assert ctx["valuation_summary"]["high_method"] == "Comparable Companies"


def test_deal_analysis_contains_no_arithmetic():
    """No +, -, *, /, **, % operators or numeric comparisons in deal_analysis.py."""
    tree = ast.parse((ROOT / "deal_analysis.py").read_text())
    # `|` (BitOr) is excluded: it only appears in type hints such as `int | None`.
    arithmetic = [n for n in ast.walk(tree) if isinstance(n, (ast.BinOp, ast.AugAssign, ast.UnaryOp))
                  and not isinstance(getattr(n, "op", None), (ast.Not, ast.BitOr))]
    numeric_compares = [
        n for n in ast.walk(tree) if isinstance(n, ast.Compare)
        and any(isinstance(op, (ast.Lt, ast.LtE, ast.Gt, ast.GtE)) for op in n.ops)
    ]
    assert arithmetic == [], [ast.unparse(n) for n in arithmetic]
    assert numeric_compares == [], [ast.unparse(n) for n in numeric_compares]


def test_invalid_deal_assumptions_are_rejected(deal):
    from dataclasses import replace

    bad = replace(deal, dcf_assumptions=replace(deal.dcf_assumptions, terminal_growth_rate=0.10))
    with pytest.raises(ValueError, match="must exceed terminal_growth_rate"):
        build_valuation_context(bad)


# --- Synergy bridge -----------------------------------------------------------


def asdict_synergy(deal):
    from dataclasses import asdict
    return asdict(deal.synergy_assumptions)


@pytest.fixture(scope="module")
def syn(deal):
    return build_synergy_context(deal)


def test_synergy_sample_values(syn):
    calc = syn["calculation"]
    assert calc["annual_pre_tax_synergies"] == approx(9.0)
    assert calc["annual_after_tax_synergies"] == approx(6.75)
    assert calc["pv_synergies"] == approx(25.588, abs=1e-3)
    assert calc["integration_cost"] == approx(12.0)
    assert calc["net_synergy_value"] == approx(13.588, abs=1e-3)
    assert len(calc["discounted_synergies"]) == 5


def test_synergy_labels(syn):
    assert syn["labels"] == {
        "net_synergy_value_status": "positive",
        "integration_cost_vs_pv_synergies": "below",
    }


def test_synergy_structure_and_assumptions_mirror_deal_data(deal, syn):
    from dataclasses import asdict

    assert set(syn) == {"calculation_notes", "assumptions", "calculation", "labels"}
    assert syn["assumptions"] == asdict(deal.synergy_assumptions)
    assert set(syn["calculation"]) == {
        "annual_cost_synergy", "annual_revenue_synergy_ebitda", "annual_pre_tax_synergies",
        "annual_after_tax_synergies", "tax_rate", "discount_rate", "realization_years",
        "discounted_synergies", "pv_synergies", "integration_cost", "net_synergy_value",
    }


def test_synergy_calculation_equals_direct_valuation_call(deal):
    ctx = build_synergy_context(deal, ndigits=None)
    assert ctx["calculation"] == valuation.calculate_synergy_value(**asdict_synergy(deal))


def test_build_synergy_context_calls_calculate_synergy_value(deal, monkeypatch):
    calls = []
    original = deal_analysis.calculate_synergy_value

    def spy(**kwargs):
        calls.append(kwargs)
        return original(**kwargs)

    monkeypatch.setattr(deal_analysis, "calculate_synergy_value", spy)
    build_synergy_context(deal)
    assert calls == [asdict_synergy(deal)]


def test_synergy_labels_come_from_python_outputs(deal, monkeypatch):
    """If valuation.py reported a loss, the labels would follow it."""
    def fake(**kwargs):
        return {"integration_cost": 12.0, "pv_synergies": 5.0, "net_synergy_value": -7.0}

    monkeypatch.setattr(deal_analysis, "calculate_synergy_value", fake)
    ctx = build_synergy_context(deal)
    assert ctx["calculation"]["net_synergy_value"] == -7.0
    assert ctx["labels"] == {
        "net_synergy_value_status": "negative",
        "integration_cost_vs_pv_synergies": "above",
    }


def test_invalid_synergy_assumptions_are_rejected(deal):
    from dataclasses import replace

    bad = replace(deal, synergy_assumptions=replace(deal.synergy_assumptions, tax_rate=1.5))
    with pytest.raises(ValueError, match="tax_rate"):
        build_synergy_context(bad)
