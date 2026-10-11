"""Strict parser for the IC Partner's machine-readable review.

The orchestrator never interprets free-form prose. Malformed reviews are rejected,
never repaired.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from agents.registry import AGENT_NAMES

APPROVED = "approved"
REVISION_REQUIRED = "revision_required"

_TOP_LEVEL_KEYS = frozenset(
    {"status", "summary", "revision_requests", "cross_workstream_issues",
     "unresolved_information_gaps"}
)
_REQUEST_KEYS = frozenset({"agent", "reason", "required_change"})


class ICReviewParseError(ValueError):
    """Raised when IC Partner output does not match the review schema exactly."""


@dataclass(frozen=True)
class RevisionRequest:
    agent: str
    reason: str
    required_change: str


@dataclass(frozen=True)
class ICReview:
    status: str
    summary: str
    revision_requests: tuple[RevisionRequest, ...]
    cross_workstream_issues: tuple[str, ...]
    unresolved_information_gaps: tuple[str, ...]

    @property
    def approved(self) -> bool:
        return self.status == APPROVED

    @property
    def flagged_agents(self) -> tuple[str, ...]:
        return tuple(r.agent for r in self.revision_requests)


def _non_empty_string(value: Any, where: str) -> str:
    if not isinstance(value, str):
        raise ICReviewParseError(f"{where} must be a string, got {type(value).__name__}")
    if not value.strip():
        raise ICReviewParseError(f"{where} must not be empty")
    return value.strip()


def _string_list(value: Any, where: str) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise ICReviewParseError(f"{where} must be a list, got {type(value).__name__}")
    return tuple(_non_empty_string(item, f"{where} item") for item in value)


def _exact_keys(obj: dict, expected: frozenset[str], where: str) -> None:
    missing = expected - obj.keys()
    extra = obj.keys() - expected
    if missing:
        raise ICReviewParseError(f"{where} is missing keys: {sorted(missing)}")
    if extra:
        raise ICReviewParseError(f"{where} has unexpected keys: {sorted(extra)}")


def _parse_request(raw: Any, index: int) -> RevisionRequest:
    where = f"revision_requests item {index}"
    if not isinstance(raw, dict):
        raise ICReviewParseError(f"{where} must be an object")
    _exact_keys(raw, _REQUEST_KEYS, where)
    agent = _non_empty_string(raw["agent"], f"{where} agent")
    if agent not in AGENT_NAMES:
        raise ICReviewParseError(
            f"{where} targets unknown agent {agent!r}; must be one of {list(AGENT_NAMES)}"
        )
    return RevisionRequest(
        agent=agent,
        reason=_non_empty_string(raw["reason"], f"{where} reason"),
        required_change=_non_empty_string(raw["required_change"], f"{where} required_change"),
    )


def parse_ic_review(text: str) -> ICReview:
    """Parse and validate IC Partner output. Raises ICReviewParseError if invalid."""
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, TypeError) as exc:
        raise ICReviewParseError(f"IC review is not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise ICReviewParseError("IC review must be a JSON object")
    _exact_keys(data, _TOP_LEVEL_KEYS, "IC review")

    status = data["status"]
    if status not in (APPROVED, REVISION_REQUIRED):
        raise ICReviewParseError(
            f"status must be {APPROVED!r} or {REVISION_REQUIRED!r}, got {status!r}"
        )
    if not isinstance(data["revision_requests"], list):
        raise ICReviewParseError("revision_requests must be a list")
    requests = tuple(_parse_request(r, i) for i, r in enumerate(data["revision_requests"]))

    targets = [r.agent for r in requests]
    duplicates = sorted({t for t in targets if targets.count(t) > 1})
    if duplicates:
        raise ICReviewParseError(f"duplicate revision requests for: {duplicates}")
    if status == APPROVED and requests:
        raise ICReviewParseError("an approved review must have no revision_requests")
    if status == REVISION_REQUIRED and not requests:
        raise ICReviewParseError("a revision_required review needs at least one revision request")

    return ICReview(
        status=status,
        summary=_non_empty_string(data["summary"], "summary"),
        revision_requests=requests,
        cross_workstream_issues=_string_list(
            data["cross_workstream_issues"], "cross_workstream_issues"),
        unresolved_information_gaps=_string_list(
            data["unresolved_information_gaps"], "unresolved_information_gaps"),
    )
