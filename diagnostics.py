"""Redacted health diagnostics and bounded application logging."""

from __future__ import annotations

import json
import logging
import platform
import re
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

from app_paths import is_frozen, log_dir, runtime_dir, settings_path, state_path
from settings import Settings


LOGGER_NAME = "q_tracker"
_SENSITIVE = re.compile(
    r"(?i)(access[_-]?token|refresh[_-]?token|authorization|csrf|api[_-]?key)"
    r"(\s*[=:]\s*|\s+)([^\s,;\"'}]+)"
)
_BEARER = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/-]+")


class _RedactingFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        message = _SENSITIVE.sub(lambda match: f"{match.group(1)}=<redacted>", message)
        message = _BEARER.sub("Bearer <redacted>", message)
        record.msg = message
        record.args = ()
        return True


def configure_logging(directory: Path | None = None) -> logging.Logger:
    """Configure one rotating application log without ever recording secrets."""
    destination = Path(directory) if directory is not None else log_dir()
    destination.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(logging.INFO)
    logger.propagate = False
    for handler in list(logger.handlers):
        if getattr(handler, "_ai_usage_tracker_handler", False):
            logger.removeHandler(handler)
            handler.close()
    handler = RotatingFileHandler(
        destination / "app.log",
        maxBytes=1_000_000,
        backupCount=3,
        encoding="utf-8",
    )
    handler._ai_usage_tracker_handler = True  # type: ignore[attr-defined]
    handler.addFilter(_RedactingFilter())
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(threadName)s %(message)s")
    )
    logger.addHandler(handler)
    return logger


def _sanitized_path(path: Path) -> str:
    resolved = Path(path).resolve()
    try:
        relative = resolved.relative_to(Path.home().resolve())
        return str(Path("%USERPROFILE%") / relative)
    except ValueError:
        return str(resolved)


def _int(value: Any) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


def collect_diagnostics(settings: Settings, state: dict[str, Any]) -> dict[str, Any]:
    """Collect only explicitly safe health fields from settings and state."""
    meta = state.get("_meta") if isinstance(state.get("_meta"), dict) else {}
    providers_raw = state.get("providers") if isinstance(state.get("providers"), dict) else {}
    providers: dict[str, dict[str, Any]] = {}
    for provider_id, raw in providers_raw.items():
        if not isinstance(raw, dict):
            continue
        providers[str(provider_id)] = {
            "status": str(raw.get("status") or "unknown"),
            "source": str(raw.get("source") or ""),
            "observed_at": raw.get("observed_at"),
            "refreshed_at": raw.get("refreshed_at"),
            "error_kind": str(raw.get("error_kind") or ""),
            "has_quota_windows": bool(raw.get("windows")),
        }

    usage = state.get("usage") if isinstance(state.get("usage"), dict) else {}
    scanner_root = usage.get("scanner") if isinstance(usage.get("scanner"), dict) else {}
    scanner_raw = (
        scanner_root.get("codex_diagnostics")
        if isinstance(scanner_root.get("codex_diagnostics"), dict)
        else {}
    )
    scanner = {
        "files_seen": _int(scanner_raw.get("files_seen")),
        "files_scanned": _int(scanner_raw.get("files_scanned")),
        "malformed_lines": _int(scanner_raw.get("malformed_lines")),
        "updated_at": scanner_raw.get("updated_at"),
    }
    return {
        "application": {
            "name": "Q-Tracker",
            "version": "1.4.0",
            "frozen": is_frozen(),
        },
        "runtime": {
            "python": platform.python_version(),
            "platform": platform.platform(),
            "executable": _sanitized_path(Path(sys.executable)),
            "data_directory": _sanitized_path(runtime_dir()),
        },
        "files": {
            "state_exists": state_path().exists(),
            "settings_exists": settings_path().exists(),
        },
        "settings": {
            "refresh_interval_seconds": settings.refresh_interval_seconds,
            "enabled_providers": list(settings.enabled_providers),
            "notification_thresholds": list(settings.notification_thresholds),
        },
        "state": {
            "schema_version": _int(meta.get("schema_version")),
            "written_at": meta.get("written_at"),
        },
        "providers": providers,
        "scanner": scanner,
    }


def render_diagnostics(report: dict[str, Any]) -> str:
    return json.dumps(report, indent=2, ensure_ascii=False, sort_keys=True)

