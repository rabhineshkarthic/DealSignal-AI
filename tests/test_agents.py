"""Agent tests using a fake LLM client. No network calls, no API key required."""

import json
from pathlib import Path

import pytest

from agents import (
    FINANCIAL_DATA_RULES,
    BaseAgent,
    DealLead,
    StrategicFit,
    SynergyAnalyst,
    ValuationAnalyst,
)
from models import deal_to_context, load_deal

ROOT = Path(__file__).resolve().parent.parent
SAMPLE_DEAL_PATH = ROOT / "data" / "sample_deal.json"


class FakeLLM:
    """Records every call and returns a canned response."""

    def __init__(self, response: str = "fake response") -> None:
        self.response = response
        self.calls: list[dict[str, str]] = []

    def generate(self, system_prompt: str, user_prompt: str) -> str:
        self.calls.append({"system_prompt": system_prompt, "user_prompt": user_prompt})
        return self.response


@pytest.fixture(autouse=True)
def no_api_key(monkeypatch):
    """Prove agents run without any Gemini credentials."""
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)


@pytest.fixture(scope="module")
def deal_context():
    return deal_to_context(load_deal(SAMPLE_DEAL_PATH))


# --- Context helper -----------------------------------------------------------

def test_deal_to_context_mirrors_sample_json(deal_context):
    raw = json.loads(SAMPLE_DEAL_PATH.read_text())
    raw.pop("disclaimer")
    assert deal_context == raw


# --- BaseAgent ----------------------------------------------------------------

def test_base_agent_passes_context_to_llm(deal_context):
    llm = FakeLLM("ok")
    result = BaseAgent(llm).run({"deal": deal_context})

    assert result == "ok"
    assert len(llm.calls) == 1
    user_prompt = llm.calls[0]["user_prompt"]
    assert "=== DEAL DATA" in user_prompt
    assert json.dumps(deal_context, indent=2) in user_prompt


def test_base_agent_includes_prior_analyses(deal_context):
    llm = FakeLLM()
    BaseAgent(llm).run({"deal": deal_context, "prior_analyses": {"Deal Lead": "THESIS TEXT"}})

    user_prompt = llm.calls[0]["user_prompt"]
    assert "=== PRIOR ANALYSES" in user_prompt
    assert "--- Deal Lead ---\nTHESIS TEXT" in user_prompt


def test_base_agent_requires_deal():
    with pytest.raises(ValueError, match="'deal'"):
        BaseAgent(FakeLLM()).run({"prior_analyses": {}})


def test_base_agent_rejects_unknown_context_keys(deal_context):
    with pytest.raises(ValueError, match="unexpected context keys"):
        BaseAgent(FakeLLM()).run({"deal": deal_context, "market_size": 20_000})


# --- Guardrails ---------------------------------------------------------------

@pytest.mark.parametrize("agent_cls", [BaseAgent, DealLead, StrategicFit])
def test_system_prompt_contains_financial_data_rules(agent_cls):
    prompt = agent_cls(FakeLLM()).build_system_prompt()
    assert FINANCIAL_DATA_RULES in prompt
    for rule in [
        "Never fabricate a financial figure.",
        "Never calculate valuation results yourself",
        "Use only figures supplied in DEAL DATA or DETERMINISTIC CALCULATIONS.",
        "explicitly say that it is unavailable",
        "labelled explicitly as an ASSUMPTION",
        "Do not present assumptions as facts.",
    ]:
        assert rule in prompt


def test_guardrails_sent_on_every_call(deal_context):
    llm = FakeLLM()
    DealLead(llm).run({"deal": deal_context})
    StrategicFit(llm).run({"deal": deal_context})
    assert all(FINANCIAL_DATA_RULES in c["system_prompt"] for c in llm.calls)


def test_deal_lead_defers_final_recommendation():
    prompt = DealLead(FakeLLM()).build_system_prompt()
    assert "Do NOT issue a proceed/pass recommendation" in prompt
    assert "market leadership cannot yet be assessed" in prompt


def test_strategic_fit_requires_explicit_missing_data_statements():
    prompt = StrategicFit(FakeLLM()).build_system_prompt()
    assert "cannot be assessed from the supplied data" in prompt


# --- Concrete agents ----------------------------------------------------------

def test_deal_lead_calls_injected_client(deal_context):
    llm = FakeLLM("# Preliminary Acquisition Thesis")
    result = DealLead(llm).run({"deal": deal_context})

    assert result == "# Preliminary Acquisition Thesis"
    assert len(llm.calls) == 1
    assert "# Preliminary Acquisition Thesis" in llm.calls[0]["system_prompt"]
    assert "Northstar Software" in llm.calls[0]["user_prompt"]
    assert "Atlas Analytics" in llm.calls[0]["user_prompt"]


def test_strategic_fit_calls_injected_client_with_deal_lead_output(deal_context):
    llm = FakeLLM("# Strategic Fit Assessment")
    result = StrategicFit(llm).run(
        {"deal": deal_context, "prior_analyses": {"Deal Lead": "Preliminary thesis here."}}
    )

    assert result == "# Strategic Fit Assessment"
    assert len(llm.calls) == 1
    assert "# Strategic Fit Assessment" in llm.calls[0]["system_prompt"]
    assert "Preliminary thesis here." in llm.calls[0]["user_prompt"]


def test_agents_share_one_injected_client(deal_context):
    llm = FakeLLM()
    lead, fit = DealLead(llm), StrategicFit(llm)
    assert lead.llm_client is fit.llm_client is llm
    lead.run({"deal": deal_context})
    fit.run({"deal": deal_context})
    assert len(llm.calls) == 2


@pytest.mark.parametrize("path", sorted((ROOT / "agents").rglob("*.py")),
                         ids=lambda p: str(p.relative_to(ROOT / "agents")))
def test_agent_files_do_not_hardcode_deal_data(path):
    source = path.read_text()
    for literal in ["Northstar", "Atlas", "650.0", "130.0", "120.0", "18.0", "205.0",
                    "189.0", "216.0", "172.02", "185.0", "6.75", "25.58", "13.58"]:
        assert literal not in source


# --- No-arithmetic rule -------------------------------------------------------

ALL_AGENT_CLASSES = [BaseAgent, *BaseAgent.__subclasses__()]

NO_ARITHMETIC_PHRASES = [
    "NO ARITHMETIC RULE",
    "You must NOT perform any financial arithmetic.",
    "add, subtract, multiply, or divide figures",
    "calculate percentages, ratios, multiples, or margins",
    "calculate combined or pro forma figures",
    "calculate market capitalization",
    "total or net synergies",
    "calculate premiums",
    "infer or hypothesize accretion or dilution",
    "derive any valuation metric",
    "must be supplied by the deterministic calculation layer",
]


@pytest.mark.parametrize("agent_cls", ALL_AGENT_CLASSES, ids=lambda c: c.__name__)
def test_every_agent_inherits_no_arithmetic_rule(agent_cls):
    prompt = agent_cls(FakeLLM()).build_system_prompt()
    for phrase in NO_ARITHMETIC_PHRASES:
        assert phrase in prompt, phrase


def test_no_arithmetic_rule_cannot_be_dropped_by_a_subclass():
    class NewAgent(BaseAgent):
        name = "New Agent"
        system_prompt = "Some task-specific instructions."

    prompt = NewAgent(FakeLLM()).build_system_prompt()
    assert FINANCIAL_DATA_RULES in prompt
    assert prompt.index("NO ARITHMETIC RULE") < prompt.index("Some task-specific instructions.")


@pytest.mark.parametrize("agent_cls", [DealLead, StrategicFit])
def test_agents_no_longer_invite_simple_comparisons(agent_cls):
    prompt = agent_cls.system_prompt.lower()
    assert "simple direct" not in prompt


def test_deal_lead_restrictions():
    prompt = DealLead(FakeLLM()).build_system_prompt()
    # accretion / dilution
    assert "Never state, predict, or hypothesize that the deal is accretive, dilutive" in prompt
    assert "deterministic accretion_dilution calculation" in prompt
    # proceed / pass
    assert "Do NOT issue a proceed/pass recommendation" in prompt
    assert "do not signal one indirectly" in prompt
    # synergies as supplied assumptions
    assert "Synergy figures in DEAL DATA are supplied assumptions, not validated value creation" in prompt
    assert "Do not total them" in prompt
    # two kinds of assumption
    assert "DEAL DATA ASSUMPTION" in prompt
    assert "AGENT ASSUMPTION" in prompt


def test_strategic_fit_restrictions():
    prompt = StrategicFit(FakeLLM()).build_system_prompt()
    assert "Do NOT calculate relative scale ratios or percentages" in prompt
    assert "Do NOT calculate market capitalization" in prompt
    assert "Do NOT make claims about financing capacity" in prompt
    assert "unless a deterministic financing analysis" in prompt
    assert "financing capacity has not yet been assessed" in prompt


# --- Deterministic calculations section -----------------------------------------

CALCS = {"valuation_summary": {"low": 1.5, "high": 2.5}, "transaction": {"offer_vs_range": "within"}}


def test_deterministic_calculations_rendered_in_separate_section(deal_context):
    llm = FakeLLM()
    BaseAgent(llm).run({"deal": deal_context, "deterministic_calculations": CALCS})
    user_prompt = llm.calls[0]["user_prompt"]

    deal_header = "=== DEAL DATA"
    calc_header = "=== DETERMINISTIC CALCULATIONS"
    assert user_prompt.count(calc_header) == 1
    calc_json = json.dumps(CALCS, indent=2)
    assert calc_json in user_prompt
    # calculations sit after DEAL DATA and are not merged into the DEAL DATA JSON
    assert user_prompt.index(deal_header) < user_prompt.index(calc_header) < user_prompt.index(calc_json)
    assert "valuation_summary" not in json.dumps(deal_context)


def test_sections_are_ordered_and_prior_analyses_stay_separate(deal_context):
    llm = FakeLLM()
    BaseAgent(llm).run({
        "deal": deal_context,
        "deterministic_calculations": CALCS,
        "prior_analyses": {"Deal Lead": "THESIS TEXT"},
    })
    p = llm.calls[0]["user_prompt"]
    order = [p.index(h) for h in (
        "=== DEAL DATA", "=== DETERMINISTIC CALCULATIONS", "=== PRIOR ANALYSES", "=== TASK")]
    assert order == sorted(order)
    # prior analysis text appears only in its own section
    assert p.index("THESIS TEXT") > p.index("=== PRIOR ANALYSES")
    assert "THESIS TEXT" not in json.dumps(CALCS)


def test_no_calculations_section_when_not_supplied(deal_context):
    llm = FakeLLM()
    BaseAgent(llm).run({"deal": deal_context})
    assert "=== DETERMINISTIC CALCULATIONS" not in llm.calls[0]["user_prompt"]


def test_shared_rules_define_trust_levels_for_each_section():
    for phrase in [
        "Figures from DEAL DATA may be quoted.",
        "Figures from DETERMINISTIC CALCULATIONS may be quoted.",
        "Figures from PRIOR ANALYSES are not trusted source data.",
        "Do not derive additional numbers yourself.",
    ]:
        assert phrase in FINANCIAL_DATA_RULES


# --- ValuationAnalyst ---------------------------------------------------------------

def test_valuation_analyst_inherits_all_shared_rules():
    prompt = ValuationAnalyst(FakeLLM()).build_system_prompt()
    assert FINANCIAL_DATA_RULES in prompt
    for phrase in NO_ARITHMETIC_PHRASES:
        assert phrase in prompt


def test_valuation_analyst_must_use_deterministic_outputs():
    prompt = ValuationAnalyst(FakeLLM()).build_system_prompt()
    assert "USE DETERMINISTIC OUTPUTS ONLY" in prompt
    assert "Quote valuation figures only as they appear in DETERMINISTIC" in prompt
    assert "say that it has\n  not been calculated" in prompt
    assert "no deterministic valuation\n  results were supplied" in prompt


def test_valuation_analyst_forbids_derived_metrics():
    prompt = ValuationAnalyst(FakeLLM()).build_system_prompt()
    assert "Do NOT calculate premiums, discounts, percentages" in prompt
    assert "implied multiples" in prompt
    assert "NOT ALLOWED: \"The implied premium is N%.\"" in prompt


def test_valuation_analyst_takes_ordering_only_from_python_labels():
    prompt = ValuationAnalyst(FakeLLM()).build_system_prompt()
    assert "Do NOT determine numerical ordering yourself" in prompt
    for label in ["low_method", "high_method", "offer_vs_range", "offer_vs_method"]:
        assert label in prompt


def test_valuation_analyst_does_not_propose_new_assumptions():
    prompt = ValuationAnalyst(FakeLLM()).build_system_prompt()
    assert "DEAL DATA ASSUMPTIONS" in prompt
    assert "Do NOT propose, select, or substitute any replacement numerical assumption" in prompt
    assert "Do not calculate or estimate sensitivities" in prompt
    assert "Do NOT issue a proceed/pass recommendation" in prompt


def test_valuation_analyst_output_headings():
    prompt = ValuationAnalyst(FakeLLM()).build_system_prompt()
    headings = ["# Valuation Assessment", "## Method Summary", "## Valuation Range",
                "## Offer Positioning", "## Key Assumptions", "## Valuation Risks",
                "## Information Required"]
    positions = [prompt.index(h) for h in headings]
    assert positions == sorted(positions)


def test_valuation_analyst_receives_all_three_sections(deal_context):
    from deal_analysis import build_valuation_context

    calcs = build_valuation_context(load_deal(SAMPLE_DEAL_PATH))
    llm = FakeLLM("# Valuation Assessment")
    result = ValuationAnalyst(llm).run({
        "deal": deal_context,
        "deterministic_calculations": calcs,
        "prior_analyses": {"Deal Lead": "A", "Strategic Fit Analyst": "B"},
    })

    assert result == "# Valuation Assessment"
    assert len(llm.calls) == 1
    p = llm.calls[0]["user_prompt"]
    assert json.dumps(deal_context, indent=2) in p
    assert json.dumps(calcs, indent=2) in p
    assert "--- Deal Lead ---\nA" in p
    assert "--- Strategic Fit Analyst ---\nB" in p


# --- SynergyAnalyst -----------------------------------------------------------------

def _synergy_prompt():
    return SynergyAnalyst(FakeLLM()).build_system_prompt()


def test_synergy_analyst_inherits_base_agent_rules():
    assert issubclass(SynergyAnalyst, BaseAgent)
    prompt = _synergy_prompt()
    assert FINANCIAL_DATA_RULES in prompt
    for phrase in NO_ARITHMETIC_PHRASES:
        assert phrase in prompt


def test_synergy_analyst_prohibits_synergy_arithmetic():
    prompt = _synergy_prompt()
    assert "SYNERGY ARITHMETIC IS PROHIBITED" in prompt
    for phrase in [
        "add cost synergy and revenue synergy",
        "subtract integration cost",
        "discount cash flows",
        "calculate synergy NPV",
        "calculate realization percentages",
        "calculate break-even periods",
        "calculate payback periods",
        "calculate synergy margins",
        "calculate synergy as a % of revenue or EBITDA",
        "calculate alternative cases",
        "calculate sensitivities",
        "must be produced by the\ndeterministic calculation layer",
    ]:
        assert phrase in prompt, phrase


def test_synergy_analyst_treats_synergies_as_assumptions():
    prompt = _synergy_prompt()
    assert "SYNERGIES ARE ASSUMPTIONS, NOT FACTS" in prompt
    assert "DEAL DATA ASSUMPTION" in prompt
    assert "has not been\n  validated" in prompt
    assert "proven, achievable, validated, realistic" in prompt
    assert "guaranteed unless DEAL DATA contains evidence" in prompt
    assert "Do not claim that any specific opportunity" in prompt


def test_synergy_analyst_uses_recommended_diligence_action_language():
    prompt = _synergy_prompt()
    assert '"Recommended diligence action": use this label for non-numerical next steps' in prompt
    assert "Never label a next step as an AGENT ASSUMPTION" in prompt


def test_synergy_analyst_takes_relationships_only_from_python_labels():
    prompt = _synergy_prompt()
    assert "labels.net_synergy_value_status" in prompt
    assert "labels.integration_cost_vs_pv_synergies" in prompt


def test_synergy_analyst_scope_and_headings():
    prompt = _synergy_prompt()
    assert "Do NOT issue a proceed/pass recommendation" in prompt
    headings = ["# Synergy Assessment", "## Supplied Synergy Assumptions",
                "## Deterministic Synergy Value", "## Cost Synergy Assessment",
                "## Revenue Synergy Assessment", "## Integration Risk", "## Key Risks",
                "## Information Required"]
    positions = [prompt.index(h) for h in headings]
    assert positions == sorted(positions)


def test_synergy_analyst_receives_separate_sections(deal_context):
    from deal_analysis import build_synergy_context

    synergy = build_synergy_context(load_deal(SAMPLE_DEAL_PATH))
    llm = FakeLLM("# Synergy Assessment")
    result = SynergyAnalyst(llm).run({
        "deal": deal_context,
        "deterministic_calculations": synergy,
        "prior_analyses": {"Deal Lead": "A", "Strategic Fit Analyst": "B",
                           "Valuation Analyst": "C"},
    })

    assert result == "# Synergy Assessment"
    assert len(llm.calls) == 1
    p = llm.calls[0]["user_prompt"]
    deal_json = json.dumps(deal_context, indent=2)
    synergy_json = json.dumps(synergy, indent=2)
    headers = ["=== DEAL DATA", "=== DETERMINISTIC CALCULATIONS", "=== PRIOR ANALYSES"]
    # each block sits in its own section, in order
    assert p.index(headers[0]) < p.index(deal_json) < p.index(headers[1])
    assert p.index(headers[1]) < p.index(synergy_json) < p.index(headers[2])
    for name, text in [("Deal Lead", "A"), ("Strategic Fit Analyst", "B"), ("Valuation Analyst", "C")]:
        assert p.index(f"--- {name} ---\n{text}") > p.index(headers[2])
    # synergy results were not merged into DEAL DATA
    assert "net_synergy_value" not in deal_json


# --- DealLead financing scope ---------------------------------------------------------

def _deal_lead_prompt():
    return DealLead(FakeLLM()).build_system_prompt()


def test_deal_lead_financing_is_out_of_scope():
    prompt = _deal_lead_prompt()
    assert "FINANCING AND CAPITAL STRUCTURE ARE OUT OF SCOPE" in prompt
    assert "Deal Structuring workstream" in prompt
    for term in ["financing", "capital-structure", "leverage", "refinancing",
                 "debt-capacity", "funding"]:
        assert term in prompt.split("FINANCING AND CAPITAL STRUCTURE ARE OUT OF SCOPE")[1], term
    assert "Do NOT propose or hypothesize financing, capital-structure, leverage, refinancing,\n  debt-capacity, or funding assumptions" in prompt


def test_deal_lead_must_not_infer_balance_sheet_capacity():
    prompt = _deal_lead_prompt()
    assert "Do NOT infer balance-sheet capacity" in prompt
    assert "from cash, debt, or any other figures" in prompt


def test_deal_lead_may_flag_financing_as_unknown():
    prompt = _deal_lead_prompt()
    assert "You MAY list financing as an unanswered question under Key Unknowns." in prompt
    assert "have not yet been\n    assessed and should be evaluated by the deterministic financing analysis" in prompt


def test_deal_lead_no_new_numerical_assumptions():
    prompt = _deal_lead_prompt()
    assert "Do NOT create new numerical financial assumptions" in prompt
    assert '"AGENT ASSUMPTION": only for a genuinely new, non-numerical' in prompt


def test_deal_lead_uses_recommended_diligence_action_language():
    prompt = _deal_lead_prompt()
    assert '"Recommended diligence action": use this label for qualitative next steps.' in prompt
    assert "Never label a next step as an AGENT ASSUMPTION." in prompt


def test_deal_lead_keeps_existing_rules():
    prompt = _deal_lead_prompt()
    assert "NO ARITHMETIC RULE" in prompt
    assert "Do NOT issue a proceed/pass recommendation" in prompt
    assert "Never state, predict, or hypothesize that the deal is accretive, dilutive" in prompt


# --- Source attribution (shared) --------------------------------------------------------

SOURCE_ATTRIBUTION_PHRASES = [
    "SOURCE ATTRIBUTION",
    "Never promote PRIOR ANALYSES into DEAL DATA or DETERMINISTIC CALCULATIONS.",
    "Attribute every claim to its true source.",
    "remains that agent's judgment. It is never a DEAL DATA\n  ASSUMPTION and never a fact.",
    "Never quote a figure or assumption that appears only in PRIOR ANALYSES as source data.",
    "Never infer a company's products, technologies, capabilities, industry, business\n  model, customers, markets, or strategic motives from its name.",
    "A company name may be repeated, but it is not evidence of anything.",
]


@pytest.mark.parametrize("agent_cls", ALL_AGENT_CLASSES, ids=lambda c: c.__name__)
def test_every_agent_inherits_source_attribution_rules(agent_cls):
    prompt = agent_cls(FakeLLM()).build_system_prompt()
    for phrase in SOURCE_ATTRIBUTION_PHRASES:
        assert phrase in prompt, phrase


# --- Problem 1: DealLead must not infer capabilities from a company name ----------------

def test_deal_lead_must_not_infer_from_company_name():
    prompt = _deal_lead_prompt()
    assert ("Do NOT infer products, technology, capabilities, customer value, or strategic\n"
            "  motives from a company name") in prompt


def test_deal_lead_states_product_rationale_cannot_be_assessed():
    prompt = _deal_lead_prompt()
    assert "a product- or capability-led rationale cannot yet be assessed" in prompt
    assert ("GOOD: \"The supplied data contains no product or capability information, so a\n"
            "    product-led strategic rationale cannot yet be assessed.\"") in prompt


def test_deal_lead_hypotheses_must_be_grounded_in_evidence():
    prompt = _deal_lead_prompt()
    assert "Every qualitative hypothesis must be grounded in specific supplied evidence" in prompt
    assert "list the underlying question under Key Unknowns instead" in prompt


def test_deal_lead_agent_assumption_cannot_carry_unsupported_thesis():
    prompt = _deal_lead_prompt()
    assert ("AGENT ASSUMPTION must NOT be used to introduce a specific product, technology,\n"
            "  capability, or customer thesis that DEAL DATA does not support.") in prompt
    assert "does not make it grounded" in prompt


# --- Problem 2: StrategicFit must not promote prior analyses ---------------------------

def _strategic_fit_prompt():
    return StrategicFit(FakeLLM()).build_system_prompt()


def test_strategic_fit_never_describes_prior_analyses_as_deal_data():
    prompt = _strategic_fit_prompt()
    assert "PRIOR ANALYSES ARE JUDGMENT, NOT SOURCE DATA" in prompt
    assert "PRIOR ANALYSES must never be described as DEAL DATA." in prompt
    assert 'Never write "DEAL DATA\n  provides/states/contains ..."' in prompt


def test_strategic_fit_keeps_prior_agent_assumptions_labelled_as_judgment():
    prompt = _strategic_fit_prompt()
    assert "must stay labelled as that\n  agent's judgment" in prompt
    assert "Never relabel it as a DEAL DATA\n  ASSUMPTION, a fact, or a finding." in prompt
    assert "evaluated critically but never promoted to source truth" in prompt


def test_strategic_fit_does_not_quote_prior_only_figures_as_source():
    prompt = _strategic_fit_prompt()
    assert ("Figures or assumptions appearing only in PRIOR ANALYSES must never be quoted as\n"
            "  source data.") in prompt


def test_strategic_fit_flags_unverified_prior_hypotheses():
    prompt = _strategic_fit_prompt()
    assert "say\n  explicitly that it remains unverified" in prompt
    assert "so this hypothesis remains unverified" in prompt
    assert "BAD: \"The DEAL DATA provides an ASSUMPTION" in prompt


def test_prior_analysis_text_is_only_in_prior_section(deal_context):
    """Structural guarantee: prior-agent text never lands in the DEAL DATA block."""
    llm = FakeLLM()
    hypothesis = "AGENT ASSUMPTION: motive inferred from name"
    StrategicFit(llm).run({"deal": deal_context, "prior_analyses": {"Deal Lead": hypothesis}})
    p = llm.calls[0]["user_prompt"]
    assert p.count(hypothesis) == 1
    assert p.index(hypothesis) > p.index("=== PRIOR ANALYSES")
