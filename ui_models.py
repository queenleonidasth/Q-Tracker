"""Pure presentation models shared by taskbar, tray and dashboard UIs."""

from __future__ import annotations

import re

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable, Optional


@dataclass(frozen=True, slots=True)
class WindowView:
    window_id: str
    label: str
    short_label: str
    remaining_percent: float
    used_percent: float
    reset_at: Optional[str]
    reset_in: str
    severity: str


@dataclass(frozen=True, slots=True)
class ProviderView:
    provider_id: str
    display_name: str
    status: str
    indicator: str
    compact_text: str
    windows: tuple[WindowView, ...]
    source: str
    observed_at: Optional[str]
    refreshed_at: Optional[str]
    age_text: str
    plan: str
    message: str


@dataclass(frozen=True, slots=True)
class TrackerView:
    providers: tuple[ProviderView, ...]
    compact_text: str
    token_totals: dict[str, int]

    @property
    def fingerprint(self) -> tuple[Any, ...]:
        return (
            self.compact_text,
            tuple(
                (
                    provider.provider_id,
                    provider.status,
                    provider.observed_at,
                    tuple((window.window_id, window.reset_in) for window in provider.windows),
                )
                for provider in self.providers
            ),
            tuple(sorted(self.token_totals.items())),
        )


def taskbar_overlay_width(configured_width: int, taskbar_width: int) -> int:
    """Resolve a compact horizontal overlay width within available taskbar space."""
    return min(configured_width, 480, max(240, taskbar_width - 300))


def taskbar_windows(
    provider: ProviderView,
    preferred_ids: Optional[Iterable[str]] = None,
) -> tuple[WindowView, ...]:
    """Return only quota windows requested for the native taskbar overlay."""
    by_id = {window.window_id: window for window in provider.windows}

    def pick(ids: Iterable[str]) -> tuple[WindowView, ...]:
        return tuple(by_id[window_id] for window_id in ids if window_id in by_id)

    preferred = tuple(str(window_id) for window_id in preferred_ids) if preferred_ids else ()
    if preferred:
        selected = pick(preferred)
        if selected:
            return selected
    if provider.provider_id == "agy":
        local_ids = ("session", "weekly")
        if any(window_id in by_id for window_id in local_ids):
            return pick(local_ids)
        return pick(("gemini", "3p"))
    if provider.provider_id == "codex":
        return pick(("session", "weekly")) or provider.windows[:1]
    if provider.provider_id == "gemini":
        return pick(("pro", "flash", "flash_lite"))
    return ()


def context_detail_lines(view: TrackerView) -> tuple[str, ...]:
    """Readable provider detail lines for compact native context surfaces."""
    if not view.providers:
        return ("Waiting for provider data",)
    lines: list[str] = []
    for provider in view.providers:
        source = provider.source or "no source"
        lines.append(
            f"{provider.display_name} · {provider.status} · {source} · confirmed {provider.age_text}"
        )
        for window in provider.windows:
            lines.append(
                f"  {window.label}: {window.remaining_percent:.1f}% left · resets {window.reset_in}"
            )
        if provider.message:
            lines.append(f"  {provider.message}")
    return tuple(lines)


def _parse_timestamp(value: Any) -> Optional[datetime]:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def format_countdown(reset_at: Any, now: Optional[datetime] = None) -> str:
    reset = _parse_timestamp(reset_at)
    if reset is None:
        return "—"
    reference = now or datetime.now(timezone.utc)
    if reference.tzinfo is None:
        reference = reference.replace(tzinfo=timezone.utc)
    seconds = max(0, int((reset.astimezone(timezone.utc) - reference.astimezone(timezone.utc)).total_seconds()))
    if seconds <= 0:
        return "now"
    days, remainder = divmod(seconds, 86_400)
    hours, remainder = divmod(remainder, 3_600)
    minutes = remainder // 60
    if days:
        return f"{days}d {hours}h"
    if hours:
        return f"{hours}h {minutes}m"
    return f"{max(1, minutes)}m"


def format_age(observed_at: Any, now: Optional[datetime] = None) -> str:
    observed = _parse_timestamp(observed_at)
    if observed is None:
        return "never"
    reference = now or datetime.now(timezone.utc)
    if reference.tzinfo is None:
        reference = reference.replace(tzinfo=timezone.utc)
    seconds = max(0, int((reference.astimezone(timezone.utc) - observed.astimezone(timezone.utc)).total_seconds()))
    if seconds < 60:
        return "just now"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes}m ago"
    hours = minutes // 60
    if hours < 48:
        return f"{hours}h ago"
    return f"{hours // 24}d ago"


def format_tokens(value: Any) -> str:
    try:
        number = max(0, int(value))
    except (TypeError, ValueError):
        number = 0
    if number >= 1_000_000:
        return f"{number / 1_000_000:.1f}M"
    if number >= 1_000:
        return f"{number / 1_000:.1f}K"
    return str(number)


def _window_order(window_id: str) -> tuple[int, str]:
    preferred = {"session": 0, "weekly": 1, "monthly": 2, "code_review": 3, "pro": 4, "flash": 5, "flash_lite": 6}
    return preferred.get(window_id, 10), window_id


def _short_label(window_id: str, label: str) -> str:
    if window_id == "session":
        text = str(label or "").strip()
        if re.fullmatch(r"\d+[HhMm]", text):
            return text
        return "5H"
    if window_id == "weekly":
        return "W"
    if window_id == "monthly":
        return "M"
    if window_id == "pro":
        return "Pro"
    if window_id == "flash":
        return "Flash"
    if window_id == "flash_lite":
        return "Lite"
    return label or window_id.replace("_", " ").title()


def _float(value: Any, fallback: float = 0.0) -> float:
    try:
        return max(0.0, min(100.0, float(value)))
    except (TypeError, ValueError):
        return fallback


def countdown_suffix(reset_in: Any) -> str:
    """Compact ``·1h57m`` token for surfaces that show a reset time."""
    text = str(reset_in or "").strip()
    if not text or text in {"-", "—"}:
        return ""
    return f"·{text.replace(' ', '')}"


def build_provider_view(snapshot: dict[str, Any], now: Optional[datetime] = None) -> ProviderView:
    reference = now or datetime.now(timezone.utc)
    provider_id = str(snapshot.get("provider_id") or snapshot.get("id") or "unknown")
    display_name = str(snapshot.get("display_name") or snapshot.get("provider_name") or provider_id.title())
    status = str(snapshot.get("status") or "unavailable")
    indicator = "" if status == "ok" else "~" if status == "stale" else "!"
    raw_windows = snapshot.get("windows") if isinstance(snapshot.get("windows"), dict) else {}
    windows: list[WindowView] = []
    for window_id, raw in sorted(raw_windows.items(), key=lambda item: _window_order(str(item[0]))):
        if not isinstance(raw, dict):
            continue
        remaining = raw.get("remaining_percent")
        if remaining is None and raw.get("used_percent") is not None:
            remaining = 100.0 - _float(raw.get("used_percent"))
        if remaining is None:
            continue
        remaining_value = _float(remaining)
        used_value = _float(raw.get("used_percent"), 100.0 - remaining_value)
        label = str(raw.get("label") or str(window_id).replace("_", " ").title())
        severity = "critical" if remaining_value <= 10 else "warning" if remaining_value <= 20 else "normal"
        windows.append(
            WindowView(
                window_id=str(window_id),
                label=label,
                short_label=_short_label(str(window_id), label),
                remaining_percent=remaining_value,
                used_percent=used_value,
                reset_at=raw.get("reset_at") or raw.get("reset_time"),
                reset_in=format_countdown(raw.get("reset_at") or raw.get("reset_time"), reference),
                severity=severity,
            )
        )

    prefix = display_name + (f" {indicator}" if indicator else "")
    if windows:
        parts: list[str] = []
        for window in windows:
            part = f"{window.remaining_percent:.1f}% {window.short_label}"
            suffix = countdown_suffix(window.reset_in)
            if suffix:
                part += f" {suffix}"
            parts.append(part)
        details = " · ".join(parts)
    else:
        details = "—"
    observed_at = snapshot.get("observed_at") or snapshot.get("fetched_at")
    return ProviderView(
        provider_id=provider_id,
        display_name=display_name,
        status=status,
        indicator=indicator,
        compact_text=f"{prefix} {details}",
        windows=tuple(windows),
        source=str(snapshot.get("source") or ""),
        observed_at=str(observed_at) if observed_at else None,
        refreshed_at=str(snapshot.get("refreshed_at")) if snapshot.get("refreshed_at") else None,
        age_text=format_age(observed_at, reference),
        plan=str(snapshot.get("plan") or snapshot.get("plan_type") or "unknown"),
        message=str(snapshot.get("message") or snapshot.get("error_message") or ""),
    )


def _period_total(period: Any) -> int:
    if not isinstance(period, dict):
        return 0
    return sum(
        max(0, int(value.get("total", 0)))
        for value in period.values()
        if isinstance(value, dict)
    )


def build_tracker_view(
    state: dict[str, Any],
    provider_order: Iterable[str] = ("agy", "codex", "gemini"),
    now: Optional[datetime] = None,
) -> TrackerView:
    reference = now or datetime.now().astimezone()
    providers_state = state.get("providers") if isinstance(state.get("providers"), dict) else {}
    providers = tuple(
        build_provider_view(providers_state[provider_id], reference)
        for provider_id in provider_order
        if provider_id in providers_state and isinstance(providers_state[provider_id], dict)
    )
    usage = state.get("usage") if isinstance(state.get("usage"), dict) else {}
    today = reference.date().isoformat()
    month = today[:7]
    daily = usage.get("daily") if isinstance(usage.get("daily"), dict) else {}
    monthly = usage.get("monthly") if isinstance(usage.get("monthly"), dict) else {}
    totals = usage.get("total") if isinstance(usage.get("total"), dict) else {}
    token_totals = {
        "today": _period_total(daily.get(today)),
        "month": _period_total(monthly.get(month)),
        "lifetime": _period_total(totals),
    }
    return TrackerView(
        providers=providers,
        compact_text="  |  ".join(provider.compact_text for provider in providers),
        token_totals=token_totals,
    )
