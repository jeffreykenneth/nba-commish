"""Typed application configuration sourced from the process environment."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from urllib.parse import urlsplit


class ConfigurationError(ValueError):
    """Raised when required application configuration is missing or invalid."""


@dataclass(frozen=True, slots=True)
class YahooSettings:
    """Credentials and callback settings for Yahoo OAuth."""

    client_id: str
    client_secret: str
    redirect_uri: str

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> YahooSettings:
        """Load and validate Yahoo settings from an environment mapping."""
        source = os.environ if environ is None else environ
        names = (
            "YAHOO_CLIENT_ID",
            "YAHOO_CLIENT_SECRET",
            "YAHOO_REDIRECT_URI",
        )
        values = {name: source.get(name, "").strip() for name in names}
        missing = [name for name, value in values.items() if not value]

        if missing:
            joined_names = ", ".join(missing)
            raise ConfigurationError(
                f"Missing required environment variables: {joined_names}"
            )

        redirect_uri = values["YAHOO_REDIRECT_URI"]
        try:
            parsed_redirect = urlsplit(redirect_uri)
            _ = parsed_redirect.port
        except ValueError:
            parsed_redirect = None

        if (
            parsed_redirect is None
            or parsed_redirect.scheme not in {"http", "https"}
            or parsed_redirect.hostname is None
        ):
            raise ConfigurationError(
                "YAHOO_REDIRECT_URI must be an absolute HTTP(S) URL with a host"
            )

        return cls(
            client_id=values["YAHOO_CLIENT_ID"],
            client_secret=values["YAHOO_CLIENT_SECRET"],
            redirect_uri=redirect_uri,
        )
