"""
Real-time AGY quota client — queries the running AGY language server's local HTTP API.
Zero process creation for AGY itself, zero CMD popups.

Approach (same as TokenTracker xiufengsun/TokenTracker usage-limits.js):
1. Find running agy.exe process (wmic on Windows)
2. Find its listening TCP port (netstat)
3. POST to /exa.language_server_pb.LanguageServerService/RetrieveUserQuotaSummary
4. Parse quota groups (gemini-5h, gemini-weekly, 3p-5h, 3p-weekly)
5. If AGY not running → return None (caller falls back to cached data)

Compatible with Python 3.14, stdlib only, Windows-specific.
"""

from __future__ import annotations

import json
import os
import re
import ssl
import subprocess
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from urllib.request import Request, urlopen
from urllib.error import URLError

# --- Paths ---
HOME = Path.home()
AGY_QUOTA_CACHE = HOME / ".tokentracker" / "tracker" / "agy_quota_cache.json"

# --- Windows subprocess helpers (no window flash) ---
# CREATE_NO_WINDOW prevents any console window from flashing
_CREATE_NO_WINDOW = 0x08000000


def _get_startupinfo():
    """STARTUPINFO with SW_HIDE to suppress any window flash."""
    si = subprocess.STARTUPINFO()
    si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    si.wShowWindow = 0  # SW_HIDE
    return si


def _run_silent(cmd: list[str], timeout: float = 3.0) -> str:
    """Run a command silently (no window), return stdout or empty string."""
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            startupinfo=_get_startupinfo(),
            creationflags=_CREATE_NO_WINDOW,
        )
        return result.stdout or ""
    except (subprocess.TimeoutExpired, OSError, FileNotFoundError):
        return ""


# --- Step 1: Find running agy.exe process ---

def _find_agy_pids() -> list[int]:
    """
    Find PIDs of running agy.exe processes using wmic.
    
    wmic is instant (<50ms) and doesn't create visible windows when
    called with CREATE_NO_WINDOW + STARTUPINFO(SW_HIDE).
    
    Returns list of PIDs (may be multiple if AGY has child processes).
    """
    # Method 1: wmic (available on all Windows versions)
    output = _run_silent([
        "wmic", "process", "where", "name='agy.exe'",
        "get", "processid", "/format:csv"
    ])
    
    pids = []
    if output.strip():
        for line in output.strip().splitlines():
            line = line.strip()
            if not line or line.lower().startswith("node"):
                continue
            # CSV format: Node,ProcessId
            parts = line.split(",")
            for part in parts:
                part = part.strip()
                if part.isdigit():
                    pids.append(int(part))
    
    if pids:
        return pids
    
    # Method 2: Fallback to tasklist (if wmic is deprecated on newer Windows)
    output = _run_silent(["tasklist", "/FI", "IMAGENAME eq agy.exe", "/FO", "CSV", "/NH"])
    if output.strip() and "agy.exe" in output.lower():
        for line in output.strip().splitlines():
            # CSV: "agy.exe","12345","Console","1","123,456 K"
            parts = line.strip().strip('"').split('","')
            if len(parts) >= 2 and parts[1].strip('"').isdigit():
                pids.append(int(parts[1].strip('"')))
    
    return pids


# --- Step 2: Find listening TCP ports for a PID ---

def _find_listening_ports(pid: int) -> list[int]:
    """
    Find TCP ports that a specific PID is listening on using netstat.
    
    netstat -ano is instant and gives us all TCP connections with PIDs.
    We filter for LISTENING state and our target PID.
    """
    output = _run_silent(["netstat", "-ano", "-p", "TCP"])
    
    ports = set()
    pid_str = str(pid)
    
    for line in output.splitlines():
        # Typical netstat line:
        #   TCP    127.0.0.1:12345    0.0.0.0:0    LISTENING    12345
        #   TCP    0.0.0.0:12345      0.0.0.0:0    LISTENING    12345
        line = line.strip()
        if "LISTENING" not in line:
            continue
        
        parts = line.split()
        if len(parts) < 5:
            continue
        
        # Last column is PID
        if parts[-1] != pid_str:
            continue
        
        # Second column is local address (IP:port)
        local_addr = parts[1]
        match = re.search(r":(\d+)$", local_addr)
        if match:
            port = int(match.group(1))
            # Skip well-known system ports (AGY uses high ports)
            if port > 1024:
                ports.add(port)
    
    return sorted(ports)


def _find_all_agy_ports() -> list[int]:
    """Find all listening ports for any running agy.exe process."""
    pids = _find_agy_pids()
    if not pids:
        return []
    
    all_ports = []
    for pid in pids:
        ports = _find_listening_ports(pid)
        all_ports.extend(ports)
    
    # Deduplicate and sort
    return sorted(set(all_ports))


# --- Step 3: HTTP request helpers ---

def _make_request(
    scheme: str,
    port: int,
    path: str,
    body: dict,
    timeout: float = 5.0,
) -> Optional[dict]:
    """
    POST JSON to the AGY language server and return parsed response.
    
    Uses urllib.request with SSL verification disabled (localhost, self-signed cert).
    """
    url = f"{scheme}://127.0.0.1:{port}{path}"
    raw_body = json.dumps(body).encode("utf-8")
    
    headers = {
        "Content-Type": "application/json",
        "Connect-Protocol-Version": "1",
        "Content-Length": str(len(raw_body)),
    }
    
    req = Request(url, data=raw_body, headers=headers, method="POST")
    
    # SSL context: disable verification for localhost (AGY uses self-signed cert)
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    
    try:
        with urlopen(req, timeout=timeout, context=ctx) as resp:
            if resp.status != 200:
                return None
            data = resp.read().decode("utf-8")
            return json.loads(data)
    except (URLError, OSError, json.JSONDecodeError, TimeoutError):
        return None


def _default_body() -> dict:
    """Request body for quota endpoints (same as TokenTracker)."""
    return {
        "metadata": {
            "ideName": "antigravity",
            "extensionName": "antigravity",
            "ideVersion": "unknown",
            "locale": "en",
        }
    }


def _unleash_body() -> dict:
    """Request body for the probe endpoint (GetUnleashData)."""
    return {
        "context": {
            "properties": {
                "devMode": "false",
                "extensionVersion": "unknown",
                "hasAnthropicModelAccess": "true",
                "ide": "antigravity",
                "ideVersion": "unknown",
                "installationId": "codex-tracker",
                "language": "UNSPECIFIED",
                "os": "windows",
                "requestedModelId": "MODEL_UNSPECIFIED",
            }
        }
    }


# --- Step 3b: Probe port to verify it's an AGY language server ---

def _probe_port(port: int) -> Optional[str]:
    """
    Probe the AGY unencrypted HTTP JSON-RPC listener only.

    AGY also exposes a separate TLS listener. Sending plain HTTP to that TLS
    port makes AGY print a noisy "client sent an HTTP request to an HTTPS
    server" message. Callers must therefore pass only the higher HTTP/RPC
    listener port discovered for each agy.exe process.
    """
    result = _make_request(
        scheme="http",
        port=port,
        path="/exa.language_server_pb.LanguageServerService/GetUnleashData",
        body=_unleash_body(),
        timeout=3.0,
    )
    if result is not None:
        return "http"
    return None


# --- Step 4: Fetch quota data ---

def _parse_quota_summary(body: dict) -> Optional[dict]:
    """
    Parse RetrieveUserQuotaSummary response into our cache format.
    
    Response structure (from TokenTracker's normalizeAntigravityQuotaSummary):
    {
      "code": 0,  (or null — both mean OK)
      "response": {
        "groups": [
          {
            "buckets": [
              {"bucketId": "gemini-5h", "remainingFraction": 0.85, "resetTime": "..."},
              {"bucketId": "gemini-weekly", "remainingFraction": 0.72, "resetTime": "..."},
              ...
            ]
          }
        ]
      }
    }
    """
    # Validate response code
    code = body.get("code")
    if code is not None and code != 0:
        return None
    
    # Extract groups
    response = body.get("response", {})
    groups_list = response.get("groups", [])
    if not isinstance(groups_list, list) or not groups_list:
        return None
    
    # Flatten all buckets from all groups into bucketId-keyed map
    buckets = {}
    for group in groups_list:
        if not isinstance(group, dict):
            continue
        for bucket in group.get("buckets", []):
            if not isinstance(bucket, dict):
                continue
            bucket_id = bucket.get("bucketId", "")
            if bucket_id:
                buckets[bucket_id] = bucket
    
    if not buckets:
        return None
    
    # Build our cache format
    now_iso = datetime.now().isoformat()
    cache_groups = {}
    
    for bucket_id, bucket in buckets.items():
        remaining_fraction = bucket.get("remainingFraction")
        if remaining_fraction is None or not isinstance(remaining_fraction, (int, float)):
            remaining_fraction = 1.0
        
        remaining_fraction = max(0.0, min(1.0, float(remaining_fraction)))
        remaining_percent = round(remaining_fraction * 100, 1)
        
        reset_time = bucket.get("resetTime", "")
        
        # Calculate reset_in_seconds from resetTime
        reset_in_seconds = 0
        if reset_time:
            try:
                # Parse ISO timestamp
                reset_dt = datetime.fromisoformat(reset_time.replace("Z", "+00:00"))
                now_dt = datetime.now(timezone.utc)
                delta = (reset_dt - now_dt).total_seconds()
                reset_in_seconds = max(0, int(delta))
            except (ValueError, TypeError):
                pass
        
        cache_groups[bucket_id] = {
            "remaining_percent": remaining_percent,
            "remaining_fraction": round(remaining_fraction, 7),
            "reset_time": reset_time,
            "reset_in_seconds": reset_in_seconds,
        }
    
    return {
        "timestamp": now_iso,
        "model": "AGY (live API)",
        "plan_tier": "?",
        "groups": cache_groups,
    }


def _parse_user_status(body: dict) -> Optional[dict]:
    """
    Parse GetUserStatus response (fallback endpoint).
    
    Structure varies but typically has quotaInfo or similar.
    Falls back to extracting from modelConfigs if needed.
    """
    code = body.get("code")
    if code is not None and code != 0:
        return None
    
    # Try quotaInfo path
    quota_info = body.get("quotaInfo") or body.get("quota_info")
    if isinstance(quota_info, dict):
        groups = quota_info.get("groups", [])
        if groups:
            # Same format as quota summary, reuse parser
            return _parse_quota_summary({"code": 0, "response": {"groups": groups}})
    
    # Try modelConfigs path (GetCommandModelConfigs fallback)
    configs = body.get("modelConfigs") or body.get("model_configs", [])
    if isinstance(configs, list) and configs:
        return _parse_model_configs(configs)
    
    return None


def _parse_model_configs(configs: list) -> Optional[dict]:
    """
    Parse GetCommandModelConfigs response as last-resort fallback.
    
    Each config may have a quota field with remainingFraction and resetTime.
    """
    now_iso = datetime.now().isoformat()
    cache_groups = {}
    
    for config in configs:
        if not isinstance(config, dict):
            continue
        quota = config.get("quota")
        if not isinstance(quota, dict):
            continue
        
        bucket_id = quota.get("bucketId", "")
        if not bucket_id:
            continue
        
        remaining_fraction = quota.get("remainingFraction", 1.0)
        if not isinstance(remaining_fraction, (int, float)):
            remaining_fraction = 1.0
        
        remaining_fraction = max(0.0, min(1.0, float(remaining_fraction)))
        reset_time = quota.get("resetTime", "")
        
        reset_in_seconds = 0
        if reset_time:
            try:
                reset_dt = datetime.fromisoformat(reset_time.replace("Z", "+00:00"))
                now_dt = datetime.now(timezone.utc)
                delta = (reset_dt - now_dt).total_seconds()
                reset_in_seconds = max(0, int(delta))
            except (ValueError, TypeError):
                pass
        
        cache_groups[bucket_id] = {
            "remaining_percent": round(remaining_fraction * 100, 1),
            "remaining_fraction": round(remaining_fraction, 7),
            "reset_time": reset_time,
            "reset_in_seconds": reset_in_seconds,
        }
    
    if not cache_groups:
        return None
    
    return {
        "timestamp": now_iso,
        "model": "AGY (live API / modelConfigs)",
        "plan_tier": "?",
        "groups": cache_groups,
    }


# --- Step 5: Write cache ---

def _write_cache(data: dict) -> None:
    """Write quota data to agy_quota_cache.json for other consumers."""
    temporary = None
    try:
        AGY_QUOTA_CACHE.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(
            prefix=f".{AGY_QUOTA_CACHE.stem}-",
            suffix=".tmp",
            dir=AGY_QUOTA_CACHE.parent,
        )
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(data, handle, indent=2, ensure_ascii=False)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, AGY_QUOTA_CACHE)
        temporary = None
    except OSError:
        pass  # Non-critical: cache write failure shouldn't break anything
    finally:
        if temporary is not None:
            try:
                os.unlink(temporary)
            except OSError:
                pass


# --- Public API ---

def fetch_from_running_agy(verbose: bool = False) -> Optional[dict]:
    """
    Query the running AGY language server's HTTP API for real-time quota data.
    
    Returns:
        dict in agy_quota_cache.json format (with "groups" key), or None if
        AGY is not running or unreachable.
    
    This function:
    1. Finds agy.exe process(es) via wmic (no window, <50ms)
    2. Finds listening TCP ports via netstat (no window, <50ms)
    3. Probes each port with GetUnleashData to find the API port
    4. Fetches quota via RetrieveUserQuotaSummary (primary) or GetUserStatus (fallback)
    5. Writes result to agy_quota_cache.json for other consumers
    
    Total time: typically <1 second (wmic + netstat + 1 HTTP request)
    """
    t0 = time.perf_counter()
    
    # Step 1: Find AGY process
    pids = _find_agy_pids()
    if not pids:
        if verbose:
            print("  [AGY API] agy.exe not running")
        return None
    
    if verbose:
        elapsed = (time.perf_counter() - t0) * 1000
        print(f"  [AGY API] Found agy.exe PID(s): {pids} ({elapsed:.0f}ms)")
    
    # Step 2: Find the HTTP/RPC listener candidate for each AGY process.
    # AGY binds TLS first and its unencrypted HTTP JSON-RPC listener second,
    # so the HTTP listener is normally the higher port. Never probe a process
    # until both listeners are present; this avoids sending HTTP to a TLS-only
    # startup state.
    all_ports = []
    candidate_ports = []
    for pid in pids:
        ports = _find_listening_ports(pid)
        all_ports.extend(ports)
        if len(ports) >= 2:
            candidate_ports.append(max(ports))
    all_ports = sorted(set(all_ports))
    candidate_ports = sorted(set(candidate_ports), reverse=True)
    
    if not candidate_ports:
        if verbose:
            print("  [AGY API] HTTP/RPC listener not ready; skipping local probe")
        return None
    
    if verbose:
        elapsed = (time.perf_counter() - t0) * 1000
        print(f"  [AGY API] Listening ports: {all_ports}; HTTP candidates: {candidate_ports} ({elapsed:.0f}ms)")
    
    # Step 3: Probe only HTTP/RPC candidates. Never probe the TLS listener.
    working_port = None
    working_scheme = None
    
    for port in candidate_ports:
        scheme = _probe_port(port)
        if scheme:
            working_port = port
            working_scheme = scheme
            break
    
    if not working_port:
        if verbose:
            print("  [AGY API] No working API port found (probe failed on all ports)")
        return None
    
    if verbose:
        elapsed = (time.perf_counter() - t0) * 1000
        print(f"  [AGY API] Working port: {working_scheme}://127.0.0.1:{working_port} ({elapsed:.0f}ms)")
    
    # Step 4a: Try RetrieveUserQuotaSummary (preferred — structured buckets)
    body = _default_body()
    response = _make_request(
        scheme=working_scheme,
        port=working_port,
        path="/exa.language_server_pb.LanguageServerService/RetrieveUserQuotaSummary",
        body=body,
        timeout=5.0,
    )
    
    if response:
        data = _parse_quota_summary(response)
        if data:
            if verbose:
                elapsed = (time.perf_counter() - t0) * 1000
                print(f"  [AGY API] Got quota via RetrieveUserQuotaSummary ({elapsed:.0f}ms)")
            _write_cache(data)
            return data
    
    # Step 4b: Fallback to GetUserStatus
    response = _make_request(
        scheme=working_scheme,
        port=working_port,
        path="/exa.language_server_pb.LanguageServerService/GetUserStatus",
        body=body,
        timeout=5.0,
    )
    
    if response:
        data = _parse_user_status(response)
        if data:
            if verbose:
                elapsed = (time.perf_counter() - t0) * 1000
                print(f"  [AGY API] Got quota via GetUserStatus ({elapsed:.0f}ms)")
            _write_cache(data)
            return data
    
    # Step 4c: Last resort — GetCommandModelConfigs
    response = _make_request(
        scheme=working_scheme,
        port=working_port,
        path="/exa.language_server_pb.LanguageServerService/GetCommandModelConfigs",
        body=body,
        timeout=5.0,
    )
    
    if response:
        data = _parse_user_status(response)  # Handles modelConfigs internally
        if data:
            if verbose:
                elapsed = (time.perf_counter() - t0) * 1000
                print(f"  [AGY API] Got quota via GetCommandModelConfigs ({elapsed:.0f}ms)")
            _write_cache(data)
            return data
    
    if verbose:
        elapsed = (time.perf_counter() - t0) * 1000
        print(f"  [AGY API] All endpoints failed ({elapsed:.0f}ms)")
    
    return None


def is_agy_running() -> bool:
    """Quick check: is agy.exe currently running? (No port probing.)"""
    return len(_find_agy_pids()) > 0


def get_agy_port_info() -> dict:
    """
    Diagnostic: return info about the running AGY process and its ports.
    Useful for testing/debugging.
    """
    pids = _find_agy_pids()
    if not pids:
        return {"running": False, "pids": [], "ports": [], "working_port": None}
    
    all_ports = []
    candidate_ports = []
    for pid in pids:
        ports = _find_listening_ports(pid)
        all_ports.extend(ports)
        if len(ports) >= 2:
            candidate_ports.append(max(ports))
    all_ports = sorted(set(all_ports))
    candidate_ports = sorted(set(candidate_ports), reverse=True)
    
    working_port = None
    working_scheme = None
    for port in candidate_ports:
        scheme = _probe_port(port)
        if scheme:
            working_port = port
            working_scheme = scheme
            break
    
    return {
        "running": True,
        "pids": pids,
        "ports": all_ports,
        "http_candidates": candidate_ports,
        "working_port": working_port,
        "working_scheme": working_scheme,
    }


# --- CLI for testing ---

def main():
    """Test the AGY API client from command line."""
    import pprint
    
    print("=" * 60)
    print("AGY API Client — Real-time Quota Fetcher")
    print("=" * 60)
    print()
    
    # Step 1: Check if AGY is running
    print("[1] Detecting agy.exe process...")
    pids = _find_agy_pids()
    if not pids:
        print("    ✗ agy.exe is NOT running.")
        print("    → Will return cached data (if available).")
        if AGY_QUOTA_CACHE.exists():
            print(f"    → Cache exists: {AGY_QUOTA_CACHE}")
            try:
                cache = json.loads(AGY_QUOTA_CACHE.read_text(encoding="utf-8"))
                print(f"    → Cache timestamp: {cache.get('timestamp', '?')}")
            except Exception:
                pass
        return
    print(f"    ✓ Found agy.exe PID(s): {pids}")
    
    # Step 2: Find ports
    print("\n[2] Finding listening TCP ports...")
    for pid in pids:
        ports = _find_listening_ports(pid)
        print(f"    PID {pid}: ports = {ports}")
    
    all_ports = _find_all_agy_ports()
    if not all_ports:
        print("    ✗ No listening ports found!")
        return
    print(f"    ✓ All ports: {all_ports}")
    
    # Step 3: Probe ports
    print("\n[3] Probing ports for AGY API...")
    for port in all_ports:
        scheme = _probe_port(port)
        if scheme:
            print(f"    ✓ Port {port} responds ({scheme})")
        else:
            print(f"    ✗ Port {port} — no response")
    
    # Step 4: Fetch quota
    print("\n[4] Fetching real-time quota...")
    t0 = time.perf_counter()
    result = fetch_from_running_agy(verbose=True)
    elapsed = (time.perf_counter() - t0) * 1000
    
    print(f"\n[Result] ({elapsed:.0f}ms total)")
    if result:
        print("    ✓ Got live quota data:")
        groups = result.get("groups", {})
        for name, g in groups.items():
            pct = g.get("remaining_percent", "?")
            reset = g.get("reset_time", "?")
            print(f"      {name}: {pct}% remaining (resets {reset})")
        print(f"\n    Cache written to: {AGY_QUOTA_CACHE}")
    else:
        print("    ✗ Failed to fetch quota from API")
    
    print()


if __name__ == "__main__":
    main()
