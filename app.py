"""DealSignal AI — M&A Decision Intelligence Platform (Streamlit presentation layer).

The analysis itself is performed by the AI M&A Deal Team workflow (orchestrator.py)
on top of the deterministic finance engine; this module only renders it.

Launch:  streamlit run app.py

Offline Demo (default) needs no API key: finance is real, qualitative content is
illustrative. Live Analysis runs the real orchestrator with Gemini, only when the
user clicks an action.
"""

from __future__ import annotations

import pandas as pd
import streamlit as st

import ui_components as uc
import ui_helpers as ui
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
from deal_input import (
    MULTIPLES_NOTE,
    PERCENT_NOTE,
    SECTIONS,
    DealInputError,
    build_deal,
    form_from_deal,
)
from research_ui import research_flow
from ui_forms import field_widget
from demo_state import DEMO_LABEL, SAMPLE_DEAL_PATH, build_demo_state
from memo import InvestmentCommitteeMemoGenerator
from models import load_deal
from orchestrator import DealTeamOrchestrator, OrchestrationError

st.set_page_config(page_title="DealSignal AI — M&A Decision Intelligence", page_icon="📊",
                   layout="wide")
st.markdown(uc.CSS, unsafe_allow_html=True)

LIVE_KEYS = ("deal_team_result", "partial_result", "memo_result", "last_error",
             "rejected_memo_attempts")
PRIMARY_SECTIONS = ("Overview", "Financial Analysis", "Deal Team", "IC Review", "Deliverables")
FINANCIAL_TABS = ("Valuation", "Synergies", "Transaction & Accretion")
DELIVERABLE_TABS = ("IC Memo", "Excel Model", "Architecture")
DEAL_TEAM_FLOW = ((DEAL_LEAD,), (STRATEGIC_FIT,), (VALUATION, SYNERGY),
                  (FINANCIAL_DD, COMMERCIAL_DD, LEGAL_OPS_DD), (STRUCTURING,), (RISK,))


def html(markup: str) -> None:
    st.markdown(markup, unsafe_allow_html=True)


@st.cache_resource(show_spinner="Building offline demo state…")
def demo_state():
    return build_demo_state()


@st.cache_resource
def sample_deal():
    return load_deal(SAMPLE_DEAL_PATH)


def init_state() -> None:
    st.session_state.setdefault("mode", ui.OFFLINE_DEMO)
    st.session_state.setdefault("deal_source", ui.SAMPLE_DEAL)
    st.session_state.setdefault("hypothetical_label", False)
    for key in (*LIVE_KEYS, "excel_bytes", "custom_deal", "custom_errors", "active_deal",
                "research_jurisdiction", *ui.RESEARCH_STATE_KEYS):
        st.session_state.setdefault(key, None)


def active_provenance():
    """Research provenance of the active deal, or None for sample/custom deals."""
    if st.session_state.deal_source != ui.RESEARCH_DEAL:
        return None
    return st.session_state.get("research_provenance")


def active_lineage(deal):
    """The single source of input lineage (share basis, data-quality notes) for every
    consumer of the active deal: live run, previews and views."""
    return ui.input_lineage(active_provenance(), deal)


def data_quality_callout(notes) -> None:
    if notes:
        html(uc.callout(" ".join(notes), "amber", "Data-quality note (no value was adjusted)"))


def hypothetical(source: str) -> bool:
    """The user opted to label a user-built deal as hypothetical."""
    return source != ui.SAMPLE_DEAL and bool(st.session_state.hypothetical_label)


def show_error(message: ui.UserMessage) -> None:
    st.error(f"**{message.title}.** {message.message}")
    with st.expander("Technical details"):
        st.code(message.details, language=None)


# --- Live actions (unchanged behaviour) ------------------------------------------------------------

def run_live(deal, settings) -> None:
    for key in (*LIVE_KEYS, "excel_bytes"):
        st.session_state[key] = None
    llm = ui.create_live_client(settings)
    with st.status("Running the deal team (this makes many LLM calls)…", expanded=True) as status:
        orchestrator = DealTeamOrchestrator(llm, on_event=lambda e: status.write(ui.event_label(e)))
        try:
            result = orchestrator.run(deal, active_lineage(deal))
        except OrchestrationError as exc:
            st.session_state.partial_result = exc.partial_result
            st.session_state.last_error = ui.describe_error(exc)
            status.update(label="Deal team run stopped — partial results kept", state="error")
        else:
            st.session_state.deal_team_result = result
            status.update(label=f"Deal team finished: {result.status}", state="complete")
    st.rerun()  # redraw so actions reflect the new state (e.g. enable memo generation)


def generate_memo(deal, settings) -> None:
    result = st.session_state.deal_team_result
    st.session_state.rejected_memo_attempts = None
    with st.spinner("Drafting the IC memo…"):
        try:
            st.session_state.memo_result = InvestmentCommitteeMemoGenerator(
                ui.create_live_client(settings)).generate(result, deal)
            st.session_state.last_error = None
        except Exception as exc:  # shown to the user with details; never swallowed silently
            st.session_state.last_error = ui.describe_error(exc)
            # rejected raw responses, kept for Technical details only (never agent input)
            st.session_state.rejected_memo_attempts = ui.rejected_memo_state(exc)
    st.rerun()


# --- Sidebar -----------------------------------------------------------------------------------------

def sidebar_top():
    sb = st.sidebar
    sb.markdown(uc.sidebar_brand(), unsafe_allow_html=True)
    sb.markdown(uc.sidebar_block("Mode", ""), unsafe_allow_html=True)
    mode = sb.radio("Mode", ui.MODES, key="mode", horizontal=True, label_visibility="collapsed")
    sb.markdown(uc.sidebar_block("Deal source", ""), unsafe_allow_html=True)
    source = sb.radio("Deal source", ui.DEAL_SOURCES, key="deal_source",
                      label_visibility="collapsed")
    sb.caption(ui.DEAL_SOURCE_DESCRIPTIONS[source])
    if source != ui.SAMPLE_DEAL:
        sb.checkbox("Label as hypothetical transaction", key="hypothetical_label",
                    help="Show a disclaimer that this is a hypothetical, unannounced "
                         "transaction and not investment advice.")
    return mode, source


def sidebar_actions(deal, mode, source):
    sb = st.sidebar
    deal_text = (f"{uc._e(deal.acquirer.name)} → {uc._e(deal.target.name)}" if deal
                 else "No deal created yet")
    sb.markdown(uc.sidebar_block("Deal", f'<div class="sb-deal">{deal_text}</div>'),
                unsafe_allow_html=True)
    if deal is not None and hypothetical(source):
        sb.markdown(uc.sidebar_hypothetical(), unsafe_allow_html=True)

    summary = ui.live_settings_summary() if mode == ui.LIVE_ANALYSIS else None
    configured = bool(summary and summary.configured)
    result = st.session_state.deal_team_result
    actions = ui.action_state(mode, configured and deal is not None, result)

    sb.markdown(uc.sidebar_block("Primary action", ""), unsafe_allow_html=True)
    if sb.button("Run Full Deal Team", disabled=not actions.can_run, type="primary",
                 width="stretch"):
        run_live(deal, summary.settings)
    if mode == ui.LIVE_ANALYSIS:
        if sb.button("Generate IC Memo", disabled=not actions.can_generate_memo, width="stretch"):
            generate_memo(deal, summary.settings)

    if mode == ui.OFFLINE_DEMO:
        sb.caption("Switch to Live Analysis to run the deal team with Gemini.")
    elif not configured:
        sb.warning(summary.message)
    elif deal is None:
        sb.caption("Create the deal first.")
    elif actions.memo_block_reason and result is not None:
        sb.caption(actions.memo_block_reason)

    if mode == ui.LIVE_ANALYSIS and configured:
        with sb.expander("Advanced settings"):
            st.markdown(f"Primary model: `{summary.primary_model}`")
            st.markdown("Fallback models: " + (", ".join(f"`{m}`" for m in summary.fallback_models)
                                                or "none"))
    sb.markdown(uc.sidebar_footer(source), unsafe_allow_html=True)
    return summary


# --- Custom deal builder ------------------------------------------------------------------------------

def deal_builder(mode, summary):
    custom = st.session_state.custom_deal
    with st.expander("Deal builder", expanded=custom is None):
        st.caption("Enter a transaction manually: private companies, hypothetical deals, or "
                   "any case where public research is unavailable. " + PERCENT_NOTE
                   + " Cash % and Stock % must total 100%. " + MULTIPLES_NOTE)
        c1, c2 = st.columns([3, 1])
        c1.markdown(uc.callout(ui.PUBLIC_RESEARCH_HINT, "navy"), unsafe_allow_html=True)
        # on_click runs before the rerun, so it may change the (already drawn) source radio.
        c2.button("Go to Public Company Research", width="stretch",
                  on_click=lambda: st.session_state.update(deal_source=ui.RESEARCH_DEAL))
        defaults = form_from_deal(custom or sample_deal())
        with st.form("custom_deal_form"):
            values = {}
            for section, fields in SECTIONS:
                html(f'<div class="card-title" style="margin-top:0.6rem">{uc._e(section)}</div>')
                columns = st.columns(3)
                for i, field in enumerate(fields):
                    with columns[i % 3]:
                        values[field.path] = field_widget(field, defaults[field.path],
                                                          key=f"form_{field.path}")
            submitted = st.form_submit_button("Create Deal", type="primary")
        if submitted:
            try:
                st.session_state.custom_deal = build_deal(values)
                st.session_state.custom_errors = None
            except DealInputError as exc:
                st.session_state.custom_errors = exc.errors
            st.rerun()
    if st.session_state.custom_errors:
        st.error("The deal was not created:\n\n" + "\n".join(
            f"- {e}" for e in st.session_state.custom_errors))
    custom = st.session_state.custom_deal
    if custom is None:
        return
    contexts = DealTeamOrchestrator.build_deterministic_contexts(custom)
    html(uc.card(f"Deal created · {custom.acquirer.name} → {custom.target.name}",
                 uc.kv_rows(ui.deal_preview(custom, contexts))))
    configured = bool(summary and summary.configured)
    actions = ui.action_state(mode, configured, st.session_state.deal_team_result)
    if mode == ui.LIVE_ANALYSIS:
        if st.button("Run Full Deal Team", key="run_from_preview", type="primary",
                     disabled=not actions.can_run):
            run_live(custom, summary.settings)
    else:
        st.caption("Deterministic figures above need no API key. Switch to Live Analysis to "
                   "run the deal team on this deal.")
    st.write("")


# --- Sections -----------------------------------------------------------------------------------------

def header(deal, mode, result):
    badges = [uc.pill("Offline demo" if mode == ui.OFFLINE_DEMO else "Live analysis", "accent")]
    if result is not None:
        badges.append(uc.status_pill(result.status))
    if hypothetical(st.session_state.deal_source):
        badges.append(uc.pill("Hypothetical", "amber"))
    note = "Illustrative qualitative analysis" if mode == ui.OFFLINE_DEMO else None
    kind = {ui.SAMPLE_DEAL: "Fictional transaction", ui.CUSTOM_DEAL: "User-defined transaction",
            ui.RESEARCH_DEAL: "Researched public companies"}[st.session_state.deal_source]
    # The title is always taken from the active Deal (see uc.transaction_title).
    html(uc.deal_header(deal.acquirer.name, deal.target.name,
                        f"{kind} · {deal.currency} {deal.units}", badges, note))


def valuation_bars(deal, contexts) -> str:
    chart = ui.valuation_view(deal, contexts)["chart"]
    short = {"Discounted Cash Flow": "DCF", "Comparable Companies (EV/Revenue)": "Comps (EV/Revenue)",
             "Precedent Transactions (EV/Revenue)": "Precedents (EV/Revenue)"}
    items = sorted(((short.get(k, k), v, ui.fmt_money(v, deal), k == "Offer EV")
                    for k, v in chart.items()), key=lambda item: item[1])
    return uc.bar_comparison(items)


def section_overview(deal, contexts, mode, result, memo):
    header(deal, mode, result)
    recommendation = ui.recommendation_view(deal, result, memo)
    if recommendation is not None:  # no card at all before a complete, approved case
        html(uc.recommendation_compact(recommendation))
    html(uc.kpi_cards((label, value, None) for label, value in ui.overview_metrics(deal, contexts)))
    tx = ui.transaction_view(deal, contexts)
    left, right = st.columns(2, gap="medium")
    with left:
        html(uc.card("Valuation snapshot · enterprise value", valuation_bars(deal, contexts)))
        if contexts["valuation"]["valuation_basis"]["primary_multiple_basis"] == "EV/Revenue":
            st.caption("EV/EBITDA methods are not applicable (target EBITDA non-positive); "
                       "EV/Revenue is used.")
    with right:
        cons, shares = dict(tx["consideration"]), dict(tx["shares"])
        rows = [("Cash %", cons["Cash %"]), ("Stock %", cons["Stock %"]),
                ("Acquirer share basis used", shares["Acquirer share basis used"]),
                ("New shares issued", shares["New Shares Issued"]),
                ("Seller ownership", shares["Seller Ownership"]),
                ("EPS impact · excluding synergies", tx["cases"][0]["result"]),
                ("EPS impact · incl. run-rate synergies", tx["cases"][1]["result"])]
        html(uc.card("Transaction snapshot", uc.kv_rows(rows)))
    st.write("")
    data_quality_callout(tx["data_quality_notes"])
    acquirer, target = ui.company_rows(deal, active_provenance(), contexts)
    left, right = st.columns(2, gap="medium")
    with left:
        html(uc.card(f"Acquirer · {deal.acquirer.name}", uc.kv_rows(acquirer)))
    with right:
        html(uc.card(f"Target · {deal.target.name}", uc.kv_rows(target)))


def tab_valuation(deal, contexts):
    view = ui.valuation_view(deal, contexts)
    if view["basis"]["primary_multiple_basis"] == "EV/Revenue":
        html(uc.callout(ui.NON_POSITIVE_EBITDA_NOTICE, "navy", "Valuation basis"))
    html(uc.card("Valuation overview · enterprise value", valuation_bars(deal, contexts)))
    st.write("")
    html(uc.card("Valuation methods", uc.method_table(view["method_rows"])))
    range_text = (f'Offer vs deterministic range: {uc._e(view["offer_vs_range"])} · low: '
                  f'{uc._e(view["low_method"])} · high: {uc._e(view["high_method"])}'
                  if view["range_available"] else uc._e(view["range_note"]))
    html(f'<div class="pill-note" style="margin:0.5rem 0 1.2rem">{range_text} · no premium or '
         'discount is calculated</div>')
    left, right = st.columns([1, 2], gap="medium")
    with left:
        html(uc.card("DCF assumptions", uc.kv_rows(view["dcf_assumptions"])))
        st.write("")
        html(uc.card("DCF result", uc.kv_rows(view["dcf_totals"])))
    with right:
        html('<div class="card-title">DCF schedule</div>')
        st.dataframe(pd.DataFrame(view["schedule"]), hide_index=True, width="stretch")


def tab_synergies(deal, contexts):
    view = ui.synergy_view(deal, contexts)
    metrics = dict(view["metrics"])
    html(uc.kpi_cards([
        ("Annual cost synergy", metrics["Annual Cost Synergy"], None),
        ("Revenue synergy EBITDA", metrics["Annual Revenue Synergy EBITDA"], None),
        ("PV of synergies", metrics["PV of Synergies"], None),
        ("Integration cost", metrics["Integration Cost"], None),
        ("Net synergy value", metrics["Net Synergy Value"],
         f"Status: {view['labels']['net_synergy_value_status']}"),
    ]))
    html(uc.callout(ui.SYNERGY_WARNING, "amber"))
    left, right = st.columns(2, gap="medium")
    with left:
        html('<div class="card-title">Discounted synergy schedule</div>')
        st.dataframe(pd.DataFrame(view["schedule"]), hide_index=True, width="stretch")
        st.caption(f"Annual after-tax synergies: {metrics['Annual After-Tax Synergies']}")
    with right:
        html(uc.card("Modelling conventions", uc.bullet_list(view["conventions"])))


def tab_transaction(deal, contexts):
    view = ui.transaction_view(deal, contexts)
    left, right = st.columns(2, gap="medium")
    with left:
        html(uc.card("Transaction structure",
                     uc.kv_rows(view["consideration"] + view["shares"])))
        st.caption(f"Acquirer share basis: {view['share_basis_definition']}. Seller ownership = "
                   "new shares ÷ (share basis + new shares).")
        data_quality_callout(view["data_quality_notes"])
        html(uc.callout(ui.FINANCING_NOTE, "navy", "Financing modelling convention"))
        html(uc.card("Financing under the model convention", uc.kv_rows(view["financing"])))
    with right:
        html(uc.callout(ui.SYNERGY_CASE_CAUTION, "amber"))
        for case in view["cases"]:
            html(uc.card(f"{case['title']} · {case['result']}", uc.kv_rows(case["rows"])))
            st.write("")


def section_financial(deal, contexts):
    tabs = st.tabs(list(FINANCIAL_TABS))
    with tabs[0]:
        tab_valuation(deal, contexts)
    with tabs[1]:
        tab_synergies(deal, contexts)
    with tabs[2]:
        tab_transaction(deal, contexts)


def section_deal_team(result, is_demo):
    html(uc.flow([list(level) for level in DEAL_TEAM_FLOW]))
    if is_demo:
        html(uc.callout(DEMO_LABEL, "navy"))
    if result is None:
        st.caption("Run the full deal team in Live Analysis mode to see reviewed outputs.")
        return
    for card in ui.agent_cards(result):
        if card.output is None:
            st.expander(f"{card.step} · {card.name} — not run").write(
                "No output: the run stopped before this agent.")
            continue
        with st.expander(f"{card.step} · {card.name} — Version {card.version} · "
                         f"{ui.kind_label(card.kind)}"):
            st.caption(card.role)
            st.markdown(card.output)


def section_review(result):
    if result is None:
        st.caption("No IC review yet.")
        return
    status = result.status or "incomplete"
    html(uc.kpi_cards([("Status", status.replace("_", " ").title(), None),
                       ("Review rounds", str(result.review_rounds_used), None)]))
    left, right = st.columns([3, 2], gap="medium")
    with left:
        html('<div class="card-title">Review timeline (audit trail)</div>')
        html(uc.timeline(ui.review_timeline(result)))
    with right:
        final = result.review_history[-1].review if result.review_history else None
        gaps = list(final.unresolved_information_gaps) if final else []
        html(uc.card("Unresolved information gaps",
                     uc.bullet_list(gaps or ["None recorded in the final IC review."])))
        if final and final.cross_workstream_issues:
            st.write("")
            html(uc.card("Cross-workstream issues", uc.bullet_list(final.cross_workstream_issues)))
    retries = ui.validation_retries(result)
    if retries:
        with st.expander(f"Numeric validation retries ({len(retries)})"):
            for r in retries:
                st.markdown(f"**{r['agent']}** — attempt {r['attempt']} · {r['outcome']} · "
                            f"unsupported: {r['tokens']}")
                if r["rejected_output"]:
                    st.code(r["rejected_output"], language=None)


def tab_memo(deal, result, memo, mode):
    recommendation = ui.recommendation_view(deal, result, memo)
    if recommendation is not None:
        html(uc.recommendation_card(recommendation,
                                    DEMO_LABEL if mode == ui.OFFLINE_DEMO else None))
    if memo is None:
        state = ui.action_state(mode, True, result)
        html(uc.callout(state.memo_block_reason or
                        "The memo has not been generated. Use Generate IC Memo in the sidebar.",
                        "navy"))
        return
    html(uc.conclusion(memo.conclusion, f"Generated from IC review round "
                       f"{memo.generated_from_review_round} · review rounds used: "
                       f"{memo.review_rounds_used}"))
    if mode == ui.OFFLINE_DEMO:
        html(uc.callout(DEMO_LABEL, "navy"))
    st.download_button("Download Memo (.md)", ui.memo_download_bytes(memo),
                       file_name="IC_Memo.md", mime="text/markdown")
    if memo.rejected_attempts:
        st.caption(f"Accepted on attempt {len(memo.rejected_attempts) + 1} after "
                   f"{len(memo.rejected_attempts)} schema-rejected response(s); rejected "
                   "responses were not used.")
    with st.container(border=True):
        st.markdown(memo.memo_text)
    if memo.rejected_attempts:
        with st.expander("Rejected memo attempts (not accepted)"):
            for a in memo.rejected_attempts:
                st.markdown(f"**Attempt {a.attempt}** · {ui.memo_failure_label(a.category)} "
                            f"in `{a.field}` — {a.message}")
                st.code(a.raw_response, language=None)
    with st.expander("Source agent versions"):
        st.dataframe(pd.DataFrame([vars(r) for r in memo.source_agent_versions]),
                     hide_index=True, width="stretch")


def tab_excel(deal, result, memo, mode):
    html(uc.card("Workbook sheets", uc.sheet_grid(ui.SHEETS)))
    html('<div class="pill-note" style="margin:0.6rem 0">Formula-driven model; every formula is '
         'checked against the deterministic Python engine before the file is written.</div>')
    if mode == ui.OFFLINE_DEMO:
        html(uc.callout(ui.EXCEL_DEMO_NOTE, "navy"))
    state = ui.action_state(mode, True, result)
    if not state.can_export:
        html(uc.callout(state.export_block_reason, "amber"))
        return
    if st.button("Generate Excel Model", type="primary"):
        try:
            source_kind = st.session_state.deal_source
            provenance = st.session_state.research_provenance
            input_sources = (provenance.excel_sources()
                             if source_kind == ui.RESEARCH_DEAL and provenance else None)
            source = (ui.SAMPLE_SOURCE_LABEL if source_kind == ui.SAMPLE_DEAL
                      else ui.CUSTOM_SOURCE_LABEL)
            st.session_state.excel_bytes = ui.workbook_bytes(deal, result, memo, source,
                                                             input_sources)
        except Exception as exc:
            show_error(ui.describe_error(exc))
    if st.session_state.excel_bytes:
        st.download_button(
            "Download Excel Model (.xlsx)", st.session_state.excel_bytes,
            file_name=ui.workbook_filename(deal),
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


def tab_architecture():
    st.markdown(f"### {uc.PRODUCT_NAME} Architecture")
    html(uc.flow([[step] for step in ui.ARCHITECTURE_FLOW], accent_last=True))
    html(uc.responsibilities(
        ["Interpretation", "Diligence", "Risk synthesis", "IC review", "Memo drafting"],
        ["Valuation", "DCF", "Synergy math", "Transaction mechanics", "Accretion",
         "Dependency routing", "Validation", "Excel checks"]))
    st.write("")
    html(uc.card("Why this architecture", uc._e(
        "Many agent demos let an LLM calculate financial outputs directly. This project does "
        "not: core arithmetic is deterministic, tested Python, and agents cannot authorize new "
        "numbers from their own or other agents' previous outputs.")))
    st.write("")
    html(uc.card("Controls designed to reduce unsupported outputs",
                 uc.bullet_list(name for name, _ in ui.CONTROLS)))
    with st.expander("What each control does"):
        for name, what in ui.CONTROLS:
            st.markdown(f"**{name}.** {what}")


def section_deliverables(deal, export_result, memo, result, mode):
    tabs = st.tabs(list(DELIVERABLE_TABS))
    with tabs[0]:
        tab_memo(deal, result, memo, mode)
    with tabs[1]:
        tab_excel(deal, export_result, memo, mode)
    with tabs[2]:
        tab_architecture()


# --- Main ---------------------------------------------------------------------------------------------

def main() -> None:
    init_state()
    html(uc.brand_header())
    mode, source = sidebar_top()
    deal = {ui.SAMPLE_DEAL: sample_deal(), ui.CUSTOM_DEAL: st.session_state.custom_deal,
            ui.RESEARCH_DEAL: st.session_state.researched_deal}[source]
    ui.reset_if_deal_changed(st.session_state, deal)  # never show another deal's analysis
    summary = sidebar_actions(deal, mode, source)
    if hypothetical(source):
        html(uc.hypothetical_notice())

    if deal is None:
        html(uc.empty_state())  # no transaction title until a deal exists
    if source == ui.CUSTOM_DEAL:
        deal_builder(mode, summary)
    elif source == ui.RESEARCH_DEAL:
        research_flow(mode, summary, run_live)
    if deal is None:
        return
    if mode == ui.OFFLINE_DEMO and source == ui.SAMPLE_DEAL:
        demo = demo_state()
        deal, result, memo, is_demo = demo.deal, demo.result, demo.memo, True
    elif mode == ui.OFFLINE_DEMO:
        result, memo, is_demo = None, None, False  # deterministic finance only
    else:
        result = st.session_state.deal_team_result or st.session_state.partial_result
        memo, is_demo = st.session_state.memo_result, False
        if st.session_state.last_error:
            show_error(st.session_state.last_error)
        if st.session_state.partial_result and not st.session_state.deal_team_result:
            html(uc.callout("Showing partial results from a run that stopped early.", "amber"))
    contexts = (result.deterministic_contexts if result
                else DealTeamOrchestrator.build_deterministic_contexts(deal, active_lineage(deal)))
    export_result = st.session_state.deal_team_result if mode == ui.LIVE_ANALYSIS else result

    sections = st.tabs(list(PRIMARY_SECTIONS))
    with sections[0]:
        section_overview(deal, contexts, mode, result, memo)
    with sections[1]:
        section_financial(deal, contexts)
    with sections[2]:
        section_deal_team(result, is_demo)
    with sections[3]:
        section_review(result)
    with sections[4]:
        section_deliverables(deal, export_result, memo, result, mode)


main()
