"""User-defined credit allowance, independent of undocumented credit endpoints.

The allowance is what the account owner states for their OpenAI Platform billing
cycle.  Official Costs API numbers turn that allowance into a used/remaining
view.  A missing spend value must never be read as "zero spending", so the
snapshot keeps an explicit ``unavailable`` state instead of inventing a number.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Optional


FIRST_CYCLE_START = date(2026, 10, 22)
RESET_DAY = 22
FIRST_ALLOWANCE = Decimal("31.00")
FIRST_USED = Decimal("3.79")
REGULAR_ALLOWANCE = Decimal("40.00")

STATUS_ESTIMATED = "estimated"
STATUS_UNAVAILABLE = "unavailable"
BASIS_OFFICIAL = "official_cycle_costs"
BASIS_MANUAL_BASELINE = "user_reported_initial"
BASIS_ALLOWANCE_ONLY = "fixed_monthly_allowance"


def _reference(now: Optional[datetime]) -> datetime:
    if now is None:
        return datetime.now().astimezone()
    if now.tzinfo is None:
        return now.replace(tzinfo=timezone.utc).astimezone()
    return now.astimezone()


def _decimal(value: Any) -> Optional[Decimal]:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None


def cycle_for(now: Optional[datetime] = None) -> tuple[datetime, datetime]:
    """Return the local-time credit cycle window ``[start, end)`` holding ``now``."""
    reference = _reference(now)
    day = min(RESET_DAY, 28)
    if reference.day >= day:
        start_year, start_month = reference.year, reference.month
    elif reference.month == 1:
        start_year, start_month = reference.year - 1, 12
    else:
        start_year, start_month = reference.year, reference.month - 1
    start = datetime(start_year, start_month, day, tzinfo=reference.tzinfo)
    if start_month == 12:
        end_year, end_month = start_year + 1, 1
    else:
        end_year, end_month = start_year, start_month + 1
    end = datetime(end_year, end_month, day, tzinfo=reference.tzinfo)
    return start, end


def allowance_for(cycle_start: datetime) -> Decimal:
    """The first tracked cycle keeps its reported allowance; later ones renew."""
    return FIRST_ALLOWANCE if cycle_start.date() == FIRST_CYCLE_START else REGULAR_ALLOWANCE


def spent_since_anchor(anchor_usd: Any, current_usd: Any) -> Optional[Decimal]:
    """Additional spend after a manual baseline, or ``None`` when unknowable."""
    anchor = _decimal(anchor_usd)
    current = _decimal(current_usd)
    if anchor is None or current is None:
        return None
    if anchor < 0 or current < 0:
        raise ValueError("Spend cannot be negative")
    return max(Decimal("0"), current - anchor)


def credit_snapshot(
    *,
    cycle_cost_usd: Any = None,
    anchor_usd: Any = None,
    current_usd: Any = None,
    now: Optional[datetime] = None,
) -> dict[str, Any]:
    """Build the credit block shown in the dashboard from official spend.

    ``cycle_cost_usd`` is the official Costs total for the cycle window.  When it
    is missing, the first tracked cycle can still fall back to the manually
    reported baseline plus any measurable spend since that observation.
    """
    start, end = cycle_for(now)
    allowance = allowance_for(start)
    result: dict[str, Any] = {
        "status": STATUS_UNAVAILABLE,
        "allowance_usd": float(allowance),
        "cycle_start": start.isoformat(),
        "cycle_end": end.isoformat(),
        "reset_day": RESET_DAY,
        "basis": BASIS_ALLOWANCE_ONLY,
    }

    used = _decimal(cycle_cost_usd)
    if used is not None:
        if used < 0:
            raise ValueError("Spend cannot be negative")
        result["basis"] = BASIS_OFFICIAL
    else:
        since_anchor = spent_since_anchor(anchor_usd, current_usd)
        if start.date() == FIRST_CYCLE_START and since_anchor is not None:
            used = FIRST_USED + since_anchor
            result["basis"] = BASIS_MANUAL_BASELINE

    if used is None:
        return result

    result["status"] = STATUS_ESTIMATED
    result["used_usd"] = float(used)
    result["remaining_usd"] = float(max(Decimal("0"), allowance - used))
    return result
