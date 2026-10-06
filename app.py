"""Entry point: the taskbar readout, plus command-line refresh/diagnostics modes."""

from __future__ import annotations

import argparse
import threading
from typing import Any, Optional

from app_paths import runtime_dir, settings_path
from instance_guard import SingleInstanceGuard
from settings import Settings
from state_store import get_store
from usage_service import RefreshScheduler, get_service


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="AI quota and token usage tracker")
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--refresh", action="store_true", help="refresh provider state once")
    modes.add_argument("--diagnostics", action="store_true", help="print a redacted health report")
    modes.add_argument("--enable-startup", action="store_true", help="enable HKCU startup entry")
    modes.add_argument("--disable-startup", action="store_true", help="disable HKCU startup entry")
    return parser


def main(
    argv: Optional[list[str]] = None,
    *,
    service: Any = None,
) -> int:
    args = _parser().parse_args(argv)
    active_service = service or get_service()

    if args.enable_startup:
        from app_paths import build_startup_command
        from startup import set_startup

        set_startup(True, build_startup_command())
        print("Windows startup enabled")
        return 0

    if args.disable_startup:
        from startup import set_startup

        set_startup(False, [])
        print("Windows startup disabled")
        return 0

    if args.diagnostics:
        from diagnostics import collect_diagnostics, render_diagnostics

        store = get_store()
        settings = Settings.load(settings_path())
        print(render_diagnostics(collect_diagnostics(settings, store.load())))
        return 0

    if args.refresh:
        snapshots = active_service.refresh(force=True)
        for provider_id, snapshot in snapshots.items():
            print(f"{provider_id}: {snapshot.status.value} ({snapshot.source or 'no source'})")
        return 0

    return _run_default(active_service)


def _run_default(service: Any) -> int:
    from alerts import QuotaAlerts
    from diagnostics import configure_logging
    from taskbar_widget import run_taskbar

    settings = Settings.load(settings_path())
    store = get_store()
    logger = configure_logging()
    guard = SingleInstanceGuard(runtime_dir() / ".instance.lock")
    if not guard.acquire():
        logger.info("An existing tracker instance is already running")
        return 0

    scheduler = RefreshScheduler(service, settings.refresh_interval_seconds)
    alerts = QuotaAlerts(store, settings)

    def refresh() -> None:
        threading.Thread(
            target=service.refresh,
            kwargs={"force": True},
            name="ai-usage-manual-refresh",
            daemon=True,
        ).start()

    try:
        logger.info("Starting taskbar and refresh scheduler")
        scheduler.start()
        alerts.start()
        ret = run_taskbar(
            store=store,
            settings=settings,
            on_open=refresh,
            on_refresh=refresh,
        )
        logger.info("run_taskbar returned %s", ret)
        return ret
    except Exception:
        logger.exception("Fatal error in tracker execution")
        raise
    finally:
        scheduler.stop()
        alerts.stop()
        guard.release()
        logger.info("Tracker stopped")


if __name__ == "__main__":
    raise SystemExit(main())
