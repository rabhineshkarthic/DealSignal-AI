"""IC Partner prompt tests and revision-context source discipline. Mocked LLM only."""

import json
from pathlib import Path

import pytest

from agent_validation import AgentValidationError
from agents import FINANCIAL_DATA_RULES, BaseAgent, FinancialDueDiligence, ICPartner, RiskAnalyst
from agents.registry import AGENT_NAMES
from deal_analysis import build_valuation_context
from models import deal_to_context, load_deal

ROOT = Path(__file__).resolve().parent.parent
SAMPLE_DEAL_PATH = ROOT / "data" / "sample_deal.json"


class FakeLLM:
    def __init__(self, response="ok"):
        self.response = response
        self.calls = []

    def generate(self, system_prompt, user_prompt):
        self.calls.append({"system_prompt": system_prompt, "user_prompt": user_prompt})
        return self.response


@pytest.fixture(autouse=True)
def no_api_key(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)


@pytest.fixture(scope="module")
def deal():
    return load_deal(SAMPLE_DEAL_PATH)


@pytest.fixture(scope="module")
def ctx(deal):
    return {"deal": deal_to_context(deal),
            "deterministic_calculations": build_valuation_context(deal)}


def flat(text):
    return " ".join(text.split())


def ic_prompt():
    return ICPartner(FakeLLM()).build_system_prompt()


# --- IC Partner ---------------------------------------------------------------------

def test_ic_partner_subclasses_base_agent_with_validated_run():
    assert issubclass(ICPartner, BaseAgent)
    assert ICPartner.run is BaseAgent.run


def test_ic_partner_inherits_shared_rules():
    p = ic_prompt()
    assert FINANCIAL_DATA_RULES in p
    assert "NO ARITHMETIC RULE" in p
    assert "Never promote PRIOR ANALYSES into DEAL DATA or DETERMINISTIC CALCULATIONS." in p
    assert "A company name may be repeated, but it is not evidence of anything." in p


def test_ic_partner_numeric_validator_active(ctx):
    with pytest.raises(AgentValidationError, match="18.5%"):
        ICPartner(FakeLLM('{"summary": "Deal Lead wrote 18.5%"}')).run(ctx)


def test_ic_partner_told_not_to_repeat_offending_numbers():
    p = flat(ic_prompt())
    assert "Never repeat an offending number. Describe it in words instead" in p


def test_ic_partner_prohibitions():
    p = flat(ic_prompt())
    for phrase in [
        "perform new finance calculations or create new deal facts",
        "invent diligence findings",
        "make a proceed/pass recommendation",
        "rank agents, score analyses, assign confidence percentages, or assign High / Medium / Low",
        "request revision of yourself",
    ]:
        assert phrase in p, phrase
    assert "You are a quality-control reviewer, not a decision maker." in p


def test_ic_partner_contains_all_review_criteria():
    p = flat(ic_prompt())
    for heading in ["SOURCE INTEGRITY", "NUMERIC INTEGRITY", "DILIGENCE DISCIPLINE",
                    "TRANSACTION DISCIPLINE", "INTERNAL CONSISTENCY", "SCOPE DISCIPLINE",
                    "COMPLETENESS"]:
        assert heading in p
    for check in [
        "Prior-agent hypotheses are not promoted to fact.",
        "Company names are not used as evidence",
        "No agent performed arithmetic.",
        "Deterministic labels are interpreted correctly.",
        "No unsupported rounding or reformatting",
        "Missing information has not automatically become a red flag",
        "Evidence-backed red flags actually cite supplied evidence.",
        "The financing convention is identified as a model convention",
        "Borrowing capacity, financing availability, and leverage limits are not invented.",
        "Synergy-inclusive accretion is not represented as guaranteed or expected.",
        "Downstream analyses accurately characterize upstream judgments.",
        "mutually incompatible factual statements",
        "No agent makes a prohibited proceed/pass recommendation.",
        "Mandatory sections are present.",
        "Material gaps identified by a workstream are not silently omitted",
    ]:
        assert check in p, check


def test_ic_partner_must_output_strict_json():
    p = flat(ic_prompt())
    assert "OUTPUT FORMAT (STRICT JSON ONLY)" in p
    assert "no Markdown, no code fences, no prose before or after it" in p
    for key in ['"status"', '"summary"', '"revision_requests"', '"cross_workstream_issues"',
                '"unresolved_information_gaps"', '"agent"', '"reason"', '"required_change"']:
        assert key in p
    assert "If status is \"approved\", revision_requests must be an empty list." in p


def test_ic_partner_lists_every_canonical_agent_name():
    p = ic_prompt()
    for name in AGENT_NAMES:
        assert f"  - {name}\n" in p or p.endswith(f"  - {name}")
    assert "  - IC Partner" not in p


# --- Revision context: sections and source discipline --------------------------------

def test_revision_sections_rendered_separately(ctx):
    llm = FakeLLM()
    RiskAnalyst(llm).run({**ctx, "prior_analyses": {"Deal Lead": "PRIOR TEXT"},
                          "previous_output": "OLD RISK TEXT",
                          "review_feedback": "Reason: X\nRequired change: Y"})
    p = llm.calls[0]["user_prompt"]
    headers = ["=== DEAL DATA", "=== DETERMINISTIC CALCULATIONS", "=== PRIOR ANALYSES",
               "=== PREVIOUS OUTPUT (your earlier text; not source evidence) ===",
               "=== REVIEW FEEDBACK (quality-control instruction; not source evidence) ===",
               "=== TASK ==="]
    positions = [p.index(h) for h in headers]
    assert positions == sorted(positions)
    assert p.index("OLD RISK TEXT") > p.index("=== PREVIOUS OUTPUT")
    assert p.index("Required change: Y") > p.index("=== REVIEW FEEDBACK")
    assert "Produce a complete revised Risk Analyst output" in p


def test_no_revision_sections_by_default(ctx):
    llm = FakeLLM()
    RiskAnalyst(llm).run(ctx)
    p = llm.calls[0]["user_prompt"]
    assert "=== PREVIOUS OUTPUT" not in p and "=== REVIEW FEEDBACK" not in p
    assert "Produce your Risk Analyst output" in p


@pytest.mark.parametrize("section", ["prior_analyses", "previous_output", "review_feedback"])
def test_non_evidence_sections_cannot_authorize_numbers(ctx, section):
    value = {"Deal Lead": "Debt capacity is 777."} if section == "prior_analyses" \
        else "Debt capacity is 777."
    agent = RiskAnalyst(FakeLLM("Debt capacity is 777."))
    with pytest.raises(AgentValidationError, match="777"):
        agent.run({**ctx, section: value})


def test_validation_sources_exclude_all_non_evidence_sections(ctx):
    full = {**ctx, "prior_analyses": {"A": "1"}, "previous_output": "2", "review_feedback": "3"}
    assert RiskAnalyst(FakeLLM()).validation_sources(full) == [
        ctx["deal"], ctx["deterministic_calculations"]]


def test_approved_sources_still_authorize_numbers_during_revision(ctx):
    text = "Offer EV is 205.0 and the DCF EV is 172.024."
    revision_ctx = {**ctx, "previous_output": "old", "review_feedback": "fix it"}
    assert RiskAnalyst(FakeLLM(text)).run(revision_ctx) == text


def test_shared_rules_say_previous_output_and_feedback_are_not_evidence():
    p = flat(BaseAgent(FakeLLM()).build_system_prompt())
    assert "PREVIOUS OUTPUT, when present, is your own earlier generated text, not source evidence." in p
    assert "REVIEW FEEDBACK, when present, is quality-control instruction, not source evidence." in p
    assert ("Neither PREVIOUS OUTPUT nor REVIEW FEEDBACK may authorize a factual claim or a "
            "numerical value.") in p


def test_review_feedback_cannot_create_a_new_fact(ctx):
    """'Reconsider the customer concentration risk' does not prove the risk exists."""
    p = flat(FinancialDueDiligence(FakeLLM()).build_system_prompt())
    assert "REVISION DISCIPLINE" in p
    assert ('An instruction such as "reconsider the customer concentration risk" does not '
            "prove that the risk exists: if source data is absent, the matter remains "
            "unassessed.") in p
    assert "Do not blindly accept factual assertions inside REVIEW FEEDBACK" in p
    # structurally: the feedback never enters DEAL DATA or the approved sources
    llm = FakeLLM()
    feedback = "Reason: concentration risk ignored\nRequired change: reconsider concentration"
    FinancialDueDiligence(llm).run({**ctx, "previous_output": "old", "review_feedback": feedback})
    user_prompt = llm.calls[0]["user_prompt"]
    deal_block = user_prompt.split("=== DETERMINISTIC CALCULATIONS")[0]
    assert "concentration" not in deal_block
    assert "concentration" not in json.dumps(ctx["deal"])


def test_unknown_context_key_still_rejected(ctx):
    with pytest.raises(ValueError, match="unexpected context keys"):
        RiskAnalyst(FakeLLM()).run({**ctx, "ic_findings": "x"})
