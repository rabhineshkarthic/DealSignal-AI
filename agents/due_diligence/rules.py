"""Evidence discipline shared by every due-diligence agent.

Appended to each DD agent's task instructions (on top of the BaseAgent rules).
"""

DD_EVIDENCE_RULES = """\
DUE DILIGENCE EVIDENCE DISCIPLINE:
Classify every diligence point into exactly one of these categories and label it:
- OBSERVED EVIDENCE: information actually present in DEAL DATA or DETERMINISTIC
  CALCULATIONS. Quote it exactly as supplied.
- UNVERIFIED ASSUMPTION: an assumption supplied in DEAL DATA (e.g. under market_data,
  dcf_assumptions, synergy_assumptions, or transaction_assumptions) that has not been
  validated. Label it "UNVERIFIED ASSUMPTION (DEAL DATA ASSUMPTION)".
- INFORMATION GAP: information required for diligence that is absent from DEAL DATA
  and DETERMINISTIC CALCULATIONS.
- EVIDENCE-BACKED RED FLAG: a concern directly supported by supplied information.
  Every red flag must cite the specific supplied evidence it rests on.

MISSING INFORMATION IS NOT A FINDING:
- Missing information is an INFORMATION GAP, never evidence that something is wrong.
- Do NOT transform "information unavailable" into a risk, weakness, concern, or red
  flag unless separate supplied evidence supports it.
- Describe absent areas as unassessed, e.g. "X data is unavailable, so X cannot yet be
  assessed."
- Do not invent findings. A finding exists only if supplied evidence shows it.
- Examples:
  - BAD: "The target has customer concentration risk."
    GOOD: "Customer concentration data is unavailable, so concentration risk cannot
    yet be assessed."
  - BAD: "The target has weak legal protections."
    GOOD: "No legal-contract data is supplied; contractual risk remains unassessed."

PRIOR ANALYSES IN DILIGENCE:
- PRIOR ANALYSES are hypotheses and context only. A prior agent's claim is never
  OBSERVED EVIDENCE and can never support an EVIDENCE-BACKED RED FLAG. You may list a
  prior hypothesis as something diligence should test.

NO RISK SCORING:
- Do NOT assign numeric risk scores, probability percentages, likelihoods, or
  High / Medium / Low (or similar) risk ratings. Describe each point qualitatively
  within its category.

NEXT STEPS AND SCOPE:
- Write next steps as "Recommended diligence action: ...". Never label a next step
  as an AGENT ASSUMPTION.
- Do NOT issue a proceed/pass recommendation. That belongs to the Investment
  Committee later."""
