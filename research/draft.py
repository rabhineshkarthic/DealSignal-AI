"""ResearchDealDraft: researched company facts awaiting user review."""

from __future__ import annotations

from dataclasses import dataclass

from research.errors import ResearchError
from research.models import (
    CompanyResearchResult,
    ResearchJurisdiction,
    ResearchValue,
    missing_value,
)

ACQUIRER_FIELDS = ("revenue", "ebitda", "net_income", "cash", "debt", "shares_outstanding",
                   "share_price")
TARGET_FIELDS = ("revenue", "ebitda", "net_income", "free_cash_flow", "cash", "debt")
FIELD_LABELS = {
    "revenue": "Revenue", "ebitda": "EBITDA", "net_income": "Net income",
    "free_cash_flow": "Free cash flow", "cash": "Cash", "debt": "Debt",
    "shares_outstanding": "Point-in-time shares outstanding", "share_price": "Share price",
}


@dataclass(frozen=True)
class DraftRow:
    path: str          # deal field path, e.g. "acquirer.revenue"
    role: str          # "acquirer" | "target"
    field: str
    label: str
    value: ResearchValue


@dataclass(frozen=True)
class ResearchDealDraft:
    jurisdiction: ResearchJurisdiction
    acquirer: CompanyResearchResult
    target: CompanyResearchResult
    share_price: ResearchValue
    currency: str
    units: str

    def rows(self) -> tuple[DraftRow, ...]:
        rows = []
        for role, company, fields in (("acquirer", self.acquirer, ACQUIRER_FIELDS),
                                      ("target", self.target, TARGET_FIELDS)):
            for field in fields:
                value = self.share_price if field == "share_price" else company.fields[field]
                rows.append(DraftRow(f"{role}.{field}", role, field, FIELD_LABELS[field], value))
        return tuple(rows)

    def missing_paths(self) -> tuple[str, ...]:
        return tuple(r.path for r in self.rows() if not r.value.available)


def create_draft(jurisdiction: ResearchJurisdiction, acquirer: CompanyResearchResult,
                 target: CompanyResearchResult, share_price: ResearchValue) -> ResearchDealDraft:
    if acquirer.reporting_currency != target.reporting_currency:
        raise ResearchError(
            f"The companies report in different currencies ({acquirer.reporting_currency} vs "
            f"{target.reporting_currency}); currency conversion is not supported.")
    if acquirer.units != target.units:
        raise ResearchError("The companies were researched in different units.")
    expected_unit = f"{acquirer.reporting_currency} per share"
    if share_price.available and share_price.normalized_unit != expected_unit:
        share_price = missing_value(
            "share_price", f"The market quote is in {share_price.normalized_unit}, not "
                           f"{expected_unit}; enter the share price manually.")
    return ResearchDealDraft(jurisdiction, acquirer, target, share_price,
                             acquirer.reporting_currency, acquirer.units)
