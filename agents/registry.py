"""Canonical analytical agents, their order, and their dependencies.

Pure data plus small graph helpers. The orchestrator, the IC review parser, and the
IC Partner prompt all read agent names from here so they cannot drift apart.
"""

from __future__ import annotations

from typing import Iterable

from agents.base import BaseAgent
from agents.deal_lead import DealLead
from agents.deal_structuring import DealStructuringAnalyst
from agents.due_diligence import (
    CommercialDueDiligence,
    FinancialDueDiligence,
    LegalOperationalDueDiligence,
)
from agents.risk_analyst import RiskAnalyst
from agents.strategic_fit import StrategicFit
from agents.synergy_analyst import SynergyAnalyst
from agents.valuation_analyst import ValuationAnalyst

# Topological order: every agent appears after all of its dependencies.
ANALYTICAL_AGENT_CLASSES: tuple[type[BaseAgent], ...] = (
    DealLead,
    StrategicFit,
    ValuationAnalyst,
    SynergyAnalyst,
    FinancialDueDiligence,
    CommercialDueDiligence,
    LegalOperationalDueDiligence,
    DealStructuringAnalyst,
    RiskAnalyst,
)
AGENT_NAMES: tuple[str, ...] = tuple(cls.name for cls in ANALYTICAL_AGENT_CLASSES)

DEAL_LEAD = DealLead.name
STRATEGIC_FIT = StrategicFit.name
VALUATION = ValuationAnalyst.name
SYNERGY = SynergyAnalyst.name
FINANCIAL_DD = FinancialDueDiligence.name
COMMERCIAL_DD = CommercialDueDiligence.name
LEGAL_OPS_DD = LegalOperationalDueDiligence.name
STRUCTURING = DealStructuringAnalyst.name
RISK = RiskAnalyst.name

DD_PEERS: frozenset[str] = frozenset({FINANCIAL_DD, COMMERCIAL_DD, LEGAL_OPS_DD})

_PRE_DD = (DEAL_LEAD, STRATEGIC_FIT, VALUATION, SYNERGY)

# Which agents' outputs each agent receives as PRIOR ANALYSES. DD peers never
# depend on one another.
DEPENDENCIES: dict[str, tuple[str, ...]] = {
    DEAL_LEAD: (),
    STRATEGIC_FIT: (DEAL_LEAD,),
    VALUATION: (DEAL_LEAD, STRATEGIC_FIT),
    SYNERGY: (DEAL_LEAD, STRATEGIC_FIT, VALUATION),
    FINANCIAL_DD: _PRE_DD,
    COMMERCIAL_DD: _PRE_DD,
    LEGAL_OPS_DD: _PRE_DD,
    STRUCTURING: (*_PRE_DD, FINANCIAL_DD, COMMERCIAL_DD, LEGAL_OPS_DD),
    RISK: (*_PRE_DD, FINANCIAL_DD, COMMERCIAL_DD, LEGAL_OPS_DD, STRUCTURING),
}

# Which deterministic contexts each agent receives. A single string passes that
# context directly (the agent's prompt refers to its top-level keys); a tuple passes
# a dict keyed by context name.
CALCULATIONS: dict[str, str | tuple[str, ...] | None] = {
    DEAL_LEAD: None,
    STRATEGIC_FIT: None,
    VALUATION: "valuation",
    SYNERGY: "synergy",
    FINANCIAL_DD: ("valuation", "synergy"),
    COMMERCIAL_DD: ("valuation", "synergy"),
    LEGAL_OPS_DD: ("valuation", "synergy"),
    STRUCTURING: ("valuation", "synergy", "transaction_structure"),
    RISK: ("valuation", "synergy", "transaction_structure"),
}

# Display labels for the CLI.
STEP_LABELS: dict[str, str] = {
    DEAL_LEAD: "1", STRATEGIC_FIT: "2", VALUATION: "3", SYNERGY: "4",
    FINANCIAL_DD: "5A", COMMERCIAL_DD: "5B", LEGAL_OPS_DD: "5C",
    STRUCTURING: "6", RISK: "7",
}


def _check_graph() -> None:
    assert set(DEPENDENCIES) == set(AGENT_NAMES) == set(CALCULATIONS) == set(STEP_LABELS)
    for index, name in enumerate(AGENT_NAMES):
        for dependency in DEPENDENCIES[name]:
            assert AGENT_NAMES.index(dependency) < index, f"{name} precedes {dependency}"
    for peer in DD_PEERS:
        assert not DD_PEERS & set(DEPENDENCIES[peer]), f"{peer} depends on a DD peer"


_check_graph()


def dependents_of(name: str) -> set[str]:
    """Every agent that transitively depends on `name` (excluding `name` itself)."""
    affected: set[str] = set()
    for candidate in AGENT_NAMES:  # topological order, so one pass suffices
        deps = set(DEPENDENCIES[candidate])
        if name in deps or affected & deps:
            affected.add(candidate)
    return affected


def rerun_plan(flagged: Iterable[str]) -> tuple[str, ...]:
    """Agents to rerun after revising `flagged`: the flagged agents plus everything
    downstream of any of them, each once, in topological order."""
    flagged = set(flagged)
    unknown = flagged - set(AGENT_NAMES)
    if unknown:
        raise ValueError(f"unknown agents: {sorted(unknown)}")
    affected = set(flagged)
    for name in flagged:
        affected |= dependents_of(name)
    return tuple(name for name in AGENT_NAMES if name in affected)
