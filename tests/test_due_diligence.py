"""Due-diligence agent tests. Mocked LLM only; no API key, no network."""

import json
import re
from pathlib import Path

import pytest

from agents.registry import STEP_LABELS
from orchestrator import AgentRun, DealTeamOrchestrator
from agent_validation import AgentValidationError
from agents import (
    FINANCIAL_DATA_RULES,
    BaseAgent,
    CommercialDueDiligence,
    FinancialDueDiligence,
    LegalOperationalDueDiligence,
)
from agents.due_diligence import DD_EVIDENCE_RULES
from deal_analysis import build_synergy_context, build_valuation_context
from models import deal_to_context, load_deal

ROOT = Path(__file__).resolve().parent.parent
SAMPLE_DEAL_PATH = ROOT / "data" / "sample_deal.json"
DD_CLASSES = [FinancialDueDiligence, CommercialDueDiligence, LegalOperationalDueDiligence]
dd_param = pytest.mark.parametrize("agent_cls", DD_CLASSES, ids=lambda c: c.__name__)


class FakeLLM:
    """Records calls; replies with a digit-free marker naming the calling agent."""

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


def prompt(agent_cls):
    return agent_cls(FakeLLM()).build_system_prompt()


# --- Inheritance ---------------------------------------------------------------

@dd_param
def test_dd_agents_subclass_base_agent(agent_cls):
    assert issubclass(agent_cls, BaseAgent)
    assert agent_cls.run is BaseAgent.run  # validation path is not overridden


@dd_param
def test_dd_agents_inherit_shared_rules(agent_cls):
    p = prompt(agent_cls)
    assert FINANCIAL_DATA_RULES in p
    assert "SOURCE ATTRIBUTION" in p
    assert "Never promote PRIOR ANALYSES into DEAL DATA or DETERMINISTIC CALCULATIONS." in p
    assert "A company name may be repeated, but it is not evidence of anything." in p
    assert "NUMBER FORMATTING (AUTOMATICALLY CHECKED)" in p
    assert DD_EVIDENCE_RULES in p


@dd_param
def test_dd_agents_use_numeric_validator(agent_cls, deal):
    ctx = {"deal": deal_to_context(deal)}
    with pytest.raises(AgentValidationError, match="18.5%"):
        agent_cls(FakeLLM("Revenue would rise 18.5%.")).run(ctx)
    ok = "OBSERVED EVIDENCE: the target reports EBITDA of 18.0."
    assert agent_cls(FakeLLM(ok)).run(ctx) == ok


@dd_param
def test_dd_agents_accept_nested_deterministic_values(agent_cls, deal):
    ctx = {"deal": deal_to_context(deal),
           "deterministic_calculations": {"valuation": build_valuation_context(deal),
                                          "synergy": build_synergy_context(deal)}}
    ok = "Target net debt is 20.0; net synergy value is 13.5878."
    assert agent_cls(FakeLLM(ok)).run(ctx) == ok


# --- Headings -------------------------------------------------------------------

HEADINGS = {
    FinancialDueDiligence: [
        "# Financial Due Diligence", "## Observed Financial Evidence",
        "## Quality of Earnings", "## Cash Flow & Balance Sheet Review",
        "## Key Financial Red Flags", "## Information Gaps",
        "## Recommended Diligence Actions"],
    CommercialDueDiligence: [
        "# Commercial Due Diligence", "## Observed Commercial Evidence",
        "## Market & Competitive Position", "## Customer Quality",
        "## Product & Revenue Model", "## Commercial Synergy Validation",
        "## Key Commercial Red Flags", "## Information Gaps",
        "## Recommended Diligence Actions"],
    LegalOperationalDueDiligence: [
        "# Legal & Operational Due Diligence", "## Observed Evidence",
        "## Legal & Regulatory Review", "## Technology & Operations Review",
        "## Integration Considerations", "## Key Legal / Operational Red Flags",
        "## Information Gaps", "## Recommended Diligence Actions"],
}


@dd_param
def test_output_headings_in_order(agent_cls):
    p = prompt(agent_cls)
    output_format = p[p.index("OUTPUT FORMAT"):]
    positions = [output_format.index(h) for h in HEADINGS[agent_cls]]
    assert positions == sorted(positions)


def test_financial_dd_exact_no_red_flag_sentence():
    assert ('"No evidence-backed financial red flag can be concluded from the supplied\n'
            'data alone."') in prompt(FinancialDueDiligence)


# --- Evidence discipline ------------------------------------------------------------

@dd_param
def test_four_evidence_categories_defined(agent_cls):
    p = prompt(agent_cls)
    for label in ["OBSERVED EVIDENCE:", "UNVERIFIED ASSUMPTION:", "INFORMATION GAP:",
                  "EVIDENCE-BACKED RED FLAG:"]:
        assert label in p
    assert "Every red flag must cite the specific supplied evidence" in p


@dd_param
def test_missing_information_is_gap_not_red_flag(agent_cls):
    p = prompt(agent_cls)
    assert "Missing information is an INFORMATION GAP, never evidence that something is wrong." in p
    assert 'Do NOT transform "information unavailable" into a risk' in p
    assert "cannot yet be\n  assessed" in p
    assert 'BAD: "The target has customer concentration risk."' in p
    assert "contractual risk remains unassessed" in p


@dd_param
def test_no_risk_scoring(agent_cls):
    p = prompt(agent_cls)
    assert "Do NOT assign numeric risk scores, probability percentages, likelihoods, or\n  High / Medium / Low (or similar) risk ratings." in p


@dd_param
def test_prior_analyses_never_evidence(agent_cls):
    p = prompt(agent_cls)
    assert "A prior agent's claim is never\n  OBSERVED EVIDENCE and can never support an EVIDENCE-BACKED RED FLAG." in p


@dd_param
def test_no_proceed_pass_and_diligence_action_language(agent_cls):
    p = prompt(agent_cls)
    assert "Do NOT issue a proceed/pass recommendation." in p
    assert 'Write next steps as "Recommended diligence action: ...".' in p


# --- Agent-specific prohibitions -----------------------------------------------------

def test_commercial_dd_cannot_infer_industry_from_name():
    p = prompt(CommercialDueDiligence)
    assert "Do NOT infer the target's industry, product category, market, customers," in p
    for source in ["  - company names;", "  - financial performance", "  - prior-agent speculation"]:
        assert source in p
    assert ("GOOD: \"The target's industry and product category are not supplied, so market\n"
            "    structure cannot be assessed.\"") in p


def flat(text):
    """Collapse line wrapping so phrase checks don't depend on prompt line breaks."""
    return " ".join(text.split())


def test_financial_dd_cannot_invent_qoe_working_capital_capex():
    p = prompt(FinancialDueDiligence)
    assert "DO NOT INVENT FINANCIAL DILIGENCE DATA" in p
    section = flat(p.split("DO NOT INVENT FINANCIAL DILIGENCE DATA")[1]
                   .split("DETERMINISTIC CALCULATIONS:")[0])
    for item in ["working capital", "capex", "recurring revenue", "customer concentration",
                 "accounting adjustments", "tax liabilities", "quality-of-earnings (QoE) adjustments",
                 "normalization adjustments", "historical trends"]:
        assert item in section, item
    assert "A single reported period cannot show a trend." in p
    assert "Do NOT compute or characterize ratios such as margins, cash conversion, leverage" in p


def test_legal_operational_dd_cannot_invent_issues():
    p = prompt(LegalOperationalDueDiligence)
    section = flat(p.split("DO NOT INVENT LEGAL OR OPERATIONAL ISSUES:")[1]
                   .split("INTEGRATION CONSIDERATIONS:")[0])
    for issue in ["litigation", "regulatory breaches", "IP disputes", "cybersecurity vulnerabilities",
                  "contractual problems", "employee attrition", "system incompatibility",
                  "integration issues"]:
        assert issue in section, issue


def test_legal_operational_dd_does_not_judge_integration_plan():
    p = prompt(LegalOperationalDueDiligence)
    assert "Do NOT infer that the integration plan, budget, or timeline is adequate" in p
    assert "integration cost and realization period in DEAL DATA may be quoted as\n  UNVERIFIED ASSUMPTIONS" in p


def test_synergy_assumptions_remain_assumptions():
    assert ("The revenue-synergy figure in DEAL DATA is an UNVERIFIED ASSUMPTION (DEAL DATA\n"
            "  ASSUMPTION) requiring validation.") in prompt(CommercialDueDiligence)
    assert "UNVERIFIED ASSUMPTIONS (DEAL DATA ASSUMPTIONS)" in prompt(LegalOperationalDueDiligence)
    assert "depends on UNVERIFIED ASSUMPTIONS" in prompt(FinancialDueDiligence)


# --- Context separation ---------------------------------------------------------------

@dd_param
def test_prior_analyses_stay_separate_from_source_data(agent_cls, deal):
    llm = FakeLLM("ok")
    deal_ctx = deal_to_context(deal)
    calcs = {"valuation": build_valuation_context(deal), "synergy": build_synergy_context(deal)}
    agent_cls(llm).run({"deal": deal_ctx, "deterministic_calculations": calcs,
                        "prior_analyses": {"Deal Lead": "HYPOTHESIS TEXT"}})
    p = llm.calls[0]["user_prompt"]
    deal_json, calc_json = json.dumps(deal_ctx, indent=2), json.dumps(calcs, indent=2)
    assert p.index("=== DEAL DATA") < p.index(deal_json) < p.index("=== DETERMINISTIC CALCULATIONS")
    assert p.index("=== DETERMINISTIC CALCULATIONS") < p.index(calc_json) < p.index("=== PRIOR ANALYSES")
    assert p.count("HYPOTHESIS TEXT") == 1
    assert p.index("HYPOTHESIS TEXT") > p.index("=== PRIOR ANALYSES")


# --- Structural runner test -----------------------------------------------------------

PRE_DD = ["Deal Lead", "Strategic Fit Analyst", "Valuation Analyst", "Synergy Analyst"]
DD = ["Financial Due Diligence", "Commercial Due Diligence", "Legal & Operational Due Diligence"]


@pytest.fixture(scope="module")
def pipeline_run(deal):
    llm = FakeLLM()
    reported = []
    orchestrator = DealTeamOrchestrator(
        llm, on_event=lambda e: reported.append((STEP_LABELS[e.agent], e.agent))
        if isinstance(e, AgentRun) else None)
    outputs = orchestrator.run_initial(deal).outputs
    by_agent = {re.match(r"You are the (.+?), ", c["system_prompt"]).group(1): c for c in llm.calls}
    return llm, outputs, reported, by_agent


def test_pipeline_order_and_labels(pipeline_run):
    _, outputs, reported, _ = pipeline_run
    post_dd = ["Deal Structuring Analyst", "Risk Analyst"]
    assert reported == list(zip(["1", "2", "3", "4", "5A", "5B", "5C", "6", "7"],
                                PRE_DD + DD + post_dd))
    assert list(outputs) == PRE_DD + DD + post_dd


def test_pipeline_uses_one_shared_client(pipeline_run):
    llm, _, _, by_agent = pipeline_run
    assert len(llm.calls) == 9
    assert set(PRE_DD + DD) <= set(by_agent)


@pytest.mark.parametrize("dd_name", DD)
def test_dd_agents_receive_all_pre_dd_outputs(pipeline_run, dd_name):
    _, outputs, _, by_agent = pipeline_run
    p = by_agent[dd_name]["user_prompt"]
    for name in PRE_DD:
        assert f"--- {name} ---\n{outputs[name]}" in p


@pytest.mark.parametrize("dd_name", DD)
def test_dd_agents_never_see_other_dd_outputs(pipeline_run, dd_name):
    _, outputs, _, by_agent = pipeline_run
    p = by_agent[dd_name]["user_prompt"]
    for other in DD:
        assert outputs[other] not in p, f"{other} output leaked into {dd_name}"
        assert f"--- {other} ---" not in p


def test_dd_agents_receive_identical_context(pipeline_run):
    _, _, _, by_agent = pipeline_run
    prompts = {by_agent[name]["user_prompt"].split("=== TASK ===")[0] for name in DD}
    assert len(prompts) == 1


def test_dd_agents_receive_nested_valuation_and_synergy_calculations(pipeline_run, deal):
    _, _, _, by_agent = pipeline_run
    expected = {"valuation": build_valuation_context(deal), "synergy": build_synergy_context(deal)}
    for name in DD:
        assert json.dumps(expected, indent=2) in by_agent[name]["user_prompt"]
