# CV bullets: DealSignal AI (personal project)

- Built **DealSignal AI**, an M&A decision-intelligence platform in Python and Streamlit.
  - It researches listed companies from **SEC EDGAR** filings and **Alpha Vantage** market data,
    tagging each field as reported, derived, market data or user override.
  - It runs deterministic comparable, precedent, DCF, synergy-NPV, dilution and EPS-accretion
    models.
  - It exports a formula-driven Excel model whose 137 formulas are validated against the Python
    engine.
- Designed a **multi-agent AI deal team** on Gemini: nine specialist analysts plus an **IC
  Partner** that reviews the work and triggers targeted, dependency-aware revisions.
  - A numeric-provenance validator lets agents quote only sourced or calculated figures.
  - Bounded retries recover from invalid outputs without weakening the strict schemas.
- Delivered a live, end-to-end **hypothetical Accenture → EPAM** case: research, IC review,
  controlled IC recommendation, memo and Excel model, backed by **1,100+ automated tests**.
  - Human review of sourced data caught and documented three stale SEC facts.
