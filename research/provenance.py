"""User approval of researched inputs, and the provenance record kept beside the Deal.

VALUE LINEAGE (one path, `resolve_effective_value`):

    RAW RESEARCHED VALUE (ResearchValue; immutable, kept even when overridden or stale)
        -> optional USER OVERRIDE (value, reason, as-of date, source, share basis)
        -> EFFECTIVE DEAL VALUE (ApprovedField.value)
        -> models.Deal -> deterministic finance, agents, memo, Excel, every view

models.Deal holds only effective values; every source detail lives here instead. No
view decides for itself which value is current: views read ApprovedField.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any, Mapping

from deal_analysis import InputLineage
from deal_input import SECTIONS, build_deal
from models import Deal
from research.draft import ResearchDealDraft
from research.errors import ResearchError
from research.models import (
    POINT_IN_TIME_SHARES,
    SHARE_BASES,
    CompanyResearchResult,
    ResearchJurisdiction,
    ResearchStatus,
    ResearchValue,
    SourceDocument,
)
from research.quality import undated, vintage_warning

ASSUMPTION_SECTIONS = ("Market valuation", "DCF assumptions", "Synergy assumptions",
                       "Transaction assumptions")
ASSUMPTION_PATHS: tuple[str, ...] = tuple(
    f.path for section, fields in SECTIONS if section in ASSUMPTION_SECTIONS for f in fields)
SETUP_PATHS = ("deal_name", "currency", "units")


SHARES_PATH = "acquirer.shares_outstanding"
BALANCE_SHEET_FIELDS = {"acquirer": ("cash", "debt", "shares_outstanding"),
                        "target": ("cash", "debt")}
BALANCE_SHEET_LABELS = {"cash": "Cash", "debt": "Debt", "shares_outstanding": "Share basis"}


@dataclass(frozen=True)
class OverrideDetails:
    """What the user supplies with an override (all optional except the value)."""

    reason: str | None = None
    as_of: str | None = None       # YYYY-MM-DD of the override value
    source: str | None = None      # e.g. the filing and concept the value came from
    basis: str | None = None       # share-count definition (acquirer shares only)


@dataclass(frozen=True)
class ApprovedField:
    """The EFFECTIVE value of one deal input, with its full lineage.

    `value` is the only number any calculation or view uses. `original` is the raw
    researched observation and is never modified, including for overrides and STALE
    facts."""

    path: str
    value: float
    status: ResearchStatus          # REPORTED / DERIVED / MARKET, or USER_OVERRIDE
    original: ResearchValue         # always retained, including for overrides
    override_reason: str | None = None
    override_as_of: str | None = None
    override_source: str | None = None
    basis: str | None = None        # share-count definition for the share basis

    @property
    def is_override(self) -> bool:
        return self.status == ResearchStatus.USER_OVERRIDE

    @property
    def as_of(self) -> str | None:
        """As-of date / period end of the EFFECTIVE value."""
        return self.override_as_of if self.is_override else self.original.period_end

    @property
    def period_start(self) -> str | None:
        return None if self.is_override else self.original.period_start

    @property
    def vintage_label(self) -> str:
        if self.is_override:
            return f"as of {self.override_as_of}" if self.override_as_of else "as-of date not supplied"
        return self.original.period_label


SHARE_BASIS_LABEL = "Acquirer share basis"


def effective_label(approved: ApprovedField, raw_label: str) -> str:
    """Label for the EFFECTIVE value. A share count approved on a basis other than
    point-in-time shares outstanding (e.g. a weighted-average proxy) is labelled as the
    acquirer share basis; the raw observation keeps `raw_label` (the original concept)."""
    if approved.path == SHARES_PATH and approved.basis and approved.basis != POINT_IN_TIME_SHARES:
        return SHARE_BASIS_LABEL
    return raw_label


def resolve_effective_value(path: str, research: ResearchValue, entered: float | None,
                            details: OverrideDetails | None = None) -> ApprovedField | None:
    """The single rule turning a researched value and an optional user entry into the
    effective deal value. Returns None when there is no usable value (MISSING, or STALE
    and not replaced). An entry identical to an available researched value is an
    acceptance, not an override. The researched value itself is never changed."""
    details = details or OverrideDetails()
    is_shares = path == SHARES_PATH
    if entered is not None and not (research.available and _same(entered, research.normalized_value)):
        basis = details.basis or (POINT_IN_TIME_SHARES if is_shares else None)
        if is_shares and basis not in SHARE_BASES:
            raise ValueError(f"unknown share basis {basis!r}")
        as_of = _clean(details.as_of)
        if as_of is not None:
            try:
                date.fromisoformat(as_of)
            except ValueError:
                raise ValueError(f"override as-of date {as_of!r} must be YYYY-MM-DD") from None
        return ApprovedField(path, float(entered), ResearchStatus.USER_OVERRIDE, research,
                             _clean(details.reason), _clean(details.as_of),
                             _clean(details.source), basis if is_shares else None)
    if research.available:
        return ApprovedField(path, research.normalized_value, research.status, research,
                             basis=POINT_IN_TIME_SHARES if is_shares else None)
    return None


def _clean(text: str | None) -> str | None:
    return (text or "").strip() or None


@dataclass(frozen=True)
class DealResearchProvenance:
    jurisdiction: ResearchJurisdiction
    provider_names: tuple[str, ...]
    acquirer: CompanyResearchResult
    target: CompanyResearchResult
    approved: dict[str, ApprovedField]
    assumption_paths: tuple[str, ...] = ASSUMPTION_PATHS

    @property
    def overrides(self) -> tuple[ApprovedField, ...]:
        return tuple(a for a in self.approved.values() if a.status == ResearchStatus.USER_OVERRIDE)

    @property
    def source_documents(self) -> tuple[SourceDocument, ...]:
        docs = {d.document_id or d.url or d.title: d
                for d in (*self.acquirer.source_documents, *self.target.source_documents)}
        return tuple(docs.values())

    def status_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for a in self.approved.values():
            counts[a.status.value] = counts.get(a.status.value, 0) + 1
        return counts

    @property
    def share_basis(self) -> str:
        approved = self.approved.get(SHARES_PATH)
        return (approved.basis or POINT_IN_TIME_SHARES) if approved else POINT_IN_TIME_SHARES

    def data_quality_warnings(self) -> tuple[str, ...]:
        """Deterministic vintage checks on the EFFECTIVE balance-sheet inputs of each
        company. Warnings only: no value is adjusted and nothing is blocked."""
        warnings = []
        for role, company in (("acquirer", self.acquirer), ("target", self.target)):
            dated, missing_dates = [], []
            for name in BALANCE_SHEET_FIELDS[role]:
                approved = self.approved.get(f"{role}.{name}")
                if approved is None:
                    continue
                if approved.as_of:
                    dated.append((BALANCE_SHEET_LABELS[name], approved.as_of))
                else:
                    missing_dates.append(f"{company.company_name} {BALANCE_SHEET_LABELS[name].lower()}")
            warning = vintage_warning(company.company_name, dated)
            if warning:
                if role == "target" and {label for label, _ in dated} >= {"Cash", "Debt"}:
                    warning += (" Target net debt (debt − cash) is still computed "
                                "deterministically from these effective values.")
                warnings.append(warning)
            note = undated(missing_dates)
            if note:
                warnings.append(note)
        return tuple(warnings)

    def lineage(self) -> InputLineage:
        """Input metadata for the deterministic contexts (never changes a number)."""
        return InputLineage(share_basis=self.share_basis,
                            data_quality_notes=self.data_quality_warnings())

    def excel_sources(self) -> dict[str, str]:
        """Source text for each Excel input cell (keyed by deal field path)."""
        sources = {p: "User assumption (not researched)" for p in self.assumption_paths}
        sources.update({p: "Research deal setup (confirmed by the user)" for p in SETUP_PATHS})
        for role, company in (("acquirer", self.acquirer), ("target", self.target)):
            sources[f"{role}.name"] = (f"{company.provider_name} — registrant name "
                                       f"({company.id_label} {company.provider_company_id})")
        for path, approved in self.approved.items():
            company = self.acquirer if path.startswith("acquirer.") else self.target
            sources[path] = _describe(approved, company)
        return sources


def _describe(approved: ApprovedField, company: CompanyResearchResult) -> str:
    """Full lineage text for one effective value (Excel comments, audit views)."""
    original = approved.original
    basis = f" Share basis: {approved.basis}." if approved.basis else ""
    if approved.is_override:
        parts = [f"EFFECTIVE VALUE {approved.value} — user override"]
        if approved.override_as_of:
            parts.append(f"as of {approved.override_as_of}")
        if approved.override_source:
            parts.append(f"override source: {approved.override_source}")
        if approved.override_reason:
            parts.append(f"reason: {approved.override_reason}")
        return " — ".join(parts) + "." + basis + " " + _original_text(original, company)
    if approved.status == ResearchStatus.MARKET:
        return f"{original.source_provider} — as of {original.period_end}"
    parts = [original.source_provider or company.provider_name, company.company_name]
    if original.report_type:
        parts.append(f"Form {original.report_type}")
    if approved.status == ResearchStatus.DERIVED:
        parts.append(f"derived: {original.derivation}")
    else:
        parts.append(f"{original.taxonomy}:{original.concept}")
        parts.append(original.period_label)
        if original.source_document_id:
            parts.append(f"document {original.source_document_id}")
    return " — ".join(parts) + ("." + basis if basis else "")


def _original_text(original: ResearchValue, company: CompanyResearchResult) -> str:
    provider = original.source_provider or company.provider_name
    if not original.observed:
        return f"Original researched value: missing ({provider})."
    concept = (original.derivation if original.derivation
               else f"{original.taxonomy}:{original.concept}" if original.concept else "")
    return (f"Original researched value (kept for provenance, not used): "
            f"{original.normalized_value} {original.normalized_unit or ''}".rstrip()
            + f" — {original.status.value} — {provider}"
            + (f" — {concept}" if concept else "")
            + f" — {original.period_label}.")


def _same(a: float, b: float | None) -> bool:
    return b is not None and Decimal(repr(float(a))) == Decimal(repr(float(b)))


def approve_draft(draft: ResearchDealDraft, overrides: Mapping[str, float] | None = None,
                  reasons: Mapping[str, str] | None = None,
                  details: Mapping[str, OverrideDetails] | None = None) -> DealResearchProvenance:
    """Resolve every researched row to its effective value (resolve_effective_value).
    Every MISSING or STALE value must be supplied by the user. Raises ResearchError
    listing each unresolved field. `details` may carry as-of date, source and share
    basis per overridden path; `reasons` is kept for callers that only pass reasons."""
    overrides, reasons, details = dict(overrides or {}), dict(reasons or {}), dict(details or {})
    approved, errors = {}, []
    for row in draft.rows():
        research = row.value
        company = draft.acquirer.company_name if row.role == "acquirer" else draft.target.company_name
        entered = overrides.get(row.path)
        if entered is not None and (isinstance(entered, bool) or not isinstance(entered, (int, float))
                                    or not math.isfinite(entered)):
            errors.append(f"{company} — {row.label}: enter a finite number.")
            continue
        detail = details.get(row.path) or OverrideDetails()
        if row.path in reasons and not detail.reason:
            detail = OverrideDetails(reasons[row.path], detail.as_of, detail.source, detail.basis)
        try:
            effective = resolve_effective_value(row.path, research, entered, detail)
        except ValueError as exc:
            errors.append(f"{company} — {row.label}: {exc}.")
            continue
        if effective is not None:
            approved[row.path] = effective
        elif research.status == ResearchStatus.STALE:
            errors.append(f"{company} — {row.label} is stale ({research.period_label}): the "
                          "researched value is not used automatically. Enter a current value "
                          "(with its as-of date and source) before building the deal.")
        else:
            note = research.notes or "not found."
            errors.append(f"{company} — {row.label} is missing: {note}"
                          if "manually" in note else
                          f"{company} — {row.label} is missing: {note} Enter it manually "
                          "before building the deal.")
    if errors:
        raise ResearchError("\n".join(errors))
    providers = tuple(dict.fromkeys(
        [draft.acquirer.provider_name, draft.target.provider_name]
        + [draft.share_price.source_provider] * draft.share_price.available))
    return DealResearchProvenance(draft.jurisdiction, providers, draft.acquirer, draft.target,
                                  approved)


def build_researched_deal(draft: ResearchDealDraft, provenance: DealResearchProvenance,
                          assumptions: Mapping[str, Any], deal_name: str | None = None) -> Deal:
    """Build the existing models.Deal through the Phase-7 builder (same validation)."""
    values: dict[str, Any] = {
        "deal_name": (deal_name or "").strip()
        or f"{draft.acquirer.company_name} acquiring {draft.target.company_name}",
        "currency": draft.currency, "units": draft.units,
        "acquirer.name": draft.acquirer.company_name, "target.name": draft.target.company_name,
    }
    values.update({path: a.value for path, a in provenance.approved.items()})
    values.update({path: assumptions[path] for path in ASSUMPTION_PATHS if path in assumptions})
    return build_deal(values)
