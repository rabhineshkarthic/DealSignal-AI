"""Tests for post-generation numeric provenance validation. No API key, no network."""

from decimal import Decimal
from pathlib import Path

import pytest

from agent_validation import (
    AgentValidationError,
    approved_numbers,
    find_numeric_violations,
    validate_agent_output,
)
from agents import BaseAgent
from deal_analysis import build_synergy_context, build_valuation_context
from models import deal_to_context, load_deal

ROOT = Path(__file__).resolve().parent.parent
SAMPLE_DEAL_PATH = ROOT / "data" / "sample_deal.json"

DEAL = {"tax_rate": 0.25, "offer_enterprise_value": 205.0, "units": "millions"}
CALCS = {"dcf": {"enterprise_value": 172.024}}


def tokens(response, sources=(DEAL,)):
    return [v.token for v in find_numeric_violations(response, sources)]


# --- Allowed vs rejected --------------------------------------------------------

def test_supplied_decimal_is_allowed():
    assert tokens("The tax rate is 0.25.") == []


def test_derived_percentage_is_rejected():
    assert tokens("The tax rate is 25%.") == ["25%"]


def test_percentage_not_authorized_by_same_plain_number():
    """A share price of 25.0 must not authorize "25%": percent is a unit."""
    assert tokens("A 25% stake.", sources=({"share_price": 25.0},)) == ["25%"]
    assert tokens("A price of 25.", sources=({"share_price": 25.0},)) == []


def test_percentage_allowed_only_if_source_contains_that_percentage():
    assert tokens("A 25% stake.", sources=({"note": "a 25% stake"},)) == []


def test_supplied_amount_is_allowed_in_equivalent_decimal_forms():
    assert tokens("Offer EV of 205.0 (quoted as 205 or $205 million).") == []


@pytest.mark.parametrize("derived", ["205000000", "205,000,000", "$205,000,000"])
def test_rescaled_amount_is_rejected(derived):
    assert tokens(f"Offer EV of {derived}.") == [derived.lstrip("$")]


def test_rounded_value_is_rejected():
    assert tokens("DCF EV of 172.02.", sources=(DEAL, CALCS)) == ["172.02"]


def test_deterministic_values_are_allowed():
    assert tokens("DCF EV is 172.024.", sources=(DEAL, CALCS)) == []


def test_deterministic_values_rejected_when_calculations_not_supplied():
    assert tokens("DCF EV is 172.024.", sources=(DEAL,)) == ["172.024"]


def test_numbers_inside_source_strings_are_approved():
    assert tokens("From year 1.", sources=({"note": "counted from year 1"},)) == []


def test_booleans_and_none_are_not_numbers():
    assert approved_numbers([{"flag": True, "x": None}]) == set()


# --- Markdown numbering and identifiers -----------------------------------------

def test_markdown_list_and_heading_numbers_are_ignored():
    response = (
        "## 7. Key Unknowns\n"
        "1. First question\n"
        "2) Second question\n"
        "  3. Nested item\n"
        "- 4. Bulleted numbered item\n"
        "### 8.2 Sub-heading\n"
    )
    assert tokens(response) == []


def test_numbers_in_list_item_text_are_still_checked():
    assert tokens("1. The tax rate is 25%.") == ["25%"]


def test_numbers_glued_to_identifiers_are_ignored():
    assert tokens("Q3 results, EBITDA2 field, v1.2 release") == []


def test_violation_reports_token_and_line():
    (violation,) = find_numeric_violations("ok line\nrate is 8% here", [DEAL])
    assert violation.token == "8%"
    assert violation.value == Decimal("8")
    assert violation.line_number == 2
    assert violation.line == "rate is 8% here"


# --- validate_agent_output ------------------------------------------------------

def test_validate_returns_response_unchanged_when_valid():
    text = "Offer EV of 205.0 and a tax rate of 0.25."
    assert validate_agent_output("Agent", text, [DEAL]) is text


def test_validate_raises_with_every_offending_token():
    with pytest.raises(AgentValidationError) as exc_info:
        validate_agent_output("Agent", "Rates: 25%, 10% and 0.25.", [DEAL])
    err = exc_info.value
    assert [v.token for v in err.violations] == ["25%", "10%"]
    assert "'25%'" in str(err) and "'10%'" in str(err)
    assert err.agent_name == "Agent"
    assert err.response == "Rates: 25%, 10% and 0.25."


# --- BaseAgent integration ------------------------------------------------------

class FakeLLM:
    def __init__(self, response):
        self.response = response

    def generate(self, system_prompt, user_prompt):
        return self.response


ALL_AGENT_CLASSES = [BaseAgent, *BaseAgent.__subclasses__()]


@pytest.fixture(scope="module")
def sample():
    deal = load_deal(SAMPLE_DEAL_PATH)
    return {
        "deal": deal_to_context(deal),
        "valuation": build_valuation_context(deal),
        "synergy": build_synergy_context(deal),
    }


@pytest.fixture(autouse=True)
def no_api_key(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)


@pytest.mark.parametrize("agent_cls", ALL_AGENT_CLASSES, ids=lambda c: c.__name__)
def test_every_agent_rejects_derived_numbers(agent_cls, sample):
    agent = agent_cls(FakeLLM("The tax rate is 25%."))
    with pytest.raises(AgentValidationError, match="25%"):
        agent.run({"deal": sample["deal"]})


@pytest.mark.parametrize("agent_cls", ALL_AGENT_CLASSES, ids=lambda c: c.__name__)
def test_every_agent_accepts_supplied_numbers(agent_cls, sample):
    text = "The target reports revenue of 120.0 and a tax rate of 0.25."
    assert agent_cls(FakeLLM(text)).run({"deal": sample["deal"]}) == text


def test_sample_deal_percent_conversions_from_live_run_are_all_rejected(sample):
    """The exact conversions seen in the live run."""
    response = "Rates of 25%, 10%, 8%, 2.5%, 60%, 40% and 5.5%."
    agent = BaseAgent(FakeLLM(response))
    with pytest.raises(AgentValidationError) as exc_info:
        agent.run({"deal": sample["deal"]})
    assert [v.token for v in exc_info.value.violations] == [
        "25%", "10%", "8%", "2.5%", "60%", "40%", "5.5%"]


def test_sample_deal_decimals_are_accepted(sample):
    response = "Rates of 0.25, 0.1, 0.08, 0.025, 0.6, 0.4 and 0.055."
    assert BaseAgent(FakeLLM(response)).run({"deal": sample["deal"]}) == response


def test_deterministic_calculation_values_accepted_via_agent(sample):
    response = ("DCF EV 172.024, range low 172.024 to high 216.0, midpoint 194.012, "
                "net synergy value 13.5878.")
    ctx = {"deal": sample["deal"],
           "deterministic_calculations": {**sample["valuation"], **sample["synergy"]}}
    assert BaseAgent(FakeLLM(response)).run(ctx) == response


def test_prior_analysis_only_figures_are_rejected(sample):
    """A figure a prior agent invented does not become an approved source."""
    ctx = {"deal": sample["deal"],
           "prior_analyses": {"Deal Lead": "Combined revenue would be 770."}}
    agent = BaseAgent(FakeLLM("The Deal Lead cited combined revenue of 770."))
    with pytest.raises(AgentValidationError, match="770"):
        agent.run(ctx)


def test_validation_sources_exclude_prior_analyses(sample):
    ctx = {"deal": sample["deal"], "deterministic_calculations": sample["valuation"],
           "prior_analyses": {"Deal Lead": "text 770"}}
    sources = BaseAgent(FakeLLM("")).validation_sources(ctx)
    assert sources == [sample["deal"], sample["valuation"]]


# --- Prompt rules that support the validator -------------------------------------

@pytest.mark.parametrize("agent_cls", ALL_AGENT_CLASSES, ids=lambda c: c.__name__)
def test_company_name_inference_prohibited_in_every_prompt(agent_cls):
    prompt = agent_cls(FakeLLM("")).build_system_prompt()
    assert ("Never infer a company's products, technologies, capabilities, industry, business\n"
            "  model, customers, markets, or strategic motives from its name.") in prompt
    assert "A company name may be repeated, but it is not evidence of anything." in prompt


@pytest.mark.parametrize("agent_cls", ALL_AGENT_CLASSES, ids=lambda c: c.__name__)
def test_every_prompt_announces_number_checking_and_bans_conversions(agent_cls):
    prompt = agent_cls(FakeLLM("")).build_system_prompt()
    assert "NUMBER FORMATTING (AUTOMATICALLY CHECKED)" in prompt
    assert "Do not convert decimals to percentages" in prompt
    assert "Write counts, ordinals, and list positions in words" in prompt


def test_prompt_no_longer_demonstrates_percent_conversion():
    prompt = BaseAgent(FakeLLM("")).build_system_prompt()
    assert "means 8%" not in prompt
    assert "do not convert them to percentages" in prompt


# --- Sign preservation -----------------------------------------------------------

POS = ({"net_debt": 20.0},)
NEG = ({"net_synergy_value": -20.0},)


def test_positive_source_does_not_authorize_negative_output():
    assert tokens("Net debt is -20.", sources=POS) == ["-20"]


@pytest.mark.parametrize("text", ["-20", "-20.0", "-$20", "−20", "(-20)"])
def test_negative_source_authorizes_equivalent_negative_forms(text):
    assert tokens(f"Net synergy value is {text}.", sources=NEG) == []


@pytest.mark.parametrize("text", ["20", "20.0", "$20"])
def test_negative_source_does_not_authorize_positive_output(text):
    assert tokens(f"Net synergy value is {text}.", sources=NEG) == [text.lstrip("$")]


def test_negative_decimals():
    src = ({"eps_change": -0.0125},)
    assert tokens("EPS change of -0.0125.", sources=src) == []
    assert tokens("EPS change of 0.0125.", sources=src) == ["0.0125"]
    assert tokens("EPS change of -0.0125.", sources=({"eps_change": 0.0125},)) == ["-0.0125"]


def test_negative_rounding_and_rescaling_still_rejected():
    assert tokens("Value of -20.5.", sources=({"v": -20.55},)) == ["-20.5"]
    assert tokens("Value of -20,000,000.", sources=NEG) == ["-20,000,000"]


def test_negative_percentage_allowed_only_with_explicit_negative_percentage_source():
    src = ({"note": "revenue declined -5% last year"},)
    assert tokens("Revenue declined -5%.", sources=src) == []
    assert tokens("Revenue grew 5%.", sources=src) == ["5%"]
    # a plain -5.0 still does not authorize a percentage
    assert tokens("Revenue declined -5%.", sources=({"v": -5.0},)) == ["-5%"]


def test_negative_numbers_inside_source_strings_keep_their_sign():
    src = ({"note": "net value was -20 in the downside"},)
    assert tokens("Downside of -20.", sources=src) == []
    assert tokens("Downside of 20.", sources=src) == ["20"]


def test_markdown_bullet_dash_is_not_a_negative_sign():
    response = "- 20.0 is the reported net debt\n* 20.0 again\n  - 20.0 nested"
    assert tokens(response, sources=POS) == []
    assert tokens(response, sources=NEG) == ["20.0", "20.0", "20.0"]


def test_bullet_followed_by_negative_number():
    assert tokens("- -20 is the net synergy value", sources=NEG) == []
    assert tokens("- -20 is net debt", sources=POS) == ["-20"]


def test_line_starting_with_negative_number_is_negative():
    assert tokens("-20 is the net synergy value", sources=NEG) == []


@pytest.mark.parametrize("text", ["3-5", "10.5x-12.0x", "172.024-216.0", "year-2"])
def test_ranges_and_hyphenated_words_are_not_negatives(text):
    src = ({"a": 3.0, "b": 5.0, "c": 10.5, "d": 12.0, "e": 172.024, "f": 216.0, "g": 2.0},)
    assert tokens(f"Range {text}.", sources=src) == []


def test_sample_deal_values_keep_sign(sample):
    ctx = {"deal": sample["deal"], "deterministic_calculations": sample["synergy"]}
    ok = "Net synergy value is 13.5878; target net debt inputs are 25.0 and 5.0."
    assert BaseAgent(FakeLLM(ok)).run(ctx) == ok
    with pytest.raises(AgentValidationError, match="-13.5878"):
        BaseAgent(FakeLLM("Net synergy value is -13.5878.")).run(ctx)
