"""Deterministic unit normalization with Decimal arithmetic (no float residue)."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation

from research.errors import NormalizationError

SCALES: dict[str, Decimal] = {
    "units": Decimal(1), "thousands": Decimal(10) ** 3, "millions": Decimal(10) ** 6,
    "billions": Decimal(10) ** 9,
}


def to_decimal(value: object) -> Decimal:
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise NormalizationError(f"not a number: {value!r}") from exc


def scale_for(units: str) -> Decimal:
    try:
        return SCALES[units.lower()]
    except KeyError as exc:
        raise NormalizationError(f"unsupported units {units!r}; use one of {list(SCALES)}") from exc


def normalize(raw: Decimal, units: str) -> float:
    """Scale a reported amount (e.g. USD, shares) to model units: 211915000000 -> 211915.0."""
    return float(raw / scale_for(units))


def money_unit(currency: str, units: str) -> str:
    return f"{currency} {units}"


def shares_unit(units: str) -> str:
    return f"shares ({units})"


def describe_scaling(raw_unit: str, units: str) -> str:
    return f"{raw_unit} ÷ {scale_for(units):,} → {units}"
