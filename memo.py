"""Phase 4: Investment Committee memo generation from a reviewed DealTeamResult.

The orchestrator produces a reviewed analytical state; this module consumes it. It
gates on that state deterministically, routes only the CURRENT output of each agent
to the memo writer, and parses the writer's strict JSON response. Nothing here
calculates financial values.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from agents.base import LLMClient
from agents.ic_memo_writer import (
    CONCLUSION_HEADING,
    MEMO_HEADINGS,
    PERMITTED_CONCLUSIONS,
    ICMemoWriter,
)
from agents.registry import AGENT_NAMES
from ic_review import ICReview
from deal_analysis import InputLineage, annotate_deal_context, share_basis_from_contexts
from models import Deal, deal_to_context
from orchestrator import APPROVED, REVIEW_LIMIT_REACHED, DealTeamResult

REQUIRED_CONTEXTS = ("valuation", "synergy", "transaction_structure")
_MEMO_KEYS = frozenset({"conclusion", "memo"})


class MemoGenerationError(Exception):
    """The DealTeamResult is not in a state from which a memo may be generated."""


# Failure categories reported for a rejected memo response.
PARSE_FAILURE = "parse_failure"      # not a single valid JSON object
SCHEMA_MISMATCH = "schema_mismatch"  # valid JSON, wrong keys / missing or misordered sections
ENUM_MISMATCH = "enum_mismatch"      # conclusion is not one of PERMITTED_CONCLUSIONS
MAX_MEMO_ATTEMPTS = 3                # initial attempt + 2 schema retries


class MemoParseError(ValueError):
    """The memo writer's response does not match the required schema.

    `category` is PARSE_FAILURE, SCHEMA_MISMATCH or ENUM_MISMATCH; `field` names the
    failing field or section. `feedback` describes the problem for a retry without
    quoting the rejected response."""

    def __init__(self, message: str, *, category: str = SCHEMA_MISMATCH,
                 field: str = "memo", feedback: str | None = None) -> None:
        super().__init__(message)
        self.category = category
        self.field = field
        self.feedback = feedback or message


@dataclass(frozen=True)
class MemoAttempt:
    """One memo-writer response rejected by the strict schema check (audit only; the
    raw response is never used as input to a later attempt)."""

    attempt: int
    category: str
    field: str
    message: str
    raw_response: str


class MemoRejectedError(MemoParseError):
    """Every permitted memo-writer attempt failed the strict schema check."""

    def __init__(self, attempts: tuple[MemoAttempt, ...]) -> None:
        last = attempts[-1]
        super().__init__(
            f"memo rejected after {len(attempts)} attempt(s); last failure: {last.message}",
            category=last.category, field=last.field)
        self.attempts = attempts

    @property
    def retry_attempted(self) -> bool:
        return len(self.attempts) > 1

    @property
    def raw_response(self) -> str:
        return self.attempts[-1].raw_response


@dataclass(frozen=True)
class AgentVersionRef:
    """Which run of an agent the memo was generated from."""

    agent: str
    version: int  # 1 = initial output, 2 = first replacement, ...
    kind: str
    after_review_round: int


@dataclass(frozen=True)
class MemoResult:
    memo_text: str
    conclusion: str
    deal_name: str
    orchestration_status: str
    generated_from_review_round: int
    review_rounds_used: int
    source_agent_versions: tuple[AgentVersionRef, ...]
    # Schema-rejected attempts that preceded the accepted one (audit only).
    rejected_attempts: tuple[MemoAttempt, ...] = ()


# --- Phase-3 gate -------------------------------------------------------------------

def check_memo_eligibility(result: DealTeamResult) -> ICReview:
    """Return the final approved IC review, or raise MemoGenerationError."""
    if result.status is None:
        raise MemoGenerationError(
            "orchestration has no final status; the deal-team review did not complete")
    if result.status == REVIEW_LIMIT_REACHED:
        raise MemoGenerationError(
            "the analytical review remains unresolved (review_limit_reached); an IC memo "
            "cannot be generated from an unresolved review")
    if result.status != APPROVED:
        raise MemoGenerationError(f"unsupported orchestration status {result.status!r}")

    missing = [name for name in AGENT_NAMES if not result.outputs.get(name)]
    if missing:
        raise MemoGenerationError(f"missing analytical outputs for: {missing}")
    missing_contexts = [k for k in REQUIRED_CONTEXTS if not result.deterministic_contexts.get(k)]
    if missing_contexts:
        raise MemoGenerationError(f"missing deterministic contexts: {missing_contexts}")

    if not result.review_history:
        raise MemoGenerationError("no IC review is recorded")
    final_review = result.review_history[-1].review
    if not final_review.approved:
        raise MemoGenerationError("the final recorded IC review is not an approval")
    return final_review


# --- Parsing ------------------------------------------------------------------------------

def _section(memo: str, heading: str) -> str:
    start = memo.index(heading) + len(heading)
    following = [memo.find(h, start) for h in MEMO_HEADINGS]
    following = [i for i in following if i != -1]
    return memo[start:min(following)] if following else memo[start:]


def parse_memo_response(text: str) -> tuple[str, str]:
    """Return (conclusion, memo_markdown) or raise MemoParseError. Never repairs."""
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, TypeError) as exc:
        raise MemoParseError(
            f"memo response is not valid JSON: {exc}", category=PARSE_FAILURE,
            field="response",
            feedback="the response is not a single valid JSON object (Markdown code fences, "
                     "prose before or after the object, or truncated JSON are not allowed)",
        ) from exc
    if not isinstance(data, dict):
        raise MemoParseError("memo response must be a JSON object", category=PARSE_FAILURE,
                             field="response")
    missing, extra = _MEMO_KEYS - data.keys(), data.keys() - _MEMO_KEYS
    if missing:
        raise MemoParseError(f"memo response is missing keys: {sorted(missing)}",
                             field="keys")
    if extra:
        raise MemoParseError(f"memo response has unexpected keys: {sorted(extra)}",
                             field="keys")

    conclusion, memo = data["conclusion"], data["memo"]
    if conclusion not in PERMITTED_CONCLUSIONS:
        raise MemoParseError(
            f"conclusion {conclusion!r} is not one of {list(PERMITTED_CONCLUSIONS)}",
            category=ENUM_MISMATCH, field="conclusion",
            feedback="the \"conclusion\" field is not exactly one of the permitted "
                     "conclusions (exact wording and capitalization are required)")
    if not isinstance(memo, str) or not memo.strip():
        raise MemoParseError("memo must be non-empty Markdown text", field="memo")

    positions = []
    for heading in MEMO_HEADINGS:
        if heading not in memo:
            raise MemoParseError(f"memo is missing required heading {heading!r}",
                                 field=heading)
        positions.append(memo.index(heading))
    if positions != sorted(positions):
        raise MemoParseError("memo headings are not in the required order",
                             field="memo headings")

    section = _section(memo, CONCLUSION_HEADING)
    if conclusion not in section:
        raise MemoParseError(
            "the conclusion section does not state the conclusion field",
            field=CONCLUSION_HEADING,
            feedback="the conclusion section does not state the \"conclusion\" value "
                     "verbatim (exact wording and capitalization)")
    others = [c for c in PERMITTED_CONCLUSIONS if c != conclusion and c in section]
    if others:
        raise MemoParseError(f"the conclusion section also names other conclusions: {others}",
                             field=CONCLUSION_HEADING)
    return conclusion, memo.strip()


# --- Service ------------------------------------------------------------------------------

def _review_context(review: ICReview) -> dict[str, Any]:
    return {
        "status": review.status,
        "summary": review.summary,
        "cross_workstream_issues": list(review.cross_workstream_issues),
        "unresolved_information_gaps": list(review.unresolved_information_gaps),
    }


def current_agent_versions(result: DealTeamResult) -> tuple[AgentVersionRef, ...]:
    refs = []
    for name in AGENT_NAMES:
        versions = result.versions(name)
        latest = versions[-1]
        assert latest.output == result.outputs[name], f"{name} output/history mismatch"
        refs.append(AgentVersionRef(name, len(versions), latest.kind, latest.after_review_round))
    return tuple(refs)


def schema_feedback(error: MemoParseError, attempt: int) -> str:
    """Retry instruction for the memo writer. Describes the failure only; it never
    quotes the rejected response and is not source evidence."""
    return (
        f"Your previous response (attempt {attempt} of {MAX_MEMO_ATTEMPTS}) was rejected by "
        "the strict memo schema check and was NOT accepted.\n"
        f"- Failure type: {error.category.replace('_', ' ')}\n"
        f"- Field or section: {error.field}\n"
        f"- Problem: {error.feedback}\n"
        "Return the complete memo again as one JSON object and nothing else: start with { "
        "and end with }, no Markdown code fences, no prose outside the object, exactly the "
        "keys \"conclusion\" and \"memo\", every required heading exactly as listed and in "
        "order, and the conclusion copied verbatim from the permitted list into both the "
        "\"conclusion\" field and the conclusion section. Correct the format only. This "
        "feedback is not source evidence and authorizes no fact or number."
    )


class InvestmentCommitteeMemoGenerator:
    """Validates a reviewed DealTeamResult, runs the memo writer, returns a MemoResult."""

    def __init__(self, llm: LLMClient) -> None:
        self.writer = ICMemoWriter(llm)

    def build_context(self, result: DealTeamResult, deal: Deal) -> dict[str, Any]:
        final_review = check_memo_eligibility(result)
        return {
            "deal": annotate_deal_context(deal_to_context(deal), InputLineage(
                share_basis=share_basis_from_contexts(result.deterministic_contexts))),
            "deterministic_calculations": {k: result.deterministic_contexts[k]
                                           for k in REQUIRED_CONTEXTS},
            # Current outputs only: history stays in the result, out of the prompt.
            "prior_analyses": {name: result.outputs[name] for name in AGENT_NAMES},
            "review_context": _review_context(final_review),
        }

    def generate(self, result: DealTeamResult, deal: Deal) -> MemoResult:
        """Run the memo writer, retrying ONLY the memo writer when its response fails the
        strict schema check (at most MAX_MEMO_ATTEMPTS attempts in total).

        Every attempt uses the same context: the same DEAL DATA, DETERMINISTIC
        CALCULATIONS, prior analyses and review context. A retry adds only SCHEMA
        FEEDBACK describing the failure; the rejected response itself is never sent
        back. Every attempt is numerically validated (writer.run) and schema-checked.
        A numeric-provenance failure is not retried here and propagates unchanged."""
        context = self.build_context(result, deal)  # gate runs first
        rejected: list[MemoAttempt] = []
        last_error: MemoParseError | None = None
        for attempt in range(1, MAX_MEMO_ATTEMPTS + 1):
            run_context = context
            if last_error is not None:
                run_context = {**context,
                               "schema_feedback": schema_feedback(last_error, attempt - 1)}
            raw = self.writer.run(run_context)  # numeric validation (raw + decoded)
            try:
                conclusion, memo_text = parse_memo_response(raw)
            except MemoParseError as exc:
                last_error = exc
                rejected.append(MemoAttempt(attempt, exc.category, exc.field, str(exc), raw))
                continue
            return MemoResult(
                memo_text=memo_text,
                conclusion=conclusion,
                deal_name=deal.deal_name,
                orchestration_status=result.status,
                generated_from_review_round=result.review_history[-1].round,
                review_rounds_used=result.review_rounds_used,
                source_agent_versions=current_agent_versions(result),
                rejected_attempts=tuple(rejected),
            )
        raise MemoRejectedError(tuple(rejected))
