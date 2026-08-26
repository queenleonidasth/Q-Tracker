import json
from datetime import datetime, timezone

import agy_cloud_client
from agy_cloud_client import (
    aggregate_model_quota,
    fetch_from_cloud,
    load_stored_token,
    parse_expiry,
    parse_quota_groups,
)

NOW = datetime(2026, 8, 22, 12, 0, tzinfo=timezone.utc)

SUMMARY_GROUPS = [
    {
        "displayName": "Gemini Models",
        "buckets": [
            {
                "bucketId": "gemini-5h",
                "window": "5h",
                "resetTime": "2026-08-22T14:09:17Z",
                "remainingFraction": 0.9755764,
            },
            {
                "bucketId": "gemini-weekly",
                "window": "weekly",
                "resetTime": "2026-08-26T11:22:25Z",
                "remainingFraction": 0.87544805,
            },
        ],
    },
    {
        "displayName": "Claude and GPT models",
        "buckets": [
            {
                "bucketId": "3p-5h",
                "window": "5h",
                "resetTime": "2026-08-22T17:11:49Z",
                "remainingFraction": 1,
            },
            {
                "bucketId": "3p-weekly",
                "window": "weekly",
                "resetTime": "2026-08-29T12:11:49Z",
                "remainingFraction": 1,
            },
        ],
    },
]


def test_aggregate_merges_models_by_family_with_binding_minimum():
    models = {
        "gemini-3-flash": {"quotaInfo": {"remainingFraction": 0.9, "resetTime": "2026-08-22T14:09:17Z"}},
        "gemini-3.1-pro-high": {"quotaInfo": {"remainingFraction": 0.4, "resetTime": "2026-08-22T14:09:17Z"}},
        "claude-sonnet-4-6": {"quotaInfo": {"remainingFraction": 1.0, "resetTime": "2026-08-22T16:36:57Z"}},
        "gpt-oss-120b-medium": {"quotaInfo": {"remainingFraction": 0.7, "resetTime": "2026-08-22T16:36:57Z"}},
    }

    groups = aggregate_model_quota(models, now=NOW)

    assert set(groups) == {"gemini", "3p"}
    assert groups["gemini"]["remaining_percent"] == 40.0
    assert groups["gemini"]["remaining_fraction"] == 0.4
    assert groups["gemini"]["label"] == "Gemini"
    assert groups["gemini"]["reset_time"] == "2026-08-22T14:09:17Z"
    assert groups["gemini"]["reset_in_seconds"] == 2 * 3600 + 9 * 60 + 17
    assert groups["3p"]["remaining_percent"] == 70.0
    assert groups["3p"]["label"] == "Claude & GPT"


def test_aggregate_skips_internal_and_quotaless_entries():
    models = {
        "chat_23310": {"quotaInfo": {"remainingFraction": 1}},
        "tab_flash_lite_preview": {"quotaInfo": {"remainingFraction": 1}},
        "gemini-3-flash": {"displayName": "Gemini 3 Flash"},
        "claude-opus-4-6-thinking": {"quotaInfo": {}},
        "broken": None,
    }

    assert aggregate_model_quota(models, now=NOW) == {}


def test_parse_expiry_accepts_offset_and_z_formats():
    offset = parse_expiry("2026-08-22T19:31:01.9830187+07:00")
    zulu = parse_expiry("2026-08-22T14:09:17Z")

    assert offset is not None and offset.utcoffset().total_seconds() == 7 * 3600
    assert zulu == datetime(2026, 8, 22, 14, 9, 17, tzinfo=timezone.utc)
    assert parse_expiry("") is None
    assert parse_expiry("not-a-date") is None


def test_load_stored_token_reads_credential_json(monkeypatch):
    blob = json.dumps(
        {
            "token": {
                "access_token": "ya29.test",
                "refresh_token": "1//0g",
                "expiry": "2026-08-22T19:31:01+07:00",
            },
            "auth_method": "consumer",
        }
    )
    monkeypatch.setattr(agy_cloud_client, "read_credential_blob", lambda target=None: blob)

    token = load_stored_token()

    assert token == {
        "access_token": "ya29.test",
        "refresh_token": "1//0g",
        "expiry": "2026-08-22T19:31:01+07:00",
    }


def test_load_stored_token_returns_none_without_credential(monkeypatch):
    monkeypatch.setattr(agy_cloud_client, "read_credential_blob", lambda target=None: None)

    assert load_stored_token() is None


def test_parse_quota_groups_flattens_buckets_with_labels_and_reset_seconds():
    groups = parse_quota_groups(SUMMARY_GROUPS, now=NOW)

    assert set(groups) == {"gemini-5h", "gemini-weekly", "3p-5h", "3p-weekly"}
    assert groups["gemini-5h"]["remaining_percent"] == 97.6
    assert groups["gemini-5h"]["label"] == "Gemini 5H"
    assert groups["gemini-5h"]["reset_in_seconds"] == 2 * 3600 + 9 * 60 + 17
    assert groups["gemini-weekly"]["remaining_percent"] == 87.5
    assert groups["3p-5h"]["label"] == "Claude & GPT 5H"
    assert groups["3p-weekly"]["reset_in_seconds"] > 0


def test_parse_quota_groups_skips_unusable_buckets():
    raw = [
        {"buckets": [{"bucketId": "", "remainingFraction": 1}, {"remainingFraction": 1}]},
        {"buckets": [{"bucketId": "gemini-5h"}]},
        None,
    ]

    assert parse_quota_groups(raw, now=NOW) == {}


def test_fetch_from_cloud_happy_path_writes_cache(monkeypatch, tmp_path):
    stored = {
        "access_token": "ya29.valid",
        "refresh_token": "1//0g",
        "expiry": "2026-08-22T19:31:01+07:00",
    }
    monkeypatch.setattr(
        agy_cloud_client, "load_stored_token", lambda: dict(stored)
    )
    calls = {}

    def fake_summary(access_token, timeout=agy_cloud_client.REQUEST_TIMEOUT):
        calls["access_token"] = access_token
        calls["endpoint"] = "summary"
        return 200, SUMMARY_GROUPS

    monkeypatch.setattr(agy_cloud_client, "fetch_quota_summary", fake_summary)

    def fail_models(*args, **kwargs):
        raise AssertionError("models fallback must not run when summary succeeds")

    monkeypatch.setattr(agy_cloud_client, "fetch_available_models", fail_models)
    written = {}
    monkeypatch.setattr(agy_cloud_client, "_write_cache", lambda data: written.update(data))

    result = fetch_from_cloud()

    assert calls == {"access_token": "ya29.valid", "endpoint": "summary"}
    assert set(result["groups"]) == {"gemini-5h", "gemini-weekly", "3p-5h", "3p-weekly"}
    assert result["groups"]["gemini-5h"]["remaining_percent"] == 97.6
    assert result["model"] == "AGY (cloud API)"
    assert written.get("groups") == result["groups"]


def test_fetch_from_cloud_falls_back_to_models_endpoint(monkeypatch):
    monkeypatch.setattr(
        agy_cloud_client,
        "load_stored_token",
        lambda: {
            "access_token": "ya29.valid",
            "refresh_token": "",
            "expiry": "2099-01-01T00:00:00Z",
        },
    )
    monkeypatch.setattr(
        agy_cloud_client,
        "fetch_quota_summary",
        lambda access_token, timeout=10.0: (404, []),
    )
    endpoints = []
    monkeypatch.setattr(
        agy_cloud_client,
        "fetch_available_models",
        lambda access_token, timeout=10.0: (
            endpoints.append("models") or (
                200,
                {
                    "gemini-3-flash": {
                        "quotaInfo": {"remainingFraction": 0.5, "resetTime": "2026-08-22T14:09:17Z"}
                    },
                    "claude-sonnet-4-6": {
                        "quotaInfo": {"remainingFraction": 0.25, "resetTime": "2026-08-22T16:36:57Z"}
                    },
                },
            )
        ),
    )
    monkeypatch.setattr(agy_cloud_client, "_write_cache", lambda data: None)

    result = fetch_from_cloud()

    assert endpoints == ["models"]
    assert set(result["groups"]) == {"gemini", "3p"}
    assert result["groups"]["gemini"]["remaining_percent"] == 50.0
    assert result["groups"]["3p"]["remaining_percent"] == 25.0


def test_fetch_from_cloud_refreshes_expired_token(monkeypatch):
    monkeypatch.setattr(
        agy_cloud_client,
        "load_stored_token",
        lambda: {
            "access_token": "ya29.expired",
            "refresh_token": "1//refresh",
            "expiry": "2020-01-01T00:00:00Z",
        },
    )
    refreshed = {}
    monkeypatch.setattr(
        agy_cloud_client,
        "refresh_access_token",
        lambda refresh_token, timeout=10.0: refreshed.setdefault("token", "ya29.fresh"),
    )

    def fake_fetch(access_token, timeout=agy_cloud_client.REQUEST_TIMEOUT):
        refreshed["used"] = access_token
        return 200, {
            "gemini-3-flash": {
                "quotaInfo": {"remainingFraction": 0.8, "resetTime": "2026-08-22T14:09:17Z"}
            }
        }

    monkeypatch.setattr(agy_cloud_client, "fetch_available_models", fake_fetch)
    monkeypatch.setattr(agy_cloud_client, "_write_cache", lambda data: None)

    result = fetch_from_cloud()

    assert refreshed["token"] == "ya29.fresh"
    assert refreshed["used"] == "ya29.fresh"
    assert result["groups"]["gemini"]["remaining_percent"] == 80.0


def test_fetch_from_cloud_returns_none_when_api_fails(monkeypatch):
    monkeypatch.setattr(
        agy_cloud_client,
        "load_stored_token",
        lambda: {
            "access_token": "ya29.x",
            "refresh_token": "",
            "expiry": "2099-01-01T00:00:00Z",
        },
    )
    monkeypatch.setattr(
        agy_cloud_client,
        "fetch_quota_summary",
        lambda access_token, timeout=10.0: (401, []),
    )
    monkeypatch.setattr(
        agy_cloud_client,
        "fetch_available_models",
        lambda access_token, timeout=10.0: (401, {}),
    )

    assert fetch_from_cloud() is None


def test_fetch_from_cloud_returns_none_without_credential(monkeypatch):
    monkeypatch.setattr(agy_cloud_client, "load_stored_token", lambda: None)

    assert fetch_from_cloud() is None
