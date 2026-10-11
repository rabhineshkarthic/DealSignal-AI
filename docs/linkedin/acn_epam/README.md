# DealSignal AI — Accenture → EPAM screenshots

> **Hypothetical transaction for demonstration purposes. Not an announced transaction and not investment advice.**

Every image below was captured by browser automation (headless Google Chrome, 1440 × 1100)
from the real Streamlit app in **Live Analysis** mode on 29 September 2026. The research came from
live SEC EDGAR and Alpha Vantage calls, and the deal team, IC Partner and IC Memo Writer ran on
live Gemini. No Offline Demo, fixtures, scripted responses or mock UI were used. The only change
made for capture was extra blank space below the page content, so short tabs could scroll to the
top of the viewport. No page content was added or edited.

The "Label as hypothetical transaction" option was switched on, so the disclaimer is visible in
the sidebar of every screenshot.

| # | File | What it demonstrates | Use on LinkedIn? |
|---|------|----------------------|------------------|
| 01 | `01_public_company_research.png` | Live SEC EDGAR research for Accenture plc (ACN): identity (CIK, exchange, fiscal year end), researched values with **REPORTED / DERIVED / MARKET DATA** badges, periods, and XBRL concept or derivation for each field. It also shows, transparently, two stale SEC facts that were later overridden in review (debt as of 2024-08-31, share count as of 2010-03-19). | **Yes (carousel #2).** |
| 01b | `01b_research_review_overrides.png` | The review outcome: the deterministic deal preview, provenance counts (**DERIVED 3 · MARKET DATA 1 · REPORTED 6 · USER OVERRIDE 3**), and the three user overrides, each with its original value and the SEC filing concept used to replace it. | Optional. A good follow-up image for a comment about human review of sourced data. |
| 02 | `02_deal_overview.png` | **Hero image.** DealSignal AI branding, dynamic title *Accenture plc → EPAM Systems, Inc.*, LIVE ANALYSIS / APPROVED / HYPOTHETICAL pills, the controlled deal recommendation, offer EV, offer equity value, cash and stock consideration, target net debt, the valuation snapshot, the transaction snapshot, and both companies' researched financials. | **Yes (carousel #1).** |
| 03 | `03_financial_analysis.png` | Valuation: comparable companies, precedent transactions, DCF and offer EV bars; method table with in-range status; DCF assumptions, schedule and result (terminal value share of EV). | **Yes (carousel #3).** |
| 03b | `03b_transaction_accretion.png` | Transaction & Accretion: consideration mix, new shares, pro forma shares, seller ownership, the financing modelling convention, and EPS accretion/dilution excluding and including run-rate synergies. | Optional alternative to 03. |
| 04 | `04_ai_deal_team.png` | The multi-agent workflow (Deal Lead → Strategic Fit → Valuation + Synergy → Financial / Commercial / Legal & Operational DD → Deal Structuring → Risk) and each agent's final reviewed version. | Optional. |
| 05 | `05_ic_review.png` | IC Partner quality control: status **Approved**, 1 review round, the review audit trail, and three unresolved information gaps. No revisions were requested in this run, so no revision cascade is shown. | **Yes (carousel #4).** |
| 06 | `06_deal_recommendation.png` | The deal recommendation card: the controlled conclusion **"Decision deferred pending additional information"**, why, key conditions, key risks / gaps, and financial context. | **Yes (carousel #5).** |
| 07 | `07_ic_memo.png` | The Investment Committee memo: controlled conclusion, a note that the memo was accepted on attempt 2 after one schema-rejected response, the Transaction Overview and the Executive Summary. The model-written overview says "DEAL DATA reports that Accenture plc is considering the acquisition…", which is the model's phrasing of the hypothetical deal data, not a factual claim. | Not recommended as a standalone image (see note). Use 07b instead if you want to show the memo. |
| 07b | `07b_ic_memo_executive_summary.png` | The same live memo from Executive Summary onward: Strategic Rationale, Valuation and Synergies sections quoting deterministic values. | Optional. |
| 08 | `08_architecture.png` | The DealSignal AI architecture flow: research → deterministic finance → AI deal team → due diligence → structuring → risk → IC Partner → targeted revisions → approved state → recommendation → IC memo → Excel. | Optional, useful for a technical audience or a comment. |
| 09 | `09_excel_model.png` | **Not captured.** macOS Screen Recording permission is not granted to the terminal session, so desktop capture failed (`screencapture: could not create image from rect`). See the manual checklist below. | Yes, if you capture it manually. |

## Recommended LinkedIn carousel (5 images)

1. `02_deal_overview.png`: what the product is, and the decision at a glance (**input → decision**)
2. `01_public_company_research.png`: sourced research with provenance (**input**)
3. `03_financial_analysis.png`: deterministic valuation modelling (**analysis**)
4. `05_ic_review.png`: IC Partner quality control and unresolved gaps (**quality control**)
5. `06_deal_recommendation.png`: the controlled, evidence-bounded recommendation (**decision**)

## Manual checklist for `09_excel_model.png`

1. Open `reports/linkedin_demo/DealSignal_AI_ACN_EPAM_MA_Model.xlsx` in Microsoft Excel.
2. If Excel shows Protected View, click **Enable Editing** so the formulas calculate, then close
   any banners.
3. Select the **IC Summary** sheet (or **Valuation**). Set zoom to about 110% so the text is
   readable at LinkedIn size.
4. Click an empty cell outside the data so no formula is highlighted. Hide the ribbon (⌘⌥R) if
   you want a cleaner frame.
5. Press **⌘⇧4**, then **Space**, and click the Excel window to capture only that window.
6. Before saving, check that no file path, account name, email or other window appears in the
   frame.
7. Save it as `docs/linkedin/acn_epam/09_excel_model.png`.

## Security review of these images

- No API key, `.env` content, terminal, personal email or SEC User-Agent value appears in any
  image. The Advanced settings panel, which lists model names only, was collapsed.
- No error banners, loading states or Technical details panels appear.
- Every image shows the hypothetical-transaction disclaimer in the sidebar.
