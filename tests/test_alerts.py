import time
from alerts import QuotaAlerts
from settings import Settings


class DummyStore:
    def __init__(self, initial_state=None):
        self.state = initial_state or {}

    def load(self, force=False):
        return self.state

    def mutate(self, updater):
        updater(self.state)


def test_quota_alerts_claims_and_shows_events(tmp_path):
    settings = Settings.load(tmp_path / "config.json")
    shown = []

    store = DummyStore({
        "providers": {
            "codex": {
                "provider_id": "codex",
                "provider_name": "Codex",
                "windows": {
                    "session": {
                        "remaining_percent": 15.0,
                        "reset_at": "2026-10-06T18:00:00Z",
                    }
                }
            }
        }
    })

    alerts = QuotaAlerts(store, settings, show=lambda events: shown.extend(events))
    events = alerts.check_once()

    assert len(events) == 1
    assert events[0].provider_id == "codex"
    assert events[0].threshold == 20
    assert len(shown) == 1

    # Second check should not duplicate
    events2 = alerts.check_once()
    assert len(events2) == 0


def test_quota_alerts_handles_show_exception(tmp_path):
    settings = Settings.load(tmp_path / "config.json")

    def broken_show(events):
        raise RuntimeError("Notification failed")

    store = DummyStore({
        "providers": {
            "codex": {
                "provider_id": "codex",
                "provider_name": "Codex",
                "windows": {
                    "session": {
                        "remaining_percent": 5.0,
                        "reset_at": "2026-10-06T18:00:00Z",
                    }
                }
            }
        }
    })

    alerts = QuotaAlerts(store, settings, show=broken_show)
    # Should not raise exception
    events = alerts.check_once()
    assert len(events) == 1


def test_quota_alerts_start_and_stop(tmp_path):
    settings = Settings.load(tmp_path / "config.json")
    store = DummyStore()
    alerts = QuotaAlerts(store, settings, show=lambda _: None)

    alerts.start()
    assert alerts._thread is not None
    assert alerts._thread.is_alive()

    alerts.stop()
    assert alerts._thread is None
