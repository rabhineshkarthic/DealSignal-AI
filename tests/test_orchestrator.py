"""Orchestrator tests: initial run, IC review loop, dependency cascade, audit trail.

Scripted fake LLM only. No Gemini, no API key, no network.
"""

import json
import re
from pathlib import Path

import pytest

import orchestrator as orch_module
from agent_validation import AgentValidationError
from agents.registry import (
    AGENT_NAMES,
    COMMERCIAL_DD,
    DD_PEERS,
    DEAL_LEAD,
    DEPENDENCIES,
    FINANCIAL_DD,
    LEGAL_OPS_DD,
    RISK,
    STRATEGIC_FIT,
    STRUCTURING,
    SYNERGY,
    VALUATION,
    dependents_of,
    rerun_plan,
)
from ic_review import ICReviewParseError
from llm_client import LLMError
from models import load_deal
from orchestrator import (
    APPROVED,
    REVIEW_LIMIT_REACHED,
    AgentRun,
    DealTeamOrchestrator,
    OrchestrationError,
    ReviewRound,
)

ROOT = Path(__file__).resolve().parent.parent
SAMPLE_DEAL_PATH = ROOT / "data" / "sample_deal.json"
IC = "IC Partner"
VERSION = "ABCDEFGHIJ"  # digit-free version tags so the numeric validator stays quiet


def approved():
    return json.dumps({"status": "approved", "summary": "Quality criteria met.",
                       "revision_requests": [], "cross_workstream_issues": [],
                       "unresolved_information_gaps": []})


def revise(*agents):
    return json.dumps({
        "status": "revision_required", "summary": "Revisions needed.",
        "revision_requests": [
            {"agent": a, "reason": f"Problem in {a}.", "required_change": f"Fix {a}."}
            for a in agents],
        "cross_workstream_issues": [], "unresolved_information_gaps": [],
    })


class ScriptedLLM:
    """Analytical agents answer with a versioned marker; the IC Partner pops scripted JSON."""

    def __init__(self, ic_responses, fail_agent=None, fail_with=None):
        self.ic_responses = list(ic_responses)
        self.calls = []  # (agent, user_prompt)
        self.versions = {}
        self.fail_agent, self.fail_with = fail_agent, fail_with

    def generate(self, system_prompt, user_prompt):
        agent = re.match(r"You are the (.+?), ", system_prompt).group(1)
        self.calls.append((agent, user_prompt))
        if agent == self.fail_agent:
            raise self.fail_with
        if agent == IC:
            return self.ic_responses.pop(0)
        n = self.versions.get(agent, 0)
        self.versions[agent] = n + 1
        return f"{agent.upper()} VERSION {VERSION[n]}"

    def order(self):
        return [a for a, _ in self.calls]

    def prompts_for(self, agent):
        return [p for a, p in self.calls if a == agent]


@pytest.fixture(autouse=True)
def no_api_key(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)


@pytest.fixture(scope="module")
def deal():
    return load_deal(SAMPLE_DEAL_PATH)


def run(deal, ic_responses, **kwargs):
    llm = ScriptedLLM(ic_responses)
    events = []
    result = DealTeamOrchestrator(llm, on_event=events.append, **kwargs).run(deal)
    return llm, result, events


def v(agent, n):
    return f"{agent.upper()} VERSION {VERSION[n]}"


# --- Dependency graph -----------------------------------------------------------------

def test_dependency_graph_is_explicit_and_topological():
    assert AGENT_NAMES == (DEAL_LEAD, STRATEGIC_FIT, VALUATION, SYNERGY, FINANCIAL_DD,
                           COMMERCIAL_DD, LEGAL_OPS_DD, STRUCTURING, RISK)
    assert DEPENDENCIES[RISK] == AGENT_NAMES[:-1]
    for peer in DD_PEERS:
        assert not DD_PEERS & set(DEPENDENCIES[peer])


@pytest.mark.parametrize("flagged, expected", [
    ([DEAL_LEAD], AGENT_NAMES),
    ([STRATEGIC_FIT], AGENT_NAMES[1:]),
    ([VALUATION], AGENT_NAMES[2:]),
    ([SYNERGY], AGENT_NAMES[3:]),
    ([FINANCIAL_DD], (FINANCIAL_DD, STRUCTURING, RISK)),
    ([COMMERCIAL_DD], (COMMERCIAL_DD, STRUCTURING, RISK)),
    ([LEGAL_OPS_DD], (LEGAL_OPS_DD, STRUCTURING, RISK)),
    ([STRUCTURING], (STRUCTURING, RISK)),
    ([RISK], (RISK,)),
    ([RISK, FINANCIAL_DD], (FINANCIAL_DD, STRUCTURING, RISK)),
    ([FINANCIAL_DD, COMMERCIAL_DD], (FINANCIAL_DD, COMMERCIAL_DD, STRUCTURING, RISK)),
    ([SYNERGY, DEAL_LEAD], AGENT_NAMES),
])
def test_rerun_plan(flagged, expected):
    assert rerun_plan(flagged) == expected


def test_dd_peers_never_depend_on_each_other():
    for peer in DD_PEERS:
        assert not (DD_PEERS - {peer}) & dependents_of(peer)


# --- Initial run ------------------------------------------------------------------------

def test_initial_run_order_and_peer_isolation(deal):
    llm, result, _ = run(deal, [approved()])
    assert llm.order()[:9] == list(AGENT_NAMES)
    for peer in DD_PEERS:
        (prompt,) = llm.prompts_for(peer)
        for other in DD_PEERS - {peer}:
            assert v(other, 0) not in prompt


def test_each_agent_gets_exactly_its_dependencies_as_prior_analyses(deal):
    llm, _, _ = run(deal, [approved()])
    for name in AGENT_NAMES:
        (prompt,) = llm.prompts_for(name)
        for other in AGENT_NAMES:
            present = f"--- {other} ---\n{v(other, 0)}" in prompt
            assert present == (other in DEPENDENCIES[name]), (name, other)


def test_deterministic_contexts_built_once(deal, monkeypatch):
    counts = {"valuation": 0, "synergy": 0, "transaction": 0}
    for key, fn in [("valuation", "build_valuation_context"), ("synergy", "build_synergy_context"),
                    ("transaction", "build_transaction_structure_context")]:
        original = getattr(orch_module, fn)

        def counted(d, _key=key, _original=original):
            counts[_key] += 1
            return _original(d)

        monkeypatch.setattr(orch_module, fn, counted)
    run(deal, [revise(DEAL_LEAD), approved()])
    assert counts == {"valuation": 1, "synergy": 1, "transaction": 1}


def test_ic_partner_reviews_all_nine_with_full_calculations(deal):
    llm, result, _ = run(deal, [approved()])
    (prompt,) = llm.prompts_for(IC)
    for name in AGENT_NAMES:
        assert f"--- {name} ---\n{v(name, 0)}" in prompt
    assert json.dumps(result.deterministic_contexts, indent=2) in prompt


# --- Approval flow -------------------------------------------------------------------------

def test_approval_flow(deal):
    llm, result, events = run(deal, [approved()])
    assert llm.order() == list(AGENT_NAMES) + [IC]
    assert result.status == APPROVED
    assert result.review_rounds_used == 1
    assert len(result.review_history) == 1 and result.review_history[0].review.approved
    assert result.revision_history == []
    assert all(r.kind == "initial" for r in result.agent_runs)
    assert result.outputs == {name: v(name, 0) for name in AGENT_NAMES}
    assert [type(e) for e in events] == [AgentRun] * 9 + [ReviewRound]


# --- Direct revision --------------------------------------------------------------------------

def test_direct_revision_of_risk_only(deal):
    llm, result, _ = run(deal, [revise(RISK), approved()])
    assert llm.order() == list(AGENT_NAMES) + [IC, RISK, IC]
    first, revised = llm.prompts_for(RISK)
    assert "=== PREVIOUS OUTPUT" not in first
    assert f"=== PREVIOUS OUTPUT (your earlier text; not source evidence) ===\n\n{v(RISK, 0)}" in revised
    assert "Reason: Problem in Risk Analyst.\nRequired change: Fix Risk Analyst." in revised
    second_review = llm.prompts_for(IC)[1]
    assert f"--- {RISK} ---\n{v(RISK, 1)}" in second_review
    assert v(RISK, 0) not in second_review
    assert result.status == APPROVED and result.review_rounds_used == 2


# --- Dependency cascade ----------------------------------------------------------------------

def test_financial_dd_revision_cascade(deal):
    llm, result, _ = run(deal, [revise(FINANCIAL_DD), approved()])
    assert llm.order()[10:] == [FINANCIAL_DD, STRUCTURING, RISK, IC]
    assert len(llm.prompts_for(COMMERCIAL_DD)) == 1
    assert len(llm.prompts_for(LEGAL_OPS_DD)) == 1
    structuring_rerun = llm.prompts_for(STRUCTURING)[1]
    assert f"--- {FINANCIAL_DD} ---\n{v(FINANCIAL_DD, 1)}" in structuring_rerun
    assert f"--- {COMMERCIAL_DD} ---\n{v(COMMERCIAL_DD, 0)}" in structuring_rerun
    risk_rerun = llm.prompts_for(RISK)[1]
    assert f"--- {STRUCTURING} ---\n{v(STRUCTURING, 1)}" in risk_rerun
    (record,) = result.revision_history
    assert record.directly_flagged == (FINANCIAL_DD,)
    assert record.stale_reruns == (STRUCTURING, RISK)
    assert record.rerun_order == (FINANCIAL_DD, STRUCTURING, RISK)


def test_deal_lead_revision_reruns_everything_in_order_with_peer_isolation(deal):
    llm, result, _ = run(deal, [revise(DEAL_LEAD), approved()])
    assert llm.order()[10:] == list(AGENT_NAMES) + [IC]
    for peer in DD_PEERS:
        rerun_prompt = llm.prompts_for(peer)[1]
        assert f"--- {DEAL_LEAD} ---\n{v(DEAL_LEAD, 1)}" in rerun_prompt
        for other in DD_PEERS - {peer}:
            assert other.upper() not in rerun_prompt
    assert result.revision_history[0].stale_reruns == AGENT_NAMES[1:]


def test_multiple_targets_rerun_each_affected_agent_once(deal):
    llm, result, _ = run(deal, [revise(FINANCIAL_DD, COMMERCIAL_DD), approved()])
    assert llm.order()[10:] == [FINANCIAL_DD, COMMERCIAL_DD, STRUCTURING, RISK, IC]
    assert len(llm.prompts_for(LEGAL_OPS_DD)) == 1
    assert len(llm.prompts_for(STRUCTURING)) == 2
    assert len(llm.prompts_for(RISK)) == 2
    record = result.revision_history[0]
    assert record.directly_flagged == (FINANCIAL_DD, COMMERCIAL_DD)
    run_for = {r.agent: r for r in result.agent_runs if r.after_review_round == 1}
    assert run_for[STRUCTURING].invalidated_by == (FINANCIAL_DD, COMMERCIAL_DD)


def test_flagged_agent_that_is_also_downstream_runs_once_as_direct_revision(deal):
    llm, result, _ = run(deal, [revise(DEAL_LEAD, RISK), approved()])
    assert llm.order()[10:].count(RISK) == 1
    risk_rerun = [r for r in result.agent_runs if r.agent == RISK and r.after_review_round == 1]
    assert [r.kind for r in risk_rerun] == ["direct_revision"]
    assert "Required change: Fix Risk Analyst." in llm.prompts_for(RISK)[1]


def test_deterministic_calculations_unchanged_by_revisions(deal):
    llm, result, _ = run(deal, [revise(DEAL_LEAD), approved()])
    source_part = re.compile(r"=== (?:PRIOR ANALYSES|PREVIOUS OUTPUT|TASK)")
    for name in AGENT_NAMES:
        first, second = llm.prompts_for(name)
        # DEAL DATA and DETERMINISTIC CALCULATIONS sections are byte-identical
        assert source_part.split(first)[0] == source_part.split(second)[0]


# --- Feedback routing ---------------------------------------------------------------------------

def test_feedback_only_reaches_its_target(deal):
    llm, result, _ = run(deal, [revise(FINANCIAL_DD, STRUCTURING), approved()])
    for agent, prompt in llm.calls[10:]:
        if agent == IC:
            continue
        if agent in (FINANCIAL_DD, STRUCTURING):
            assert f"Required change: Fix {agent}." in prompt
            others = {FINANCIAL_DD, STRUCTURING} - {agent}
            for other in others:
                assert f"Fix {other}." not in prompt
        else:
            assert "=== REVIEW FEEDBACK" not in prompt


def test_stale_reruns_get_no_feedback_or_previous_output(deal):
    llm, result, _ = run(deal, [revise(FINANCIAL_DD), approved()])
    for name in (STRUCTURING, RISK):
        rerun_prompt = llm.prompts_for(name)[1]
        assert "=== REVIEW FEEDBACK" not in rerun_prompt
        assert "=== PREVIOUS OUTPUT" not in rerun_prompt
        assert "Problem in" not in rerun_prompt


# --- Review limit ---------------------------------------------------------------------------------

def test_review_limit_reached(deal):
    llm, result, _ = run(deal, [revise(RISK), revise(RISK)])
    assert llm.order() == list(AGENT_NAMES) + [IC, RISK, IC]
    assert result.status == REVIEW_LIMIT_REACHED
    assert result.review_rounds_used == 2
    assert [r.review.status for r in result.review_history] == ["revision_required"] * 2
    assert len(result.revision_history) == 1  # no unreviewed revisions after the last round
    assert result.outputs[RISK] == v(RISK, 1)


@pytest.mark.parametrize("rounds", [1, 2, 3, 4])
def test_configurable_limit_bounds_the_loop(deal, rounds):
    llm, result, _ = run(deal, [revise(STRUCTURING)] * rounds, max_review_rounds=rounds)
    assert result.status == REVIEW_LIMIT_REACHED
    assert llm.order().count(IC) == rounds
    assert len(result.revision_history) == rounds - 1
    assert llm.ic_responses == []


def test_approval_in_a_later_round(deal):
    _, result, _ = run(deal, [revise(RISK), revise(RISK), approved()], max_review_rounds=3)
    assert result.status == APPROVED and result.review_rounds_used == 3


@pytest.mark.parametrize("bad, exc", [(0, ValueError), (-1, ValueError), (1.5, TypeError),
                                      (True, TypeError)])
def test_max_review_rounds_validated(bad, exc):
    with pytest.raises(exc):
        DealTeamOrchestrator(ScriptedLLM([]), max_review_rounds=bad)


# --- Audit history --------------------------------------------------------------------------------

def test_audit_history_keeps_every_version(deal):
    _, result, _ = run(deal, [revise(FINANCIAL_DD), approved()])
    fdd = result.versions(FINANCIAL_DD)
    assert [(r.kind, r.output) for r in fdd] == [
        ("initial", v(FINANCIAL_DD, 0)), ("direct_revision", v(FINANCIAL_DD, 1))]
    assert fdd[1].feedback.reason == "Problem in Financial Due Diligence."
    assert fdd[1].feedback.required_change == "Fix Financial Due Diligence."
    risk = result.versions(RISK)
    assert risk[1].kind == "stale_rerun" and risk[1].invalidated_by == (FINANCIAL_DD,)
    assert risk[1].feedback is None
    assert [r.agent for r in result.versions(COMMERCIAL_DD)] == [COMMERCIAL_DD]
    assert result.review_history[0].raw_output == revise(FINANCIAL_DD)
    assert len(result.agent_runs) == 9 + 3


# --- Error handling --------------------------------------------------------------------------------

def test_malformed_ic_json_raises(deal):
    with pytest.raises(OrchestrationError) as exc_info:
        run(deal, ["```json\n" + approved() + "\n```"])
    assert isinstance(exc_info.value.cause, ICReviewParseError)
    assert exc_info.value.__cause__ is exc_info.value.cause


def test_ic_numeric_violation_raises_before_parsing(deal):
    bad = json.dumps({"status": "approved", "summary": "Deal Lead wrote 18.5%.",
                      "revision_requests": [], "cross_workstream_issues": [],
                      "unresolved_information_gaps": []})
    with pytest.raises(OrchestrationError, match="18.5%") as exc_info:
        run(deal, [bad] * 3 + [approved()])  # 3 attempts, all invalid; never reaches the 4th
    assert isinstance(exc_info.value.cause, AgentValidationError)
    assert exc_info.value.stage == "ic_review"
    events = exc_info.value.partial_result.validation_events
    assert [(e.agent, e.attempt, e.outcome) for e in events] == [
        ("IC Partner", 1, "retry_scheduled"), ("IC Partner", 2, "retry_scheduled"),
        ("IC Partner", 3, "failed_after_max_attempts")]
    assert exc_info.value.partial_result.review_history == []  # nothing invalid was recorded


def test_agent_validation_failure_is_visible(deal):
    llm = ScriptedLLM([approved()])
    original = llm.generate

    def generate(system_prompt, user_prompt):
        out = original(system_prompt, user_prompt)
        return "Rates of 25%." if system_prompt.startswith(f"You are the {SYNERGY},") else out

    llm.generate = generate
    with pytest.raises(OrchestrationError, match="Synergy Analyst") as exc_info:
        DealTeamOrchestrator(llm).run(deal)
    assert isinstance(exc_info.value.cause, AgentValidationError)
    assert [v.token for v in exc_info.value.cause.violations] == ["25%"]


def test_llm_errors_are_not_swallowed(deal):
    llm = ScriptedLLM([approved()], fail_agent=COMMERCIAL_DD, fail_with=LLMError("down"))
    with pytest.raises(OrchestrationError, match="down") as exc_info:
        DealTeamOrchestrator(llm).run(deal)
    assert isinstance(exc_info.value.__cause__, LLMError)
