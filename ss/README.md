# DealSignal AI — final LinkedIn screenshot package

> **Hypothetical transaction for demonstration purposes. Not an announced transaction and not investment advice.**

All screenshots were captured from the final live DealSignal AI application after the
data-lineage consistency patch and the share-basis terminology fix.

## This run

| Item | Value |
|---|---|
| Captured | 3 October 2026, fresh Streamlit server, fresh headless Chrome profile, Live Analysis |
| Research | Live SEC EDGAR (force refresh) and live Alpha Vantage |
| Acquirer share price | **USD 198.90** (Alpha Vantage, latest trading day 2026-10-02) |
| Stale research observations | ACN debt (2024-08-31), ACN point-in-time shares (2010-03-19), EPAM debt (2015-12-31). Kept as provenance, never used automatically. |
| Approved overrides | ACN debt **5,142.265** (as of 2026-05-31); ACN share basis **612.213453m**, *Weighted-average basic shares (modelling proxy)* (as of 2026-05-31); EPAM debt **25.0** (as of 2026-06-30). Each has its SEC 10-Q source and reason. |
| New shares issued | **10.6552m** |
| Seller ownership | **1.71%** = 10.6552 / (612.2135 + 10.6552) |
| Mixed-vintage warning | None (all effective balance-sheet dates fall within one quarter) |
| Deal team | 9 agents, live Gemini. Deal Structuring's first response was rejected by the numeric validator and accepted on its bounded retry. |
| IC review | **Approved**, round 1, no revision requests, 6 unresolved information gaps |
| IC memo | Accepted on attempt 2, after one schema rejection (JSON wrapped in a code fence) |
| Controlled recommendation | **Decision deferred pending additional information** |

One more step happened during memo generation. The first **Generate IC Memo** click was
rejected by the numeric validator: the memo writer reformatted 0.7 and 0.3 as "70.0%" and
"30.0%". That rejection is not retried automatically, so no memo was produced. The second click
made a new live attempt, and that is the accepted memo shown here.

All images are real browser captures at 1440px wide and 100% zoom, cropped vertically to a
clean edge. Each file is one contiguous region of one screenshot. Some frames used a taller
viewport (up to 1440 × 1500) so that cards are not clipped.

## Index

| # | File | What it shows | Carousel |
|---|------|---------------|----------|
| 01 | `01_dealsignal_overview.png` | **Hero.** Branding; dynamic title *Accenture plc → EPAM Systems, Inc.*; LIVE ANALYSIS, APPROVED and HYPOTHETICAL pills; the deal recommendation (*Decision deferred pending additional information*); offer EV, equity value, cash and stock consideration, target net debt; valuation and transaction snapshots (share basis 612.21m · new shares 10.66m · seller ownership 1.71%); company cards with as-of dates and override markers. | **#1** |
| 02 | `02_public_company_research.png` | Live SEC research for Accenture with **EFFECTIVE VALUE** rows and their statuses (REPORTED, DERIVED, MARKET DATA, USER OVERRIDE). The share row is labelled **Acquirer share basis**, with its definition beneath the value. The original SEC observation keeps its concept label: *Original researched value (Point-in-time shares outstanding): 637.016m · Stale · as of 2010-03-19*. | **#2** |
| 03 | `03_research_review_lineage.png` | Deal preview, provenance counts (DERIVED 3 · MARKET DATA 1 · REPORTED 6 · USER OVERRIDE 3), the share-basis definition used by the model, and the three overrides (**Acquirer debt**, **Acquirer share basis**, **Target debt**), each with effective value, as-of date, definition, SEC source, reason and original observation. | Alternate for #2 |
| 04 | `04_financial_analysis.png` | Deterministic valuation: comparables, precedents, DCF and offer EV; method table; DCF assumptions, schedule and result. | **#3** |
| 05 | `05_transaction_accretion.png` | Cash/stock structure, **acquirer share basis used 612.21m**, new shares 10.66m, pro forma shares 622.87m, seller ownership 1.71%, with the share-basis definition and reconciliation formula; the financing convention; EPS excluding and including synergies. | Optional |
| 06 | `06_ai_deal_team.png` | The multi-agent workflow and each agent's final reviewed version. | **#4** |
| 07 | `07_ic_review.png` | IC Partner: **Approved**, 1 round, the audit trail, 6 unresolved gaps, and the collapsed "Numeric validation retries (2)" audit item. | **#5** |
| 08 | `08_deal_recommendation.png` | The full Deal Recommendation: **Decision deferred pending additional information**, why, key conditions, key risks and gaps, financial context, and the IC conclusion card. | **#6** |
| 09 | `09_ic_memo.png` | The accepted memo from Transaction Structure & Financing through to the **Investment Committee Conclusion**. It quotes "the acquirer share basis of 612.2135 (weighted-average basic shares used as a modelling proxy)". | Optional |
| 09b | `09b_ic_memo_executive_summary.png` | The memo's Executive Summary, Strategic Rationale and Valuation sections. | Optional |
| 10 | `10_architecture.png` | The DealSignal AI architecture flow. | Optional (good for a comment) |
| 11 | `11_excel_model.png` | **Not captured.** Automating it means taking a screenshot of the live desktop; manual steps are below. | — |

## Recommended 6-image carousel

1. `01_dealsignal_overview.png`: product and decision
2. `02_public_company_research.png`: real data with validated inputs and transparent lineage
3. `04_financial_analysis.png`: deterministic finance
4. `06_ai_deal_team.png`: AI analysis
5. `07_ic_review.png`: quality control
6. `08_deal_recommendation.png`: decision

## Deliverables from this run

- `reports/final_live_run/DealSignal_AI_ACN_EPAM_MA_Model.xlsx`: 9 sheets, all 137 formulas
  evaluate; effective inputs in the cells, lineage in the cell comments.
- `reports/final_live_run/DealSignal_AI_ACN_EPAM_IC_Memo.md`: the accepted live memo.

## Manual steps for `11_excel_model.png`

1. Open `reports/final_live_run/DealSignal_AI_ACN_EPAM_MA_Model.xlsx` in Microsoft Excel and
   click **Enable Editing** if prompted.
2. Select **IC Summary** (or **Valuation**). Set zoom to about 110% and click an empty cell.
3. Close other windows and silence notifications.
4. Press **⌘⇧4**, then **Space**, and click the Excel window.
5. Check the frame for paths, account names or other windows, then save it as
   `ss/11_excel_model.png`.

## Privacy check

No image contains an API key, `.env` content, terminal, email address, SEC User-Agent, error
banner, loading state or Technical details panel. Every image shows the hypothetical
disclaimer in the sidebar.
