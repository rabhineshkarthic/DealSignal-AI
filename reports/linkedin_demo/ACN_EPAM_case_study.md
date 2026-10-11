# DealSignal AI
## Accenture → EPAM Hypothetical M&A Case

> **Hypothetical transaction for demonstration purposes. Not an announced transaction and not investment advice.**
> Accenture has not announced any intention to acquire EPAM, and nothing here suggests that EPAM
> is for sale. This is a personal portfolio project, not client work.

All figures are in USD millions unless stated otherwise. Every number below was produced by
DealSignal AI's deterministic Python engine, or retrieved by its research layer, during one live
run on 29 September 2026. None was recomputed by hand for this write-up.

---

### Case Objective

The case tests whether a structured, AI-assisted M&A workflow can take two listed companies from
public filings to an Investment Committee (IC) decision. The workflow keeps three things strictly
separate:

- **Facts:** sourced from SEC filings and market data;
- **Assumptions:** entered by the user and labelled as such;
- **Judgment:** written by AI specialist agents and reviewed by an AI IC Partner.

The system was asked to assess a hypothetical acquisition of **EPAM Systems, Inc.** (target, NYSE:
EPAM) by **Accenture plc** (acquirer, NYSE: ACN), and to produce:

- a valuation;
- a synergy case;
- transaction mechanics and EPS impact;
- diligence findings;
- a controlled IC recommendation;
- an IC memo;
- a formula-driven Excel model.

### Research

Both companies were researched live from **SEC EDGAR** (XBRL CompanyFacts). The acquirer share
price came from **Alpha Vantage**. Each value carries a provenance status, reporting period, source
filing and XBRL concept or derivation.

| Field | Accenture plc | EPAM Systems, Inc. |
|---|---|---|
| Revenue | 69,672.977 (REPORTED, 10-K, FY to 31 Aug 2025) | 5,457.056 (REPORTED, 10-K, FY 2025) |
| EBITDA | 11,594.049 (DERIVED: operating income + depreciation + amortization) | 644.814 (DERIVED: operating income + D&A) |
| Net income | 7,678.433 (REPORTED) | 377.678 (REPORTED) |
| Free cash flow | not required for the acquirer | 612.691 (DERIVED: operating cash flow − capex) |
| Cash | 10,165.245 (REPORTED, 10-Q, as of 31 May 2026) | 789.397 (REPORTED, 10-Q, as of 30 Jun 2026) |
| Debt | 5,142.265 (USER OVERRIDE, sourced from SEC 10-Q; see below) | 25.0 (USER OVERRIDE, sourced from SEC 10-Q; see below) |
| Shares outstanding | 612.213453m (USER OVERRIDE, sourced from SEC 10-Q; see below) | not required for the target |
| Share price | USD 174.47 (MARKET DATA, Alpha Vantage, latest trading day 2026-09-28) | not required |

**Provenance mix:** 6 REPORTED, 3 DERIVED, 1 MARKET DATA and 3 USER OVERRIDE. Nothing was
silently filled.

**Human review of stale facts.** The research table showed that three automatically selected SEC
facts were out of date:

- Accenture debt, as of 31 August 2024;
- Accenture share count, from a 2010 cover page;
- EPAM debt, as of 31 December 2015.

In each case the company had stopped reporting under the XBRL concepts the tool maps. Instead of
using stale values, each one was replaced in the review step with a documented USER OVERRIDE from
a current SEC filing:

- **Accenture debt, 5,142.265:** `us-gaap:LongTermDebtAndCapitalLeaseObligations` (5,029.449)
  plus `us-gaap:DebtCurrent` (112.816), 10-Q as of 31 May 2026.
- **Accenture shares, 612.213453m:** `us-gaap:WeightedAverageNumberOfSharesOutstandingBasic`
  for the quarter 1 March–31 May 2026, 10-Q filed 18 June 2026. This is a basic weighted-average
  count, used because no current point-in-time share count is available in the SEC CompanyFacts
  data.
- **EPAM debt, 25.0:** `us-gaap:LongTermDebtNoncurrent`, 10-Q as of 30 June 2026. No current
  debt was reported.

Each override keeps its original value and reason in the audit trail, including in the Excel
cell comments.

Qualitative information (products, customers, markets, contracts, litigation) is not researched
by design. It remains an explicit information gap.

### Transaction Assumptions

These are **illustrative user assumptions, not researched facts**. The app labels them as such.

| Assumption | Value |
|---|---|
| Comparable EV / EBITDA | 8.20x |
| Precedent transaction EV / EBITDA | 12.30x |
| EV / Revenue multiples | not used (left blank; target EBITDA is positive) |
| DCF | 5 years, FCF growth 8.0%, WACC 10.5%, terminal growth 2.5% |
| Synergies | annual cost synergy 250, annual revenue-synergy EBITDA 75, 5 realization years, tax 25.3%, discount rate 10.5%, integration cost 300 |
| Offer | enterprise value 6,300; 70% cash / 30% stock; new-debt interest 5.5%; transaction tax rate 25.3% |

### Valuation

| Method | Enterprise value |
|---|---|
| Comparable companies (8.20x EBITDA) | 5,287.4748 |
| Precedent transactions (12.30x EBITDA) | 7,931.2122 |
| DCF | 9,863.0608 |
| **Deterministic range** | **5,287.4748 – 9,863.0608** (midpoint 7,575.2678; average 7,693.9159) |
| **Offer EV** | **6,300.0: within the range**, above comparables, below precedents and the DCF |

DCF detail:

- PV of forecast FCF: 2,861.6950;
- discounted terminal value: 7,001.3658;
- terminal value share of EV: 71.0%.

The terminal value share shows how sensitive the DCF is to the WACC and terminal-growth
assumptions. The system does not calculate premiums or discounts to the methods.

### Synergies

The synergy inputs are **assumptions that have not been operationally validated**.

- Annual pre-tax synergies: 325.0.
- After-tax: 242.775 per year for 5 years.
- PV of synergies: 908.6724.
- Integration cost: 300.0, one-off at close.
- **Net synergy value: 608.6724.**

Modelling conventions: run-rate synergies count in full from year 1 with no ramp-up, and there is
no terminal value for synergies.

### Transaction Mechanics

| Item | Value |
|---|---|
| Target net debt | −764.397 (net cash) |
| Offer equity value | 7,064.397 |
| Cash consideration | 4,945.0779 |
| Stock consideration | 2,119.3191 |
| New Accenture shares issued | 12.1472m |
| Pro forma shares | 624.3606m |
| EPAM holders' ownership of the combined company | 1.95% |
| New debt under the model's financing convention | 4,945.0779 (after-tax interest 203.1685 a year) |
| EPS excluding synergies | 12.5421 → 12.5776 (+0.0355, +0.3%): **accretive** |
| EPS including full run-rate synergies | 12.5421 → 12.9664 (+0.4243, +3.4%): **accretive** (not the expected case) |

Funding the cash portion entirely with new debt is a modelling convention, not a financing plan.

### AI M&A Deal Team

Nine specialist agents ran live on Gemini, in dependency order:

1. Deal Lead;
2. Strategic Fit;
3. Valuation and Synergy;
4. Financial, Commercial, and Legal & Operational Due Diligence;
5. Deal Structuring;
6. Risk.

Each agent interprets the deterministic outputs but cannot calculate new numbers. Every numeral
it writes is checked against the source data and deterministic calculations.

The final reviewed findings:

- **Strategic fit:** product, geographic and customer fit could not be assessed, because the
  supplied data contains no product, market or customer information. The agents reported this as
  a gap rather than inferring it from the company names.
- **Valuation:** the offer sits within the deterministic range, above the comparables method and
  below the precedent and DCF methods. The DCF depends heavily on its WACC and terminal-growth
  inputs.
- **Structuring:** consideration, dilution and EPS were quoted from the engine. The debt
  financing is a modelling convention.
- **Risk:** the evidence-backed risks were:
  - where the offer sits relative to the market methods;
  - synergy timing risk, because full run-rate synergies are assumed from year one;
  - reliance on debt financing under the model convention.

### Due Diligence

- **Financial:** revenue and EBITDA quality, normalization, working capital, capex and tax
  exposure cannot yet be assessed. There are no multi-period historical statements or detailed
  ledgers.
- **Commercial:** market position, competition, customer concentration and retention, pricing
  power and demand sustainability are all information gaps.
- **Legal & operational:** material contracts, change-of-control clauses, litigation, IP, data
  privacy, key-person dependencies, systems and integration complexity remain unassessed.

### IC Review

- **IC Partner review:** 1 round, **Approved**. The partner found the analyses consistent with the
  source data and deterministic outputs, with assumptions attributed and gaps separated from
  risks.
- **Revision activity:** none requested in this run, so there was no targeted revision cascade.
  The workflow supports bounded review rounds and dependency-aware reruns when the IC Partner asks
  for revisions.
- **Numeric-validation retries (deal team):** none needed. All agents were accepted on their
  first output.
- **IC memo:** the first memo draft was rejected by the strict schema check because the JSON was
  wrapped in a Markdown code fence, which is a parse failure. The memo writer alone was retried
  with feedback describing the failure, and the second draft passed:
  - numeric provenance validation;
  - strict JSON and schema parsing;
  - required heading order;
  - controlled-conclusion validation.

  The rejected draft is kept for audit only and was never used as evidence.
- **Unresolved information gaps**, as recorded by the IC Partner:
  1. multi-period historical financial statements for both companies;
  2. qualitative product, technology, capability, customer and market-share information;
  3. definitive debt financing agreements and lender commitments.

### Deal Recommendation

**Controlled IC conclusion: "Decision deferred pending additional information"**

The offer sits within the deterministic valuation range, and EPS is accretive in both scenarios.
The memo nevertheless deferred a decision, because the qualitative and historical diligence
evidence needed to support a commitment is not in the public filings used. The required next
steps it lists are:

- obtain multi-period historical financial statements for EPAM;
- obtain customer contracts, customer lists and retention metrics;
- verify the operational feasibility of the 250 cost and 75 revenue synergies;
- audit historical integration spend against the 300 integration cost;
- obtain formal financing commitments to replace the model's financing convention;
- conduct legal, tax, litigation and IP audits.

This is the conclusion the system reached, reported unchanged.

### Deliverables

- **IC memo:** `DealSignal_AI_ACN_EPAM_IC_Memo.md`, the live memo exactly as generated.
- **Excel model:** `DealSignal_AI_ACN_EPAM_MA_Model.xlsx`.
  - 9 sheets: IC Summary, Assumptions, Valuation, Synergies, Transaction Structure, Accretion
    Dilution, Due Diligence, Risk Summary, IC Memo.
  - 137 live formulas, each checked against the Python engine before the file was written.
  - Research provenance and overrides are recorded as cell comments.
- **Research provenance:** an SEC filing, period and XBRL concept or derivation for every
  researched field, and an Alpha Vantage source for the share price.
- **Deal recommendation:** the conclusion, conditions, risks and gaps, and financial context
  (screenshot `06_deal_recommendation.png`).

### Architecture

```
Public-company research (SEC EDGAR + Alpha Vantage, with provenance)
  → User review (accept / override with reason / fill missing)
  → Deterministic Python finance (valuation, DCF, synergies, transaction, accretion)
  → AI specialist agents (strategy, valuation, synergy, 3× due diligence, structuring, risk)
  → IC Partner quality control (strict JSON review, bounded targeted revisions)
  → Deal recommendation (controlled conclusion)
  → IC memo (strict schema, numeric provenance validation, bounded retry)
  → Excel model (formulas validated against Python)
```

The LLMs never do financial arithmetic. Numbers may come only from sourced deal data or the
deterministic calculations, and a validator rejects any other numeral.

### Disclaimer

This is a hypothetical transaction for demonstration purposes. It is not an announced Accenture
transaction, and it is not investment advice. The synergy, multiple, DCF and offer inputs are
illustrative assumptions. The research is limited to structured SEC financial data and one market
quote, and it has known coverage gaps (see Research).
