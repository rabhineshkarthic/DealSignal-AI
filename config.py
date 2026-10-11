"""Runtime configuration loaded from the environment / a local .env file.

The API key is never printed: it is excluded from the settings repr and never
included in error messages.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

from dotenv import load_dotenv

DEFAULT_GEMINI_MODEL = "gemini-3.8-flash"
PLACEHOLDER_API_KEY = "your_api_key_here"
DEFAULT_ENV_FILE = Path(__file__).resolve().parent / ".env"


class ConfigError(RuntimeError):
    """Raised when required configuration is missing or invalid."""


@dataclass(frozen=True)
class GeminiSettings:
    api_key: str = field(repr=False)
    model: str
    fallback_models: tuple[str, ...] = ()


def unique_models(models: Iterable[str]) -> tuple[str, ...]:
    """Strip names, drop blanks, and de-duplicate while preserving order."""
    seen: list[str] = []
    for name in models:
        name = name.strip()
        if name and name not in seen:
            seen.append(name)
    return tuple(seen)


def parse_fallback_models(raw: str, primary: str) -> tuple[str, ...]:
    """Parse a comma-separated model list, excluding the primary model.

    Blank entries are ignored and duplicates removed (first occurrence wins).
    """
    models = unique_models(raw.split(","))
    invalid = [m for m in models if any(ch.isspace() for ch in m)]
    if invalid:
        raise ConfigError(
            f"GEMINI_FALLBACK_MODELS contains invalid model names {invalid}; "
            "use a comma-separated list such as model-a,model-b"
        )
    return tuple(m for m in models if m != primary)


def load_settings(env_file: str | Path | None = DEFAULT_ENV_FILE) -> GeminiSettings:
    """Load Gemini settings from environment variables (and `env_file` if it exists).

    Variables already set in the environment take precedence over the .env file.
    Pass env_file=None to skip reading a .env file.
    """
    if env_file is not None:
        load_dotenv(env_file, override=False)

    api_key = os.getenv("GEMINI_API_KEY", "").strip()
    if not api_key:
        raise ConfigError(
            "GEMINI_API_KEY is not set. Copy .env.example to .env and add your "
            "Gemini API key, or export GEMINI_API_KEY in your shell."
        )
    if api_key == PLACEHOLDER_API_KEY:
        raise ConfigError(
            "GEMINI_API_KEY still has the placeholder value from .env.example. "
            "Replace it with your real Gemini API key in .env."
        )

    model = os.getenv("GEMINI_MODEL", "").strip() or DEFAULT_GEMINI_MODEL
    fallback_models = parse_fallback_models(os.getenv("GEMINI_FALLBACK_MODELS", ""), model)
    return GeminiSettings(api_key=api_key, model=model, fallback_models=fallback_models)
