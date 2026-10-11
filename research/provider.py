"""The provider interface every jurisdiction implements.

The application depends only on this protocol and the common models, never on a
specific provider. A future France or Germany provider implements the same methods.
"""

from __future__ import annotations

from typing import Protocol

from research.models import CompanyCandidate, CompanyResearchResult

# Financial fields every provider tries to research, in model units.
RESEARCH_FIELDS: tuple[str, ...] = (
    "revenue", "ebitda", "net_income", "free_cash_flow", "cash", "debt", "shares_outstanding",
)


class CompanyResearchProvider(Protocol):
    name: str               # provider display name
    jurisdiction_code: str  # e.g. "US"
    id_label: str           # name of the provider's company identifier

    def search_company(self, query: str) -> list[CompanyCandidate]:
        """Candidate companies for a ticker or name query (possibly empty)."""

    def resolve_company(self, query: str) -> CompanyCandidate:
        """A single company for an exact identifier, or raise CompanyNotFoundError /
        AmbiguousCompanyError (carrying candidates for the user to choose from)."""

    def research_company(self, candidate: CompanyCandidate, *, units: str = "millions",
                         force_refresh: bool = False) -> CompanyResearchResult:
        """Researched financial fields for the company, normalized to `units`, with
        provenance. Fields that cannot be found or derived are MISSING, never guessed."""
