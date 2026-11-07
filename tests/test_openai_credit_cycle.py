from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

import openai_credit_cycle as cycle


LOCAL_TZ = datetime.now().astimezone().tzinfo


def local(year: int, month: int, day: int, hour: int = 12) -> datetime:
    return datetime(year, month, day, hour, 0, tzinfo=LOCAL_TZ)


def test_cycle_for_uses_the_local_reset_day_boundary():
    start, end = cycle.cycle_for(local(2026, 11, 7))

    assert (start.month, start.day) == (10, 22)
    assert (end.month, end.day) == (11, 22)


def test_cycle_for_wraps_the_year_boundary():
    start, end = cycle.cycle_for(local(2027, 1, 5))

    assert (start.year, start.month, start.day) == (2026, 12, 22)
    assert (end.year, end.month, end.day) == (2027, 1, 22)


def test_cycle_for_treats_the_reset_day_as_already_renewed():
    start, _ = cycle.cycle_for(local(2026, 11, 22, hour=0))

    assert (start.month, start.day) == (11, 22)


def test_allowance_keeps_the_reported_first_cycle_then_renews():
    assert cycle.allowance_for(local(2026, 10, 22, hour=0)) == Decimal("31.00")
    assert cycle.allowance_for(local(2026, 11, 22, hour=0)) == Decimal("40.00")


def test_credit_snapshot_reports_official_cycle_spend():
    result = cycle.credit_snapshot(cycle_cost_usd=Decimal("6.39"), now=local(2026, 11, 7))

    assert result["status"] == "estimated"
    assert result["basis"] == "official_cycle_costs"
    assert result["allowance_usd"] == 31.0
    assert result["used_usd"] == 6.39
    assert result["remaining_usd"] == pytest.approx(24.61)


def test_missing_spend_never_becomes_zero_spending():
    result = cycle.credit_snapshot(now=local(2026, 12, 5))

    assert result["status"] == "unavailable"
    assert result["basis"] == "fixed_monthly_allowance"
    assert "used_usd" not in result
    assert "remaining_usd" not in result


def test_first_cycle_falls_back_to_the_reported_baseline():
    result = cycle.credit_snapshot(
        anchor_usd=Decimal("10.00"),
        current_usd=Decimal("12.00"),
        now=local(2026, 11, 7),
    )

    assert result["basis"] == "user_reported_initial"
    assert result["used_usd"] == pytest.approx(5.79)
    assert result["remaining_usd"] == pytest.approx(25.21)


def test_later_cycles_do_not_invent_a_manual_baseline():
    result = cycle.credit_snapshot(
        anchor_usd=Decimal("10.00"),
        current_usd=Decimal("12.00"),
        now=local(2026, 12, 5),
    )

    assert result["status"] == "unavailable"


def test_negative_spend_is_rejected():
    with pytest.raises(ValueError, match="Spend cannot be negative"):
        cycle.credit_snapshot(cycle_cost_usd=Decimal("-1.00"), now=local(2026, 11, 7))


def test_spent_since_anchor_clamps_regressions():
    assert cycle.spent_since_anchor(Decimal("10"), Decimal("8")) == Decimal("0")
    assert cycle.spent_since_anchor(None, Decimal("8")) is None
