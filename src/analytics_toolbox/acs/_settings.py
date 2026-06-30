"""Census API key resolution via pydantic-settings.

The API key is a secret and never belongs in the (committable) config YAML. It is
read from the ``CENSUS_API_KEY`` environment variable, or a ``.env`` file in the
working directory. ``resolve_api_key`` lets an explicit argument win over the
environment for callers that manage the secret themselves.
"""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict

_SIGNUP_URL = "https://api.census.gov/data/key_signup.html"


class AcsSettings(BaseSettings):
    """Environment-sourced settings for ACS ingest.

    ``census_api_key`` is populated from the ``CENSUS_API_KEY`` env var (matched
    case-insensitively) or a ``.env`` file in the current directory.
    """

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    census_api_key: str | None = None


def resolve_api_key(explicit: str | None = None) -> str:
    """Return the Census API key, preferring an explicit value over the environment.

    Args:
        explicit: A key passed directly by the caller. Wins over the environment.

    Returns:
        The resolved Census API key.

    Raises:
        ValueError: If no key is available from either source.
    """
    key = explicit or AcsSettings().census_api_key
    if not key:
        raise ValueError(
            "No Census API key provided. Pass api_key=... or set the CENSUS_API_KEY "
            f"environment variable (or put it in a .env file). Get a free key at {_SIGNUP_URL}"
        )
    return key
