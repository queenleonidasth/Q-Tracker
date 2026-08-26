from urllib.error import HTTPError

import pytest

import codex_api_client
from quota_models import ProviderErrorKind, ProviderFetchError


@pytest.mark.parametrize(
    ("status", "kind"),
    [
        (401, ProviderErrorKind.AUTH_REQUIRED),
        (403, ProviderErrorKind.AUTH_REQUIRED),
        (429, ProviderErrorKind.RATE_LIMITED),
        (500, ProviderErrorKind.OTHER),
    ],
)
def test_live_http_errors_preserve_structured_kind(monkeypatch, status, kind):
    def fail(*_args, **_kwargs):
        raise HTTPError(codex_api_client.WHAM_USAGE_URL, status, "failure", {}, None)

    monkeypatch.setattr(codex_api_client, "urlopen", fail)

    with pytest.raises(ProviderFetchError) as captured:
        codex_api_client._fetch_usage("not-a-real-token", None)

    assert captured.value.kind is kind
    assert "not-a-real-token" not in str(captured.value)


def test_live_timeout_is_structured_without_credentials(monkeypatch):
    def fail(*_args, **_kwargs):
        raise TimeoutError("timed out")

    monkeypatch.setattr(codex_api_client, "urlopen", fail)

    with pytest.raises(ProviderFetchError) as captured:
        codex_api_client._fetch_usage("not-a-real-token", "account")

    assert captured.value.kind is ProviderErrorKind.TIMEOUT
    assert "not-a-real-token" not in str(captured.value)


def test_missing_codex_auth_is_an_explicit_auth_failure(monkeypatch):
    monkeypatch.setattr(codex_api_client, "_read_codex_auth", lambda: None)

    with pytest.raises(ProviderFetchError) as captured:
        codex_api_client.fetch_codex_live_limits()

    assert captured.value.kind is ProviderErrorKind.AUTH_REQUIRED


def test_classify_window_accepts_non_standard_session_duration():
    """A 4-hour session window must classify as session without exact-match luck."""
    assert codex_api_client._classify_window({"limit_window_seconds": 14_400}) == "session"
    assert codex_api_client._classify_window({"limit_window_seconds": 18_000}) == "session"
    assert codex_api_client._classify_window({"limit_window_seconds": 604_800}) == "weekly"
    assert codex_api_client._classify_window({"limit_window_seconds": 900_000}) == "weekly"


def test_normalize_usage_reports_real_minutes_for_short_session():
    body = {
        "rate_limit": {
            "primary_window": {
                "used_percent": 12.0,
                "limit_window_seconds": 14_400,
                "reset_at": "2026-08-27T00:00:00Z",
            }
        }
    }

    result = codex_api_client._normalize_usage_response(body)

    assert result is not None
    assert result["window_minutes"] == 240
    assert result["percent_left"] == 88.0
