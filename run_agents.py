"""Command-line wrapper around DealTeamOrchestrator (no business logic here).

Runs the nine analytical agents on the fictional sample deal, then the bounded
IC Partner review / revision loop. Requires GEMINI_API_KEY (see .env.example).
"""

from __future__ import annotations

import sys
from pathlib import Path

from agent_validation import AgentValidationError
from agents.registry import STEP_LABELS
from config import ConfigError, load_settings
from ic_review import ICReviewParseError
from llm_client import GeminiClient, LLMError
from models import load_deal
from orchestrator import (
    AgentRun,
    DealTeamOrchestrator,
    Event,
    OrchestrationError,
    ReviewRound,
    ValidationRetryEvent,
)

SAMPLE_DEAL_PATH = Path(__file__).resolve().parent / "data" / "sample_deal.json"


def print_section(title: str, body: str, model: str | None = None) -> None:
    bar = "=" * 78
    print(f"\n{bar}\n{title}\n{bar}")
    if model:
        print(f"(model: {model})")
    print()
    print(body.strip())


def describe(event: Event, model: str | None) -> None:
    if isinstance(event, ValidationRetryEvent):
        tokens = ", ".join(event.unsupported_tokens) or "-"
        print(f"\n[validation] {event.agent} attempt {event.attempt}: {event.outcome} "
              f"(unsupported numerals: {tokens})")
        return
    if isinstance(event, AgentRun):
        label = f"[{STEP_LABELS[event.agent]}] {event.agent}"
        if event.kind == "direct_revision":
            label += f" - REVISED after IC review round {event.after_review_round}"
        elif event.kind == "stale_rerun":
            label += (f" - RERUN (upstream revised: {', '.join(event.invalidated_by)}) "
                      f"after IC review round {event.after_review_round}")
        print_section(label, event.output, model)
    elif isinstance(event, ReviewRound):
        review = event.review
        lines = [f"Status: {review.status}", f"Summary: {review.summary}"]
        lines += [f"- Revision requested for {r.agent}: {r.reason} | Required: {r.required_change}"
                  for r in review.revision_requests]
        lines += [f"- Cross-workstream issue: {i}" for i in review.cross_workstream_issues]
        lines += [f"- Unresolved information gap: {g}" for g in review.unresolved_information_gaps]
        print_section(f"IC PARTNER REVIEW - round {event.round}", "\n".join(lines), model)


EXIT_CODES = {LLMError: 1, AgentValidationError: 2, ICReviewParseError: 3}


def report_failure(exc: OrchestrationError, model: str | None) -> None:
    """Describe a failed run and the partial state kept on the exception. The error
    messages come from our own exceptions, which never include the API key."""
    err = sys.stderr
    print("\nORCHESTRATION FAILED", file=err)
    print(f"  Stage: {exc.stage}", file=err)
    if exc.agent:
        print(f"  Agent: {exc.agent}", file=err)
    if exc.review_round:
        print(f"  Review round: {exc.review_round}", file=err)
    print(f"  Cause: {type(exc.cause).__name__}: {exc.cause}", file=err)

    if isinstance(exc.cause, AgentValidationError):
        print("  Numbers not found in DEAL DATA or DETERMINISTIC CALCULATIONS:", file=err)
        for v in exc.cause.violations:
            print(f"    - '{v.token}' on line {v.line_number}: {v.line.strip()}", file=err)
        print_section(f"REJECTED OUTPUT (not accepted): {exc.cause.agent_name}",
                      exc.cause.response, model)
    if exc.raw_ic_output is not None:
        print_section("MALFORMED IC REVIEW OUTPUT (not accepted)", exc.raw_ic_output, model)

    partial = exc.partial_result
    completed = ", ".join(partial.outputs) or "(none)"
    print("\nPartial state is available on OrchestrationError.partial_result:", file=err)
    print(f"  Agents with a current output: {completed}", file=err)
    print(f"  Agent runs recorded: {len(partial.agent_runs)}", file=err)
    print(f"  IC review rounds completed: {len(partial.review_history)}", file=err)
    print(f"  Revision rounds started: {len(partial.revision_history)}", file=err)


def main() -> int:
    deal = load_deal(SAMPLE_DEAL_PATH)
    try:
        settings = load_settings()
    except ConfigError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 1

    llm = GeminiClient.from_settings(settings)
    print(f"Deal: {deal.deal_name} (fictional, illustrative)")
    print(f"Primary model: {settings.model}")
    print(f"Fallback models: {', '.join(settings.fallback_models) or '(none)'}")

    orchestrator = DealTeamOrchestrator(
        llm, on_event=lambda event: describe(event, llm.last_model_used)
    )
    try:
        result = orchestrator.run(deal)
    except OrchestrationError as exc:
        report_failure(exc, llm.last_model_used)
        return EXIT_CODES.get(type(exc.cause), 1)

    print(f"\nFINAL ORCHESTRATION STATUS: {result.status} "
          f"(IC review rounds used: {result.review_rounds_used} of "
          f"{orchestrator.max_review_rounds})")
    return 0 if result.status == "approved" else 4


if __name__ == "__main__":
    sys.exit(main())
