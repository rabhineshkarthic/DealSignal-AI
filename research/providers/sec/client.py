"""SEC EDGAR HTTP client with fair-access controls.

- Declared User-Agent from SEC_USER_AGENT (required for any uncached request).
- Request timeout, bounded retry with backoff on 429 / 5xx / network errors.
- Minimum interval between requests (well below SEC's published rate limit).
- Local JSON cache of raw responses (TTL-based), with force refresh.
Only this module (and the market-data provider) performs network access.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable

from research.errors import ProviderRequestError, ResearchConfigurationError

TICKERS_URL = "https://www.sec.gov/files/company_tickers_exchange.json"
SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik}.json"
COMPANY_FACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"
FILING_INDEX_URL = "https://www.sec.gov/Archives/edgar/data/{cik_int}/{accession_nodash}/"

DEFAULT_CACHE_DIR = Path(os.getenv("RESEARCH_CACHE_DIR", Path(__file__).resolve().parents[3]
                                   / ".research_cache")) / "sec"
RETRYABLE_HTTP = frozenset({429, 500, 502, 503, 504})

Fetch = Callable[[str, dict[str, str], float], bytes]


def urllib_fetch(url: str, headers: dict[str, str], timeout: float) -> bytes:
    request = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 (fixed SEC URLs)
        body = response.read()
        if response.headers.get("Content-Encoding") == "gzip":
            body = gzip.decompress(body)
        return body


def cik10(cik: str | int) -> str:
    return f"{int(cik):010d}"


class SECClient:
    def __init__(
        self,
        user_agent: str | None = None,
        *,
        cache_dir: Path | None = DEFAULT_CACHE_DIR,
        cache_ttl_seconds: float = 24 * 3600,
        fetch: Fetch = urllib_fetch,
        timeout: float = 20.0,
        max_retries: int = 2,
        min_interval: float = 0.25,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.user_agent = (user_agent if user_agent is not None
                           else os.getenv("SEC_USER_AGENT", "")).strip()
        self.cache_dir = Path(cache_dir) if cache_dir else None
        self.cache_ttl_seconds = cache_ttl_seconds
        self._fetch = fetch
        self.timeout = timeout
        self.max_retries = max_retries
        self.min_interval = min_interval
        self._sleep = sleep
        self._clock = clock
        self._last_request: float | None = None
        self._memory: dict[str, Any] = {}  # per-client memo: each URL fetched once
        self.requests_made = 0

    # --- public endpoints ---------------------------------------------------------------
    def company_tickers(self, *, force_refresh: bool = False) -> Any:
        return self.get_json(TICKERS_URL, force_refresh=force_refresh)

    def submissions(self, cik: str | int, *, force_refresh: bool = False) -> Any:
        return self.get_json(SUBMISSIONS_URL.format(cik=cik10(cik)), force_refresh=force_refresh)

    def company_facts(self, cik: str | int, *, force_refresh: bool = False) -> Any:
        return self.get_json(COMPANY_FACTS_URL.format(cik=cik10(cik)), force_refresh=force_refresh)

    # --- cache --------------------------------------------------------------------------
    def _cache_path(self, url: str) -> Path | None:
        if not self.cache_dir:
            return None
        return self.cache_dir / f"{hashlib.sha256(url.encode()).hexdigest()}.json"

    def _read_cache(self, url: str) -> Any | None:
        path = self._cache_path(url)
        if not path or not path.exists():
            return None
        entry = json.loads(path.read_text())
        if entry.get("url") != url or time.time() - entry.get("fetched_at", 0) > self.cache_ttl_seconds:
            return None
        return entry["body"]

    def _write_cache(self, url: str, body: Any) -> None:
        path = self._cache_path(url)
        if path:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps({"url": url, "fetched_at": time.time(), "body": body}))

    # --- requests -----------------------------------------------------------------------
    def get_json(self, url: str, *, force_refresh: bool = False) -> Any:
        if not force_refresh:
            if url in self._memory:
                return self._memory[url]
            cached = self._read_cache(url)
            if cached is not None:
                self._memory[url] = cached
                return cached
        if not self.user_agent:
            raise ResearchConfigurationError(
                "SEC_USER_AGENT is not set. SEC EDGAR requires a descriptive User-Agent with "
                'contact details, e.g. SEC_USER_AGENT="AI-MA-Deal-Team you@example.com". '
                "Add it to .env.")
        headers = {"User-Agent": self.user_agent, "Accept-Encoding": "gzip, deflate"}
        attempts = self.max_retries + 1
        for attempt in range(1, attempts + 1):
            self._respect_rate_limit()
            try:
                self.requests_made += 1
                body = json.loads(self._fetch(url, headers, self.timeout))
            except urllib.error.HTTPError as exc:
                if exc.code not in RETRYABLE_HTTP or attempt == attempts:
                    raise ProviderRequestError(
                        f"SEC request failed with HTTP {exc.code}: {url}") from exc
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                if attempt == attempts:
                    raise ProviderRequestError(f"SEC request failed: {exc}") from exc
            except json.JSONDecodeError as exc:
                raise ProviderRequestError(f"SEC returned invalid JSON: {url}") from exc
            else:
                self._write_cache(url, body)
                self._memory[url] = body
                return body
            self._sleep(2.0 ** (attempt - 1))
        raise AssertionError("unreachable")

    def _respect_rate_limit(self) -> None:
        now = self._clock()
        if self._last_request is not None:
            wait = self.min_interval - (now - self._last_request)
            if wait > 0:
                self._sleep(wait)
                now = self._clock()
        self._last_request = now
