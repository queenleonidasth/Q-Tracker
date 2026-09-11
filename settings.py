"""Validated user settings with migration from the original config shape."""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any


DEFAULT_PROVIDER_STYLES = {
    "agy": {"display_name": "Antigravity", "color": "#F4C76B", "icon": "A"},
    "codex": {"display_name": "Codex", "color": "#F4C76B", "icon": "C"},
    "gemini": {"display_name": "Gemini", "color": "#FFB74D", "icon": "G"},
}

DEFAULT_ENABLED_PROVIDERS = ("agy", "codex")

TASKBAR_WINDOW_IDS = frozenset(
    {"session", "weekly", "monthly", "code_review", "pro", "flash", "flash_lite"}
)

DEFAULT_DISPLAY = {
    "font_size": 18,
    "font_name": "Segoe UI Variable Display",
    "separator_color": [180, 180, 200],
    "width": 460,
    "update_interval_ms": 1_000,
    "show_percent_left": True,
    "taskbar_windows": {},
}


def _clamp_int(value: Any, minimum: int, maximum: int, fallback: int) -> int:
    try:
        return max(minimum, min(maximum, int(value)))
    except (TypeError, ValueError):
        return fallback


def _atomic_json_write(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.stem}-", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, indent=2, ensure_ascii=False)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except Exception:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


@dataclass(frozen=True, slots=True)
class Settings:
    refresh_interval_seconds: int
    notification_thresholds: tuple[int, ...]
    enabled_providers: tuple[str, ...]
    display: dict[str, Any]
    provider_styles: dict[str, dict[str, Any]]
    config_path: Path

    @classmethod
    def load(cls, path: Path) -> "Settings":
        path = Path(path)
        try:
            raw = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
            if not isinstance(raw, dict):
                raw = {}
        except (OSError, json.JSONDecodeError):
            raw = {}

        refresh = _clamp_int(raw.get("refresh_interval_seconds"), 30, 3_600, 60)

        notification_raw = raw.get("notifications")
        if not isinstance(notification_raw, dict):
            notification_raw = {}
        thresholds_raw = notification_raw.get("thresholds", [20, 10, 5])
        if not isinstance(thresholds_raw, (list, tuple)):
            thresholds_raw = [20, 10, 5]
        thresholds: set[int] = set()
        for value in thresholds_raw:
            try:
                parsed = int(value)
            except (TypeError, ValueError):
                continue
            if 1 <= parsed <= 99:
                thresholds.add(parsed)

        styles = {key: dict(value) for key, value in DEFAULT_PROVIDER_STYLES.items()}
        legacy_providers = raw.get("providers")
        if isinstance(legacy_providers, dict):
            for name, data in legacy_providers.items():
                provider_id = str(name).strip().lower()
                if provider_id == "antigravity":
                    provider_id = "agy"
                if provider_id in styles and isinstance(data, dict):
                    for key in ("color", "icon", "display_name"):
                        if data.get(key) not in (None, ""):
                            styles[provider_id][key] = data[key]

        enabled_raw = raw.get("enabled_providers")
        if isinstance(enabled_raw, (list, tuple)):
            enabled = tuple(
                provider_id
                for provider_id in (str(value).strip().lower() for value in enabled_raw)
                if provider_id in styles
            )
        else:
            enabled = DEFAULT_ENABLED_PROVIDERS
        if not enabled:
            enabled = DEFAULT_ENABLED_PROVIDERS

        display = dict(DEFAULT_DISPLAY)
        raw_display = raw.get("display")
        if isinstance(raw_display, dict):
            display.update({key: value for key, value in raw_display.items() if key in display})
        display["font_size"] = _clamp_int(display.get("font_size"), 8, 36, 18)
        display["width"] = _clamp_int(display.get("width"), 240, 1_200, 460)
        display["update_interval_ms"] = _clamp_int(
            display.get("update_interval_ms"), 500, 60_000, 1_000
        )
        display["show_percent_left"] = bool(display.get("show_percent_left", True))

        raw_taskbar_windows = display.get("taskbar_windows")
        clean_taskbar_windows: dict[str, tuple[str, ...]] = {}
        if isinstance(raw_taskbar_windows, dict):
            for provider_id, window_ids in raw_taskbar_windows.items():
                normalized_provider = str(provider_id).strip().lower()
                if normalized_provider not in styles or not isinstance(window_ids, (list, tuple)):
                    continue
                sequence = tuple(
                    window_id
                    for window_id in (str(value).strip().lower() for value in window_ids)
                    if window_id in TASKBAR_WINDOW_IDS
                )
                if sequence:
                    clean_taskbar_windows[normalized_provider] = sequence
        display["taskbar_windows"] = clean_taskbar_windows

        settings = cls(
            refresh_interval_seconds=refresh,
            notification_thresholds=tuple(sorted(thresholds, reverse=True)),
            enabled_providers=enabled,
            display=display,
            provider_styles=styles,
            config_path=path,
        )
        _atomic_json_write(path, settings.to_dict())
        return settings

    def to_dict(self) -> dict[str, Any]:
        providers = {
            {"agy": "AGY", "codex": "Codex", "gemini": "Gemini"}.get(
                provider_id, provider_id.title()
            ): {
                key: value
                for key, value in style.items()
                if key in {"color", "icon", "display_name"}
            }
            for provider_id, style in self.provider_styles.items()
        }
        return {
            "schema_version": 1,
            "refresh_interval_seconds": self.refresh_interval_seconds,
            "enabled_providers": list(self.enabled_providers),
            "notifications": {"thresholds": list(self.notification_thresholds)},
            "providers": providers,
            "display": self.display,
        }
