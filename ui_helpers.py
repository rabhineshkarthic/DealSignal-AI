"""Pure, testable helpers behind the Streamlit app (no Streamlit imports here).

Formatting, UI data structures, action availability, error messages, and download
bytes. Every number shown comes from Deal inputs or the deterministic contexts; these
helpers only format and arrange values, they never compute new economics.
"""

from __future__ import annotations

import re
import tempfile
from decimal import Decimal
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, MutableMapping

from agent_validation import AgentValidationError
from agents.registry import ANALYTICAL_AGENT_CLASSES, STEP_LABELS
from config import ConfigError, GeminiSettings, load_settings
from excel_export import SHEET_ORDER, ExcelExportError, export_ma_workbook
from ic_review import ICReviewParseError
from llm_client import GeminiClient, LLMError
from memo import (
    MAX_MEMO_ATTEMPTS,
    MemoGenerationError,
    MemoParseError,
    MemoRejectedError,
    MemoResult,
    current_agent_versions,
)
from models import Deal
from deal_analysis import DEFAULT_SHARE_BASIS, InputLineage, lineage_from_contexts
from research.draft import FIELD_LABELS
from research.provenance import effective_label
from orchestrator import (
    APPROVED,
    MAX_VALIDATION_RETRIES,
    REVIEW_LIMIT_REACHED,
    AgentRun,
    DealTeamResult,
    OrchestrationError,
    ReviewRound,
    ValidationRetryEvent,
)

OFFLINE_DEMO = "Offline Demo"
LIVE_ANALYSIS = "Live Analysis"
MODES = (OFFLINE_DEMO, LIVE_ANALYSIS)

SYNERGY_WARNING = ("Synergy assumptions are supplied assumptions and have not been "
                   "operationally validated.")
FINANCING_NOTE = ("The current deterministic model assumes the cash portion of the equity "
                  "purchase price is funded entirely with new debt. This is a modelling "
                  "convention, not a confirmed financing plan.")
SYNERGY_CASE_CAUTION = ("The synergy-inclusive scenario assumes full supplied run-rate "
                        "synergies and is not an expected-case forecast.")
EXCEL_DEMO_NOTE = "Qualitative sections contain illustrative offline demo content."

# --- Formatting ------------------------------------------------------------------------------

def _unit_suffix(units: str) -> str:
    return "m" if units.lower() == "millions" else f" {units}"


BILLION_THRESHOLD = Decimal(1000)  # model values in millions; >= 1,000m shows as bn


def format_amount(value: float, currency: str, units: str, decimals: int = 1) -> str:
    """Display-only money text. The single shared formatter for the UI.

    Values stay in the model's units; only the text is scaled. For "millions":
    below 1,000 -> "USD 576.8m" (existing decimals); 1,000 and above -> "USD 60.0bn"
    with 1 decimal, or 2 when the caller asked for extra precision (decimals > 1).
    Negatives use the existing "(USD 1.2bn)" convention. Decimal arithmetic avoids
    float artifacts in the scaled figure.
    """
    amount = abs(Decimal(repr(float(value))))
    if units.lower() == "millions":
        millions_text = f"{amount:,.{decimals}f}"
        if Decimal(millions_text.replace(",", "")) >= BILLION_THRESHOLD:
            bn_decimals = 1 if decimals <= 1 else 2
            text = f"{currency} {amount / BILLION_THRESHOLD:,.{bn_decimals}f}bn"
        else:
            text = f"{currency} {millions_text}m"
    else:
        text = f"{currency} {amount:,.{decimals}f}{_unit_suffix(units)}"
    return f"({text})" if value < 0 else text


def fmt_money(value: float, deal: Deal, decimals: int = 1) -> str:
    return format_amount(value, deal.currency, deal.units, decimals)


def fmt_pct(value: float, decimals: int = 1) -> str:
    """Display a supplied or deterministic decimal (e.g. 0.055) as a percentage."""
    return f"{value * 100:.{decimals}f}%"


def fmt_multiple(value: float) -> str:
    return f"{value:.1f}x"


def fmt_eps(value: float) -> str:
    return f"{value:.4f}"


def fmt_shares(value: float, deal: Deal) -> str:
    return f"{value:,.2f}{_unit_suffix(deal.units)} shares"


def fmt_factor(value: float) -> str:
    return f"{value:.4f}"


# --- Tab data ---------------------------------------------------------------------------------

def overview_metrics(deal: Deal, contexts: dict[str, Any]) -> list[tuple[str, str]]:
    calc = contexts["transaction_structure"]["calculation"]
    return [
        ("Offer Enterprise Value", fmt_money(deal.transaction_assumptions.offer_enterprise_value, deal)),
        ("Offer Equity Value", fmt_money(calc["offer_equity_value"], deal)),
        ("Cash Consideration", fmt_money(calc["cash_consideration"], deal)),
        ("Stock Consideration", fmt_money(calc["stock_consideration"], deal)),
        ("Target Net Debt", fmt_money(calc["target_net_debt"], deal)),
    ]


def company_rows(deal: Deal, provenance=None, contexts: dict[str, Any] | None = None
                 ) -> tuple[list[tuple[str, str]], list[tuple[str, str]]]:
    """Effective Deal values. With research provenance, each balance-sheet value shows
    its effective vintage (and override status); the share basis shows its definition."""
    a, t = deal.acquirer, deal.target
    acquirer = [("Revenue", "revenue", a.revenue), ("EBITDA", "ebitda", a.ebitda),
                ("Net Income", "net_income", a.net_income), ("Cash", "cash", a.cash),
                ("Debt", "debt", a.debt)]
    target = [("Revenue", "revenue", t.revenue), ("EBITDA", "ebitda", t.ebitda),
              ("Net Income", "net_income", t.net_income),
              ("Free Cash Flow", "free_cash_flow", t.free_cash_flow), ("Cash", "cash", t.cash),
              ("Debt", "debt", t.debt)]

    def rows(role, items):
        out = []
        for label, name, value in items:
            text = fmt_money(value, deal)
            approved = provenance.approved.get(f"{role}.{name}") if provenance else None
            if approved is not None and name in BALANCE_SHEET_NAMES:
                text += f" · {approved.vintage_label}"
                if approved.is_override:
                    text += " · user override"
            out.append((label, text))
        return out

    acquirer_rows = rows("acquirer", acquirer)
    basis = share_basis_view(deal, contexts, provenance)
    acquirer_rows.append(("Share basis", f"{basis['value']} · {basis['definition']}"
                          + (f" · {basis['vintage']}" if basis["vintage"] else "")))
    return acquirer_rows, rows("target", target)


BALANCE_SHEET_NAMES = ("cash", "debt")


def share_basis_view(deal: Deal, contexts: dict[str, Any] | None = None,
                     provenance=None) -> dict[str, str]:
    """The acquirer share basis the transaction model used: the value comes from the
    transaction context (exactly what finance used), the definition from the lineage
    notes. Never labelled generically as "shares outstanding"."""
    if contexts is not None:
        terms = contexts["transaction_structure"]["transaction_terms"]
        notes = contexts["transaction_structure"]["calculation_notes"]
        value = terms["acquirer_shares_outstanding"]
        definition = notes.get("acquirer_share_basis_definition", DEFAULT_SHARE_BASIS)
    else:
        value, definition = deal.acquirer.shares_outstanding, DEFAULT_SHARE_BASIS
    approved = provenance.approved.get("acquirer.shares_outstanding") if provenance else None
    vintage = ""
    if approved is not None:
        vintage = approved.vintage_label + (" · user override" if approved.is_override else "")
    return {"value": fmt_shares(value, deal), "definition": definition, "vintage": vintage}


def data_quality_notes(contexts: dict[str, Any] | None) -> tuple[str, ...]:
    if not contexts:
        return ()
    return lineage_from_contexts(contexts)[1]


def input_lineage(provenance, deal: Deal | None) -> InputLineage | None:
    """Lineage for the active research deal (None for sample/custom deals, which use
    the default share-basis definition and have no source dates)."""
    return provenance.lineage() if provenance is not None and deal is not None else None


def valuation_view(deal: Deal, contexts: dict[str, Any]) -> dict[str, Any]:
    val = contexts["valuation"]
    summary = val["valuation_summary"]
    dcf = val["dcf"]
    chart = dict(summary["by_method"])
    chart["Offer EV"] = deal.transaction_assumptions.offer_enterprise_value
    return {
        "chart": chart,
        "methods": [(m, fmt_money(v, deal), val["transaction"]["offer_vs_method"][m])
                    for m, v in summary["by_method"].items()],
        "method_rows": method_rows(deal, contexts),
        "basis": val["valuation_basis"],
        "range_available": summary["range_available"],
        "range_note": summary["range_note"],
        "low": _money_or_na(summary["low"], deal), "low_method": summary["low_method"] or "—",
        "high": _money_or_na(summary["high"], deal), "high_method": summary["high_method"] or "—",
        "offer": fmt_money(deal.transaction_assumptions.offer_enterprise_value, deal),
        "offer_vs_range": val["transaction"]["offer_vs_range"],
        "dcf_assumptions": [
            ("Base FCF", fmt_money(dcf["base_fcf"], deal)),
            ("FCF growth rate", fmt_pct(dcf["growth_rate"])),
            ("WACC", fmt_pct(dcf["wacc"])),
            ("Terminal growth rate", fmt_pct(dcf["terminal_growth_rate"])),
            ("Forecast years", str(dcf["forecast_years"])),
        ],
        "schedule": [
            {"Year": year, "FCF": fmt_money(fcf, deal, 4), "Discount Factor": fmt_factor(df),
             "Discounted FCF": fmt_money(dfcf, deal, 4)}
            for year, fcf, df, dfcf in zip(dcf["years"], dcf["forecast_fcfs"],
                                           dcf["discount_factors"], dcf["discounted_fcfs"])
        ],
        "dcf_totals": [
            ("PV of forecast FCF", fmt_money(dcf["pv_forecast_fcfs"], deal, 4)),
            ("Discounted terminal value", fmt_money(dcf["discounted_terminal_value"], deal, 4)),
            ("Terminal value share of EV", fmt_pct(dcf["terminal_value_share_of_ev"])),
        ],
    }


def _money_or_na(value: float | None, deal: Deal, decimals: int = 1) -> str:
    return "Not available" if value is None else fmt_money(value, deal, decimals)


def method_rows(deal: Deal, contexts: dict[str, Any]) -> list[tuple[str, str, str]]:
    """(method, value, status) for every valuation method, including not-applicable ones."""
    offer_vs = contexts["valuation"]["transaction"]["offer_vs_method"]
    rows = []
    for m in contexts["valuation"]["valuation_summary"]["methods"]:
        if not m["applicable"]:
            rows.append((m["method"], "Not applicable", m["reason"]))
        elif m["included_in_range"]:
            rows.append((m["method"], fmt_money(m["enterprise_value"], deal),
                         f"In range · offer is {offer_vs[m['method']]} this method"))
        else:
            rows.append((m["method"], fmt_money(m["enterprise_value"], deal),
                         "Supplementary · not in range"))
    return rows


NON_POSITIVE_EBITDA_NOTICE = (
    "Target EBITDA is non-positive. EV/EBITDA valuation is not economically meaningful; the "
    "comparable and precedent analyses therefore use EV/Revenue.")


def synergy_view(deal: Deal, contexts: dict[str, Any]) -> dict[str, Any]:
    syn = contexts["synergy"]
    calc = syn["calculation"]
    return {
        "metrics": [
            ("Annual Cost Synergy", fmt_money(calc["annual_cost_synergy"], deal)),
            ("Annual Revenue Synergy EBITDA", fmt_money(calc["annual_revenue_synergy_ebitda"], deal)),
            ("Annual After-Tax Synergies", fmt_money(calc["annual_after_tax_synergies"], deal, 4)),
            ("PV of Synergies", fmt_money(calc["pv_synergies"], deal, 4)),
            ("Integration Cost", fmt_money(calc["integration_cost"], deal)),
            ("Net Synergy Value", fmt_money(calc["net_synergy_value"], deal, 4)),
        ],
        "schedule": [
            {"Year": year, "Discounted Synergy": fmt_money(value, deal, 4)}
            for year, value in enumerate(calc["discounted_synergies"], start=1)
        ],
        "conventions": syn["calculation_notes"]["modelling_conventions"],
        "labels": syn["labels"],
    }


def transaction_view(deal: Deal, contexts: dict[str, Any]) -> dict[str, Any]:
    tx = contexts["transaction_structure"]
    calc, terms = tx["calculation"], tx["transaction_terms"]

    def case(key: str, title: str) -> dict[str, Any]:
        c = calc[key]
        return {
            "title": title,
            "rows": [
                ("Standalone EPS", fmt_eps(c["standalone_eps"])),
                ("Pro Forma EPS", fmt_eps(c["pro_forma_eps"])),
                ("EPS Change", fmt_eps(c["eps_change"])),
                ("EPS Change %", "n/a" if c["eps_change_pct"] is None
                 else fmt_pct(c["eps_change_pct"])),
            ],
            "result": c["result"],
        }

    return {
        "consideration": [
            ("Cash %", fmt_pct(terms["cash_percentage"])),
            ("Stock %", fmt_pct(terms["stock_percentage"])),
            ("Cash Consideration", fmt_money(calc["cash_consideration"], deal)),
            ("Stock Consideration", fmt_money(calc["stock_consideration"], deal)),
        ],
        "shares": [
            ("Acquirer share basis used", fmt_shares(terms["acquirer_shares_outstanding"], deal)),
            ("New Shares Issued", fmt_shares(calc["new_shares_issued"], deal)),
            ("Pro Forma Shares", fmt_shares(calc["pro_forma_shares_outstanding"], deal)),
            ("Seller Ownership", fmt_pct(calc["seller_ownership_of_pro_forma_shares"], 2)),
        ],
        "financing": [
            ("New debt under model convention",
             fmt_money(calc["new_debt_under_financing_convention"], deal)),
            ("New debt interest rate", fmt_pct(terms["new_debt_interest_rate"])),
            ("After-tax interest expense",
             fmt_money(calc["annual_after_tax_interest_expense"], deal, 4)),
        ],
        "share_basis_definition": tx["calculation_notes"].get(
            "acquirer_share_basis_definition", DEFAULT_SHARE_BASIS),
        "data_quality_notes": data_quality_notes(contexts),
        "cases": [
            case("accretion_dilution_excluding_synergies", "A. Excluding synergies"),
            case("accretion_dilution_including_run_rate_synergies",
                 "B. Including full run-rate synergies"),
        ],
    }


@dataclass(frozen=True)
class AgentCard:
    step: str
    name: str
    role: str
    output: str | None
    version: int | None
    kind: str | None
    after_review_round: int | None
    history: tuple[AgentRun, ...]


def agent_cards(result: DealTeamResult | None) -> list[AgentCard]:
    cards = []
    for cls in ANALYTICAL_AGENT_CLASSES:
        runs = tuple(result.versions(cls.name)) if result else ()
        latest = runs[-1] if runs else None
        cards.append(AgentCard(
            step=STEP_LABELS[cls.name], name=cls.name, role=cls.role,
            output=result.outputs.get(cls.name) if result else None,
            version=len(runs) or None, kind=latest.kind if latest else None,
            after_review_round=latest.after_review_round if latest else None, history=runs,
        ))
    return cards


def review_rounds(result: DealTeamResult | None) -> list[dict[str, Any]]:
    if not result:
        return []
    return [{
        "round": r.round, "status": r.review.status, "summary": r.review.summary,
        "revision_requests": [(q.agent, q.reason, q.required_change)
                              for q in r.review.revision_requests],
        "cross_workstream_issues": list(r.review.cross_workstream_issues),
        "unresolved_information_gaps": list(r.review.unresolved_information_gaps),
    } for r in result.review_history]


def revision_flows(result: DealTeamResult | None) -> list[list[str]]:
    """Audit-trail flows, e.g. ['Financial Due Diligence revised', 'Deal Structuring
    Analyst stale rerun', 'Risk Analyst stale rerun', 'IC re-review (round 2)'].
    Empty when no revisions occurred."""
    if not result:
        return []
    flows = []
    for record in result.revision_history:
        steps = [f"{name} revised" if name in record.directly_flagged
                 else f"{name} stale rerun" for name in record.rerun_order]
        next_round = record.after_review_round + 1
        if any(r.round == next_round for r in result.review_history):
            steps.append(f"IC re-review (round {next_round})")
        flows.append(steps)
    return flows


def event_label(event: AgentRun | ReviewRound | ValidationRetryEvent) -> str:
    """One progress line for a live orchestration event."""
    if isinstance(event, ValidationRetryEvent):
        who = f"[{STEP_LABELS.get(event.agent, 'IC')}] {event.agent}"
        tokens = ", ".join(event.unsupported_tokens)
        if event.outcome == "accepted_on_retry":
            return f"{who}: corrected response accepted (attempt {event.attempt})"
        if event.outcome == "retry_scheduled":
            return (f"{who}: response rejected by the numeric validator (unsupported: {tokens}); "
                    f"retrying (attempt {event.attempt + 1} of {MAX_VALIDATION_RETRIES + 1})")
        return f"{who}: response rejected on the final attempt (unsupported: {tokens})"
    if isinstance(event, ReviewRound):
        return f"IC Partner review round {event.round}: {event.review.status}"
    step = STEP_LABELS[event.agent]
    suffix = {"initial": "", "direct_revision": " (revised)",
              "stale_rerun": " (rerun: upstream revised)"}[event.kind]
    return f"[{step}] {event.agent}{suffix} completed"


# --- Actions ---------------------------------------------------------------------------------------

@dataclass(frozen=True)
class ActionState:
    can_run: bool
    can_generate_memo: bool
    can_export: bool
    memo_block_reason: str | None
    export_block_reason: str | None


def action_state(mode: str, settings_ok: bool, result: DealTeamResult | None) -> ActionState:
    if result is None:
        block = "Run the full deal team first."
    elif result.status == REVIEW_LIMIT_REACHED:
        block = ("Blocked: the IC review is unresolved (review_limit_reached), so the "
                 "Phase-3 gate does not allow a memo or export.")
    elif result.status != APPROVED:
        block = "Blocked: the analytical review did not complete."
    else:
        block = None
    approved = block is None
    return ActionState(
        can_run=mode == LIVE_ANALYSIS and settings_ok,
        can_generate_memo=mode == LIVE_ANALYSIS and settings_ok and approved,
        can_export=approved,
        memo_block_reason=block,
        export_block_reason=block,
    )


@dataclass(frozen=True)
class LiveSettingsSummary:
    configured: bool
    primary_model: str | None
    fallback_models: tuple[str, ...]
    message: str | None
    settings: GeminiSettings | None


def live_settings_summary() -> LiveSettingsSummary:
    """Model names and configuration status; the API key is never exposed."""
    try:
        settings = load_settings()
    except ConfigError as exc:
        return LiveSettingsSummary(False, None, (), str(exc), None)
    return LiveSettingsSummary(True, settings.model, settings.fallback_models, None, settings)


def create_live_client(settings: GeminiSettings) -> GeminiClient:
    """Only called from explicit Live Analysis actions."""
    return GeminiClient.from_settings(settings)


# --- Errors -----------------------------------------------------------------------------------------

@dataclass(frozen=True)
class UserMessage:
    title: str
    message: str
    details: str


def describe_error(exc: BaseException) -> UserMessage:
    """Plain-language message for known failures. Details come from our own exception
    messages, which never include the API key."""
    details = f"{type(exc).__name__}: {exc}"
    stage = ""
    cause: BaseException = exc
    if isinstance(exc, OrchestrationError):
        stage = f" during {exc.stage.replace('_', ' ')}" + (f" ({exc.agent})" if exc.agent else "")
        cause = exc.cause
    text = str(cause).lower()
    if isinstance(cause, ConfigError):
        return UserMessage("Gemini is not configured", str(cause), details)
    if isinstance(cause, LLMError):
        if "daily quota" in text or "429" in text:
            msg = "Gemini quota or rate limit reached. Wait for the quota to reset or configure fallback models."
        elif "503" in text or "unavailable" in text:
            msg = "Gemini capacity is temporarily unavailable. Try again later or configure fallback models."
        else:
            msg = "The Gemini request failed."
        return UserMessage(f"LLM error{stage}", msg, details)
    if isinstance(cause, AgentValidationError):
        tokens = ", ".join(v.token for v in cause.violations)
        details = f"{details}\n\nRejected output (not accepted):\n{cause.response}"
        return UserMessage(
            f"Output rejected by the numeric provenance validator{stage}",
            f"{cause.agent_name} used numbers not found in DEAL DATA or DETERMINISTIC "
            f"CALCULATIONS: {tokens}. The output was not accepted.", details)
    if isinstance(cause, ICReviewParseError):
        return UserMessage(f"IC review rejected{stage}",
                           "The IC Partner returned a review that does not match the strict JSON schema.",
                           details)
    if isinstance(cause, MemoGenerationError):
        return UserMessage("Memo generation blocked", str(cause), details)
    if isinstance(cause, MemoParseError):
        return describe_memo_rejection(cause)
    if isinstance(cause, ExcelExportError):
        return UserMessage("Excel export failed", str(cause), details)
    return UserMessage("Unexpected error", "An unexpected error occurred.", details)


MEMO_FAILURE_LABELS = {"parse_failure": "parse failure", "schema_mismatch": "schema mismatch",
                       "enum_mismatch": "enum mismatch"}


def memo_failure_label(category: str) -> str:
    return MEMO_FAILURE_LABELS.get(category, category.replace("_", " "))


def describe_memo_rejection(exc: MemoParseError) -> UserMessage:
    """Diagnostic for a memo the strict schema check rejected: which field or section
    failed, the failure type, and whether the memo writer was retried. Technical
    details carry every rejected raw response (none was accepted)."""
    attempts = getattr(exc, "attempts", ())
    count = len(attempts) or 1
    retry = (f"Retry attempted: yes ({count} of {MAX_MEMO_ATTEMPTS} attempts, all rejected)."
             if count > 1 else "Retry attempted: no.")
    message = (f"The memo writer's response did not match the strict memo schema. "
               f"Failure type: {memo_failure_label(exc.category)}. "
               f"Field or section: {exc.field}. {retry} No memo was accepted.")
    lines = [f"{type(exc).__name__}: {exc}"]
    for a in attempts:
        lines += ["", f"--- Attempt {a.attempt} of {MAX_MEMO_ATTEMPTS}: "
                      f"{memo_failure_label(a.category)} in {a.field} ---",
                  a.message, "Rejected raw response (not accepted):", a.raw_response]
    return UserMessage("Memo rejected", message, "\n".join(lines))


def rejected_memo_state(exc: BaseException) -> list[dict[str, Any]] | None:
    """Session-state record of rejected memo-writer responses, or None if `exc` is not
    a schema rejection. Audit only: never fed back to any agent."""
    if not isinstance(exc, MemoRejectedError):
        return None
    return [{"attempt": a.attempt, "failure_type": memo_failure_label(a.category),
             "field": a.field, "message": a.message, "raw_response": a.raw_response}
            for a in exc.attempts]


# --- Downloads -----------------------------------------------------------------------------------------

def workbook_bytes(deal: Deal, result: DealTeamResult, memo: MemoResult | None,
                   deal_source: str = "data/sample_deal.json",
                   input_sources: dict[str, str] | None = None) -> bytes:
    """Build the workbook with the exporter into a temporary file. `deal_source` labels
    where input cells came from; `input_sources` gives per-field provenance (research)."""
    with tempfile.TemporaryDirectory() as tmp:
        path = export_ma_workbook(deal, result, memo, Path(tmp) / "model.xlsx",
                                  deal_source=deal_source, input_sources=input_sources)
        return path.read_bytes()


def memo_download_bytes(memo: MemoResult) -> bytes:
    """Exactly MemoResult.memo_text, unmodified."""
    return memo.memo_text.encode("utf-8")


def _filename_part(name: str) -> str:
    first = (name.split() or ["Deal"])[0]
    return re.sub(r"[^A-Za-z0-9-]+", "", first) or "Deal"


def workbook_filename(deal: Deal) -> str:
    """Safe file name from the company names, e.g. Northstar_Atlas_MA_Model.xlsx."""
    return f"{_filename_part(deal.acquirer.name)}_{_filename_part(deal.target.name)}_MA_Model.xlsx"


# --- Static architecture content -------------------------------------------------------------------------

ARCHITECTURE_FLOW = (
    "Deal inputs: sourced public-company research or manual entry",
    "Deterministic Python finance", "AI M&A Deal Team (specialist agents)",
    "Due diligence sub-crew", "Deal structuring", "Risk", "IC Partner quality control",
    "Targeted revision cascade", "Approved analytical state", "Deal recommendation",
    "IC memo", "Excel model",
)
LLM_RESPONSIBILITIES = ("Interpretation", "Synthesis", "Diligence questions",
                        "Qualitative judgment", "Quality-control review (IC Partner)",
                        "Memo drafting")
PYTHON_RESPONSIBILITIES = ("Valuation arithmetic (comps, precedents, DCF)", "Synergy valuation",
                           "Transaction mechanics", "Accretion / dilution",
                           "Agent dependency graph", "Revision routing and review limits",
                           "Numeric provenance validation", "Strict JSON parsing",
                           "Excel formulas and formula-vs-Python validation")
CONTROLS = (
    ("Deterministic financial engine", "All valuation and transaction arithmetic runs in tested Python."),
    ("Numeric provenance validator", "Every number an agent writes must match DEAL DATA or deterministic calculations."),
    ("Source-layer isolation", "Prior analyses, previous output, and review feedback can never authorize numbers."),
    ("Strict JSON parsers", "IC reviews and memos must match exact schemas; malformed output is rejected, not repaired."),
    ("Company-name inference prohibition", "Agents may not infer products or markets from a company's name."),
    ("Evidence vs information gap", "Missing data is labelled a gap, not converted into a risk."),
    ("Bounded IC review loop", "Review rounds are capped; unresolved reviews are reported, not passed."),
    ("Dependency-aware reruns", "Only agents downstream of a revision are rerun; DD peers stay isolated."),
    ("Partial-state recovery", "Failures keep completed outputs and the audit trail for inspection."),
    ("LLM retry / model fallback", "Bounded retries and fallback on capacity or daily-quota errors."),
    ("Formula-vs-Python Excel validation", "Every workbook formula is evaluated and checked before saving."),
)
SHEETS = SHEET_ORDER


# --- Review timeline (visual view of the audit trail) -------------------------------------------------

_KIND_LABELS = {"direct_revision": "direct revision", "stale_rerun": "stale rerun"}


def review_timeline(result: DealTeamResult | None) -> list[dict[str, Any]]:
    """One entry per IC review round, with the revision requests it made and the
    reruns that followed it, taken directly from the audit trail."""
    if not result:
        return []
    records = {r.after_review_round: r for r in result.revision_history}
    rounds = []
    for review in result.review_history:
        record = records.get(review.round)
        reruns = []
        if record:
            reruns = [(name, "direct revision" if name in record.directly_flagged
                       else "stale rerun") for name in record.rerun_order]
        rounds.append({
            "round": review.round, "status": review.review.status,
            "summary": review.review.summary,
            "requests": [(q.agent, q.required_change) for q in review.review.revision_requests],
            "reruns": reruns,
        })
    return rounds


def kind_label(kind: str | None) -> str:
    return {"initial": "Initial", "direct_revision": "Direct revision",
            "stale_rerun": "Stale rerun"}.get(kind or "", "Not run")


# --- Deal source (sample vs custom) --------------------------------------------------------------------

SAMPLE_DEAL = "Sample Deal"
CUSTOM_DEAL = "Create New Deal"
RESEARCH_DEAL = "Research Public Companies"
DEAL_SOURCES = (SAMPLE_DEAL, CUSTOM_DEAL, RESEARCH_DEAL)
SAMPLE_SOURCE_LABEL = "data/sample_deal.json"
CUSTOM_SOURCE_LABEL = "custom deal input (Streamlit form)"
ANALYSIS_KEYS = ("deal_team_result", "partial_result", "memo_result", "last_error",
                 "excel_bytes", "rejected_memo_attempts")
PUBLIC_RESEARCH_HINT = ("Want to analyze a listed company? Use Research Public Companies in "
                        "the sidebar to automatically retrieve sourced financial data from SEC "
                        "filings and market data.")
DEAL_SOURCE_DESCRIPTIONS = {
    "Sample Deal": "Fictional demonstration deal.",
    "Create New Deal": "Manual inputs: private, hypothetical, or any company you enter yourself.",
    "Research Public Companies": "Automated, sourced research of listed companies (SEC filings "
                                 "and market data).",
}


def reset_if_deal_changed(state: MutableMapping[str, Any], deal: Deal | None) -> bool:
    """Clear every piece of analysis if the active deal differs from the one it was
    produced for, so results for one deal can never be shown for another. Returns
    True if anything was reset."""
    if state.get("active_deal") == deal:
        return False
    for key in ANALYSIS_KEYS:
        state[key] = None
    state["active_deal"] = deal
    return True


def deal_preview(deal: Deal, contexts: dict[str, Any]) -> list[tuple[str, str]]:
    """Deterministic figures to inspect before spending any LLM calls."""
    val = contexts["valuation"]
    summary = val["valuation_summary"]
    tx = contexts["transaction_structure"]["calculation"]
    syn = contexts["synergy"]["calculation"]
    labels = {"Comparable Companies": "Comparable companies EV",
              "Precedent Transactions": "Precedent transactions EV",
              "Comparable Companies (EV/Revenue)": "Comparable companies EV (EV/Revenue)",
              "Precedent Transactions (EV/Revenue)": "Precedent transactions EV (EV/Revenue)",
              "Discounted Cash Flow": "DCF EV"}
    methods = [(labels[m["method"]], fmt_money(m["enterprise_value"], deal)
                if m["applicable"] else "Not applicable — target EBITDA non-positive"
                if m["basis"] == "EV/EBITDA" and "non-positive" in (m["reason"] or "")
                else f"Not applicable — {m['reason']}")
               for m in summary["methods"]]
    valuation_range = (f"{fmt_money(summary['low'], deal)} – {fmt_money(summary['high'], deal)}"
                       if summary["range_available"] else f"Not available — {summary['range_note']}")
    return [
        ("Offer EV", fmt_money(deal.transaction_assumptions.offer_enterprise_value, deal)),
        ("Offer equity value", fmt_money(tx["offer_equity_value"], deal)),
        *methods,
        ("Valuation range", valuation_range),
        ("Net synergy value", fmt_money(syn["net_synergy_value"], deal, 4)),
        ("Cash / stock consideration",
         f"{fmt_money(tx['cash_consideration'], deal)} / {fmt_money(tx['stock_consideration'], deal)}"),
    ]


# --- Research view helpers ------------------------------------------------------------------------------

RESEARCH_STATE_KEYS = (
    "acquirer_research", "target_research", "acquirer_candidates", "target_candidates",
    "acquirer_candidate", "target_candidate", "research_draft", "research_provenance",
    "researched_deal", "research_errors",
)
STATUS_TONES = {"REPORTED": "green", "DERIVED": "accent", "MARKET": "market",
                "USER_OVERRIDE": "amber", "STALE": "missing", "MISSING": "missing"}
STATUS_TEXT = {"REPORTED": "Reported", "DERIVED": "Derived", "MARKET": "Market data",
               "USER_OVERRIDE": "User override", "STALE": "Stale", "MISSING": "Missing"}


RESEARCH_WIDGET_PREFIXES = ("rv_", "rr_", "ro_", "rs_", "rb_", "ra_", "research_pick_",
                            "research_deal_name")


def clear_research_state(state: MutableMapping[str, Any]) -> None:
    """Forget every research artefact (companies changed, jurisdiction changed, rerun).

    Review-form widget values are removed too: Streamlit keeps a widget's value under
    its key and ignores new defaults, so without this a new company pair would be
    prefilled with the previous pair's numbers."""
    for key in RESEARCH_STATE_KEYS:
        state[key] = None
    for key in [k for k in list(state.keys()) if isinstance(k, str)
                and k.startswith(RESEARCH_WIDGET_PREFIXES)]:
        del state[key]


def research_value_text(value, currency_units: str | None = None) -> str:
    if not value.observed:  # a STALE value still shows its observed number
        return "—"
    number = value.normalized_value
    unit = value.normalized_unit or ""
    if unit.startswith("shares"):
        return f"{number:,.6g}".rstrip() + f" {unit}"
    if "per share" in unit:
        return f"{unit.split()[0]} {number:,.2f} per share"
    currency, _, units = unit.partition(" ")
    return format_amount(number, currency, units or "units")


def research_source_text(value) -> str:
    if not value.source_provider:
        return "—"
    parts = [value.source_provider]
    if value.report_type:
        parts.append(value.report_type)
    return " · ".join(parts)


def research_concept_text(value) -> str:
    if value.derivation:
        return value.derivation
    if value.concept:
        return f"{value.taxonomy}:{value.concept}" if value.taxonomy else value.concept
    return value.notes or "—"


def source_details(value) -> list[tuple[str, str]]:
    """Every provenance attribute available for a researched value (for display)."""
    rows = [
        ("Status", STATUS_TEXT[value.status.value]),
        ("Provider", value.source_provider), ("Source", value.source_name),
        ("Report type", value.report_type), ("Filed", value.filed_date),
        ("Fiscal year / period", f"{value.fiscal_year or ''} {value.fiscal_period or ''}".strip()),
        ("Period start", value.period_start), ("Period end / as of", value.period_end),
        ("Taxonomy", value.taxonomy), ("Concept", value.concept),
        ("Source document", value.source_document_id), ("Source URL", value.source_url),
        ("Raw value", f"{value.raw_value} {value.raw_unit or ''}".strip() if value.raw_value else None),
        ("Normalized value", research_value_text(value) if value.observed else None),
        ("Derivation", value.derivation), ("Notes", value.notes),
    ]
    rows += list(value.provider_metadata)
    return [(label, str(text)) for label, text in rows if text]


def research_fact_rows(draft, role: str, provenance=None) -> list[tuple]:
    """Fact-table rows for one company. Before a deal is built, the researched values
    (STALE ones marked as not used). After it is built, the EFFECTIVE value used by the
    model, with the original researched observation beneath any override."""
    rows = []
    for r in draft.rows():
        if r.role != role:
            continue
        raw = r.value
        raw_source = f"{research_source_text(raw)} · {research_concept_text(raw)}"
        approved = provenance.approved.get(r.path) if provenance is not None else None
        if approved is None:
            tag = "Researched value" if raw.status.value != "STALE" else "Not used — stale observation"
            rows.append((r.label, research_value_text(raw), STATUS_TONES[raw.status.value],
                         STATUS_TEXT[raw.status.value], raw.period_label, raw_source, tag, None))
            continue
        value = research_value_text(_effective_as_value(approved))
        label = effective_label(approved, r.label)   # the raw observation keeps r.label
        basis = f" Share basis: {approved.basis}." if approved.basis else ""
        definition = f"Definition: {approved.basis}. " if approved.basis else ""
        if approved.is_override:
            source = "; ".join(x for x in (
                f"Override source: {approved.override_source}" if approved.override_source else "",
                f"Reason: {approved.override_reason}" if approved.override_reason else "") if x)
            original = (f"Original researched value ({r.label}): " + (
                f"{research_value_text(raw)} · {STATUS_TEXT[raw.status.value]} · "
                f"{research_source_text(raw)} · {raw.period_label}" if raw.observed else "missing"))
            rows.append((label, value, STATUS_TONES["USER_OVERRIDE"], STATUS_TEXT["USER_OVERRIDE"],
                         approved.vintage_label, (source or "User override") + basis,
                         "Effective value", definition + original))
        else:
            rows.append((label, value, STATUS_TONES[approved.status.value],
                         STATUS_TEXT[approved.status.value], approved.vintage_label,
                         raw_source + basis, "Effective value", definition.strip() or None))
    return rows


def _effective_as_value(approved):
    """The effective value formatted with the original's units (display only)."""
    return replace(approved.original, normalized_value=approved.value,
                    status=approved.original.status if approved.original.observed else approved.status,
                    normalized_unit=approved.original.normalized_unit or _unit_for(approved.path))


def _unit_for(path: str) -> str:
    return {"acquirer.shares_outstanding": "shares (millions)",
            "acquirer.share_price": "USD per share"}.get(path, "USD millions")


def override_lines(provenance) -> list[str]:
    """Audit lines for every override: effective value, vintage, definition, source,
    reason, and the original researched observation it replaced (never erased)."""
    lines = []
    for o in provenance.overrides:
        raw = o.original
        role, field = o.path.split(".", 1)
        raw_label = FIELD_LABELS[field]
        label = effective_label(o, f"{role.title()} {raw_label.lower()}")
        original = (f"{research_value_text(raw)} · {STATUS_TEXT[raw.status.value]} · "
                    f"{research_source_text(raw)} · {raw.period_label}" if raw.observed else "missing")
        parts = [f"**{label}** — effective value {research_value_text(_effective_as_value(o))} "
                 f"({o.vintage_label})"]
        if o.basis:
            parts.append(f"definition: {o.basis}")
        if o.override_source:
            parts.append(f"override source: {o.override_source}")
        if o.override_reason:
            parts.append(f"reason: {o.override_reason}")
        parts.append(f"original researched value ({raw_label}): {original}")
        lines.append(" — ".join(parts))
    return lines


# --- Deal recommendation (surfaced from the existing memo conclusion) -----------------------------------

RECOMMENDATION_SECTIONS = {
    "why": "## Investment Committee Conclusion",
    "conditions": "## Conditions / Required Next Steps",
    "risks": "## Key Risks & Unresolved Matters",
}


@dataclass(frozen=True)
class RecommendationView:
    conclusion: str                      # exactly MemoResult.conclusion
    review_round: int
    why: tuple[str, ...]                 # verbatim memo lines
    conditions: tuple[str, ...]
    risks: tuple[str, ...]
    gaps: tuple[str, ...]                # final IC review unresolved information gaps
    financial: tuple[tuple[str, str], ...]  # deterministic values only


def memo_section_lines(memo_text: str, heading: str, limit: int = 4) -> tuple[str, ...]:
    """Non-empty lines of one memo section, verbatim (only a leading list marker is
    dropped for display), at most `limit` of them."""
    lines, inside = [], False
    for line in memo_text.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            if inside:
                break
            inside = stripped == heading
            continue
        if inside and stripped:
            lines.append(re.sub(r"^([-*+]|\d+[.)])\s+", "", stripped))
    return tuple(lines[:limit])


def recommendation_view(deal: Deal | None, result: DealTeamResult | None,
                        memo: MemoResult | None) -> RecommendationView | None:
    """The recommendation exists only for a complete, approved deal case with a memo
    generated from that exact reviewed state. Otherwise there is no recommendation
    (which is not a negative recommendation)."""
    if deal is None or result is None or memo is None:
        return None
    if result.status != APPROVED or memo.orchestration_status != APPROVED:
        return None
    if not result.review_history or not result.review_history[-1].review.approved:
        return None
    if (memo.deal_name != deal.deal_name
            or memo.generated_from_review_round != result.review_history[-1].round
            or memo.source_agent_versions != current_agent_versions(result)):
        return None
    contexts = result.deterministic_contexts
    summary = contexts["valuation"]["valuation_summary"]
    offer_vs_range = contexts["valuation"]["transaction"]["offer_vs_range"]
    synergy = contexts["synergy"]
    labels = contexts["transaction_structure"]["labels"]
    financial = (
        ("Valuation range (EV)", f"{fmt_money(summary['low'], deal)} – {fmt_money(summary['high'], deal)}"
         if summary["range_available"] else "Not available (too few applicable methods)"),
        ("Offer EV", f"{fmt_money(deal.transaction_assumptions.offer_enterprise_value, deal)} "
                     f"({offer_vs_range} the range)"),
        ("Net synergy value", f"{fmt_money(synergy['calculation']['net_synergy_value'], deal, 4)} "
                              "(unvalidated assumptions)"),
        ("EPS impact · excluding synergies", labels["eps_impact_excluding_synergies"]),
        ("EPS impact · incl. run-rate synergies",
         f"{labels['eps_impact_including_run_rate_synergies']} (not the expected case)"),
        *(("Data-quality note", note) for note in data_quality_notes(contexts)),
    )
    final_review = result.review_history[-1].review
    return RecommendationView(
        conclusion=memo.conclusion, review_round=memo.generated_from_review_round,
        why=memo_section_lines(memo.memo_text, RECOMMENDATION_SECTIONS["why"]),
        conditions=memo_section_lines(memo.memo_text, RECOMMENDATION_SECTIONS["conditions"]),
        risks=memo_section_lines(memo.memo_text, RECOMMENDATION_SECTIONS["risks"]),
        gaps=tuple(final_review.unresolved_information_gaps[:4]),
        financial=financial,
    )


def validation_retries(result: DealTeamResult | None) -> list[dict[str, Any]]:
    """Audit view of numeric-validation retries (rejected responses are kept verbatim)."""
    if not result:
        return []
    return [{"agent": e.agent, "attempt": e.attempt, "category": e.failure_category,
             "tokens": ", ".join(e.unsupported_tokens) or "—", "outcome": e.outcome,
             "rejected_output": e.rejected_output} for e in result.validation_events]
