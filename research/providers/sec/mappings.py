"""Deterministic US-GAAP / DEI concept mappings for the SEC provider.

Candidates are listed in priority order. No LLM ever selects a concept. If none of a
field's candidates yields a reliable fact, the field is MISSING.
"""

from __future__ import annotations

US_GAAP = "us-gaap"
DEI = "dei"

REVENUE = (
    (US_GAAP, "RevenueFromContractWithCustomerExcludingAssessedTax"),
    (US_GAAP, "Revenues"),
    (US_GAAP, "SalesRevenueNet"),
    (US_GAAP, "RevenueFromContractWithCustomerIncludingAssessedTax"),
)
NET_INCOME = ((US_GAAP, "NetIncomeLoss"),)  # attributable to the parent (not ProfitLoss)
# Cash and cash equivalents only: restricted cash is deliberately excluded.
CASH = ((US_GAAP, "CashAndCashEquivalentsAtCarryingValue"),)
OPERATING_CASH_FLOW = (
    (US_GAAP, "NetCashProvidedByUsedInOperatingActivities"),
    (US_GAAP, "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations"),
)
CAPEX = ((US_GAAP, "PaymentsToAcquirePropertyPlantAndEquipment"),)
OPERATING_INCOME = ((US_GAAP, "OperatingIncomeLoss"),)
DEPRECIATION_AMORTIZATION = (
    (US_GAAP, "DepreciationDepletionAndAmortization"),
    (US_GAAP, "DepreciationAndAmortization"),
    (US_GAAP, "DepreciationAmortizationAndAccretionNet"),
)
# EBITDA fallback C: separate depreciation and amortization of intangible assets, used
# only when no combined D&A fact exists for the operating-income period.
DEPRECIATION = ((US_GAAP, "Depreciation"),)
AMORTIZATION = ((US_GAAP, "AmortizationOfIntangibleAssets"),)
# Cover-page shares first, then balance-sheet shares. One class-level total only:
# multi-class share counts reported by class are not combined (model simplification).
SHARES_OUTSTANDING = (
    (DEI, "EntityCommonStockSharesOutstanding"),
    (US_GAAP, "CommonStockSharesOutstanding"),
)
# A reported EBITDA fact is used only under these exact concept names, in any taxonomy
# present in CompanyFacts. (Standard US-GAAP has no EBITDA concept, so it is normally
# derived.) "Adjusted EBITDA" is deliberately not accepted.
REPORTED_EBITDA_CONCEPTS = ("EBITDA", "EarningsBeforeInterestTaxesDepreciationAndAmortization")

# Debt: each rule is a set of NON-OVERLAPPING concepts whose sum is total debt. A rule
# applies only if every concept has a fact at the same balance-sheet date. Never mixed:
# DebtCurrent already contains LongTermDebtCurrent and ShortTermBorrowings, and
# LongTermDebt already contains its current maturities. Commercial paper is not added
# separately (it is normally within ShortTermBorrowings / DebtCurrent). Lease
# liabilities are excluded.
DEBT_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("Total debt (current and non-current)", ("DebtLongtermAndShorttermCombinedAmount",)),
    ("Long-term debt incl. current maturities + short-term borrowings",
     ("LongTermDebt", "ShortTermBorrowings")),
    ("Non-current long-term debt + all current debt", ("LongTermDebtNoncurrent", "DebtCurrent")),
    ("Non-current + current long-term debt + short-term borrowings",
     ("LongTermDebtNoncurrent", "LongTermDebtCurrent", "ShortTermBorrowings")),
    ("Non-current + current long-term debt", ("LongTermDebtNoncurrent", "LongTermDebtCurrent")),
    ("Long-term debt incl. current maturities (no short-term borrowings reported)",
     ("LongTermDebt",)),
)

# Debt fallback C: recognised debt-instrument classes, CARRYING amounts only. Fair-value
# (e.g. LongTermDebtFairValue, ConvertibleDebtFairValueDisclosures) and principal / face
# amounts (DebtInstrumentFaceAmount) are deliberately never mapped. Within a class the
# total concept is used if present, otherwise its current + non-current parts (never
# both). Overlap policy (see SECProvider): convertible and senior notes may describe the
# same obligation, so both are combined only if they are identical (then counted once);
# generic notes payable alongside a specific notes class is ambiguous; loans are a
# separate obligation class and may be added to notes.
DEBT_INSTRUMENT_CLASSES: tuple[tuple[str, tuple[str, ...], tuple[str, ...], tuple[str, ...]], ...] = (
    # (class, total concepts, current concepts, non-current concepts)
    ("convertible notes", ("ConvertibleNotesPayable",),
     ("ConvertibleNotesPayableCurrent", "ConvertibleDebtCurrent"),
     ("ConvertibleLongTermNotesPayable", "ConvertibleDebtNoncurrent")),
    ("senior notes", ("SeniorNotes",), ("SeniorNotesCurrent",), ("SeniorLongTermNotes",)),
    ("notes payable", ("NotesPayable",), ("NotesPayableCurrent",), ("LongTermNotesPayable",)),
    ("loans payable", ("LoansPayable",), ("LoansPayableCurrent",), ("LongTermLoansPayable",)),
)
OVERLAPPING_NOTE_CLASSES = ("convertible notes", "senior notes")
GENERIC_NOTE_CLASS = "notes payable"

# Debt fallback D: issuer-specific extension facts, only when no standard rule applies.
# Every predicate must hold: non-standard taxonomy, USD, instant, 10-K/10-Q, dated at the
# company's latest balance-sheet date, a debt-obligation name AND label, and none of the
# excluded terms. More than one qualifying concept is ambiguous -> MISSING.
STANDARD_TAXONOMIES = frozenset({"us-gaap", "dei", "srt", "ifrs-full", "invest"})
EXTENSION_DEBT_NAME = r"(Notes|Debt|Borrowings|TermLoans?|LoansPayable)"
EXTENSION_DEBT_LABEL = r"\b(notes|debt|borrowings?|term loans?|loans? payable)\b"
EXTENSION_EXCLUDED = (r"(fair ?value|principal|face ?amount|issuance|proceeds|repayment|interest|"
                      r"discount|premium|cost|expense|amortization|gain|loss|conversion|capped|"
                      r"hedge|derivative|rate|maturit|payment|capacity|available|remaining|"
                      r"commitment|covenant|receivable|asset|investment)")

MONEY_UNIT = "USD"
SHARES_UNIT = "shares"

LABELS = {
    "revenue": "Revenue", "ebitda": "EBITDA", "net_income": "Net income",
    "free_cash_flow": "Free cash flow", "cash": "Cash and cash equivalents", "debt": "Debt",
    "shares_outstanding": "Point-in-time shares outstanding",
}
