"""Thin wrapper around the official google-genai SDK.

One GeminiClient instance is created by the caller and shared by every agent.
"""

from __future__ import annotations

import sys
import time
from typing import Any, Callable, Iterable

import httpx
from google import genai
from google.genai import errors, types

from config import GeminiSettings, unique_models

# HTTP status codes worth retrying: timeouts, rate limits, and server-side failures.
# Anything else (400 bad request, 401/403 auth, 404 unknown model, ...) is permanent,
# as is a 429 caused by an exhausted per-day quota (see _is_daily_quota_exhausted).
RETRYABLE_STATUS_CODES = frozenset({408, 429, 500, 502, 503, 504})


DAILY_QUOTA_MESSAGE_MARKERS = ("per day", "perday", "daily")


DEFAULT_REQUEST_TIMEOUT_SECONDS = 30.0


def build_http_options(timeout_seconds: float) -> types.HttpOptions:
    """SDK HTTP options: one attempt per request (no SDK retries) and a request timeout.

    Our GeminiClient is the only retry/fallback layer, so the SDK must never retry on
    its own. HttpOptions.timeout is in milliseconds.
    """
    if timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be > 0")
    return types.HttpOptions(
        timeout=int(timeout_seconds * 1000),
        retry_options=types.HttpRetryOptions(attempts=1),
    )


class LLMError(RuntimeError):
    """Raised when the LLM call fails permanently or after all retries."""


def _quota_ids(exc: errors.APIError) -> list[str]:
    """Extract quotaId values from a google.rpc.QuotaFailure in the error body, if present."""
    body = exc.details if isinstance(exc.details, dict) else {}
    error = body.get("error", body)
    details = error.get("details") if isinstance(error, dict) else None
    ids = []
    for detail in details if isinstance(details, list) else []:
        if not isinstance(detail, dict):
            continue
        for violation in detail.get("violations") or []:
            if isinstance(violation, dict) and violation.get("quotaId"):
                ids.append(str(violation["quotaId"]))
    return ids


def _is_daily_quota_exhausted(exc: Exception) -> bool:
    """True for a 429 caused by a per-day quota, which retrying within seconds cannot fix.

    Per-minute limits (e.g. GenerateRequestsPerMinutePerProjectPerModel-FreeTier) are
    NOT daily and remain retryable.
    """
    if not isinstance(exc, errors.APIError) or exc.code != 429:
        return False
    ids = _quota_ids(exc)
    if ids:
        return any("perday" in quota_id.lower() for quota_id in ids)
    message = (exc.message or "").lower()
    return any(marker in message for marker in DAILY_QUOTA_MESSAGE_MARKERS)


# After retries are exhausted, 503 means "this model is unavailable right now"
# (overloaded / high demand), so a different model may still succeed. Daily-quota
# 429s also trigger fallback (see _is_daily_quota_exhausted); short-term 429s and
# other 5xx errors do not.
FALLBACK_STATUS_CODES = frozenset({503})


def _is_model_unavailable(exc: Exception) -> bool:
    return isinstance(exc, errors.APIError) and exc.code in FALLBACK_STATUS_CODES


def _is_retryable(exc: Exception) -> bool:
    if _is_daily_quota_exhausted(exc):
        return False
    if isinstance(exc, errors.APIError):
        return exc.code in RETRYABLE_STATUS_CODES
    return isinstance(exc, httpx.TransportError)  # connection resets, timeouts, etc.


class ModelUnavailableError(LLMError):
    """A model-specific failure that another model may not share (overload, rate limit,
    daily quota). Raised internally to trigger fallback; never escapes generate()."""

    def __init__(self, model: str, reason: str) -> None:
        super().__init__(f"{model}: {reason}")
        self.model = model
        self.reason = reason


def _log_to_stderr(message: str) -> None:
    print(message, file=sys.stderr)


class GeminiClient:
    """Generates text from a system prompt and a user prompt, with retry/backoff and
    optional fallback to other models when the current one is unavailable."""

    def __init__(
        self,
        api_key: str,
        model: str,
        *,
        fallback_models: Iterable[str] = (),
        max_retries: int = 3,
        initial_delay: float = 1.0,
        request_timeout_seconds: float = DEFAULT_REQUEST_TIMEOUT_SECONDS,
        sdk_client: Any | None = None,
        sleep: Callable[[float], None] = time.sleep,
        log: Callable[[str], None] = _log_to_stderr,
    ) -> None:
        if max_retries < 0:
            raise ValueError("max_retries must be >= 0")
        self.models = unique_models([model, *fallback_models])
        if not self.models:
            raise ValueError("a primary model name is required")
        self.model = self.models[0]
        self.max_retries = max_retries
        self.initial_delay = initial_delay
        self.last_model_used: str | None = None
        self.http_options = build_http_options(request_timeout_seconds)
        self._client = (
            sdk_client
            if sdk_client is not None
            else genai.Client(api_key=api_key, http_options=self.http_options)
        )
        self._sleep = sleep
        self._log = log

    @classmethod
    def from_settings(cls, settings: GeminiSettings, **kwargs: Any) -> GeminiClient:
        return cls(
            api_key=settings.api_key,
            model=settings.model,
            fallback_models=settings.fallback_models,
            **kwargs,
        )

    def generate(self, system_prompt: str, user_prompt: str) -> str:
        """Call the model with `system_prompt` as the system instruction.

        Each model in self.models (primary first) is tried at most once. Within a
        model, transient failures are retried with exponential backoff. The next model
        is tried only if the current one is unavailable (see ModelUnavailableError);
        any other failure raises immediately.
        """
        config = types.GenerateContentConfig(
            system_instruction=system_prompt,
            # Agents pass no tools; make that explicit rather than relying on SDK defaults.
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
        )
        failures: list[str] = []

        for index, model in enumerate(self.models):
            if index > 0:
                self._log(f"Falling back to: {model}")
            try:
                text = self._generate_with_retries(model, config, user_prompt)
            except ModelUnavailableError as exc:
                label = "Primary" if index == 0 else "Fallback"
                self._log(f"{label} model unavailable: {model} ({exc.reason})")
                failures.append(str(exc))
                continue
            self.last_model_used = model
            if index > 0:
                self._log(f"Succeeded with fallback model: {model}")
            return text

        raise LLMError(
            f"All configured Gemini models are unavailable ({', '.join(self.models)}). "
            f"Failures: {'; '.join(failures)}. Wait for capacity or quota to recover, or "
            "set GEMINI_FALLBACK_MODELS to models with remaining capacity."
        )

    def _generate_with_retries(
        self, model: str, config: types.GenerateContentConfig, user_prompt: str
    ) -> str:
        attempts = self.max_retries + 1

        for attempt in range(1, attempts + 1):
            try:
                response = self._client.models.generate_content(
                    model=model, contents=user_prompt, config=config
                )
            except Exception as exc:
                if _is_daily_quota_exhausted(exc):
                    quota = ", ".join(_quota_ids(exc)) or "daily request quota"
                    raise ModelUnavailableError(
                        model, f"daily quota exhausted ({quota}); retrying will not help"
                    ) from exc
                if not _is_retryable(exc):
                    raise LLMError(f"Gemini request failed (not retryable): {exc}") from exc
                if attempt == attempts:
                    if _is_model_unavailable(exc):
                        raise ModelUnavailableError(
                            model, f"HTTP {exc.code} after {attempts} attempts"
                        ) from exc
                    raise LLMError(
                        f"Gemini request failed after {attempts} attempts: {exc}"
                    ) from exc
                self._sleep(self.initial_delay * 2 ** (attempt - 1))
                continue

            text = response.text
            if not text:
                raise LLMError(
                    "Gemini returned an empty response (it may have been blocked by safety filters)."
                )
            return text

        raise AssertionError("unreachable")
