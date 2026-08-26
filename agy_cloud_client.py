"""
Cloud AGY quota client — reads Antigravity quota straight from Google's backend
without agy.exe running.

Approach:
1. Read the OAuth token the Antigravity CLI already stored in Windows Credential
   Manager (target ``gemini:antigravity``) via CredReadW — zero process creation,
   zero console popups.
2. Refresh the access token through oauth2.googleapis.com when it is expired,
   using the installed-app OAuth clients embedded in agy.exe (installed-app
   clients cannot keep a secret; the same values ship inside the public binary).
3. POST ``cloudcode-pa.googleapis.com/v1internal:retrieveUserQuotaSummary`` for
   the full bucket set (gemini-5h / gemini-weekly / 3p-5h / 3p-weekly), falling
   back to ``fetchAvailableModels`` per-model quotas aggregated by family.
4. Write the result to ``agy_quota_cache.json`` so offline consumers stay warm.

Nothing spawns agy.exe and no token material is ever logged.
Compatible with Python 3.13+, stdlib only, Windows-specific (returns None elsewhere).
"""

from __future__ import annotations

import ctypes
import json
import os
import sys
import time
from datetime import datetime, timezone
from typing import Any, Optional
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from agy_api_client import _write_cache

CREDENTIAL_TARGET = "gemini:antigravity"
CRED_TYPE_GENERIC = 1

FETCH_AVAILABLE_MODELS_URL = (
    "https://cloudcode-pa.googleapis.com/v1internal:fetchAvailableModels"
)
RETRIEVE_QUOTA_SUMMARY_URL = (
    "https://cloudcode-pa.googleapis.com/v1internal:retrieveUserQuotaSummary"
)
TOKEN_URL = "https://oauth2.googleapis.com/token"
REQUEST_TIMEOUT = 10.0

FAMILY_LABELS = {"gemini": "Gemini", "3p": "Claude & GPT"}
BUCKET_LABELS = {
    "gemini-5h": "Gemini 5H",
    "gemini-weekly": "Gemini Weekly",
    "3p-5h": "Claude & GPT 5H",
    "3p-weekly": "Claude & GPT Weekly",
}
INTERNAL_MODEL_PREFIXES = ("chat_", "tab_")


def _configured_oauth_clients() -> tuple[tuple[str, str], ...]:
    clients = []
    for suffix in ("", "_2"):
        client_id = os.environ.get(f"Q_TRACKER_AGY_OAUTH_CLIENT_ID{suffix}", "").strip()
        client_secret = os.environ.get(
            f"Q_TRACKER_AGY_OAUTH_CLIENT_SECRET{suffix}", ""
        ).strip()
        if client_id and client_secret:
            clients.append((client_id, client_secret))
    return tuple(clients)


# --- Windows Credential Manager (read-only) ---

class _CREDENTIAL(ctypes.Structure):
    _fields_ = [
        ("Flags", ctypes.c_ulong),
        ("Type", ctypes.c_ulong),
        ("TargetName", ctypes.c_wchar_p),
        ("Comment", ctypes.c_wchar_p),
        ("LastWritten", ctypes.c_ulong * 2),
        ("CredentialBlobSize", ctypes.c_ulong),
        ("CredentialBlob", ctypes.POINTER(ctypes.c_byte)),
        ("Persist", ctypes.c_ulong),
        ("AttributeCount", ctypes.c_ulong),
        ("Attributes", ctypes.c_void_p),
        ("TargetAlias", ctypes.c_wchar_p),
        ("UserName", ctypes.c_wchar_p),
    ]


def read_credential_blob(target: str = CREDENTIAL_TARGET) -> Optional[str]:
    """Return the generic credential blob as text, or None when unavailable."""
    if sys.platform != "win32":
        return None
    try:
        advapi32 = ctypes.WinDLL("advapi32")
        credential_ptr = ctypes.POINTER(_CREDENTIAL)()
        if not advapi32.CredReadW(
            target, CRED_TYPE_GENERIC, 0, ctypes.byref(credential_ptr)
        ):
            return None
        try:
            credential = credential_ptr.contents
            size = int(credential.CredentialBlobSize)
            if size <= 0 or not credential.CredentialBlob:
                return None
            buffer = (ctypes.c_char * size).from_address(
                ctypes.addressof(credential.CredentialBlob.contents)
            )
            return buffer.raw.decode("utf-8", "replace")
        finally:
            ctypes.windll.advapi32.CredFree(credential_ptr)
    except (OSError, AttributeError):
        return None


def load_stored_token() -> Optional[dict[str, Any]]:
    """Parse the stored Antigravity OAuth blob into {access_token, refresh_token, expiry}."""
    raw = read_credential_blob()
    if not raw:
        return None
    try:
        blob = json.loads(raw)
    except json.JSONDecodeError:
        return None
    token = blob.get("token") if isinstance(blob, dict) else None
    if not isinstance(token, dict) or not token.get("access_token"):
        return None
    return {
        "access_token": str(token["access_token"]),
        "refresh_token": str(token.get("refresh_token") or ""),
        "expiry": token.get("expiry"),
    }


# --- Token refresh ---

def parse_expiry(value: Any) -> Optional[datetime]:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip())
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def refresh_access_token(
    refresh_token: str, timeout: float = REQUEST_TIMEOUT
) -> Optional[str]:
    """Exchange the stored refresh token for a fresh access token."""
    if not refresh_token:
        return None
    for client_id, client_secret in _configured_oauth_clients():
        body = urlencode(
            {
                "client_id": client_id,
                "client_secret": client_secret,
                "refresh_token": refresh_token,
                "grant_type": "refresh_token",
            }
        ).encode("utf-8")
        request = Request(
            TOKEN_URL,
            data=body,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            method="POST",
        )
        try:
            with urlopen(request, timeout=timeout) as response:
                payload = json.loads(response.read().decode("utf-8", "replace"))
        except (HTTPError, URLError, OSError, json.JSONDecodeError, ValueError):
            continue
        access_token = payload.get("access_token")
        if isinstance(access_token, str) and access_token:
            return access_token
    return None


# --- Cloud API ---

def post_json(
    url: str, body: dict, headers: dict, timeout: float = REQUEST_TIMEOUT
) -> tuple[int, str]:
    data = json.dumps(body).encode("utf-8")
    request = Request(url, data=data, headers=headers, method="POST")
    try:
        with urlopen(request, timeout=timeout) as response:
            return response.status, response.read().decode("utf-8", "replace")
    except HTTPError as error:
        try:
            return error.code, error.read().decode("utf-8", "replace")
        except OSError:
            return error.code, ""
    except (URLError, OSError) as error:
        reason = getattr(error, "reason", error)
        return -1, f"network error: {reason}"


def fetch_available_models(
    access_token: str, timeout: float = REQUEST_TIMEOUT
) -> tuple[int, dict[str, Any]]:
    status, text = post_json(
        FETCH_AVAILABLE_MODELS_URL,
        {},
        {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
            "User-Agent": "antigravity",
        },
        timeout=timeout,
    )
    if status != 200:
        return status, {}
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return status, {}
    models = payload.get("models") if isinstance(payload, dict) else None
    if not isinstance(models, dict):
        return status, {}
    return status, models


def fetch_quota_summary(
    access_token: str, timeout: float = REQUEST_TIMEOUT
) -> tuple[int, list]:
    """POST retrieveUserQuotaSummary; returns (status, raw groups list)."""
    status, text = post_json(
        RETRIEVE_QUOTA_SUMMARY_URL,
        {},
        {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
            "User-Agent": "antigravity",
        },
        timeout=timeout,
    )
    if status != 200:
        return status, []
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return status, []
    groups = payload.get("groups") if isinstance(payload, dict) else None
    if not isinstance(groups, list):
        return status, []
    return status, groups


def parse_quota_groups(
    raw_groups: list, now: Optional[datetime] = None
) -> dict[str, dict[str, Any]]:
    """Flatten summary buckets into the cache format keyed by bucketId."""
    now_dt = now or datetime.now(timezone.utc)
    groups: dict[str, dict[str, Any]] = {}
    for group in raw_groups:
        if not isinstance(group, dict):
            continue
        for bucket in group.get("buckets", []):
            if not isinstance(bucket, dict):
                continue
            bucket_id = str(bucket.get("bucketId") or "")
            if not bucket_id:
                continue
            fraction = bucket.get("remainingFraction")
            if not isinstance(fraction, (int, float)) or isinstance(fraction, bool):
                continue
            fraction = max(0.0, min(1.0, float(fraction)))
            reset_time = str(bucket.get("resetTime") or "")
            reset_in_seconds = 0
            reset_dt = parse_expiry(reset_time)
            if reset_dt is not None:
                reset_in_seconds = max(0, int((reset_dt - now_dt).total_seconds()))
            groups[bucket_id] = {
                "remaining_percent": round(fraction * 100.0, 1),
                "remaining_fraction": round(fraction, 7),
                "reset_time": reset_time,
                "reset_in_seconds": reset_in_seconds,
                "label": BUCKET_LABELS.get(bucket_id, bucket_id.replace("-", " ").title()),
            }
    return groups


# --- Aggregation (pure; unit-tested without network) ---

def aggregate_model_quota(
    models: dict[str, Any], now: Optional[datetime] = None
) -> dict[str, dict[str, Any]]:
    """Fold per-model quotaInfo into gemini/3p groups using the binding minimum."""
    now_dt = now or datetime.now(timezone.utc)
    best: dict[str, tuple[float, str]] = {}
    for name, entry in models.items():
        if not isinstance(name, str) or name.startswith(INTERNAL_MODEL_PREFIXES):
            continue
        if not isinstance(entry, dict):
            continue
        quota = entry.get("quotaInfo")
        if not isinstance(quota, dict):
            continue
        fraction = quota.get("remainingFraction")
        if not isinstance(fraction, (int, float)) or isinstance(fraction, bool):
            continue
        fraction = max(0.0, min(1.0, float(fraction)))
        family = "gemini" if name.startswith("gemini") else "3p"
        reset_time = str(quota.get("resetTime") or "")
        current = best.get(family)
        if current is None or fraction < current[0]:
            best[family] = (fraction, reset_time)

    groups: dict[str, dict[str, Any]] = {}
    for family, (fraction, reset_time) in best.items():
        reset_in_seconds = 0
        reset_dt = parse_expiry(reset_time)
        if reset_dt is not None:
            reset_in_seconds = max(0, int((reset_dt - now_dt).total_seconds()))
        groups[family] = {
            "remaining_percent": round(fraction * 100.0, 1),
            "remaining_fraction": round(fraction, 7),
            "reset_time": reset_time,
            "reset_in_seconds": reset_in_seconds,
            "label": FAMILY_LABELS.get(family, family),
        }
    return groups


# --- Public API ---

def fetch_from_cloud(verbose: bool = False) -> Optional[dict]:
    """
    Fetch AGY quota from Google's cloud API using the locally stored OAuth token.

    Returns a dict in agy_quota_cache.json format ({"groups": {...}}), or None
    when no stored credential exists, the token cannot be refreshed, or the
    cloud API does not return usable quota data.
    """
    started = time.perf_counter()

    def log(message: str) -> None:
        if verbose:
            elapsed = (time.perf_counter() - started) * 1000
            print(f"  [AGY cloud] {message} ({elapsed:.0f}ms)")

    token = load_stored_token()
    if token is None:
        log("no stored Antigravity credential found")
        return None

    access_token = token["access_token"]
    expiry = parse_expiry(token.get("expiry"))
    if expiry is not None and expiry <= datetime.now(timezone.utc):
        refreshed = refresh_access_token(token["refresh_token"])
        if refreshed:
            access_token = refreshed
            log("access token refreshed")
        else:
            log("token expired and refresh failed; trying stale token")

    groups: dict[str, dict[str, Any]] = {}
    status, raw_groups = fetch_quota_summary(access_token)
    if status == 200 and raw_groups:
        groups = parse_quota_groups(raw_groups)
        if groups:
            log(f"got {len(groups)} bucket(s) via retrieveUserQuotaSummary")
    else:
        log(f"retrieveUserQuotaSummary failed (HTTP {status}); trying models endpoint")

    if not groups:
        status, models = fetch_available_models(access_token)
        if status != 200 or not models:
            log(f"fetchAvailableModels failed (HTTP {status})")
            return None
        groups = aggregate_model_quota(models)
        if not groups:
            log("response contained no usable quota entries")
            return None
        log(f"got {len(groups)} group(s) via fetchAvailableModels")

    data = {
        "timestamp": datetime.now().isoformat(),
        "model": "AGY (cloud API)",
        "plan_tier": "?",
        "groups": groups,
    }
    _write_cache(data)
    return data


if __name__ == "__main__":
    result = fetch_from_cloud(verbose=True)
    if result is None:
        print("No cloud quota data available.")
    else:
        for name, group in result["groups"].items():
            print(
                f"{group.get('label', name)}: {group['remaining_percent']}% "
                f"(resets {group['reset_time'] or '?'})"
            )
