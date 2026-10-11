"""Research Public Companies flow (Streamlit presentation).

Steps: jurisdiction -> company search -> identity resolution -> researched facts with
provenance -> review / override -> user assumptions -> build the existing Deal ->
deterministic preview. All research logic lives in the provider-neutral `research`
package; this module only renders it and stores results in session state.
"""

from __future__ import annotations

import streamlit as st

import ui_components as uc
import ui_helpers as ui
from deal_input import (
    FIELDS,
    MULTIPLES_NOTE,
    PERCENT_NOTE,
    SECTIONS,
    DealInputError,
    form_from_deal,
)
from demo_state import SAMPLE_DEAL_PATH
from models import load_deal
from orchestrator import DealTeamOrchestrator
from research import registry
from research.draft import create_draft
from research.errors import (
    AmbiguousCompanyError,
    CompanyNotFoundError,
    MarketPriceError,
    ResearchError,
)
from research.market_data.base import missing_price, quote_to_value
from research.models import SHARE_BASES
from research.provenance import (
    ASSUMPTION_PATHS,
    ASSUMPTION_SECTIONS,
    SHARES_PATH,
    OverrideDetails,
    approve_draft,
    build_researched_deal,
)
from ui_forms import field_widget

ROLES = ("acquirer", "target")


def html(markup: str) -> None:
    st.markdown(markup, unsafe_allow_html=True)


def _state():
    return st.session_state


def _errors(messages) -> None:
    _state().research_errors = list(messages)


# --- Research actions -------------------------------------------------------------------------------

def _research_companies(code: str, force_refresh: bool) -> None:
    state = _state()
    try:
        provider = registry.create_provider(code)
        results = {role: provider.research_company(state[f"{role}_candidate"],
                                                   force_refresh=force_refresh)
                   for role in ROLES}
    except ResearchError as exc:
        _errors([str(exc)])
        return
    acquirer = results["acquirer"]
    market = registry.create_market_price_provider(code)
    if market is None or not acquirer.ticker:
        price = missing_price("No market-price provider or ticker is available.")
    else:
        try:
            price = quote_to_value(market.get_quote(acquirer.ticker, code))
        except MarketPriceError as exc:  # never fatal to company research
            price = missing_price(f"Market price unavailable ({exc}).")
    try:
        state.research_draft = create_draft(registry.get_jurisdiction(code), acquirer,
                                            results["target"], price)
    except ResearchError as exc:
        _errors([str(exc)])
        return
    state.acquirer_research, state.target_research = acquirer, results["target"]


def _resolve(code: str, queries: dict[str, str], force_refresh: bool) -> None:
    state = _state()
    ui.clear_research_state(state)
    errors = []
    try:
        provider = registry.create_provider(code)
        for role in ROLES:
            if not queries[role].strip():
                errors.append(f"Enter a ticker or company name for the {role}.")
                continue
            try:
                state[f"{role}_candidate"] = provider.resolve_company(queries[role])
            except AmbiguousCompanyError as exc:
                state[f"{role}_candidates"] = list(exc.candidates)
            except CompanyNotFoundError as exc:
                errors.append(f"{role.title()}: {exc}")
    except ResearchError as exc:
        errors.append(str(exc))
    if errors:
        _errors(errors)
        return
    if all(state[f"{role}_candidate"] for role in ROLES):
        _research_companies(code, force_refresh)


# --- Rendering ---------------------------------------------------------------------------------------

def _jurisdiction() -> str:
    state = _state()
    available = registry.available_jurisdictions()
    labels = [j.label for j in available]
    label = st.selectbox("Research jurisdiction", labels, key="research_jurisdiction_label")
    code = available[labels.index(label)].code
    if state.get("research_jurisdiction") != code:
        ui.clear_research_state(state)
        state.research_jurisdiction = code
    planned = " ".join(uc.pill(j.label) for j in registry.planned_jurisdictions())
    html(f'<div class="pill-note" style="margin:-0.4rem 0 0.8rem">Not yet available: {planned}</div>')
    return code


def _company_card(company) -> str:
    identity = [("Registrant", company.legal_name), ("Ticker", company.ticker or "—"),
                (company.id_label, company.provider_company_id),
                ("Exchange", company.exchange or "—"),
                ("Fiscal year end", company.fiscal_year_end or "—"),
                ("Provider", company.provider_name)]
    return uc.kv_rows(identity)


def _facts(draft, provenance=None) -> None:
    """Researched facts. Once a deal is built, each row shows the EFFECTIVE value used by
    the model, with the original researched observation kept beneath any override."""
    rows_by_role = {"acquirer": [], "target": []}
    for row in draft.rows():
        rows_by_role[row.role].append(row)
    if provenance is None and any(r.value.status.value == "STALE" for r in draft.rows()):
        html(uc.callout("Some researched balance-sheet facts are stale (more than about 12 months "
                        "older than the company's latest periodic filing). They are shown for "
                        "provenance only and are not used: enter a current value, with its as-of "
                        "date and source, in the review below.", "amber", "Stale research"))
    for role, company in (("acquirer", draft.acquirer), ("target", draft.target)):
        st.markdown(f"#### {role.title()} · {company.company_name}")
        left, right = st.columns([1, 2], gap="medium")
        with left:
            html(uc.card("Identity", _company_card(company)))
        with right:
            html(uc.fact_table(ui.research_fact_rows(draft, role, provenance)))
        for r in rows_by_role[role]:
            with st.expander(f"Original research record — {company.company_name} · {r.label}"):
                html(uc.kv_rows(ui.source_details(r.value)))
                for ingredient in r.value.ingredients:
                    st.markdown(f"**Ingredient: {ingredient.field_name.replace('_', ' ')}**")
                    html(uc.kv_rows(ui.source_details(ingredient)))


def _review_form(draft) -> None:
    state = _state()
    sample = form_from_deal(load_deal(SAMPLE_DEAL_PATH))  # defaults for assumptions only
    with st.form("research_review_form"):
        st.markdown("#### Review researched inputs")
        st.caption("Accept each researched value, change it (a user override keeps the original "
                   "source in the audit trail), or enter values marked Missing.")
        entered, details = {}, {}
        for row in draft.rows():
            company = draft.acquirer if row.role == "acquirer" else draft.target
            is_shares = row.path == SHARES_PATH
            label = (f"{company.company_name} — Acquirer share basis (researched: {row.label.lower()})"
                     if is_shares else f"{company.company_name} — {row.label}")
            c1, c2, c3 = st.columns([3, 1, 3])
            with c1:
                # 6 decimals: researched amounts (reported / 10^6) round-trip exactly, so
                # an untouched value is never mistaken for a user override. A STALE or
                # MISSING value is not prefilled: the user must supply a current value.
                default = row.value.normalized_value if row.value.available else None
                entered[row.path] = field_widget(FIELDS[row.path], default, key=f"rv_{row.path}",
                                                 label=label, fmt="%.6f")
            with c2:
                html('<div style="height:1.9rem"></div>'
                     + uc.pill(ui.STATUS_TEXT[row.value.status.value],
                               ui.STATUS_TONES[row.value.status.value]))
            with c3:
                reason = st.text_input("Override reason (optional)", key=f"rr_{row.path}")
            d1, d2, d3 = st.columns([2, 3, 2])
            as_of = d1.text_input("Override as of (YYYY-MM-DD)", key=f"ro_{row.path}",
                                  label_visibility="collapsed",
                                  placeholder="Override as of (YYYY-MM-DD)")
            source = d2.text_input("Override source", key=f"rs_{row.path}",
                                   label_visibility="collapsed",
                                   placeholder="Override source (e.g. filing and concept)")
            basis = None
            if is_shares:
                basis = d3.selectbox("Share basis of an override", SHARE_BASES, key=f"rb_{row.path}",
                                     label_visibility="collapsed")
            details[row.path] = OverrideDetails(reason, as_of, source, basis)
        st.markdown("#### User assumptions")
        html(uc.pill("User assumptions", "amber")
             + '<span class="pill-note"> Entered by you — not researched facts.</span>')
        st.caption(PERCENT_NOTE + " Cash % and Stock % must total 100%.")
        target_ebitda = draft.target.fields["ebitda"]
        if target_ebitda.available and target_ebitda.normalized_value <= 0:
            html(uc.callout("Target EBITDA is non-positive. EV/EBITDA multiples are not "
                            "meaningful for this deal. Use EV/Revenue multiples instead.",
                            "navy", "Target EBITDA status: non-positive"))
        elif target_ebitda.available:
            st.caption("Target EBITDA status: positive — EV/EBITDA multiples are required; "
                       "EV/Revenue multiples are optional (supplementary).")
        else:
            st.caption("Target EBITDA status: to be entered above. " + MULTIPLES_NOTE)
        deal_name = st.text_input(
            "Deal name", value=f"{draft.acquirer.company_name} acquiring {draft.target.company_name}",
            key="research_deal_name")
        assumptions = {}
        for section, fields in SECTIONS:
            if section not in ASSUMPTION_SECTIONS:
                continue
            html(f'<div class="card-title" style="margin-top:0.6rem">{uc._e(section)}</div>')
            columns = st.columns(3)
            for i, field in enumerate(fields):
                default = (None if field.path == "transaction_assumptions.offer_enterprise_value"
                           else sample[field.path])
                with columns[i % 3]:
                    assumptions[field.path] = field_widget(field, default, key=f"ra_{field.path}")
        submitted = st.form_submit_button("Build Deal", type="primary")
    if not submitted:
        return
    try:
        provenance = approve_draft(draft, {p: v for p, v in entered.items() if v is not None},
                                   details=details)
        deal = build_researched_deal(draft, provenance,
                                     {p: v for p, v in assumptions.items() if v is not None},
                                     deal_name)
    except ResearchError as exc:
        _errors(str(exc).splitlines())
        state.researched_deal = state.research_provenance = None
    except DealInputError as exc:
        _errors(exc.errors)
        state.researched_deal = state.research_provenance = None
    else:
        state.research_provenance, state.researched_deal = provenance, deal
        state.research_errors = None
    st.rerun()


def _preview(mode, summary, run_live) -> None:
    state = _state()
    deal, provenance = state.researched_deal, state.research_provenance
    contexts = DealTeamOrchestrator.build_deterministic_contexts(deal, provenance.lineage())
    html(uc.card(f"Deal created · {deal.acquirer.name} → {deal.target.name}",
                 uc.kv_rows(ui.deal_preview(deal, contexts))))
    counts = provenance.status_counts()
    badges = " ".join(uc.pill(f"{ui.STATUS_TEXT[s]}: {n}", ui.STATUS_TONES[s])
                      for s, n in sorted(counts.items()))
    html(f'<div style="margin:0.6rem 0">{badges}</div>')
    basis = ui.share_basis_view(deal, contexts, provenance)
    st.caption(f"Acquirer share basis used in the transaction model: {basis['value']} · "
               f"{basis['definition']}" + (f" · {basis['vintage']}" if basis["vintage"] else ""))
    notes = provenance.data_quality_warnings()
    if notes:
        html(uc.callout(" ".join(notes), "amber", "Data-quality note (no value was adjusted)"))
    if provenance.overrides:
        with st.expander(f"User overrides ({len(provenance.overrides)})"):
            for line in ui.override_lines(provenance):
                st.markdown(f"- {line}")
    if mode == ui.LIVE_ANALYSIS:
        configured = bool(summary and summary.configured)
        actions = ui.action_state(mode, configured, state.deal_team_result)
        if st.button("Run Full Deal Team", key="run_from_research", type="primary",
                     disabled=not actions.can_run):
            run_live(deal, summary.settings)
    else:
        st.caption("The deterministic preview needs no API key. Switch to Live Analysis to run "
                   "the deal team on this deal.")


def research_flow(mode, summary, run_live) -> None:
    state = _state()
    with st.expander("Research public companies", expanded=state.researched_deal is None):
        html(uc.callout(
            "Financial facts are retrieved from official filings and normalized deterministically. "
            "No AI model researches or estimates financial values. Qualitative company information "
            "(strategy, customers, markets, legal) is not researched and remains an information gap.",
            "navy"))
        code = _jurisdiction()
        c1, c2 = st.columns(2)
        queries = {"acquirer": c1.text_input("Acquirer (ticker or company name)",
                                             key="research_q_acquirer"),
                   "target": c2.text_input("Target (ticker or company name)",
                                           key="research_q_target")}
        force = st.checkbox("Force refresh (bypass the local cache)", key="research_force_refresh")
        if st.button("Research Companies", type="primary"):
            _resolve(code, queries, force)
            st.rerun()  # redraw from the new state so no previous deal is rendered

        pending = [role for role in ROLES
                   if state.get(f"{role}_candidates") and not state.get(f"{role}_candidate")]
        for role in pending:
            candidates = state[f"{role}_candidates"]
            choice = st.radio(f"Several companies match the {role} — select one",
                              range(len(candidates)), format_func=lambda i, c=candidates: c[i].display,
                              key=f"research_pick_{role}")
            if st.button(f"Confirm {role}", key=f"research_confirm_{role}"):
                state[f"{role}_candidate"] = candidates[choice]
                state[f"{role}_candidates"] = None
                if all(state.get(f"{r}_candidate") for r in ROLES):
                    _research_companies(code, force)
                st.rerun()

        if state.research_errors:
            st.error("\n".join(f"- {e}" for e in state.research_errors))
        draft = state.get("research_draft")
        if draft is not None:
            _facts(draft, state.get("research_provenance"))
            _review_form(draft)
    if state.researched_deal is not None:
        _preview(mode, summary, run_live)
