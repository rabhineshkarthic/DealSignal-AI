"""Jurisdiction-agnostic company research layer.

The application uses only the provider-neutral pieces exported here. Jurisdiction
providers live under research/providers/ and are selected by research.registry; the
United States is the first implemented jurisdiction, France and Germany are planned.
"""

from research.draft import ResearchDealDraft, create_draft
from research.errors import (
    AmbiguousCompanyError,
    CompanyNotFoundError,
    FactNotFoundError,
    MarketPriceError,
    NormalizationError,
    ProviderRequestError,
    ResearchConfigurationError,
    ResearchError,
)
from research.models import (
    CompanyCandidate,
    CompanyResearchResult,
    ResearchJurisdiction,
    ResearchStatus,
    ResearchValue,
    SourceDocument,
)
from research.provenance import (
    ApprovedField,
    DealResearchProvenance,
    approve_draft,
    build_researched_deal,
)
from research.provider import CompanyResearchProvider

__all__ = [
    "AmbiguousCompanyError", "ApprovedField", "CompanyCandidate", "CompanyNotFoundError",
    "CompanyResearchProvider", "CompanyResearchResult", "DealResearchProvenance",
    "FactNotFoundError", "MarketPriceError", "NormalizationError", "ProviderRequestError",
    "ResearchConfigurationError", "ResearchDealDraft", "ResearchError", "ResearchJurisdiction",
    "ResearchStatus", "ResearchValue", "SourceDocument", "approve_draft",
    "build_researched_deal", "create_draft",
]
