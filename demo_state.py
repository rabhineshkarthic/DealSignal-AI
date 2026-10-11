"""Offline demo state: the full pipeline without Gemini, network, or an API key.

The deterministic finance (valuation, synergies, transaction mechanics, accretion) is
REAL: it comes from the same Python engine as live mode. The qualitative agent text,
IC reviews, and memo are ILLUSTRATIVE demo content written for this module and served
by a scripted stand-in client. Every such text is labelled with DEMO_LABEL.

The demo runs through the real DealTeamOrchestrator and memo generator, so all
production controls apply (numeric provenance validation, strict JSON parsing, the
dependency cascade, the Phase-3 memo gate). The scripted IC review requests one
revision so the audit trail shows a genuine revision cascade.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agents.registry import (
    COMMERCIAL_DD,
    DEAL_LEAD,
    FINANCIAL_DD,
    LEGAL_OPS_DD,
    RISK,
    STRATEGIC_FIT,
    STRUCTURING,
    SYNERGY,
    VALUATION,
)
from memo import InvestmentCommitteeMemoGenerator, MemoResult
from models import Deal, load_deal
from orchestrator import DealTeamOrchestrator, DealTeamResult

SAMPLE_DEAL_PATH = Path(__file__).resolve().parent / "data" / "sample_deal.json"
DEMO_LABEL = "Offline Demo — qualitative agent content is illustrative."
_BANNER = f"> {DEMO_LABEL} Not produced by the live deal team."
IC, MEMO_WRITER = "IC Partner", "IC Memo Writer"
DEMO_CONCLUSION = "Proceed to further diligence"

# Illustrative agent outputs. Numbers appear only where they exist in DEAL DATA or the
# deterministic calculations the agent receives; the numeric validator enforces this.
_OUTPUTS: dict[str, str] = {
    DEAL_LEAD: """# Preliminary Acquisition Thesis
## Strategic Rationale
DEAL DATA reports acquirer revenue of 650.0 and target revenue of 120.0. A relative-scale metric would need to be supplied by the deterministic calculation layer.
## Why This Target
The supplied data contains no product or capability information, so a product-led strategic rationale cannot yet be assessed.
## Potential Value Drivers
DEAL DATA ASSUMPTION: annual cost synergy of 6.0 and annual revenue synergy EBITDA of 3.0, both unvalidated.
## Key Unknowns
Market position, customers, and financing capacity have not yet been assessed.
## Preliminary Thesis
The thesis is a hypothesis to be tested through diligence.""",
    STRATEGIC_FIT: """# Strategic Fit Assessment
## Scale & Financial Context
DEAL DATA reports acquirer EBITDA of 130.0 and target EBITDA of 18.0.
## Product Fit
Product information is not supplied, so product fit cannot be assessed from the supplied data.
## Geographic Fit
Geography information is not supplied, so geographic fit cannot be assessed.
## Customer / Commercial Fit
Customer information is not supplied, so customer fit cannot be assessed.
## Buy vs Build Considerations
A buy-versus-build comparison requires product and cost data that is not supplied.
## Strategic Gaps
The Deal Lead's hypotheses remain unverified.
## Information Required
Recommended diligence action: obtain product, customer, and market data.""",
    VALUATION: """# Valuation Assessment
## Method Summary
Comparable companies: enterprise value of 189.0. Precedent transactions: 216.0. DCF: 172.024.
## Valuation Range
The deterministic range runs from 172.024 to 216.0.
## Offer Positioning
The offer enterprise value of 205.0 falls within the deterministic valuation range.
## Key Assumptions
DEAL DATA ASSUMPTIONS: comparable and precedent multiples, DCF growth, WACC, and terminal growth.
## Valuation Risks
The DCF depends heavily on WACC and terminal growth; comparables depend on peer selection.
## Information Required
Peer-set and transaction-set composition, and support for the forecast assumptions.""",
    SYNERGY: """# Synergy Assessment
## Supplied Synergy Assumptions
DEAL DATA ASSUMPTION: annual cost synergy of 6.0. DEAL DATA ASSUMPTION: annual revenue synergy EBITDA of 3.0. DEAL DATA ASSUMPTION: integration cost of 12.0.
## Deterministic Synergy Value
PV of synergies is 25.5878; net synergy value is 13.5878.
## Cost Synergy Assessment
Headcount, procurement, and systems overlap cannot be assessed from the supplied data.
## Revenue Synergy Assessment
Cross-sell and customer overlap cannot be assessed from the supplied data.
## Integration Risk
Realization may be delayed; the model assumes full run-rate from the first year.
## Key Risks
The synergy assumptions are unvalidated.
## Information Required
Recommended diligence action: obtain departmental cost and customer data.""",
    FINANCIAL_DD: """# Financial Due Diligence
## Observed Financial Evidence
OBSERVED EVIDENCE: target EBITDA of 18.0 and free cash flow of 10.0; target net debt of 20.0.
## Quality of Earnings
Quality of earnings cannot be assessed from a single reported period.
## Cash Flow & Balance Sheet Review
Working capital and capex data are not supplied.
## Key Financial Red Flags
The target has customer concentration risk.
## Information Gaps
Historical financials, working capital, capex, and customer data.
## Recommended Diligence Actions
Recommended diligence action: obtain multi-year audited financials.""",
    COMMERCIAL_DD: """# Commercial Due Diligence
## Observed Commercial Evidence
No commercial evidence is supplied.
## Market & Competitive Position
The target's industry and product category are not supplied, so market structure cannot be assessed.
## Customer Quality
Customer data is unavailable, so customer quality cannot yet be assessed.
## Product & Revenue Model
The revenue model is not supplied.
## Commercial Synergy Validation
UNVERIFIED ASSUMPTION (DEAL DATA ASSUMPTION): annual revenue synergy EBITDA of 3.0.
## Key Commercial Red Flags
No evidence-backed commercial red flag can be concluded from the supplied data alone.
## Information Gaps
Market, customer, pricing, and channel data.
## Recommended Diligence Actions
Recommended diligence action: commission a customer and market review.""",
    LEGAL_OPS_DD: """# Legal & Operational Due Diligence
## Observed Evidence
No legal or operational data is supplied.
## Legal & Regulatory Review
Contracts, litigation, IP, and regulatory exposure remain unassessed.
## Technology & Operations Review
Systems, cybersecurity, and key-person dependencies remain unassessed.
## Integration Considerations
UNVERIFIED ASSUMPTION (DEAL DATA ASSUMPTION): integration cost of 12.0 over a realization period of 5 years. No integration plan is supplied.
## Key Legal / Operational Red Flags
No evidence-backed legal or operational red flag can be concluded from the supplied data alone.
## Information Gaps
Material contracts, change-of-control terms, IP ownership, and systems inventory.
## Recommended Diligence Actions
Recommended diligence action: request a data room covering contracts and IP.""",
    STRUCTURING: """# Deal Structuring Assessment
## Proposed Consideration Structure
DEAL DATA ASSUMPTION: cash percentage of 0.6 and stock percentage of 0.4.
## Deterministic Transaction Mechanics
Cash consideration of 111.0; stock consideration of 74.0; new shares issued of 2.96.
## Financing Considerations
The model assumes the cash portion is debt funded; this is a modelling convention, not a confirmed financing plan. Financing availability has not been assessed.
## Seller / Ownership Considerations
Seller ownership of pro forma shares is 0.0287.
## Structuring Information Gaps
INFORMATION GAP: financing sources, debt terms, escrow, and earn-outs.
## Recommended Structuring Actions
Recommended structuring action: confirm financing sources and terms.""",
    RISK: """# Deal Risk Assessment
## Evidence-Backed Risks
EVIDENCE-BACKED RISK: the DCF enterprise value of 172.024 is the lowest deterministic method (valuation_summary.low_method).
## Unverified Assumptions
UNVERIFIED ASSUMPTION: synergy assumptions (synergy_assumptions) and DCF assumptions (dcf_assumptions).
## Information Gaps
Financial Due Diligence identified customer data as an information gap.
## Cross-Workstream Dependencies
The synergy-inclusive accretion case depends on unvalidated synergy assumptions.
## Risk Mitigation / Diligence Actions
Recommended diligence action: validate synergies with operational data.
## Items for Investment Committee Attention
Whether the unresolved commercial and legal gaps can be closed before commitment.""",
}

_FINANCIAL_DD_REVISED = _OUTPUTS[FINANCIAL_DD].replace(
    "The target has customer concentration risk.",
    "No evidence-backed financial red flag can be concluded from the supplied data alone. "
    "Customer concentration data is unavailable, so concentration risk cannot yet be assessed.",
)

_REVIEW_ROUND_ONE = {
    "status": "revision_required",
    "summary": "One workstream presented an information gap as a red flag.",
    "revision_requests": [{
        "agent": FINANCIAL_DD,
        "reason": "Customer concentration was stated as a red flag, but no customer data "
                  "is supplied.",
        "required_change": "Relabel customer concentration as an information gap.",
    }],
    "cross_workstream_issues": [],
    "unresolved_information_gaps": [],
}
_REVIEW_ROUND_TWO = {
    "status": "approved",
    "summary": "The revised analyses meet the quality criteria. Material diligence gaps "
               "remain open.",
    "revision_requests": [],
    "cross_workstream_issues": [
        "The synergy-inclusive accretion case depends on unvalidated synergy assumptions."],
    "unresolved_information_gaps": [
        "Customer, market, and product data are not supplied.",
        "Financing sources and terms are not supplied.",
        "Material contracts and IP ownership are unassessed.",
    ],
}


def _demo_memo() -> str:
    sections = {
        "## Transaction Overview": "The acquirer proposes an offer enterprise value of 205.0 "
        "with a cash percentage of 0.6 and a stock percentage of 0.4. Debt funding of the "
        "cash portion is a modelling convention, not a confirmed financing plan.",
        "## Executive Summary": "The offer sits within the deterministic valuation range. "
        "Synergy assumptions are unverified and material diligence gaps remain.",
        "## Strategic Rationale": "Product, customer, and market evidence is absent, so the "
        "strategic rationale remains a hypothesis.",
        "## Valuation": "Comparable companies 189.0; precedent transactions 216.0; DCF "
        "172.024; offer enterprise value 205.0; range 172.024 to 216.0.",
        "## Synergies": "Supplied assumptions remain unverified. Deterministic PV of "
        "synergies 25.5878; net synergy value 13.5878.",
        "## Financial Due Diligence": "Financial Due Diligence reports no evidence-backed "
        "red flag and lists historical financials as an information gap.",
        "## Commercial Due Diligence": "Commercial evidence is not supplied.",
        "## Legal & Operational Due Diligence": "Legal and operational areas remain "
        "unassessed.",
        "## Transaction Structure & Financing": "Excluding-synergies case: Accretive. "
        "Including-run-rate-synergies case: Accretive, which is not the expected outcome.",
        "## Key Risks & Unresolved Matters": "Information gaps in customer, financing, and "
        "legal diligence remain open.",
        "## Conditions / Required Next Steps": "Obtain customer, contract, and financing "
        "information before commitment.",
        "## Investment Committee Conclusion": f"{DEMO_CONCLUSION}. The deterministic "
        "outputs do not by themselves support commitment while diligence gaps remain open.",
    }
    body = ["# Investment Committee Memorandum", _BANNER]
    for heading, text in sections.items():
        body += [heading, text]
    return "\n\n".join(body)


class OfflineDemoLLM:
    """Scripted stand-in LLM for the offline demo. Never makes a network call."""

    def __init__(self) -> None:
        self.ic_reviews = [_REVIEW_ROUND_ONE, _REVIEW_ROUND_TWO]
        self.calls = 0
        self.last_model_used = "offline-demo (no LLM)"

    def generate(self, system_prompt: str, user_prompt: str) -> str:
        self.calls += 1
        agent = re.match(r"You are the (.+?), ", system_prompt).group(1)
        if agent == IC:
            return json.dumps(self.ic_reviews.pop(0))
        if agent == MEMO_WRITER:
            return json.dumps({"conclusion": DEMO_CONCLUSION, "memo": _demo_memo()})
        text = _OUTPUTS[agent]
        if agent == FINANCIAL_DD and "=== REVIEW FEEDBACK" in user_prompt:
            text = _FINANCIAL_DD_REVISED
        return f"{_BANNER}\n\n{text}"


@dataclass(frozen=True)
class DemoState:
    deal: Deal
    result: DealTeamResult
    memo: MemoResult
    is_demo: bool = True
    label: str = DEMO_LABEL

    @property
    def deterministic_contexts(self) -> dict[str, Any]:
        return self.result.deterministic_contexts


def build_demo_state(deal_path: Path = SAMPLE_DEAL_PATH) -> DemoState:
    """Run the real orchestrator and memo generator with the scripted demo client."""
    deal = load_deal(deal_path)
    llm = OfflineDemoLLM()
    result = DealTeamOrchestrator(llm).run(deal)
    memo = InvestmentCommitteeMemoGenerator(llm).generate(result, deal)
    return DemoState(deal=deal, result=result, memo=memo)
