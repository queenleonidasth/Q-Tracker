"""Read-only OpenAI Platform API billing adapter.

Costs and usage are official organization APIs.  Legacy credit grants is an
undocumented best-effort API, never a guarantee of current account balance.
No browser automation, cookies, or ChatGPT session token access.
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from typing import Any, Callable, Optional

from openai_credentials import get_admin_key, get_legacy_key
from openai_credit_cycle import credit_snapshot, cycle_for
from quota_models import FetchStatus, ProviderSnapshot, utc_now_iso


BASE_URL = "https://api.openai.com/v1"
USER_AGENT = "Q-Tracker/1.0"
REQUEST_TIMEOUT_SECONDS = 20
# ``bucket_width=1d`` responses cap their page size at 31 buckets.
PAGE_LIMIT = 31
MAX_PAGES = 40


class BillingError(RuntimeError):
    """Billing API failures with no raw response bodies or secret content."""


def _get(path: str, params: dict[str, Any], api_key: str) -> dict[str, Any]:
    if not api_key:
        raise BillingError("OpenAI API key not configured")
    query = urlencode(params, doseq=True)
    url = f"{BASE_URL}{path}"
    if query:
        url = f"{url}?{query}"
    request = Request(
        url,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Accept": "application/json",
            "User-Agent": USER_AGENT,
        },
    )
    try:
        with urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        raise BillingError(f"OpenAI billing HTTP {exc.code}") from exc
    except (URLError, TimeoutError, OSError) as exc:
        raise BillingError("OpenAI billing endpoint unavailable") from exc
    except (ValueError, UnicodeDecodeError) as exc:
        raise BillingError("OpenAI billing returned invalid JSON") from exc
    if not isinstance(payload, dict):
        raise BillingError("OpenAI billing returned invalid JSON shape")
    return payload


def _pages(path: str, params: dict[str, Any], api_key: str) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    request_params = dict(params)
    seen: set[str] = set()
    for _ in range(MAX_PAGES):
        payload = _get(path, request_params, api_key)
        if payload.get("object") != "page":
            raise BillingError("OpenAI billing returned invalid JSON shape")
        data = payload.get("data")
        if isinstance(data, list):
            for item in data:
                if isinstance(item, dict):
                    entries.append(item)
        if not payload.get("has_more"):
            break
        token = str(payload.get("next_page") or "")
        if not token or token in seen:
            break
        seen.add(token)
        request_params["page"] = token
    else:
        raise BillingError("OpenAI pagination safety limit reached")
    return entries


def _result_items(entry: dict[str, Any]) -> list[dict[str, Any]]:
    """Costs and usage pages group their numbers inside ``results`` buckets."""
    results = entry.get("results")
    if isinstance(results, list):
        return [item for item in results if isinstance(item, dict)]
    return [entry]


def _amount(item: dict[str, Any]) -> Decimal:
    amount = item.get("amount")
    if isinstance(amount, dict):
        currency = str(amount.get("currency") or "usd").lower()
        value = amount.get("value")
    else:
        currency = str(item.get("currency") or "usd").lower()
        value = amount
    if currency not in ("usd", "$"):
        return Decimal("0")
    if value is None:
        return Decimal("0")
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        raise BillingError("Invalid cost amount") from None


def _epoch(value: datetime) -> int:
    return int(value.timestamp())


def current_cost_bucket_end(now: Optional[datetime] = None) -> datetime:
    """Return tomorrow 00:00 UTC (exclusive), including today's 1d bucket.

    The Costs API's ``end_time`` is exclusive and may omit today's bucket if the
    timestamp falls inside the unfinished UTC day.
    """
    reference = now or datetime.now(timezone.utc)
    if reference.tzinfo is None:
        reference = reference.replace(tzinfo=timezone.utc)
    reference = reference.astimezone(timezone.utc)
    tomorrow = (reference + timedelta(days=1)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    return tomorrow


def _month_range(now: Optional[datetime] = None) -> tuple[datetime, datetime]:
    reference = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    start = reference.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    end = current_cost_bucket_end(reference)
    if end <= start:
        raise BillingError("end_time must exceed start_time")
    return start, end


def costs_usd(admin_key: str, start: datetime, end: datetime) -> Decimal:
    if end <= start:
        raise BillingError("end_time must exceed start_time")
    entries = _pages(
        "/organization/costs",
        {
            "start_time": _epoch(start),
            "end_time": _epoch(end),
            "bucket_width": "1d",
            "limit": PAGE_LIMIT,
        },
        admin_key,
    )
    total = Decimal("0")
    for entry in entries:
        for item in _result_items(entry):
            total += _amount(item)
    return total


def _cost_block(admin_key: str, start: datetime, end: datetime) -> dict[str, Any]:
    return {
        "status": "live",
        "spent_usd": float(costs_usd(admin_key, start, end)),
        "source": "official_costs_api",
        "period_start": _epoch(start),
        "period_end": _epoch(end),
    }


def monthly_cost(admin_key: str, now: Optional[datetime] = None) -> dict[str, Any]:
    start, end = _month_range(now)
    return _cost_block(admin_key, start, end)


def cycle_cost(admin_key: str, now: Optional[datetime] = None) -> dict[str, Any]:
    """Official spend from the credit cycle start up to the current bucket."""
    reference = now or datetime.now(timezone.utc)
    start, _ = cycle_for(reference)
    end = current_cost_bucket_end(reference)
    if end <= start:
        raise BillingError("end_time must exceed start_time")
    return _cost_block(admin_key, start, end)


def monthly_usage(admin_key: str, now: Optional[datetime] = None) -> dict[str, Any]:
    start, end = _month_range(now)
    entries = _pages(
        "/organization/usage/completions",
        {
            "start_time": _epoch(start),
            "end_time": _epoch(end),
            "bucket_width": "1d",
            "limit": PAGE_LIMIT,
        },
        admin_key,
    )
    totals = {
        "input_tokens": 0,
        "output_tokens": 0,
        "input_cached_tokens": 0,
        "num_model_requests": 0,
    }
    for entry in entries:
        for item in _result_items(entry):
            for key in totals:
                try:
                    totals[key] += max(0, int(item.get(key, 0)))
                except (TypeError, ValueError):
                    continue
    return {"status": "live", **totals}


def legacy_credit_balance(api_key: str) -> dict[str, Any]:
    """Unstable endpoint for eligible legacy keys, never a public API."""
    payload = _get("/dashboard/billing/credit_grants", {}, api_key)
    available = payload.get("total_available")
    if available is None:
        raise BillingError("Credit balance missing from legacy response")
    try:
        remaining = max(0.0, float(available))
    except (TypeError, ValueError, OverflowError):
        raise BillingError("Invalid legacy credit amount") from None
    return {
        "status": "live",
        "remaining_usd": remaining,
        "source": "legacy_unofficial",
        "credit_source": str(payload.get("object") or "credit_grants"),
        "credit_status": str(payload.get("status") or "active"),
    }


def snapshot(
    admin_key: Optional[str],
    legacy_key: Optional[str],
    *,
    include_usage: bool = False,
    now: Optional[datetime] = None,
) -> dict[str, Any]:
    """Fetch each source independently, preserving honest unavailable states."""
    reference = now or datetime.now(timezone.utc)
    result: dict[str, Any] = {
        "status": "ok",
        "costs": {"status": "unavailable"},
        "usage": {"status": "unavailable"},
        "credit": {"status": "unavailable"},
        "checked_at": reference.timestamp(),
        "errors": [],
    }
    if not admin_key and not legacy_key:
        result["status"] = "unavailable"
        result["reason"] = "OpenAI API key not configured"
        return result

    if admin_key:
        try:
            result["costs"] = monthly_cost(admin_key, reference)
        except BillingError as error:
            result["errors"].append(str(error))
        if include_usage:
            try:
                result["usage"] = monthly_usage(admin_key, reference)
            except BillingError as error:
                result["errors"].append(str(error))
        try:
            cycle_costs = cycle_cost(admin_key, reference)
            result["cycle"] = cycle_costs
        except BillingError as error:
            result["errors"].append(str(error))

    if legacy_key:
        try:
            result["credit"] = legacy_credit_balance(legacy_key)
        except BillingError as error:
            result["errors"].append(str(error))

    if result["costs"].get("status") != "live" and result["credit"].get("status") != "live":
        result["status"] = "error"
        result["reason"] = "; ".join(result["errors"]) or "OpenAI billing returned invalid JSON shape"
    return result


class OpenAIPlatformQuotaSource:
    """``openai_platform`` provider.  Environment variables are read at fetch time.

    OPENAI_ADMIN_KEY: organization-level admin key, for official costs/usage.
    OPENAI_API_KEY: legacy user key, only for best-effort credit grants.
    No credential is read from ChatGPT, Codex, browser cookies or state.
    """

    def __init__(
        self,
        *,
        fetch_snapshot: Optional[Callable[..., dict[str, Any]]] = None,
        clock: Callable[[], float] = time.monotonic,
        min_interval_seconds: float = 45.0,
    ):
        self._fetch_snapshot = fetch_snapshot or snapshot
        self._clock = clock
        self._min_interval = max(0.0, float(min_interval_seconds))
        self._next_attempt = 0.0
        self._last_result: Optional[ProviderSnapshot] = None

    def invalidate(self) -> None:
        self._next_attempt = 0.0

    def fetch(self) -> ProviderSnapshot:
        now = self._clock()
        if self._last_result is not None and now < self._next_attempt:
            return self._last_result
        self._next_attempt = now + self._min_interval
        self._last_result = self._fetch()
        return self._last_result

    def _fetch(self) -> ProviderSnapshot:
        admin_key = get_admin_key()
        legacy_key = get_legacy_key()
        local_now = datetime.now().astimezone()
        data = self._fetch_snapshot(
            admin_key,
            legacy_key,
            include_usage=True,
            now=local_now,
        )
        costs = data.get("costs") if isinstance(data.get("costs"), dict) else {}
        usage = data.get("usage") if isinstance(data.get("usage"), dict) else {}
        cycle_costs = data.get("cycle") if isinstance(data.get("cycle"), dict) else {}
        credit = data.get("credit") if isinstance(data.get("credit"), dict) else {}

        cycle_spend = cycle_costs.get("spent_usd") if cycle_costs.get("status") == "live" else None
        manual = credit_snapshot(
            cycle_cost_usd=cycle_spend,
            now=local_now,
        )
        if admin_key and manual.get("status") != "estimated" and credit.get("status") != "live":
            manual = {
                **manual,
                "status": "unavailable",
                "message": "Manual baseline only; configure Admin API Key for live cycle spending",
            }

        values_available = (
            costs.get("status") == "live"
            or cycle_costs.get("status") == "live"
            or credit.get("status") == "live"
        )
        errors = [str(item) for item in data.get("errors", []) if item]
        if not values_available:
            message = str(data.get("reason") or "Balance is unavailable for this key")
            status = (
                FetchStatus.UNAVAILABLE
                if data.get("status") == "unavailable"
                else FetchStatus.ERROR
            )
        else:
            status = FetchStatus.OK
            message = "; ".join(errors[:2])

        billing = {
            "costs": costs,
            "usage": usage,
            "credit": manual,
            "legacy": credit,
            "checked_at": data.get("checked_at"),
        }
        observed = utc_now_iso()
        return ProviderSnapshot(
            provider_id="openai_platform",
            provider_name="OpenAI API",
            windows={},
            status=status,
            source="openai_platform_api",
            observed_at=observed,
            refreshed_at=observed,
            plan_type="unknown",
            message=message,
            billing=billing,
        )
