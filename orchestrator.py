"""Deterministic orchestration of the deal team, IC review, and revision loop.

The LLM decides WHAT needs revision (via the IC Partner's JSON review). Python
decides everything else: schema validity, which agents are stale, rerun order,
feedback routing, the review-round limit, the audit trail, and when to stop.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Literal

from agents.base import BaseAgent, LLMClient
from agents.ic_partner import ICPartner
from agents.registry import (
    AGENT_NAMES,
    ANALYTICAL_AGENT_CLASSES,
    CALCULATIONS,
    DEPENDENCIES,
    dependents_of,
    rerun_plan,
)
from deal_analysis import (
    InputLineage,
    annotate_deal_context,
    annotate_input_lineage,
    build_synergy_context,
    build_transaction_structure_context,
    build_valuation_context,
)
from agent_validation import AgentValidationError
from ic_review import ICReview, ICReviewParseError, RevisionRequest, parse_ic_review
from llm_client import LLMError
from models import Deal, deal_to_context

APPROVED = "approved"
REVIEW_LIMIT_REACHED = "review_limit_reached"

RunKind = Literal["initial", "direct_revision", "stale_rerun"]


@dataclass(frozen=True)
class AgentRun:
    """One execution of one analytical agent. Never overwritten."""

    agent: str
    output: str
    kind: RunKind
    after_review_round: int  # 0 for the initial analysis
    feedback: RevisionRequest | None = None  # only for direct revisions
    invalidated_by: tuple[str, ...] = ()  # directly flagged agents that made this stale


@dataclass(frozen=True)
class ReviewRound:
    round: int
    raw_output: str
    review: ICReview


@dataclass(frozen=True)
class RevisionRecord:
    """What one review round caused: direct revisions plus stale downstream reruns."""

    after_review_round: int
    directly_flagged: tuple[str, ...]
    feedback: dict[str, RevisionRequest]
    stale_reruns: tuple[str, ...]
    rerun_order: tuple[str, ...]


@dataclass
@dataclass(frozen=True)
class ValidationRetryEvent:
    """One agent response rejected by the numeric validator (or accepted on retry)."""

    agent: str
    attempt: int                       # 1 = original attempt
    unsupported_tokens: tuple[str, ...]
    outcome: str                       # "retry_scheduled" | "failed_after_max_attempts"
                                       # | "accepted_on_retry"
    after_review_round: int
    rejected_output: str | None = None  # the visible rejected response, for audit
    failure_category: str = "numeric_provenance_validation"


@dataclass
class DealTeamResult:
    outputs: dict[str, str]
    deterministic_contexts: dict[str, dict[str, Any]]
    review_history: list[ReviewRound] = field(default_factory=list)
    revision_history: list[RevisionRecord] = field(default_factory=list)
    agent_runs: list[AgentRun] = field(default_factory=list)
    validation_events: list[ValidationRetryEvent] = field(default_factory=list)
    status: str | None = None
    review_rounds_used: int = 0

    def versions(self, agent: str) -> list[AgentRun]:
        """Every run of `agent`, oldest first."""
        return [run for run in self.agent_runs if run.agent == agent]


Event = AgentRun | ReviewRound | ValidationRetryEvent

MAX_VALIDATION_RETRIES = 2  # initial attempt + 2 retries = 3 attempts per agent call


def validation_feedback(tokens: tuple[str, ...]) -> str:
    return (f"Your previous response contained unsupported numerals: {', '.join(tokens)}. "
            "Rewrite the response without unsupported numbers. You may quote only numbers "
            "present in DEAL DATA or DETERMINISTIC CALCULATIONS. Do not perform arithmetic or "
            "introduce derived percentages, totals, ratios, durations or monetary amounts.")

# Failures that carry partial state. Anything else (e.g. a programming error)
# propagates unwrapped.
RECOVERABLE_ERRORS = (LLMError, AgentValidationError, ICReviewParseError)

STAGE_BY_KIND: dict[str, str] = {
    "initial": "initial_analysis",
    "direct_revision": "direct_revision",
    "stale_rerun": "stale_rerun",
}


class OrchestrationError(Exception):
    """An orchestration step failed. The original error is chained as __cause__ and
    also exposed as `cause`; `partial_result` holds everything completed so far.

    stage: "initial_analysis", "direct_revision", "stale_rerun", "ic_review" (the IC
    Partner call or its numeric validation), or "ic_review_parse" (malformed JSON).
    """

    def __init__(
        self, stage: str, cause: Exception, partial_result: DealTeamResult, *,
        agent: str | None = None, review_round: int | None = None,
        raw_ic_output: str | None = None,
    ) -> None:
        self.stage = stage
        self.cause = cause
        self.partial_result = partial_result
        self.agent = agent
        self.review_round = review_round
        self.raw_ic_output = raw_ic_output
        where = f" ({agent})" if agent else ""
        when = f" in review round {review_round}" if review_round else ""
        super().__init__(
            f"orchestration failed during {stage}{where}{when}: "
            f"{type(cause).__name__}: {cause}"
        )


def format_review_feedback(request: RevisionRequest) -> str:
    return f"Reason: {request.reason}\nRequired change: {request.required_change}"


class DealTeamOrchestrator:
    """Runs the nine analytical agents, then a bounded IC review / revision loop."""

    def __init__(
        self,
        llm: LLMClient,
        *,
        max_review_rounds: int = 2,
        on_event: Callable[[Event], None] | None = None,
    ) -> None:
        if isinstance(max_review_rounds, bool) or not isinstance(max_review_rounds, int):
            raise TypeError("max_review_rounds must be an integer")
        if max_review_rounds < 1:
            raise ValueError("max_review_rounds must be >= 1")
        self.max_review_rounds = max_review_rounds
        self.agents: dict[str, BaseAgent] = {
            cls.name: cls(llm) for cls in ANALYTICAL_AGENT_CLASSES
        }
        self.ic_partner = ICPartner(llm)
        self._emit = on_event or (lambda event: None)

    # --- context building --------------------------------------------------------

    @staticmethod
    def build_deterministic_contexts(deal: Deal, lineage: InputLineage | None = None
                                     ) -> dict[str, dict[str, Any]]:
        """Deterministic finance from the effective Deal values, plus input-lineage notes
        (share-basis definition, data-quality warnings) that never change a number."""
        return annotate_input_lineage({
            "valuation": build_valuation_context(deal),
            "synergy": build_synergy_context(deal),
            "transaction_structure": build_transaction_structure_context(deal),
        }, lineage)

    @staticmethod
    def _calculations_for(name: str, contexts: dict[str, dict[str, Any]]) -> Any:
        spec = CALCULATIONS[name]
        if spec is None:
            return None
        if isinstance(spec, str):
            return contexts[spec]
        return {key: contexts[key] for key in spec}

    def _agent_context(
        self, name: str, result: DealTeamResult, deal_context: dict[str, Any],
        request: RevisionRequest | None = None,
    ) -> dict[str, Any]:
        context: dict[str, Any] = {"deal": deal_context}
        calculations = self._calculations_for(name, result.deterministic_contexts)
        if calculations is not None:
            context["deterministic_calculations"] = calculations
        prior = {dep: result.outputs[dep] for dep in DEPENDENCIES[name]}
        if prior:
            context["prior_analyses"] = prior
        if request is not None:
            context["previous_output"] = result.outputs[name]
            context["review_feedback"] = format_review_feedback(request)
        return context

    # --- execution -------------------------------------------------------------------

    def _run_agent(
        self, name: str, result: DealTeamResult, deal_context: dict[str, Any], *,
        kind: RunKind, round_: int, request: RevisionRequest | None = None,
        invalidated_by: tuple[str, ...] = (),
    ) -> None:
        context = self._agent_context(name, result, deal_context, request)
        output = self._validated_run(self.agents[name], context, result,
                                     stage=STAGE_BY_KIND[kind], round_=round_)
        run = AgentRun(name, output, kind, round_, request, invalidated_by)
        result.outputs[name] = output
        result.agent_runs.append(run)
        self._emit(run)

    def _validated_run(self, agent: BaseAgent, context: dict[str, Any], result: DealTeamResult,
                       *, stage: str, round_: int) -> str:
        """Run one agent; on a numeric-provenance rejection, retry the SAME agent with the
        SAME context plus validation feedback (never an approved numeric source), up to
        MAX_VALIDATION_RETRIES times. Every attempt passes through the same validator."""
        review_round = round_ or None
        attempt_context = context
        for attempt in range(1, MAX_VALIDATION_RETRIES + 2):
            try:
                output = agent.run(attempt_context)
            except AgentValidationError as exc:
                tokens = tuple(v.token for v in exc.violations)
                last = attempt == MAX_VALIDATION_RETRIES + 1
                event = ValidationRetryEvent(
                    agent.name, attempt, tokens,
                    "failed_after_max_attempts" if last else "retry_scheduled", round_,
                    exc.response)
                result.validation_events.append(event)
                self._emit(event)
                if last:
                    raise OrchestrationError(stage, exc, result, agent=agent.name,
                                             review_round=review_round) from exc
                attempt_context = {**context, "validation_feedback": validation_feedback(tokens)}
                continue
            except RECOVERABLE_ERRORS as exc:
                raise OrchestrationError(stage, exc, result, agent=agent.name,
                                         review_round=review_round) from exc
            if attempt > 1:
                event = ValidationRetryEvent(agent.name, attempt, (), "accepted_on_retry", round_)
                result.validation_events.append(event)
                self._emit(event)
            return output
        raise AssertionError("unreachable")

    def _review(self, round_: int, result: DealTeamResult, deal_context: dict[str, Any]) -> ICReview:
        context = {
            "deal": deal_context,
            "deterministic_calculations": dict(result.deterministic_contexts),
            "prior_analyses": {name: result.outputs[name] for name in AGENT_NAMES},
        }
        raw = self._validated_run(self.ic_partner, context, result, stage="ic_review",
                                  round_=round_)  # numeric validation happens here
        try:
            review = parse_ic_review(raw)  # schema validation happens here
        except ICReviewParseError as exc:
            raise OrchestrationError(
                "ic_review_parse", exc, result, agent=self.ic_partner.name,
                review_round=round_, raw_ic_output=raw,
            ) from exc
        record = ReviewRound(round_, raw, review)
        result.review_history.append(record)
        result.review_rounds_used = round_
        self._emit(record)
        return review

    def _apply_revisions(
        self, review: ICReview, round_: int, result: DealTeamResult, deal_context: dict[str, Any]
    ) -> None:
        requests = {r.agent: r for r in review.revision_requests}
        flagged = tuple(name for name in AGENT_NAMES if name in requests)
        order = rerun_plan(flagged)
        stale = tuple(name for name in order if name not in requests)
        result.revision_history.append(
            RevisionRecord(round_, flagged, dict(requests), stale, order)
        )
        for name in order:  # topological, so upstream replacements land first
            if name in requests:
                self._run_agent(name, result, deal_context, kind="direct_revision",
                                round_=round_, request=requests[name])
            else:
                upstream = tuple(f for f in flagged if name in dependents_of(f))
                self._run_agent(name, result, deal_context, kind="stale_rerun",
                                round_=round_, invalidated_by=upstream)

    def run_initial(self, deal: Deal, lineage: InputLineage | None = None) -> DealTeamResult:
        """Build deterministic contexts once and run the nine analytical agents."""
        deal_context = annotate_deal_context(deal_to_context(deal), lineage)
        result = DealTeamResult(
            outputs={}, deterministic_contexts=self.build_deterministic_contexts(deal, lineage)
        )
        for name in AGENT_NAMES:
            self._run_agent(name, result, deal_context, kind="initial", round_=0)
        return result

    def run(self, deal: Deal, lineage: InputLineage | None = None) -> DealTeamResult:
        """Initial analysis, then up to max_review_rounds IC reviews with targeted
        revisions between rounds. Status is APPROVED or REVIEW_LIMIT_REACHED.
        `lineage` adds input metadata (share basis, data-quality notes) to the
        deterministic contexts; it never changes a number."""
        deal_context = annotate_deal_context(deal_to_context(deal), lineage)
        result = self.run_initial(deal, lineage)

        for round_ in range(1, self.max_review_rounds + 1):
            review = self._review(round_, result, deal_context)
            if review.approved:
                result.status = APPROVED
                return result
            if round_ == self.max_review_rounds:
                break  # no further review round to check revisions against
            self._apply_revisions(review, round_, result, deal_context)

        result.status = REVIEW_LIMIT_REACHED
        return result
