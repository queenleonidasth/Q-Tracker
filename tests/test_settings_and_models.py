import json
import sys
from datetime import datetime, timezone

import app_paths
from quota_models import FetchStatus, ProviderSnapshot, QuotaWindow
from settings import Settings


def test_snapshot_round_trip_preserves_windows_source_and_timestamps():
    """Dropping provenance during serialization would make fresh data indistinguishable from cache."""
    window = QuotaWindow(
        window_type="session",
        remaining_percent=75.0,
        remaining_fraction=0.75,
        reset_time="2026-08-10T12:00:00Z",
        reset_in_seconds=7_200,
        label="5H",
        window_minutes=300,
    )
    snapshot = ProviderSnapshot(
        provider_id="codex",
        provider_name="Codex",
        windows={"session": window},
        observed_at="2026-08-10T10:00:00Z",
        refreshed_at="2026-08-10T10:00:01Z",
        status=FetchStatus.OK,
        source="live_api",
        plan_type="plus",
    )

    restored = ProviderSnapshot.from_dict(snapshot.to_dict())

    assert restored == snapshot
    assert restored.windows["session"].used_percent == 25.0


def test_snapshot_failure_does_not_invent_quota_windows():
    """An unavailable provider must not silently look like 100 percent remaining."""
    snapshot = ProviderSnapshot.failure(
        provider_id="agy",
        provider_name="Antigravity",
        status=FetchStatus.UNAVAILABLE,
        message="Antigravity is not running",
        refreshed_at="2026-08-10T10:00:00Z",
    )

    assert snapshot.windows == {}
    assert snapshot.to_dict()["windows"] == {}
    assert snapshot.message == "Antigravity is not running"


def test_settings_clamps_refresh_and_normalizes_thresholds(tmp_path):
    """Out-of-range config must not create a hot polling loop or duplicate notifications."""
    path = tmp_path / "config.json"
    path.write_text(
        json.dumps(
            {
                "refresh_interval_seconds": 1,
                "notifications": {"thresholds": [20, 20, -1, 101, 5]},
            }
        ),
        encoding="utf-8",
    )

    settings = Settings.load(path)

    assert settings.refresh_interval_seconds == 30
    assert settings.notification_thresholds == (20, 5)
    persisted = json.loads(path.read_text(encoding="utf-8"))
    assert persisted["refresh_interval_seconds"] == 30
    assert persisted["notifications"]["thresholds"] == [20, 5]


def test_settings_recovers_invalid_json_with_safe_defaults(tmp_path):
    """A hand-edited config syntax error must not prevent the tracker from starting."""
    path = tmp_path / "config.json"
    path.write_text("{broken", encoding="utf-8")

    settings = Settings.load(path)

    assert settings.refresh_interval_seconds == 60
    assert settings.enabled_providers == ("agy", "codex", "gemini")
    assert json.loads(path.read_text(encoding="utf-8"))["schema_version"] == 1


def test_settings_accepts_legacy_display_and_provider_shape(tmp_path):
    """Existing user display choices must survive the settings migration."""
    path = tmp_path / "config.json"
    path.write_text(
        json.dumps(
            {
                "providers": {
                    "AGY": {"color": "#123456", "icon": "A"},
                    "Codex": {"color": "#654321", "icon": "C"},
                },
                "display": {"font_size": 22, "width": 480, "show_percent_left": True},
            }
        ),
        encoding="utf-8",
    )

    settings = Settings.load(path)

    assert settings.display["font_size"] == 22
    assert settings.display["width"] == 480
    assert settings.provider_styles["agy"]["color"] == "#123456"
    assert settings.provider_styles["codex"]["icon"] == "C"


def test_settings_validate_taskbar_window_map(tmp_path):
    """Only known providers and window ids may steer the taskbar selection."""
    path = tmp_path / "config.json"
    path.write_text(
        json.dumps(
            {
                "display": {
                    "taskbar_windows": {
                        "codex": ["weekly"],
                        "agy": ["bogus"],
                        "gemini": [],
                        "unknown_provider": ["session"],
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    settings = Settings.load(path)

    assert settings.display["taskbar_windows"] == {"codex": ("weekly",)}
    persisted = json.loads(path.read_text(encoding="utf-8"))
    assert persisted["display"]["taskbar_windows"] == {"codex": ["weekly"]}


def test_snapshot_default_timestamp_is_timezone_aware():
    """Naive timestamps would make freshness comparisons depend on the machine timezone."""
    snapshot = ProviderSnapshot.failure("codex", "Codex", FetchStatus.ERROR, "boom")

    parsed = datetime.fromisoformat(snapshot.refreshed_at.replace("Z", "+00:00"))
    assert parsed.tzinfo == timezone.utc


def test_runtime_dir_honors_explicit_override(monkeypatch, tmp_path):
    """Tests and portable installs must not accidentally write into the source data directory."""
    target = tmp_path / "runtime"
    monkeypatch.setenv("AI_USAGE_TRACKER_DATA_DIR", str(target))

    assert app_paths.runtime_dir() == target.resolve()


def test_source_child_command_uses_current_interpreter_and_app_entrypoint():
    """Opening the dashboard through a .py file association would fail on this machine."""
    command = app_paths.build_child_command("--dashboard")

    assert command[0] == sys.executable
    assert command[1].endswith("app.py")
    assert command[2:] == ["--dashboard"]
