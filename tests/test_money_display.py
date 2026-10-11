"""Display-only money formatting: values stay in millions; text auto-scales to bn."""

from dataclasses import replace
from pathlib import Path

import pytest

import ui_helpers as ui
from deal_analysis import build_valuation_context
from models import load_deal
from orchestrator import DealTeamOrchestrator
from research.models import ResearchStatus, ResearchValue

ROOT = Path(__file__).resolve().parent.parent
SAMPLE = load_deal(ROOT / "data" / "sample_deal.json")


@pytest.mark.parametrize("value, text", [
    (999.9, "USD 999.9m"), (1000, "USD 1.0bn"), (60000, "USD 60.0bn"),
    (19272.1, "USD 19.3bn"), (-1214.7, "(USD 1.2bn)"), (576.8, "USD 576.8m"),
    (0.0, "USD 0.0m"), (-45.0, "(USD 45.0m)"),
])
def test_auto_scaled_display(value, text):
    assert ui.fmt_money(value, SAMPLE) == text


def test_rounding_up_to_one_thousand_switches_to_billions():
    assert ui.fmt_money(999.96, SAMPLE) == "USD 1.0bn"  # not "USD 1,000.0m"


@pytest.mark.parametrize("value, text", [(1214.7, "USD 1.21bn"), (19272.1, "USD 19.27bn"),
                                         (-1214.7, "(USD 1.21bn)"), (13.5878, "USD 13.5878m")])
def test_extra_precision_when_caller_asks(value, text):
    assert ui.fmt_money(value, SAMPLE, 4) == text


def test_other_units_are_not_rescaled():
    deal = replace(SAMPLE, units="thousands")
    assert ui.fmt_money(1500.0, deal) == "USD 1,500.0 thousands"


def test_research_fact_values_use_the_same_formatter():
    value = ResearchValue("revenue", ResearchStatus.REPORTED, 245122.0, "USD millions")
    assert ui.research_value_text(value) == "USD 245.1bn" == ui.fmt_money(245122.0, SAMPLE)
    small = ResearchValue("cash", ResearchStatus.REPORTED, 576.8, "USD millions")
    assert ui.research_value_text(small) == "USD 576.8m"


def test_display_does_not_change_model_values():
    big = replace(SAMPLE, transaction_assumptions=replace(
        SAMPLE.transaction_assumptions, offer_enterprise_value=60000.0))
    before = build_valuation_context(SAMPLE, ndigits=None)
    contexts = DealTeamOrchestrator.build_deterministic_contexts(big)
    preview = dict(ui.deal_preview(big, contexts))
    assert preview["Offer EV"] == "USD 60.0bn"
    assert big.transaction_assumptions.offer_enterprise_value == 60000.0           # millions
    assert contexts["transaction_structure"]["calculation"]["offer_equity_value"] == 59980.0
    assert build_valuation_context(SAMPLE, ndigits=None) == before                 # untouched
    assert dict(ui.overview_metrics(SAMPLE, DealTeamOrchestrator.build_deterministic_contexts(SAMPLE))) == {
        "Offer Enterprise Value": "USD 205.0m", "Offer Equity Value": "USD 185.0m",
        "Cash Consideration": "USD 111.0m", "Stock Consideration": "USD 74.0m",
        "Target Net Debt": "USD 20.0m"}  # sample figures are below 1bn: unchanged text
