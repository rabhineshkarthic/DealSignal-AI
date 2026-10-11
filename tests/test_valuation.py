"""Tests for the deterministic valuation engine, using the fictional sample deal."""

from pathlib import Path

import pytest

from models import Deal, load_deal
from valuation import (
    accretion_dilution,
    calculate_net_debt,
    calculate_synergy_value,
    comparable_company_valuation,
    dcf_valuation,
    enterprise_to_equity_value,
    precedent_transaction_valuation,
    valuation_summary,
)

SAMPLE_DEAL_PATH = Path(__file__).resolve().parent.parent / "data" / "sample_deal.json"
approx = pytest.approx


@pytest.fixture(scope="module")
def deal() -> Deal:
    return load_deal(SAMPLE_DEAL_PATH)


@pytest.fixture(scope="module")
def dcf(deal):
    a = deal.dcf_assumptions
    return dcf_valuation(
        base_fcf=deal.target.free_cash_flow,
        growth_rate=a.fcf_growth_rate,
        wacc=a.wacc,
        terminal_growth_rate=a.terminal_growth_rate,
        forecast_years=a.forecast_years,
    )


# --- Sample deal loading ----------------------------------------------------

def test_sample_deal_loads(deal):
    assert deal.deal_name == "Northstar Software acquiring Atlas Analytics"
    assert deal.currency == "USD"
    assert deal.units == "millions"
    assert deal.target.ebitda == 18.0


def test_load_deal_rejects_missing_field(tmp_path):
    import json

    data = json.loads(SAMPLE_DEAL_PATH.read_text())
    del data["target"]["ebitda"]
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="missing fields"):
        load_deal(bad)


# --- Net debt and EV -> equity bridge ---------------------------------------

def test_target_net_debt(deal):
    result = calculate_net_debt(deal.target.debt, deal.target.cash)
    assert result["net_debt"] == approx(20.0)


def test_net_debt_can_be_negative():
    assert calculate_net_debt(debt=10.0, cash=30.0)["net_debt"] == approx(-20.0)


def test_net_debt_rejects_negative_inputs():
    with pytest.raises(ValueError):
        calculate_net_debt(debt=-1.0, cash=5.0)
    with pytest.raises(ValueError):
        calculate_net_debt(debt=1.0, cash=-5.0)


def test_offer_equity_purchase_value(deal):
    result = enterprise_to_equity_value(
        deal.transaction_assumptions.offer_enterprise_value,
        deal.target.debt,
        deal.target.cash,
    )
    assert result["enterprise_value"] == approx(205.0)
    assert result["net_debt"] == approx(20.0)
    assert result["equity_value"] == approx(185.0)


def test_equity_bridge_with_net_cash_adds_to_value():
    result = enterprise_to_equity_value(100.0, debt=0.0, cash=15.0)
    assert result["equity_value"] == approx(115.0)


def test_equity_bridge_rejects_negative_equity():
    with pytest.raises(ValueError, match="negative"):
        enterprise_to_equity_value(50.0, debt=80.0, cash=10.0)


# --- Relative valuation -----------------------------------------------------

def test_comparable_company_valuation(deal):
    result = comparable_company_valuation(
        deal.target.ebitda, deal.market_data.comparable_ev_ebitda_multiple
    )
    assert result["method"] == "Comparable Companies"
    assert result["ebitda"] == 18.0
    assert result["multiple"] == 10.5
    assert result["enterprise_value"] == approx(189.0)


def test_precedent_transaction_valuation(deal):
    result = precedent_transaction_valuation(
        deal.target.ebitda, deal.market_data.precedent_ev_ebitda_multiple
    )
    assert result["method"] == "Precedent Transactions"
    assert result["enterprise_value"] == approx(216.0)


@pytest.mark.parametrize("fn", [comparable_company_valuation, precedent_transaction_valuation])
@pytest.mark.parametrize("ebitda, multiple", [(0.0, 10.0), (-5.0, 10.0), (18.0, 0.0), (18.0, -2.0)])
def test_multiple_valuation_rejects_non_positive_inputs(fn, ebitda, multiple):
    with pytest.raises(ValueError):
        fn(ebitda, multiple)


# --- DCF --------------------------------------------------------------------

def test_dcf_forecast_fcfs(dcf):
    expected = [10.8, 11.664, 12.59712, 13.6048896, 14.693280768]
    assert dcf["forecast_fcfs"] == approx(expected)
    assert dcf["years"] == [1, 2, 3, 4, 5]


def test_dcf_present_values(dcf):
    assert dcf["pv_forecast_fcfs"] == approx(47.338, abs=1e-3)
    assert dcf["terminal_value"] == approx(200.808, abs=1e-3)
    assert dcf["discounted_terminal_value"] == approx(124.686, abs=1e-3)
    assert dcf["enterprise_value"] == approx(172.024, abs=1e-3)


def test_dcf_components_are_internally_consistent(dcf):
    assert sum(dcf["discounted_fcfs"]) == approx(dcf["pv_forecast_fcfs"])
    assert dcf["enterprise_value"] == approx(
        dcf["pv_forecast_fcfs"] + dcf["discounted_terminal_value"]
    )


def test_dcf_zero_growth_single_year():
    # One year, no growth: FCF_1 = 100; TV = 100 * 1 / 0.10 = 1000; EV = 1100 / 1.1 = 1000.
    result = dcf_valuation(100.0, 0.0, 0.10, 0.0, forecast_years=1)
    assert result["enterprise_value"] == approx(1000.0)


@pytest.mark.parametrize("terminal_growth", [0.10, 0.12])
def test_dcf_rejects_terminal_growth_at_or_above_wacc(terminal_growth):
    with pytest.raises(ValueError, match="must exceed terminal_growth_rate"):
        dcf_valuation(10.0, 0.08, 0.10, terminal_growth, forecast_years=5)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"wacc": 0.0},
        {"wacc": -0.05},
        {"wacc": 1.5},
        {"forecast_years": 0},
        {"forecast_years": 2.5},
        {"base_fcf": 0.0},
        {"growth_rate": -1.0},
        {"growth_rate": float("nan")},
    ],
)
def test_dcf_rejects_invalid_assumptions(kwargs):
    params = {
        "base_fcf": 10.0,
        "growth_rate": 0.08,
        "wacc": 0.10,
        "terminal_growth_rate": 0.025,
        "forecast_years": 5,
    }
    params.update(kwargs)
    with pytest.raises((ValueError, TypeError)):
        dcf_valuation(**params)


# --- Synergies --------------------------------------------------------------

def test_synergy_value(deal):
    s = deal.synergy_assumptions
    result = calculate_synergy_value(
        s.annual_cost_synergy,
        s.annual_revenue_synergy_ebitda,
        s.tax_rate,
        s.discount_rate,
        s.realization_years,
        s.integration_cost,
    )
    assert result["annual_pre_tax_synergies"] == approx(9.0)
    assert result["annual_after_tax_synergies"] == approx(6.75)
    annuity_factor = (1 - 1.10 ** -5) / 0.10
    assert result["pv_synergies"] == approx(6.75 * annuity_factor)
    assert result["net_synergy_value"] == approx(6.75 * annuity_factor - 12.0)
    assert result["net_synergy_value"] == approx(13.588, abs=1e-3)


def test_synergy_rejects_bad_rates():
    with pytest.raises(ValueError):
        calculate_synergy_value(6.0, 3.0, tax_rate=1.2, discount_rate=0.1,
                                realization_years=5, integration_cost=12.0)
    with pytest.raises(ValueError):
        calculate_synergy_value(6.0, 3.0, tax_rate=0.25, discount_rate=0.0,
                                realization_years=5, integration_cost=12.0)


# --- Accretion / dilution ---------------------------------------------------

def _sample_accretion(deal, synergies=0.0):
    t = deal.transaction_assumptions
    equity = enterprise_to_equity_value(t.offer_enterprise_value, deal.target.debt, deal.target.cash)
    return accretion_dilution(
        acquirer_net_income=deal.acquirer.net_income,
        acquirer_shares=deal.acquirer.shares_outstanding,
        acquirer_share_price=deal.acquirer.share_price,
        target_net_income=deal.target.net_income,
        purchase_equity_value=equity["equity_value"],
        cash_percentage=t.cash_percentage,
        stock_percentage=t.stock_percentage,
        interest_rate=t.new_debt_interest_rate,
        tax_rate=t.tax_rate,
        annual_after_tax_synergies=synergies,
    )


def test_accretion_dilution_without_synergies(deal):
    r = _sample_accretion(deal)
    # Cash 111.0 funded by debt at 5.5% -> 6.105 pre-tax, 4.57875 after-tax.
    # Stock 74.0 / $25 -> 2.96m new shares.
    assert r["cash_consideration"] == approx(111.0)
    assert r["stock_consideration"] == approx(74.0)
    assert r["after_tax_interest_expense"] == approx(4.57875)
    assert r["new_shares_issued"] == approx(2.96)
    assert r["standalone_eps"] == approx(0.72)
    assert r["pro_forma_net_income"] == approx(72.0 + 8.0 - 4.57875)
    assert r["pro_forma_eps"] == approx(75.42125 / 102.96)
    assert r["result"] == "Accretive"


def test_accretion_dilution_synergies_increase_eps(deal):
    base = _sample_accretion(deal)
    with_syn = _sample_accretion(deal, synergies=6.75)
    assert with_syn["pro_forma_eps"] > base["pro_forma_eps"]
    assert with_syn["pro_forma_net_income"] == approx(base["pro_forma_net_income"] + 6.75)


def test_all_stock_deal_can_be_dilutive():
    # Buying a low-earning target with expensive stock dilutes EPS.
    r = accretion_dilution(100.0, 100.0, 20.0, 1.0, 400.0, 0.0, 1.0, 0.05, 0.25)
    assert r["new_shares_issued"] == approx(20.0)
    assert r["result"] == "Dilutive"


def test_accretion_rejects_mix_not_summing_to_one():
    with pytest.raises(ValueError, match="must equal 1.0"):
        accretion_dilution(72.0, 100.0, 25.0, 8.0, 185.0, 0.6, 0.5, 0.055, 0.25)


# --- Summary ----------------------------------------------------------------

def test_valuation_summary_range(deal, dcf):
    comps = comparable_company_valuation(deal.target.ebitda, deal.market_data.comparable_ev_ebitda_multiple)
    precs = precedent_transaction_valuation(deal.target.ebitda, deal.market_data.precedent_ev_ebitda_multiple)
    summary = valuation_summary(comps["enterprise_value"], precs["enterprise_value"], dcf["enterprise_value"])
    assert summary["basis"] == "enterprise_value"
    assert summary["low"] == approx(172.024, abs=1e-3)
    assert summary["high"] == approx(216.0)
    assert summary["low_method"] == "Discounted Cash Flow"
    assert summary["high_method"] == "Precedent Transactions"
    assert summary["midpoint"] == approx((172.024 + 216.0) / 2, abs=1e-3)
    assert summary["average"] == approx((189.0 + 216.0 + 172.024) / 3, abs=1e-3)


def test_valuation_summary_rejects_non_positive():
    with pytest.raises(ValueError):
        valuation_summary(100.0, 0.0, 120.0)


# --- Relationship labels ----------------------------------------------------

@pytest.mark.parametrize("value, expected", [
    (100.0, "below"), (172.0, "within"), (200.0, "within"), (216.0, "within"), (216.01, "above"),
])
def test_classify_against_range(value, expected):
    from valuation import classify_against_range
    assert classify_against_range(value, 172.0, 216.0) == expected


def test_classify_against_range_rejects_inverted_bounds():
    from valuation import classify_against_range
    with pytest.raises(ValueError, match="low"):
        classify_against_range(1.0, 5.0, 2.0)


@pytest.mark.parametrize("value, reference, expected", [
    (205.0, 189.0, "above"), (205.0, 216.0, "below"), (205.0, 205.0, "equal"),
])
def test_compare_to_reference(value, reference, expected):
    from valuation import compare_to_reference
    assert compare_to_reference(value, reference) == expected


@pytest.mark.parametrize("value, expected", [
    (13.588, "positive"), (0.0, "zero"), (-0.01, "negative"),
])
def test_classify_sign(value, expected):
    from valuation import classify_sign
    assert classify_sign(value) == expected
