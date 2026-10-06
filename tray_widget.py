"""System tray controller; the full Tk dashboard runs in a separate main-thread process."""

from __future__ import annotations

import threading
from typing import Any, Callable, Optional

import pystray
from PIL import Image, ImageDraw, ImageFont
from pystray import MenuItem as item

from settings import Settings
from state_store import AtomicStateStore
from notifications import NotificationPolicy
from ui_models import TrackerView, build_tracker_view, format_tokens


class TokenTrayIcon:
    def __init__(
        self,
        store: AtomicStateStore,
        service: Any,
        settings: Settings,
        on_open: Callable[[], Any],
        on_exit: Optional[Callable[[], Any]] = None,
    ):
        self.store = store
        self.service = service
        self.settings = settings
        self.on_open = on_open
        self.on_exit = on_exit
        self.icon: Optional[pystray.Icon] = None
        self._stop = threading.Event()
        self._monitor: Optional[threading.Thread] = None
        self._notifications = NotificationPolicy(settings.notification_thresholds)

    def _create_image(self, view: Optional[TrackerView] = None) -> Image.Image:
        view = view or build_tracker_view(self.store.load(), self.settings.enabled_providers)
        remaining = [window.remaining_percent for provider in view.providers for window in provider.windows]
        lowest = min(remaining) if remaining else 0
        ring = "#FF6B6B" if remaining and lowest <= 10 else "#FFB454" if remaining and lowest <= 20 else "#6CB6FF"
        image = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
        draw = ImageDraw.Draw(image)
        draw.ellipse((3, 3, 61, 61), fill="#171B25", outline=ring, width=4)
        try:
            font = ImageFont.truetype("segoeuib.ttf", 27)
        except OSError:
            font = ImageFont.load_default()
        text = "Q"
        bounds = draw.textbbox((0, 0), text, font=font)
        width, height = bounds[2] - bounds[0], bounds[3] - bounds[1]
        draw.text(((64 - width) / 2, (64 - height) / 2 - 3), text, fill=ring, font=font)
        return image

    def start_detached(self) -> None:
        if self.icon is not None:
            return
        menu = pystray.Menu(
            item("Open dashboard", lambda _icon, _item: self.on_open(), default=True),
            item("Refresh now", self._refresh),
            item("Today's token summary", self._show_summary),
            pystray.Menu.SEPARATOR,
            item("Exit", self._quit),
        )
        self.icon = pystray.Icon(
            "q_tracker",
            self._create_image(),
            "Q-Tracker",
            menu,
        )
        self.icon.run_detached()
        self._stop.clear()
        self._monitor = threading.Thread(target=self._monitor_state, name="tray-state", daemon=True)
        self._monitor.start()

    def stop(self) -> None:
        self._stop.set()
        if self.icon is not None:
            try:
                self.icon.stop()
            finally:
                self.icon = None

    def _monitor_state(self) -> None:
        last_fingerprint = None
        while not self._stop.is_set():
            state = self.store.load()
            view = build_tracker_view(state, self.settings.enabled_providers)
            if view.fingerprint != last_fingerprint:
                last_fingerprint = view.fingerprint
                events = self._notifications.claim(
                    self.store,
                    state.get("providers", {}) if isinstance(state.get("providers"), dict) else {},
                )
                if self.icon is not None:
                    self.icon.icon = self._create_image(view)
                    self.icon.title = view.compact_text[:127] or "Q-Tracker"
                    for event in events:
                        self.icon.notify(event.message, event.title)
            if self._stop.wait(5.0):
                break

    def _refresh(self, _icon=None, _item=None) -> None:
        threading.Thread(
            target=self.service.refresh,
            kwargs={"force": True},
            name="tray-refresh",
            daemon=True,
        ).start()

    def _show_summary(self, _icon=None, _item=None) -> None:
        view = build_tracker_view(self.store.load(), self.settings.enabled_providers)
        if self.icon is not None:
            self.icon.notify(
                f"Today {format_tokens(view.token_totals['today'])} · Month {format_tokens(view.token_totals['month'])}",
                "ChatGPT token usage",
            )

    def _quit(self, _icon=None, _item=None) -> None:
        if self.on_exit is not None:
            self.on_exit()
        self.stop()


if __name__ == "__main__":
    from app import main

    raise SystemExit(main([]))
