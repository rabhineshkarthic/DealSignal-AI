"""Validation-aware agent retry. Scripted fake LLM only: no Gemini, no network."""

import json
import re
from pathlib import Path

import pytest

import ui_helpers as ui
from agent_validation import AgentValidationError
from agents import BaseAgent
from agents.registry import AGENT_NAMES, RISK, STRUCTURING
from models import deal_to_context, load_deal
from orchestrator import (
    APPROVED,
    MAX_VALIDATION_RETRIES,
    DealTeamOrchestrator,
    OrchestrationError,
    ValidationRetryEvent,
)

ROOT = Path(__file__).resolve().parent.parent
DEAL = load_deal(ROOT / "data" / "sample_deal.json")
# 338 and 17 appear nowhere in the sample's DEAL DATA or DETERMINISTIC CALCULATIONS.
# (10 would be approved here: the sample target's free cash flow is 10.0.)
BAD = "Consider an election worth 338 over 17 years."
GOOD = "Cash consideration of 111.0 and stock consideration of 74.0."   # approved values


def approved():
    return json.dumps({"status": "approved", "summary": "Quality criteria met.",
                       "revision_requests": [], "cross_workstream_issues": [],
                       "unresolved_information_gaps": []})


class ScriptedLLM:
    """Per-agent scripted responses; other agents return a digit-free marker."""

    def __init__(self, scripts: dict[str, list[str]]):
        self.scripts = {k: list(v) for k, v in scripts.items()}
        self.calls: list[tuple[str, str]] = []

    def generate(self, system_prompt, user_prompt):
        agent = re.match(r"You are the (.+?), ", system_prompt).group(1)
        self.calls.append((agent, user_prompt))
        if agent == "IC Partner":
            return approved()
        if agent in self.scripts and self.scripts[agent]:
            return self.scripts[agent].pop(0)
        return f"{agent.upper()} OUTPUT"

    def prompts(self, agent):
        return [p for a, p in self.calls if a == agent]


def run(structuring_responses):
    llm = ScriptedLLM({STRUCTURING: structuring_responses})
    events = []
    orchestrator = DealTeamOrchestrator(llm, on_event=events.append)
    return llm, orchestrator, events


def test_attempts_permitted():
    assert MAX_VALIDATION_RETRIES == 2  # initial attempt + 2 retries = 3 attempts


def test_first_invalid_second_valid_succeeds_and_continues_downstream():
    llm, orchestrator, _ = run([BAD, GOOD])
    result = orchestrator.run(DEAL)
    assert result.status == APPROVED and result.outputs[STRUCTURING] == GOOD
    assert len(llm.prompts(STRUCTURING)) == 2
    assert len(llm.prompts(RISK)) == 1  # downstream ran after the corrected response
    assert [(e.attempt, e.outcome, e.unsupported_tokens) for e in result.validation_events] == [
        (1, "retry_scheduled", ("338", "17")), (2, "accepted_on_retry", ())]


def test_two_invalid_then_valid_succeeds_on_third_attempt():
    llm, orchestrator, _ = run([BAD, BAD, GOOD])
    result = orchestrator.run(DEAL)
    assert result.outputs[STRUCTURING] == GOOD and len(llm.prompts(STRUCTURING)) == 3
    assert [e.outcome for e in result.validation_events] == [
        "retry_scheduled", "retry_scheduled", "accepted_on_retry"]


def test_all_attempts_invalid_raises_existing_orchestration_error():
    llm, orchestrator, _ = run([BAD, BAD, BAD, GOOD])
    with pytest.raises(OrchestrationError) as exc_info:
        orchestrator.run(DEAL)
    err = exc_info.value
    assert err.stage == "initial_analysis" and err.agent == STRUCTURING
    assert isinstance(err.cause, AgentValidationError)
    assert len(llm.prompts(STRUCTURING)) == 3  # never a 4th attempt, never accepted
    assert STRUCTURING not in err.partial_result.outputs
    assert [e.outcome for e in err.partial_result.validation_events] == [
        "retry_scheduled", "retry_scheduled", "failed_after_max_attempts"]


def test_retry_uses_same_approved_context_plus_feedback():
    llm, orchestrator, _ = run([BAD, GOOD])
    orchestrator.run(DEAL)
    first, second = llm.prompts(STRUCTURING)
    source = re.compile(r"=== (?:PRIOR ANALYSES|VALIDATION FEEDBACK|TASK)")
    assert source.split(first)[0] == source.split(second)[0]  # DEAL DATA + CALCULATIONS identical
    assert "=== VALIDATION FEEDBACK" not in first
    assert ("=== VALIDATION FEEDBACK (from the numeric validator; not source evidence) ===" in second)
    assert "unsupported numerals: 338, 17" in second
    assert "Do not perform arithmetic or introduce derived percentages, totals, ratios, " \
           "durations or monetary amounts." in second


def test_rejected_output_and_feedback_never_authorize_numbers():
    llm, orchestrator, _ = run([BAD, BAD, BAD])  # retries repeat 338 after being told about it
    with pytest.raises(OrchestrationError) as exc_info:
        orchestrator.run(DEAL)
    # the feedback naming 338 (and the rejected text) did not make 338 acceptable
    assert all("338" in e.unsupported_tokens
               for e in exc_info.value.partial_result.validation_events)
    agent = BaseAgent(ScriptedLLM({}))
    context = {"deal": deal_to_context(DEAL), "deterministic_calculations": {"x": 1.0},
               "validation_feedback": "unsupported numerals: 338, 17", "previous_output": BAD}
    assert agent.validation_sources(context) == [context["deal"], {"x": 1.0}]



def test_every_retry_attempt_is_revalidated():
    llm, orchestrator, _ = run([BAD, BAD, BAD])
    with pytest.raises(OrchestrationError) as exc_info:
        orchestrator.run(DEAL)
    tokens = [e.unsupported_tokens for e in exc_info.value.partial_result.validation_events]
    assert tokens == [("338", "17")] * 3


def test_unrelated_agents_are_not_rerun():
    llm, orchestrator, _ = run([BAD, BAD, GOOD])
    orchestrator.run(DEAL)
    counts = {name: len(llm.prompts(name)) for name in AGENT_NAMES}
    assert counts == {**{name: 1 for name in AGENT_NAMES}, STRUCTURING: 3}


def test_audit_records_rejected_output_and_events_are_emitted():
    llm, orchestrator, events = run([BAD, GOOD])
    result = orchestrator.run(DEAL)
    first = result.validation_events[0]
    assert isinstance(first, ValidationRetryEvent)
    assert (first.agent, first.attempt, first.failure_category) == (
        STRUCTURING, 1, "numeric_provenance_validation")
    assert first.rejected_output == BAD and first.after_review_round == 0
    emitted = [e for e in events if isinstance(e, ValidationRetryEvent)]
    assert emitted == result.validation_events
    labels = [ui.event_label(e) for e in emitted]
    assert labels == [
        "[6] Deal Structuring Analyst: response rejected by the numeric validator (unsupported: "
        "338, 17); retrying (attempt 2 of 3)",
        "[6] Deal Structuring Analyst: corrected response accepted (attempt 2)"]
    view = ui.validation_retries(result)
    assert view[0]["rejected_output"] == BAD and view[0]["tokens"] == "338, 17"


def test_error_details_include_full_rejected_output():
    llm, orchestrator, _ = run([BAD, BAD, BAD])
    with pytest.raises(OrchestrationError) as exc_info:
        orchestrator.run(DEAL)
    message = ui.describe_error(exc_info.value)
    assert "Rejected output (not accepted):\n" + BAD in message.details
    assert "338, 17" in message.message


def test_ic_partner_retry_label():
    event = ValidationRetryEvent("IC Partner", 1, ("888",), "retry_scheduled", 1, "x")
    assert ui.event_label(event).startswith("[IC] IC Partner: response rejected")
