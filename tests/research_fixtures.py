"""Shared offline fixtures for research tests: a fake SEC fetch, a CompanyFacts builder,
and a fake market-price fetch. Nothing here touches the network."""

from __future__ import annotations

import json
import urllib.error
from pathlib import Path

from research.providers.sec import SECClient, SECProvider
from research.providers.sec.client import COMPANY_FACTS_URL, SUBMISSIONS_URL, TICKERS_URL

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "sec"
UA = "AI-MA-Deal-Team-tests tests@example.invalid"


def fixture_routes() -> dict[str, Path]:
    routes = {TICKERS_URL: FIXTURES / "company_tickers_exchange.json"}
    for cik in ("0000000101", "0000000202"):
        routes[SUBMISSIONS_URL.format(cik=cik)] = FIXTURES / f"submissions_{cik}.json"
        routes[COMPANY_FACTS_URL.format(cik=cik)] = FIXTURES / f"companyfacts_{cik}.json"
    return routes


class FakeSECFetch:
    """Serves fixture files by URL; records every request and its headers."""

    def __init__(self, overrides: dict[str, object] | None = None):
        self.routes = fixture_routes()
        self.overrides = overrides or {}
        self.calls: list[tuple[str, dict]] = []

    def __call__(self, url, headers, timeout):
        self.calls.append((url, dict(headers)))
        if url in self.overrides:
            body = self.overrides[url]
            if isinstance(body, Exception):
                raise body
            return json.dumps(body).encode()
        if url not in self.routes:
            raise urllib.error.HTTPError(url, 404, "Not Found", {}, None)
        return self.routes[url].read_bytes()


def sec_provider(fetch=None, cache_dir=None, user_agent=UA) -> SECProvider:
    client = SECClient(user_agent, cache_dir=cache_dir, fetch=fetch or FakeSECFetch(),
                       sleep=lambda s: None)
    return SECProvider(client)


# --- CompanyFacts builder for rule-level tests ---------------------------------------------------

def annual(start, end, val, filed, accn="0000000999-24-000001", form="10-K", fp="FY"):
    return {"start": start, "end": end, "val": val, "accn": accn, "fy": int(filed[:4]),
            "fp": fp, "form": form, "filed": filed}


def instant(end, val, filed, accn="0000000999-24-000001", form="10-Q", fp="Q1"):
    return {"end": end, "val": val, "accn": accn, "fy": int(filed[:4]), "fp": fp, "form": form,
            "filed": filed}


def company_facts(us_gaap: dict | None = None, dei: dict | None = None, **other_taxonomies):
    facts = {"us-gaap": {c: {"units": {"USD": e}} for c, e in (us_gaap or {}).items()}}
    if dei:
        facts["dei"] = {c: {"units": {"shares": e}} for c, e in dei.items()}
    for taxonomy, concepts in other_taxonomies.items():
        facts[taxonomy.replace("_", "-")] = {c: {"units": {"USD": e}} for c, e in concepts.items()}
    return {"cik": 999, "entityName": "Test Co", "facts": facts}


class FakeMarketFetch:
    def __init__(self, body: str | Exception = "Symbol,Date,Time,Close\nALPH.US,2025-09-26,22:00:08,412.37\n"):
        self.body, self.calls = body, []

    def __call__(self, url, timeout):
        self.calls.append(url)
        if isinstance(self.body, Exception):
            raise self.body
        return self.body.encode()
