"""Provider registry, TTL cache, single-flight refresh and state persistence."""

from __future__ import annotations

import copy
import threading
import time
from dataclasses import replace
from typing import Any, Callable, Mapping, Optional

from codex_usage import CodexUsageScanner
from quota_models import (
    FetchStatus,
    ProviderErrorKind,
    ProviderFetchError,
    ProviderSnapshot,
    apply_monotonic_guard,
    utc_now_iso,
)
from quota_sources import AgyQuotaSource, CodexQuotaSource, GeminiQuotaSource
from state_store import AtomicStateStore, get_store


TTL_SECONDS = {
    FetchStatus.OK: 60.0,
    FetchStatus.STALE: 45.0,
    FetchStatus.UNAVAILABLE: 45.0,
    FetchStatus.ERROR: 45.0,
    FetchStatus.RATE_LIMITED: 300.0,
}

TOKEN_METRICS = ("input", "cached_input", "output", "reasoning_output", "total")


def _token_metrics(value: Any) -> dict[str, int]:
    raw = value if isinstance(value, dict) else {}
    metrics: dict[str, int] = {}
    for key in TOKEN_METRICS:
        try:
            metrics[key] = max(0, int(raw.get(key, 0)))
        except (TypeError, ValueError):
            metrics[key] = 0
    return metrics


def _add_token_metrics(first: Any, second: Any) -> dict[str, int]:
    left, right = _token_metrics(first), _token_metrics(second)
    return {key: left[key] + right[key] for key in TOKEN_METRICS}


def _max_token_metrics(first: Any, second: Any) -> dict[str, int]:
    left, right = _token_metrics(first), _token_metrics(second)
    return {key: max(left[key], right[key]) for key in TOKEN_METRICS}


def _subtract_token_metrics(first: Any, second: Any) -> dict[str, int]:
    left, right = _token_metrics(first), _token_metrics(second)
    return {key: max(0, left[key] - right[key]) for key in TOKEN_METRICS}


class UsageService:
    def __init__(
        self,
        store: AtomicStateStore,
        providers: Mapping[str, Any],
        scanner: Optional[CodexUsageScanner] = None,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.store = store
        self.providers = dict(providers)
        self.scanner = scanner
        self.clock = clock
        self._cache: dict[str, tuple[float, ProviderSnapshot]] = {}
        self._last_good: dict[str, ProviderSnapshot] = {}
        self._locks = {provider_id: threading.Lock() for provider_id in self.providers}
        self._scanner_expires_at = 0.0

    def refresh(
        self, provider_id: Optional[str] = None, force: bool = False
    ) -> dict[str, ProviderSnapshot]:
        target_ids = [provider_id] if provider_id is not None else list(self.providers)
        results = {target: self._refresh_one(target, force=force) for target in target_ids if target in self.providers}
        if "codex" in target_ids and self.scanner is not None:
            self._refresh_scanner(force=force)
        return results

    def snapshot(self, provider_id: str) -> Optional[ProviderSnapshot]:
        cached = self._cache.get(provider_id)
        if cached is not None:
            return copy.deepcopy(cached[1])
        persisted = self.store.load().get("providers", {}).get(provider_id)
        if isinstance(persisted, dict):
            try:
                return ProviderSnapshot.from_dict(persisted)
            except (TypeError, ValueError):
                return None
        return None

    def _refresh_one(self, provider_id: str, force: bool) -> ProviderSnapshot:
        now = self.clock()
        cached = self._cache.get(provider_id)
        if not force and cached is not None and now < cached[0]:
            return copy.deepcopy(cached[1])

        lock = self._locks[provider_id]
        if not lock.acquire(blocking=False):
            existing = self.snapshot(provider_id)
            if existing is not None:
                return existing
            return ProviderSnapshot.failure(
                provider_id,
                provider_id.title(),
                FetchStatus.UNAVAILABLE,
                "Refresh already in progress",
            )

        try:
            now = self.clock()
            cached = self._cache.get(provider_id)
            if not force and cached is not None and now < cached[0]:
                return copy.deepcopy(cached[1])
            previous = self._last_good.get(provider_id) or self.snapshot(provider_id)
            try:
                incoming = self.providers[provider_id].fetch()
                if not isinstance(incoming, ProviderSnapshot):
                    raise ProviderFetchError(
                        ProviderErrorKind.PARSE, "Provider returned an invalid snapshot"
                    )
            except ProviderFetchError as error:
                incoming = self._failure_snapshot(provider_id, error)
            except Exception as error:
                incoming = ProviderSnapshot.failure(
                    provider_id,
                    provider_id.title(),
                    FetchStatus.ERROR,
                    str(error) or error.__class__.__name__,
                    error_kind=ProviderErrorKind.OTHER.value,
                )

            merged = self._merge_with_previous(incoming, previous)
            if merged.status in {FetchStatus.OK, FetchStatus.STALE} and merged.windows:
                self._last_good[provider_id] = copy.deepcopy(merged)
            expires_at = self.clock() + TTL_SECONDS[merged.status]
            self._cache[provider_id] = (expires_at, copy.deepcopy(merged))
            self.store.mutate(
                lambda state: state["providers"].__setitem__(provider_id, merged.to_dict())
            )
            return copy.deepcopy(merged)
        finally:
            lock.release()

    @staticmethod
    def _failure_snapshot(provider_id: str, error: ProviderFetchError) -> ProviderSnapshot:
        status = (
            FetchStatus.RATE_LIMITED
            if error.kind == ProviderErrorKind.RATE_LIMITED
            else FetchStatus.UNAVAILABLE
            if error.kind in {
                ProviderErrorKind.NOT_INSTALLED,
                ProviderErrorKind.NOT_RUNNING,
                ProviderErrorKind.AUTH_REQUIRED,
            }
            else FetchStatus.ERROR
        )
        return ProviderSnapshot.failure(
            provider_id,
            "Antigravity" if provider_id == "agy" else provider_id.title(),
            status,
            str(error),
            error_kind=error.kind.value,
        )

    @staticmethod
    def _merge_with_previous(
        incoming: ProviderSnapshot, previous: Optional[ProviderSnapshot]
    ) -> ProviderSnapshot:
        if previous is None or not previous.windows:
            return incoming
        if incoming.status not in {FetchStatus.OK, FetchStatus.STALE} or not incoming.windows:
            return replace(
                incoming,
                windows=copy.deepcopy(previous.windows),
                source=previous.source,
                observed_at=previous.observed_at,
                fetched_at=previous.observed_at,
                plan_type=previous.plan_type,
            )

        guarded = dict(incoming.windows)
        for window_id, new_window in incoming.windows.items():
            guarded[window_id] = apply_monotonic_guard(
                new_window, previous.windows.get(window_id)
            )
        return replace(incoming, windows=guarded)

    def _refresh_scanner(self, force: bool) -> None:
        now = self.clock()
        if not force and now < self._scanner_expires_at:
            return
        state = self.store.load()
        previous_index = state.get("usage", {}).get("scanner", {}).get("codex")
        try:
            result = self.scanner.scan(previous_index)
        except Exception:
            self._scanner_expires_at = now + 45.0
            return

        def update(current: dict[str, Any]) -> None:
            usage = current["usage"]
            scanner_state = usage["scanner"]
            adjustment = scanner_state.get("codex_adjustment")
            if not isinstance(adjustment, dict):
                adjustment = {"daily": {}, "monthly": {}, "total": {}}
                scanner_state["codex_adjustment"] = adjustment
            for key in ("daily", "monthly", "total"):
                if not isinstance(adjustment.get(key), dict):
                    adjustment[key] = {}
            baseline = scanner_state.get("codex_baseline")
            if not isinstance(baseline, dict):
                # A v3 scanner index means current Codex metrics already came from
                # this scanner.  Without an index, retain migrated/manual metrics
                # as an immutable baseline instead of erasing them.
                already_scanned = (
                    isinstance(previous_index, dict)
                    and previous_index.get("version") == 1
                )
                baseline = {"daily": {}, "monthly": {}, "total": {}}
                if not already_scanned:
                    for period_name in ("daily", "monthly"):
                        adjustment_periods = (
                            adjustment.get(period_name)
                            if isinstance(adjustment.get(period_name), dict)
                            else {}
                        )
                        baseline[period_name] = {
                            period: _subtract_token_metrics(
                                providers.get("codex"), adjustment_periods.get(period)
                            )
                            for period, providers in usage[period_name].items()
                            if isinstance(providers, dict)
                            and isinstance(providers.get("codex"), dict)
                        }
                    baseline["total"] = _subtract_token_metrics(
                        usage["total"].get("codex"), adjustment.get("total")
                    )
                scanner_state["codex_baseline"] = copy.deepcopy(baseline)

            for period_name in ("daily", "monthly"):
                for providers in usage[period_name].values():
                    if isinstance(providers, dict):
                        providers.pop("codex", None)
                source = result.daily if period_name == "daily" else result.monthly
                baseline_periods = (
                    baseline.get(period_name)
                    if isinstance(baseline.get(period_name), dict)
                    else {}
                )
                adjustment_periods = (
                    adjustment.get(period_name)
                    if isinstance(adjustment.get(period_name), dict)
                    else {}
                )
                for period in set(source) | set(baseline_periods) | set(adjustment_periods):
                    usage[period_name].setdefault(period, {})["codex"] = _add_token_metrics(
                        _max_token_metrics(
                            baseline_periods.get(period), source.get(period)
                        ),
                        adjustment_periods.get(period),
                    )
            usage["total"]["codex"] = _add_token_metrics(
                _max_token_metrics(baseline.get("total"), result.total),
                adjustment.get("total"),
            )
            scanner_state["codex"] = copy.deepcopy(result.index)
            scanner_state["codex_diagnostics"] = {
                "files_seen": result.files_seen,
                "files_scanned": result.files_scanned,
                "malformed_lines": result.malformed_lines,
                "updated_at": utc_now_iso(),
                "models": copy.deepcopy(result.models),
            }

        self.store.mutate(update)
        self._scanner_expires_at = self.clock() + 60.0


class RefreshScheduler:
    """One owned, stoppable background refresh loop for the GUI process."""

    def __init__(self, service: UsageService, interval_seconds: float = 60.0):
        self.service = service
        self.interval_seconds = max(0.01, float(interval_seconds))
        self._stop = threading.Event()
        self._lifecycle_lock = threading.Lock()
        self._thread: Optional[threading.Thread] = None

    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        with self._lifecycle_lock:
            if self.is_running:
                return
            self._stop.clear()
            self._thread = threading.Thread(
                target=self._run,
                name="ai-usage-refresh",
                daemon=True,
            )
            self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        with self._lifecycle_lock:
            thread = self._thread
            self._stop.set()
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=max(0.0, float(timeout)))
        with self._lifecycle_lock:
            if self._thread is thread and (thread is None or not thread.is_alive()):
                self._thread = None

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self.service.refresh()
            except Exception:
                # A provider failure is represented in state; an unexpected loop error
                # must not terminate future refreshes.
                pass
            if self._stop.wait(self.interval_seconds):
                break


_service: Optional[UsageService] = None


def get_service() -> UsageService:
    global _service
    if _service is None:
        _service = UsageService(
            get_store(),
            {
                "agy": AgyQuotaSource(),
                "codex": CodexQuotaSource(),
                "gemini": GeminiQuotaSource(),
            },
            scanner=CodexUsageScanner(),
        )
    return _service
