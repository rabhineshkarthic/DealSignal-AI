"""Bounded memo-writer retry on strict-schema failures, and the resulting diagnostics.
Scripted fake LLMs only: no Gemini, no network."""

import copy
import json
import re
from pathlib import Path

import pytest

import llm_client
import ui_components as uc
import ui_helpers as ui
from agent_validation import AgentValidationError
from agents import ICMemoWriter
from agents.ic_memo_writer import CONCLUSION_HEADING, MEMO_HEADINGS, PERMITTED_CONCLUSIONS
from config import GeminiSettings, load_settings
from demo_state import DEMO_CONCLUSION, OfflineDemoLLM
from memo import (
    ENUM_MISMATCH,
    MAX_MEMO_ATTEMPTS,
    PARSE_FAILURE,
    SCHEMA_MISMATCH,
    InvestmentCommitteeMemoGenerator,
    MemoParseError,
    MemoRejectedError,
    parse_memo_response,
)
from models import load_deal
from orchestrator import DealTeamOrchestrator

ROOT = Path(__file__).resolve().parent.parent
DEAL = load_deal(ROOT / "data" / "sample_deal.json")
IC, WRITER = "IC Partner", "IC Memo Writer"
CONCLUSION = PERMITTED_CONCLUSIONS[0]
REJECTED_MARKER = "UNIQUE REJECTED MEMO PROSE"


def memo_markdown(conclusion=CONCLUSION, skip=None, prose="Synthesis of the reviewed analyses."):
    body = []
    for heading in MEMO_HEADINGS:
        if heading == skip:
            continue
        body.append(heading)
        body.append(f"{conclusion}. Material information gaps remain."
                    if heading == CONCLUSION_HEADING else prose)
    return "\n\n".join(body)


GOOD = json.dumps({"conclusion": CONCLUSION, "memo": memo_markdown()})
FENCED = "```json\n" + json.dumps({"conclusion": CONCLUSION,
                                    "memo": memo_markdown(prose=REJECTED_MARKER)}) + "\n```"
BAD_ENUM = json.dumps({"conclusion": "Proceed with caution", "memo": memo_markdown()})
MISSING_HEADING = json.dumps({"conclusion": CONCLUSION,
                              "memo": memo_markdown(skip="## Synergies")})


def approved():
    return json.dumps({"status": "approved", "summary": "Quality criteria met.",
                       "revision_requests": [], "cross_workstream_issues": [],
                       "unresolved_information_gaps": ["Customer data is not supplied."]})


class ScriptedLLM:
    def __init__(self, memo_responses=()):
        self.memo_responses = list(memo_responses)
        self.calls = []

    def generate(self, system_prompt, user_prompt):
        agent = re.match(r"You are the (.+?), ", system_prompt).group(1)
        self.calls.append((agent, user_prompt))
        if agent == IC:
            return approved()
        if agent == WRITER:
            return self.memo_responses.pop(0)
        return f"{agent.upper()} OUTPUT"

    def prompts(self, agent=WRITER):
        return [p for a, p in self.calls if a == agent]


@pytest.fixture(autouse=True)
def no_api_key(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)


@pytest.fixture(scope="module")
def result():
    return DealTeamOrchestrator(ScriptedLLM()).run(DEAL)


def generate(result, *responses):
    llm = ScriptedLLM(responses)
    return InvestmentCommitteeMemoGenerator(llm), llm


def evidence(prompt):
    """Everything before the non-evidence feedback / review / task sections."""
    return re.split(r"=== (?:SCHEMA FEEDBACK|IC REVIEW CONTEXT|TASK)", prompt)[0]


# --- Classification ------------------------------------------------------------------------

@pytest.mark.parametrize("raw, category, field", [
    (FENCED, PARSE_FAILURE, "response"),
    ("[]", PARSE_FAILURE, "response"),
    (json.dumps({"conclusion": CONCLUSION}), SCHEMA_MISMATCH, "keys"),
    (BAD_ENUM, ENUM_MISMATCH, "conclusion"),
    (MISSING_HEADING, SCHEMA_MISMATCH, "## Synergies"),
    (json.dumps({"conclusion": CONCLUSION, "memo": memo_markdown().replace(
        f"{CONCLUSION}. Material", f"{CONCLUSION.lower()}. Material")}),
     SCHEMA_MISMATCH, CONCLUSION_HEADING),
])
def test_parse_failures_are_classified(raw, category, field):
    with pytest.raises(MemoParseError) as exc_info:
        parse_memo_response(raw)
    assert (exc_info.value.category, exc_info.value.field) == (category, field)


def test_schema_is_not_weakened():
    """Fenced JSON is still rejected; the parser never repairs a response."""
    with pytest.raises(MemoParseError):
        parse_memo_response(FENCED)
    assert parse_memo_response(GOOD) == (CONCLUSION, memo_markdown())


# --- Retry ---------------------------------------------------------------------------------

def test_attempts_permitted():
    assert MAX_MEMO_ATTEMPTS == 3


def test_valid_first_response_needs_no_retry(result):
    generator, llm = generate(result, GOOD)
    memo = generator.generate(result, DEAL)
    assert memo.conclusion == CONCLUSION and memo.rejected_attempts == ()
    (prompt,) = llm.prompts()
    assert "=== SCHEMA FEEDBACK" not in prompt


def test_malformed_then_valid_succeeds_on_retry(result):
    generator, llm = generate(result, FENCED, GOOD)
    memo = generator.generate(result, DEAL)
    assert memo.memo_text == memo_markdown() and len(llm.prompts()) == 2
    (rejected,) = memo.rejected_attempts
    assert (rejected.attempt, rejected.category, rejected.field) == (1, PARSE_FAILURE, "response")
    assert rejected.raw_response == FENCED


def test_retry_prompt_has_same_context_plus_targeted_feedback(result):
    generator, llm = generate(result, BAD_ENUM, GOOD)
    generator.generate(result, DEAL)
    first, second = llm.prompts()
    assert evidence(first) == evidence(second)  # same DEAL DATA, CALCULATIONS, analyses
    assert first.split("=== IC REVIEW CONTEXT")[1] == second.split("=== IC REVIEW CONTEXT")[1]
    assert "=== SCHEMA FEEDBACK" not in first
    feedback = second.split("=== SCHEMA FEEDBACK (from the strict output-schema check; "
                            "not source evidence) ===")[1].split("=== IC REVIEW CONTEXT")[0]
    assert "Failure type: enum mismatch" in feedback
    assert "Field or section: conclusion" in feedback
    assert "attempt 1 of 3" in feedback


def test_rejected_memo_text_is_never_sent_back_or_approved(result):
    generator, llm = generate(result, FENCED, BAD_ENUM, GOOD)
    generator.generate(result, DEAL)
    prompts = llm.prompts()
    assert len(prompts) == 3
    assert all(REJECTED_MARKER not in p for p in prompts)
    assert all("Proceed with caution" not in p for p in prompts)  # rejected enum not echoed
    context = {**generator.build_context(result, DEAL), "schema_feedback": "attempt 1 of 3 at 777"}
    writer = ICMemoWriter(ScriptedLLM())
    assert writer.validation_sources(context) == [context["deal"],
                                                  context["deterministic_calculations"]]


def test_every_retry_is_still_numerically_validated(result):
    """A retry cannot carry an unapproved number: numeric failure stops at once."""
    tainted = json.dumps({"conclusion": CONCLUSION, "memo": memo_markdown(prose="Premium of 4242.")})
    generator, llm = generate(result, FENCED, tainted, GOOD)
    with pytest.raises(AgentValidationError, match="4242"):
        generator.generate(result, DEAL)
    assert len(llm.prompts()) == 2


def test_deterministic_values_are_not_mutated(result):
    before = copy.deepcopy(result.deterministic_contexts)
    generator, _ = generate(result, MISSING_HEADING, GOOD)
    generator.generate(result, DEAL)
    assert result.deterministic_contexts == before


def test_all_attempts_failing_raises_with_every_attempt(result):
    generator, llm = generate(result, FENCED, BAD_ENUM, MISSING_HEADING, GOOD)
    with pytest.raises(MemoRejectedError) as exc_info:
        generator.generate(result, DEAL)
    err = exc_info.value
    assert len(llm.prompts()) == MAX_MEMO_ATTEMPTS  # never a 4th attempt
    assert isinstance(err, MemoParseError) and err.retry_attempted
    assert [(a.attempt, a.category, a.field) for a in err.attempts] == [
        (1, PARSE_FAILURE, "response"), (2, ENUM_MISMATCH, "conclusion"),
        (3, SCHEMA_MISMATCH, "## Synergies")]
    assert err.raw_response == MISSING_HEADING
    assert (err.category, err.field) == (SCHEMA_MISMATCH, "## Synergies")


# --- Diagnostics -----------------------------------------------------------------------------

def rejection(result):
    generator, _ = generate(result, FENCED, BAD_ENUM, MISSING_HEADING)
    with pytest.raises(MemoRejectedError) as exc_info:
        generator.generate(result, DEAL)
    return exc_info.value


def test_user_message_names_field_type_and_retry(result):
    message = ui.describe_error(rejection(result))
    assert message.title == "Memo rejected"
    assert "Failure type: schema mismatch." in message.message
    assert "Field or section: ## Synergies." in message.message
    assert "Retry attempted: yes (3 of 3 attempts, all rejected)." in message.message
    for n, raw in enumerate([FENCED, BAD_ENUM, MISSING_HEADING], start=1):
        assert f"--- Attempt {n} of 3" in message.details and raw in message.details
    assert "parse failure in response" in message.details
    assert "enum mismatch in conclusion" in message.details


def test_single_parse_error_reports_no_retry():
    message = ui.describe_error(MemoParseError("bad", category=PARSE_FAILURE, field="response"))
    assert "Failure type: parse failure." in message.message
    assert "Retry attempted: no." in message.message


def test_session_state_record_of_rejected_responses(result):
    state = ui.rejected_memo_state(rejection(result))
    assert [(s["attempt"], s["failure_type"], s["field"]) for s in state] == [
        (1, "parse failure", "response"), (2, "enum mismatch", "conclusion"),
        (3, "schema mismatch", "## Synergies")]
    assert state[-1]["raw_response"] == MISSING_HEADING
    assert ui.rejected_memo_state(ValueError("x")) is None
    assert "rejected_memo_attempts" in ui.ANALYSIS_KEYS  # cleared when the deal changes


# --- Streamlit (live path with a scripted client) --------------------------------------------

class DemoWithMemoScript(OfflineDemoLLM):
    def __init__(self, memo_script):
        super().__init__()
        self.memo_script = memo_script

    def generate(self, system_prompt, user_prompt):
        if system_prompt.startswith(f"You are the {WRITER}, ") and self.memo_script:
            return self.memo_script.pop(0)
        return super().generate(system_prompt, user_prompt)


def run_live_app(monkeypatch, memo_script):
    from streamlit.testing.v1 import AppTest

    monkeypatch.setattr(llm_client.GeminiClient, "__init__",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("Gemini")))
    monkeypatch.setattr(ui, "load_settings", lambda: load_settings(env_file=None))
    monkeypatch.setattr(ui, "live_settings_summary", lambda: ui.LiveSettingsSummary(
        True, "primary-model", (), None, GeminiSettings(api_key="test-key", model="primary-model")))
    monkeypatch.setattr(ui, "create_live_client", lambda settings: DemoWithMemoScript(memo_script))
    at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=120).run()
    at.sidebar.radio(key="mode").set_value(ui.LIVE_ANALYSIS).run()
    next(b for b in at.sidebar.button if b.label == "Run Full Deal Team").click().run()
    next(b for b in at.sidebar.button if b.label == "Generate IC Memo").click().run()
    assert not at.exception
    return at


def test_app_shows_diagnostic_and_stores_rejected_responses(monkeypatch):
    at = run_live_app(monkeypatch, [FENCED, BAD_ENUM, MISSING_HEADING])
    assert at.session_state["memo_result"] is None
    stored = at.session_state["rejected_memo_attempts"]
    assert [s["raw_response"] for s in stored] == [FENCED, BAD_ENUM, MISSING_HEADING]
    errors = " ".join(e.value for e in at.error)
    assert "Memo rejected" in errors and "Retry attempted: yes" in errors
    assert "Field or section: ## Synergies" in errors
    assert any(MISSING_HEADING in c.value for c in at.code)  # Technical details


def test_app_accepts_memo_after_retry(monkeypatch):
    at = run_live_app(monkeypatch, [FENCED])  # then the demo client's valid memo
    memo = at.session_state["memo_result"]
    assert memo.conclusion == DEMO_CONCLUSION and len(memo.rejected_attempts) == 1
    assert at.session_state["rejected_memo_attempts"] is None and not at.error


# --- Branding ----------------------------------------------------------------------------------

def text(markup):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", markup)).strip()


def test_hero_and_sidebar_branding_components():
    hero, side = uc.brand_header(), uc.sidebar_brand()
    assert text(hero).startswith("DealSignal AI M&amp;A Decision Intelligence Platform")
    assert 'class="brand-name"' in hero and 'class="mark lg"' in hero
    assert text(side) == "DealSignal AI M&amp;A Decision Intelligence Platform"
    assert 'class="sb-title"' in side and 'class="mark"' in side
    for markup in (hero, side):  # static product branding, never a deal name
        assert "Northstar" not in markup and "→" not in markup
