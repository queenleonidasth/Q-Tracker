# Q-Tracker

Q-Tracker is a lightweight Windows 11 utility that displays real-time AI quota and token usage for OpenAI Codex, Antigravity (AGY), and Google Gemini CLI directly on your taskbar and system tray.

Instead of interrupting your work to check web dashboards or run CLI status commands, Q-Tracker sits unobtrusively on the taskbar, showing remaining quota percentages and reset countdowns at a glance.

## Features

- Taskbar integration: Attaches directly to the Windows 11 taskbar (`Shell_TrayWnd`). It dynamically calculates free space and positions itself safely next to the system tray, automatically avoiding the Start button and taskbar buttons regardless of whether your taskbar is set to center or left alignment.
- Explorer recovery: Survives Windows Explorer crashes and restarts. A dedicated thread timer detects shell restarts and re-attaches the window without requiring an app restart.
- Codex tracking: Shows both the 5-hour session window and weekly quota with accurate reset countdowns. Also aggregates token usage by reading local session history (`~/.codex/sessions`) incrementally.
- Antigravity (AGY) tracking: Connects to the local running AGY instance, or falls back to Google's Cloud API by reading existing OAuth credentials from Windows Credential Manager (`gemini:antigravity`) so it works even when AGY is closed.
- Gemini CLI tracking: Reads stored OAuth credentials from `~/.gemini` to track quota buckets for Gemini Pro, Flash, and Flash Lite models via the Google Code Assist backend.
- Quiet operation: Queries local APIs and official OAuth endpoints directly without spawning console windows or background CLI child processes.
- Threshold alerts: Displays desktop notifications when quota drops to 20%, 10%, or 5% (alerting once per threshold per reset cycle).
- Built-in dashboard: Double-click the taskbar readout or tray icon to open the full dashboard with per-provider details, token usage breakdown (today, this month, all-time), and connection health.
- Privacy-first: Runs entirely locally. No analytics, no telemetry, and no secret keys or tokens are ever written to logs or state files.

## Installation

### Pre-built Binary

Download the latest `Q-Tracker-v1.4.0-windows-x64.zip` from the [Releases](https://github.com/queenleonidasth/Q-Tracker/releases/latest) page, extract the archive, and run `Q-Tracker.exe`. No Python setup is required.

### Running from Source

Requires Windows 11 and Python 3.13+.

1. Clone the repository:
   ```cmd
   git clone https://github.com/queenleonidasth/Q-Tracker.git
   cd Q-Tracker
   ```

2. Run the setup script to configure the virtual environment:
   ```powershell
   powershell -ExecutionPolicy Bypass -File .\setup.ps1
   ```

3. Launch the application:
   ```cmd
   .\run.bat
   ```
   `run.bat` uses `pythonw.exe` so it runs silently in the background without keeping a command prompt open.

## Controls & Indicators

- Double-click (taskbar text or tray icon): Opens the dashboard.
- Right-click (taskbar text or tray icon): Opens a menu to refresh data, view token summary, or exit.
- Start with Windows: Can be enabled directly from the dashboard settings. It writes a run key under `HKCU\Software\Microsoft\Windows\CurrentVersion\Run` and requires no administrator rights.

### Status Symbols

| Symbol | Description |
|---|---|
| *(none)* | OK. Data is current and confirmed by the provider. |
| `~` | Stale. Showing last verified values while waiting for the next successful update. |
| `!` | Unavailable or error. Communication failed; last known good values are preserved. |

Readouts turn yellow when quota is at or below 20%, and red when at or below 10%.

## Provider Authentication

### Codex
Q-Tracker automatically picks up existing local session tokens from your Codex CLI environment. Make sure you have logged in via the Codex CLI at least once.

### Antigravity (AGY)
Sign in once via the `agy` CLI so credentials are stored in Windows Credential Manager under `gemini:antigravity`. Q-Tracker reads these tokens to check quotas even when AGY is not running.

To support automatic token refresh in automated environments, configure these environment variables:
- `Q_TRACKER_AGY_OAUTH_CLIENT_ID`
- `Q_TRACKER_AGY_OAUTH_CLIENT_SECRET`

### Gemini CLI
Sign in once with the `gemini` CLI so tokens are saved in `~/.gemini/`. Q-Tracker reads these credentials to query the Code Assist API directly.

For automated refresh:
- `Q_TRACKER_GEMINI_OAUTH_CLIENT_ID`
- `Q_TRACKER_GEMINI_OAUTH_CLIENT_SECRET`

## Configuration

Configuration is stored in `data/config.json` (source mode) or `%LOCALAPPDATA%\Q-Tracker\config.json` (binary mode):

```json
{
  "refresh_interval_seconds": 60,
  "enabled_providers": ["agy", "codex"],
  "notification_thresholds": [0.2, 0.1, 0.05],
  "display": {
    "taskbar_windows": {
      "codex": ["session", "weekly"]
    }
  }
}
```

- `display.taskbar_windows`: Select which windows to display on the taskbar per provider. Supported IDs: `session`, `weekly`, `monthly`, `code_review`, `pro`, `flash`, `flash_lite`.

## Building

To build the standalone executable package:

```powershell
powershell -ExecutionPolicy Bypass -File .\build.ps1
```

The script runs the full test suite and packages the application with PyInstaller into `dist\Q-Tracker\`.

## CLI Utilities

```powershell
# Refresh all providers once and print statuses
.\.venv\Scripts\python.exe .\app.py --refresh

# Print a sanitized diagnostics report
.\.venv\Scripts\python.exe .\app.py --diagnostics

# Open dashboard directly
.\.venv\Scripts\pythonw.exe .\app.py --dashboard

# Run tests
.\.venv\Scripts\python.exe -m pytest -q
```

## Data Storage & Privacy

- Source execution stores files in `<project>\data`.
- Standalone execution stores files in `%LOCALAPPDATA%\Q-Tracker`.
- File writes use atomic replacement and cross-process file locks (`msvcrt.locking`) to protect against corruption.
- No personal data or credentials leave your machine. Network calls only communicate directly with the respective provider APIs.

## License

Distributed under the MIT License. See [LICENSE](LICENSE) for details.
