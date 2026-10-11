"""Visual components for the Streamlit app: CSS and small HTML builders.

Pure functions that return HTML strings (no Streamlit imports), so they are easy to
test. All dynamic text is HTML-escaped. These functions format and lay out values
they are given; they never compute financial figures.
"""

from __future__ import annotations

from html import escape
from typing import Iterable, Sequence

NAVY = "#16233A"
ACCENT = "#3157D5"

CSS = """
<style>
:root {
  --ink: #111827; --muted: #667085; --navy: #16233A; --accent: #3157D5;
  --border: #E5E7EB; --card: #FFFFFF; --page: #F7F8FA;
  --amber-bg: #FFF8EB; --amber-border: #F5DDA9; --amber-ink: #7A4B00;
  --green-bg: #ECF7F0; --green-ink: #1E6B3C; --blue-bg: #EEF2FD;
}
html, body, [data-testid="stAppViewContainer"], [data-testid="stSidebar"] {
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Inter, Roboto,
               "Helvetica Neue", Arial, sans-serif;
}
[data-testid="stAppViewContainer"] { background: var(--page); }
[data-testid="stHeader"] { background: transparent; }
[data-testid="stDecoration"] { display: none; }
.block-container { max-width: 1280px; padding-top: 2.2rem; padding-bottom: 4rem; }
h1, h2, h3, h4 { color: var(--navy); letter-spacing: -0.01em; }

/* Sidebar */
[data-testid="stSidebar"] { background: #FFFFFF; border-right: 1px solid var(--border); }
[data-testid="stSidebar"] .block-container { padding-top: 1.5rem; }
.sb-brand { display: flex; align-items: center; gap: 0.7rem; padding: 0.2rem 0 1rem;
            margin-bottom: 0.4rem; border-bottom: 1px solid var(--border); }
.sb-title { font-size: 1.45rem; font-weight: 800; color: var(--navy); letter-spacing: -0.025em;
            line-height: 1.1; }
.sb-title .ai, .brand-name .ai { color: var(--accent); }
.sb-subtitle { font-size: 0.74rem; font-weight: 600; color: var(--muted); margin-top: 0.2rem;
               letter-spacing: 0.01em; }
.mark { flex: none; display: inline-flex; align-items: flex-end; gap: 3px; width: 40px; height: 40px;
        padding: 9px 9px; box-sizing: border-box; border-radius: 11px;
        background: linear-gradient(135deg, #3157D5 0%, #16233A 100%);
        box-shadow: 0 4px 12px rgba(49, 87, 213, 0.28); }
.mark i { display: block; flex: 1; border-radius: 2px; background: #FFFFFF; }
.mark i:nth-child(1) { height: 38%; opacity: 0.55; }
.mark i:nth-child(2) { height: 66%; opacity: 0.8; }
.mark i:nth-child(3) { height: 100%; }
.mark.lg { width: 58px; height: 58px; padding: 13px; gap: 4px; border-radius: 15px;
           background: rgba(255, 255, 255, 0.12); box-shadow: inset 0 0 0 1px rgba(255, 255, 255, 0.22); }
.brand { position: relative; overflow: hidden; display: flex; align-items: center; gap: 1.2rem;
         flex-wrap: wrap; padding: 1.7rem 2rem; margin-bottom: 1.6rem; border-radius: 16px;
         background: radial-gradient(circle at 88% 12%, rgba(91, 127, 240, 0.45) 0%, transparent 45%),
                     linear-gradient(120deg, #101A2E 0%, #16233A 45%, #22387A 100%);
         box-shadow: 0 10px 30px rgba(22, 35, 58, 0.18); }
.brand-text { display: flex; flex-direction: column; gap: 0.25rem; min-width: 0; }
.brand-name { font-size: 2.35rem; font-weight: 800; letter-spacing: -0.035em; line-height: 1.05;
              color: #FFFFFF; }
.brand .brand-name .ai { color: #9DB4FF; }
.brand-sub { font-size: 1.02rem; font-weight: 500; color: #C9D3EA; }
.brand-line { display: flex; flex-wrap: wrap; gap: 0.45rem; margin-left: auto; }
.brand-line span { font-size: 0.74rem; font-weight: 600; color: #E3E9F7; padding: 0.3rem 0.7rem;
                   border-radius: 999px; background: rgba(255, 255, 255, 0.09);
                   border: 1px solid rgba(255, 255, 255, 0.16); white-space: nowrap; }
@media (max-width: 900px) { .brand-line { margin-left: 0; } .brand-name { font-size: 1.9rem; } }
.empty-state { color: var(--muted); font-size: 0.95rem; padding: 1.4rem 0; }
.sb-label { font-size: 0.68rem; font-weight: 700; letter-spacing: 0.08em; color: var(--muted);
            text-transform: uppercase; margin: 1.1rem 0 0.35rem; }
.sb-deal { font-size: 0.9rem; color: var(--ink); font-weight: 600; }
.sb-hypo { margin-top: 0.7rem; padding: 0.55rem 0.7rem; border-radius: 8px; font-size: 0.74rem;
           line-height: 1.4; background: var(--amber-bg); border: 1px solid var(--amber-border);
           color: var(--amber-ink); }
.sb-footer { margin-top: 2rem; padding-top: 0.9rem; border-top: 1px solid var(--border);
             font-size: 0.72rem; color: var(--muted); line-height: 1.6; }
[data-testid="stSidebar"] [role="radiogroup"] { gap: 0.25rem; }
[data-testid="stSidebar"] [role="radiogroup"] label {
  border: 1px solid var(--border); border-radius: 6px; padding: 0.3rem 0.7rem; background: #FFF; }

/* Tabs */
[data-baseweb="tab-list"] { gap: 1.6rem; border-bottom: 1px solid var(--border); }
[data-baseweb="tab"] { padding: 0.6rem 0; font-weight: 600; color: var(--muted); }
[data-baseweb="tab"][aria-selected="true"] { color: var(--navy); }
[data-baseweb="tab-highlight"] { background-color: var(--accent); }

/* Buttons, expanders, tables */
.stButton > button, .stDownloadButton > button { border-radius: 6px; font-weight: 600; }
[data-testid="stExpander"] { border: 1px solid var(--border); border-radius: 10px;
                             background: var(--card); box-shadow: none; }
[data-testid="stDataFrame"] { border: 1px solid var(--border); border-radius: 8px; }

/* Components */
.card { background: var(--card); border: 1px solid var(--border); border-radius: 10px;
        padding: 1.1rem 1.25rem; height: 100%; }
.card-title { font-size: 0.72rem; font-weight: 700; letter-spacing: 0.08em; color: var(--muted);
              text-transform: uppercase; margin-bottom: 0.8rem; }
.eyebrow { font-size: 0.72rem; font-weight: 700; letter-spacing: 0.12em; color: var(--accent);
           text-transform: uppercase; }
.deal-header { display: flex; justify-content: space-between; align-items: flex-start;
               gap: 1rem; flex-wrap: wrap; margin-bottom: 1.4rem; }
.deal-title { font-size: 1.9rem; font-weight: 700; color: var(--navy); line-height: 1.2;
              margin: 0.3rem 0; }
.deal-title .arrow { color: var(--muted); font-weight: 400; margin: 0 0.4rem; }
.deal-sub { color: var(--muted); font-size: 0.9rem; }
.badges { display: flex; flex-direction: column; align-items: flex-end; gap: 0.4rem; }
.pill { display: inline-block; font-size: 0.7rem; font-weight: 700; letter-spacing: 0.06em;
        text-transform: uppercase; padding: 0.28rem 0.6rem; border-radius: 999px;
        border: 1px solid var(--border); background: #FFF; color: var(--navy); }
.pill.accent { background: var(--blue-bg); border-color: #D3DDF8; color: var(--accent); }
.pill.green { background: var(--green-bg); border-color: #CBE8D6; color: var(--green-ink); }
.pill.amber { background: var(--amber-bg); border-color: var(--amber-border); color: var(--amber-ink); }
.pill.market { background: #F1F3F6; border-color: #D9DDE3; color: #344054; }
.pill.missing { background: #FDF0EF; border-color: #F4CFCB; color: #9B2C24; }
.fact-table { width: 100%; border-collapse: collapse; font-size: 0.84rem; }
.fact-table th { text-align: left; font-size: 0.66rem; letter-spacing: 0.08em; text-transform: uppercase;
                 color: var(--muted); border-bottom: 1px solid var(--border); padding: 0.4rem 0.5rem; }
.fact-table td { padding: 0.45rem 0.5rem; border-bottom: 1px solid #F1F3F6; vertical-align: top; }
.fact-table td.v { font-weight: 600; color: var(--navy); white-space: nowrap; font-variant-numeric: tabular-nums; }
.fact-table td.c { color: var(--muted); font-size: 0.76rem; word-break: break-word; }
.fact-table .lin { display: block; margin-top: 0.3rem; font-weight: 400; white-space: normal;
                   font-size: 0.72rem; color: var(--muted); line-height: 1.35; }
.fact-table .lin-tag { display: block; font-size: 0.62rem; font-weight: 700; letter-spacing: 0.08em;
                       text-transform: uppercase; color: var(--muted); margin-bottom: 0.1rem; }
.pill-note { font-size: 0.72rem; color: var(--muted); }

.kpi-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(170px, 1fr));
            gap: 0.9rem; margin: 0.4rem 0 1.4rem; }
.kpi { background: var(--card); border: 1px solid var(--border); border-radius: 10px;
       padding: 1rem 1.1rem; }
.kpi-label { font-size: 0.68rem; font-weight: 700; letter-spacing: 0.08em; color: var(--muted);
             text-transform: uppercase; }
.kpi-value { font-size: 1.55rem; font-weight: 700; color: var(--navy); margin-top: 0.35rem; }
.kpi-context { font-size: 0.74rem; color: var(--muted); margin-top: 0.2rem; }

.bars { display: flex; flex-direction: column; gap: 0.7rem; }
.bar-row { display: grid; grid-template-columns: 11rem 1fr 6rem; align-items: center; gap: 0.8rem; }
.bar-label { font-size: 0.85rem; color: var(--ink); }
.bar-track { background: #F1F3F6; border-radius: 4px; height: 0.9rem; }
.bar-fill { background: var(--navy); height: 100%; border-radius: 4px; }
.bar-fill.highlight { background: var(--accent); }
.bar-value { font-size: 0.85rem; font-weight: 600; color: var(--navy); text-align: right; }

.kv { display: flex; justify-content: space-between; padding: 0.45rem 0;
      border-bottom: 1px solid #F1F3F6; font-size: 0.9rem; }
.kv:last-child { border-bottom: none; }
.kv .k { color: var(--muted); }
.kv .v { color: var(--ink); font-weight: 600; font-variant-numeric: tabular-nums; }

.callout { border-radius: 8px; padding: 0.8rem 1rem; font-size: 0.88rem; margin: 0.6rem 0 1rem;
           border: 1px solid var(--border); background: var(--card); color: var(--ink); }
.callout.amber { background: var(--amber-bg); border-color: var(--amber-border); color: var(--amber-ink); }
.callout.navy { background: var(--blue-bg); border-color: #D3DDF8; color: var(--navy); }
.callout-title { font-weight: 700; margin-bottom: 0.2rem; }
.conclusion { border-left: 4px solid var(--accent); background: var(--card); border-radius: 8px;
              border-top: 1px solid var(--border); border-right: 1px solid var(--border);
              border-bottom: 1px solid var(--border); padding: 1rem 1.25rem; margin-bottom: 1rem; }
.conclusion .value { font-size: 1.3rem; font-weight: 700; color: var(--navy); margin-top: 0.25rem; }
.conclusion .meta { font-size: 0.78rem; color: var(--muted); margin-top: 0.3rem; }
.rec { background: var(--card); border: 1px solid var(--border); border-top: 3px solid var(--navy);
       border-radius: 10px; padding: 1.2rem 1.35rem; margin-bottom: 1.2rem; }
.rec-value { font-size: 1.45rem; font-weight: 700; color: var(--navy); margin: 0.3rem 0 0.2rem; }
.rec-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(230px, 1fr));
            gap: 1.2rem; margin-top: 1rem; }
.rec-compact { display: flex; justify-content: space-between; align-items: center; gap: 1rem;
               flex-wrap: wrap; background: var(--card); border: 1px solid var(--border);
               border-left: 4px solid var(--navy); border-radius: 8px; padding: 0.8rem 1.1rem;
               margin-bottom: 1.2rem; }

.flow { display: flex; flex-direction: column; align-items: center; gap: 0.3rem; margin: 0.5rem 0 1.4rem; }
.flow-level { display: flex; gap: 0.6rem; flex-wrap: wrap; justify-content: center; }
.flow-node { background: var(--card); border: 1px solid var(--border); border-radius: 8px;
             padding: 0.45rem 0.9rem; font-size: 0.84rem; font-weight: 600; color: var(--navy); }
.flow-node.accent { border-color: #D3DDF8; background: var(--blue-bg); }
.flow-arrow { color: var(--muted); font-size: 0.9rem; line-height: 1; }

.timeline { border-left: 2px solid var(--border); margin: 0.6rem 0 1.2rem 0.4rem; padding-left: 1.2rem; }
.tl-round { position: relative; margin-bottom: 1.2rem; }
.tl-round::before { content: ""; position: absolute; left: -1.62rem; top: 0.25rem; width: 0.7rem;
                    height: 0.7rem; border-radius: 50%; background: var(--navy); }
.tl-head { display: flex; gap: 0.6rem; align-items: center; }
.tl-round-label { font-size: 0.72rem; font-weight: 700; letter-spacing: 0.08em; color: var(--muted); }
.tl-summary { font-size: 0.88rem; color: var(--ink); margin: 0.35rem 0; }
.tl-step { display: flex; gap: 0.6rem; align-items: baseline; font-size: 0.86rem; margin: 0.25rem 0; }
.tl-step .who { font-weight: 600; color: var(--navy); }
.tl-step .how { color: var(--muted); font-size: 0.78rem; }
.tl-request { font-size: 0.82rem; color: var(--muted); margin: 0.1rem 0 0.4rem 0.2rem; }

.sheet-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr)); gap: 0.6rem; }
.sheet { background: var(--card); border: 1px solid var(--border); border-radius: 8px;
         padding: 0.6rem 0.8rem; font-size: 0.84rem; color: var(--navy); font-weight: 600; }
.sheet .n { color: var(--muted); font-weight: 500; margin-right: 0.4rem; }
.resp-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 1rem; }
.resp-item { font-size: 0.88rem; padding: 0.3rem 0; color: var(--ink); }
.bullets { margin: 0; padding-left: 1.1rem; }
.bullets li { font-size: 0.88rem; color: var(--ink); margin: 0.3rem 0; }
@media (max-width: 900px) {
  .resp-grid { grid-template-columns: 1fr; }
  .bar-row { grid-template-columns: 8rem 1fr 5rem; }
}
</style>
"""


def _e(value: object) -> str:
    return escape(str(value))


def pill(text: str, tone: str = "") -> str:
    return f'<span class="pill {tone}">{_e(text)}</span>'


def deal_header(acquirer: str, target: str, subtitle: str, badges: Sequence[str],
                note: str | None = None) -> str:
    note_html = f'<div class="pill-note">{_e(note)}</div>' if note else ""
    return (
        '<div class="deal-header"><div>'
        '<div class="eyebrow">Transaction</div>'
        f'<div class="deal-title">{_e(acquirer)}<span class="arrow">→</span>{_e(target)}</div>'
        f'<div class="deal-sub">{_e(subtitle)}</div></div>'
        f'<div class="badges">{"".join(badges)}{note_html}</div></div>'
    )


def kpi_cards(items: Iterable[tuple[str, str, str | None]]) -> str:
    cards = "".join(
        f'<div class="kpi"><div class="kpi-label">{_e(label)}</div>'
        f'<div class="kpi-value">{_e(value)}</div>'
        + (f'<div class="kpi-context">{_e(context)}</div>' if context else "")
        + "</div>"
        for label, value, context in items)
    return f'<div class="kpi-grid">{cards}</div>'


def bar_comparison(items: Sequence[tuple[str, float, str, bool]]) -> str:
    """items: (label, value, formatted value, highlight). Bars are scaled to the
    largest value for display only."""
    top = max((value for _, value, _, _ in items), default=0) or 1
    rows = "".join(
        f'<div class="bar-row"><div class="bar-label">{_e(label)}</div>'
        f'<div class="bar-track"><div class="bar-fill{" highlight" if highlight else ""}" '
        f'style="width:{max(value, 0) / top * 100:.1f}%"></div></div>'
        f'<div class="bar-value">{_e(shown)}</div></div>'
        for label, value, shown, highlight in items)
    return f'<div class="bars">{rows}</div>'


def kv_rows(rows: Iterable[tuple[str, str]]) -> str:
    return "".join(f'<div class="kv"><span class="k">{_e(k)}</span>'
                   f'<span class="v">{_e(v)}</span></div>' for k, v in rows)


def card(title: str, body_html: str) -> str:
    return f'<div class="card"><div class="card-title">{_e(title)}</div>{body_html}</div>'


def callout(text: str, tone: str = "amber", title: str | None = None) -> str:
    head = f'<div class="callout-title">{_e(title)}</div>' if title else ""
    return f'<div class="callout {tone}">{head}{_e(text)}</div>'


def conclusion(value: str, meta: str) -> str:
    return (f'<div class="conclusion"><div class="eyebrow">Investment Committee conclusion</div>'
            f'<div class="value">{_e(value)}</div><div class="meta">{_e(meta)}</div></div>')


def flow(levels: Sequence[Sequence[str]], accent_last: bool = False) -> str:
    parts = []
    for i, level in enumerate(levels):
        tone = " accent" if accent_last and i == len(levels) - 1 else ""
        nodes = "".join(f'<div class="flow-node{tone}">{_e(n)}</div>' for n in level)
        parts.append(f'<div class="flow-level">{nodes}</div>')
    return '<div class="flow">' + '<div class="flow-arrow">↓</div>'.join(parts) + "</div>"


_STATUS_TONE = {"approved": "green", "revision_required": "amber",
                "review_limit_reached": "amber"}


def status_pill(status: str | None) -> str:
    if status is None:
        return pill("Not run")
    return pill(status.replace("_", " "), _STATUS_TONE.get(status, ""))


def timeline(rounds: Sequence[dict]) -> str:
    items = []
    for r in rounds:
        steps = "".join(
            f'<div class="tl-step"><span class="how">↓</span><span class="who">{_e(agent)}</span>'
            f'<span class="how">{_e(kind)}</span></div>'
            for agent, kind in r["reruns"])
        requests = "".join(
            f'<div class="tl-request">Requested for {_e(agent)}: {_e(change)}</div>'
            for agent, change in r["requests"])
        items.append(
            f'<div class="tl-round"><div class="tl-head">'
            f'<span class="tl-round-label">ROUND {r["round"]}</span>{status_pill(r["status"])}</div>'
            f'<div class="tl-summary">{_e(r["summary"])}</div>{requests}{steps}</div>')
    return f'<div class="timeline">{"".join(items)}</div>'


def bullet_list(items: Iterable[str]) -> str:
    return '<ul class="bullets">' + "".join(f"<li>{_e(i)}</li>" for i in items) + "</ul>"


def sheet_grid(names: Sequence[str]) -> str:
    cells = "".join(f'<div class="sheet"><span class="n">{i}</span>{_e(n)}</div>'
                    for i, n in enumerate(names, start=1))
    return f'<div class="sheet-grid">{cells}</div>'


def responsibilities(llm: Sequence[str], python: Sequence[str]) -> str:
    def column(title: str, items: Sequence[str]) -> str:
        return card(title, "".join(f'<div class="resp-item">{_e(i)}</div>' for i in items))
    return f'<div class="resp-grid">{column("LLM agents", llm)}{column("Python", python)}</div>'


def sidebar_block(label: str, value_html: str) -> str:
    return f'<div class="sb-label">{_e(label)}</div>{value_html}'


SIDEBAR_FOOTER = ('<div class="sb-footer">Fictional sample transaction<br>'
                  'Deterministic finance · AI qualitative analysis</div>')
CUSTOM_SIDEBAR_FOOTER = ('<div class="sb-footer">User-defined transaction<br>'
                         'Deterministic finance · AI qualitative analysis</div>')


RESEARCH_SIDEBAR_FOOTER = ('<div class="sb-footer">Researched public-company inputs<br>'
                           'Deterministic finance · AI qualitative analysis</div>')


def sidebar_footer(source: str | bool = False) -> str:
    """Footer text for the active deal source ("Create New Deal", "Research Public
    Companies", or anything else for the sample). A bool is accepted for custom."""
    if source is True or source == "Create New Deal":
        return CUSTOM_SIDEBAR_FOOTER
    if source == "Research Public Companies":
        return RESEARCH_SIDEBAR_FOOTER
    return SIDEBAR_FOOTER


def fact_table(rows: Sequence[tuple]) -> str:
    """rows: (field, value, status code, status text, period, source + concept[, tag,
    lineage]). The optional tag labels the value (e.g. "Effective value"); the optional
    lineage line is shown muted beneath it (e.g. the original researched observation)."""
    head = ("<tr><th>Field</th><th>Value</th><th>Status</th><th>Period</th>"
            "<th>Source · concept / derivation</th></tr>")

    def row_html(row) -> str:
        field, value, tone, text, period, source, *extra = row
        tag, lineage = (list(extra) + [None, None])[:2]
        tag_html = f'<span class="lin-tag">{_e(tag)}</span>' if tag else ""
        lin_html = f'<span class="lin">{_e(lineage)}</span>' if lineage else ""
        return (f'<tr><td>{_e(field)}</td><td class="v">{tag_html}{_e(value)}{lin_html}</td>'
                f'<td>{pill(text, tone)}</td><td>{_e(period)}</td><td class="c">{_e(source)}</td></tr>')

    return f'<table class="fact-table">{head}{"".join(row_html(r) for r in rows)}</table>'


def recommendation_card(view, note: str | None = None) -> str:
    """Full Deal Recommendation card. Every line comes from the memo, the final IC
    review, or deterministic calculations; nothing is generated here."""
    def block(title: str, items) -> str:
        items = list(items) or ["None stated in the memo."]
        return f'<div><div class="card-title">{_e(title)}</div>{bullet_list(items)}</div>'

    risks = list(view.risks) + [f"Unresolved: {g}" for g in view.gaps]
    note_html = f'<div class="pill-note" style="margin-top:0.2rem">{_e(note)}</div>' if note else ""
    return (
        '<div class="rec"><div class="eyebrow">Deal recommendation</div>'
        f'<div class="rec-value">{_e(view.conclusion)}</div>'
        f'<div class="pill-note">Investment Committee memo conclusion · from approved IC '
        f'review round {view.review_round} · the modelled IC process, not financial advice</div>'
        f'{note_html}<div class="rec-grid">'
        f'{block("Why", view.why)}{block("Key conditions", view.conditions)}'
        f'{block("Key risks / gaps", risks)}'
        f'<div><div class="card-title">Financial context</div>{kv_rows(view.financial)}</div>'
        "</div></div>"
    )


def recommendation_compact(view) -> str:
    return ('<div class="rec-compact"><div><div class="eyebrow">Deal recommendation</div>'
            f'<div class="rec-value" style="font-size:1.15rem">{_e(view.conclusion)}</div></div>'
            f'<div class="pill-note">IC review approved (round {view.review_round}) · details in '
            "Deliverables → IC Memo</div></div>")


def method_table(rows) -> str:
    """rows: (method, value, status). Not-applicable methods are shown, never hidden."""
    body = "".join(
        f'<tr><td>{_e(m)}</td><td class="v">{_e(v)}</td><td class="c">{_e(status)}</td></tr>'
        for m, v, status in rows)
    return ('<table class="fact-table"><tr><th>Method</th><th>Enterprise value</th>'
            f'<th>Status</th></tr>{body}</table>')


PRODUCT_NAME = "DealSignal AI"
PRODUCT_CATEGORY = "M&A Decision Intelligence Platform"
PRODUCT_LINE = "Sourced research · Deterministic finance · AI deal-team analysis"
NO_DEAL_MESSAGE = "Build or research a transaction to begin."
HYPOTHETICAL_NOTICE = ("Hypothetical transaction for demonstration purposes. "
                       "Not an announced transaction and not investment advice.")


_MARK = "<i></i><i></i><i></i>"


def _wordmark() -> str:
    """PRODUCT_NAME with its trailing "AI" highlighted."""
    head, _, tail = PRODUCT_NAME.rpartition(" ")
    return f'{_e(head)} <span class="ai">{_e(tail)}</span>' if head else _e(PRODUCT_NAME)


def brand_header() -> str:
    """Product hero for the main content area (static; never deal-specific)."""
    chips = "".join(f"<span>{_e(part.strip())}</span>" for part in PRODUCT_LINE.split("·"))
    return (f'<div class="brand"><span class="mark lg">{_MARK}</span>'
            f'<div class="brand-text"><div class="brand-name">{_wordmark()}</div>'
            f'<div class="brand-sub">{_e(PRODUCT_CATEGORY)}</div></div>'
            f'<div class="brand-line" title="{_e(PRODUCT_LINE)}">{chips}</div></div>')


def sidebar_brand() -> str:
    """Product lockup at the top of the sidebar (static; never deal-specific)."""
    return (f'<div class="sb-brand"><span class="mark">{_MARK}</span><div>'
            f'<div class="sb-title">{_wordmark()}</div>'
            f'<div class="sb-subtitle">{_e(PRODUCT_CATEGORY)}</div></div></div>')


def transaction_title(deal) -> str:
    """Always derived from the active Deal; there is no name table or special case."""
    return f"{deal.acquirer.name} → {deal.target.name}"


def hypothetical_notice() -> str:
    """Opt-in disclaimer for a user-built deal (static text; never deal-specific)."""
    return callout(HYPOTHETICAL_NOTICE, "amber", "Hypothetical transaction")


def sidebar_hypothetical() -> str:
    """Compact form of the opt-in disclaimer, kept in view on every page."""
    return f'<div class="sb-hypo">{_e(HYPOTHETICAL_NOTICE)}</div>'


def empty_state() -> str:
    return f'<div class="empty-state">{_e(NO_DEAL_MESSAGE)}</div>'
