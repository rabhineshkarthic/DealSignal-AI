"""Post-generation validation of agent output: numeric provenance.

Every numeral an agent writes must have the same numeric value as a number that
appears in an approved source (DEAL DATA or DETERMINISTIC CALCULATIONS). Prompt
rules ask for this; this module enforces it.

Deliberately strict:
- Equality is by exact decimal value: "205", "205.0", and "$205" all match 205.0.
- Reformatted equivalents do NOT match: 0.25 does not authorize "25%", and 205.0
  does not authorize "205,000,000".
- A percentage is a unit. "25%" is allowed only if an approved source contains the
  percentage 25% itself; a plain 25.0 (e.g. a share price) does not authorize it.
  DEAL DATA stores rates as decimals, so in practice percentages are rejected.
- Rounding does not match: 172.024 does not authorize "172.02".
- Sign is part of the value: 20.0 does not authorize "-20", and -20.0 does not
  authorize "20". A minus counts as a sign only when it directly precedes the digits
  (e.g. "-20", "-$20", "(-20)") and is not joined to a preceding word or number, so
  a Markdown bullet ("- 20.0") and ranges ("3-5", "10.5x-12.0x") stay positive.

Known limitations (a numeric check, not a semantic one):
- A derived number that coincidentally equals some approved number passes.
- A minus separated from the number by a space ("revenue - 20") reads as positive.
- Accounting-style negatives in parentheses ("(20)") read as positive.
- Numerals in text are checked even when not financial; agents are told to write
  counts and ordinals in words for this reason.

Output is never rewritten. A violation raises AgentValidationError.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Iterable

# A numeral not glued to a preceding letter, digit, underscore, or dot (so "Q3",
# "EBITDA2", and "v1.2" fragments are ignored). Optional thousands separators and
# decimals; a trailing % is captured so the report shows the offending token.
# An optional sign ("-" or Unicode minus, optionally followed by "$") counts only
# when it is not itself glued to a preceding word, number, or closing bracket and is
# immediately followed by the digits: so "- 20" (bullet), "3-5", and "x-12" are
# unsigned, while "-20", "-$20", and "(-20)" are negative.
_NUMBER = re.compile(
    r"(?:(?<![A-Za-z0-9_.)\]%])(?P<sign>[-\u2212])\$?)?"
    r"(?<![A-Za-z0-9_.])"
    r"(?P<number>\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?|\.\d+)"
    r"(?![0-9])"
    r"(?P<percent>\s?%)?"
)

# Markdown ordered-list markers ("1. ", "  2) ") and numbered headings ("## 3. ").
_LIST_MARKER = re.compile(r"^(\s*(?:[-*+]\s+)?)\d+[.)](?=\s)")
_HEADING_NUMBER = re.compile(r"^(\s*#{1,6}\s+)\d+(?:\.\d+)*[.)]?(?=\s)")


class AgentValidationError(Exception):
    """Raised when an agent response contains numbers without an approved source."""

    def __init__(self, agent_name: str, violations: list[NumericViolation], response: str):
        self.agent_name = agent_name
        self.violations = violations
        self.response = response
        details = "; ".join(
            f"'{v.token}' (line {v.line_number}: {v.line.strip()[:120]!r})" for v in violations
        )
        super().__init__(
            f"{agent_name} output rejected: {len(violations)} number(s) not found in "
            f"DEAL DATA or DETERMINISTIC CALCULATIONS: {details}"
        )


@dataclass(frozen=True)
class NumericViolation:
    token: str
    value: Decimal
    line_number: int
    line: str


def _to_decimal(text: str) -> Decimal | None:
    try:
        return Decimal(text.replace(",", ""))
    except InvalidOperation:
        return None


def _signed_value(match: re.Match[str]) -> Decimal | None:
    value = _to_decimal(match.group("number"))
    if value is not None and match.group("sign"):
        value = -value
    return value


@dataclass(frozen=True)
class ApprovedValues:
    numbers: frozenset[Decimal]
    percentages: frozenset[Decimal]


def approved_values(sources: Iterable[Any]) -> ApprovedValues:
    """Every numeric value in the sources (including numerals inside string values),
    with percentages written in source strings (e.g. "5%") tracked separately."""
    approved: set[Decimal] = set()
    percentages: set[Decimal] = set()

    def walk(obj: Any) -> None:
        if isinstance(obj, bool) or obj is None:
            return
        if isinstance(obj, (int, float)):
            approved.add(Decimal(str(obj)))
        elif isinstance(obj, str):
            for match in _NUMBER.finditer(obj):
                value = _signed_value(match)
                if value is not None:
                    (percentages if match.group("percent") else approved).add(value)
        elif isinstance(obj, dict):
            for value in obj.values():
                walk(value)
        elif isinstance(obj, (list, tuple)):
            for value in obj:
                walk(value)

    for source in sources:
        walk(source)
    return ApprovedValues(frozenset(approved), frozenset(percentages))


def approved_numbers(sources: Iterable[Any]) -> set[Decimal]:
    """Plain (non-percentage) numeric values in the sources."""
    return set(approved_values(sources).numbers)


def _strip_markdown_numbering(line: str) -> str:
    line = _HEADING_NUMBER.sub(r"\1", line)
    return _LIST_MARKER.sub(r"\1", line)


def find_numeric_violations(response: str, sources: Iterable[Any]) -> list[NumericViolation]:
    """Return every numeral in `response` whose value is not in the approved sources."""
    approved = approved_values(sources)
    violations = []
    for line_number, line in enumerate(response.splitlines(), start=1):
        for match in _NUMBER.finditer(_strip_markdown_numbering(line)):
            value = _signed_value(match)
            if value is None:
                continue
            allowed = approved.percentages if match.group("percent") else approved.numbers
            if value not in allowed:
                violations.append(
                    NumericViolation(match.group(0).strip(), value, line_number, line)
                )
    return violations


def validate_agent_output(agent_name: str, response: str, sources: Iterable[Any]) -> str:
    """Return `response` unchanged if it passes; otherwise raise AgentValidationError."""
    violations = find_numeric_violations(response, sources)
    if violations:
        raise AgentValidationError(agent_name, violations, response)
    return response


def decoded_json_strings(text: str) -> list[str]:
    """Every string (keys and values, recursively) in `text` if it decodes as JSON.

    Used only to give the numeric validator extra text to check: JSON escapes such as
    \\n or \\u0020 can leave a number glued to a letter in the raw text, hiding it
    from the numeral pattern. Returns [] for undecodable text; nothing is repaired,
    so malformed JSON is still left to the caller's strict parser.
    """
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return []
    strings: list[str] = []

    def walk(obj: Any) -> None:
        if isinstance(obj, str):
            strings.append(obj)
        elif isinstance(obj, dict):
            for key, value in obj.items():
                strings.append(key)
                walk(value)
        elif isinstance(obj, list):
            for value in obj:
                walk(value)

    walk(data)
    return strings
