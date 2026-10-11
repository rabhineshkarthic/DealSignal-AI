"""Phase 4 memo tests. Scripted fake LLM only: no Gemini, no API key, no network."""

import json
import re
from dataclasses import replace
from pathlib import Path

import pytest

from agent_validation import AgentValidationError
from agents import FINANCIAL_DATA_RULES, BaseAgent, ICMemoWriter
from agents.ic_memo_writer import CONCLUSION_HEADING, MEMO_HEADINGS, PERMITTED_CONCLUSIONS
from agents.registry import (
    AGENT_NAMES,
    COMMERCIAL_DD,
    FINANCIAL_DD,
    LEGAL_OPS_DD,
    RISK,
    STRUCTURING,
)
from memo import (
    InvestmentCommitteeMemoGenerator,
    MemoGenerationError,
    MemoParseError,
    MemoResult,
    check_memo_eligibility,
    parse_memo_response,
)
from models import load_deal
from orchestrator import APPROVED, REVIEW_LIMIT_REACHED, DealTeamOrchestrator

ROOT = Path(__file__).resolve().parent.parent
SAMPLE_DEAL_PATH = ROOT / "data" / "sample_deal.json"
IC, WRITER = "IC Partner", "IC Memo Writer"
VERSION = "ABCDEFGHIJ"


def memo_markdown(conclusion=PERMITTED_CONCLUSIONS[0], extra=""):
    body = []
    for heading in MEMO_HEADINGS:
        body.append(heading)
        if heading == CONCLUSION_HEADING:
            body.append(f"{conclusion}. The reviewed work leaves material information gaps.")
        else:
            body.append("Synthesis of the reviewed analyses." + extra)
    return "\n\n".join(body)


def memo_json(conclusion=PERMITTED_CONCLUSIONS[0], memo=None, **extra_keys):
    return json.dumps({"conclusion": conclusion,
                       "memo": memo if memo is not None else memo_markdown(conclusion),
                       **extra_keys})


def approved(gaps=("Customer data is not supplied.",)):
    return json.dumps({"status": "approved", "summary": "Quality criteria met.",
                       "revision_requests": [], "cross_workstream_issues": [],
                       "unresolved_information_gaps": list(gaps)})


def revise(*agents):
    return json.dumps({
        "status": "revision_required", "summary": "Revisions needed.",
        "revision_requests": [{"agent": a, "reason": f"Problem in {a}.",
                               "required_change": f"Fix {a}."} for a in agents],
        "cross_workstream_issues": [], "unresolved_information_gaps": []})


class ScriptedLLM:
    def __init__(self, ic_responses=(), memo_response=None):
        self.ic_responses = list(ic_responses)
        self.memo_response = memo_response if memo_response is not None else memo_json()
        self.calls = []
        self.versions = {}

    def generate(self, system_prompt, user_prompt):
        agent = re.match(r"You are the (.+?), ", system_prompt).group(1)
        self.calls.append((agent, user_prompt))
        if agent == IC:
            return self.ic_responses.pop(0)
        if agent == WRITER:
            return self.memo_response
        n = self.versions.get(agent, 0)
        self.versions[agent] = n + 1
        return f"{agent.upper()} VERSION {VERSION[n]}"

    def prompts_for(self, agent):
        return [p for a, p in self.calls if a == agent]


def v(agent, n):
    return f"{agent.upper()} VERSION {VERSION[n]}"


@pytest.fixture(autouse=True)
def no_api_key(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)


@pytest.fixture(scope="module")
def deal():
    return load_deal(SAMPLE_DEAL_PATH)


@pytest.fixture(scope="module")
def approved_result(deal):
    return DealTeamOrchestrator(ScriptedLLM([approved()])).run(deal)


@pytest.fixture(scope="module")
def revised_result(deal):
    """Financial DD revised in round one, approved in round two."""
    return DealTeamOrchestrator(ScriptedLLM([revise(FINANCIAL_DD), approved()])).run(deal)


def generate(result, deal, memo_response=None):
    llm = ScriptedLLM(memo_response=memo_response)
    memo = InvestmentCommitteeMemoGenerator(llm).generate(result, deal)
    return memo, llm


def writer_prompt():
    return ICMemoWriter(ScriptedLLM()).build_system_prompt()


def flat(text):
    return " ".join(text.split())


# --- Memo writer agent ---------------------------------------------------------------

def test_writer_subclasses_base_agent_with_validated_run():
    assert issubclass(ICMemoWriter, BaseAgent)
    assert ICMemoWriter.run is BaseAgent.run


def test_writer_inherits_shared_rules():
    p = writer_prompt()
    assert FINANCIAL_DATA_RULES in p
    assert "NO ARITHMETIC RULE" in p
    assert "Never promote PRIOR ANALYSES into DEAL DATA or DETERMINISTIC CALCULATIONS." in p
    assert "A company name may be repeated, but it is not evidence of anything." in p
    assert "IC REVIEW CONTEXT, when present" in p


def test_writer_prompt_lists_all_headings_in_order():
    p = writer_prompt()
    fmt = p[p.index("OUTPUT FORMAT"):]
    positions = [fmt.index(h + "\n") if h != CONCLUSION_HEADING else fmt.index(h)
                 for h in MEMO_HEADINGS]
    assert positions == sorted(positions)


def test_writer_prompt_content_rules():
    p = flat(writer_prompt())
    for phrase in [
        "debt funding of the cash portion is an existing modelling convention, not a "
        "confirmed financing plan",
        "Synergy assumptions remain unverified; never present them as validated.",
        "never present the synergy-inclusive case as the expected outcome",
        "You must NOT calculate premiums, discounts, combined financials, margins, ratios, "
        "payback, leverage, ownership, EPS, accretion/dilution, synergy totals, valuation "
        "averages, or implied multiples.",
        "Never turn an information gap into a factual risk.",
        "Do not infer industry, product, or market from company names.",
        'Do NOT use equity-research or promotional language such as "strong buy", '
        '"highly attractive"',
        "Do NOT give any score, probability of success, confidence percentage, or High / "
        "Medium / Low rating.",
        "Do NOT reach a conclusion based solely on EPS accretion, the offer being within "
        "the valuation range, or a positive synergy value.",
        "not financial advice",
        "No invented quantitative thresholds.",
    ]:
        assert phrase in p, phrase
    for conclusion in PERMITTED_CONCLUSIONS:
        assert f'"{conclusion}"' in p


# --- Phase-3 gate ------------------------------------------------------------------------------

def test_gate_accepts_approved_result(approved_result):
    assert check_memo_eligibility(approved_result).approved


def test_gate_rejects_review_limit_reached(deal):
    unresolved = DealTeamOrchestrator(ScriptedLLM([revise(RISK), revise(RISK)])).run(deal)
    assert unresolved.status == REVIEW_LIMIT_REACHED
    llm = ScriptedLLM()
    with pytest.raises(MemoGenerationError, match="remains unresolved"):
        InvestmentCommitteeMemoGenerator(llm).generate(unresolved, deal)
    assert llm.calls == []  # no memo call is made


def test_gate_rejects_none_status(approved_result):
    with pytest.raises(MemoGenerationError, match="no final status"):
        check_memo_eligibility(replace(approved_result, status=None))


def test_gate_rejects_unknown_status(approved_result):
    with pytest.raises(MemoGenerationError, match="unsupported"):
        check_memo_eligibility(replace(approved_result, status="proceed"))


@pytest.mark.parametrize("agent", [FINANCIAL_DD, RISK])
def test_gate_rejects_missing_agent_output(approved_result, agent):
    outputs = {k: val for k, val in approved_result.outputs.items() if k != agent}
    with pytest.raises(MemoGenerationError, match="missing analytical outputs"):
        check_memo_eligibility(replace(approved_result, outputs=outputs))


@pytest.mark.parametrize("key", ["valuation", "synergy", "transaction_structure"])
def test_gate_rejects_missing_deterministic_context(approved_result, key):
    contexts = {k: val for k, val in approved_result.deterministic_contexts.items() if k != key}
    with pytest.raises(MemoGenerationError, match="missing deterministic contexts"):
        check_memo_eligibility(replace(approved_result, deterministic_contexts=contexts))


def test_gate_rejects_missing_ic_review(approved_result):
    with pytest.raises(MemoGenerationError, match="no IC review"):
        check_memo_eligibility(replace(approved_result, review_history=[]))


def test_gate_rejects_final_review_that_is_not_an_approval(deal, approved_result):
    unresolved = DealTeamOrchestrator(ScriptedLLM([revise(RISK), revise(RISK)])).run(deal)
    tampered = replace(approved_result, review_history=unresolved.review_history)
    with pytest.raises(MemoGenerationError, match="not an approval"):
        check_memo_eligibility(tampered)


# --- Conclusion schema ------------------------------------------------------------------------

@pytest.mark.parametrize("conclusion", PERMITTED_CONCLUSIONS)
def test_all_permitted_conclusions_accepted(conclusion):
    parsed_conclusion, memo = parse_memo_response(memo_json(conclusion))
    assert parsed_conclusion == conclusion
    assert memo.startswith("# Investment Committee Memorandum")


@pytest.mark.parametrize("conclusion", ["Proceed", "Strong buy", "proceed to further diligence",
                                        "Pass", "", None])
def test_other_conclusions_rejected(conclusion):
    with pytest.raises(MemoParseError, match="not one of"):
        parse_memo_response(memo_json(conclusion, memo=memo_markdown()))


def test_extra_keys_rejected():
    with pytest.raises(MemoParseError, match="unexpected keys"):
        parse_memo_response(memo_json(score="n/a"))


@pytest.mark.parametrize("key", ["conclusion", "memo"])
def test_missing_keys_rejected(key):
    data = json.loads(memo_json())
    del data[key]
    with pytest.raises(MemoParseError, match="missing keys"):
        parse_memo_response(json.dumps(data))


@pytest.mark.parametrize("memo", ["", "   ", 7])
def test_empty_memo_rejected(memo):
    with pytest.raises(MemoParseError, match="non-empty"):
        parse_memo_response(memo_json(memo=memo))


@pytest.mark.parametrize("text", ["not json", "```json\n" + memo_json() + "\n```", "[]"])
def test_malformed_json_rejected(text):
    with pytest.raises(MemoParseError):
        parse_memo_response(text)


def test_missing_heading_rejected():
    memo = memo_markdown().replace("## Synergies", "## Synergy Notes")
    with pytest.raises(MemoParseError, match="missing required heading '## Synergies'"):
        parse_memo_response(memo_json(memo=memo))


def test_heading_order_enforced():
    memo = memo_markdown().replace("## Valuation", "## TMP").replace(
        "## Synergies", "## Valuation").replace("## TMP", "## Synergies")
    with pytest.raises(MemoParseError, match="order"):
        parse_memo_response(memo_json(memo=memo))


def test_conclusion_section_must_match_conclusion_field():
    memo = memo_markdown(PERMITTED_CONCLUSIONS[1])
    with pytest.raises(MemoParseError, match="does not state the conclusion field"):
        parse_memo_response(memo_json(PERMITTED_CONCLUSIONS[0], memo=memo))


def test_conclusion_section_must_not_name_two_conclusions():
    memo = memo_markdown(PERMITTED_CONCLUSIONS[0]) + f"\nAlternatively: {PERMITTED_CONCLUSIONS[2]}."
    with pytest.raises(MemoParseError, match="also names other conclusions"):
        parse_memo_response(memo_json(PERMITTED_CONCLUSIONS[0], memo=memo))


# --- Source discipline -----------------------------------------------------------------------------

def test_deal_data_number_accepted(approved_result, deal):
    memo, _ = generate(approved_result, deal, memo_json(memo=memo_markdown(
        extra=" The target reports revenue of 120.0.")))
    assert "120.0" in memo.memo_text


def test_deterministic_number_accepted(approved_result, deal):
    memo, _ = generate(approved_result, deal, memo_json(memo=memo_markdown(
        extra=" DCF EV 172.024; net synergy value 13.5878; new shares issued 2.96.")))
    assert "13.5878" in memo.memo_text


def test_number_only_in_final_agent_analysis_rejected(deal):
    result = DealTeamOrchestrator(ScriptedLLM([approved()])).run(deal)
    tainted_text = result.outputs[RISK] + " cites 777"
    runs = [r if r.agent != RISK else replace(r, output=tainted_text) for r in result.agent_runs]
    tainted = replace(result, outputs={**result.outputs, RISK: tainted_text}, agent_runs=runs)
    with pytest.raises(AgentValidationError, match="777"):
        generate(tainted, deal, memo_json(memo=memo_markdown(extra=" Risk cited 777.")))


def test_ic_partner_cannot_record_an_unapproved_number(deal):
    """First line of defence: the IC Partner's own output is numerically validated."""
    from orchestrator import OrchestrationError
    bad = approved(gaps=["A gap of 888 remains."])
    with pytest.raises(OrchestrationError, match="888") as exc_info:
        DealTeamOrchestrator(ScriptedLLM([bad, bad, bad])).run(deal)  # every retry invalid too
    assert exc_info.value.partial_result.review_history == []


def test_number_only_in_ic_review_rejected(approved_result, deal):
    """Second line of defence: even if a review carried a number, it cannot authorize one."""
    last = approved_result.review_history[-1]
    tainted_review = replace(last.review, unresolved_information_gaps=("A gap of 888 remains.",))
    tainted = replace(approved_result,
                      review_history=[*approved_result.review_history[:-1],
                                      replace(last, review=tainted_review)])
    memo_llm = ScriptedLLM(memo_response=memo_json(memo=memo_markdown(extra=" Gap of 888.")))
    generator = InvestmentCommitteeMemoGenerator(memo_llm)
    with pytest.raises(AgentValidationError, match="888"):
        generator.generate(tainted, deal)
    (prompt,) = memo_llm.prompts_for(WRITER)
    assert "A gap of 888 remains." in prompt.split("=== IC REVIEW CONTEXT")[1]


def test_number_only_in_historical_output_rejected(deal, revised_result):
    # Replace the original (historical) Financial DD text with one containing a number.
    runs = list(revised_result.agent_runs)
    first = next(i for i, r in enumerate(runs) if r.agent == FINANCIAL_DD)
    runs[first] = replace(runs[first], output="Stale analysis quoted 999.")
    tainted = replace(revised_result, agent_runs=runs)
    with pytest.raises(AgentValidationError, match="999"):
        generate(tainted, deal, memo_json(memo=memo_markdown(extra=" Stale value 999.")))


def test_numbers_hidden_behind_json_escapes_are_caught(approved_result, deal):
    raw = json.dumps({"conclusion": PERMITTED_CONCLUSIONS[0],
                      "memo": memo_markdown(extra="\n25% of revenue")})
    assert "\\n25%" in raw  # the raw text hides the number behind an escape
    with pytest.raises(AgentValidationError, match="25%"):
        generate(approved_result, deal, raw)


def test_review_context_is_non_evidence_and_separate(approved_result, deal):
    _, llm = generate(approved_result, deal)
    (prompt,) = llm.prompts_for(WRITER)
    header = "=== IC REVIEW CONTEXT (quality-control context; not source evidence) ==="
    assert header in prompt
    assert prompt.index("=== PRIOR ANALYSES") < prompt.index(header) < prompt.index("=== TASK")
    assert "Customer data is not supplied." in prompt.split(header)[1]
    assert "Customer data is not supplied." not in prompt.split("=== DETERMINISTIC")[0]


# --- Final-state routing ---------------------------------------------------------------------------

def test_writer_receives_current_outputs_not_stale_versions(revised_result, deal):
    assert revised_result.status == APPROVED
    memo, llm = generate(revised_result, deal)
    (prompt,) = llm.prompts_for(WRITER)
    for current in [v(FINANCIAL_DD, 1), v(STRUCTURING, 1), v(RISK, 1),
                    v(COMMERCIAL_DD, 0), v(LEGAL_OPS_DD, 0)]:
        assert current in prompt, current
    for stale in [v(FINANCIAL_DD, 0), v(STRUCTURING, 0), v(RISK, 0)]:
        assert stale not in prompt, stale
    refs = {r.agent: r for r in memo.source_agent_versions}
    assert (refs[FINANCIAL_DD].version, refs[FINANCIAL_DD].kind) == (2, "direct_revision")
    assert (refs[STRUCTURING].version, refs[STRUCTURING].kind) == (2, "stale_rerun")
    assert (refs[RISK].version, refs[RISK].kind) == (2, "stale_rerun")
    assert (refs[COMMERCIAL_DD].version, refs[COMMERCIAL_DD].kind) == (1, "initial")
    assert memo.generated_from_review_round == 2


def test_writer_receives_each_agent_once_and_full_calculations(approved_result, deal):
    _, llm = generate(approved_result, deal)
    (prompt,) = llm.prompts_for(WRITER)
    for name in AGENT_NAMES:
        assert prompt.count(f"--- {name} ---") == 1
    assert json.dumps(approved_result.deterministic_contexts, indent=2) in prompt


# --- Integration ----------------------------------------------------------------------------------

def test_approved_orchestration_to_memo(deal):
    llm = ScriptedLLM([approved()], memo_response=memo_json(PERMITTED_CONCLUSIONS[3]))
    result = DealTeamOrchestrator(llm).run(deal)
    memo = InvestmentCommitteeMemoGenerator(llm).generate(result, deal)
    assert isinstance(memo, MemoResult)
    assert memo.conclusion == "Decision deferred pending additional information"
    assert memo.orchestration_status == APPROVED
    assert memo.deal_name == deal.deal_name
    assert memo.review_rounds_used == 1 and memo.generated_from_review_round == 1
    assert [r.agent for r in memo.source_agent_versions] == list(AGENT_NAMES)
    assert [a for a, _ in llm.calls] == list(AGENT_NAMES) + [IC, WRITER]


def test_memo_generation_does_not_mutate_result(approved_result, deal):
    before = (dict(approved_result.outputs), len(approved_result.agent_runs),
              len(approved_result.review_history))
    generate(approved_result, deal)
    assert before == (dict(approved_result.outputs), len(approved_result.agent_runs),
                      len(approved_result.review_history))
