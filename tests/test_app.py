import sys

import app
from quota_models import FetchStatus, ProviderSnapshot
from settings import Settings


def test_refresh_mode_forces_service_refresh_and_returns_success(capsys):
    """The command-line refresh path must use the same service as the GUI."""
    calls = []

    class Service:
        def refresh(self, provider_id=None, force=False):
            calls.append((provider_id, force))
            return {
                "codex": ProviderSnapshot(
                    provider_id="codex",
                    provider_name="Codex",
                    status=FetchStatus.OK,
                    source="live_api",
                )
            }

    result = app.main(["--refresh"], service=Service())

    assert result == 0
    assert calls == [(None, True)]
    assert "codex: ok" in capsys.readouterr().out.lower()


def test_enable_and_disable_startup_flags(monkeypatch, capsys):
    startup_calls = []

    monkeypatch.setattr(
        "startup.set_startup",
        lambda enabled, cmd: startup_calls.append((enabled, cmd)),
    )
    monkeypatch.setattr(
        "app_paths.build_startup_command",
        lambda: ["Q-Tracker.exe"],
    )

    res_enable = app.main(["--enable-startup"])
    assert res_enable == 0
    assert startup_calls[-1] == (True, ["Q-Tracker.exe"])
    assert "windows startup enabled" in capsys.readouterr().out.lower()

    res_disable = app.main(["--disable-startup"])
    assert res_disable == 0
    assert startup_calls[-1] == (False, [])
    assert "windows startup disabled" in capsys.readouterr().out.lower()


def test_diagnostics_mode_prints_redacted_health_report(tmp_path, monkeypatch, capsys):
    settings = Settings.load(tmp_path / "config.json")

    class Store:
        def load(self):
            return {
                "providers": {
                    "codex": {
                        "status": "ok",
                        "source": "live_api",
                        "access_token": "must-not-leak",
                    }
                }
            }

    monkeypatch.setattr(app, "get_store", lambda: Store())
    monkeypatch.setattr(app.Settings, "load", lambda _path: settings)

    result = app.main(["--diagnostics"], service=object())
    output = capsys.readouterr().out

    assert result == 0
    assert '"status": "ok"' in output
    assert "must-not-leak" not in output
