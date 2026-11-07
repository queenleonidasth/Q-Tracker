from datetime import datetime, timezone
from decimal import Decimal

import pytest

import openai_platform_billing as billing
from quota_models import FetchStatus, ProviderSnapshot


COSTS_PAGE = {
    "object": "page",
    "data": [
        {
            "object": "bucket",
            "start_time": 1,
            "results": [
                {"object": "organization.costs.result", "amount": {"currency": "usd", "value": 4.20}},
                {"object": "organization.costs.result", "amount": {"currency": "usd", "value": 2.19}},
            ],
        },
        {
            "object": "bucket",
            "start_time": 3,
            "results": [{"amount": {"currency": "eur", "value": 99.00}}],
        },
    ],
    "has_more": False,
}
USAGE_PAGE = {
    "object": "page",
    "data": [
        {
            "results": [
                {
                    "input_tokens": 10,
                    "output_tokens": 5,
                    "input_cached_tokens": 2,
                    "num_model_requests": 3,
                },
                {"input_tokens": 90, "num_model_requests": 1},
            ]
        }
    ],
    "has_more": False,
}


class _Response:
    def __init__(self, payload: bytes):
        self._payload = payload

    def read(self) -> bytes:
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


def test_get_sends_bearer_and_parses_json(monkeypatch):
    calls = []

    def fake_urlopen(request, timeout=None):
        calls.append((request.full_url, dict(request.headers)))
        return _Response(b'{"object": "page", "data": []}')

    monkeypatch.setattr(billing, "urlopen", fake_urlopen)

    payload = billing._get("/organization/costs", {"limit": 3}, "sk-admin")

    assert payload == {"object": "page", "data": []}
    url, headers = calls[0]
    assert url.startswith(billing.BASE_URL + "/organization/costs?")
    assert "limit=3" in url
    assert headers["Authorization"] == "Bearer sk-admin"
    # urllib capitalizes request header names when storing them.
    assert headers["User-agent"] == "Q-Tracker/1.0"


def test_get_never_leaks_a_response_body_into_the_error(monkeypatch):
    from urllib.error import HTTPError

    def fail(request, timeout=None):
        raise HTTPError("https://api.openai.com/v1/x", 401, "Unauthorized", {}, None)

    monkeypatch.setattr(billing, "urlopen", fail)

    with pytest.raises(billing.BillingError) as error:
        billing._get("/organization/costs", {}, "sk-admin")

    assert str(error.value) == "OpenAI billing HTTP 401"


def test_get_requires_a_key():
    with pytest.raises(billing.BillingError, match="OpenAI API key not configured"):
        billing._get("/organization/costs", {}, "")


def test_pages_follows_the_pagination_token(monkeypatch):
    seen = []

    def fake_get(path, params, api_key):
        seen.append(dict(params))
        if len(seen) == 1:
            return {"object": "page", "data": [{"amount": "1"}], "has_more": True, "next_page": "p2"}
        return {"object": "page", "data": [{"amount": "1"}], "has_more": False}

    monkeypatch.setattr(billing, "_get", fake_get)

    entries = billing._pages("/organization/costs", {"limit": 200}, "sk-admin")

    assert len(entries) == 2
    assert "page" not in seen[0]
    assert seen[1]["page"] == "p2"


def test_pages_stops_on_a_repeated_token(monkeypatch):
    def looping(path, params, api_key):
        return {"object": "page", "data": [], "has_more": True, "next_page": "same"}

    monkeypatch.setattr(billing, "_get", looping)

    assert billing._pages("/organization/costs", {}, "sk-admin") == []


def test_pages_hits_the_safety_limit(monkeypatch):
    counter = {"n": 0}

    def always_more(path, params, api_key):
        counter["n"] += 1
        return {
            "object": "page",
            "data": [],
            "has_more": True,
            "next_page": f"page-{counter['n']}",
        }

    monkeypatch.setattr(billing, "_get", always_more)

    with pytest.raises(billing.BillingError, match="pagination safety limit"):
        billing._pages("/organization/costs", {}, "sk-admin")


def test_costs_usd_sums_only_usd_amounts(monkeypatch):
    monkeypatch.setattr(billing, "_get", lambda *args: COSTS_PAGE)

    total = billing.costs_usd("sk-admin", datetime(2026, 11, 1, tzinfo=timezone.utc), datetime(2026, 11, 8, tzinfo=timezone.utc))

    assert total == Decimal("6.39")


def test_costs_reject_an_inverted_window(monkeypatch):
    monkeypatch.setattr(billing, "_get", lambda *args: COSTS_PAGE)

    with pytest.raises(billing.BillingError, match="end_time must exceed start_time"):
        billing.costs_usd(
            "sk-admin",
            datetime(2026, 11, 8, tzinfo=timezone.utc),
            datetime(2026, 11, 1, tzinfo=timezone.utc),
        )


def test_monthly_usage_totals_across_buckets(monkeypatch):
    monkeypatch.setattr(billing, "_get", lambda *args: USAGE_PAGE)

    usage = billing.monthly_usage("sk-admin")

    assert usage["status"] == "live"
    assert usage["input_tokens"] == 100
    assert usage["output_tokens"] == 5
    assert usage["num_model_requests"] == 4


def test_legacy_balance_requires_a_credit_field(monkeypatch):
    monkeypatch.setattr(billing, "_get", lambda *args: {"object": "credit_grants"})

    with pytest.raises(billing.BillingError, match="Credit balance missing"):
        billing.legacy_credit_balance("sk-legacy")


def test_snapshot_keeps_each_source_independent(monkeypatch):
    def fake_get(path, params, api_key):
        if path == "/organization/costs":
            return COSTS_PAGE
        if path == "/dashboard/billing/credit_grants":
            return {"object": "credit_grants", "total_available": 12.5}
        raise billing.BillingError("OpenAI billing HTTP 500")

    monkeypatch.setattr(billing, "_get", fake_get)

    result = billing.snapshot(
        "sk-admin",
        "sk-legacy",
        include_usage=True,
        now=datetime(2026, 11, 7, 1, 0, tzinfo=timezone.utc),
    )

    assert result["costs"]["status"] == "live"
    assert result["usage"]["status"] == "unavailable"
    assert result["credit"]["remaining_usd"] == 12.5
    assert "OpenAI billing HTTP 500" in result["errors"]


def test_snapshot_without_any_key_is_unavailable():
    result = billing.snapshot(None, None)

    assert result["status"] == "unavailable"
    assert result["reason"] == "OpenAI API key not configured"


def _source(monkeypatch, snapshot_result, keys=("sk-admin", None)):
    monkeypatch.setattr(billing, "get_admin_key", lambda: keys[0])
    monkeypatch.setattr(billing, "get_legacy_key", lambda: keys[1])
    return billing.OpenAIPlatformQuotaSource(
        fetch_snapshot=lambda *args, **kwargs: snapshot_result,
        clock=lambda: 0.0,
    )


def test_quota_source_builds_a_billing_snapshot(monkeypatch):
    now = datetime(2026, 11, 7, 1, 0, tzinfo=timezone.utc)
    snapshot_result = {
        "status": "ok",
        "costs": {"status": "live", "spent_usd": 6.39, "source": "official_costs_api"},
        "usage": {"status": "live", "input_tokens": 100, "output_tokens": 5},
        "cycle": {"status": "live", "spent_usd": 6.39},
        "credit": {"status": "unavailable"},
        "errors": [],
        "checked_at": now.timestamp(),
    }
    source = _source(monkeypatch, snapshot_result)

    result = source.fetch()

    assert isinstance(result, ProviderSnapshot)
    assert result.provider_id == "openai_platform"
    assert result.provider_name == "OpenAI API"
    assert result.status is FetchStatus.OK
    assert result.source == "openai_platform_api"
    assert result.windows == {}
    assert result.billing["credit"]["used_usd"] == 6.39
    assert result.billing["credit"]["allowance_usd"] == 31.0
    assert result.to_dict()["billing"]["credit"]["basis"] == "official_cycle_costs"


def test_quota_source_reports_a_missing_key(monkeypatch):
    source = _source(monkeypatch, billing.snapshot(None, None), keys=(None, None))

    result = source.fetch()

    assert result.status is FetchStatus.UNAVAILABLE
    assert result.message == "OpenAI API key not configured"


def test_quota_source_reuses_a_recent_result(monkeypatch):
    calls = {"n": 0}
    payload = {
        "status": "ok",
        "costs": {"status": "live", "spent_usd": 1.0},
        "usage": {"status": "unavailable"},
        "credit": {"status": "unavailable"},
        "errors": [],
    }

    def counting(*args, **kwargs):
        calls["n"] += 1
        return payload

    monkeypatch.setattr(billing, "get_admin_key", lambda: "sk-admin")
    monkeypatch.setattr(billing, "get_legacy_key", lambda: None)
    source = billing.OpenAIPlatformQuotaSource(fetch_snapshot=counting, clock=lambda: 0.0)

    source.fetch()
    source.fetch()
    assert calls["n"] == 1

    source.invalidate()
    source.fetch()
    assert calls["n"] == 2
