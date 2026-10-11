"""Phase 8.1 SEC fallbacks: EBITDA from separate D + A, debt instruments, and the
extension-tag fallback. Fictional fixtures in CompanyFacts shape; no network."""

import json
import socket
from pathlib import Path

import pytest

from research.models import CompanyCandidate, ResearchStatus
from research.providers.sec.client import COMPANY_FACTS_URL, SUBMISSIONS_URL
from research_fixtures import FakeSECFetch, annual, company_facts, instant, sec_provider

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "sec"
P = ("2025-01-01", "2025-12-31")


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def blocked(*args, **kwargs):
        raise AssertionError("real network access attempted")

    monkeypatch.setattr(socket.socket, "connect", blocked)
    monkeypatch.setattr("urllib.request.urlopen", blocked)


def research(facts: dict, cik="0000000999"):
    fetch = FakeSECFetch(overrides={SUBMISSIONS_URL.format(cik=cik): {"name": "TEST CO"},
                                    COMPANY_FACTS_URL.format(cik=cik): facts})
    return sec_provider(fetch).research_company(
        CompanyCandidate(cik, "TEST CO", "TST", "NYSE", "US", "CIK")).fields


def fixture(cik):
    return research(json.loads((FIXTURES / f"companyfacts_{cik}.json").read_text()), cik)


def fy(concept, value, period=P, filed="2026-02-01"):
    return {concept: [annual(period[0], period[1], value, filed)]}


# --- Oracle-like EBITDA --------------------------------------------------------------------------

def test_oracle_like_ebitda_from_separate_depreciation_and_amortization():
    ebitda = fixture("0000000404")["ebitda"]
    assert ebitda.status == ResearchStatus.DERIVED
    assert ebitda.normalized_value == 29700.0            # 21,200 + 6,400 + 2,100
    assert [i.concept for i in ebitda.ingredients] == [
        "OperatingIncomeLoss", "Depreciation", "AmortizationOfIntangibleAssets"]
    assert [i.field_name for i in ebitda.ingredients] == ["operating_income", "depreciation",
                                                          "amortization"]
    assert ebitda.derivation == (
        "EBITDA = operating income (us-gaap:OperatingIncomeLoss) + depreciation "
        "(us-gaap:Depreciation) + amortization (us-gaap:AmortizationOfIntangibleAssets), "
        "period 2025-06-01 to 2026-05-31")
    assert "not company-reported EBITDA" in ebitda.notes
    assert all(i.period_end == "2026-05-31" and i.source_document_id for i in ebitda.ingredients)


def test_combined_da_preferred_over_separate_components():
    fields = research(company_facts({**fy("OperatingIncomeLoss", 100), **fy("DepreciationDepletionAndAmortization", 30),
                                     **fy("Depreciation", 20), **fy("AmortizationOfIntangibleAssets", 5)}))
    ebitda = fields["ebitda"]
    assert ebitda.raw_value == "130" and len(ebitda.ingredients) == 2
    assert ebitda.ingredients[1].concept == "DepreciationDepletionAndAmortization"


def test_mismatched_component_period_rejected():
    fields = research(company_facts({**fy("OperatingIncomeLoss", 100), **fy("Depreciation", 20),
                                     **fy("AmortizationOfIntangibleAssets", 5,
                                          period=("2024-01-01", "2024-12-31"))}))
    assert fields["ebitda"].status == ResearchStatus.MISSING
    assert "separate amortization fact" in fields["ebitda"].notes


@pytest.mark.parametrize("present, absent", [("Depreciation", "amortization"),
                                              ("AmortizationOfIntangibleAssets", "depreciation")])
def test_missing_one_component_is_missing(present, absent):
    fields = research(company_facts({**fy("OperatingIncomeLoss", 100), **fy(present, 20)}))
    assert fields["ebitda"].status == ResearchStatus.MISSING
    assert f"separate {absent} fact" in fields["ebitda"].notes
    assert "Enter EBITDA manually" in fields["ebitda"].notes


# --- Snowflake-like debt ---------------------------------------------------------------------------

def test_snowflake_like_convertible_notes_carrying_value():
    debt = fixture("0000000505")["debt"]
    assert debt.status == ResearchStatus.REPORTED
    assert debt.concept == "ConvertibleNotesPayable" and debt.normalized_value == 2268.0
    assert debt.period_end == "2026-04-30"
    assert "Debt instruments at carrying amount (convertible notes)" in debt.notes


def test_fair_value_and_principal_never_used_as_debt():
    only_non_carrying = {
        "ConvertibleDebtFairValueDisclosures": [instant("2026-04-30", 2710, "2026-05-30")],
        "LongTermDebtFairValue": [instant("2026-04-30", 2710, "2026-05-30")],
        "DebtInstrumentFaceAmount": [instant("2026-04-30", 2300, "2026-05-30")],
    }
    assert research(company_facts(only_non_carrying))["debt"].status == ResearchStatus.MISSING


D = "2026-04-30"


def debt(us_gaap):
    return research(company_facts(us_gaap))["debt"]


def test_class_total_not_added_to_its_components():
    value = debt({"ConvertibleNotesPayable": [instant(D, 1000, "2026-05-30")],
                  "ConvertibleNotesPayableCurrent": [instant(D, 200, "2026-05-30")],
                  "ConvertibleLongTermNotesPayable": [instant(D, 800, "2026-05-30")]})
    assert value.raw_value == "1000" and value.status == ResearchStatus.REPORTED


def test_current_and_noncurrent_parts_combined_when_no_total():
    value = debt({"ConvertibleNotesPayableCurrent": [instant(D, 200, "2026-05-30")],
                  "ConvertibleLongTermNotesPayable": [instant(D, 800, "2026-05-30")]})
    assert value.status == ResearchStatus.DERIVED and value.raw_value == "1000"
    assert [i.concept for i in value.ingredients] == ["ConvertibleLongTermNotesPayable",
                                                      "ConvertibleNotesPayableCurrent"]


def test_distinct_obligations_combined():
    value = debt({"ConvertibleNotesPayable": [instant(D, 1000, "2026-05-30")],
                  "LongTermLoansPayable": [instant(D, 300, "2026-05-30")]})
    assert value.raw_value == "1300"
    assert [i.concept for i in value.ingredients] == ["ConvertibleNotesPayable", "LongTermLoansPayable"]


def test_same_obligation_reported_twice_counted_once():
    value = debt({"ConvertibleNotesPayable": [instant(D, 1000, "2026-05-30")],
                  "SeniorNotes": [instant(D, 1000, "2026-05-30")]})
    assert value.raw_value == "1000" and "counted once" in value.notes


def test_possibly_overlapping_notes_are_ambiguous():
    value = debt({"ConvertibleNotesPayable": [instant(D, 1000, "2026-05-30")],
                  "SeniorNotes": [instant(D, 1500, "2026-05-30")]})
    assert value.status == ResearchStatus.MISSING and "may overlap" in value.notes


def test_generic_notes_alongside_specific_notes_is_ambiguous():
    value = debt({"ConvertibleNotesPayable": [instant(D, 1000, "2026-05-30")],
                  "NotesPayable": [instant(D, 1000, "2026-05-30")]})
    assert value.status == ResearchStatus.MISSING and "may include them" in value.notes


def test_standard_total_preferred_on_the_same_date_no_double_count():
    value = debt({"LongTermDebt": [instant(D, 2268, "2026-05-30")],
                  "ShortTermBorrowings": [instant(D, 0, "2026-05-30")],
                  "ConvertibleNotesPayable": [instant(D, 2268, "2026-05-30")]})
    assert value.raw_value == "2268"
    assert [i.concept for i in value.ingredients] == ["LongTermDebt", "ShortTermBorrowings"]


def test_stale_standard_debt_loses_to_more_recent_instrument_facts():
    value = debt({"LongTermDebt": [instant("2023-01-31", 900, "2023-03-20", form="10-K", fp="FY")],
                  "ConvertibleNotesPayable": [instant(D, 2268, "2026-05-30")]})
    assert value.raw_value == "2268" and value.period_end == D


# --- Extension-tag fallback -------------------------------------------------------------------------

def ext_facts(*extension_concepts, cash_date=D):
    facts = company_facts({"CashAndCashEquivalentsAtCarryingValue": [instant(cash_date, 50, "2026-05-30")]})
    facts["facts"]["snow"] = {name: {"label": label, "units": {unit: entries}}
                              for name, label, unit, entries in extension_concepts}
    return facts


def ext(name, label, value=2268, end=D, unit="USD", start=None, form="10-Q"):
    entry = instant(end, value, "2026-05-30", form=form)
    if start:
        entry["start"] = start
    return (name, label, unit, [entry])


def test_single_qualifying_extension_fact_is_used_with_visible_note():
    debt_value = research(ext_facts(ext("ConvertibleSeniorNotesNet", "Convertible senior notes, net")))["debt"]
    assert debt_value.status == ResearchStatus.REPORTED
    assert (debt_value.taxonomy, debt_value.concept, debt_value.raw_value) == (
        "snow", "ConvertibleSeniorNotesNet", "2268")
    assert "Reported filing fact selected through SEC extension-tag fallback" in debt_value.notes
    assert "label: 'Convertible senior notes, net'" in debt_value.notes


@pytest.mark.parametrize("concept", [
    ext("ConvertibleSeniorNotesFairValue", "Convertible senior notes, fair value"),
    ext("ConvertibleSeniorNotesPrincipal", "Convertible senior notes, principal amount"),
    ext("ConvertibleSeniorNotesNet", "Convertible senior notes, net", end="2026-01-31"),   # stale
    ext("ConvertibleSeniorNotesNet", "Convertible senior notes, net", start="2025-05-01"),  # duration
    ext("ConvertibleSeniorNotesNet", "Convertible senior notes, net", unit="EUR"),         # non-USD
    ext("ConvertibleSeniorNotesNet", "Convertible senior notes, net", form="8-K"),         # filing
    ext("DeferredRevenueCurrent", "Deferred revenue"),                                     # not debt
    ext("OtherLiabilities", "Convertible senior notes"),                                    # name fails
    ext("DebtIssuanceCostsNet", "Debt issuance costs"),                                     # excluded term
])
def test_extension_predicates_reject(concept):
    assert research(ext_facts(concept))["debt"].status == ResearchStatus.MISSING


def test_ambiguous_extension_tags_are_missing():
    value = research(ext_facts(ext("ConvertibleSeniorNotes2027Net", "2027 convertible senior notes, net"),
                               ext("ConvertibleSeniorNotes2029Net", "2029 convertible senior notes, net",
                                   value=1000)))["debt"]
    assert value.status == ResearchStatus.MISSING and "ambiguous" in value.notes


def test_extension_fallback_not_used_when_standard_debt_exists():
    facts = ext_facts(ext("ConvertibleSeniorNotesNet", "Convertible senior notes, net"))
    facts["facts"]["us-gaap"]["ConvertibleNotesPayable"] = {"units": {"USD": [instant(D, 2268, "2026-05-30")]}}
    value = research(facts)["debt"]
    assert value.taxonomy == "us-gaap" and "extension" not in (value.notes or "")


def test_previous_conservative_cases_still_missing():
    assert research(company_facts({"LongTermDebtNoncurrent": [instant(D, 700, "2026-05-30")]}))[
        "debt"].status == ResearchStatus.MISSING
    assert research(company_facts({**fy("OperatingIncomeLoss", 100)}))["ebitda"].status == \
        ResearchStatus.MISSING
