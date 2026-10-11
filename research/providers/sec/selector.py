"""Deterministic period selection over SEC CompanyFacts entries.

ANNUAL (income statement / cash flow) facts:
  - form in the 10-K family, fiscal period "FY", has a start date,
  - duration 350-380 days (excludes quarterly and year-to-date facts),
  - the latest period end across all candidate concepts is chosen first; then the
    highest-priority concept that reports that period is used,
  - several facts for the same period (original, amendment, later comparatives):
    the most recently filed wins (ties broken by accession number).
INSTANT (balance sheet) facts:
  - no start date, form in the 10-K or 10-Q family,
  - latest period-end date, then most recently filed.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any, Iterable, Sequence

ANNUAL_FORMS = frozenset({"10-K", "10-K/A", "10-KT", "10-KT/A"})
INSTANT_FORMS = ANNUAL_FORMS | {"10-Q", "10-Q/A"}
ANNUAL_MIN_DAYS, ANNUAL_MAX_DAYS = 350, 380


@dataclass(frozen=True)
class Fact:
    taxonomy: str
    concept: str
    unit: str
    value: Decimal
    start: str | None
    end: str
    fy: int | None
    fp: str | None
    form: str | None
    filed: str | None
    accn: str | None

    @property
    def period(self) -> tuple[str | None, str]:
        return (self.start, self.end)


def facts_for(company_facts: dict[str, Any], taxonomy: str, concept: str, unit: str) -> list[Fact]:
    entries = (company_facts.get("facts", {}).get(taxonomy, {}).get(concept, {})
               .get("units", {}).get(unit, []))
    facts = []
    for e in entries:
        if "end" not in e or "val" not in e:
            continue
        facts.append(Fact(taxonomy, concept, unit, Decimal(str(e["val"])), e.get("start"),
                          e["end"], e.get("fy"), e.get("fp"), e.get("form"), e.get("filed"),
                          e.get("accn")))
    return facts


def _days(start: str, end: str) -> int:
    return (date.fromisoformat(end) - date.fromisoformat(start)).days


def is_annual(fact: Fact) -> bool:
    return (fact.start is not None and fact.form in ANNUAL_FORMS and fact.fp == "FY"
            and ANNUAL_MIN_DAYS <= _days(fact.start, fact.end) <= ANNUAL_MAX_DAYS)


def is_instant(fact: Fact) -> bool:
    return fact.start is None and fact.form in INSTANT_FORMS


def most_recently_filed(facts: Iterable[Fact]) -> Fact:
    """Deterministic tie-break for facts describing the same period."""
    return max(facts, key=lambda f: (f.filed or "", f.accn or ""))


def select_annual(company_facts, concepts: Sequence[tuple[str, str]], unit: str) -> Fact | None:
    by_concept = {c: [f for f in facts_for(company_facts, *c, unit) if is_annual(f)]
                  for c in concepts}
    ends = [f.end for facts in by_concept.values() for f in facts]
    if not ends:
        return None
    latest_end = max(ends)
    for concept in concepts:  # priority order
        same_period = [f for f in by_concept[concept] if f.end == latest_end]
        if same_period:
            return most_recently_filed(same_period)
    return None


def select_annual_for_period(company_facts, concepts: Sequence[tuple[str, str]], unit: str,
                             period: tuple[str | None, str]) -> Fact | None:
    """An annual fact for exactly `period` (used to align derivation ingredients)."""
    for concept in concepts:
        matches = [f for f in facts_for(company_facts, *concept, unit)
                   if is_annual(f) and f.period == period]
        if matches:
            return most_recently_filed(matches)
    return None


def select_instant(company_facts, concepts: Sequence[tuple[str, str]], unit: str) -> Fact | None:
    by_concept = {c: [f for f in facts_for(company_facts, *c, unit) if is_instant(f)]
                  for c in concepts}
    ends = [f.end for facts in by_concept.values() for f in facts]
    if not ends:
        return None
    latest_end = max(ends)
    for concept in concepts:
        same_date = [f for f in by_concept[concept] if f.end == latest_end]
        if same_date:
            return most_recently_filed(same_date)
    return None


def instant_facts_by_date(company_facts, taxonomy: str, concept: str, unit: str) -> dict[str, Fact]:
    """Latest-filed instant fact for each balance-sheet date."""
    grouped: dict[str, list[Fact]] = {}
    for f in facts_for(company_facts, taxonomy, concept, unit):
        if is_instant(f):
            grouped.setdefault(f.end, []).append(f)
    return {end: most_recently_filed(facts) for end, facts in grouped.items()}
