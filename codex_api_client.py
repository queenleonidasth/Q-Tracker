"""
Real-time Codex CLI rate-limit client — queries ChatGPT backend API.
Same approach as TokenTracker (xiufengsun/TokenTracker).

Reads access_token from ~/.codex/auth.json, calls wham/usage endpoint.
Falls back gracefully if token is expired or API is unreachable.

Compatible with Python 3.14, stdlib only (urllib.request, json, base64).
"""

from __future__ import annotations

import base64
import json
import os
import ssl
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from quota_models import ProviderErrorKind, ProviderFetchError

# --- Paths ---
HOME = Path.home()
CODEX_HOME = Path(os.environ.get("CODEX_HOME", HOME / ".codex"))
AUTH_JSON = CODEX_HOME / "auth.json"

# --- Constants ---
WHAM_USAGE_URL = "https://chatgpt.com/backend-api/wham/usage"
WHAM_RESET_CREDITS_URL = "https://chatgpt.com/backend-api/wham/rate-limit-reset-credits"
REQUEST_TIMEOUT = 8  # seconds

# Window duration classification (same as TokenTracker)
CODEX_SESSION_WINDOW_SECONDS = 18000   # 5h = 300 minutes
CODEX_WEEKLY_WINDOW_SECONDS = 604800   # 7d = 10080 minutes


def _read_codex_auth() -> Optional[dict]:
    """
    Read access_token + account_id from ~/.codex/auth.json.

    Expected structure:
    {
        "auth_mode": "chatgpt",
        "tokens": {
            "access_token": "eyJ...",
            "refresh_token": "rt...",
            "account_id": "cc677b00-..."
        },
        "last_refresh": "2026-07-24T07:00:26.050895500Z"
    }

    Returns dict with keys: access_token, account_id (may be None), last_refresh
    Returns None if file is missing or malformed.
    """
    if not AUTH_JSON.exists():
        return None

    try:
        raw = json.loads(AUTH_JSON.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None

    tokens = raw.get("tokens")
    if not isinstance(tokens, dict):
        return None

    access_token = tokens.get("access_token")
    if not access_token or not isinstance(access_token, str):
        return None

    return {
        "access_token": access_token,
        "account_id": tokens.get("account_id") or None,
        "last_refresh": raw.get("last_refresh", ""),
    }


def _decode_jwt_payload(token: str) -> Optional[dict]:
    """
    Decode the payload (middle part) of a JWT without signature verification.
    Only needed to check the 'exp' claim for staleness detection.

    Returns decoded payload dict, or None on failure.
    """
    if not token or not isinstance(token, str):
        return None

    parts = token.split(".")
    if len(parts) < 2:
        return None

    payload_b64 = parts[1]
    # Add padding (JWT uses base64url without padding)
    pad_len = (4 - len(payload_b64) % 4) % 4
    payload_b64 += "=" * pad_len
    # Convert base64url to standard base64
    payload_b64 = payload_b64.replace("-", "+").replace("_", "/")

    try:
        payload_bytes = base64.b64decode(payload_b64)
        return json.loads(payload_bytes.decode("utf-8"))
    except (ValueError, json.JSONDecodeError, UnicodeDecodeError):
        return None


def _is_token_expired(access_token: str) -> bool:
    """
    Check if the JWT access_token is expired by decoding the 'exp' claim.
    Returns True if expired or if we can't determine expiry.
    """
    payload = _decode_jwt_payload(access_token)
    if not payload:
        return True  # Can't decode → treat as expired

    exp = payload.get("exp")
    if not isinstance(exp, (int, float)):
        return True  # No exp claim → treat as expired

    # Add 30-second grace period (token might expire during request)
    return time.time() > (exp - 30)


def _get_token_expiry_info(access_token: str) -> dict:
    """Get human-readable expiry info for diagnostics."""
    payload = _decode_jwt_payload(access_token)
    if not payload:
        return {"decodable": False}

    exp = payload.get("exp")
    if not isinstance(exp, (int, float)):
        return {"decodable": True, "has_exp": False}

    now = time.time()
    expires_at = datetime.fromtimestamp(exp, tz=timezone.utc).isoformat()
    remaining_seconds = int(exp - now)

    return {
        "decodable": True,
        "has_exp": True,
        "expires_at": expires_at,
        "remaining_seconds": remaining_seconds,
        "is_expired": remaining_seconds < 0,
    }


def _classify_window(window: dict) -> Optional[str]:
    """
    Classify a wham rate-limit window by limit_window_seconds.
    Same logic as TokenTracker's classifyCodexWindow.

    18000s = 5h session window
    604800s = 7d weekly window
    """
    if not isinstance(window, dict):
        return None
    seconds = window.get("limit_window_seconds")
    if not isinstance(seconds, (int, float)):
        return None
    seconds = int(seconds)
    if seconds == CODEX_SESSION_WINDOW_SECONDS:
        return "session"
    if seconds == CODEX_WEEKLY_WINDOW_SECONDS:
        return "weekly"
    # Plans may report a non-standard session length (e.g. 14400s); any window
    # shorter than a day is a rolling session quota, anything beyond six days
    # is the weekly bucket. Unknown mid-range durations stay unclassified so
    # positional fallback can decide.
    if 0 < seconds < 86_400:
        return "session"
    if seconds >= 6 * 86_400:
        return "weekly"
    return None


def _http_failure(status: int) -> ProviderFetchError:
    if status in (401, 403):
        return ProviderFetchError(
            ProviderErrorKind.AUTH_REQUIRED, "Codex authentication is required"
        )
    if status == 429:
        return ProviderFetchError(
            ProviderErrorKind.RATE_LIMITED, "Codex usage API rate limit reached"
        )
    return ProviderFetchError(
        ProviderErrorKind.OTHER, f"Codex usage API returned HTTP {int(status)}"
    )


def _fetch_usage(access_token: str, account_id: Optional[str], verbose: bool = False) -> dict:
    """
    GET https://chatgpt.com/backend-api/wham/usage

    Headers (same as TokenTracker / CodexBar):
        Authorization: Bearer {access_token}
        Accept: application/json
        ChatGPT-Account-Id: {account_id}  (if available)

    Returns raw JSON and raises a credential-free structured error on failure.
    """
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Accept": "application/json",
    }
    if account_id:
        headers["ChatGPT-Account-Id"] = account_id

    req = Request(WHAM_USAGE_URL, headers=headers, method="GET")

    # SSL context (chatgpt.com uses valid certs, so default verification is fine)
    ctx = ssl.create_default_context()

    try:
        with urlopen(req, timeout=REQUEST_TIMEOUT, context=ctx) as resp:
            if resp.status != 200:
                raise _http_failure(resp.status)
            data = resp.read().decode("utf-8")
            return json.loads(data)
    except HTTPError as error:
        raise _http_failure(error.code) from None
    except TimeoutError:
        raise ProviderFetchError(
            ProviderErrorKind.TIMEOUT, "Codex usage API timed out"
        ) from None
    except URLError as error:
        kind = (
            ProviderErrorKind.TIMEOUT
            if isinstance(error.reason, TimeoutError)
            else ProviderErrorKind.OTHER
        )
        message = (
            "Codex usage API timed out"
            if kind is ProviderErrorKind.TIMEOUT
            else "Codex usage API is unreachable"
        )
        raise ProviderFetchError(kind, message) from None
    except OSError:
        raise ProviderFetchError(
            ProviderErrorKind.OTHER, "Codex usage API is unreachable"
        ) from None
    except json.JSONDecodeError:
        raise ProviderFetchError(
            ProviderErrorKind.PARSE, "Codex usage API returned invalid JSON"
        ) from None


def _clamp_percent(value) -> Optional[float]:
    """Clamp a percentage value to [0, 100], return None if invalid."""
    if value is None or value == "":
        return None
    try:
        n = float(value)
    except (TypeError, ValueError):
        return None
    if n <= 0:
        return 0.0
    if n >= 100:
        return 100.0
    return n


def _normalize_usage_response(body: dict) -> Optional[dict]:
    """
    Parse the wham/usage response into our standard format.

    The response contains:
    {
        "rate_limit": {
            "primary_window": {
                "used_percent": N,
                "limit_window_seconds": 18000,
                "reset_at": "2026-08-01T12:00:00Z",
                ...
            },
            "secondary_window": {
                "used_percent": N,
                "limit_window_seconds": 604800,
                "reset_at": "2026-08-04T00:00:00Z",
                ...
            }
        },
        "spend_control": { ... },
        "additional_rate_limits": [ ... ]
    }

    Windows are classified by limit_window_seconds (not position):
    - 18000s = session (5h)
    - 604800s = weekly (7d)
    """
    if not body or not isinstance(body, dict):
        return None

    rate_limit = body.get("rate_limit")
    if not isinstance(rate_limit, dict):
        return None

    primary_raw = rate_limit.get("primary_window")
    secondary_raw = rate_limit.get("secondary_window")

    # Classify windows by duration (same logic as TokenTracker)
    session_window = None
    weekly_window = None

    for window in (primary_raw, secondary_raw):
        if not isinstance(window, dict):
            continue
        kind = _classify_window(window)
        if kind == "session" and session_window is None:
            session_window = window
        elif kind == "weekly" and weekly_window is None:
            weekly_window = window

    # Fallback: if classification failed, use positional assignment
    if session_window is None and weekly_window is None:
        if isinstance(primary_raw, dict):
            session_window = primary_raw
        if isinstance(secondary_raw, dict):
            weekly_window = secondary_raw

    # Some plans temporarily expose only the weekly window. Keep it as the
    # primary normalized record while preserving its 10080-minute duration;
    # consumers classify by duration and therefore render "Weekly", not "5H".
    if session_window is None and weekly_window is not None:
        session_window = weekly_window
        weekly_window = None

    # Build primary (session/5h) result
    if session_window is None:
        return None

    used_pct = _clamp_percent(session_window.get("used_percent"))
    if used_pct is None:
        return None

    # Parse reset_at as unix timestamp
    reset_at_str = session_window.get("reset_at", "")
    resets_at_unix = 0
    if isinstance(reset_at_str, str) and reset_at_str:
        try:
            reset_dt = datetime.fromisoformat(reset_at_str.replace("Z", "+00:00"))
            resets_at_unix = int(reset_dt.timestamp())
        except (ValueError, TypeError):
            pass
    elif isinstance(reset_at_str, (int, float)):
        resets_at_unix = int(reset_at_str)

    window_seconds = session_window.get("limit_window_seconds")
    window_minutes = int(window_seconds) // 60 if isinstance(window_seconds, (int, float)) else 300

    now_iso = datetime.now(timezone.utc).isoformat()

    result = {
        "used_percent": round(used_pct, 1),
        "percent_left": round(100.0 - used_pct, 1),
        "window_minutes": window_minutes,
        "resets_at": resets_at_unix,
        "plan_type": "chatgpt",
        "timestamp": now_iso,
    }

    # Build secondary (weekly) result if available
    if weekly_window is not None:
        weekly_used_pct = _clamp_percent(weekly_window.get("used_percent"))
        if weekly_used_pct is not None:
            weekly_reset_str = weekly_window.get("reset_at", "")
            weekly_resets_at = 0
            if isinstance(weekly_reset_str, str) and weekly_reset_str:
                try:
                    weekly_reset_dt = datetime.fromisoformat(weekly_reset_str.replace("Z", "+00:00"))
                    weekly_resets_at = int(weekly_reset_dt.timestamp())
                except (ValueError, TypeError):
                    pass
            elif isinstance(weekly_reset_str, (int, float)):
                weekly_resets_at = int(weekly_reset_str)

            weekly_window_seconds = weekly_window.get("limit_window_seconds")
            weekly_window_minutes = (
                int(weekly_window_seconds) // 60
                if isinstance(weekly_window_seconds, (int, float))
                else 10080
            )

            result["secondary"] = {
                "used_percent": round(weekly_used_pct, 1),
                "percent_left": round(100.0 - weekly_used_pct, 1),
                "window_minutes": weekly_window_minutes,
                "resets_at": weekly_resets_at,
            }

    return result


def fetch_codex_live_limits(verbose: bool = False) -> Optional[dict]:
    """
    Main entry point — fetch Codex rate limits from ChatGPT backend API.

    Returns dict compatible with auto_fetch.py consumers:
    {
        "used_percent": float,       # primary window usage (0-100)
        "percent_left": float,       # 100 - used_percent
        "window_minutes": int,       # primary window duration (300 = 5h)
        "resets_at": int,            # unix timestamp of primary window reset
        "plan_type": str,            # "chatgpt"
        "timestamp": str,            # ISO timestamp of this fetch
        "secondary": {               # weekly window (if available)
            "used_percent": float,
            "percent_left": float,
            "window_minutes": int,
            "resets_at": int,
        }
    }

    Raises ``ProviderFetchError`` if:
    - auth.json is missing or malformed
    - Token is expired (JWT exp < now)
    - API returns non-200 (401/403/429/etc.)
    - Network error or timeout
    """
    # Step 1: Read auth credentials
    auth = _read_codex_auth()
    if not auth:
        if verbose:
            print("  [Codex API] Cannot read ~/.codex/auth.json")
        raise ProviderFetchError(
            ProviderErrorKind.AUTH_REQUIRED,
            "Codex authentication is unavailable; run Codex and sign in once",
        )

    access_token = auth["access_token"]
    account_id = auth["account_id"]

    if verbose:
        print(f"  [Codex API] Auth loaded (account_id: {account_id or 'none'})")
        print(f"  [Codex API] Token length: {len(access_token)} chars")
        print(f"  [Codex API] Last refresh: {auth['last_refresh']}")

    # Step 2: Check token expiry
    if _is_token_expired(access_token):
        if verbose:
            expiry_info = _get_token_expiry_info(access_token)
            print(f"  [Codex API] Token EXPIRED: {expiry_info}")
            print("  [Codex API] Run `codex` once to refresh the token.")
        raise ProviderFetchError(
            ProviderErrorKind.AUTH_REQUIRED,
            "Codex authentication expired; run Codex to refresh sign-in",
        )

    if verbose:
        expiry_info = _get_token_expiry_info(access_token)
        remaining = expiry_info.get("remaining_seconds", 0)
        print(f"  [Codex API] Token valid ({remaining}s remaining)")

    # Step 3: Fetch usage from API
    if verbose:
        print(f"  [Codex API] Fetching {WHAM_USAGE_URL} ...")

    body = _fetch_usage(access_token, account_id, verbose=verbose)

    # Step 4: Normalize response
    result = _normalize_usage_response(body)
    if result is None:
        if verbose:
            print("  [Codex API] Could not parse response — unexpected format")
            # Dump raw keys for diagnostics
            print(f"  [Codex API] Response keys: {list(body.keys())}")
            rl = body.get("rate_limit", {})
            if isinstance(rl, dict):
                print(f"  [Codex API] rate_limit keys: {list(rl.keys())}")
        raise ProviderFetchError(
            ProviderErrorKind.PARSE,
            "Codex usage API returned an unsupported response",
        )

    if verbose:
        print(f"  [Codex API] Success! {result['percent_left']:.1f}% left "
              f"(used {result['used_percent']:.1f}%, "
              f"window={result['window_minutes']}min)")
        if "secondary" in result:
            sec = result["secondary"]
            print(f"  [Codex API] Weekly: {sec['percent_left']:.1f}% left "
                  f"(used {sec['used_percent']:.1f}%, "
                  f"window={sec['window_minutes']}min)")

    return result


def main():
    """CLI test mode — run full pipeline with diagnostics."""
    print("=" * 60)
    print("Codex API Client — Real-time Rate Limit Fetcher")
    print("Same approach as TokenTracker (xiufengsun/TokenTracker)")
    print("=" * 60)
    print()

    # Step 1: Auth file check
    print(f"[1] Auth file: {AUTH_JSON}")
    if not AUTH_JSON.exists():
        print("    ❌ File not found!")
        print("    Run `codex` once to create auth.json")
        sys.exit(1)
    print("    ✓ File exists")

    # Read and display auth info (redacted)
    auth = _read_codex_auth()
    if not auth:
        print("    ❌ Could not parse auth.json")
        sys.exit(1)

    print(f"    account_id: {auth['account_id'] or '(none)'}")
    print(f"    token length: {len(auth['access_token'])} chars")
    print(f"    last_refresh: {auth['last_refresh']}")
    print()

    # Step 2: Token expiry check
    print("[2] Token expiry check:")
    expiry_info = _get_token_expiry_info(auth["access_token"])
    if not expiry_info.get("decodable"):
        print("    ⚠ Cannot decode JWT payload")
    elif not expiry_info.get("has_exp"):
        print("    ⚠ No 'exp' claim in JWT")
    else:
        print(f"    expires_at: {expiry_info['expires_at']}")
        remaining = expiry_info["remaining_seconds"]
        if expiry_info["is_expired"]:
            print(f"    ❌ EXPIRED ({abs(remaining)}s ago)")
            print("    Run `codex` once to refresh the token.")
        else:
            hours = remaining // 3600
            minutes = (remaining % 3600) // 60
            print(f"    ✓ Valid ({hours}h {minutes}m remaining)")
    print()

    # Step 3: Fetch
    print("[3] Fetching wham/usage endpoint:")
    print(f"    URL: {WHAM_USAGE_URL}")
    t0 = time.perf_counter()
    try:
        result = fetch_codex_live_limits(verbose=True)
    except ProviderFetchError as error:
        print(f"    ❌ {error}")
        result = None
    elapsed_ms = (time.perf_counter() - t0) * 1000
    print(f"    Time: {elapsed_ms:.0f}ms")
    print()

    # Step 4: Results
    print("[4] Result:")
    if result is None:
        print("    ❌ No data (see above for reason)")
        sys.exit(1)

    print(f"    ✓ Primary (5h window):")
    print(f"      Used:      {result['used_percent']:.1f}%")
    print(f"      Remaining: {result['percent_left']:.1f}%")
    print(f"      Window:    {result['window_minutes']} minutes")
    if result["resets_at"]:
        reset_dt = datetime.fromtimestamp(result["resets_at"], tz=timezone.utc)
        print(f"      Resets at: {reset_dt.isoformat()}")
    print(f"      Plan:      {result['plan_type']}")
    print(f"      Fetched:   {result['timestamp']}")

    if "secondary" in result:
        sec = result["secondary"]
        print(f"    ✓ Secondary (weekly window):")
        print(f"      Used:      {sec['used_percent']:.1f}%")
        print(f"      Remaining: {sec['percent_left']:.1f}%")
        print(f"      Window:    {sec['window_minutes']} minutes")
        if sec["resets_at"]:
            reset_dt = datetime.fromtimestamp(sec["resets_at"], tz=timezone.utc)
            print(f"      Resets at: {reset_dt.isoformat()}")

    print()
    print("=" * 60)
    print("JSON output (for integration testing):")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
