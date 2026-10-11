"""Partial-state preservation when orchestration fails. Scripted fake LLM only."""

import json
import re
from pathlib import Path

import pytest

from agent_validation import AgentValidationError
from agents.registry import (
    AGENT_NAMES,
    COMMERCIAL_DD,
    FINANCIAL_DD,
    LEGAL_OPS_DD,
    RISK,
    STRUCTURING,
    SYNERGY,
)
from ic_review import ICReviewParseError
from llm_client import LLMError
from models import load_deal
from orchestrator import (
    APPROVED,
    REVIEW_LIMIT_REACHED,
    DealTeamOrchestrator,
    OrchestrationError,
)

ROOT = Path(__file__).resolve().parent.parent
SAMPLE_DEAL_PATH = ROOT / "data" / "sample_deal.json"
IC = "IC Partner"
VERSION = "ABCDEFGHIJ"


def approved():
    return json.dumps({"status": "approved", "summary": "Quality criteria met.",
                       "revision_requests": [], "cross_workstream_issues": [],
                       "unresolved_information_gaps": []})


def revise(*agents):
    return json.dumps({
        "status": "revision_required", "summary": "Revisions needed.",
        "revision_requests": [{"agent": a, "reason": f"Problem in {a}.",
                               "required_change": f"Fix {a}."} for a in agents],
        "cross_workstream_issues": [], "unresolved_information_gaps": [],
    })


class FailingLLM:
    """Scripted LLM that raises `error` on the `nth` call (from zero) to `fail_agent`."""

    def __init__(self, ic_responses, fail_agent=None, nth=0, error=None):
        self.ic_responses = list(ic_responses)
        self.fail_agent, self.nth, self.error = fail_agent, nth, error
        self.counts = {}

    def generate(self, system_prompt, user_prompt):
        agent = re.match(r"You are the (.+?), ", system_prompt).group(1)
        n = self.counts.get(agent, 0)
        self.counts[agent] = n + 1
        if agent == self.fail_agent and n == self.nth:
            raise self.error
        if agent == IC:
            return self.ic_responses.pop(0)
        return f"{agent.upper()} VERSION {VERSION[n]}"


@pytest.fixture(autouse=True)
def no_api_key(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)


@pytest.fixture(scope="module")
def deal():
    return load_deal(SAMPLE_DEAL_PATH)


def fail(deal, llm, **kwargs):
    with pytest.raises(OrchestrationError) as exc_info:
        DealTeamOrchestrator(llm, **kwargs).run(deal)
    return exc_info.value


def v(agent, n):
    return f"{agent.upper()} VERSION {VERSION[n]}"


# 1. Failure during the initial analytical run ---------------------------------------

def test_initial_run_failure_preserves_completed_outputs(deal):
    err = fail(deal, FailingLLM([], fail_agent=FINANCIAL_DD, error=LLMError("unavailable")))
    assert err.stage == "initial_analysis"
    assert err.agent == FINANCIAL_DD
    assert err.review_round is None
    assert isinstance(err.cause, LLMError) and err.__cause__ is err.cause
    partial = err.partial_result
    completed = list(AGENT_NAMES[:AGENT_NAMES.index(FINANCIAL_DD)])
    assert list(partial.outputs) == completed
    assert [r.agent for r in partial.agent_runs] == completed
    assert partial.status is None and partial.review_history == []
    assert set(partial.deterministic_contexts) == {"valuation", "synergy", "transaction_structure"}


def test_initial_validation_failure_keeps_violation_details(deal):
    class BadSynergy(FailingLLM):
        def generate(self, system_prompt, user_prompt):
            out = super().generate(system_prompt, user_prompt)
            return "A rate of 25%." if f"You are the {SYNERGY}," in system_prompt else out

    err = fail(deal, BadSynergy([]))
    assert err.stage == "initial_analysis" and err.agent == SYNERGY
    assert isinstance(err.cause, AgentValidationError)
    assert [x.token for x in err.cause.violations] == ["25%"]
    assert err.cause.response == "A rate of 25%."
    assert SYNERGY not in err.partial_result.outputs


# 2. Failure during IC review ---------------------------------------------------------

def test_ic_review_llm_failure_preserves_all_nine_outputs(deal):
    err = fail(deal, FailingLLM([], fail_agent=IC, error=LLMError("quota")))
    assert err.stage == "ic_review"
    assert err.agent == IC and err.review_round == 1
    assert isinstance(err.cause, LLMError)
    assert err.partial_result.outputs == {n: v(n, 0) for n in AGENT_NAMES}
    assert err.partial_result.review_history == []


def test_second_round_ic_failure_keeps_first_review_and_revisions(deal):
    err = fail(deal, FailingLLM([revise(RISK)], fail_agent=IC, nth=1, error=LLMError("x")))
    assert err.stage == "ic_review" and err.review_round == 2
    partial = err.partial_result
    assert len(partial.review_history) == 1
    assert partial.review_rounds_used == 1
    assert [r.kind for r in partial.versions(RISK)] == ["initial", "direct_revision"]
    assert partial.outputs[RISK] == v(RISK, 1)


# 3. Malformed IC JSON --------------------------------------------------------------------

def test_malformed_ic_json_keeps_raw_response_and_analysis_state(deal):
    raw = "Looks good to me."
    err = fail(deal, FailingLLM([raw]))
    assert err.stage == "ic_review_parse"
    assert err.review_round == 1
    assert err.raw_ic_output == raw
    assert isinstance(err.cause, ICReviewParseError)
    assert err.partial_result.outputs == {n: v(n, 0) for n in AGENT_NAMES}
    assert err.partial_result.review_history == []


# 4. Failure during a direct revision --------------------------------------------------------

def test_direct_revision_failure_preserves_versions_and_history(deal):
    err = fail(deal, FailingLLM([revise(FINANCIAL_DD)], fail_agent=FINANCIAL_DD, nth=1,
                                error=LLMError("timeout")))
    assert err.stage == "direct_revision"
    assert err.agent == FINANCIAL_DD and err.review_round == 1
    partial = err.partial_result
    assert partial.outputs[FINANCIAL_DD] == v(FINANCIAL_DD, 0)  # original still active
    assert [r.kind for r in partial.versions(FINANCIAL_DD)] == ["initial"]
    assert len(partial.review_history) == 1
    (record,) = partial.revision_history
    assert record.directly_flagged == (FINANCIAL_DD,)
    assert record.rerun_order == (FINANCIAL_DD, STRUCTURING, RISK)


# 5. Failure during a stale downstream rerun -----------------------------------------------------

def test_stale_rerun_failure_preserves_upstream_revision(deal):
    err = fail(deal, FailingLLM([revise(FINANCIAL_DD)], fail_agent=STRUCTURING, nth=1,
                                error=LLMError("unavailable")))
    assert err.stage == "stale_rerun"
    assert err.agent == STRUCTURING and err.review_round == 1
    partial = err.partial_result
    assert [r.kind for r in partial.versions(FINANCIAL_DD)] == ["initial", "direct_revision"]
    assert partial.outputs[FINANCIAL_DD] == v(FINANCIAL_DD, 1)
    assert partial.outputs[STRUCTURING] == v(STRUCTURING, 0)
    assert [r.kind for r in partial.versions(RISK)] == ["initial"]  # never reached
    assert partial.revision_history[0].stale_reruns == (STRUCTURING, RISK)


# 6. Partial state is inspectable, and nothing is swallowed -----------------------------------------

def test_error_message_names_stage_agent_round_and_cause(deal):
    err = fail(deal, FailingLLM([revise(FINANCIAL_DD)], fail_agent=STRUCTURING, nth=1,
                                error=LLMError("service unavailable")))
    message = str(err)
    for part in ["stale_rerun", STRUCTURING, "review round 1", "LLMError", "service unavailable"]:
        assert part in message


def test_programming_errors_are_not_wrapped(deal):
    llm = FailingLLM([], fail_agent=COMMERCIAL_DD, error=KeyError("bug"))
    with pytest.raises(KeyError):
        DealTeamOrchestrator(llm).run(deal)


def test_peer_outputs_before_failure_are_kept(deal):
    err = fail(deal, FailingLLM([], fail_agent=LEGAL_OPS_DD, error=LLMError("x")))
    assert {FINANCIAL_DD, COMMERCIAL_DD} <= set(err.partial_result.outputs)
    assert LEGAL_OPS_DD not in err.partial_result.outputs


# 7. Successful flows unchanged ---------------------------------------------------------------------

def test_approved_flow_unchanged(deal):
    result = DealTeamOrchestrator(FailingLLM([approved()])).run(deal)
    assert result.status == APPROVED and result.review_rounds_used == 1
    assert len(result.agent_runs) == 9


def test_review_limit_flow_unchanged(deal):
    result = DealTeamOrchestrator(FailingLLM([revise(RISK), revise(RISK)])).run(deal)
    assert result.status == REVIEW_LIMIT_REACHED and result.review_rounds_used == 2
    assert len(result.agent_runs) == 10


# --- CLI reporting ----------------------------------------------------------------------

def test_cli_reports_stage_agent_and_partial_state(deal, capsys):
    import run_agents

    err = fail(deal, FailingLLM([revise(FINANCIAL_DD)], fail_agent=STRUCTURING, nth=1,
                                error=LLMError("service unavailable")))
    run_agents.report_failure(err, model=None)
    out = capsys.readouterr().err
    for part in ["ORCHESTRATION FAILED", "Stage: stale_rerun", f"Agent: {STRUCTURING}",
                 "Review round: 1", "LLMError: service unavailable",
                 "Partial state is available on OrchestrationError.partial_result",
                 "IC review rounds completed: 1", "Revision rounds started: 1"]:
        assert part in out, part
    assert "GEMINI_API_KEY" not in out
    assert run_agents.EXIT_CODES[type(err.cause)] == 1


def test_cli_shows_malformed_ic_output_and_exit_code(deal, capsys):
    import run_agents

    err = fail(deal, FailingLLM(["Looks good to me."]))
    run_agents.report_failure(err, model=None)
    captured = capsys.readouterr()
    assert "Stage: ic_review_parse" in captured.err
    assert "MALFORMED IC REVIEW OUTPUT (not accepted)" in captured.out
    assert "Looks good to me." in captured.out
    assert run_agents.EXIT_CODES[type(err.cause)] == 3
