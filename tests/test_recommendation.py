"""Deal Recommendation: surfaced only from the existing memo conclusion, and only for a
complete, approved deal case. No Gemini, no network."""

from dataclasses import replace
from pathlib import Path

import pytest

import llm_client
import ui_components as uc
import ui_helpers as ui
from agents.ic_memo_writer import PERMITTED_CONCLUSIONS
from agents.registry import RISK
from config import load_settings
from demo_state import DEMO_LABEL, build_demo_state
from memo import InvestmentCommitteeMemoGenerator
from models import load_deal
from orchestrator import DealTeamOrchestrator
from test_memo import ScriptedLLM, approved, memo_json, revise

ROOT = Path(__file__).resolve().parent.parent
SAMPLE = load_deal(ROOT / "data" / "sample_deal.json")


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def no_client(*args, **kwargs):
        raise AssertionError("a real GeminiClient must not be created")

    monkeypatch.setattr(llm_client.GeminiClient, "__init__", no_client)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)


@pytest.fixture(scope="module")
def demo():
    return build_demo_state()


# --- Gate ------------------------------------------------------------------------------------------

def test_no_recommendation_without_a_complete_case(demo):
    assert ui.recommendation_view(None, demo.result, demo.memo) is None
    assert ui.recommendation_view(demo.deal, None, demo.memo) is None
    assert ui.recommendation_view(demo.deal, demo.result, None) is None  # memo not generated


def test_no_recommendation_when_review_unresolved(demo):
    unresolved = DealTeamOrchestrator(ScriptedLLM([revise(RISK), revise(RISK)])).run(SAMPLE)
    assert unresolved.status == "review_limit_reached"
    assert ui.recommendation_view(SAMPLE, unresolved, demo.memo) is None


def test_no_recommendation_from_a_memo_for_a_different_state(demo):
    other = DealTeamOrchestrator(ScriptedLLM([approved()])).run(SAMPLE)
    assert ui.recommendation_view(SAMPLE, other, demo.memo) is None       # versions differ
    other_deal = replace(SAMPLE, deal_name="Another deal")
    assert ui.recommendation_view(other_deal, demo.result, demo.memo) is None
    assert ui.recommendation_view(demo.deal, demo.result,
                                  replace(demo.memo, orchestration_status="review_limit_reached")) is None


# --- Content ----------------------------------------------------------------------------------------

def test_uses_the_existing_controlled_conclusion_only(demo):
    view = ui.recommendation_view(demo.deal, demo.result, demo.memo)
    assert view.conclusion == demo.memo.conclusion and view.conclusion in PERMITTED_CONCLUSIONS
    assert view.review_round == demo.result.review_history[-1].round
    field_names = set(vars(view))
    assert not field_names & {"score", "probability", "confidence", "rating"}


@pytest.mark.parametrize("conclusion", PERMITTED_CONCLUSIONS)
def test_every_permitted_conclusion_is_surfaced_verbatim(conclusion):
    llm = ScriptedLLM([approved()], memo_response=memo_json(conclusion))
    result = DealTeamOrchestrator(llm).run(SAMPLE)
    memo = InvestmentCommitteeMemoGenerator(llm).generate(result, SAMPLE)
    view = ui.recommendation_view(SAMPLE, result, memo)
    assert view.conclusion == conclusion
    html = uc.recommendation_card(view)
    assert conclusion in html and "Deal recommendation" in html


def test_sections_are_verbatim_memo_lines(demo):
    view = ui.recommendation_view(demo.deal, demo.result, demo.memo)
    memo_lines = {line.strip() for line in demo.memo.memo_text.splitlines()}
    for line in (*view.why, *view.conditions, *view.risks):
        assert line in memo_lines
    assert view.conditions == ("Obtain customer, contract, and financing information before "
                               "commitment.",)
    assert view.gaps == tuple(demo.result.review_history[-1].review.unresolved_information_gaps)


def test_financial_context_comes_from_deterministic_calculations(demo):
    view = ui.recommendation_view(demo.deal, demo.result, demo.memo)
    ctx = demo.result.deterministic_contexts
    financial = dict(view.financial)
    summary = ctx["valuation"]["valuation_summary"]
    assert financial["Valuation range (EV)"] == (
        f"{ui.fmt_money(summary['low'], demo.deal)} – {ui.fmt_money(summary['high'], demo.deal)}")
    assert financial["Offer EV"] == "USD 205.0m (within the range)"
    assert financial["Net synergy value"].startswith(
        ui.fmt_money(ctx["synergy"]["calculation"]["net_synergy_value"], demo.deal, 4))
    assert financial["EPS impact · excluding synergies"] == \
        ctx["transaction_structure"]["labels"]["eps_impact_excluding_synergies"]
    assert "not the expected case" in financial["EPS impact · incl. run-rate synergies"]


def test_memo_section_extraction():
    text = "# T\n## A\n- one\n2) two\nthree\nfour\nfive\n## B\nother"
    assert ui.memo_section_lines(text, "## A") == ("one", "two", "three", "four")
    assert ui.memo_section_lines(text, "## Missing") == ()


def test_card_escapes_text(demo):
    view = replace(ui.recommendation_view(demo.deal, demo.result, demo.memo),
                   why=("<script>x</script>",))
    assert "<script>" not in uc.recommendation_card(view)


# --- UI placement -------------------------------------------------------------------------------------

def app_test():
    from streamlit.testing.v1 import AppTest
    return AppTest.from_file(str(ROOT / "app.py"), default_timeout=120)


@pytest.fixture
def unconfigured(monkeypatch):
    monkeypatch.setattr(ui, "load_settings", lambda: load_settings(env_file=None))


def markdown_values(at):
    return [m.value for m in at.markdown]


def test_offline_demo_shows_recommendation_above_memo_and_in_overview(unconfigured, demo):
    at = app_test().run()
    assert not at.exception
    values = markdown_values(at)
    compact = [i for i, v in enumerate(values) if 'class="rec-compact"' in v]
    full = [i for i, v in enumerate(values) if 'class="rec"' in v]
    memo_body = [i for i, v in enumerate(values) if v == demo.memo.memo_text]
    assert len(compact) == 1 and len(full) == 1 and len(memo_body) == 1
    assert full[0] < memo_body[0]                 # recommendation above the full memo
    assert demo.memo.conclusion in values[full[0]] and DEMO_LABEL in values[full[0]]


def test_no_recommendation_before_analysis(unconfigured):
    at = app_test().run()
    at.sidebar.radio(key="deal_source").set_value(ui.CUSTOM_DEAL).run()
    at.button(key="FormSubmitter:custom_deal_form-Create Deal").click().run()
    assert at.session_state["custom_deal"] is not None
    assert not any('class="rec' in v for v in markdown_values(at))  # no card
    at.sidebar.radio(key="mode").set_value(ui.LIVE_ANALYSIS).run()
    at.sidebar.radio(key="deal_source").set_value(ui.SAMPLE_DEAL).run()
    assert not any('class="rec' in v for v in markdown_values(at))  # no card
