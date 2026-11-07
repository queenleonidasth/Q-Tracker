"""Normalized, serializable domain models for provider quota snapshots."""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional


def utc_now_iso() -> str:
    """Return an unambiguous UTC timestamp suitable for persisted state."""
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


class FetchStatus(str, enum.Enum):
    """Outcome of a provider observation."""

    OK = "ok"
    STALE = "stale"
    UNAVAILABLE = "unavailable"
    ERROR = "error"
    RATE_LIMITED = "rate_limited"


class ProviderErrorKind(str, enum.Enum):
    NOT_INSTALLED = "not_installed"
    NOT_RUNNING = "not_running"
    AUTH_REQUIRED = "auth_required"
    TIMEOUT = "timeout"
    RATE_LIMITED = "rate_limited"
    PARSE = "parse"
    OTHER = "other"


class ProviderFetchError(RuntimeError):
    """Expected provider failure that can be rendered without a traceback."""

    def __init__(self, kind: ProviderErrorKind, message: str):
        super().__init__(message)
        self.kind = kind


class WindowType(str, enum.Enum):
    FIVE_HOUR = "5h"
    WEEKLY = "weekly"


@dataclass(slots=True)
class QuotaWindow:
    """One normalized rate-limit window, stored primarily as remaining quota."""

    window_type: str
    remaining_percent: float
    remaining_fraction: float
    reset_time: str
    reset_in_seconds: int = 0
    label: str = ""
    window_minutes: Optional[int] = None

    def __post_init__(self) -> None:
        self.window_type = str(self.window_type)
        self.remaining_percent = clamp_percent(self.remaining_percent)
        self.remaining_fraction = max(0.0, min(1.0, float(self.remaining_fraction)))
        self.reset_time = str(self.reset_time or "")
        self.reset_in_seconds = max(0, int(self.reset_in_seconds or 0))
        self.label = str(self.label or self.window_type)
        if self.window_minutes is not None:
            self.window_minutes = max(0, int(self.window_minutes))

    @property
    def used_percent(self) -> float:
        return round(100.0 - self.remaining_percent, 1)

    @property
    def reset_at(self) -> Optional[str]:
        return self.reset_time or None

    def to_dict(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "used_percent": self.used_percent,
            "remaining_percent": self.remaining_percent,
            "remaining_fraction": self.remaining_fraction,
            "window_minutes": self.window_minutes,
            "reset_at": self.reset_at,
            "reset_in_seconds": self.reset_in_seconds,
        }

    @classmethod
    def from_dict(cls, window_type: str, data: dict[str, Any]) -> "QuotaWindow":
        remaining = data.get("remaining_percent")
        if remaining is None:
            remaining = 100.0 - float(data.get("used_percent", 0.0))
        fraction = data.get("remaining_fraction")
        if fraction is None:
            fraction = float(remaining) / 100.0
        return cls(
            window_type=window_type,
            remaining_percent=remaining,
            remaining_fraction=fraction,
            reset_time=data.get("reset_at") or data.get("reset_time") or "",
            reset_in_seconds=data.get("reset_in_seconds", 0),
            label=data.get("label") or window_type,
            window_minutes=data.get("window_minutes"),
        )


@dataclass(slots=True)
class ProviderSnapshot:
    """Point-in-time provider data with explicit provenance and freshness."""

    provider_name: str
    windows: dict[str, QuotaWindow] = field(default_factory=dict)
    fetched_at: str = ""
    status: FetchStatus = FetchStatus.OK
    plan_type: str = "unknown"
    error_message: str = ""
    provider_id: str = ""
    source: str = ""
    observed_at: str = ""
    refreshed_at: str = ""
    message: str = ""
    error_kind: str = ""
    billing: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.status, FetchStatus):
            self.status = FetchStatus(str(self.status))
        if not self.provider_id:
            self.provider_id = self.provider_name.strip().lower().replace(" ", "_")
        if not self.refreshed_at:
            self.refreshed_at = utc_now_iso()
        if not self.observed_at:
            self.observed_at = self.fetched_at
        if not self.fetched_at:
            self.fetched_at = self.observed_at
        if not self.message:
            self.message = self.error_message
        if not self.error_message:
            self.error_message = self.message

    @property
    def is_healthy(self) -> bool:
        return self.status == FetchStatus.OK

    def get_window(self, window_type: str) -> Optional[QuotaWindow]:
        return self.windows.get(window_type)

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider_id": self.provider_id,
            "display_name": self.provider_name,
            "status": self.status.value,
            "source": self.source,
            "observed_at": self.observed_at or None,
            "refreshed_at": self.refreshed_at,
            "plan": self.plan_type,
            "message": self.message,
            "error_kind": self.error_kind or None,
            "billing": self.billing,
            "windows": {key: value.to_dict() for key, value in self.windows.items()},
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ProviderSnapshot":
        provider_id = str(data.get("provider_id") or data.get("id") or "")
        display_name = str(data.get("display_name") or data.get("provider_name") or provider_id)
        raw_windows = data.get("windows") if isinstance(data.get("windows"), dict) else {}
        windows = {
            str(key): QuotaWindow.from_dict(str(key), value)
            for key, value in raw_windows.items()
            if isinstance(value, dict)
        }
        return cls(
            provider_id=provider_id,
            provider_name=display_name,
            windows=windows,
            fetched_at=str(data.get("observed_at") or data.get("fetched_at") or ""),
            observed_at=str(data.get("observed_at") or data.get("fetched_at") or ""),
            refreshed_at=str(data.get("refreshed_at") or ""),
            status=FetchStatus(str(data.get("status") or FetchStatus.UNAVAILABLE.value)),
            source=str(data.get("source") or ""),
            plan_type=str(data.get("plan") or data.get("plan_type") or "unknown"),
            message=str(data.get("message") or data.get("error_message") or ""),
            error_kind=str(data.get("error_kind") or ""),
            billing=data.get("billing") if isinstance(data.get("billing"), dict) else {},
        )

    @classmethod
    def failure(
        cls,
        provider_id: str,
        provider_name: str,
        status: FetchStatus,
        message: str,
        refreshed_at: str = "",
        error_kind: str = "",
    ) -> "ProviderSnapshot":
        return cls(
            provider_id=provider_id,
            provider_name=provider_name,
            status=status,
            message=message,
            refreshed_at=refreshed_at,
            error_kind=error_kind,
        )


def clamp_percent(value: float) -> float:
    return max(0.0, min(100.0, float(value)))


def apply_monotonic_guard(
    new_window: QuotaWindow,
    old_window: Optional[QuotaWindow],
) -> QuotaWindow:
    """Prevent a cached value increasing inside the same reset identity."""
    if (
        old_window is None
        or not new_window.reset_time
        or not old_window.reset_time
        or new_window.reset_time != old_window.reset_time
    ):
        return new_window

    remaining_percent = min(new_window.remaining_percent, old_window.remaining_percent)
    remaining_fraction = min(new_window.remaining_fraction, old_window.remaining_fraction)
    return QuotaWindow(
        window_type=new_window.window_type,
        remaining_percent=remaining_percent,
        remaining_fraction=remaining_fraction,
        reset_time=new_window.reset_time,
        reset_in_seconds=new_window.reset_in_seconds,
        label=new_window.label,
        window_minutes=new_window.window_minutes,
    )
