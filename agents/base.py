"""Reusable base class for all deal-team agents.

An agent turns a deal context into a structured prompt, sends it to the shared
LLM client, and returns the text. Agents perform judgment and narrative only;
all financial arithmetic belongs to valuation.py.
"""

from __future__ import annotations

import json
from typing import Any, Protocol

from agent_validation import decoded_json_strings, validate_agent_output


class LLMClient(Protocol):
    """Anything with this method can back an agent (the Gemini client, or a test fake)."""

    def generate(self, system_prompt: str, user_prompt: str) -> str: ...


FINANCIAL_DATA_RULES = """\
FINANCIAL DATA RULES:
- Never fabricate a financial figure.
- Never fabricate market data (market size, market share, growth rates, peer multiples, deal values).
- Never calculate valuation results yourself; valuations are produced separately by a deterministic engine.
- Use only figures supplied in DEAL DATA or DETERMINISTIC CALCULATIONS.
- Figures from DEAL DATA may be quoted.
- Figures from DETERMINISTIC CALCULATIONS may be quoted. They are trusted outputs of
  the Python calculation layer; do not recompute, adjust, or second-guess the arithmetic.
- Figures from PRIOR ANALYSES are not trusted source data. Never quote a figure that
  appears only in PRIOR ANALYSES.
- Do not derive additional numbers yourself.
- If data needed for a conclusion is absent, explicitly say that it is unavailable.
- Clearly distinguish factual deal data from qualitative judgment.
- Any newly proposed number must be labelled explicitly as an ASSUMPTION.
- Do not present assumptions as facts.

SOURCE ATTRIBUTION:
- Never promote PRIOR ANALYSES into DEAL DATA or DETERMINISTIC CALCULATIONS.
- Attribute every claim to its true source. Write "DEAL DATA states/reports ..." only
  for content actually present in DEAL DATA. Attribute content from PRIOR ANALYSES to
  the agent that wrote it (e.g. "the Deal Lead hypothesized ...").
- A hypothesis or assumption from another agent, including anything that agent
  labelled AGENT ASSUMPTION, remains that agent's judgment. It is never a DEAL DATA
  ASSUMPTION and never a fact.
- Never quote a figure or assumption that appears only in PRIOR ANALYSES as source data.
- Never infer a company's products, technologies, capabilities, industry, business
  model, customers, markets, or strategic motives from its name.
- A company name may be repeated, but it is not evidence of anything.
- PREVIOUS OUTPUT, when present, is your own earlier generated text, not source
  evidence.
- REVIEW FEEDBACK, when present, is quality-control instruction, not source evidence.
- Neither PREVIOUS OUTPUT nor REVIEW FEEDBACK may authorize a factual claim or a
  numerical value. Only DEAL DATA and DETERMINISTIC CALCULATIONS are approved factual
  and numeric sources; PRIOR ANALYSES remain judgment and context only.
- VALIDATION FEEDBACK, when present, lists numerals the numeric validator rejected in
  your previous attempt. It is not source evidence and never authorizes a number: remove
  those numerals unless DEAL DATA or DETERMINISTIC CALCULATIONS contain them.
- acquirer.shares_outstanding in DEAL DATA is the acquirer share basis used in the
  transaction model. Describe it using acquirer.shares_outstanding_definition (for example
  "an acquirer share basis of N million, defined as weighted-average basic shares used as
  a modelling proxy"). Never call it point-in-time shares outstanding unless that is its
  stated definition.
- IC REVIEW CONTEXT, when present, is the Investment Committee reviewer's
  quality-control assessment. It is not source evidence and may not authorize a
  factual claim or a numerical value; never promote a statement in it to fact.

REVISION DISCIPLINE (applies when REVIEW FEEDBACK is present):
- Fix only issues that are supported by DEAL DATA and DETERMINISTIC CALCULATIONS.
- Do not blindly accept factual assertions inside REVIEW FEEDBACK, and do not treat
  it as new source data. An instruction such as "reconsider the customer
  concentration risk" does not prove that the risk exists: if source data is absent,
  the matter remains unassessed.
- Do not copy figures from REVIEW FEEDBACK or PREVIOUS OUTPUT unless they appear in
  DEAL DATA or DETERMINISTIC CALCULATIONS.
- Produce a complete replacement analysis. Preserve your original mandatory output
  format and continue to follow all of your role-specific rules.

NO ARITHMETIC RULE (HARD RULE):
You must NOT perform any financial arithmetic. Every number you write must appear
verbatim in DEAL DATA or DETERMINISTIC CALCULATIONS, or be explicitly labelled as an
ASSUMPTION you are proposing. You may quote those figures exactly as supplied. You may NOT:
- add, subtract, multiply, or divide figures
- calculate percentages, ratios, multiples, or margins
- calculate combined or pro forma figures (e.g. combined revenue or combined EBITDA)
- calculate market capitalization
- total or net synergies
- calculate premiums
- infer or hypothesize accretion or dilution
- derive any valuation metric
- approximate or round any of the above ("roughly", "about", "nearly", "more than Nx")
If a derived metric would help your analysis, name the metric and state that it
must be supplied by the deterministic calculation layer. Do not estimate it.

NUMBER FORMATTING (AUTOMATICALLY CHECKED):
Every numeral in your output is automatically checked against DEAL DATA and
DETERMINISTIC CALCULATIONS, and your output is rejected if any numeral does not
match a supplied value.
- Quote each number in the form supplied. Do not convert decimals to percentages:
  a rate supplied as the decimal R must be quoted as R, never as a percentage.
- Do not change scale or units: an amount supplied as X in the stated units must be
  quoted as X, never rescaled (e.g. never expanded into a full dollar amount).
- Do not round supplied numbers.
- Write counts, ordinals, and list positions in words ("three methods", "the second
  year"), not numerals.

Examples (placeholders stand for figures from DEAL DATA):
- BAD: "The target would increase the acquirer's revenue by approximately N%."
  GOOD: "The acquirer reports revenue of X and the target reports revenue of Y. A
  relative-scale metric should be computed by the deterministic calculation layer."
- BAD: "The combined company would have revenue of X+Y."
  GOOD: "The supplied data reports acquirer revenue of X and target revenue of Y."
- BAD: "The transaction will be accretive."
  GOOD: "Accretion/dilution cannot be determined by this agent and must come from the
  deterministic accretion_dilution calculation.\""""

DATA_CONVENTIONS = """\
DATA CONVENTIONS:
- Monetary amounts are in the currency and units stated in DEAL DATA.
- Rates in DEAL DATA are expressed as decimals. Quote them as decimals exactly as
  supplied; do not convert them to percentages."""


ALLOWED_CONTEXT_KEYS = frozenset(
    {"deal", "deterministic_calculations", "prior_analyses", "previous_output",
     "review_feedback", "review_context", "validation_feedback", "schema_feedback"}
)


class BaseAgent:
    """Subclasses set `name`, `role`, and `system_prompt` (their task-specific instructions)."""

    name: str = "Base Agent"
    role: str = "an analyst on an M&A advisory deal team"
    system_prompt: str = ""
    # Set True for agents whose output is strict JSON: their decoded strings are
    # validated in addition to the raw text (see validation_texts).
    structured_json_output: bool = False

    def __init__(self, llm_client: LLMClient) -> None:
        self.llm_client = llm_client

    def build_system_prompt(self) -> str:
        """Role, then universal guardrails, then agent-specific instructions."""
        parts = [
            f"You are the {self.name}, {self.role}.",
            FINANCIAL_DATA_RULES,
            DATA_CONVENTIONS,
        ]
        if self.system_prompt:
            parts.append(self.system_prompt.strip())
        return "\n\n".join(parts)

    def build_user_prompt(self, context: dict[str, Any]) -> str:
        """Render the context as clearly delimited sections.

        Expected context keys:
        - "deal" (required): the deal data dict, e.g. from models.deal_to_context
        - "deterministic_calculations" (optional): Python-computed results,
          e.g. from deal_analysis.build_valuation_context. Kept separate from DEAL DATA.
        - "prior_analyses" (optional): {agent name: that agent's text output}
        - "previous_output" (optional): this agent's own earlier output, for revision
        - "review_feedback" (optional): IC Partner feedback addressed to this agent
        None of the optional sections except deterministic_calculations is an approved
        source for numbers (see validation_sources).
        """
        if "deal" not in context:
            raise ValueError("context must include 'deal'")
        unexpected = context.keys() - ALLOWED_CONTEXT_KEYS
        if unexpected:
            raise ValueError(f"unexpected context keys: {sorted(unexpected)}")

        sections = [
            "=== DEAL DATA (source inputs: reported figures and supplied assumptions) ===",
            json.dumps(context["deal"], indent=2),
        ]
        calculations = context.get("deterministic_calculations")
        if calculations:
            sections.append(
                "=== DETERMINISTIC CALCULATIONS (trusted Python outputs; quote, never recompute) ==="
            )
            sections.append(json.dumps(calculations, indent=2))
        prior = context.get("prior_analyses") or {}
        if prior:
            sections.append("=== PRIOR ANALYSES (from other agents; judgment, not source data) ===")
            for agent_name, output in prior.items():
                sections.append(f"--- {agent_name} ---\n{output.strip()}")
        previous = context.get("previous_output")
        if previous:
            sections.append("=== PREVIOUS OUTPUT (your earlier text; not source evidence) ===")
            sections.append(previous.strip())
        feedback = context.get("review_feedback")
        if feedback:
            sections.append(
                "=== REVIEW FEEDBACK (quality-control instruction; not source evidence) ==="
            )
            sections.append(feedback.strip())
        validation_feedback = context.get("validation_feedback")
        if validation_feedback:
            sections.append(
                "=== VALIDATION FEEDBACK (from the numeric validator; not source evidence) ==="
            )
            sections.append(validation_feedback.strip())
        schema_feedback = context.get("schema_feedback")
        if schema_feedback:
            sections.append(
                "=== SCHEMA FEEDBACK (from the strict output-schema check; not source evidence) ==="
            )
            sections.append(schema_feedback.strip())
        review_context = context.get("review_context")
        if review_context:
            sections.append(
                "=== IC REVIEW CONTEXT (quality-control context; not source evidence) ==="
            )
            sections.append(json.dumps(review_context, indent=2))
        sections.append("=== TASK ===")
        if feedback:
            sections.append(
                f"Produce a complete revised {self.name} output that addresses the REVIEW "
                "FEEDBACK where it is supported by DEAL DATA and DETERMINISTIC CALCULATIONS, "
                "following your REVISION DISCIPLINE and your original output format."
            )
        else:
            sections.append(f"Produce your {self.name} output following your instructions.")
        return "\n\n".join(sections)

    def validation_sources(self, context: dict[str, Any]) -> list[Any]:
        """Approved sources for numbers in the output: DEAL DATA and DETERMINISTIC
        CALCULATIONS only. PRIOR ANALYSES, PREVIOUS OUTPUT, REVIEW FEEDBACK, IC REVIEW
        CONTEXT, VALIDATION FEEDBACK, and SCHEMA FEEDBACK are deliberately excluded."""
        sources = [context["deal"]]
        if context.get("deterministic_calculations"):
            sources.append(context["deterministic_calculations"])
        return sources

    def run(self, context: dict[str, Any]) -> str:
        """Generate, then validate numeric provenance before returning.

        Raises agent_validation.AgentValidationError if the response contains a number
        not found in DEAL DATA or DETERMINISTIC CALCULATIONS. The response is never
        rewritten.
        """
        user_prompt = self.build_user_prompt(context)
        response = self.llm_client.generate(
            system_prompt=self.build_system_prompt(),
            user_prompt=user_prompt,
        )
        sources = self.validation_sources(context)
        for text in self.validation_texts(response):
            validate_agent_output(self.name, text, sources)
        return response

    def validation_texts(self, response: str) -> list[str]:
        """Texts the numeric validator must check. The raw response is always
        included. For structured_json_output agents, every decoded JSON string is
        added too, because escapes such as \\n can hide numbers from a scan of the raw
        text. Checking more text can only reject more, never approve more; undecodable
        output is left for the agent's strict parser to reject."""
        if self.structured_json_output:
            return [response, *decoded_json_strings(response)]
        return [response]
