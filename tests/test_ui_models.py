from datetime import datetime, timezone

from ui_models import (
    _short_label,
    build_provider_view,
    build_tracker_view,
    context_detail_lines,
    format_countdown,
    format_tokens,
    taskbar_overlay_width,
    taskbar_windows,
)


NOW = datetime(2026, 8, 10, 10, 0, tzinfo=timezone.utc)


def _provider(status="ok", windows=None, **overrides):
    value = {
        "provider_id": "codex",
        "display_name": "Codex",
        "status": status,
        "source": "live_api",
        "observed_at": "2026-08-10T09:59:00Z",
        "refreshed_at": "2026-08-10T10:00:00Z",
        "plan": "plus",
        "message": "",
        "windows": windows or {},
    }
    value.update(overrides)
    return value


def _window(label, remaining, reset="2026-08-10T12:30:00Z"):
    return {
        "label": label,
        "used_percent": 100 - remaining,
        "remaining_percent": remaining,
        "window_minutes": 300 if label == "5H" else 10_080,
        "reset_at": reset,
    }


def test_stale_missing_quota_never_defaults_to_full():
    """Unavailable windows must render an em dash rather than a reassuring 100 percent."""
    view = build_provider_view(_provider(status="stale"), NOW)

    assert view.compact_text == "Codex ~ —"
    assert view.indicator == "~"


def test_error_last_good_values_keep_numbers_with_error_indicator():
    """Last-good quota remains useful only when the current error is visible beside it."""
    view = build_provider_view(
        _provider(status="error", windows={"session": _window("5H", 42)}, message="Timed out"),
        NOW,
    )

    assert view.compact_text == "Codex ! 42.0% 5H ·2h30m"
    assert view.message == "Timed out"


def test_countdown_formats_days_hours_minutes_and_expired_values():
    """Reset labels should remain compact and never show negative durations."""
    assert format_countdown("2026-08-10T12:30:00Z", NOW) == "2h 30m"
    assert format_countdown("2026-08-12T12:00:00Z", NOW) == "2d 2h"
    assert format_countdown("2026-08-10T09:59:00Z", NOW) == "now"
    assert format_countdown("bad", NOW) == "—"


def test_window_order_and_colors_are_based_on_remaining_quota():
    """Provider response order must not swap session/weekly display or warning severity."""
    view = build_provider_view(
        _provider(
            windows={
                "weekly": _window("Weekly", 8),
                "session": _window("5H", 19),
                "code_review": _window("Review", 75),
            }
        ),
        NOW,
    )

    assert [window.window_id for window in view.windows] == ["session", "weekly", "code_review"]
    assert [window.severity for window in view.windows] == ["warning", "critical", "normal"]
    assert view.compact_text == (
        "Codex 19.0% 5H ·2h30m · 8.0% W ·2h30m · 75.0% Review ·2h30m"
    )


def test_taskbar_windows_select_only_agy_session_and_weekly():
    """Adding an AGY quota group must not make the native taskbar overflow again."""
    provider = build_provider_view(
        _provider(
            provider_id="agy",
            display_name="Antigravity",
            windows={
                "3p_weekly": _window("3P", 70),
                "weekly": _window("Weekly", 80),
                "session": _window("5H", 90),
            },
        ),
        NOW,
    )

    assert [window.window_id for window in taskbar_windows(provider)] == ["session", "weekly"]
    assert [window.short_label for window in taskbar_windows(provider)] == ["5H", "W"]
    assert [window.window_id for window in provider.windows] == ["session", "weekly", "3p_weekly"]


def test_taskbar_windows_fall_back_to_cloud_family_ids():
    """Cloud-sourced AGY snapshots (gemini/3p) must still reach the native taskbar."""
    provider = build_provider_view(
        _provider(
            provider_id="agy",
            display_name="Antigravity",
            windows={"gemini": _window("Gemini", 97.6), "3p": _window("Claude & GPT", 100)},
        ),
        NOW,
    )

    assert [window.window_id for window in taskbar_windows(provider)] == ["gemini", "3p"]


def test_taskbar_windows_select_codex_session_then_weekly():
    """The Codex 5-hour quota must be visible alongside the weekly window."""
    provider = build_provider_view(
        _provider(windows={"session": _window("5H", 90), "weekly": _window("Weekly", 80)}),
        NOW,
    )

    assert [window.window_id for window in taskbar_windows(provider)] == ["session", "weekly"]
    assert [window.short_label for window in taskbar_windows(provider)] == ["5H", "W"]


def test_taskbar_windows_select_codex_weekly_when_session_missing():
    """Snapshots that only expose the weekly window still reach the taskbar."""
    provider = build_provider_view(_provider(windows={"weekly": _window("Weekly", 80)}), NOW)

    assert [window.window_id for window in taskbar_windows(provider)] == ["weekly"]


def test_short_label_prefers_hour_label_reported_by_source():
    """A 4-hour plan must read 4H on the taskbar instead of a hard-coded 5H."""
    assert _short_label("session", "4H") == "4H"
    assert _short_label("session", "75m") == "75m"
    assert _short_label("session", "") == "5H"
    assert _short_label("session", "Weekly-ish") == "5H"


def test_taskbar_windows_fall_back_for_codex_without_weekly():
    """Codex still shows useful quota when older snapshots lack a weekly window."""
    session = build_provider_view(_provider(windows={"session": _window("5H", 90)}), NOW)
    extra = build_provider_view(_provider(windows={"monthly": _window("Monthly", 70)}), NOW)

    assert [window.window_id for window in taskbar_windows(session)] == ["session"]
    assert [window.window_id for window in taskbar_windows(extra)] == ["monthly"]


def test_taskbar_windows_honors_preferred_ids():
    """Configured preferences must restore a weekly-only Codex taskbar if wanted."""
    provider = build_provider_view(
        _provider(windows={"session": _window("5H", 90), "weekly": _window("Weekly", 80)}),
        NOW,
    )

    preferred = taskbar_windows(provider, preferred_ids=("weekly",))
    unmatched = taskbar_windows(provider, preferred_ids=("code_review",))
    empty = taskbar_windows(provider, preferred_ids=())

    assert [window.window_id for window in preferred] == ["weekly"]
    assert [window.window_id for window in unmatched] == ["session", "weekly"]
    assert [window.window_id for window in empty] == ["session", "weekly"]


def test_taskbar_overlay_width_caps_wide_configuration():
    """The compact horizontal overlay stays within the configured width cap."""
    assert taskbar_overlay_width(configured_width=460, taskbar_width=1920) == 460
    assert taskbar_overlay_width(configured_width=360, taskbar_width=1920) == 360
    assert taskbar_overlay_width(configured_width=1_200, taskbar_width=1920) == 480


def test_tracker_view_uses_configured_order_and_token_totals():
    """Taskbar and dashboard must derive from the same provider order and usage state."""
    state = {
        "providers": {
            "codex": _provider(windows={"weekly": _window("Weekly", 80)}),
            "agy": {
                "provider_id": "agy",
                "display_name": "Antigravity",
                "status": "unavailable",
                "source": "",
                "observed_at": None,
                "refreshed_at": "2026-08-10T10:00:00Z",
                "plan": "unknown",
                "message": "Not running",
                "windows": {},
            },
        },
        "usage": {
            "daily": {"2026-08-10": {"codex": {"total": 1_250}}},
            "monthly": {"2026-08": {"codex": {"total": 2_500}}},
            "total": {"codex": {"total": 12_500}},
        },
    }

    view = build_tracker_view(state, provider_order=("agy", "codex"), now=NOW)

    assert [provider.provider_id for provider in view.providers] == ["agy", "codex"]
    assert view.compact_text == "Antigravity ! —  |  Codex 80.0% W ·2h30m"
    assert view.token_totals == {"today": 1_250, "month": 2_500, "lifetime": 12_500}


def test_format_tokens_uses_readable_units_without_hiding_small_values():
    assert format_tokens(999) == "999"
    assert format_tokens(1_250) == "1.2K"
    assert format_tokens(2_500_000) == "2.5M"


def test_context_details_include_source_freshness_reset_and_error():
    provider = build_provider_view(
        _provider(
            status="error",
            observed_at="2026-08-10T08:00:00Z",
            windows={"session": _window("5H", 42)},
            message="Timed out",
        ),
        NOW,
    )
    view = build_tracker_view(
        {"providers": {"codex": _provider()}, "usage": {}},
        provider_order=(),
        now=NOW,
    )
    view = type(view)(providers=(provider,), compact_text=provider.compact_text, token_totals=view.token_totals)

    lines = context_detail_lines(view)

    assert "live_api" in lines[0]
    assert "2h ago" in lines[0]
    assert "resets 2h 30m" in lines[1]
    assert lines[2] == "  Timed out"
