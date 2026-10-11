"""Research-layer errors (provider-neutral)."""

from __future__ import annotations

from typing import Sequence


class ResearchError(Exception):
    """Base class for company-research failures."""


class ResearchConfigurationError(ResearchError):
    """Required research configuration is missing (e.g. a provider User-Agent)."""


class CompanyNotFoundError(ResearchError):
    """No company matched the query."""


class AmbiguousCompanyError(ResearchError):
    """The query matched several companies (or needs confirmation); the user must
    choose one of `candidates`. Nothing is selected silently."""

    def __init__(self, message: str, candidates: Sequence) -> None:
        super().__init__(message)
        self.candidates = tuple(candidates)


class ProviderRequestError(ResearchError):
    """A request to a data provider failed."""


class FactNotFoundError(ResearchError):
    """A required fact could not be found or derived."""


class NormalizationError(ResearchError):
    """A value could not be normalized to the model's units."""


class MarketPriceError(ResearchError):
    """A market price could not be retrieved. Always non-fatal to company research."""
