"""Yahoo OAuth authorization-code flow and local token persistence."""

from __future__ import annotations

import base64
import contextlib
import ipaddress
import json
import math
import os
import secrets
import socket
import sys
import tempfile
import time
import webbrowser
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any, Protocol, cast
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlencode, urlsplit
from urllib.request import Request, urlopen

from nba_commish.config import ConfigurationError, YahooSettings

AUTHORIZATION_ENDPOINT = "https://api.login.yahoo.com/oauth2/request_auth"
TOKEN_ENDPOINT = "https://api.login.yahoo.com/oauth2/get_token"
DEFAULT_CALLBACK_TIMEOUT_SECONDS = 300.0
DEFAULT_TOKEN_PATH = Path("data/private/yahoo/oauth-token.json")


class AuthorizationError(RuntimeError):
    """Raised when Yahoo authorization cannot be completed safely."""


class AuthorizationTimeout(AuthorizationError):
    """Raised when the redirect callback is not received in time."""


@dataclass(frozen=True, slots=True)
class YahooToken:
    """The reusable fields returned by Yahoo for an authorized account."""

    access_token: str
    refresh_token: str
    token_type: str
    expires_at: str


class CallbackReceiver(Protocol):
    """Receives one authorization redirect callback."""

    def receive(self, timeout_seconds: float) -> Mapping[str, Sequence[str]]:
        """Wait for and return the callback query parameters."""


class TokenTransport(Protocol):
    """Exchanges an authorization code for a Yahoo token response."""

    def exchange_code(
        self, settings: YahooSettings, authorization_code: str
    ) -> Mapping[str, object]:
        """Exchange one authorization code using the configured credentials."""


class TokenStore(Protocol):
    """Persists a token independently of the authorization flow."""

    def save(self, token: YahooToken) -> None:
        """Persist a complete token record."""


class JsonFileTokenStore:
    """Atomically persist a Yahoo token as user-private JSON."""

    def __init__(self, path: Path = DEFAULT_TOKEN_PATH) -> None:
        self.path = path

    def save(self, token: YahooToken) -> None:
        """Replace the token file atomically after writing a complete record."""
        directory = self.path.parent
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        if os.name == "posix":
            directory.chmod(0o700)

        descriptor, temporary_name = tempfile.mkstemp(
            dir=directory,
            prefix=f".{self.path.name}.",
            suffix=".tmp",
        )
        temporary_path = Path(temporary_name)
        try:
            if os.name == "posix":
                os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8") as token_file:
                descriptor = -1
                json.dump(asdict(token), token_file, indent=2, sort_keys=True)
                token_file.write("\n")
                token_file.flush()
                os.fsync(token_file.fileno())
            os.replace(temporary_path, self.path)
            if os.name == "posix":
                self.path.chmod(0o600)
            _sync_directory(directory)
        except BaseException:
            if descriptor >= 0:
                os.close(descriptor)
            with contextlib.suppress(FileNotFoundError):
                temporary_path.unlink()
            raise


class YahooTokenTransport:
    """Exchange an authorization code at Yahoo's token endpoint."""

    def __init__(
        self,
        opener: Callable[..., Any] = urlopen,
    ) -> None:
        self._opener = opener

    def exchange_code(
        self, settings: YahooSettings, authorization_code: str
    ) -> Mapping[str, object]:
        credentials = f"{settings.client_id}:{settings.client_secret}".encode()
        basic_credentials = base64.b64encode(credentials).decode("ascii")
        request = Request(
            TOKEN_ENDPOINT,
            data=urlencode(
                {
                    "code": authorization_code,
                    "grant_type": "authorization_code",
                    "redirect_uri": settings.redirect_uri,
                }
            ).encode("ascii"),
            headers={
                "Accept": "application/json",
                "Authorization": f"Basic {basic_credentials}",
                "Content-Type": "application/x-www-form-urlencoded",
            },
            method="POST",
        )

        try:
            with self._opener(request, timeout=30) as response:
                status = getattr(response, "status", 200)
                if not 200 <= status < 300:
                    raise AuthorizationError(
                        f"Yahoo token exchange failed with HTTP status {status}."
                    )
                body = response.read()
        except HTTPError as error:
            raise AuthorizationError(
                f"Yahoo token exchange failed with HTTP status {error.code}."
            ) from None
        except (TimeoutError, URLError, OSError):
            raise AuthorizationError(
                "Yahoo token exchange could not be completed; check the network "
                "connection and try again."
            ) from None

        try:
            payload = json.loads(body)
        except (json.JSONDecodeError, UnicodeDecodeError, TypeError):
            raise AuthorizationError(
                "Yahoo token endpoint returned malformed JSON; reauthorize and try "
                "again."
            ) from None
        if not isinstance(payload, dict):
            raise AuthorizationError(
                "Yahoo token endpoint returned an invalid response; reauthorize and "
                "try again."
            )
        return cast(dict[str, object], payload)


class LoopbackCallbackReceiver:
    """Listen for a single callback on the configured loopback URI."""

    def __init__(self, redirect_uri: str) -> None:
        parsed = urlsplit(redirect_uri)
        if parsed.scheme != "http" or not _is_loopback_host(parsed.hostname):
            raise AuthorizationError(
                "YAHOO_REDIRECT_URI must use http and a loopback host such as "
                "localhost or 127.0.0.1."
            )
        try:
            port = parsed.port or 80
        except ValueError:
            raise AuthorizationError(
                "YAHOO_REDIRECT_URI must contain a valid callback port."
            ) from None
        if parsed.query or parsed.fragment:
            raise AuthorizationError(
                "YAHOO_REDIRECT_URI must not contain a query string or fragment."
            )

        self._callback_path = parsed.path or "/"
        server_type = _IPv6HTTPServer if _is_ipv6(parsed.hostname) else HTTPServer
        try:
            self._server = server_type((parsed.hostname or "", port), _CallbackHandler)
        except OSError:
            raise AuthorizationError(
                "Could not listen on the configured Yahoo callback address; make "
                "sure its port is available."
            ) from None
        self._server.callback_path = self._callback_path  # type: ignore[attr-defined]
        self._server.callback_query = None  # type: ignore[attr-defined]

    def receive(self, timeout_seconds: float) -> Mapping[str, Sequence[str]]:
        """Wait for one matching callback without logging its sensitive query."""
        deadline = time.monotonic() + timeout_seconds
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise AuthorizationTimeout(
                    "Timed out waiting for Yahoo authorization; no token was saved."
                )
            self._server.timeout = remaining
            self._server.handle_request()
            query = self._server.callback_query  # type: ignore[attr-defined]
            if query is not None:
                return cast(dict[str, list[str]], query)

    def close(self) -> None:
        """Release the callback listener."""
        self._server.server_close()


class _IPv6HTTPServer(HTTPServer):
    address_family = socket.AF_INET6


class _CallbackHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        parsed = urlsplit(self.path)
        if parsed.path != self.server.callback_path:  # type: ignore[attr-defined]
            self.send_error(404)
            return

        self.server.callback_query = parse_qs(  # type: ignore[attr-defined]
            parsed.query,
            keep_blank_values=True,
        )
        body = b"Yahoo authorization callback received. You may close this window."
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        """Suppress request logging because the request target contains the code."""


def build_authorization_url(settings: YahooSettings, state: str) -> str:
    """Build Yahoo's authorization-code consent URL."""
    return f"{AUTHORIZATION_ENDPOINT}?{urlencode({'client_id': settings.client_id, 'redirect_uri': settings.redirect_uri, 'response_type': 'code', 'state': state})}"


def authorize(
    settings: YahooSettings,
    receiver: CallbackReceiver,
    transport: TokenTransport,
    store: TokenStore,
    *,
    state_factory: Callable[[], str] = lambda: secrets.token_urlsafe(32),
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    browser_open: Callable[[str], object] = webbrowser.open,
    output: Callable[[str], object] = print,
    timeout_seconds: float = DEFAULT_CALLBACK_TIMEOUT_SECONDS,
) -> YahooToken:
    """Authorize once, validate the callback and response, then persist the token."""
    state = state_factory()
    if not state:
        raise AuthorizationError("Could not create a secure OAuth state value.")
    authorization_url = build_authorization_url(settings, state)
    output(f"Open this Yahoo consent URL in your browser:\n{authorization_url}")
    browser_open(authorization_url)

    callback = receiver.receive(timeout_seconds)
    returned_state = _one_callback_value(callback, "state")
    if returned_state is None or not secrets.compare_digest(returned_state, state):
        raise AuthorizationError(
            "Yahoo callback state was missing or did not match; no token was saved."
        )
    if _one_callback_value(callback, "error") is not None:
        raise AuthorizationError(
            "Yahoo declined or could not complete authorization; no token was saved."
        )
    authorization_code = _one_callback_value(callback, "code")
    if authorization_code is None:
        raise AuthorizationError(
            "Yahoo callback did not include an authorization code; no token was saved."
        )

    response = transport.exchange_code(settings, authorization_code)
    token = _token_from_response(response, clock())
    store.save(token)
    return token


def main() -> int:
    """Run local Yahoo authorization from process-environment settings."""
    receiver: LoopbackCallbackReceiver | None = None
    try:
        settings = YahooSettings.from_env()
        receiver = LoopbackCallbackReceiver(settings.redirect_uri)
        authorize(
            settings,
            receiver,
            YahooTokenTransport(),
            JsonFileTokenStore(),
        )
    except (ConfigurationError, AuthorizationError) as error:
        print(f"Yahoo authorization failed: {error}", file=sys.stderr)
        return 1
    except OSError:
        print(
            "Yahoo authorization failed while saving the token; check permissions "
            "and try again.",
            file=sys.stderr,
        )
        return 1
    finally:
        if receiver is not None:
            receiver.close()

    print(f"Yahoo authorization succeeded. Token saved to {DEFAULT_TOKEN_PATH}.")
    return 0


def _one_callback_value(callback: Mapping[str, Sequence[str]], name: str) -> str | None:
    values = callback.get(name, ())
    if len(values) != 1 or not values[0]:
        return None
    return values[0]


def _token_from_response(response: Mapping[str, object], now: datetime) -> YahooToken:
    fields: dict[str, str] = {}
    for field_name in ("access_token", "refresh_token", "token_type"):
        value = response.get(field_name)
        if not isinstance(value, str) or not value:
            raise AuthorizationError(
                f"Yahoo token response is missing a valid {field_name}; "
                "reauthorize and try again."
            )
        fields[field_name] = value

    expires_in = response.get("expires_in")
    if isinstance(expires_in, bool):
        expires_seconds = math.nan
    else:
        try:
            expires_seconds = float(cast(str | int | float, expires_in))
        except (TypeError, ValueError, OverflowError):
            expires_seconds = math.nan
    if not math.isfinite(expires_seconds) or expires_seconds <= 0:
        raise AuthorizationError(
            "Yahoo token response is missing a positive numeric expires_in; "
            "reauthorize and try again."
        )

    if now.tzinfo is None or now.utcoffset() is None:
        now = now.replace(tzinfo=UTC)
    expires_at = now.astimezone(UTC) + timedelta(seconds=expires_seconds)
    return YahooToken(
        access_token=fields["access_token"],
        refresh_token=fields["refresh_token"],
        token_type=fields["token_type"],
        expires_at=expires_at.isoformat().replace("+00:00", "Z"),
    )


def _is_loopback_host(hostname: str | None) -> bool:
    if hostname is None:
        return False
    if hostname.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(hostname).is_loopback
    except ValueError:
        return False


def _is_ipv6(hostname: str | None) -> bool:
    try:
        return isinstance(ipaddress.ip_address(hostname or ""), ipaddress.IPv6Address)
    except ValueError:
        return False


def _sync_directory(directory: Path) -> None:
    if os.name != "posix":
        return
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    descriptor = os.open(directory, flags)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


if __name__ == "__main__":
    raise SystemExit(main())
