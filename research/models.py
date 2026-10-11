"""Provider-neutral research domain models.

Nothing here assumes a jurisdiction, filing system, or taxonomy. Provider-specific
identifiers (a registry number, a filing reference) are carried in generic fields
(`provider_company_id`, `report_type`, `source_document_id`) and in
`provider_metadata`, a list of display-only (label, value) pairs.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class ResearchStatus(str, Enum):
    REPORTED = "REPORTED"            # taken directly from a reported fact
    DERIVED = "DERIVED"              # computed deterministically from reported facts
    MARKET = "MARKET"                # third-party market data (not accounting data)
    USER_OVERRIDE = "USER_OVERRIDE"  # entered or changed by the user
    STALE = "STALE"                  # observed, but too old for automatic use (kept for audit)
    MISSING = "MISSING"              # not found or not reliably derivable; never guessed


# Share-count definitions. They are different financial concepts and are never relabelled
# as one another; a weighted-average count may be used only as an approved modelling proxy.
POINT_IN_TIME_SHARES = "Point-in-time shares outstanding"
WEIGHTED_BASIC_SHARES = "Weighted-average basic shares (modelling proxy)"
WEIGHTED_DILUTED_SHARES = "Weighted-average diluted shares (modelling proxy)"
SHARE_BASES = (POINT_IN_TIME_SHARES, WEIGHTED_BASIC_SHARES, WEIGHTED_DILUTED_SHARES)

STATUS_LABELS = {
    ResearchStatus.REPORTED: "Reported",
    ResearchStatus.DERIVED: "Derived",
    ResearchStatus.MARKET: "Market data",
    ResearchStatus.USER_OVERRIDE: "User override",
    ResearchStatus.STALE: "Stale",
    ResearchStatus.MISSING: "Missing",
}


@dataclass(frozen=True)
class ResearchValue:
    field_name: str
    status: ResearchStatus
    normalized_value: float | None = None
    normalized_unit: str | None = None
    raw_value: str | None = None          # exact decimal string as reported
    raw_unit: str | None = None
    source_provider: str | None = None    # provider display name
    source_name: str | None = None        # dataset or document family
    source_url: str | None = None
    concept: str | None = None
    taxonomy: str | None = None
    report_type: str | None = None        # e.g. an annual report form
    fiscal_year: int | None = None
    fiscal_period: str | None = None
    period_start: str | None = None
    period_end: str | None = None
    filed_date: str | None = None
    source_document_id: str | None = None
    derivation: str | None = None
    ingredients: tuple["ResearchValue", ...] = ()
    notes: str | None = None
    provider_metadata: tuple[tuple[str, str], ...] = ()

    @property
    def available(self) -> bool:
        """Usable automatically as a deal input. A STALE value keeps its observed number
        (see `observed`) for provenance, but is never used without user approval."""
        return (self.status not in (ResearchStatus.MISSING, ResearchStatus.STALE)
                and self.normalized_value is not None)

    @property
    def observed(self) -> bool:
        """A number was researched (possibly stale)."""
        return self.status != ResearchStatus.MISSING and self.normalized_value is not None

    @property
    def period_label(self) -> str:
        if self.period_start and self.period_end:
            return f"{self.period_start} to {self.period_end}"
        if self.period_end:
            return f"as of {self.period_end}"
        return "—"


def missing_value(field_name: str, reason: str, provider: str | None = None) -> ResearchValue:
    return ResearchValue(field_name, ResearchStatus.MISSING, source_provider=provider,
                         notes=reason)


@dataclass(frozen=True)
class ResearchJurisdiction:
    code: str                  # e.g. "US", "FR", "DE"
    name: str                  # e.g. "United States"
    provider_key: str | None   # registry key of the implementing provider, if any
    provider_label: str        # e.g. "SEC"
    available: bool

    @property
    def label(self) -> str:
        suffix = self.provider_label if self.available else "coming later"
        return f"{self.name} — {suffix}"


@dataclass(frozen=True)
class CompanyCandidate:
    provider_company_id: str   # provider's stable company identifier
    name: str
    ticker: str | None
    exchange: str | None
    jurisdiction: str
    id_label: str              # how the provider names its identifier

    @property
    def display(self) -> str:
        parts = [self.name]
        if self.ticker:
            parts.append(f"Ticker: {self.ticker}")
        parts.append(f"{self.id_label}: {self.provider_company_id}")
        return " · ".join(parts)


@dataclass(frozen=True)
class SourceDocument:
    title: str
    url: str | None
    report_type: str | None
    document_id: str | None
    filed_date: str | None


@dataclass(frozen=True)
class CompanyResearchResult:
    company_name: str
    legal_name: str
    ticker: str | None
    provider_company_id: str
    id_label: str
    jurisdiction: str
    exchange: str | None
    fiscal_year_end: str | None
    reporting_currency: str
    units: str
    provider_name: str
    fields: dict[str, ResearchValue]
    source_documents: tuple[SourceDocument, ...] = ()
    notes: tuple[str, ...] = field(default_factory=tuple)

    @property
    def unresolved_fields(self) -> tuple[str, ...]:
        return tuple(name for name, value in self.fields.items() if not value.available)
