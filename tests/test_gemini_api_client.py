import json
from datetime import datetime, timezone

import pytest

import gemini_api_client
from quota_models import ProviderErrorKind, ProviderFetchError


def test_aes_gcm_decrypt_matches_known_answer_roundtrip():
    key = bytes.fromhex("feffe9928665731c6d6a8f9467308308feffe9928665731c6d6a8f9467308308")
    iv = bytes.fromhex("000102030405060708090a0b")
    plaintext = bytes.fromhex("00112233445566778899aabbccddeeff")
    ciphertext = bytes.fromhex("fc82f254a110ffcedba122934be12bed")
    tag = bytes.fromhex("2543ad0b91fe9b2ef060a9a5a46c52a4")

    assert (
        gemini_api_client._aes_gcm_decrypt(key, iv, ciphertext, tag) == plaintext
    )


def test_aes_gcm_decrypt_rejects_tampered_tag():
    key = bytes.fromhex("feffe9928665731c6d6a8f9467308308feffe9928665731c6d6a8f9467308308")
    iv = bytes.fromhex("000102030405060708090a0b")
    ciphertext = bytes.fromhex("fc82f254a110ffcedba122934be12bed")
    tag = bytes.fromhex("00000000000000000000000000000000")

    with pytest.raises(ValueError, match="authentication"):
        gemini_api_client._aes_gcm_decrypt(key, iv, ciphertext, tag)


def test_encrypted_credentials_decrypt_legacy_style_flow(monkeypatch, tmp_path):
    monkeypatch.setattr(gemini_api_client, "GEMINI_HOME", tmp_path)
    monkeypatch.setattr(gemini_api_client, "LEGACY_OAUTH_FILE", tmp_path / "oauth_creds.json")
    monkeypatch.setattr(
        gemini_api_client,
        "ENCRYPTED_OAUTH_FILE",
        tmp_path / "mcp-oauth-tokens-v2.json",
    )
    auth = {"access_token": "abc", "refresh_token": "def", "id_token": "ghi"}
    (tmp_path / "oauth_creds.json").write_text(json.dumps(auth), encoding="utf-8")

    assert gemini_api_client._read_gemini_auth()["access_token"] == "abc"


def test_missing_gemini_auth_is_auth_required(monkeypatch, tmp_path):
    monkeypatch.setattr(gemini_api_client, "GEMINI_HOME", tmp_path)
    monkeypatch.setattr(gemini_api_client, "LEGACY_OAUTH_FILE", tmp_path / "oauth_creds.json")
    monkeypatch.setattr(
        gemini_api_client,
        "ENCRYPTED_OAUTH_FILE",
        tmp_path / "mcp-oauth-tokens-v2.json",
    )

    with pytest.raises(ProviderFetchError) as captured:
        gemini_api_client.fetch_gemini_live_limits()

    assert captured.value.kind is ProviderErrorKind.AUTH_REQUIRED


def test_load_code_assist_http_errors_map_to_structured_kind(monkeypatch):
    from urllib.error import HTTPError

    def fail(*_args, **_kwargs):
        raise HTTPError("https://example.com", 429, "failure", {}, None)

    monkeypatch.setattr(gemini_api_client, "urlopen", fail)

    with pytest.raises(ProviderFetchError) as captured:
        gemini_api_client._load_code_assist("not-a-real-token")

    assert captured.value.kind is ProviderErrorKind.RATE_LIMITED


def test_retrieve_user_quota_auth_error_does_not_leak_token(monkeypatch):
    from urllib.error import HTTPError

    def fail(*_args, **_kwargs):
        raise HTTPError("https://example.com", 403, "failure", {}, None)

    monkeypatch.setattr(gemini_api_client, "urlopen", fail)

    with pytest.raises(ProviderFetchError) as captured:
        gemini_api_client._retrieve_user_quota("secret-token", "project-1")

    assert captured.value.kind is ProviderErrorKind.AUTH_REQUIRED
    assert "secret-token" not in str(captured.value)


def test_normalize_quota_response_groups_model_families():
    body = {
        "buckets": [
            {"modelId": "gemini-2.5-pro", "remainingFraction": 0.8, "resetTime": "2026-08-15T00:00:00Z"},
            {"modelId": "gemini-2.5-flash", "remainingFraction": 0.5, "resetTime": "2026-08-16T00:00:00Z"},
            {"modelId": "gemini-2.5-flash-lite", "remainingFraction": 0.9, "resetTime": "2026-08-17T00:00:00Z"},
        ]
    }

    result = gemini_api_client._normalize_quota_response(body, "standard-tier")

    assert result["plan_type"] == "Paid"
    assert set(result["models"]) == {"pro", "flash", "flash_lite"}
    assert result["models"]["pro"]["used_percent"] == 20.0
    assert result["models"]["flash"]["used_percent"] == 50.0


def test_normalize_quota_response_family_takes_lowest_remaining():
    body = {
        "buckets": [
            {"modelId": "gemini-2.5-pro", "remainingFraction": 0.8},
            {"modelId": "gemini-3.1-pro-preview", "remainingFraction": 0.4},
        ]
    }

    result = gemini_api_client._normalize_quota_response(body, None)

    assert result["models"]["pro"]["remaining_fraction"] == 0.4
    assert result["models"]["pro"]["model_id"] == "gemini-3.1-pro-preview"


def test_normalize_quota_response_ignores_unknown_models():
    body = {"buckets": [{"modelId": "some-other-model", "remainingFraction": 0.7}]}

    assert gemini_api_client._normalize_quota_response(body, None) is None


def test_plan_label_maps_tiers():
    assert gemini_api_client._plan_label("standard-tier") == "Paid"
    assert gemini_api_client._plan_label("legacy-tier") == "Legacy"
    assert gemini_api_client._plan_label("free-tier") == "Free"
    assert gemini_api_client._plan_label(None) == "unknown"


def test_parse_iso_to_epoch_handles_zulu_and_naive():
    expected = int(datetime(2026, 8, 15, tzinfo=timezone.utc).timestamp())
    assert gemini_api_client._parse_iso_to_epoch("2026-08-15T00:00:00Z") == expected
    assert gemini_api_client._parse_iso_to_epoch("2026-08-15T00:00:00") == expected
    assert gemini_api_client._parse_iso_to_epoch("") == 0