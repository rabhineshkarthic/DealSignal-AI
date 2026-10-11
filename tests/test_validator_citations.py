"""Numeric provenance around legal/statutory citations: CURRENT, UNCHANGED behaviour.

The validator has deliberately NOT been changed for citations such as
"Section 338(h)(10)": the live rejected output has not yet been inspected, so it is not
established whether 338 / 10 were statutory identifiers or unsupported financial numbers.
These tests pin today's strict behaviour (every unapproved numeral is rejected) so any
future citation exemption must be a deliberate, reviewed change.
"""

import pytest

from agent_validation import find_numeric_violations

SOURCES = [{"offer_enterprise_value": 205.0, "tax_rate": 0.25}]


def tokens(text):
    return [v.token for v in find_numeric_violations(text, SOURCES)]


@pytest.mark.parametrize("text, expected", [
    ("The buyer may consider 338 as a figure.", ["338"]),
    ("It creates USD 338m of value.", ["338"]),
    ("That is 338 shares.", ["338"]),
    ("With 338 customers.", ["338"]),
    ("A 338% premium.", ["338%"]),
    ("A 10% interest rate.", ["10%"]),
    ("Over 10 years.", ["10"]),
    ("Section analysis creates USD 338m of value.", ["338"]),
    ("Section 338(h)(10) creates 12% savings.", ["338", "10", "12%"]),
])
def test_unsupported_numerals_are_rejected(text, expected):
    assert tokens(text) == expected


@pytest.mark.parametrize("citation", ["Section 338(h)(10)", "IRC Section 338(h)(10)",
                                      "IRC §338(h)(10)", "§338(h)(10)", "Section 409A"])
def test_citations_are_currently_not_exempt(citation):
    assert tokens(f"Consider a {citation} election.")  # still rejected: no exemption added


def test_approved_values_still_accepted():
    assert tokens("Offer EV of 205.0 and a tax rate of 0.25.") == []
