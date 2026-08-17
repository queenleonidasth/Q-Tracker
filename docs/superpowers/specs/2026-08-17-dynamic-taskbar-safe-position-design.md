# Dynamic Taskbar Safe Position Design

## Goal

Keep Q-Tracker's existing taskbar text and size while ensuring its window never covers the Windows notification-area controls, including the system-tray expand arrow (`^`). The position must adapt to the actual notification-area width instead of assuming that every taskbar reserves the same number of pixels.

## Evidence and root cause

The current horizontal placement uses the fixed `TASKBAR_RIGHT_RESERVE = 230` value. On the active Windows taskbar, the `TrayNotifyWnd` child currently begins at x=3169 while `Shell_TrayWnd` ends at x=3440. The 400-pixel Q-Tracker child therefore ends at x=3210 and overlaps the first notification-area controls by 41 pixels. Because Q-Tracker is a normal taskbar child window, its transparent surface still participates in hit-testing and blocks clicks even where no text is painted.

The active window hierarchy confirms that Explorer exposes the notification area as a visible `TrayNotifyWnd` child of `Shell_TrayWnd`. Win32's `EnumChildWindows` enumerates child descendants, and `GetWindowRect` returns their screen-coordinate rectangles; these APIs are suitable for discovering the boundary at runtime. The implementation will still have a deterministic fixed-reserve fallback when the child is unavailable during Explorer transitions or on an incompatible shell layout.

## Positioning design

1. Keep `Shell_TrayWnd` as the taskbar geometry source and keep Q-Tracker as its existing child window.
2. Enumerate the taskbar's descendants and select a visible window whose class is exactly `TrayNotifyWnd`.
3. Validate that the discovered rectangle has positive dimensions, lies within the taskbar's horizontal span, and overlaps the taskbar vertically.
4. For a horizontal taskbar, set the overlay's maximum right edge to `TrayNotifyWnd.left - 12` pixels. The 12-pixel gap is a hit-test safety buffer, not a visual text margin.
5. If no valid notification-area rectangle is available, retain the current 230-pixel fallback reserve.
6. If the dynamic boundary leaves less room than the requested overlay width, clamp the overlay width to the available space so it cannot cross into the notification area. Normal displays retain the existing effective 400 x 48 size.
7. Preserve the existing vertical-taskbar behavior, transparent rendering, z-order repair, fullscreen policy, and taskbar-relative client-coordinate conversion.

The pure geometry helper will accept an optional notification-area rectangle in the same coordinate space as the taskbar rectangle so the placement policy can be tested without Win32 calls. `TrayNotifyWnd` is discovered in screen coordinates and converted into `Shell_TrayWnd` client coordinates with `ScreenToClient` before the helper is called. The Win32 discovery and conversion helpers will be defensive: missing APIs, transient Explorer handles, invalid rectangles, and callback errors return `None` and use the fallback path.

## Data flow

`_reposition` → `_taskbar_child_position` → `TrayNotifyWnd` discovery in screen coordinates → `ScreenToClient` conversion → pure horizontal geometry calculation in client coordinates → `SetWindowPos`.

The discovery runs only when geometry is recalculated. It does not change quota refresh cadence or add a new timer.

## Tests and acceptance criteria

- A valid notification area moves a 400-pixel overlay so its right edge is exactly 12 pixels before `TrayNotifyWnd.left`.
- The existing 230-pixel reserve remains the fallback when no notification area is discoverable.
- Invalid, off-taskbar, or zero-sized notification rectangles are ignored.
- A notification area narrower than the fixed assumption never causes overlay overlap; the overlay width is clamped to the available taskbar space.
- Child-coordinate conversion subtracts the taskbar's screen-left offset correctly.
- Existing geometry, z-order, fullscreen, rendering, and full-suite tests remain green.
- A Windows smoke check confirms the active notification-area rectangle is discovered and the widget's right edge is before it.

## Scope

This change affects only taskbar overlay geometry and its regression tests. It does not alter quota data, text content, settings schema, taskbar auto-hide, multi-taskbar support, or click handling inside the widget.
