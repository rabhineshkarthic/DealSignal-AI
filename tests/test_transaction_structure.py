"""Tests for the deterministic transaction-structure bridge. No LLM involved."""

from pathlib import Path

import pytest

import deal_analysis
import valuation
from deal_analysis import build_synergy_context, build_transaction_structure_context
from models import load_deal
from valuation import pro_forma_ownership

ROOT = Path(__file__).resolve().parent.parent
SAMPLE_DEAL_PATH = ROOT / "data" / "sample_deal.json"
approx = pytest.approx


@pytest.fixture(scope="module")
def deal():
    return load_deal(SAMPLE_DEAL_PATH)


@pytest.fixture(scope="module")
def ctx(deal):
    return build_transaction_structure_context(deal, ndigits=None)


# --- Sample-deal values (hand-checked) ----------------------------------------------

def test_consideration(ctx):
    calc = ctx["calculation"]
    assert calc["target_net_debt"] == approx(20.0)
    assert calc["offer_equity_value"] == approx(185.0)
    assert calc["cash_consideration"] == approx(111.0)    # 185.0 x 0.6
    assert calc["stock_consideration"] == approx(74.0)    # 185.0 x 0.4


def test_financing_convention_values(ctx):
    calc = ctx["calculation"]
    assert calc["new_debt_under_financing_convention"] == approx(111.0)
    assert calc["annual_pre_tax_interest_expense"] == approx(6.105)     # 111.0 x 0.055
    assert calc["annual_after_tax_interest_expense"] == approx(4.57875)  # x (1 - 0.25)


def test_share_issuance_and_ownership(ctx):
    calc = ctx["calculation"]
    assert calc["new_shares_issued"] == approx(2.96)                   # 74.0 / 25.0
    assert calc["pro_forma_shares_outstanding"] == approx(102.96)
    assert calc["seller_ownership_of_pro_forma_shares"] == approx(2.96 / 102.96)
    assert calc["existing_acquirer_holders_ownership_of_pro_forma_shares"] == approx(100.0 / 102.96)


def test_accretion_dilution_cases(ctx):
    ex = ctx["calculation"]["accretion_dilution_excluding_synergies"]
    inc = ctx["calculation"]["accretion_dilution_including_run_rate_synergies"]
    assert ex["standalone_eps"] == approx(0.72)
    assert ex["annual_after_tax_synergies"] == 0.0
    assert ex["pro_forma_eps"] == approx((72.0 + 8.0 - 4.57875) / 102.96)
    assert inc["annual_after_tax_synergies"] == approx(6.75)
    assert inc["pro_forma_eps"] == approx((72.0 + 8.0 - 4.57875 + 6.75) / 102.96)
    assert ctx["labels"] == {
        "eps_impact_excluding_synergies": "Accretive",
        "eps_impact_including_run_rate_synergies": "Accretive",
        "cash_percentage_vs_stock_percentage": "above",
    }


def test_transaction_terms_mirror_deal_data(deal, ctx):
    terms = ctx["transaction_terms"]
    t = deal.transaction_assumptions
    assert terms["offer_enterprise_value"] == t.offer_enterprise_value
    assert terms["cash_percentage"] == t.cash_percentage
    assert terms["stock_percentage"] == t.stock_percentage
    assert terms["new_debt_interest_rate"] == t.new_debt_interest_rate
    assert terms["tax_rate"] == t.tax_rate
    assert terms["acquirer_share_price"] == deal.acquirer.share_price
    assert terms["acquirer_shares_outstanding"] == deal.acquirer.shares_outstanding


def test_display_rounding_applied_after_labels(deal):
    rounded = build_transaction_structure_context(deal)
    assert rounded["calculation"]["annual_after_tax_interest_expense"] == 4.5788
    assert rounded["calculation"]["seller_ownership_of_pro_forma_shares"] == 0.0287
    assert rounded["labels"]["eps_impact_excluding_synergies"] == "Accretive"


# --- Explicit modelling assumptions -------------------------------------------------

def test_financing_convention_is_explicit(ctx):
    notes = " ".join(ctx["calculation_notes"]["modelling_conventions"])
    assert "FINANCING CONVENTION (existing model, not a confirmed financing plan)" in notes
    assert "funded entirely with new acquisition debt" in notes
    assert "tax-deductible" in notes
    assert "issuing new acquirer shares at the supplied acquirer share price" in notes
    assert "full run-rate annual after-tax synergies" in notes
    for not_modelled in ["transaction and financing fees", "debt capacity", "covenants",
                         "credit rating"]:
        assert not_modelled in notes


def test_conventions_contain_no_numerals(ctx):
    """Convention text is approved-source content, so it must not smuggle in numbers."""
    import re
    for line in ctx["calculation_notes"]["modelling_conventions"]:
        assert not re.search(r"\d", line), line


# --- Reuse of existing deterministic functions ----------------------------------------

def test_reuses_accretion_dilution_for_both_cases(deal, monkeypatch):
    calls = []
    original = deal_analysis.accretion_dilution

    def spy(**kwargs):
        calls.append(kwargs)
        return original(**kwargs)

    monkeypatch.setattr(deal_analysis, "accretion_dilution", spy)
    build_transaction_structure_context(deal)
    assert [c["annual_after_tax_synergies"] for c in calls] == [
        0.0, build_synergy_context(deal, ndigits=None)["calculation"]["annual_after_tax_synergies"]]
    assert all(c["interest_rate"] == deal.transaction_assumptions.new_debt_interest_rate for c in calls)


def test_outputs_come_from_accretion_dilution_not_local_math(deal, monkeypatch):
    real = valuation.accretion_dilution

    def fake(**kwargs):
        out = real(**kwargs)
        out.update(cash_consideration=999.0, new_shares_issued=7.0, pro_forma_shares=70.0)
        return out

    monkeypatch.setattr(deal_analysis, "accretion_dilution", fake)
    ctx = build_transaction_structure_context(deal, ndigits=None)
    assert ctx["calculation"]["cash_consideration"] == 999.0
    assert ctx["calculation"]["new_shares_issued"] == 7.0
    assert ctx["calculation"]["seller_ownership_of_pro_forma_shares"] == approx(0.1)


def test_calls_expected_valuation_functions(deal, monkeypatch):
    called = []
    for name in ["enterprise_to_equity_value", "accretion_dilution", "pro_forma_ownership",
                 "calculate_synergy_value", "compare_to_reference"]:
        original = getattr(deal_analysis, name)

        def spy(*args, _name=name, _original=original, **kwargs):
            called.append(_name)
            return _original(*args, **kwargs)

        monkeypatch.setattr(deal_analysis, name, spy)
    build_transaction_structure_context(deal)
    assert {"enterprise_to_equity_value", "accretion_dilution", "pro_forma_ownership",
            "calculate_synergy_value", "compare_to_reference"} <= set(called)


# --- pro_forma_ownership ---------------------------------------------------------------

def test_pro_forma_ownership():
    out = pro_forma_ownership(new_shares_issued=25.0, pro_forma_shares=125.0)
    assert out["seller_ownership_of_pro_forma_shares"] == approx(0.2)
    assert out["existing_acquirer_holders_ownership_of_pro_forma_shares"] == approx(0.8)


def test_pro_forma_ownership_all_cash_deal():
    out = pro_forma_ownership(0.0, 100.0)
    assert out["seller_ownership_of_pro_forma_shares"] == 0.0


@pytest.mark.parametrize("new, total", [(-1.0, 100.0), (10.0, 0.0), (150.0, 100.0)])
def test_pro_forma_ownership_rejects_invalid(new, total):
    with pytest.raises(ValueError):
        pro_forma_ownership(new, total)
