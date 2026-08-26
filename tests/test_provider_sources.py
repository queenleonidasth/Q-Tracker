import json
import os
from datetime import datetime, timedelta, timezone

from quota_models import FetchStatus
from quota_models import ProviderErrorKind, ProviderFetchError
from quota_sources import AgyQuotaSource, CodexQuotaSource, GeminiQuotaSource


NOW = datetime(2026, 8, 10, 10, 0, tzinfo=timezone.utc)


def _wham_window(used, seconds, reset):
    return {
        "used_percent": used,
        "limit_window_seconds": seconds,
        "reset_at": reset,
    }


def test_codex_live_windows_are_classified_by_duration_not_position(tmp_path):
    """A backend field-order change must not swap the 5-hour and weekly labels."""
    body = {
        "rate_limit": {
            "primary_window": _wham_window(60, 604_800, "2026-08-15T00:00:00Z"),
            "secondary_window": _wham_window(25, 18_000, "2026-08-10T12:00:00Z"),
        }
    }
    source = CodexQuotaSource(
        fetch_live=lambda: body,
        codex_home=tmp_path,
        now=lambda: NOW,
    )

    snapshot = source.fetch()

    assert snapshot.status is FetchStatus.OK
    assert snapshot.source == "live_api"
    assert snapshot.windows["session"].remaining_percent == 75
    assert snapshot.windows["session"].window_minutes == 300
    assert snapshot.windows["weekly"].remaining_percent == 40
    assert snapshot.windows["weekly"].window_minutes == 10_080


def test_codex_session_fallback_selects_newest_event_timestamp(tmp_path):
    """Filename ordering must not make an older quota event win."""
    day = tmp_path / "sessions" / "2026" / "08" / "10"
    day.mkdir(parents=True)
    older = {
        "timestamp": "2026-08-10T08:00:00Z",
        "type": "event_msg",
        "payload": {
            "rate_limits": {
                "primary": {"used_percent": 10, "window_minutes": 300, "resets_at": 1_786_350_000},
                "secondary": {"used_percent": 20, "window_minutes": 10_080, "resets_at": 1_786_700_000},
                "plan_type": "plus",
            }
        },
    }
    newer = {
        "timestamp": "2026-08-10T09:30:00Z",
        "type": "event_msg",
        "payload": {
            "rate_limits": {
                "primary": {"used_percent": 35, "window_minutes": 300, "resets_at": 1_786_350_000},
                "secondary": {"used_percent": 45, "window_minutes": 10_080, "resets_at": 1_786_700_000},
                "plan_type": "plus",
            }
        },
    }
    (day / "rollout-z-older.jsonl").write_text(json.dumps(older) + "\n", encoding="utf-8")
    (day / "rollout-a-newer.jsonl").write_text(json.dumps(newer) + "\n", encoding="utf-8")
    source = CodexQuotaSource(
        fetch_live=lambda: None,
        codex_home=tmp_path,
        stale_seconds=7_200,
        now=lambda: NOW,
    )

    snapshot = source.fetch()

    assert snapshot.status is FetchStatus.OK
    assert snapshot.source == "session_log"
    assert snapshot.observed_at == "2026-08-10T09:30:00Z"
    assert snapshot.windows["session"].remaining_percent == 65
    assert snapshot.windows["weekly"].remaining_percent == 55


def test_codex_without_live_or_session_data_is_unavailable_not_full(tmp_path):
    """Missing data must not render as a healthy 100-percent quota."""
    snapshot = CodexQuotaSource(
        fetch_live=lambda: None,
        codex_home=tmp_path,
        now=lambda: NOW,
    ).fetch()

    assert snapshot.status is FetchStatus.UNAVAILABLE
    assert snapshot.windows == {}
    assert "No Codex quota data" in snapshot.message


def test_codex_rate_limit_keeps_failure_status_with_session_fallback_values(tmp_path):
    """Fallback values are useful, but must not hide the live 429 cache policy."""
    day = tmp_path / "sessions" / "2026" / "08" / "10"
    day.mkdir(parents=True)
    event = {
        "timestamp": "2026-08-10T09:30:00Z",
        "type": "event_msg",
        "payload": {
            "rate_limits": {
                "primary": {
                    "used_percent": 35,
                    "window_minutes": 300,
                    "resets_at": 1_786_350_000,
                }
            }
        },
    }
    (day / "rollout.jsonl").write_text(json.dumps(event) + "\n", encoding="utf-8")

    def rate_limited():
        raise ProviderFetchError(ProviderErrorKind.RATE_LIMITED, "Too many requests")

    snapshot = CodexQuotaSource(
        fetch_live=rate_limited,
        codex_home=tmp_path,
        now=lambda: NOW,
    ).fetch()

    assert snapshot.status is FetchStatus.RATE_LIMITED
    assert snapshot.windows["session"].remaining_percent == 65
    assert snapshot.source == "session_log"
    assert snapshot.error_kind == ProviderErrorKind.RATE_LIMITED.value


def test_codex_weekly_only_response_does_not_invent_a_session_window(tmp_path):
    """Plans that temporarily expose only a weekly limit must still render that real window."""
    body = {
        "rate_limit": {
            "primary_window": _wham_window(35, 604_800, "2026-08-15T00:00:00Z"),
            "secondary_window": None,
        }
    }

    snapshot = CodexQuotaSource(
        fetch_live=lambda: body,
        codex_home=tmp_path,
        now=lambda: NOW,
    ).fetch()

    assert snapshot.status is FetchStatus.OK
    assert set(snapshot.windows) == {"weekly"}
    assert snapshot.windows["weekly"].remaining_percent == 65


def test_agy_live_groups_map_gemini_windows_and_preserve_extras(tmp_path):
    """Plan-aware AGY buckets must keep session, weekly and non-Gemini windows distinct."""
    live = {
        "plan_tier": "Google AI Pro",
        "groups": {
            "gemini-5h": {
                "remaining_percent": 80,
                "remaining_fraction": 0.8,
                "reset_time": "2026-08-10T12:00:00Z",
            },
            "gemini-weekly": {
                "remaining_percent": 55,
                "remaining_fraction": 0.55,
                "reset_time": "2026-08-15T00:00:00Z",
            },
            "3p-weekly": {
                "remaining_percent": 25,
                "remaining_fraction": 0.25,
                "reset_time": "2026-08-15T00:00:00Z",
            },
        },
    }
    snapshot = AgyQuotaSource(
        fetch_live=lambda: live,
        cache_path=tmp_path / "agy.json",
        now=lambda: NOW,
    ).fetch()

    assert snapshot.status is FetchStatus.OK
    assert snapshot.source == "local_api"
    assert snapshot.plan_type == "Google AI Pro"
    assert snapshot.windows["session"].remaining_percent == 80
    assert snapshot.windows["weekly"].remaining_percent == 55
    assert snapshot.windows["3p_weekly"].remaining_percent == 25


def test_agy_old_cache_is_stale_and_uses_cache_observation_time(tmp_path):
    """Touching tracker state must not make an old provider observation look fresh."""
    cache_path = tmp_path / "agy.json"
    cache_path.write_text(
        json.dumps(
            {
                "plan_tier": "Pro",
                "groups": {
                    "gemini-weekly": {
                        "remaining_percent": 40,
                        "remaining_fraction": 0.4,
                        "reset_time": "2026-08-15T00:00:00Z",
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    old_time = (NOW - timedelta(minutes=10)).timestamp()
    os.utime(cache_path, (old_time, old_time))

    snapshot = AgyQuotaSource(
        fetch_live=lambda: None,
        fetch_cloud=lambda: None,
        cache_path=cache_path,
        stale_seconds=300,
        now=lambda: NOW,
    ).fetch()

    assert snapshot.status is FetchStatus.STALE
    assert snapshot.source == "cache"
    assert snapshot.windows["weekly"].remaining_percent == 40
    observed = datetime.fromisoformat(snapshot.observed_at.replace("Z", "+00:00"))
    assert int((NOW - observed).total_seconds()) == 600


def test_agy_missing_cache_and_live_process_is_unavailable(tmp_path):
    """AGY being closed is an expected unavailable state, not a fake empty plan."""
    snapshot = AgyQuotaSource(
        fetch_live=lambda: None,
        fetch_cloud=lambda: None,
        cache_path=tmp_path / "missing.json",
        now=lambda: NOW,
    ).fetch()

    assert snapshot.status is FetchStatus.UNAVAILABLE
    assert snapshot.windows == {}
    assert "not running" in snapshot.message.lower()


def test_agy_cloud_fallback_works_without_running_process(tmp_path):
    """The stored-OAuth cloud API must satisfy quota even when agy.exe is closed."""
    cloud = {
        "plan_tier": "?",
        "groups": {
            "gemini": {
                "remaining_percent": 98.6,
                "remaining_fraction": 0.986,
                "reset_time": "2026-08-22T14:09:17Z",
                "label": "Gemini",
            },
            "3p": {
                "remaining_percent": 100.0,
                "remaining_fraction": 1.0,
                "reset_time": "2026-08-22T16:36:57Z",
                "label": "Claude & GPT",
            },
        },
    }
    snapshot = AgyQuotaSource(
        fetch_live=lambda: None,
        fetch_cloud=lambda: cloud,
        cache_path=tmp_path / "missing.json",
        now=lambda: NOW,
    ).fetch()

    assert snapshot.status is FetchStatus.OK
    assert snapshot.source == "cloud_api"
    assert snapshot.windows["gemini"].remaining_percent == 98.6
    assert snapshot.windows["gemini"].label == "Gemini"
    assert snapshot.windows["3p"].remaining_percent == 100.0
    assert snapshot.windows["3p"].label == "Claude & GPT"


def test_agy_prefers_cloud_data_over_stale_cache(tmp_path):
    """A stale cache must lose against a successful cloud fetch."""
    cache_path = tmp_path / "agy.json"
    cache_path.write_text(
        json.dumps(
            {
                "groups": {
                    "gemini-weekly": {
                        "remaining_percent": 10,
                        "remaining_fraction": 0.1,
                        "reset_time": "2026-08-15T00:00:00Z",
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    old_time = (NOW - timedelta(hours=5)).timestamp()
    os.utime(cache_path, (old_time, old_time))
    cloud = {
        "groups": {
            "gemini": {
                "remaining_percent": 55.0,
                "remaining_fraction": 0.55,
                "reset_time": "2026-08-10T15:00:00Z",
            }
        }
    }

    snapshot = AgyQuotaSource(
        fetch_live=lambda: None,
        fetch_cloud=lambda: cloud,
        cache_path=cache_path,
        stale_seconds=300,
        now=lambda: NOW,
    ).fetch()

    assert snapshot.status is FetchStatus.OK
    assert snapshot.source == "cloud_api"
    assert snapshot.windows["gemini"].remaining_percent == 55.0


def test_gemini_live_models_map_to_per_family_windows(tmp_path):
    """Per-model families must stay distinct windows, not merge into session/weekly."""
    live = {
        "plan_type": "Paid",
        "timestamp": "2026-08-10T10:00:00Z",
        "models": {
            "pro": {
                "used_percent": 20.0,
                "percent_left": 80.0,
                "remaining_fraction": 0.8,
                "resets_at": 1_786_800_000,
                "model_id": "gemini-2.5-pro",
            },
            "flash": {
                "used_percent": 50.0,
                "percent_left": 50.0,
                "remaining_fraction": 0.5,
                "resets_at": 1_786_800_000,
                "model_id": "gemini-2.5-flash",
            },
        },
    }
    snapshot = GeminiQuotaSource(
        fetch_live=lambda: live,
        now=lambda: NOW,
    ).fetch()

    assert snapshot.status is FetchStatus.OK
    assert snapshot.source == "live_api"
    assert snapshot.plan_type == "Paid"
    assert set(snapshot.windows) == {"pro", "flash"}
    assert snapshot.windows["pro"].remaining_percent == 80
    assert snapshot.windows["flash"].remaining_percent == 50
    assert snapshot.windows["pro"].label == "Gemini Pro"


def test_gemini_without_live_data_is_unavailable_with_auth_hint(tmp_path):
    """Missing credentials must not render as a healthy 100-percent quota."""
    def missing_credentials():
        raise ProviderFetchError(
            ProviderErrorKind.AUTH_REQUIRED,
            "Gemini authentication is unavailable; run 'gemini' and sign in once",
        )

    snapshot = GeminiQuotaSource(
        fetch_live=missing_credentials,
        now=lambda: NOW,
    ).fetch()

    assert snapshot.status is FetchStatus.UNAVAILABLE
    assert snapshot.windows == {}
    assert snapshot.error_kind == ProviderErrorKind.AUTH_REQUIRED.value


def test_gemini_rate_limited_preserves_status_and_error_kind(tmp_path):
    def rate_limited():
        raise ProviderFetchError(ProviderErrorKind.RATE_LIMITED, "Too many requests")

    snapshot = GeminiQuotaSource(
        fetch_live=rate_limited,
        now=lambda: NOW,
    ).fetch()

    assert snapshot.status is FetchStatus.RATE_LIMITED
    assert snapshot.windows == {}
    assert snapshot.error_kind == ProviderErrorKind.RATE_LIMITED.value


def test_codex_source_labels_session_by_real_duration(tmp_path):
    """The 5-hour quota label must follow the duration the backend reports."""
    raw = {
        "used_percent": 12.0,
        "percent_left": 88.0,
        "window_minutes": 240,
        "resets_at": 1_786_350_000,
        "plan_type": "chatgpt",
    }
    snapshot = CodexQuotaSource(fetch_live=lambda: raw, codex_home=tmp_path, now=lambda: NOW).fetch()

    assert snapshot.windows["session"].label == "4H"

    odd = dict(raw, window_minutes=75)
    snapshot = CodexQuotaSource(fetch_live=lambda: odd, codex_home=tmp_path, now=lambda: NOW).fetch()

    assert snapshot.windows["session"].label == "75m"


def test_codex_free_plan_monthly_window_is_not_labeled_weekly(tmp_path):
    """A single 30-day window must report its real duration, not 'Weekly'."""
    raw = {
        "used_percent": 48.0,
        "percent_left": 52.0,
        "window_minutes": 43_200,
        "resets_at": 1_789_895_397,
        "plan_type": "chatgpt",
    }
    snapshot = CodexQuotaSource(fetch_live=lambda: raw, codex_home=tmp_path, now=lambda: NOW).fetch()

    assert snapshot.windows["weekly"].label == "30D"
