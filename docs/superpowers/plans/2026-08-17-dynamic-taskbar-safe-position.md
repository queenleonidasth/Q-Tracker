# Dynamic Taskbar Safe Position Implementation Plan

> For agentic workers: REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox syntax for tracking.

**Goal:** Position Q-Tracker immediately to the left of the actual Windows notification area so the system-tray expand arrow and icons remain clickable, while preserving a safe fallback during Explorer transitions.

**Architecture:** Discover the visible TrayNotifyWnd descendant of Shell_TrayWnd with EnumChildWindows and GetWindowRect. Convert its validated screen rectangle into Shell_TrayWnd client coordinates with ScreenToClient, then feed it into the existing pure geometry calculation with a 12-pixel hit-test gap and the existing 230-pixel reserve when discovery fails. Pass the resulting client rectangle directly to SetWindowPos.

**Tech Stack:** Python 3.13, ctypes Win32 User32 APIs, pytest, PyInstaller.

## Global Constraints

- Keep the effective normal horizontal overlay size at 400 x 48 pixels.
- Keep 230 pixels as the fallback right reserve when no valid notification area is available.
- Keep a 12-pixel gap between the overlay and the discovered notification area.
- Keep Q-Tracker as a child of Shell_TrayWnd; do not change ownership, z-order, timers, fullscreen behavior, or rendering.
- Only clamp width when the dynamic boundary leaves less room than the requested overlay width.
- Preserve the existing vertical-taskbar fallback behavior.

---

### Task 1: Add failing geometry and notification-area tests

**Files:**
- Modify: tests/test_taskbar_widget.py near the existing geometry tests

**Interfaces:**
- Consumes: Existing taskbar_bounds and configured width inputs.
- Produces: Regression expectations for an optional notification-area rectangle and defensive Win32 child enumeration.

- [ ] Step 1: Write the failing pure-geometry tests

Add these tests after the existing fixed-reserve geometry test:

    def test_horizontal_position_uses_notification_area_boundary():
        """A discovered notification area defines the safe right edge."""
        assert widget._taskbar_overlay_position(
            (0, 1392, 3440, 1440),
            460,
            (3169, 1392, 3440, 1440),
        ) == (2757, 1392, 400, 48)


    def test_horizontal_position_clamps_width_before_narrow_notification_area():
        """A narrow band shrinks the child instead of crossing the tray."""
        position = widget._taskbar_overlay_position(
            (0, 1392, 600, 1440),
            460,
            (300, 1392, 600, 1440),
        )

        assert position == (0, 1392, 288, 48)


    def test_horizontal_position_keeps_fixed_reserve_without_notification_area():
        """Explorer transitions retain the deterministic fallback."""
        assert widget._taskbar_overlay_position(
            (0, 1032, 1920, 1080),
            460,
            None,
        ) == (1290, 1032, 400, 48)

- [ ] Step 2: Write the failing child-discovery tests

Add these tests after the geometry tests:

    def test_notification_area_bounds_discovers_visible_tray_notify_window(monkeypatch):
        """The visible TrayNotifyWnd child supplies the tray boundary."""
        monkeypatch.setattr(widget, "_window_class_name", lambda hwnd: {
            11: "TrayNotifyWnd",
            12: "OtherTaskbarChild",
        }[hwnd])
        monkeypatch.setattr(widget, "u32", SimpleNamespace(
            EnumChildWindows=lambda _parent, callback, _data: (
                callback(12, 0),
                callback(11, 0),
                True,
            )[-1],
            IsWindowVisible=lambda hwnd: hwnd == 11,
            GetWindowRect=_rect_reader((3169, 1392, 3440, 1440)),
        ))

        assert widget._taskbar_notification_bounds(99) == (
            3169,
            1392,
            3440,
            1440,
        )


    def test_notification_area_bounds_ignores_invalid_geometry(monkeypatch):
        """A zero-sized child cannot move the widget unsafely."""
        monkeypatch.setattr(widget, "_window_class_name", lambda _hwnd: "TrayNotifyWnd")
        monkeypatch.setattr(widget, "u32", SimpleNamespace(
            EnumChildWindows=lambda _parent, callback, _data: callback(11, 0),
            IsWindowVisible=lambda _hwnd: True,
            GetWindowRect=_rect_reader((0, 0, 0, 0)),
        ))

        assert widget._taskbar_notification_bounds(99) is None

- [ ] Step 3: Run only the new tests and verify RED

Run:

    .\.venv\Scripts\python.exe -m pytest tests\test_taskbar_widget.py -k "notification_area or uses_notification_area or clamps_width" -q

Expected: FAIL because _taskbar_overlay_position accepts only two arguments and _taskbar_notification_bounds does not exist. The failures must be feature-related rather than import or syntax errors.

- [ ] Step 4: Commit the red tests

    git add -- tests/test_taskbar_widget.py
    git commit -m "test: cover dynamic taskbar notification boundary"

### Task 2: Implement safe notification-area discovery and pure geometry

**Files:**
- Modify: taskbar_widget.py near the Win32 declarations and geometry helpers
- Test: tests/test_taskbar_widget.py from Task 1

**Interfaces:**
- Consumes: Shell_TrayWnd handle, existing taskbar bounds, optional TrayNotifyWnd bounds.
- Produces: _taskbar_notification_bounds(taskbar) returning an optional screen rectangle and _taskbar_overlay_position(taskbar_bounds, configured_width, notification_bounds=None).

- [ ] Step 1: Add callback type, constants, and optional binding

Keep the existing aliases and add:

    WNDENUMPROC = ctypes.WINFUNCTYPE(BOOL, HWND, LPARAM)
    TASKBAR_NOTIFICATION_CLASS = "TrayNotifyWnd"
    TASKBAR_NOTIFICATION_GAP = 12

After the existing GetWindowRect binding, add:

    if hasattr(u32, "EnumChildWindows"):
        u32.EnumChildWindows.argtypes = [HWND, WNDENUMPROC, LPARAM]
        u32.EnumChildWindows.restype = BOOL

- [ ] Step 2: Add the defensive notification-area helper

Place this near _window_class_name:

    def _taskbar_notification_bounds(
        taskbar: HWND,
    ) -> Optional[tuple[int, int, int, int]]:
        enum_children = getattr(u32, "EnumChildWindows", None)
        get_window_rect = getattr(u32, "GetWindowRect", None)
        is_window_visible = getattr(u32, "IsWindowVisible", None)
        if enum_children is None or get_window_rect is None:
            return None

        candidates: list[tuple[int, int, int, int]] = []

        def collect(hwnd: HWND, _data: LPARAM) -> bool:
            try:
                if _window_class_name(hwnd) != TASKBAR_NOTIFICATION_CLASS:
                    return True
                if is_window_visible is not None and not is_window_visible(hwnd):
                    return True
                bounds = wintypes.RECT()
                if not get_window_rect(hwnd, ctypes.byref(bounds)):
                    return True
                if bounds.right <= bounds.left or bounds.bottom <= bounds.top:
                    return True
                candidates.append((
                    bounds.left,
                    bounds.top,
                    bounds.right,
                    bounds.bottom,
                ))
            except (AttributeError, OSError, TypeError):
                return True
            return True

        callback = WNDENUMPROC(collect)
        try:
            enum_children(taskbar, callback, 0)
        except (AttributeError, OSError, TypeError):
            return None
        return max(candidates, key=lambda bounds: bounds.left, default=None)

- [ ] Step 3: Extend the pure geometry helper

Change the signature to accept notification_bounds=None. For horizontal taskbars, keep the fixed reserve as fallback, validate that the notification rectangle is inside the taskbar span and vertically overlaps it, then use notification_left minus TASKBAR_NOTIFICATION_GAP as the safe right edge. Clamp the normal width to available space:

    def _taskbar_overlay_position(
        taskbar_bounds: tuple[int, int, int, int],
        configured_width: int,
        notification_bounds: Optional[tuple[int, int, int, int]] = None,
    ) -> Optional[tuple[int, int, int, int]]:
        left, top, right, bottom = taskbar_bounds
        taskbar_width = right - left
        taskbar_height = bottom - top
        if taskbar_width <= 0 or taskbar_height <= 0:
            return None
        width = taskbar_overlay_width(configured_width, taskbar_width)
        if taskbar_width >= taskbar_height:
            safe_right = right - TASKBAR_RIGHT_RESERVE
            if notification_bounds is not None:
                notification_left, notification_top, notification_right, notification_bottom = notification_bounds
                overlaps_taskbar = notification_top < bottom and notification_bottom > top
                inside_taskbar = (
                    left < notification_left < right
                    and notification_left < notification_right
                )
                if overlaps_taskbar and inside_taskbar:
                    safe_right = notification_left - TASKBAR_NOTIFICATION_GAP
            safe_right = min(right, safe_right)
            available_width = max(1, safe_right - left)
            width = min(width, available_width)
            x = max(left, safe_right - width)
            return x, top, width, taskbar_height
        width = taskbar_width
        height = min(180, max(60, taskbar_height - 150))
        return left, bottom - height - 100, width, height

- [ ] Step 4: Run focused tests and verify GREEN

    .\.venv\Scripts\python.exe -m pytest tests\test_taskbar_widget.py -k "notification_area or uses_notification_area or clamps_width or horizontal_position_keeps_fixed" -q

Expected: all new and selected existing geometry tests pass.

- [ ] Step 5: Commit the discovery and pure geometry implementation

    git add -- taskbar_widget.py tests/test_taskbar_widget.py
    git commit -m "fix: calculate taskbar position from notification area"

### Task 3: Integrate dynamic geometry into taskbar child positioning

**Files:**
- Modify: taskbar_widget.py near _taskbar_child_position and _reposition
- Test: tests/test_taskbar_widget.py near the existing reposition tests
- Modify: README.md taskbar behavior paragraph

**Interfaces:**
- Consumes: _taskbar_notification_bounds, _taskbar_overlay_position, current taskbar screen/client rectangles.
- Produces: Client-coordinate SetWindowPos calls whose right edge stays before the notification area.

- [ ] Step 1: Add a failing integration test for nonzero taskbar screen-left

Add:

    def test_reposition_uses_dynamic_notification_boundary_in_client_coordinates(monkeypatch):
        runtime = _runtime(_view("test"))
        runtime.settings.display = {"width": 460}
        set_position_calls = []
        monkeypatch.setattr(widget, "_runtime", runtime)
        monkeypatch.setattr(
            widget,
            "u32",
            SimpleNamespace(
                FindWindowW=lambda *_: 99,
                GetClientRect=_rect_reader((0, 0, 1920, 48)),
                GetWindowRect=_rect_reader((1000, 1032, 2920, 1080)),
                ScreenToClient=lambda _h, point: (
                    setattr(point._obj, "x", point._obj.x - 1000)
                    or setattr(point._obj, "y", point._obj.y - 1032)
                    or 1
                ),
                SetWindowPos=lambda *args: set_position_calls.append(args) or 1,
            ),
        )
        monkeypatch.setattr(
            widget,
            "_taskbar_notification_bounds",
            lambda _taskbar: (2569, 1032, 2920, 1080),
        )

        assert widget._reposition(100) is True
        assert set_position_calls == [
            (
                100,
                None,
                1157,
                0,
                400,
                48,
                widget.SWP_NOZORDER | widget.SWP_NOACTIVATE,
            )
        ]

- [ ] Step 2: Run the integration test and verify RED

    .\.venv\Scripts\python.exe -m pytest tests\test_taskbar_widget.py::test_reposition_uses_dynamic_notification_boundary_in_client_coordinates -q

Expected: FAIL because _reposition currently supplies no notification bounds to _taskbar_child_position.

- [ ] Step 3: Thread screen notification geometry into child positioning

In _taskbar_child_position, keep the current client rectangle for width/height. Call _taskbar_notification_bounds(taskbar) in screen coordinates, convert that rectangle to taskbar client coordinates with ScreenToClient, and calculate horizontal placement using the client rectangle. If discovery or conversion is unavailable, pass None as the notification boundary so the existing fixed-reserve client-coordinate behavior remains active.

Add the ScreenToClient binding and a helper equivalent to:

    def _taskbar_notification_client_bounds(
        taskbar: HWND,
        screen_bounds: tuple[int, int, int, int],
    ) -> Optional[tuple[int, int, int, int]]:
        screen_to_client = getattr(u32, "ScreenToClient", None)
        if screen_to_client is None:
            return None
        top_left = wintypes.POINT(screen_bounds[0], screen_bounds[1])
        bottom_right = wintypes.POINT(screen_bounds[2], screen_bounds[3])
        if not screen_to_client(taskbar, ctypes.byref(top_left)):
            return None
        if not screen_to_client(taskbar, ctypes.byref(bottom_right)):
            return None
        return top_left.x, top_left.y, bottom_right.x, bottom_right.y

    notification_bounds = _taskbar_notification_bounds(taskbar)
    if notification_bounds is not None:
        notification_bounds = _taskbar_notification_client_bounds(
            taskbar,
            notification_bounds,
        )
    client_bounds = (client.left, client.top, client.right, client.bottom)
    position = _taskbar_overlay_position(
        client_bounds,
        configured_width,
        notification_bounds,
    )
    if position is None:
        return None
    return position

Retain the existing vertical-taskbar client branch and the existing screen-coordinate fallback in _reposition for transient/mocked geometry. The new dynamic path must use client coordinates so an existing resolution/DPI change test that reports a 2560-pixel client width still anchors at the same client position.

- [ ] Step 4: Run focused positioning tests and verify GREEN

    .\.venv\Scripts\python.exe -m pytest tests\test_taskbar_widget.py -k "position or reposition" -q

Expected: the dynamic integration test and all existing positioning tests pass.

- [ ] Step 5: Update README

Extend the taskbar/fullscreen paragraph with one sentence explaining that the widget reads the live notification-area boundary and falls back safely while Explorer relays out the taskbar. Do not document a user setting.

- [ ] Step 6: Commit the integration change

    git add -- taskbar_widget.py tests/test_taskbar_widget.py README.md
    git commit -m "fix: keep taskbar tray controls clickable"

### Task 4: Full verification and Windows smoke check

**Files:**
- Verify: taskbar_widget.py, tests/test_taskbar_widget.py, README.md

- [ ] Step 1: Run the complete pytest suite

    .\.venv\Scripts\python.exe -m pytest -q

Expected: the baseline 144 tests plus the new regression tests pass with zero failures.

- [ ] Step 2: Check formatting and inspect the final diff

    git diff --check
    git status --short
    git diff HEAD~1 --stat
    git diff HEAD~1 -- taskbar_widget.py tests/test_taskbar_widget.py README.md

Confirm only the dynamic positioning scope changed and no unrelated user edits are present.

- [ ] Step 3: Run the active Windows taskbar discovery smoke check

Use the project's Python interpreter to import taskbar_widget, resolve Shell_TrayWnd, call _taskbar_notification_bounds, and print the taskbar and notification rectangles. Confirm the result is a positive TrayNotifyWnd rectangle whose left edge is inside the taskbar.

- [ ] Step 4: Build the packaged app

    powershell -ExecutionPolicy Bypass -File .\build.ps1

Expected: PyInstaller exits successfully and produces the packaged executable. Do not terminate the user's running tracker process automatically; the new packaged build takes effect after the normal Q-Tracker restart.

- [ ] Step 5: Record verification evidence before handoff

Report the exact pytest count, build exit status, active notification-area coordinates, and the restart instruction. Do not claim the running process has moved until it has been restarted and observed.
