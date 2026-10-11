"""Minimal evaluator for the restricted Excel formula subset the exporter writes.

Supported: numbers, "strings", cell references (B5, $B$5), sheet-qualified references
('Sheet Name'!B5, Sheet!B5), ranges (B5:B9) inside functions, operators + - * / ^,
comparisons = <> < <= > >=, unary minus, parentheses, and SUM, MIN, MAX, AVERAGE,
ABS, IF.

Used to check exported formulas against the deterministic Python engine without
Excel or LibreOffice. Anything outside the subset raises FormulaError, so a broken
reference, unknown sheet, or unsupported function cannot pass silently.
"""

from __future__ import annotations

import re
from typing import Any, Callable

from openpyxl.utils.cell import column_index_from_string, get_column_letter


class FormulaError(ValueError):
    """Unparseable formula, unsupported function, or broken reference."""


class EvaluationError(FormulaError):
    """A well-formed formula that fails when evaluated (e.g. #DIV/0!, empty cell)."""


_TOKEN = re.compile(r"""
    \s*(?:
      (?P<string>"(?:[^"]|"")*")
    | (?P<ref>(?:(?:'(?:[^']|'')+'|[A-Za-z_][A-Za-z0-9_.]*)!)?\$?[A-Z]{1,3}\$?\d+
              (?::\$?[A-Z]{1,3}\$?\d+)?)
    | (?P<number>\d+(?:\.\d+)?(?:[Ee][+-]?\d+)?)
    | (?P<func>[A-Z]+)\(
    | (?P<op><=|>=|<>|[-+*/^(),=<>])
    )""", re.VERBOSE)

_REF_PARTS = re.compile(r"^(?:(?P<sheet>'(?:[^']|'')+'|[A-Za-z_][A-Za-z0-9_.]*)!)?"
                        r"\$?(?P<c1>[A-Z]{1,3})\$?(?P<r1>\d+)(?::\$?(?P<c2>[A-Z]{1,3})\$?(?P<r2>\d+))?$")

FUNCTIONS = frozenset({"SUM", "MIN", "MAX", "AVERAGE", "ABS", "IF"})


def tokenize(formula: str) -> list[tuple[str, str]]:
    text = formula[1:] if formula.startswith("=") else formula
    tokens, pos = [], 0
    while pos < len(text):
        if text[pos:].strip() == "":
            break
        match = _TOKEN.match(text, pos)
        if not match or match.end() == pos:
            raise FormulaError(f"cannot parse {formula!r} at position {pos}")
        kind = match.lastgroup
        tokens.append((kind, match.group(kind)))
        pos = match.end()
    return tokens


def parse_ref(ref: str, current_sheet: str) -> tuple[str, list[str]]:
    """Return (sheet, [cell coordinates]) for a single cell or a range."""
    m = _REF_PARTS.match(ref)
    if not m:
        raise FormulaError(f"bad reference {ref!r}")
    sheet = m.group("sheet")
    if sheet is None:
        sheet = current_sheet
    elif sheet.startswith("'"):
        sheet = sheet[1:-1].replace("''", "'")
    c1, r1 = column_index_from_string(m.group("c1")), int(m.group("r1"))
    if m.group("c2") is None:
        return sheet, [f"{get_column_letter(c1)}{r1}"]
    c2, r2 = column_index_from_string(m.group("c2")), int(m.group("r2"))
    return sheet, [f"{get_column_letter(c)}{r}"
                   for r in range(min(r1, r2), max(r1, r2) + 1)
                   for c in range(min(c1, c2), max(c1, c2) + 1)]


class _Parser:
    def __init__(self, tokens, sheet: str, resolve: Callable[[str, str], Any]):
        self.tokens, self.pos, self.sheet, self.resolve = tokens, 0, sheet, resolve

    def peek(self):
        return self.tokens[self.pos] if self.pos < len(self.tokens) else (None, None)

    def take(self, value=None):
        kind, text = self.peek()
        if kind is None or (value is not None and text != value):
            raise FormulaError(f"expected {value!r}, got {text!r}")
        self.pos += 1
        return kind, text

    def parse(self):
        value = self.comparison()
        if self.pos != len(self.tokens):
            raise FormulaError(f"unexpected token {self.peek()[1]!r}")
        return value

    def comparison(self):
        left = self.additive()
        while self.peek()[1] in ("=", "<>", "<", "<=", ">", ">="):
            op = self.take()[1]
            right = self.additive()
            left = {"=": left == right, "<>": left != right, "<": left < right,
                    "<=": left <= right, ">": left > right, ">=": left >= right}[op]
        return left

    def additive(self):
        left = self.term()
        while self.peek()[1] in ("+", "-"):
            op = self.take()[1]
            right = self.term()
            left = left + right if op == "+" else left - right
        return left

    def term(self):
        left = self.power()
        while self.peek()[1] in ("*", "/"):
            op = self.take()[1]
            right = self.power()
            if op == "/" and right == 0:
                raise EvaluationError("division by zero (#DIV/0!)")
            left = left * right if op == "*" else left / right
        return left

    def power(self):
        base = self.unary()
        if self.peek()[1] == "^":
            self.take("^")
            return base ** self.unary()
        return base

    def unary(self):
        if self.peek()[1] == "-":
            self.take("-")
            return -self.unary()
        if self.peek()[1] == "+":
            self.take("+")
            return self.unary()
        return self.atom()

    def atom(self):
        kind, text = self.peek()
        if kind == "number":
            self.take()
            return float(text)
        if kind == "string":
            self.take()
            return text[1:-1].replace('""', '"')
        if kind == "ref":
            self.take()
            sheet, cells = parse_ref(text, self.sheet)
            if len(cells) != 1:
                raise FormulaError(f"range {text!r} used outside a function")
            return self.resolve(sheet, cells[0])
        if kind == "func":
            return self.function()
        if text == "(":
            self.take("(")
            value = self.comparison()
            self.take(")")
            return value
        raise FormulaError(f"unexpected token {text!r}")

    def argument(self) -> list[Any]:
        kind, text = self.peek()
        if kind == "ref" and ":" in text:
            self.take()
            sheet, cells = parse_ref(text, self.sheet)
            return [self.resolve(sheet, c) for c in cells]
        return [self.comparison()]

    def lazy_if(self):
        """IF evaluates only the chosen branch, as Excel does, so a guarded division
        by zero in the untaken branch is not an error."""
        condition = self.comparison()
        branches = []
        for _ in range(2):
            self.take(",")
            start = self.pos
            try:
                branches.append((self.comparison(), None))
            except EvaluationError as exc:
                self.pos = start  # rewind, then skip the whole branch argument
                self._skip_argument()
                branches.append((None, exc))
        self.take(")")
        value, error = branches[0] if condition else branches[1]
        if error is not None:
            raise error
        return value

    def _skip_argument(self):
        depth = 0
        while True:
            kind, text = self.peek()
            if kind is None:
                raise FormulaError("unterminated IF")
            if depth == 0 and text in (",", ")"):
                return
            if kind == "func" or text == "(":
                depth += 1
            elif text == ")":
                depth -= 1
            self.pos += 1

    def function(self):
        name = self.take()[1]
        if name not in FUNCTIONS:
            raise FormulaError(f"unsupported function {name}")
        if name == "IF":
            return self.lazy_if()
        args: list[list[Any]] = []
        if self.peek()[1] != ")":
            args.append(self.argument())
            while self.peek()[1] == ",":
                self.take(",")
                args.append(self.argument())
        self.take(")")
        flat = [v for arg in args for v in arg]
        if name == "SUM":
            return sum(flat)
        if name == "MIN":
            return min(flat)
        if name == "MAX":
            return max(flat)
        if name == "AVERAGE":
            return sum(flat) / len(flat)
        if name == "ABS":
            (value,) = flat
            return abs(value)
        raise AssertionError("unreachable")


class WorkbookEvaluator:
    """Evaluates every formula in an openpyxl workbook, following references."""

    def __init__(self, workbook):
        self.workbook = workbook
        self.cache: dict[tuple[str, str], Any] = {}
        self.stack: set[tuple[str, str]] = set()

    def value(self, sheet: str, coordinate: str) -> Any:
        key = (sheet, coordinate)
        if key in self.cache:
            return self.cache[key]
        if sheet not in self.workbook.sheetnames:
            raise FormulaError(f"reference to missing sheet {sheet!r} (#REF!)")
        if key in self.stack:
            raise FormulaError(f"circular reference at {sheet}!{coordinate}")
        cell = self.workbook[sheet][coordinate]
        raw = cell.value
        if cell.data_type == "f":
            if "#REF!" in raw:
                raise FormulaError(f"{sheet}!{coordinate} contains #REF!")
            self.stack.add(key)
            try:
                result = _Parser(tokenize(raw), sheet, self.value).parse()
            finally:
                self.stack.discard(key)
        else:
            if raw is None:
                raise EvaluationError(f"reference to empty cell {sheet}!{coordinate}")
            result = raw
        self.cache[key] = result
        return result

    def evaluate_all(self) -> dict[tuple[str, str], Any]:
        """Evaluate every formula cell; raises FormulaError on the first problem."""
        results = {}
        for ws in self.workbook.worksheets:
            for row in ws.iter_rows():
                for cell in row:
                    if cell.data_type == "f":
                        results[(ws.title, cell.coordinate)] = self.value(ws.title, cell.coordinate)
        return results
