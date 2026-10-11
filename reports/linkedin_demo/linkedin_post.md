I wanted to explore what an AI-native M&A consulting workflow could look like beyond a single chatbot prompt.

So I built DealSignal AI, a personal decision-intelligence project that takes two listed companies from public filings to an Investment Committee recommendation.

For the demonstration, I used a hypothetical Accenture → EPAM transaction.

How it works:

🔎 Researches public-company financials from SEC EDGAR filings (plus a market quote from Alpha Vantage). Every value is tagged REPORTED, DERIVED, MARKET DATA or USER OVERRIDE, with its filing, period and XBRL concept.
🧾 Separates sourced facts from user assumptions. Multiples, DCF inputs, synergies and offer terms are clearly labelled as assumptions.
🧮 Performs valuation and transaction arithmetic deterministically in Python: comparables, precedents, DCF, synergy NPV, consideration mix, dilution and EPS accretion.
🤝 Runs specialist AI workstreams on Gemini: strategy, valuation, synergies, financial, commercial and legal/operational diligence, structuring and risk.
✅ Uses an AI "IC Partner" to review the team's work and trigger targeted revisions only where needed.
🔢 Validates every numeral the agents write. An agent can only quote numbers that exist in the sourced data or the Python calculations.
📄 Generates an IC recommendation, an IC memo and a formula-driven Excel model.

What I found most interesting in the live run:

• Human review mattered. The research table exposed three stale SEC data points, including a share count from a 2010 filing. I replaced each one with a documented override from a current 10-Q, and the audit trail keeps the original value.
• The quality controls did real work. The first memo draft was rejected for a formatting error, and only the memo writer was retried, with feedback on what failed.
• The system did not force a "yes". The offer sat inside the deterministic valuation range and was EPS-accretive, but the memo concluded "Decision deferred pending additional information", because public filings don't contain the customer, contract and historical detail an IC would need. That is the conclusion I'm showing, unedited.

Built with Python, Streamlit, Gemini, SEC EDGAR, Alpha Vantage and OpenPyXL, and covered by 1,100+ automated tests.

⚠️ Hypothetical transaction for demonstration purposes. Not an announced transaction and not investment advice. This is a personal project. It is not client work, and I have no affiliation with either company.

#MergersAndAcquisitions #DecisionIntelligence #AI #MultiAgentSystems #FinancialModelling #Consulting #Python
