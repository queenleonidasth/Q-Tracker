"""Provider adapters that return one normalized :class:`ProviderSnapshot` shape."""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional

import agy_api_client
import agy_cloud_client
import codex_api_client
import gemini_api_client
from quota_models import (
    FetchStatus,
    ProviderErrorKind,
    ProviderFetchError,
    ProviderSnapshot,
    QuotaWindow,
)


HOME = Path.home()
CODEX_HOME = Path(os.environ.get("CODEX_HOME", HOME / ".codex"))
AGY_QUOTA_CACHE = HOME / ".tokentracker" / "tracker" / "agy_quota_cache.json"
DEFAULT_STALE_SECONDS = 300


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_datetime(value: Any) -> Optional[datetime]:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        try:
            return datetime.fromtimestamp(float(value), tz=timezone.utc)
        except (OSError, OverflowError, ValueError):
            return None
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _window_from_used(
    window_id: str,
    label: str,
    used_percent: Any,
    minutes: Optional[int],
    reset: Any,
    now: datetime,
) -> QuotaWindow:
    used = max(0.0, min(100.0, float(used_percent or 0)))
    remaining = 100.0 - used
    reset_dt = _parse_datetime(reset)
    reset_text = _iso(reset_dt) if reset_dt else ""
    reset_seconds = max(0, int((reset_dt - now).total_seconds())) if reset_dt else 0
    return QuotaWindow(
        window_type=window_id,
        remaining_percent=remaining,
        remaining_fraction=remaining / 100.0,
        reset_time=reset_text,
        reset_in_seconds=reset_seconds,
        label=label,
        window_minutes=minutes,
    )


def _window_label(window_id: str, minutes: Optional[int]) -> str:
    """Derive a human label from the real window duration, e.g. ``4H``/``75m``."""
    if window_id == "weekly":
        return "Weekly"
    if minutes and minutes > 0:
        if minutes % 60 == 0:
            return f"{minutes // 60}H"
        return f"{minutes}m"
    return "5H"


class CodexQuotaSource:
    """Prefer the live Codex usage API, falling back to recent rollout events."""

    def __init__(
        self,
        codex_home: Optional[Path] = None,
        fetch_live: Optional[Callable[[], Optional[dict[str, Any]]]] = None,
        stale_seconds: int = DEFAULT_STALE_SECONDS,
        now: Callable[[], datetime] = _utc_now,
    ):
        self.codex_home = Path(codex_home) if codex_home is not None else CODEX_HOME
        self.fetch_live = fetch_live or codex_api_client.fetch_codex_live_limits
        self.stale_seconds = max(0, int(stale_seconds))
        self.now = now

    def fetch(self) -> ProviderSnapshot:
        fetch_error: Optional[ProviderFetchError] = None
        try:
            live = self.fetch_live()
        except ProviderFetchError as error:
            fetch_error = error
            live = None
        except Exception as error:
            fetch_error = ProviderFetchError(ProviderErrorKind.OTHER, str(error))
            live = None

        if live:
            snapshot = self._from_live(live)
            if snapshot is not None:
                return snapshot

        fallback = self._latest_session_snapshot()
        if fallback is not None:
            if fetch_error is not None:
                return ProviderSnapshot(
                    provider_id="codex",
                    provider_name="Codex",
                    windows=fallback.windows,
                    status=self._status_for_error(fetch_error),
                    source=fallback.source,
                    observed_at=fallback.observed_at,
                    refreshed_at=_iso(self.now()),
                    plan_type=fallback.plan_type,
                    message=f"{fetch_error}; using the latest local Codex session event",
                    error_kind=fetch_error.kind.value,
                )
            return fallback

        if fetch_error is not None:
            return ProviderSnapshot.failure(
                "codex",
                "Codex",
                self._status_for_error(fetch_error),
                str(fetch_error),
                error_kind=fetch_error.kind.value,
            )
        return ProviderSnapshot.failure(
            "codex",
            "Codex",
            FetchStatus.UNAVAILABLE,
            "No Codex quota data is available; sign in to Codex and run it once.",
            error_kind=ProviderErrorKind.AUTH_REQUIRED.value,
        )

    @staticmethod
    def _status_for_error(error: ProviderFetchError) -> FetchStatus:
        if error.kind == ProviderErrorKind.RATE_LIMITED:
            return FetchStatus.RATE_LIMITED
        if error.kind in {
            ProviderErrorKind.AUTH_REQUIRED,
            ProviderErrorKind.NOT_INSTALLED,
            ProviderErrorKind.NOT_RUNNING,
        }:
            return FetchStatus.UNAVAILABLE
        return FetchStatus.ERROR

    def _from_live(self, raw: dict[str, Any]) -> Optional[ProviderSnapshot]:
        normalized = (
            codex_api_client._normalize_usage_response(raw)
            if isinstance(raw.get("rate_limit"), dict)
            else raw
        )
        if not isinstance(normalized, dict) or normalized.get("used_percent") is None:
            return None

        now = self.now().astimezone(timezone.utc)
        candidates: list[tuple[dict[str, Any], str]] = [(normalized, "primary")]
        if isinstance(normalized.get("secondary"), dict):
            candidates.append((normalized["secondary"], "secondary"))
        windows: dict[str, QuotaWindow] = {}
        for value, position in candidates:
            minutes = int(value.get("window_minutes") or (300 if position == "primary" else 10_080))
            window_id = "weekly" if minutes >= 10_000 else "session"
            label = _window_label(window_id, minutes)
            windows[window_id] = _window_from_used(
                window_id,
                label,
                value.get("used_percent"),
                minutes,
                value.get("resets_at") or value.get("reset_at"),
                now,
            )
        if not windows:
            return None
        return ProviderSnapshot(
            provider_id="codex",
            provider_name="Codex",
            windows=windows,
            status=FetchStatus.OK,
            source="live_api",
            observed_at=str(normalized.get("timestamp") or _iso(now)),
            refreshed_at=_iso(now),
            plan_type=str(normalized.get("plan_type") or "chatgpt"),
        )

    def _latest_session_snapshot(self) -> Optional[ProviderSnapshot]:
        candidates: list[tuple[datetime, dict[str, Any]]] = []
        for directory_name in ("sessions", "archived_sessions"):
            directory = self.codex_home / directory_name
            if not directory.is_dir():
                continue
            try:
                files = directory.rglob("*.jsonl")
                for path in files:
                    found = self._latest_in_file(path)
                    if found is not None:
                        candidates.append(found)
            except OSError:
                continue
        if not candidates:
            return None

        event_time, rate_limits = max(candidates, key=lambda item: item[0])
        now = self.now().astimezone(timezone.utc)
        status = (
            FetchStatus.OK
            if max(0.0, (now - event_time).total_seconds()) <= self.stale_seconds
            else FetchStatus.STALE
        )
        windows: dict[str, QuotaWindow] = {}
        for position in ("primary", "secondary"):
            value = rate_limits.get(position)
            if not isinstance(value, dict) or value.get("used_percent") is None:
                continue
            default_minutes = 300 if position == "primary" else 10_080
            minutes = int(value.get("window_minutes") or default_minutes)
            window_id = "weekly" if minutes >= 10_000 else "session"
            windows[window_id] = _window_from_used(
                window_id,
                _window_label(window_id, minutes),
                value.get("used_percent"),
                minutes,
                value.get("resets_at") or value.get("reset_at"),
                now,
            )
        if not windows:
            return None
        return ProviderSnapshot(
            provider_id="codex",
            provider_name="Codex",
            windows=windows,
            status=status,
            source="session_log",
            observed_at=_iso(event_time),
            refreshed_at=_iso(now),
            plan_type=str(rate_limits.get("plan_type") or "unknown"),
            message="Using the latest local Codex session event" if status is FetchStatus.STALE else "",
        )

    @staticmethod
    def _latest_in_file(path: Path) -> Optional[tuple[datetime, dict[str, Any]]]:
        latest: Optional[tuple[datetime, dict[str, Any]]] = None
        try:
            with path.open("r", encoding="utf-8", errors="replace") as handle:
                for line in handle:
                    if "rate_limits" not in line:
                        continue
                    try:
                        entry = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if not isinstance(entry, dict):
                        continue
                    payload = entry.get("payload")
                    if not isinstance(payload, dict):
                        continue
                    info = payload.get("info") if isinstance(payload.get("info"), dict) else {}
                    rate_limits = payload.get("rate_limits") or info.get("rate_limits")
                    event_time = _parse_datetime(entry.get("timestamp"))
                    if isinstance(rate_limits, dict) and event_time is not None:
                        if latest is None or event_time >= latest[0]:
                            latest = event_time, rate_limits
        except OSError:
            return None
        return latest


class AgyQuotaSource:
    """Read AGY quota from the local language server, the cloud API, or cache."""

    def __init__(
        self,
        cache_path: Optional[Path] = None,
        fetch_live: Optional[Callable[[], Optional[dict[str, Any]]]] = None,
        fetch_cloud: Optional[Callable[[], Optional[dict[str, Any]]]] = None,
        stale_seconds: int = DEFAULT_STALE_SECONDS,
        now: Callable[[], datetime] = _utc_now,
    ):
        self.cache_path = Path(cache_path) if cache_path is not None else AGY_QUOTA_CACHE
        self.fetch_live = fetch_live or agy_api_client.fetch_from_running_agy
        self.fetch_cloud = fetch_cloud or agy_cloud_client.fetch_from_cloud
        self.stale_seconds = max(0, int(stale_seconds))
        self.now = now

    def fetch(self) -> ProviderSnapshot:
        try:
            live = self.fetch_live()
        except Exception:
            live = None
        if isinstance(live, dict):
            return self._from_groups(live, FetchStatus.OK, "local_api", self.now())

        try:
            cloud = self.fetch_cloud()
        except Exception:
            cloud = None
        if isinstance(cloud, dict):
            return self._from_groups(cloud, FetchStatus.OK, "cloud_api", self.now())

        if not self.cache_path.exists():
            return ProviderSnapshot.failure(
                "agy",
                "Antigravity",
                FetchStatus.UNAVAILABLE,
                "Antigravity is not running, the cloud API returned no data, "
                "and no quota cache exists.",
                error_kind=ProviderErrorKind.NOT_RUNNING.value,
            )
        try:
            raw = json.loads(self.cache_path.read_text(encoding="utf-8"))
            modified = datetime.fromtimestamp(self.cache_path.stat().st_mtime, tz=timezone.utc)
        except (OSError, json.JSONDecodeError):
            return ProviderSnapshot.failure(
                "agy",
                "Antigravity",
                FetchStatus.ERROR,
                "The Antigravity quota cache is unreadable.",
                error_kind=ProviderErrorKind.PARSE.value,
            )
        now = self.now().astimezone(timezone.utc)
        age = max(0.0, (now - modified).total_seconds())
        status = FetchStatus.OK if age <= self.stale_seconds else FetchStatus.STALE
        return self._from_groups(raw, status, "cache", modified)

    def _from_groups(
        self,
        raw: dict[str, Any],
        status: FetchStatus,
        source: str,
        observed: datetime,
    ) -> ProviderSnapshot:
        groups = raw.get("groups")
        if not isinstance(groups, dict) or not groups:
            return ProviderSnapshot.failure(
                "agy",
                "Antigravity",
                FetchStatus.ERROR,
                "Antigravity returned no quota groups.",
                error_kind=ProviderErrorKind.PARSE.value,
            )
        now = self.now().astimezone(timezone.utc)
        windows: dict[str, QuotaWindow] = {}
        for group_name, group in groups.items():
            if not isinstance(group, dict):
                continue
            lower = str(group_name).lower()
            if "gemini" in lower and ("5h" in lower or "hour" in lower):
                window_id, label, minutes = "session", "Gemini 5H", 300
            elif "gemini" in lower and ("week" in lower or "weekly" in lower):
                window_id, label, minutes = "weekly", "Gemini Weekly", 10_080
            else:
                window_id = re.sub(r"[^a-z0-9]+", "_", lower).strip("_") or "quota"
                explicit_label = str(group.get("label") or "").strip()
                label = (
                    explicit_label
                    or str(group_name).replace("-", " ").title()
                )
                minutes = 300 if "5h" in lower or "hour" in lower else 10_080 if "week" in lower else None
            remaining = group.get("remaining_percent")
            if remaining is None and group.get("remainingFraction") is not None:
                remaining = float(group["remainingFraction"]) * 100.0
            if remaining is None:
                continue
            fraction = group.get("remaining_fraction")
            if fraction is None:
                fraction = float(remaining) / 100.0
            reset = group.get("reset_time") or group.get("resetTime") or ""
            reset_dt = _parse_datetime(reset)
            reset_seconds = max(0, int((reset_dt - now).total_seconds())) if reset_dt else 0
            windows[window_id] = QuotaWindow(
                window_id,
                float(remaining),
                float(fraction),
                _iso(reset_dt) if reset_dt else "",
                reset_seconds,
                label=label,
                window_minutes=minutes,
            )
        if not windows:
            return ProviderSnapshot.failure(
                "agy",
                "Antigravity",
                FetchStatus.ERROR,
                "Antigravity quota groups contained no usable windows.",
                error_kind=ProviderErrorKind.PARSE.value,
            )
        return ProviderSnapshot(
            provider_id="agy",
            provider_name="Antigravity",
            windows=windows,
            status=status,
            source=source,
            observed_at=_iso(observed),
            refreshed_at=_iso(now),
            plan_type=str(raw.get("plan_tier") or "unknown"),
            message="Using the last Antigravity cache" if status is FetchStatus.STALE else "",
        )


class GeminiQuotaSource:
    """Fetch Gemini CLI quota live via the Cloud AI Companion API (no CLI spawned)."""

    FAMILY_LABELS = {
        "pro": "Gemini Pro",
        "flash": "Gemini Flash",
        "flash_lite": "Gemini Flash-Lite",
    }

    def __init__(
        self,
        fetch_live: Optional[Callable[[], Optional[dict[str, Any]]]] = None,
        now: Callable[[], datetime] = _utc_now,
    ):
        self.fetch_live = fetch_live or gemini_api_client.fetch_gemini_live_limits
        self.now = now

    def fetch(self) -> ProviderSnapshot:
        fetch_error: Optional[ProviderFetchError] = None
        try:
            live = self.fetch_live()
        except ProviderFetchError as error:
            fetch_error = error
            live = None
        except Exception as error:
            fetch_error = ProviderFetchError(ProviderErrorKind.OTHER, str(error))
            live = None

        if live:
            snapshot = self._from_live(live)
            if snapshot is not None:
                return snapshot

        if fetch_error is not None:
            return ProviderSnapshot.failure(
                "gemini",
                "Gemini",
                self._status_for_error(fetch_error),
                str(fetch_error),
                error_kind=fetch_error.kind.value,
            )
        return ProviderSnapshot.failure(
            "gemini",
            "Gemini",
            FetchStatus.UNAVAILABLE,
            "No Gemini quota data is available; sign in to the Gemini CLI once.",
            error_kind=ProviderErrorKind.AUTH_REQUIRED.value,
        )

    @staticmethod
    def _status_for_error(error: ProviderFetchError) -> FetchStatus:
        if error.kind == ProviderErrorKind.RATE_LIMITED:
            return FetchStatus.RATE_LIMITED
        if error.kind in {
            ProviderErrorKind.AUTH_REQUIRED,
            ProviderErrorKind.NOT_INSTALLED,
            ProviderErrorKind.NOT_RUNNING,
        }:
            return FetchStatus.UNAVAILABLE
        return FetchStatus.ERROR

    def _from_live(self, raw: dict[str, Any]) -> Optional[ProviderSnapshot]:
        models = raw.get("models") if isinstance(raw.get("models"), dict) else {}
        now = self.now().astimezone(timezone.utc)
        windows: dict[str, QuotaWindow] = {}
        for family, value in models.items():
            if not isinstance(value, dict) or value.get("used_percent") is None:
                continue
            windows[str(family)] = _window_from_used(
                str(family),
                self.FAMILY_LABELS.get(str(family), str(family).replace("_", " ").title()),
                value.get("used_percent"),
                None,
                value.get("resets_at") or value.get("reset_time"),
                now,
            )
        if not windows:
            return None
        return ProviderSnapshot(
            provider_id="gemini",
            provider_name="Gemini",
            windows=windows,
            status=FetchStatus.OK,
            source="live_api",
            observed_at=str(raw.get("timestamp") or _iso(now)),
            refreshed_at=_iso(now),
            plan_type=str(raw.get("plan_type") or "unknown"),
        )
