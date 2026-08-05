from __future__ import annotations

import json
import os
import stat
from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path
from typing import Any, Self
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlsplit

import pytest

from nba_commish.config import YahooSettings
from nba_commish.yahoo_auth import (
    AUTHORIZATION_ENDPOINT,
    TOKEN_ENDPOINT,
    AuthorizationError,
    AuthorizationTimeout,
    JsonFileTokenStore,
    YahooToken,
    YahooTokenTransport,
    authorize,
    build_authorization_url,
)

CLIENT_SECRET = "synthetic-client-secret"
AUTHORIZATION_CODE = "synthetic-authorization-code"
ACCESS_TOKEN = "synthetic-access-token"
REFRESH_TOKEN = "synthetic-refresh-token"
STATE = "synthetic-random-state"
SETTINGS = YahooSettings(
    client_id="synthetic-client-id",
    client_secret=CLIENT_SECRET,
    redirect_uri="http://127.0.0.1:8765/auth/yahoo/callback",
)
VALID_RESPONSE: dict[str, object] = {
    "access_token": ACCESS_TOKEN,
    "refresh_token": REFRESH_TOKEN,
    "token_type": "bearer",
    "expires_in": 3600,
}


class FakeReceiver:
    def __init__(self, callback: dict[str, list[str]] | Exception) -> None:
        self.callback = callback
        self.timeouts: list[float] = []

    def receive(self, timeout_seconds: float) -> dict[str, list[str]]:
        self.timeouts.append(timeout_seconds)
        if isinstance(self.callback, Exception):
            raise self.callback
        return self.callback


class FakeTransport:
    def __init__(self, response: dict[str, object] | None = None) -> None:
        self.response = VALID_RESPONSE if response is None else response
        self.calls: list[tuple[YahooSettings, str]] = []

    def exchange_code(
        self, settings: YahooSettings, authorization_code: str
    ) -> dict[str, object]:
        self.calls.append((settings, authorization_code))
        return self.response


class FakeStore:
    def __init__(self) -> None:
        self.saved: list[YahooToken] = []

    def save(self, token: YahooToken) -> None:
        self.saved.append(token)


class FakeResponse:
    def __init__(self, body: bytes, status: int = 200) -> None:
        self.body = body
        self.status = status

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def read(self) -> bytes:
        return self.body


def test_authorization_url_contains_code_flow_parameters() -> None:
    url = build_authorization_url(SETTINGS, STATE)

    parsed = urlsplit(url)
    query = parse_qs(parsed.query)
    assert f"{parsed.scheme}://{parsed.netloc}{parsed.path}" == AUTHORIZATION_ENDPOINT
    assert query == {
        "client_id": [SETTINGS.client_id],
        "redirect_uri": [SETTINGS.redirect_uri],
        "response_type": ["code"],
        "state": [STATE],
    }


def test_complete_authorization_flow_uses_injected_boundaries() -> None:
    receiver = FakeReceiver({"state": [STATE], "code": [AUTHORIZATION_CODE]})
    transport = FakeTransport()
    store = FakeStore()
    opened_urls: list[str] = []
    output: list[str] = []

    token = authorize(
        SETTINGS,
        receiver,
        transport,
        store,
        state_factory=lambda: STATE,
        clock=lambda: datetime(2026, 8, 5, 12, 0, tzinfo=UTC),
        browser_open=opened_urls.append,
        output=output.append,
        timeout_seconds=17,
    )

    assert receiver.timeouts == [17]
    assert transport.calls == [(SETTINGS, AUTHORIZATION_CODE)]
    assert token == YahooToken(
        access_token=ACCESS_TOKEN,
        refresh_token=REFRESH_TOKEN,
        token_type="bearer",
        expires_at="2026-08-05T13:00:00Z",
    )
    assert store.saved == [token]
    assert opened_urls == [build_authorization_url(SETTINGS, STATE)]
    assert output == [f"Open this Yahoo consent URL in your browser:\n{opened_urls[0]}"]
    rendered_output = "".join(output)
    for secret in (CLIENT_SECRET, AUTHORIZATION_CODE, ACCESS_TOKEN, REFRESH_TOKEN):
        assert secret not in rendered_output


def test_default_state_factory_uses_secure_random_source(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[int] = []

    def fake_token_urlsafe(size: int) -> str:
        calls.append(size)
        return STATE

    monkeypatch.setattr(
        "nba_commish.yahoo_auth.secrets.token_urlsafe", fake_token_urlsafe
    )

    authorize(
        SETTINGS,
        FakeReceiver({"state": [STATE], "code": [AUTHORIZATION_CODE]}),
        FakeTransport(),
        FakeStore(),
        browser_open=lambda _url: None,
        output=lambda _message: None,
    )

    assert calls == [32]


@pytest.mark.parametrize(
    ("callback", "message"),
    [
        ({"code": [AUTHORIZATION_CODE]}, "state was missing"),
        (
            {"state": ["unexpected-state"], "code": [AUTHORIZATION_CODE]},
            "state was missing or did not match",
        ),
        ({"state": [STATE]}, "did not include an authorization code"),
        (
            {"state": [STATE], "error": ["access_denied"]},
            "declined or could not complete",
        ),
    ],
)
def test_invalid_callback_does_not_exchange_or_save(
    callback: dict[str, list[str]], message: str
) -> None:
    transport = FakeTransport()
    store = FakeStore()

    with pytest.raises(AuthorizationError, match=message) as error:
        authorize(
            SETTINGS,
            FakeReceiver(callback),
            transport,
            store,
            state_factory=lambda: STATE,
            browser_open=lambda _url: None,
            output=lambda _message: None,
        )

    assert transport.calls == []
    assert store.saved == []
    for secret in (CLIENT_SECRET, AUTHORIZATION_CODE, ACCESS_TOKEN, REFRESH_TOKEN):
        assert secret not in str(error.value)


def test_callback_timeout_does_not_exchange_or_save() -> None:
    transport = FakeTransport()
    store = FakeStore()

    with pytest.raises(AuthorizationTimeout, match="Timed out waiting"):
        authorize(
            SETTINGS,
            FakeReceiver(AuthorizationTimeout("Timed out waiting for callback.")),
            transport,
            store,
            state_factory=lambda: STATE,
            browser_open=lambda _url: None,
            output=lambda _message: None,
        )

    assert transport.calls == []
    assert store.saved == []


@pytest.mark.parametrize(
    ("response", "message"),
    [
        ({**VALID_RESPONSE, "access_token": ""}, "access_token"),
        (
            {
                key: value
                for key, value in VALID_RESPONSE.items()
                if key != "refresh_token"
            },
            "refresh_token",
        ),
        ({**VALID_RESPONSE, "token_type": None}, "token_type"),
        ({**VALID_RESPONSE, "expires_in": 0}, "positive numeric expires_in"),
        (
            {**VALID_RESPONSE, "expires_in": "not-numeric"},
            "positive numeric expires_in",
        ),
    ],
)
def test_invalid_token_response_does_not_save(
    response: dict[str, object], message: str
) -> None:
    transport = FakeTransport(response)
    store = FakeStore()

    with pytest.raises(AuthorizationError, match=message) as error:
        authorize(
            SETTINGS,
            FakeReceiver({"state": [STATE], "code": [AUTHORIZATION_CODE]}),
            transport,
            store,
            state_factory=lambda: STATE,
            browser_open=lambda _url: None,
            output=lambda _message: None,
        )

    assert len(transport.calls) == 1
    assert store.saved == []
    for secret in (CLIENT_SECRET, AUTHORIZATION_CODE, ACCESS_TOKEN, REFRESH_TOKEN):
        assert secret not in str(error.value)


def test_token_transport_posts_expected_authorization_code_exchange() -> None:
    captured: dict[str, Any] = {}

    def opener(request: Any, *, timeout: int) -> FakeResponse:
        captured["request"] = request
        captured["timeout"] = timeout
        return FakeResponse(json.dumps(VALID_RESPONSE).encode())

    response = YahooTokenTransport(opener).exchange_code(SETTINGS, AUTHORIZATION_CODE)

    request = captured["request"]
    assert request.full_url == TOKEN_ENDPOINT
    assert request.method == "POST"
    assert captured["timeout"] == 30
    assert parse_qs(request.data.decode()) == {
        "code": [AUTHORIZATION_CODE],
        "grant_type": ["authorization_code"],
        "redirect_uri": [SETTINGS.redirect_uri],
    }
    assert request.get_header("Authorization").startswith("Basic ")
    assert response == VALID_RESPONSE


@pytest.mark.parametrize(
    ("failure", "message"),
    [
        (
            HTTPError(TOKEN_ENDPOINT, 401, "synthetic failure", {}, BytesIO()),
            "HTTP status 401",
        ),
        (URLError("synthetic network failure"), "check the network connection"),
    ],
)
def test_token_transport_sanitizes_request_failures(
    failure: Exception, message: str
) -> None:
    def opener(*_args: object, **_kwargs: object) -> FakeResponse:
        raise failure

    with pytest.raises(AuthorizationError, match=message) as error:
        YahooTokenTransport(opener).exchange_code(SETTINGS, AUTHORIZATION_CODE)

    rendered_error = str(error.value)
    for secret in (CLIENT_SECRET, AUTHORIZATION_CODE, ACCESS_TOKEN, REFRESH_TOKEN):
        assert secret not in rendered_error


def test_token_transport_rejects_malformed_json() -> None:
    def opener(*_args: object, **_kwargs: object) -> FakeResponse:
        return FakeResponse(b"not-json")

    with pytest.raises(AuthorizationError, match="malformed JSON"):
        YahooTokenTransport(opener).exchange_code(SETTINGS, AUTHORIZATION_CODE)


def test_file_store_persists_only_token_fields_with_private_modes(
    tmp_path: Path,
) -> None:
    token_path = tmp_path / "private" / "yahoo" / "oauth-token.json"
    store = JsonFileTokenStore(token_path)
    token = YahooToken(
        access_token=ACCESS_TOKEN,
        refresh_token=REFRESH_TOKEN,
        token_type="bearer",
        expires_at="2026-08-05T13:00:00Z",
    )

    store.save(token)

    assert json.loads(token_path.read_text()) == {
        "access_token": ACCESS_TOKEN,
        "expires_at": "2026-08-05T13:00:00Z",
        "refresh_token": REFRESH_TOKEN,
        "token_type": "bearer",
    }
    persisted = token_path.read_text()
    assert CLIENT_SECRET not in persisted
    assert AUTHORIZATION_CODE not in persisted
    if os.name == "posix":
        assert stat.S_IMODE(token_path.parent.stat().st_mode) == 0o700
        assert stat.S_IMODE(token_path.stat().st_mode) == 0o600


def test_file_store_failed_replace_preserves_existing_record(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    token_path = tmp_path / "private" / "yahoo" / "oauth-token.json"
    token_path.parent.mkdir(parents=True)
    token_path.write_text("existing complete record\n")
    store = JsonFileTokenStore(token_path)

    def failed_replace(_source: Path, _target: Path) -> None:
        raise OSError("synthetic replace failure")

    monkeypatch.setattr("nba_commish.yahoo_auth.os.replace", failed_replace)

    with pytest.raises(OSError, match="synthetic replace failure"):
        store.save(
            YahooToken(
                access_token=ACCESS_TOKEN,
                refresh_token=REFRESH_TOKEN,
                token_type="bearer",
                expires_at="2026-08-05T13:00:00Z",
            )
        )

    assert token_path.read_text() == "existing complete record\n"
    assert list(token_path.parent.glob("*.tmp")) == []
