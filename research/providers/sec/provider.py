"""SECProvider: CompanyResearchProvider for SEC-reporting U.S. public companies.

All SEC-specific knowledge (CIK, forms, US-GAAP concepts, accession numbers) stays in
this package. The provider returns provider-neutral CompanyResearchResult objects.
"""

from __future__ import annotations

import re
from dataclasses import replace
from decimal import Decimal
from typing import Any

from research.errors import AmbiguousCompanyError, CompanyNotFoundError
from research.models import (
    POINT_IN_TIME_SHARES,
    CompanyCandidate,
    CompanyResearchResult,
    ResearchStatus,
    ResearchValue,
    SourceDocument,
    missing_value,
)
from research.normalizer import describe_scaling, money_unit, normalize, shares_unit
from research.providers.sec import mappings as m
from research.providers.sec.client import (
    COMPANY_FACTS_URL,
    FILING_INDEX_URL,
    SECClient,
    cik10,
)
from research.providers.sec.selector import (
    Fact,
    facts_for,
    instant_facts_by_date,
    is_instant,
    most_recently_filed,
    select_annual,
    select_annual_for_period,
    select_instant,
    INSTANT_FORMS,
)
from research.quality import mark_if_stale

PROVIDER = "SEC EDGAR"
POINT_IN_TIME_FIELDS = ("cash", "debt", "shares_outstanding")
SOURCE_NAME = "XBRL CompanyFacts"
MAX_CANDIDATES = 10


class SECProvider:
    name = PROVIDER
    jurisdiction_code = "US"
    id_label = "CIK"

    def __init__(self, client: SECClient | None = None) -> None:
        self.client = client or SECClient()

    # --- identity ----------------------------------------------------------------------
    def _companies(self) -> list[CompanyCandidate]:
        table = self.client.company_tickers()
        fields = table["fields"]
        rows = [dict(zip(fields, row)) for row in table["data"]]
        return [CompanyCandidate(cik10(r["cik"]), r["name"], r.get("ticker"), r.get("exchange"),
                                 "US", self.id_label) for r in rows]

    def search_company(self, query: str) -> list[CompanyCandidate]:
        q = query.strip().upper()
        if not q:
            return []
        companies = self._companies()
        exact = [c for c in companies if (c.ticker or "").upper() == q]
        if exact:
            return exact
        matches = [c for c in companies
                   if (c.ticker or "").upper().startswith(q) or q in c.name.upper()]
        unique = {c.provider_company_id + (c.ticker or ""): c for c in matches}
        return sorted(unique.values(), key=lambda c: (c.name, c.ticker or ""))[:MAX_CANDIDATES]

    def resolve_company(self, query: str) -> CompanyCandidate:
        """Only an exact, unique ticker resolves automatically; anything else needs the
        user to confirm a candidate."""
        candidates = self.search_company(query)
        q = query.strip().upper()
        exact = [c for c in candidates if (c.ticker or "").upper() == q]
        if len({c.provider_company_id for c in exact}) == 1:
            return exact[0]
        if not candidates:
            raise CompanyNotFoundError(f"No SEC-registered company matches {query!r}.")
        raise AmbiguousCompanyError(
            f"{len(candidates)} companies match {query!r}; select one to continue.", candidates)

    # --- research ----------------------------------------------------------------------
    def research_company(self, candidate: CompanyCandidate, *, units: str = "millions",
                         force_refresh: bool = False) -> CompanyResearchResult:
        submissions = self.client.submissions(candidate.provider_company_id,
                                              force_refresh=force_refresh)
        facts = self.client.company_facts(candidate.provider_company_id,
                                          force_refresh=force_refresh)
        builder = _FieldBuilder(candidate, facts, units)
        fields = {
            "revenue": builder.annual("revenue", m.REVENUE),
            "ebitda": builder.ebitda(),
            "net_income": builder.annual("net_income", m.NET_INCOME),
            "free_cash_flow": builder.free_cash_flow(),
            "cash": builder.instant("cash", m.CASH),
            "debt": builder.debt(),
            "shares_outstanding": builder.shares(),
        }
        # Point-in-time inputs must be current: an old fact is kept for provenance but
        # marked STALE so it is never used automatically (research.quality).
        reference = builder.latest_periodic_filing()
        for name in POINT_IN_TIME_FIELDS:
            fields[name] = mark_if_stale(fields[name], reference)
        return CompanyResearchResult(
            company_name=submissions.get("name") or candidate.name,
            legal_name=facts.get("entityName") or submissions.get("name") or candidate.name,
            ticker=candidate.ticker, provider_company_id=candidate.provider_company_id,
            id_label=self.id_label, jurisdiction="US",
            exchange=candidate.exchange or ", ".join(submissions.get("exchanges") or []) or None,
            fiscal_year_end=_fiscal_year_end(submissions.get("fiscalYearEnd")),
            reporting_currency=m.MONEY_UNIT, units=units, provider_name=PROVIDER,
            fields=fields, source_documents=builder.documents(),
            notes=("Financial facts come from SEC XBRL CompanyFacts (standard taxonomies).",
                   "Qualitative company information is not researched."),
        )


def _fiscal_year_end(mmdd: str | None) -> str | None:
    if not mmdd or len(mmdd) != 4:
        return None
    return f"{mmdd[:2]}-{mmdd[2:]} (MM-DD)"


class _FieldBuilder:
    def __init__(self, candidate: CompanyCandidate, facts: dict[str, Any], units: str):
        self.candidate, self.facts, self.units = candidate, facts, units
        self.cik = candidate.provider_company_id
        self.used: dict[str, Fact] = {}

    # value construction -------------------------------------------------------------------
    def _filing_url(self, accn: str | None) -> str | None:
        if not accn:
            return None
        return FILING_INDEX_URL.format(cik_int=int(self.cik), accession_nodash=accn.replace("-", ""))

    def _from_fact(self, field: str, fact: Fact, *, shares: bool = False) -> ResearchValue:
        if fact.accn:
            self.used[fact.accn] = fact
        return ResearchValue(
            field_name=field, status=ResearchStatus.REPORTED,
            normalized_value=normalize(fact.value, self.units),
            normalized_unit=shares_unit(self.units) if shares else money_unit(m.MONEY_UNIT, self.units),
            raw_value=str(fact.value), raw_unit=fact.unit, source_provider=PROVIDER,
            source_name=SOURCE_NAME, source_url=self._filing_url(fact.accn), concept=fact.concept,
            taxonomy=fact.taxonomy, report_type=fact.form, fiscal_year=fact.fy,
            fiscal_period=fact.fp, period_start=fact.start, period_end=fact.end,
            filed_date=fact.filed, source_document_id=fact.accn,
            notes=f"Normalization: {describe_scaling(fact.unit, self.units)}",
            provider_metadata=(("CIK", self.cik), ("SEC form", fact.form or ""),
                               ("Accession number", fact.accn or ""),
                               ("CompanyFacts API", COMPANY_FACTS_URL.format(cik=self.cik))),
        )

    def _derived(self, field: str, total: Decimal, derivation: str,
                 ingredients: tuple[ResearchValue, ...], period: tuple[str | None, str],
                 note: str) -> ResearchValue:
        first = ingredients[0]
        return ResearchValue(
            field_name=field, status=ResearchStatus.DERIVED,
            normalized_value=normalize(total, self.units),
            normalized_unit=money_unit(m.MONEY_UNIT, self.units), raw_value=str(total),
            raw_unit=m.MONEY_UNIT, source_provider=PROVIDER, source_name=SOURCE_NAME,
            report_type=first.report_type, fiscal_year=first.fiscal_year,
            fiscal_period=first.fiscal_period, period_start=period[0], period_end=period[1],
            derivation=derivation, ingredients=ingredients, notes=note,
            provider_metadata=(("CIK", self.cik),),
        )

    # fields ------------------------------------------------------------------------------------
    def annual(self, field: str, concepts) -> ResearchValue:
        fact = select_annual(self.facts, concepts, m.MONEY_UNIT)
        if fact is None:
            return missing_value(field, f"No annual (10-K, FY) {m.LABELS[field].lower()} fact "
                                        "was found for the mapped concepts.", PROVIDER)
        return self._from_fact(field, fact)

    def instant(self, field: str, concepts) -> ResearchValue:
        fact = select_instant(self.facts, concepts, m.MONEY_UNIT)
        if fact is None:
            return missing_value(field, f"No balance-sheet {m.LABELS[field].lower()} fact was "
                                        "found for the mapped concepts.", PROVIDER)
        return self._from_fact(field, fact)

    def ebitda(self) -> ResearchValue:
        # A. reported EBITDA under an exact concept name, in any taxonomy present
        reported = []
        for taxonomy, concepts in self.facts.get("facts", {}).items():
            for name in m.REPORTED_EBITDA_CONCEPTS:
                if name in concepts:
                    reported.append((taxonomy, name))
        if reported:
            fact = select_annual(self.facts, reported, m.MONEY_UNIT)
            if fact is not None:
                return self._from_fact("ebitda", fact)
        # B. derived: operating income + D&A for the same annual period
        operating = select_annual(self.facts, m.OPERATING_INCOME, m.MONEY_UNIT)
        if operating is None:
            return missing_value("ebitda", "A reliable EBITDA could not be found or derived: no "
                                           "annual operating income fact. Enter EBITDA manually "
                                           "before building the deal.", PROVIDER)
        dna = select_annual_for_period(self.facts, m.DEPRECIATION_AMORTIZATION, m.MONEY_UNIT,
                                       operating.period)
        note = "Derived by this tool; not company-reported EBITDA. No other adjustments applied."
        if dna is not None:  # B. combined D&A (preferred)
            ingredients = (self._from_fact("operating_income", operating),
                           self._from_fact("depreciation_amortization", dna))
            return self._derived(
                "ebitda", operating.value + dna.value,
                f"EBITDA = operating income ({operating.taxonomy}:{operating.concept}) + "
                f"depreciation & amortization ({dna.taxonomy}:{dna.concept}), period "
                f"{operating.start} to {operating.end}",
                ingredients, operating.period, note)
        # C. separate depreciation + amortization, both for the same annual period
        dep = select_annual_for_period(self.facts, m.DEPRECIATION, m.MONEY_UNIT, operating.period)
        amort = select_annual_for_period(self.facts, m.AMORTIZATION, m.MONEY_UNIT, operating.period)
        if dep is None or amort is None:
            absent = " and ".join(name for name, fact in (("depreciation", dep),
                                                          ("amortization", amort)) if fact is None)
            return missing_value("ebitda", "A reliable EBITDA could not be found or derived: no "
                                           "combined depreciation & amortization fact, and no "
                                           f"separate {absent} fact, for the same annual period "
                                           "as operating income. Enter EBITDA manually before "
                                           "building the deal.", PROVIDER)
        ingredients = (self._from_fact("operating_income", operating),
                       self._from_fact("depreciation", dep),
                       self._from_fact("amortization", amort))
        return self._derived(
            "ebitda", operating.value + dep.value + amort.value,
            f"EBITDA = operating income ({operating.taxonomy}:{operating.concept}) + depreciation "
            f"({dep.taxonomy}:{dep.concept}) + amortization ({amort.taxonomy}:{amort.concept}), "
            f"period {operating.start} to {operating.end}",
            ingredients, operating.period, note)

    def free_cash_flow(self) -> ResearchValue:
        ocf = select_annual(self.facts, m.OPERATING_CASH_FLOW, m.MONEY_UNIT)
        if ocf is None:
            return missing_value("free_cash_flow", "Free cash flow could not be derived: no annual "
                                                   "operating cash flow fact.", PROVIDER)
        capex = select_annual_for_period(self.facts, m.CAPEX, m.MONEY_UNIT, ocf.period)
        if capex is None:
            return missing_value("free_cash_flow", "Free cash flow could not be derived: no capital "
                                                   "expenditure fact for the same annual period as "
                                                   "operating cash flow.", PROVIDER)
        ingredients = (self._from_fact("operating_cash_flow", ocf),
                       self._from_fact("capital_expenditures", capex))
        return self._derived(
            "free_cash_flow", ocf.value - capex.value,
            f"Free cash flow = operating cash flow ({ocf.taxonomy}:{ocf.concept}) − capital "
            f"expenditures ({capex.taxonomy}:{capex.concept}), period {ocf.start} to {ocf.end}",
            ingredients, ocf.period, "Derived by this tool from reported cash-flow facts.")

    def debt(self) -> ResearchValue:
        """A/B standard rules and C instrument classes compete on the most recent
        balance-sheet date (ties go to the standard rule). D, the extension-tag fallback,
        is used only when neither applies. Ambiguity at any step -> MISSING."""
        standard = self._standard_debt()
        instrument = self._instrument_debt()
        if isinstance(instrument, str):  # ambiguous instrument facts
            if standard is None:
                return missing_value("debt", f"Debt is ambiguous: {instrument}. Enter debt "
                                             "manually.", PROVIDER)
            instrument = None
        choice = None
        if standard and (not instrument or standard[0] >= instrument[0]):
            choice = standard
        elif instrument:
            choice = instrument
        if choice is not None:
            end, description, facts = choice
            return self._debt_value(end, description, facts)
        extension = self._extension_debt()
        if isinstance(extension, ResearchValue):
            return extension
        return missing_value("debt", extension or "No debt fact combination could be selected "
                                                  "without risking double counting. Enter debt "
                                                  "manually.", PROVIDER)

    def _debt_value(self, end: str, description: str, facts: list[Fact]) -> ResearchValue:
        if len(facts) == 1:
            value = self._from_fact("debt", facts[0])
            return replace(value, notes=f"{value.notes}. Debt rule: {description}.")
        ingredients = tuple(self._from_fact(f"debt_component:{f.concept}", f) for f in facts)
        total = sum((f.value for f in facts), Decimal(0))
        formula = " + ".join(f"{f.taxonomy}:{f.concept}" for f in facts)
        return self._derived("debt", total, f"Debt = {formula}, as of {end}", ingredients,
                             (None, end), f"Debt rule: {description}. Components do not overlap.")

    def _standard_debt(self):
        best = None
        for priority, (description, concepts) in enumerate(m.DEBT_RULES):
            by_date = [instant_facts_by_date(self.facts, m.US_GAAP, c, m.MONEY_UNIT)
                       for c in concepts]
            common = set.intersection(*(set(d) for d in by_date)) if by_date else set()
            if not common:
                continue
            end = max(common)
            key = (end, -priority)
            if best is None or key > best[0]:
                best = (key, description, [d[end] for d in by_date])
        if best is None:
            return None
        (end, _), description, facts = best
        return end, description, facts

    def _instrument_debt(self):
        """Debt-instrument classes at their latest common balance-sheet date. Returns
        (end, description, facts), None, or a string describing an ambiguity."""
        by_concept = {c: instant_facts_by_date(self.facts, m.US_GAAP, c, m.MONEY_UNIT)
                      for _, total, current, noncurrent in m.DEBT_INSTRUMENT_CLASSES
                      for c in (*total, *current, *noncurrent)}
        dates = set().union(*(set(d) for d in by_concept.values()))
        if not dates:
            return None
        end = max(dates)

        def first(concepts):
            return next((by_concept[c][end] for c in concepts if end in by_concept[c]), None)

        resolved: dict[str, list[Fact]] = {}
        for name, total, current, noncurrent in m.DEBT_INSTRUMENT_CLASSES:
            total_fact = first(total)
            if total_fact is not None:  # never add a class total to its own parts
                resolved[name] = [total_fact]
                continue
            parts = [f for f in (first(noncurrent), first(current)) if f is not None]
            if parts:
                resolved[name] = parts
        notes = [n for n in m.OVERLAPPING_NOTE_CLASSES if n in resolved]
        if m.GENERIC_NOTE_CLASS in resolved and notes:
            return (f"generic notes payable is reported alongside {' and '.join(notes)} as of "
                    f"{end}, and may include them")
        remark = ""
        if len(notes) == 2:
            totals = [sum((f.value for f in resolved[n]), Decimal(0)) for n in notes]
            if totals[0] != totals[1]:
                return (f"convertible and senior notes are both reported as of {end} with "
                        "different carrying amounts and may overlap")
            del resolved[notes[1]]
            remark = "; convertible and senior notes report the same carrying amount, counted once"
        facts = [f for name, *_ in m.DEBT_INSTRUMENT_CLASSES for f in resolved.get(name, [])]
        classes = ", ".join(name for name, *_ in m.DEBT_INSTRUMENT_CLASSES if name in resolved)
        return end, f"Debt instruments at carrying amount ({classes}){remark}", facts

    def _latest_balance_sheet_date(self) -> str | None:
        ends = [f.end for concept in self.facts.get("facts", {}).get(m.US_GAAP, {})
                for f in facts_for(self.facts, m.US_GAAP, concept, m.MONEY_UNIT) if is_instant(f)]
        return max(ends) if ends else None

    def _extension_debt(self):
        """Issuer-extension fallback. Returns a ResearchValue, or a reason string."""
        latest = self._latest_balance_sheet_date()
        if latest is None:
            return None
        name_ok = re.compile(m.EXTENSION_DEBT_NAME)
        label_ok = re.compile(m.EXTENSION_DEBT_LABEL, re.IGNORECASE)
        excluded = re.compile(m.EXTENSION_EXCLUDED, re.IGNORECASE)
        matches = []
        for taxonomy, concepts in self.facts.get("facts", {}).items():
            if taxonomy in m.STANDARD_TAXONOMIES:
                continue
            for concept, body in concepts.items():
                label = str(body.get("label") or "")
                if not (name_ok.search(concept) and label_ok.search(label)):
                    continue
                if excluded.search(concept) or excluded.search(label):
                    continue
                at_date = [f for f in facts_for(self.facts, taxonomy, concept, m.MONEY_UNIT)
                           if is_instant(f) and f.end == latest]
                if at_date:
                    matches.append((most_recently_filed(at_date), label))
        if not matches:
            return None
        if len(matches) > 1:
            names = ", ".join(f"{f.taxonomy}:{f.concept}" for f, _ in matches)
            return (f"Debt is ambiguous: several issuer-specific debt facts qualify as of "
                    f"{latest} ({names}). Enter debt manually.")
        fact, label = matches[0]
        value = self._from_fact("debt", fact)
        return replace(value, notes=(
            f"{value.notes}. Reported filing fact selected through SEC extension-tag fallback "
            f"(label: '{label}'). Predicates: monetary (USD), instant, latest balance-sheet date "
            f"{latest}, 10-K/10-Q, debt-obligation name and label, no fair-value / principal / "
            "cost terms."))

    def shares(self) -> ResearchValue:
        """Point-in-time shares outstanding: the most recent as-of date across the cover-page
        and balance-sheet concepts (latest filing breaks ties), not merely the first concept
        that has any fact. Weighted-average share counts are a different concept and are
        never used here."""
        fact = select_instant(self.facts, m.SHARES_OUTSTANDING, m.SHARES_UNIT)
        if fact is None:
            return missing_value("shares_outstanding",
                                 "No point-in-time shares-outstanding fact was found.", PROVIDER)
        value = self._from_fact("shares_outstanding", fact, shares=True)
        return replace(value, notes=(
            f"{value.notes}. Definition: {POINT_IN_TIME_SHARES}. The model accepts one share "
            "count: this is the most recent reported total; multiple share classes are not "
            "combined."))

    def latest_periodic_filing(self) -> str | None:
        """Filing date of the company's most recent 10-K/10-Q fact (reference date for the
        staleness rule; independent of the wall clock, so research is reproducible)."""
        filed = [e.get("filed") for concepts in self.facts.get("facts", {}).values()
                 for body in concepts.values() for entries in body.get("units", {}).values()
                 for e in entries if e.get("form") in INSTANT_FORMS and e.get("filed")]
        return max(filed) if filed else None

    def documents(self) -> tuple[SourceDocument, ...]:
        docs = {}
        for accn, fact in self.used.items():
            docs[accn] = SourceDocument(title=f"SEC filing {fact.form} filed {fact.filed}",
                                        url=self._filing_url(accn), report_type=fact.form,
                                        document_id=accn, filed_date=fact.filed)
        return tuple(sorted(docs.values(), key=lambda d: (d.filed_date or "", d.document_id or "")))
