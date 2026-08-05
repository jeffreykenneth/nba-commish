import pytest

from nba_commish.config import ConfigurationError, YahooSettings


def test_yahoo_settings_load_required_values() -> None:
    settings = YahooSettings.from_env(
        {
            "YAHOO_CLIENT_ID": "  client-id  ",
            "YAHOO_CLIENT_SECRET": "  client-secret  ",
            "YAHOO_REDIRECT_URI": ("  http://localhost:8000/auth/yahoo/callback  "),
        }
    )

    assert settings == YahooSettings(
        client_id="client-id",
        client_secret="client-secret",
        redirect_uri="http://localhost:8000/auth/yahoo/callback",
    )


def test_yahoo_settings_use_process_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("YAHOO_CLIENT_ID", "client-id")
    monkeypatch.setenv("YAHOO_CLIENT_SECRET", "client-secret")
    monkeypatch.setenv("YAHOO_REDIRECT_URI", "https://example.test/auth/yahoo/callback")

    assert YahooSettings.from_env().client_id == "client-id"


def test_yahoo_settings_report_all_missing_or_blank_values() -> None:
    with pytest.raises(ConfigurationError) as error:
        YahooSettings.from_env(
            {
                "YAHOO_CLIENT_ID": "  ",
                "YAHOO_CLIENT_SECRET": "",
            }
        )

    assert str(error.value) == (
        "Missing required environment variables: YAHOO_CLIENT_ID, "
        "YAHOO_CLIENT_SECRET, YAHOO_REDIRECT_URI"
    )


@pytest.mark.parametrize(
    "redirect_uri",
    [
        "localhost:8000/auth/yahoo/callback",
        "ftp://example.test/auth/yahoo/callback",
        "https:///auth/yahoo/callback",
        "https://:443/auth/yahoo/callback",
        "https://example.test:not-a-port/auth/yahoo/callback",
    ],
)
def test_yahoo_settings_reject_invalid_redirect_uri(redirect_uri: str) -> None:
    with pytest.raises(
        ConfigurationError,
        match=(r"YAHOO_REDIRECT_URI must be an absolute HTTP\(S\) URL with a host"),
    ):
        YahooSettings.from_env(
            {
                "YAHOO_CLIENT_ID": "client-id",
                "YAHOO_CLIENT_SECRET": "client-secret",
                "YAHOO_REDIRECT_URI": redirect_uri,
            }
        )


def test_configuration_errors_do_not_expose_client_secret() -> None:
    secret = "do-not-include-this-secret"

    with pytest.raises(ConfigurationError) as error:
        YahooSettings.from_env(
            {
                "YAHOO_CLIENT_ID": "client-id",
                "YAHOO_CLIENT_SECRET": secret,
                "YAHOO_REDIRECT_URI": "not-a-url",
            }
        )

    assert secret not in str(error.value)
