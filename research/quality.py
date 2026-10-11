"""Deterministic data-quality rules for researched inputs (provider-neutral).

STALENESS (point-in-time / balance-sheet facts):
  A fact whose as-of date is more than STALE_AFTER_DAYS before the reference date (the
  company's most recent periodic filing) is marked STALE. Its observed number is kept
  for provenance, but it is never used automatically as a deal input: the review step
  must supply a current, documented value.

MIXED VINTAGES (balance-sheet inputs used together for one company):
  If the effective as-of dates of cash, debt and the share basis span more than
  VINTAGE_SPREAD_DAYS, a warning is raised. The warning never changes a number and
  never blocks a calculation; derived figures such as net debt are still computed
  deterministically from the effective inputs.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date
from typing import Iterable, Sequence

from research.models import ResearchStatus, ResearchValue

STALE_AFTER_DAYS = 366        # ~12 months
VINTAGE_SPREAD_DAYS = 183     # ~two quarters / 6 months


def _date(text: str | None) -> date | None:
    try:
        return date.fromisoformat(text) if text else None
    except ValueError:
        return None


def days_between(earlier: str, later: str) -> int | None:
    a, b = _date(earlier), _date(later)
    return None if a is None or b is None else (b - a).days


def mark_if_stale(value: ResearchValue, reference_date: str | None,
                  max_age_days: int = STALE_AFTER_DAYS) -> ResearchValue:
    """Return `value` unchanged, or a STALE copy (same observed number and source) when
    its as-of date is more than `max_age_days` before `reference_date`."""
    if not value.observed or value.status == ResearchStatus.STALE or not reference_date:
        return value
    age = days_between(value.period_end, reference_date) if value.period_end else None
    if age is None or age <= max_age_days:
        return value
    note = (f"Stale: as of {value.period_end}, {age} days before the company's latest "
            f"periodic filing ({reference_date}); the limit for automatic use is "
            f"{max_age_days} days. The observed {value.status.value.lower()} value is kept "
            "for provenance only. Enter a current value (with its as-of date and source) "
            "in the review step.")
    return replace(value, status=ResearchStatus.STALE,
                   notes=f"{note} {value.notes}" if value.notes else note)


def vintage_warning(company: str, dated: Sequence[tuple[str, str | None]],
                    max_spread_days: int = VINTAGE_SPREAD_DAYS) -> str | None:
    """`dated` is (label, as-of date) for balance-sheet inputs used together. Returns a
    warning when the known dates span more than `max_spread_days`, else None."""
    known = [(label, d) for label, d in dated if _date(d) is not None]
    if len(known) < 2:
        return None
    earliest = min(known, key=lambda item: item[1])
    latest = max(known, key=lambda item: item[1])
    if days_between(earliest[1], latest[1]) <= max_spread_days:
        return None
    listing = "; ".join(f"{label} — {d}" for label, d in known)
    return (f"{company}: balance-sheet inputs use materially different reporting dates "
            f"({listing}). Derived values that combine them therefore mix reporting "
            "vintages.")


def undated(labels: Iterable[str]) -> str | None:
    labels = list(labels)
    if not labels:
        return None
    return (f"No as-of date was supplied for: {', '.join(labels)}. The reporting vintage of "
            "these inputs cannot be checked.")
