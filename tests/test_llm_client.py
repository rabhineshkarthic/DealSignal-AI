"""Config and Gemini client tests. The SDK is replaced by a fake; nothing hits the network."""

from types import SimpleNamespace

import httpx
import pytest
from google.genai import errors

from config import DEFAULT_GEMINI_MODEL, ConfigError, GeminiSettings, load_settings
import llm_client
from llm_client import (
    DEFAULT_REQUEST_TIMEOUT_SECONDS,
    GeminiClient,
    LLMError,
    build_http_options,
)

SECRET = "test-secret-key-123"


# --- config -------------------------------------------------------------------

@pytest.fixture
def clean_env(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_MODEL", raising=False)
    monkeypatch.delenv("GEMINI_FALLBACK_MODELS", raising=False)


def test_missing_api_key_raises_clear_error(clean_env):
    with pytest.raises(ConfigError, match="GEMINI_API_KEY is not set"):
        load_settings(env_file=None)


def test_placeholder_api_key_rejected(clean_env, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "your_api_key_here")
    with pytest.raises(ConfigError, match="placeholder"):
        load_settings(env_file=None)


def test_model_defaults(clean_env, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", SECRET)
    settings = load_settings(env_file=None)
    assert settings.model == DEFAULT_GEMINI_MODEL == "gemini-3.8-flash"


def test_model_override(clean_env, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", SECRET)
    monkeypatch.setenv("GEMINI_MODEL", "some-other-model")
    assert load_settings(env_file=None).model == "some-other-model"


def test_settings_loaded_from_env_file(clean_env, tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text(f"GEMINI_API_KEY={SECRET}\n")
    assert load_settings(env_file=env_file).api_key == SECRET


def test_api_key_not_in_repr():
    settings = GeminiSettings(api_key=SECRET, model="m")
    assert SECRET not in repr(settings)
    assert SECRET not in str(settings)


# --- GeminiClient -------------------------------------------------------------

class FakeModels:
    """Stands in for sdk_client.models; replays a scripted list of results/exceptions."""

    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.calls = []

    def generate_content(self, *, model, contents, config):
        self.calls.append({"model": model, "contents": contents, "config": config})
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return SimpleNamespace(text=outcome)


def make_client(outcomes, logs=None, **kwargs):
    models = FakeModels(outcomes)
    delays = []
    kwargs.setdefault("model", "test-model")
    client = GeminiClient(
        api_key=SECRET,
        sdk_client=SimpleNamespace(models=models),
        sleep=delays.append,
        log=(logs if logs is not None else []).append,
        **kwargs,
    )
    return client, models, delays


def api_error(code):
    cls = errors.ClientError if code < 500 else errors.ServerError
    return cls(code, {"error": {"code": code, "message": f"HTTP {code}", "status": "X"}})


def test_generate_uses_system_instruction_not_concatenation():
    client, models, _ = make_client(["hello"])
    assert client.generate("SYSTEM RULES", "USER QUESTION") == "hello"

    call = models.calls[0]
    assert call["model"] == "test-model"
    assert call["contents"] == "USER QUESTION"
    assert call["config"].system_instruction == "SYSTEM RULES"
    assert call["config"].automatic_function_calling.disable is True


@pytest.mark.parametrize("transient", [429, 500, 503])
def test_retries_transient_api_errors_with_exponential_delay(transient):
    client, models, delays = make_client(
        [api_error(transient), api_error(transient), "recovered"], initial_delay=1.0
    )
    assert client.generate("s", "u") == "recovered"
    assert len(models.calls) == 3
    assert delays == [1.0, 2.0]


def test_retries_network_errors():
    client, _, delays = make_client([httpx.ConnectError("reset"), "ok"])
    assert client.generate("s", "u") == "ok"
    assert len(delays) == 1


def test_gives_up_after_max_retries():
    client, models, delays = make_client([api_error(429)] * 4, max_retries=3)
    with pytest.raises(LLMError, match="after 4 attempts"):
        client.generate("s", "u")
    assert len(models.calls) == 4
    assert delays == [1.0, 2.0, 4.0]


@pytest.mark.parametrize("permanent", [400, 401, 403, 404])
def test_permanent_errors_are_not_retried(permanent):
    client, models, delays = make_client([api_error(permanent), "never reached"])
    with pytest.raises(LLMError, match="not retryable"):
        client.generate("s", "u")
    assert len(models.calls) == 1
    assert delays == []


def test_empty_response_raises():
    client, _, _ = make_client([None])
    with pytest.raises(LLMError, match="empty response"):
        client.generate("s", "u")


def test_errors_do_not_leak_api_key():
    client, _, _ = make_client([api_error(401)])
    with pytest.raises(LLMError) as exc_info:
        client.generate("s", "u")
    assert SECRET not in str(exc_info.value)


# --- 429 quota classification -------------------------------------------------

def quota_error(quota_id=None, message="You exceeded your current quota."):
    """A 429 shaped like Gemini's RESOURCE_EXHAUSTED body, optionally with a QuotaFailure."""
    details = []
    if quota_id:
        details.append({
            "@type": "type.googleapis.com/google.rpc.QuotaFailure",
            "violations": [{
                "quotaMetric": "generativelanguage.googleapis.com/generate_content_free_tier_requests",
                "quotaId": quota_id,
            }],
        })
        details.append({"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": "30s"})
    body = {"error": {"code": 429, "message": message, "status": "RESOURCE_EXHAUSTED",
                      "details": details}}
    return errors.ClientError(429, body)


DAILY_QUOTA_ID = "GenerateRequestsPerDayPerProjectPerModel-FreeTier"
MINUTE_QUOTA_ID = "GenerateRequestsPerMinutePerProjectPerModel-FreeTier"


def test_daily_quota_fails_immediately_without_retry():
    client, models, delays = make_client([quota_error(DAILY_QUOTA_ID), "never reached"])
    with pytest.raises(LLMError, match="daily quota exhausted") as exc_info:
        client.generate("s", "u")
    assert len(models.calls) == 1
    assert delays == []
    assert DAILY_QUOTA_ID in str(exc_info.value)
    assert "test-model" in str(exc_info.value)


def test_daily_quota_detected_from_message_when_no_quota_details():
    err = quota_error(message="Quota exceeded: requests per day limit reached for this model.")
    client, models, delays = make_client([err, "never reached"])
    with pytest.raises(LLMError, match="daily quota exhausted"):
        client.generate("s", "u")
    assert len(models.calls) == 1
    assert delays == []


def test_per_minute_quota_is_still_retried():
    client, models, delays = make_client([quota_error(MINUTE_QUOTA_ID), "ok"])
    assert client.generate("s", "u") == "ok"
    assert len(models.calls) == 2
    assert delays == [1.0]


def test_mixed_minute_and_daily_violations_fail_immediately():
    err = quota_error(MINUTE_QUOTA_ID)
    err.details["error"]["details"][0]["violations"].append({"quotaId": DAILY_QUOTA_ID})
    client, models, _ = make_client([err, "never reached"])
    with pytest.raises(LLMError, match="daily quota exhausted"):
        client.generate("s", "u")
    assert len(models.calls) == 1


def test_generic_429_without_daily_signal_is_retried():
    client, models, _ = make_client([quota_error(), "ok"])
    assert client.generate("s", "u") == "ok"
    assert len(models.calls) == 2


def test_daily_quota_error_does_not_leak_api_key():
    client, _, _ = make_client([quota_error(DAILY_QUOTA_ID)])
    with pytest.raises(LLMError) as exc_info:
        client.generate("s", "u")
    assert SECRET not in str(exc_info.value)


# --- Fallback configuration ---------------------------------------------------

def test_fallback_models_default_to_empty(clean_env, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", SECRET)
    assert load_settings(env_file=None).fallback_models == ()


def test_fallback_models_parsed_trimmed_and_deduplicated(clean_env, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", SECRET)
    monkeypatch.setenv("GEMINI_MODEL", "primary")
    monkeypatch.setenv("GEMINI_FALLBACK_MODELS", " fb-a , fb-b,,fb-a, primary ,fb-c ")
    settings = load_settings(env_file=None)
    assert settings.model == "primary"
    assert settings.fallback_models == ("fb-a", "fb-b", "fb-c")


def test_fallback_models_reject_names_with_spaces(clean_env, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", SECRET)
    monkeypatch.setenv("GEMINI_FALLBACK_MODELS", "fb-a,fb b")
    with pytest.raises(ConfigError, match="invalid model names"):
        load_settings(env_file=None)


def test_fallback_settings_repr_hides_key():
    settings = GeminiSettings(api_key=SECRET, model="m", fallback_models=("f",))
    assert SECRET not in repr(settings)


# --- Fallback behaviour -------------------------------------------------------

def fallback_client(outcomes, fallbacks=("fb-1", "fb-2"), **kwargs):
    logs = []
    client, models, delays = make_client(
        outcomes, logs=logs, model="primary", fallback_models=fallbacks, **kwargs)
    return client, models, delays, logs


def models_called(models):
    return [c["model"] for c in models.calls]


def test_primary_success_no_fallback():
    client, models, delays, logs = fallback_client(["ok"])
    assert client.generate("s", "u") == "ok"
    assert models_called(models) == ["primary"]
    assert client.last_model_used == "primary"
    assert logs == []


def test_primary_503_falls_back_after_retries():
    client, models, delays, logs = fallback_client(
        [api_error(503)] * 4 + ["from fallback"], max_retries=3)
    assert client.generate("s", "u") == "from fallback"
    assert models_called(models) == ["primary"] * 4 + ["fb-1"]
    assert delays == [1.0, 2.0, 4.0]  # bounded retry on primary first
    assert client.last_model_used == "fb-1"
    assert logs == [
        "Primary model unavailable: primary (HTTP 503 after 4 attempts)",
        "Falling back to: fb-1",
        "Succeeded with fallback model: fb-1",
    ]


def test_primary_daily_quota_falls_back_immediately():
    client, models, delays, logs = fallback_client([quota_error(DAILY_QUOTA_ID), "ok"])
    assert client.generate("s", "u") == "ok"
    assert models_called(models) == ["primary", "fb-1"]
    assert delays == []  # daily quota is not retried on the same model
    assert "daily quota exhausted" in logs[0]
    assert client.last_model_used == "fb-1"


def test_short_term_429_uses_bounded_retry_and_does_not_fall_back():
    client, models, delays, logs = fallback_client(
        [quota_error(MINUTE_QUOTA_ID)] * 4 + ["never"], max_retries=3)
    with pytest.raises(LLMError, match="after 4 attempts"):
        client.generate("s", "u")
    assert models_called(models) == ["primary"] * 4
    assert delays == [1.0, 2.0, 4.0]
    assert logs == []


@pytest.mark.parametrize("code", [500, 502, 504])
def test_other_5xx_use_bounded_retry_and_do_not_fall_back(code):
    client, models, delays, _ = fallback_client([api_error(code)] * 4 + ["never"], max_retries=3)
    with pytest.raises(LLMError, match="after 4 attempts"):
        client.generate("s", "u")
    assert models_called(models) == ["primary"] * 4
    assert delays == [1.0, 2.0, 4.0]


def test_fallback_model_also_gets_bounded_retries():
    outcomes = [quota_error(DAILY_QUOTA_ID), api_error(503), api_error(503), "ok"]
    client, models, delays, logs = fallback_client(outcomes, max_retries=3)
    assert client.generate("s", "u") == "ok"
    assert models_called(models) == ["primary", "fb-1", "fb-1", "fb-1"]
    assert delays == [1.0, 2.0]
    assert "Falling back to: fb-1" in logs


def test_short_term_429_that_recovers_stays_on_primary():
    client, models, _, logs = fallback_client([quota_error(MINUTE_QUOTA_ID), "ok"])
    assert client.generate("s", "u") == "ok"
    assert models_called(models) == ["primary", "primary"]
    assert logs == []


@pytest.mark.parametrize("permanent", [400, 401, 403, 404])
def test_permanent_errors_do_not_fall_back(permanent):
    client, models, delays, logs = fallback_client([api_error(permanent), "never"])
    with pytest.raises(LLMError, match="not retryable"):
        client.generate("s", "u")
    assert models_called(models) == ["primary"]
    assert delays == []
    assert logs == []


def test_programming_errors_do_not_fall_back():
    client, models, _, _ = fallback_client([TypeError("bug"), "never"])
    with pytest.raises(LLMError, match="not retryable"):
        client.generate("s", "u")
    assert models_called(models) == ["primary"]


def test_network_errors_do_not_fall_back():
    client, models, _, _ = fallback_client([httpx.ConnectError("reset")] * 4 + ["never"],
                                           max_retries=3)
    with pytest.raises(LLMError, match="after 4 attempts"):
        client.generate("s", "u")
    assert set(models_called(models)) == {"primary"}


def test_all_models_failing_raises_clear_error():
    outcomes = [api_error(503)] * 2 + [quota_error(DAILY_QUOTA_ID)] + [api_error(503)] * 2
    client, models, _, logs = fallback_client(outcomes, max_retries=1)
    with pytest.raises(LLMError, match="All configured Gemini models are unavailable") as exc_info:
        client.generate("s", "u")
    message = str(exc_info.value)
    assert models_called(models) == ["primary", "primary", "fb-1", "fb-2", "fb-2"]
    for name in ["primary", "fb-1", "fb-2"]:
        assert name in message
    assert "daily quota exhausted" in message
    assert "HTTP 503 after 2 attempts" in message
    assert client.last_model_used is None
    assert logs[-1] == "Fallback model unavailable: fb-2 (HTTP 503 after 2 attempts)"


def test_each_model_attempted_once_no_loop():
    outcomes = [quota_error(DAILY_QUOTA_ID)] * 3 + ["never"]
    client, models, _, _ = fallback_client(outcomes)
    with pytest.raises(LLMError):
        client.generate("s", "u")
    assert models_called(models) == ["primary", "fb-1", "fb-2"]


def test_duplicate_fallbacks_deduplicated_and_primary_not_repeated():
    client, models, _, _ = fallback_client(
        [quota_error(DAILY_QUOTA_ID)] * 3 + ["never"],
        fallbacks=["fb-1", "primary", " fb-1 ", "", "fb-2", "fb-2"])
    assert client.models == ("primary", "fb-1", "fb-2")
    with pytest.raises(LLMError):
        client.generate("s", "u")
    assert models_called(models) == ["primary", "fb-1", "fb-2"]


def test_from_settings_passes_fallbacks():
    settings = GeminiSettings(api_key=SECRET, model="primary", fallback_models=("fb-1",))
    client = GeminiClient.from_settings(
        settings, sdk_client=SimpleNamespace(models=FakeModels([])), log=lambda m: None)
    assert client.models == ("primary", "fb-1")


def test_fallback_errors_and_logs_do_not_leak_api_key():
    outcomes = [api_error(503), quota_error(DAILY_QUOTA_ID), api_error(401)]
    client, _, _, logs = fallback_client(outcomes, max_retries=0)
    with pytest.raises(LLMError) as exc_info:
        client.generate("s", "u")
    assert SECRET not in str(exc_info.value)
    assert all(SECRET not in line for line in logs)


# --- SDK HTTP options: we are the only retry layer ---------------------------------


class RecordingGenaiClient:
    instances = []

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        RecordingGenaiClient.instances.append(self)


@pytest.fixture
def recording_genai(monkeypatch):
    RecordingGenaiClient.instances = []
    monkeypatch.setattr(llm_client.genai, "Client", RecordingGenaiClient)
    return RecordingGenaiClient


def test_sdk_client_constructed_with_single_attempt_and_timeout(recording_genai):
    GeminiClient(api_key=SECRET, model="m", log=lambda _: None)
    (instance,) = recording_genai.instances
    options = instance.kwargs["http_options"]
    assert instance.kwargs["api_key"] == SECRET
    assert options.retry_options.attempts == 1
    assert options.timeout == 30_000  # milliseconds
    assert DEFAULT_REQUEST_TIMEOUT_SECONDS == 30.0


def test_request_timeout_is_configurable(recording_genai):
    GeminiClient(api_key=SECRET, model="m", request_timeout_seconds=12.5, log=lambda _: None)
    assert recording_genai.instances[0].kwargs["http_options"].timeout == 12_500


def test_invalid_timeout_rejected():
    with pytest.raises(ValueError, match="timeout_seconds"):
        build_http_options(0)


def test_from_settings_uses_same_sdk_options(recording_genai):
    GeminiClient.from_settings(GeminiSettings(api_key=SECRET, model="m"), log=lambda _: None)
    options = recording_genai.instances[0].kwargs["http_options"]
    assert options.retry_options.attempts == 1
    assert options.timeout == 30_000


def test_real_sdk_client_will_not_retry():
    """Build a real genai.Client (no network) and inspect the SDK's own retry policy."""
    client = GeminiClient(api_key="not-a-real-key", model="m", log=lambda _: None)
    api_client = client._client._api_client
    assert api_client._http_options.retry_options.attempts == 1
    assert api_client._http_options.timeout == 30_000
    assert api_client._retry.stop.max_attempt_number == 1


def test_http_options_repr_has_no_api_key():
    client = GeminiClient(api_key=SECRET, model="m", log=lambda _: None)
    assert SECRET not in repr(client.http_options)
