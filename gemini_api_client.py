"""
Real-time Gemini CLI rate-limit client — queries the Code Assist backend API.

Same approach as gusage (a-hariti/gusage) and TokenTracker (xiufengsun/TokenTracker):
1. Read the OAuth credentials the Gemini CLI already stored on disk
   (``~/.gemini/oauth_creds.json`` or the encrypted ``mcp-oauth-tokens-v2.json``)
2. Refresh the access token if it is expired (oauth2.googleapis.com/token)
3. POST to cloudcode-pa.googleapis.com ``loadCodeAssist`` to get the metering
   proxy project id, then ``retrieveUserQuota`` to get per-model quota buckets

Nothing ever spawns the ``gemini`` CLI: no child process, no console popup.
Compatibles with Python 3.14, stdlib only (urllib, json, base64, hashlib).
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import ssl
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from quota_models import ProviderErrorKind, ProviderFetchError

# --- Paths ---
HOME = Path.home()
GEMINI_HOME = Path(os.environ.get("GEMINI_HOME", HOME / ".gemini"))
LEGACY_OAUTH_FILE = GEMINI_HOME / "oauth_creds.json"
ENCRYPTED_OAUTH_FILE = GEMINI_HOME / "mcp-oauth-tokens-v2.json"

# --- Endpoints ---
CODE_ASSIST_ENDPOINT = "https://cloudcode-pa.googleapis.com/v1internal"
TOKEN_URL = "https://oauth2.googleapis.com/token"
REQUEST_TIMEOUT = 8  # seconds

GEMINI_OAUTH_CLIENT_ID = os.environ.get("Q_TRACKER_GEMINI_OAUTH_CLIENT_ID", "").strip()
GEMINI_OAUTH_CLIENT_SECRET = os.environ.get("Q_TRACKER_GEMINI_OAUTH_CLIENT_SECRET", "").strip()

MAIN_ACCOUNT_KEY = "main-account"

VALID_GEMINI_MODELS = (
    "gemini-3.1-pro-preview",
    "gemini-3-flash-preview",
    "gemini-2.5-pro",
    "gemini-2.5-flash",
    "gemini-2.5-flash-lite",
)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


# --------------------------------------------------------------------------
# AES-256-GCM decryption for the encrypted v2 token file.
# Pure stdlib implementation (NIST FIPS-197 + SP 800-38D) so the project keeps
# its stdlib-only, privacy-first dependency policy. Only used to decrypt the
# CLI's own local token store; nothing is ever sent anywhere unencrypted.
# --------------------------------------------------------------------------


def _aes_round_keys(key: bytes) -> list[list[int]]:
    _SBOX = [
        0x63, 0x7C, 0x77, 0x7B, 0xF2, 0x6B, 0x6F, 0xC5, 0x30, 0x01, 0x67, 0x2B, 0xFE, 0xD7, 0xAB, 0x76,
        0xCA, 0x82, 0xC9, 0x7D, 0xFA, 0x59, 0x47, 0xF0, 0xAD, 0xD4, 0xA2, 0xAF, 0x9C, 0xA4, 0x72, 0xC0,
        0xB7, 0xFD, 0x93, 0x26, 0x36, 0x3F, 0xF7, 0xCC, 0x34, 0xA5, 0xE5, 0xF1, 0x71, 0xD8, 0x31, 0x15,
        0x04, 0xC7, 0x23, 0xC3, 0x18, 0x96, 0x05, 0x9A, 0x07, 0x12, 0x80, 0xE2, 0xEB, 0x27, 0xB2, 0x75,
        0x09, 0x83, 0x2C, 0x1A, 0x1B, 0x6E, 0x5A, 0xA0, 0x52, 0x3B, 0xD6, 0xB3, 0x29, 0xE3, 0x2F, 0x84,
        0x53, 0xD1, 0x00, 0xED, 0x20, 0xFC, 0xB1, 0x5B, 0x6A, 0xCB, 0xBE, 0x39, 0x4A, 0x4C, 0x58, 0xCF,
        0xD0, 0xEF, 0xAA, 0xFB, 0x43, 0x4D, 0x33, 0x85, 0x45, 0xF9, 0x02, 0x7F, 0x50, 0x3C, 0x9F, 0xA8,
        0x51, 0xA3, 0x40, 0x8F, 0x92, 0x9D, 0x38, 0xF5, 0xBC, 0xB6, 0xDA, 0x21, 0x10, 0xFF, 0xF3, 0xD2,
        0xCD, 0x0C, 0x13, 0xEC, 0x5F, 0x97, 0x44, 0x17, 0xC4, 0xA7, 0x7E, 0x3D, 0x64, 0x5D, 0x19, 0x73,
        0x60, 0x81, 0x4F, 0xDC, 0x22, 0x2A, 0x90, 0x88, 0x46, 0xEE, 0xB8, 0x14, 0xDE, 0x5E, 0x0B, 0xDB,
        0xE0, 0x32, 0x3A, 0x0A, 0x49, 0x06, 0x24, 0x5C, 0xC2, 0xD3, 0xAC, 0x62, 0x91, 0x95, 0xE4, 0x79,
        0xE7, 0xC8, 0x37, 0x6D, 0x8D, 0xD5, 0x4E, 0xA9, 0x6C, 0x56, 0xF4, 0xEA, 0x65, 0x7A, 0xAE, 0x08,
        0xBA, 0x78, 0x25, 0x2E, 0x1C, 0xA6, 0xB4, 0xC6, 0xE8, 0xDD, 0x74, 0x1F, 0x4B, 0xBD, 0x8B, 0x8A,
        0x70, 0x3E, 0xB5, 0x66, 0x48, 0x03, 0xF6, 0x0E, 0x61, 0x35, 0x57, 0xB9, 0x86, 0xC1, 0x1D, 0x9E,
        0xE1, 0xF8, 0x98, 0x11, 0x69, 0xD9, 0x8E, 0x94, 0x9B, 0x1E, 0x87, 0xE9, 0xCE, 0x55, 0x28, 0xDF,
        0x8C, 0xA1, 0x89, 0x0D, 0xBF, 0xE6, 0x42, 0x68, 0x41, 0x99, 0x2D, 0x0F, 0xB0, 0x54, 0xBB, 0x16,
    ]
    _RCON = [0x01, 0x02, 0x04, 0x08, 0x10, 0x20, 0x40, 0x80, 0x1B, 0x36]

    def rotate(word: list[int]) -> list[int]:
        return word[1:] + word[:1]

    def sub_word(word: list[int]) -> list[int]:
        return [_SBOX[b] for b in word]

    words: list[list[int]] = [list(key[i : i + 4]) for i in range(0, 32, 4)]
    for i in range(8, 60):
        temp = list(words[i - 1])
        if i % 8 == 0:
            temp = sub_word(rotate(temp))
            temp[0] ^= _RCON[i // 8 - 1]
        elif i % 8 == 4:
            temp = sub_word(temp)
        words.append([temp[j] ^ words[i - 8][j] for j in range(4)])
    return [sum(words[round_index * 4 : round_index * 4 + 4], []) for round_index in range(15)]


_AES_SBOX = [
    0x63, 0x7C, 0x77, 0x7B, 0xF2, 0x6B, 0x6F, 0xC5, 0x30, 0x01, 0x67, 0x2B, 0xFE, 0xD7, 0xAB, 0x76,
    0xCA, 0x82, 0xC9, 0x7D, 0xFA, 0x59, 0x47, 0xF0, 0xAD, 0xD4, 0xA2, 0xAF, 0x9C, 0xA4, 0x72, 0xC0,
    0xB7, 0xFD, 0x93, 0x26, 0x36, 0x3F, 0xF7, 0xCC, 0x34, 0xA5, 0xE5, 0xF1, 0x71, 0xD8, 0x31, 0x15,
    0x04, 0xC7, 0x23, 0xC3, 0x18, 0x96, 0x05, 0x9A, 0x07, 0x12, 0x80, 0xE2, 0xEB, 0x27, 0xB2, 0x75,
    0x09, 0x83, 0x2C, 0x1A, 0x1B, 0x6E, 0x5A, 0xA0, 0x52, 0x3B, 0xD6, 0xB3, 0x29, 0xE3, 0x2F, 0x84,
    0x53, 0xD1, 0x00, 0xED, 0x20, 0xFC, 0xB1, 0x5B, 0x6A, 0xCB, 0xBE, 0x39, 0x4A, 0x4C, 0x58, 0xCF,
    0xD0, 0xEF, 0xAA, 0xFB, 0x43, 0x4D, 0x33, 0x85, 0x45, 0xF9, 0x02, 0x7F, 0x50, 0x3C, 0x9F, 0xA8,
    0x51, 0xA3, 0x40, 0x8F, 0x92, 0x9D, 0x38, 0xF5, 0xBC, 0xB6, 0xDA, 0x21, 0x10, 0xFF, 0xF3, 0xD2,
    0xCD, 0x0C, 0x13, 0xEC, 0x5F, 0x97, 0x44, 0x17, 0xC4, 0xA7, 0x7E, 0x3D, 0x64, 0x5D, 0x19, 0x73,
    0x60, 0x81, 0x4F, 0xDC, 0x22, 0x2A, 0x90, 0x88, 0x46, 0xEE, 0xB8, 0x14, 0xDE, 0x5E, 0x0B, 0xDB,
    0xE0, 0x32, 0x3A, 0x0A, 0x49, 0x06, 0x24, 0x5C, 0xC2, 0xD3, 0xAC, 0x62, 0x91, 0x95, 0xE4, 0x79,
    0xE7, 0xC8, 0x37, 0x6D, 0x8D, 0xD5, 0x4E, 0xA9, 0x6C, 0x56, 0xF4, 0xEA, 0x65, 0x7A, 0xAE, 0x08,
    0xBA, 0x78, 0x25, 0x2E, 0x1C, 0xA6, 0xB4, 0xC6, 0xE8, 0xDD, 0x74, 0x1F, 0x4B, 0xBD, 0x8B, 0x8A,
    0x70, 0x3E, 0xB5, 0x66, 0x48, 0x03, 0xF6, 0x0E, 0x61, 0x35, 0x57, 0xB9, 0x86, 0xC1, 0x1D, 0x9E,
    0xE1, 0xF8, 0x98, 0x11, 0x69, 0xD9, 0x8E, 0x94, 0x9B, 0x1E, 0x87, 0xE9, 0xCE, 0x55, 0x28, 0xDF,
    0x8C, 0xA1, 0x89, 0x0D, 0xBF, 0xE6, 0x42, 0x68, 0x41, 0x99, 0x2D, 0x0F, 0xB0, 0x54, 0xBB, 0x16,
]


def _aes_encrypt_block(round_keys: list[list[int]], block: bytes) -> bytes:
    def sub_bytes(state: list[int]) -> list[int]:
        return [_AES_SBOX[b] for b in state]

    def shift_rows(state: list[int]) -> list[int]:
        return [
            state[0], state[5], state[10], state[15],
            state[4], state[9], state[14], state[3],
            state[8], state[13], state[2], state[7],
            state[12], state[1], state[6], state[11],
        ]

    def mix_columns(state: list[int]) -> list[int]:
        result = [0] * 16
        for column in range(4):
            a = state[column * 4 : column * 4 + 4]
            t = a[0] ^ a[1] ^ a[2] ^ a[3]
            u = a[0]
            result[column * 4 + 0] = a[0] ^ _xtime(a[0] ^ a[1]) ^ t
            result[column * 4 + 1] = a[1] ^ _xtime(a[1] ^ a[2]) ^ t
            result[column * 4 + 2] = a[2] ^ _xtime(a[2] ^ a[3]) ^ t
            result[column * 4 + 3] = a[3] ^ _xtime(a[3] ^ u) ^ t
        return result

    def add_round_key(state: list[int], key_word: list[int]) -> list[int]:
        return [state[i] ^ key_word[i] for i in range(16)]

    state = list(block)
    state = add_round_key(state, round_keys[0])
    for round_index in range(1, 14):
        state = sub_bytes(state)
        state = shift_rows(state)
        state = mix_columns(state)
        state = add_round_key(state, round_keys[round_index])
    state = sub_bytes(state)
    state = shift_rows(state)
    state = add_round_key(state, round_keys[14])
    return bytes(state)


def _xtime(value: int) -> int:
    return ((value << 1) ^ 0x1B) & 0xFF if value & 0x80 else (value << 1) & 0xFF


def _gf_multiply(x: int, y: int) -> int:
    """GF(2^128) multiplication with the GCM reduction polynomial (MSB-first)."""
    result = 0
    for bit in range(127, -1, -1):
        if (y >> bit) & 1:
            result ^= x
        carry = x & 1
        x = x >> 1
        if carry:
            x ^= 0xE1000000000000000000000000000000
    return result


def _ghash(h: int, blocks: list[bytes]) -> int:
    state = 0
    for block in blocks:
        state ^= int.from_bytes(block, "big")
        state = _gf_multiply(state, h)
    return state


def _aes_gcm_decrypt(key: bytes, iv: bytes, ciphertext: bytes, tag: bytes, aad: bytes = b"") -> bytes:
    """Decrypt AES-256-GCM and verify the authentication tag (pure stdlib)."""
    round_keys = _aes_round_keys(key)
    h = int.from_bytes(_aes_encrypt_block(round_keys, bytes(16)), "big")

    if len(iv) == 12:
        j0 = iv + b"\x00\x00\x00\x01"
    else:
        padded_iv = iv + b"\x00" * ((16 - len(iv) % 16) % 16)
        j0 = _ghash(h, [padded_iv]).to_bytes(16, "big")

    def counter_block(counter_value: bytes) -> bytes:
        increment = (int.from_bytes(counter_value[12:], "big") + 1) & 0xFFFFFFFF
        return counter_value[:12] + increment.to_bytes(4, "big")

    stream: list[bytes] = []
    current = counter_block(j0)
    padded = ciphertext + b"\x00" * ((16 - len(ciphertext) % 16) % 16)
    for offset in range(0, len(padded), 16):
        stream.append(_aes_encrypt_block(round_keys, current))
        current = counter_block(current)
    keystream = b"".join(stream)[: len(ciphertext)]

    plaintext = bytes(a ^ b for a, b in zip(ciphertext, keystream))

    aad_padded = aad + b"\x00" * ((16 - len(aad) % 16) % 16)
    ct_padded = ciphertext + b"\x00" * ((16 - len(ciphertext) % 16) % 16)
    blocks = []
    for offset in range(0, len(aad_padded), 16):
        blocks.append(aad_padded[offset : offset + 16])
    for offset in range(0, len(ct_padded), 16):
        blocks.append(ct_padded[offset : offset + 16])
    blocks.append((len(aad) * 8).to_bytes(8, "big") + (len(ciphertext) * 8).to_bytes(8, "big"))

    expected_tag = int.from_bytes(_aes_encrypt_block(round_keys, j0), "big") ^ _ghash(h, blocks)
    expected = expected_tag.to_bytes(16, "big")
    if not hmac_compare(expected[: len(tag)], tag):
        raise ValueError("Gemini token file failed authentication check")
    return plaintext


def hmac_compare(first: bytes, second: bytes) -> bool:
    """Constant-time tag comparison."""
    return hmac_digest(first) == hmac_digest(second)


def hmac_digest(value: bytes) -> bytes:
    import hashlib as _hashlib

    digest = _hashlib.sha256()
    digest.update(value)
    return digest.digest()


def _derive_gemini_key() -> bytes:
    """Recreate the scrypt key the Gemini CLI uses for its local token store."""
    salt = f"{socket_gethostname()}-{getpass_getuser()}-gemini-cli"
    return hashlib.scrypt(
        b"gemini-cli-oauth",
        salt=salt.encode("utf-8"),
        n=2**14,
        r=8,
        p=1,
        dklen=32,
    )


def socket_gethostname() -> str:
    import socket

    return socket.gethostname()


def getpass_getuser() -> str:
    import getpass

    return getpass.getuser()


def _decrypt_encrypted_tokens(raw: str) -> Optional[dict[str, Any]]:
    """
    Decrypt the v2 encrypted token store.

    Format (from gemini-cli): ``ivHex:authTagHex:ciphertextHex`` encrypted with
    AES-256-GCM under a key derived from scrypt('gemini-cli-oauth', salt).
    """
    try:
        parts = raw.strip().split(":")
        if len(parts) != 3:
            return None
        iv = bytes.fromhex(parts[0])
        auth_tag = bytes.fromhex(parts[1])
        ciphertext = bytes.fromhex(parts[2])
        key = _derive_gemini_key()
        decrypted = _aes_gcm_decrypt(key, iv, ciphertext, auth_tag)
        return json.loads(decrypted.decode("utf-8"))
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError):
        return None


# --------------------------------------------------------------------------
# Credential loading
# --------------------------------------------------------------------------


def _read_legacy_credentials() -> Optional[dict[str, Any]]:
    """Read plain-text ``oauth_creds.json`` written by older gemini-cli."""
    if not LEGACY_OAUTH_FILE.exists():
        return None
    try:
        raw = json.loads(LEGACY_OAUTH_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(raw, dict):
        return None
    access_token = raw.get("access_token")
    if not access_token or not isinstance(access_token, str):
        return None
    return {
        "access_token": access_token,
        "refresh_token": raw.get("refresh_token") or None,
        "id_token": raw.get("id_token") or None,
        "expiry_date": raw.get("expiry_date"),
    }


def _read_encrypted_credentials() -> Optional[dict[str, Any]]:
    """Read and decrypt ``mcp-oauth-tokens-v2.json`` written by modern gemini-cli."""
    if not ENCRYPTED_OAUTH_FILE.exists():
        return None
    try:
        raw = ENCRYPTED_OAUTH_FILE.read_text(encoding="utf-8")
    except OSError:
        return None
    tokens = _decrypt_encrypted_tokens(raw)
    if not isinstance(tokens, dict):
        return None
    account = tokens.get(MAIN_ACCOUNT_KEY)
    if isinstance(account, dict) and isinstance(account.get("token"), dict):
        token = account["token"]
        access_token = token.get("accessToken") or token.get("access_token")
        if isinstance(access_token, str) and access_token:
            return {
                "access_token": access_token,
                "refresh_token": token.get("refreshToken") or token.get("refresh_token") or None,
                "id_token": token.get("idToken") or token.get("id_token") or None,
                "expiry_date": token.get("expiresAt") or token.get("expiry_date"),
            }
    return None


def _read_gemini_auth() -> Optional[dict[str, Any]]:
    """Prefer the modern encrypted store, falling back to the legacy file."""
    return _read_encrypted_credentials() or _read_legacy_credentials()


def _is_token_expired(auth: dict[str, Any], buffer_seconds: int = 60) -> bool:
    expiry = auth.get("expiry_date")
    if not isinstance(expiry, (int, float)) or expiry <= 0:
        return False
    expiry_ms = float(expiry)
    if expiry_ms < 1_000_000_000_000:  # seconds → milliseconds
        expiry_ms *= 1000
    return time.time() * 1000 > expiry_ms - buffer_seconds * 1000


# --------------------------------------------------------------------------
# OAuth refresh + quota API
# --------------------------------------------------------------------------


def _refresh_access_token(auth: dict[str, Any], verbose: bool = False) -> str:
    """Refresh the access token using the CLI's public OAuth client."""
    refresh_token = auth.get("refresh_token")
    if not refresh_token:
        raise ProviderFetchError(
            ProviderErrorKind.AUTH_REQUIRED,
            "Gemini access token is expired and no refresh token is available",
        )
    if not GEMINI_OAUTH_CLIENT_ID or not GEMINI_OAUTH_CLIENT_SECRET:
        raise ProviderFetchError(
            ProviderErrorKind.AUTH_REQUIRED,
            "Gemini OAuth client is not configured; set Q_TRACKER_GEMINI_OAUTH_CLIENT_ID and Q_TRACKER_GEMINI_OAUTH_CLIENT_SECRET",
        )
    body = (
        "client_id=" + _urlencode(GEMINI_OAUTH_CLIENT_ID)
        + "&client_secret=" + _urlencode(GEMINI_OAUTH_CLIENT_SECRET)
        + "&refresh_token=" + _urlencode(str(refresh_token))
        + "&grant_type=refresh_token"
    ).encode("utf-8")
    req = Request(
        TOKEN_URL,
        data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    try:
        with urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
            if resp.status != 200:
                raise _token_refresh_failure(resp.status)
            data = json.loads(resp.read().decode("utf-8"))
    except HTTPError as error:
        raise _token_refresh_failure(error.code) from None
    except TimeoutError:
        raise ProviderFetchError(ProviderErrorKind.TIMEOUT, "Gemini token refresh timed out") from None
    except (URLError, OSError):
        raise ProviderFetchError(
            ProviderErrorKind.OTHER, "Gemini token refresh is unreachable"
        ) from None
    except json.JSONDecodeError:
        raise ProviderFetchError(
            ProviderErrorKind.PARSE, "Gemini token refresh returned invalid JSON"
        ) from None

    new_token = data.get("access_token")
    if not new_token or not isinstance(new_token, str):
        raise ProviderFetchError(
            ProviderErrorKind.AUTH_REQUIRED,
            "Gemini token refresh failed; run 'gemini' once to re-authenticate",
        )
    _persist_refreshed_token(new_token, data)
    return new_token


def _token_refresh_failure(status: int) -> ProviderFetchError:
    if status in (400, 401, 403):
        return ProviderFetchError(
            ProviderErrorKind.AUTH_REQUIRED,
            "Gemini token refresh rejected; run 'gemini' once to re-authenticate",
        )
    return ProviderFetchError(
        ProviderErrorKind.OTHER, f"Gemini token refresh returned HTTP {int(status)}"
    )


def _urlencode(value: str) -> str:
    from urllib.parse import quote

    return quote(str(value), safe="")


def _persist_refreshed_token(access_token: str, data: dict[str, Any]) -> None:
    """Best-effort update of the legacy credential file so future reads stay fresh."""
    auth = _read_legacy_credentials() or {}
    auth["access_token"] = access_token
    if isinstance(data.get("id_token"), str):
        auth["id_token"] = data["id_token"]
    if isinstance(data.get("expires_in"), (int, float)):
        auth["expiry_date"] = int((time.time() + float(data["expires_in"])) * 1000)
    try:
        LEGACY_OAUTH_FILE.write_text(
            json.dumps(auth, indent=2, ensure_ascii=False), encoding="utf-8"
        )
    except OSError:
        pass


def _load_code_assist(access_token: str) -> dict[str, Any]:
    """Get the metering proxy project id from loadCodeAssist."""
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
    }
    body = json.dumps(
        {
            "metadata": {
                "ideType": "GEMINI_CLI",
                "platform": "PLATFORM_UNSPECIFIED",
                "pluginType": "GEMINI",
            }
        }
    ).encode("utf-8")
    req = Request(
        f"{CODE_ASSIST_ENDPOINT}:loadCodeAssist",
        data=body,
        headers=headers,
        method="POST",
    )
    try:
        with urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
            if resp.status != 200:
                raise _code_assist_failure(resp.status)
            return json.loads(resp.read().decode("utf-8"))
    except HTTPError as error:
        raise _code_assist_failure(error.code) from None
    except TimeoutError:
        raise ProviderFetchError(ProviderErrorKind.TIMEOUT, "Gemini loadCodeAssist timed out") from None
    except (URLError, OSError):
        raise ProviderFetchError(
            ProviderErrorKind.OTHER, "Gemini loadCodeAssist is unreachable"
        ) from None
    except json.JSONDecodeError:
        raise ProviderFetchError(
            ProviderErrorKind.PARSE, "Gemini loadCodeAssist returned invalid JSON"
        ) from None


def _code_assist_failure(status: int) -> ProviderFetchError:
    if status in (401, 403):
        return ProviderFetchError(
            ProviderErrorKind.AUTH_REQUIRED,
            "Gemini authentication is required; run 'gemini' and sign in once",
        )
    if status == 429:
        return ProviderFetchError(
            ProviderErrorKind.RATE_LIMITED, "Gemini quota API rate limit reached"
        )
    return ProviderFetchError(
        ProviderErrorKind.OTHER, f"Gemini loadCodeAssist returned HTTP {int(status)}"
    )


def _retrieve_user_quota(access_token: str, project_id: str) -> dict[str, Any]:
    """Fetch per-model quota buckets from retrieveUserQuota."""
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
    }
    body = json.dumps({"project": project_id}).encode("utf-8")
    req = Request(
        f"{CODE_ASSIST_ENDPOINT}:retrieveUserQuota",
        data=body,
        headers=headers,
        method="POST",
    )
    try:
        with urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
            if resp.status != 200:
                raise _quota_failure(resp.status)
            return json.loads(resp.read().decode("utf-8"))
    except HTTPError as error:
        raise _quota_failure(error.code) from None
    except TimeoutError:
        raise ProviderFetchError(ProviderErrorKind.TIMEOUT, "Gemini quota API timed out") from None
    except (URLError, OSError):
        raise ProviderFetchError(
            ProviderErrorKind.OTHER, "Gemini quota API is unreachable"
        ) from None
    except json.JSONDecodeError:
        raise ProviderFetchError(
            ProviderErrorKind.PARSE, "Gemini quota API returned invalid JSON"
        ) from None


def _quota_failure(status: int) -> ProviderFetchError:
    if status in (401, 403):
        return ProviderFetchError(
            ProviderErrorKind.AUTH_REQUIRED,
            "Gemini authentication is required; run 'gemini' and sign in once",
        )
    if status == 429:
        return ProviderFetchError(
            ProviderErrorKind.RATE_LIMITED, "Gemini quota API rate limit reached"
        )
    return ProviderFetchError(
        ProviderErrorKind.OTHER, f"Gemini quota API returned HTTP {int(status)}"
    )


# --------------------------------------------------------------------------
# Normalization
# --------------------------------------------------------------------------


def _parse_iso_to_epoch(value: Any) -> int:
    if not isinstance(value, str) or not value.strip():
        return 0
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return int(parsed.astimezone(timezone.utc).timestamp())
    except ValueError:
        return 0


def _plan_label(tier: Any) -> str:
    if tier == "standard-tier":
        return "Paid"
    if tier == "legacy-tier":
        return "Legacy"
    if tier == "free-tier":
        return "Free"
    return str(tier or "unknown")


def _classify_model(model_id: str) -> str:
    lower = str(model_id or "").lower()
    if "flash-lite" in lower:
        return "flash_lite"
    if "flash" in lower:
        return "flash"
    if "pro" in lower or "preview" in lower:
        return "pro"
    return "other"


def _normalize_quota_response(body: dict[str, Any], tier: Any) -> Optional[dict[str, Any]]:
    """
    Parse the retrieveUserQuota response into per-model quota windows.

    Response shape::

        {"buckets": [
            {"modelId": "gemini-2.5-pro", "remainingFraction": 0.8, "resetTime": "..."},
            ...
        ]}

    Buckets are grouped by model family (pro / flash / flash-lite); the lowest
    remaining fraction wins for families with several model variants.
    """
    buckets = body.get("buckets")
    if not isinstance(buckets, list):
        return None

    by_family: dict[str, dict[str, Any]] = {}
    for bucket in buckets:
        if not isinstance(bucket, dict):
            continue
        model_id = bucket.get("modelId") or bucket.get("model_id")
        if not isinstance(model_id, str) or not model_id:
            continue
        family = _classify_model(model_id)
        if family == "other":
            if model_id not in VALID_GEMINI_MODELS:
                continue
            family = "pro"
        remaining = bucket.get("remainingFraction")
        if remaining is None:
            remaining = bucket.get("remaining_fraction")
        try:
            remaining = float(remaining)
        except (TypeError, ValueError):
            remaining = None
        if remaining is None:
            continue
        remaining = max(0.0, min(1.0, remaining))
        reset = bucket.get("resetTime") or bucket.get("reset_time") or ""
        existing = by_family.get(family)
        if existing is None or remaining < existing["remaining_fraction"]:
            by_family[family] = {
                "model_id": model_id,
                "remaining_fraction": remaining,
                "reset_time": reset,
            }

    if not by_family:
        return None

    now = _utc_now_iso()
    models: dict[str, dict[str, Any]] = {}
    for family, value in by_family.items():
        reset_at = _parse_iso_to_epoch(value["reset_time"])
        used_percent = round((1.0 - value["remaining_fraction"]) * 100, 1)
        models[family] = {
            "used_percent": used_percent,
            "percent_left": round(value["remaining_fraction"] * 100, 1),
            "remaining_fraction": value["remaining_fraction"],
            "resets_at": reset_at,
            "model_id": value["model_id"],
        }

    return {
        "plan_type": _plan_label(tier),
        "timestamp": now,
        "models": models,
    }


def fetch_gemini_live_limits(verbose: bool = False) -> Optional[dict[str, Any]]:
    """
    Main entry point — fetch Gemini CLI quota without spawning the CLI.

    Returns a dict with:

    - ``plan_type``: Paid / Legacy / Free / unknown
    - ``timestamp``: ISO fetch time
    - ``models``: mapping of ``pro`` / ``flash`` / ``flash_lite`` to per-model
      windows with ``used_percent``, ``percent_left``, ``resets_at``

    Raises ``ProviderFetchError`` (credential-free) on any failure.
    """
    auth = _read_gemini_auth()
    if not auth:
        if verbose:
            print("  [Gemini API] No credentials in ~/.gemini (run 'gemini' once)")
        raise ProviderFetchError(
            ProviderErrorKind.AUTH_REQUIRED,
            "Gemini authentication is unavailable; run 'gemini' and sign in once",
        )

    access_token = auth["access_token"]
    if _is_token_expired(auth):
        if verbose:
            print("  [Gemini API] Access token expired; refreshing…")
        access_token = _refresh_access_token(auth, verbose=verbose)

    code_assist = _load_code_assist(access_token)
    raw_project = code_assist.get("cloudaicompanionProject")
    project_id = None
    if isinstance(raw_project, str):
        project_id = raw_project.strip() or None
    elif isinstance(raw_project, dict):
        project_id = (
            raw_project.get("projectId")
            or raw_project.get("id")
        )
        if isinstance(project_id, str):
            project_id = project_id.strip() or None
        else:
            project_id = None
    tier = None
    if isinstance(code_assist.get("currentTier"), dict):
        tier = code_assist["currentTier"].get("id")

    if not project_id:
        if verbose:
            print("  [Gemini API] loadCodeAssist returned no project id")
        raise ProviderFetchError(
            ProviderErrorKind.OTHER, "Gemini API returned no metering project id"
        )

    if verbose:
        print(f"  [Gemini API] project={project_id} tier={tier}")

    body = _retrieve_user_quota(access_token, project_id)
    result = _normalize_quota_response(body, tier)
    if result is None:
        if verbose:
            print("  [Gemini API] Quota response had no usable buckets")
        raise ProviderFetchError(
            ProviderErrorKind.PARSE, "Gemini quota API returned an unsupported response"
        )

    if verbose:
        for family, window in result["models"].items():
            print(
                f"  [Gemini API] {family}: {window['percent_left']:.1f}% left "
                f"(used {window['used_percent']:.1f}%)"
            )
    return result


def main():
    """CLI test mode — run the full pipeline with diagnostics (no CLI spawned)."""
    print("=" * 60)
    print("Gemini API Client — Real-time Quota Fetcher (no CLI process)")
    print("=" * 60)
    print()
    auth = _read_gemini_auth()
    if not auth:
        print("❌ No Gemini credentials found. Run `gemini` and sign in once.")
        print(f"   Looked in: {ENCRYPTED_OAUTH_FILE}")
        print(f"             {LEGACY_OAUTH_FILE}")
        return

    print(f"✓ Credentials found (token {len(auth['access_token'])} chars)")
    expired = _is_token_expired(auth)
    print(f"  access token expired: {expired}")

    import time as _time

    t0 = _time.perf_counter()
    try:
        result = fetch_gemini_live_limits(verbose=True)
    except ProviderFetchError as error:
        print(f"❌ {error}")
        return
    elapsed_ms = (_time.perf_counter() - t0) * 1000
    print(f"  Time: {elapsed_ms:.0f}ms")
    print()
    if result:
        print(f"Plan: {result['plan_type']}")
        for family, window in result["models"].items():
            reset = window["resets_at"]
            reset_text = (
                datetime.fromtimestamp(reset, tz=timezone.utc).isoformat() if reset else "?"
            )
            print(f"  {family}: {window['percent_left']:.1f}% left (resets {reset_text})")
        print()
        print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
