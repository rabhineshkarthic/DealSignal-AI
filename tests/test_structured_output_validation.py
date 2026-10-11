"""Numeric provenance for strict-JSON agents (IC Partner, IC Memo Writer).

Raw JSON escapes such as \\n can glue a number to a letter, hiding it from the
numeral pattern; structured agents also validate every decoded JSON string.
Mocked LLM only; no API key, no network.
"""

import json
import re
from pathlib import Path

import pytest

from agent_validation import AgentValidationError, decoded_json_strings, find_numeric_violations
from agents import BaseAgent, ICMemoWriter, ICPartner
from agents.ic_memo_writer import CONCLUSION_HEADING, MEMO_HEADINGS, PERMITTED_CONCLUSIONS
from agents.registry import AGENT_NAMES
from deal_analysis import build_synergy_context, build_transaction_structure_context, build_valuation_context
from ic_review import ICReviewParseError
from models import deal_to_context, load_deal
from orchestrator import DealTeamOrchestrator, OrchestrationError

ROOT = Path(__file__).resolve().parent.parent
SAMPLE_DEAL_PATH = ROOT / "data" / "sample_deal.json"


class FakeLLM:
    def __init__(self, response):
        self.response = response

    def generate(self, system_prompt, user_prompt):
        return self.response


@pytest.fixture(autouse=True)
def no_api_key(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)


@pytest.fixture(scope="module")
def deal():
    return load_deal(SAMPLE_DEAL_PATH)


@pytest.fixture(scope="module")
def ctx(deal):
    return {
        "deal": deal_to_context(deal),
        "deterministic_calculations": {
            "valuation": build_valuation_context(deal),
            "synergy": build_synergy_context(deal),
            "transaction_structure": build_transaction_structure_context(deal),
        },
        "prior_analyses": {name: "text" for name in AGENT_NAMES},
    }


def ic_json(summary="Quality criteria met.", gaps=(), issues=()):
    return json.dumps({"status": "approved", "summary": summary, "revision_requests": [],
                       "cross_workstream_issues": list(issues),
                       "unresolved_information_gaps": list(gaps)})


def run_ic(ctx, raw):
    return ICPartner(FakeLLM(raw)).run(ctx)


# --- Flags -------------------------------------------------------------------------------

def test_structured_agents_enable_decoded_validation():
    assert ICPartner.structured_json_output is True
    assert ICMemoWriter.structured_json_output is True
    assert BaseAgent.structured_json_output is False
    # both use the shared hook rather than their own override
    assert ICPartner.validation_texts is BaseAgent.validation_texts
    assert ICMemoWriter.validation_texts is BaseAgent.validation_texts


def test_only_the_two_json_agents_are_structured():
    structured = {cls.name for cls in BaseAgent.__subclasses__() if cls.structured_json_output}
    assert structured == {"IC Partner", "IC Memo Writer"}


# --- 1. plain unauthorized number in raw IC JSON ------------------------------------------

def test_ic_plain_unauthorized_number_rejected(ctx):
    with pytest.raises(AgentValidationError, match="18.5%"):
        run_ic(ctx, ic_json(summary="Deal Lead implied 18.5% growth."))


# --- 2. number after an escaped newline --------------------------------------------------

def test_ic_number_after_escaped_newline_rejected(ctx):
    raw = ic_json(summary="Issue:\n18.5% impact")
    assert "Issue:\\n18.5% impact" in raw
    assert find_numeric_violations(raw, [ctx["deal"]]) == []  # the raw scan alone misses it
    with pytest.raises(AgentValidationError, match="18.5%"):
        run_ic(ctx, raw)


# --- 3. other escapes -----------------------------------------------------------------------

@pytest.mark.parametrize("hidden", [
    "Issue:\t18.5% impact",          # \t
    "Issue:\r18.5% impact",          # \r
    "Issue: 18.5% impact",      # non-breaking space, escaped as
    "Issue: 18.5% impact",      # thin space, escaped as
    "Issue:\f18.5% impact",          # \f
    "Issue:\b18.5% impact",          # \b
])
def test_ic_number_after_other_escapes_rejected(ctx, hidden):
    raw = ic_json(summary=hidden)
    with pytest.raises(AgentValidationError, match="18.5%"):
        run_ic(ctx, raw)


def test_hidden_negative_via_unicode_minus_escape_rejected(ctx):
    # − is a minus sign; decoded it reads as -13.5878, which is not approved
    raw = ic_json(summary="Net synergy value of −13.5878")
    assert "\\u221213.5878" in raw
    with pytest.raises(AgentValidationError, match="13.5878"):
        run_ic(ctx, raw)


@pytest.mark.parametrize("field", ["gaps", "issues"])
def test_nested_list_strings_are_checked(ctx, field):
    raw = ic_json(**{field: ["fine", "Nested:\n777 units"]})
    with pytest.raises(AgentValidationError, match="777"):
        run_ic(ctx, raw)


def test_json_keys_are_checked_too():
    assert "Key\n999" in decoded_json_strings(json.dumps({"Key\n999": "v"}))


# --- 4. approved numbers inside decoded IC JSON -----------------------------------------------

def test_ic_approved_numbers_in_decoded_json_allowed(ctx):
    raw = ic_json(summary="Offer EV:\n205.0 sits within range; DCF EV:\n172.024.",
                  gaps=["Revenue:\n120.0 reported"])
    assert run_ic(ctx, raw) == raw


# --- 5. malformed IC JSON still reaches the strict parser ---------------------------------------

def test_decoder_returns_nothing_for_malformed_json():
    assert decoded_json_strings("not json") == []
    assert decoded_json_strings('{"summary": "unterminated') == []


def test_malformed_ic_json_without_numbers_still_raises_parse_error(deal):
    class LLM:
        def generate(self, system_prompt, user_prompt):
            if system_prompt.startswith("You are the IC Partner,"):
                return "Looks good to me."
            agent = re.match(r"You are the (.+?), ", system_prompt).group(1)
            return f"{agent.upper()} OUTPUT"

    with pytest.raises(OrchestrationError) as exc_info:
        DealTeamOrchestrator(LLM()).run(deal)
    assert exc_info.value.stage == "ic_review_parse"
    assert isinstance(exc_info.value.cause, ICReviewParseError)
    assert exc_info.value.raw_ic_output == "Looks good to me."


def test_malformed_ic_json_with_number_still_caught_by_raw_validation(ctx):
    with pytest.raises(AgentValidationError, match="18.5%"):
        run_ic(ctx, '{"summary": "impact 18.5%"')


def test_ic_validation_does_not_repair_or_alter_output(ctx):
    raw = "  " + ic_json() + "\n"
    assert run_ic(ctx, raw) == raw  # returned exactly as generated


# --- 6. memo writer still protected ----------------------------------------------------------

def memo_json(extra):
    parts = []
    for heading in MEMO_HEADINGS:
        parts.append(heading)
        parts.append(f"{PERMITTED_CONCLUSIONS[0]}." if heading == CONCLUSION_HEADING
                     else "Synthesis." + extra)
    return json.dumps({"conclusion": PERMITTED_CONCLUSIONS[0], "memo": "\n\n".join(parts)})


def test_memo_writer_escape_hidden_number_rejected(ctx):
    with pytest.raises(AgentValidationError, match="25%"):
        ICMemoWriter(FakeLLM(memo_json("\n25% of revenue"))).run(ctx)


def test_memo_writer_approved_numbers_allowed(ctx):
    raw = memo_json("\n172.024 DCF EV; new shares issued\n2.96.")
    assert ICMemoWriter(FakeLLM(raw)).run(ctx) == raw


# --- 7. source permissions unchanged ------------------------------------------------------------

@pytest.mark.parametrize("agent_cls", [ICPartner, ICMemoWriter])
def test_numeric_sources_unchanged(agent_cls, ctx):
    full = {**ctx, "previous_output": "old 111", "review_feedback": "fix 222",
            "review_context": {"summary": "gap 333"}}
    assert agent_cls(FakeLLM("")).validation_sources(full) == [
        ctx["deal"], ctx["deterministic_calculations"]]


@pytest.mark.parametrize("section, value", [
    ("prior_analyses", {"Deal Lead": "It said 444."}),
    ("previous_output", "It said 444."),
    ("review_feedback", "It said 444."),
    ("review_context", {"summary": "It said 444."}),
])
def test_non_evidence_sections_still_cannot_authorize_decoded_numbers(ctx, section, value):
    raw = ic_json(summary="Echo:\n444")
    with pytest.raises(AgentValidationError, match="444"):
        ICPartner(FakeLLM(raw)).run({**ctx, section: value})
