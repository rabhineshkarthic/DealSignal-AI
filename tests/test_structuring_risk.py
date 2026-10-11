"""Deal Structuring Analyst, Risk Analyst, and post-DD runner tests. Mocked LLM only."""

import json
import re
from pathlib import Path

import pytest

from orchestrator import DealTeamOrchestrator
from agent_validation import AgentValidationError
from agents import FINANCIAL_DATA_RULES, BaseAgent, DealStructuringAnalyst, RiskAnalyst
from deal_analysis import (
    build_synergy_context,
    build_transaction_structure_context,
    build_valuation_context,
)
from models import deal_to_context, load_deal

ROOT = Path(__file__).resolve().parent.parent
SAMPLE_DEAL_PATH = ROOT / "data" / "sample_deal.json"
NEW_AGENTS = [DealStructuringAnalyst, RiskAnalyst]
new_param = pytest.mark.parametrize("agent_cls", NEW_AGENTS, ids=lambda c: c.__name__)


class FakeLLM:
    def __init__(self, response=None):
        self.response = response
        self.calls = []

    def generate(self, system_prompt, user_prompt):
        self.calls.append({"system_prompt": system_prompt, "user_prompt": user_prompt})
        if self.response is not None:
            return self.response
        agent = re.match(r"You are the (.+?), ", system_prompt).group(1)
        return f"OUTPUT FROM {agent.upper()}"


@pytest.fixture(autouse=True)
def no_api_key(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)


@pytest.fixture(scope="module")
def deal():
    return load_deal(SAMPLE_DEAL_PATH)


@pytest.fixture(scope="module")
def full_ctx(deal):
    return {
        "deal": deal_to_context(deal),
        "deterministic_calculations": {
            "valuation": build_valuation_context(deal),
            "synergy": build_synergy_context(deal),
            "transaction_structure": build_transaction_structure_context(deal),
        },
    }


def prompt(agent_cls):
    return agent_cls(FakeLLM()).build_system_prompt()


def flat(text):
    return " ".join(text.split())


# --- Shared inheritance ---------------------------------------------------------------

@new_param
def test_subclasses_base_agent_and_keeps_validated_run(agent_cls):
    assert issubclass(agent_cls, BaseAgent)
    assert agent_cls.run is BaseAgent.run


@new_param
def test_inherits_shared_rules(agent_cls):
    p = prompt(agent_cls)
    assert FINANCIAL_DATA_RULES in p
    assert "NO ARITHMETIC RULE" in p
    assert "Never promote PRIOR ANALYSES into DEAL DATA or DETERMINISTIC CALCULATIONS." in p
    assert "A company name may be repeated, but it is not evidence of anything." in p


@new_param
def test_numeric_validator_active(agent_cls, full_ctx):
    with pytest.raises(AgentValidationError, match="2.88%"):
        agent_cls(FakeLLM("Sellers would own 2.88% of the company.")).run(full_ctx)
    ok = "Seller ownership is 0.0287; new shares issued are 2.96; EPS impact is Accretive."
    assert agent_cls(FakeLLM(ok)).run(full_ctx) == ok


@new_param
def test_prior_analysis_numbers_are_not_authorized(agent_cls, full_ctx):
    ctx = {**full_ctx, "prior_analyses": {"Deal Lead": "Debt capacity is 300."}}
    with pytest.raises(AgentValidationError, match="300"):
        agent_cls(FakeLLM("The Deal Lead said debt capacity is 300.")).run(ctx)


@new_param
def test_no_proceed_pass(agent_cls):
    assert "proceed" in prompt(agent_cls).split("SCOPE:")[1]
    assert re.search(r"Do NOT (issue a proceed/pass recommendation|recommend proceed or pass)",
                     prompt(agent_cls))


# --- Deal Structuring Analyst ---------------------------------------------------------

def test_structuring_headings_in_order():
    p = prompt(DealStructuringAnalyst)
    out = p[p.index("OUTPUT FORMAT"):]
    headings = ["# Deal Structuring Assessment", "## Proposed Consideration Structure",
                "## Deterministic Transaction Mechanics", "## Financing Considerations",
                "## Seller / Ownership Considerations", "## Structuring Information Gaps",
                "## Recommended Structuring Actions"]
    assert [out.index(h) for h in headings] == sorted(out.index(h) for h in headings)


def test_structuring_prohibited_calculations():
    section = flat(prompt(DealStructuringAnalyst).split("You must NOT calculate:")[1]
                   .split("ACCRETION / DILUTION:")[0])
    for item in ["financing amounts", "shares issued", "interest expense",
                 "ownership percentages", "accretion/dilution", "leverage", "premiums",
                 "purchase price adjustments"]:
        assert item in section, item


def test_structuring_does_not_invent_debt_capacity_or_financing():
    p = flat(prompt(DealStructuringAnalyst))
    assert ("Do NOT infer or invent debt capacity, borrowing capacity, leverage limits, "
            "covenant capacity or headroom, credit rating impact, lender appetite, or "
            "financing availability.") in p
    assert "THE FINANCING CONVENTION IS A MODELLING CONVENTION, NOT A FINANCING PLAN" in p
    assert "DEAL DATA does not show that such financing has been arranged" in p


def test_structuring_missing_terms_are_information_gaps():
    p = flat(prompt(DealStructuringAnalyst))
    for item in ["financing sources", "debt terms", "refinancing requirements", "rollover equity",
                 "management incentives", "purchase-price adjustments", "escrow / holdback",
                 "earn-outs", "tax structuring", "regulatory approvals"]:
        assert item in p, item
    assert "must be labelled INFORMATION GAP when absent from DEAL DATA" in p
    assert "Never describe an absent term as a problem." in p


def test_structuring_accretion_only_from_labels_and_case_stated():
    p = flat(prompt(DealStructuringAnalyst))
    assert "eps_impact_excluding_synergies" in p
    assert "eps_impact_including_run_rate_synergies" in p
    assert "always say which case you are describing" in p
    assert "Do not present it as the expected outcome." in p


# --- Risk Analyst -----------------------------------------------------------------------

def test_risk_headings_in_order():
    p = prompt(RiskAnalyst)
    out = p[p.index("OUTPUT FORMAT"):]
    headings = ["# Deal Risk Assessment", "## Evidence-Backed Risks", "## Unverified Assumptions",
                "## Information Gaps", "## Cross-Workstream Dependencies",
                "## Risk Mitigation / Diligence Actions",
                "## Items for Investment Committee Attention"]
    assert [out.index(h) for h in headings] == sorted(out.index(h) for h in headings)


def test_risk_four_categories():
    p = prompt(RiskAnalyst)
    for label in ["EVIDENCE-BACKED RISK:", "UNVERIFIED ASSUMPTION:", "INFORMATION GAP:",
                  "PRIOR-AGENT JUDGMENT:"]:
        assert label in p


def test_risk_does_not_convert_gaps_assumptions_or_speculation():
    p = flat(prompt(RiskAnalyst))
    assert "convert an information gap into a risk. Missing information is not automatically a risk" in p
    assert "convert an assumption into a fact." in p
    assert "convert prior-agent speculation into source evidence." in p
    assert "invent risks that no approved source or prior agent raised." in p


def test_risk_attribution():
    p = flat(prompt(RiskAnalyst))
    assert "must preserve attribution" in p
    assert ('GOOD: "Financial Due Diligence identified customer concentration as an '
            'information gap."') in p
    assert 'BAD: "Customer concentration is a material risk."' in p


def test_risk_no_scoring():
    p = flat(prompt(RiskAnalyst))
    assert ("Do NOT assign High / Medium / Low (or similar) ratings, numeric risk scores, "
            "probabilities, likelihoods, confidence percentages, or severity scores.") in p
    assert "Do not rank risks by importance." in p


def test_risk_categories_listed():
    p = prompt(RiskAnalyst)
    for c in ["strategic", "valuation", "synergy", "financial", "commercial",
              "legal / regulatory", "operational", "integration", "financing / structuring"]:
        assert c in p


# --- Runner ------------------------------------------------------------------------------

PRE_DD = ["Deal Lead", "Strategic Fit Analyst", "Valuation Analyst", "Synergy Analyst"]
DD = ["Financial Due Diligence", "Commercial Due Diligence", "Legal & Operational Due Diligence"]


@pytest.fixture(scope="module")
def run(deal):
    llm = FakeLLM()
    outputs = DealTeamOrchestrator(llm).run_initial(deal).outputs
    prompts = {re.match(r"You are the (.+?), ", c["system_prompt"]).group(1): c["user_prompt"]
               for c in llm.calls}
    return outputs, prompts


def test_pipeline_full_order(run):
    outputs, _ = run
    assert list(outputs) == PRE_DD + DD + ["Deal Structuring Analyst", "Risk Analyst"]


@pytest.mark.parametrize("dd_name", DD)
def test_dd_peers_still_isolated(run, dd_name):
    outputs, prompts = run
    for other in DD + ["Deal Structuring Analyst", "Risk Analyst"]:
        assert outputs[other] not in prompts[dd_name]


def test_structuring_receives_all_pre_dd_and_dd_outputs(run):
    outputs, prompts = run
    p = prompts["Deal Structuring Analyst"]
    for name in PRE_DD + DD:
        assert f"--- {name} ---\n{outputs[name]}" in p
    assert outputs["Risk Analyst"] not in p


def test_risk_receives_everything_including_structuring(run):
    outputs, prompts = run
    p = prompts["Risk Analyst"]
    for name in PRE_DD + DD + ["Deal Structuring Analyst"]:
        assert f"--- {name} ---\n{outputs[name]}" in p


@pytest.mark.parametrize("agent_name", ["Deal Structuring Analyst", "Risk Analyst"])
def test_transaction_structure_reaches_structuring_and_risk(run, deal, agent_name):
    _, prompts = run
    # the canonical contexts: the builders' output plus input-lineage notes (share basis)
    expected = DealTeamOrchestrator.build_deterministic_contexts(deal)
    assert expected["transaction_structure"]["calculation"] == (
        build_transaction_structure_context(deal)["calculation"])
    assert json.dumps(expected, indent=2) in prompts[agent_name]


@pytest.mark.parametrize("dd_name", DD)
def test_dd_peers_do_not_receive_transaction_structure(run, dd_name):
    _, prompts = run
    assert '"transaction_structure"' not in prompts[dd_name]
