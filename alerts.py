"""Low-quota desktop alerts without a permanent tray icon.

Windows balloon notifications need a notification-area icon, so one is shown
only for the few seconds an alert is on screen and removed afterwards.
"""

from __future__ import annotations

import logging
import threading
from typing import Any, Callable, Iterable, Optional

from notifications import NotificationEvent, NotificationPolicy
from settings import Settings


LOGGER = logging.getLogger(__name__)
CHECK_INTERVAL_SECONDS = 5.0
ALERT_VISIBLE_SECONDS = 10.0


def _show_with_transient_icon(events: Iterable[NotificationEvent]) -> None:
    import pystray
    from PIL import Image, ImageDraw

    image = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    ImageDraw.Draw(image).ellipse((4, 4, 60, 60), fill="#FFB454")
    icon = pystray.Icon("q_tracker_alert", image, "Q-Tracker")
    icon.run_detached()
    try:
        for event in events:
            icon.notify(event.message, event.title)
        threading.Event().wait(ALERT_VISIBLE_SECONDS)
    finally:
        icon.stop()


class QuotaAlerts:
    """Background watcher that turns threshold crossings into desktop alerts."""

    def __init__(
        self,
        store: Any,
        settings: Settings,
        show: Callable[[Iterable[NotificationEvent]], None] = _show_with_transient_icon,
    ):
        self.store = store
        self.show = show
        self._policy = NotificationPolicy(settings.notification_thresholds)
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def check_once(self) -> list[NotificationEvent]:
        state = self.store.load()
        providers = state.get("providers") if isinstance(state.get("providers"), dict) else {}
        events = self._policy.claim(self.store, providers)
        if events:
            try:
                self.show(events)
            except Exception:  # alerts must never take the taskbar down
                LOGGER.exception("Could not display quota alert")
        return events

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.check_once()
            except Exception:
                LOGGER.exception("Quota alert check failed")
            if self._stop.wait(CHECK_INTERVAL_SECONDS):
                break

    def start(self) -> None:
        if self._thread is not None:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="quota-alerts", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._thread = None
