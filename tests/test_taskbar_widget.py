from types import SimpleNamespace

import pytest

import taskbar_widget as widget
from ui_models import ProviderView, TrackerView, WindowView


def _view(text: str) -> TrackerView:
    return TrackerView(
        providers=(),
        compact_text=text,
        token_totals={"today": 0, "month": 0, "lifetime": 0},
    )


def _runtime(view: TrackerView, ticks: int = 0) -> SimpleNamespace:
    return SimpleNamespace(
        ticks=ticks,
        view=view,
        store=SimpleNamespace(load=lambda: {}),
        settings=SimpleNamespace(enabled_providers=("agy", "codex")),
        last_position=None,
        overlay_hidden=False,
    )


def _rect_reader(bounds):
    def read(_hwnd, rect_pointer):
        rect = rect_pointer._obj
        rect.left, rect.top, rect.right, rect.bottom = bounds
        return 1

    return read


def _monitor_info_reader(bounds_by_monitor):
    def read(monitor, info_pointer):
        bounds = bounds_by_monitor[monitor]
        info = info_pointer._obj
        (
            info.rcMonitor.left,
            info.rcMonitor.top,
            info.rcMonitor.right,
            info.rcMonitor.bottom,
        ) = bounds
        return 1

    return read


def _class_name_reader(class_name):
    def read(_hwnd, buffer, _length):
        buffer.value = class_name
        return len(class_name)

    return read


def _window(window_id: str, label: str, remaining: float) -> WindowView:
    return WindowView(window_id, label, label, remaining, 100 - remaining, None, "—", "normal")


def _provider(provider_id: str, name: str, windows: tuple[WindowView, ...]) -> ProviderView:
    return ProviderView(
        provider_id,
        name,
        "ok",
        "",
        name,
        windows,
        "test",
        None,
        None,
        "now",
        "test",
        "",
    )


def test_right_aligned_start_keeps_twelve_pixel_margin():
    """Short content must end twelve pixels before the overlay's right edge."""
    assert widget._right_aligned_start(client_width=400, content_width=320) == 68


def test_right_aligned_start_falls_back_to_left_margin_for_overflow():
    """Long content must not receive a negative or off-window start coordinate."""
    assert widget._right_aligned_start(client_width=400, content_width=410) == 10


def test_horizontal_position_keeps_230_pixel_right_reserve():
    """A geometry refresh must preserve the user's current taskbar-relative anchor."""
    assert widget._taskbar_overlay_position((0, 1032, 1920, 1080), 460) == (
        1290,
        1032,
        400,
        48,
    )


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


def test_horizontal_position_ignores_notification_area_outside_taskbar():
    """A rectangle extending beyond the taskbar cannot override the fallback."""
    assert widget._taskbar_overlay_position(
        (0, 1032, 1920, 1080),
        460,
        (1500, 1032, 2500, 1080),
    ) == (1290, 1032, 400, 48)


def test_notification_area_bounds_discovers_visible_tray_notify_window(monkeypatch):
    """The visible TrayNotifyWnd child supplies the tray boundary."""
    monkeypatch.setattr(
        widget,
        "_window_class_name",
        lambda hwnd: {
            11: "TrayNotifyWnd",
            12: "OtherTaskbarChild",
        }[hwnd],
    )
    monkeypatch.setattr(
        widget,
        "u32",
        SimpleNamespace(
            EnumChildWindows=lambda _parent, callback, _data: (
                callback(12, 0),
                callback(11, 0),
                True,
            )[-1],
            IsWindowVisible=lambda hwnd: hwnd == 11,
            GetWindowRect=_rect_reader((3169, 1392, 3440, 1440)),
        ),
    )

    assert widget._taskbar_notification_bounds(99) == (
        3169,
        1392,
        3440,
        1440,
    )


def test_notification_area_bounds_ignores_invalid_geometry(monkeypatch):
    """A zero-sized child cannot move the widget unsafely."""
    monkeypatch.setattr(widget, "_window_class_name", lambda _hwnd: "TrayNotifyWnd")
    monkeypatch.setattr(
        widget,
        "u32",
        SimpleNamespace(
            EnumChildWindows=lambda _parent, callback, _data: callback(11, 0),
            IsWindowVisible=lambda _hwnd: True,
            GetWindowRect=_rect_reader((0, 0, 0, 0)),
        ),
    )

    assert widget._taskbar_notification_bounds(99) is None


def test_taskbar_position_rejects_zero_sized_bounds():
    """Transient Explorer geometry must not collapse or move the overlay."""
    assert widget._taskbar_overlay_position((0, 0, 0, 0), 400) is None


def test_reposition_skips_unchanged_valid_position(monkeypatch):
    """Repeated display notifications must not move an already-correct overlay."""
    runtime = _runtime(_view("test"))
    runtime.settings.display = {"width": 460}
    runtime.last_position = (1290, 0, 400, 48)
    set_position_calls = []
    monkeypatch.setattr(widget, "_runtime", runtime)
    monkeypatch.setattr(
        widget,
        "u32",
        SimpleNamespace(
            FindWindowW=lambda *_: 99,
            GetWindowRect=_rect_reader((0, 1032, 1920, 1080)),
            SetWindowPos=lambda *args: set_position_calls.append(args) or 1,
        ),
    )

    assert widget._reposition(100) is True
    assert set_position_calls == []


def test_reposition_uses_taskbar_client_coordinates(monkeypatch):
    """A taskbar child must be positioned relative to its Explorer parent."""
    runtime = _runtime(_view("test"))
    runtime.settings.display = {"width": 460}
    set_position_calls = []
    monkeypatch.setattr(widget, "_runtime", runtime)
    monkeypatch.setattr(
        widget,
        "u32",
        SimpleNamespace(
            FindWindowW=lambda *_: 99,
            GetWindowRect=_rect_reader((0, 1032, 1920, 1080)),
            SetWindowPos=lambda *args: set_position_calls.append(args) or 1,
        ),
    )

    assert widget._reposition(100) is True
    assert set_position_calls == [
        (
            100,
            None,
            1290,
            0,
            400,
            48,
            widget.SWP_NOZORDER | widget.SWP_NOACTIVATE,
        )
    ]


def test_reposition_uses_current_taskbar_client_size_after_resolution_change(monkeypatch):
    """A child position must follow Explorer's current client size, not stale screen bounds."""
    runtime = _runtime(_view("test"))
    runtime.settings.display = {"width": 460}
    set_position_calls = []
    monkeypatch.setattr(widget, "_runtime", runtime)
    monkeypatch.setattr(
        widget,
        "u32",
        SimpleNamespace(
            FindWindowW=lambda *_: 99,
            GetClientRect=_rect_reader((0, 0, 2560, 64)),
            GetWindowRect=_rect_reader((0, 1032, 1920, 1080)),
            SetWindowPos=lambda *args: set_position_calls.append(args) or 1,
        ),
    )

    assert widget._reposition(100) is True
    assert set_position_calls == [
        (
            100,
            None,
            1930,
            0,
            400,
            64,
            widget.SWP_NOZORDER | widget.SWP_NOACTIVATE,
        )
    ]


def test_reposition_uses_dynamic_notification_boundary_in_client_coordinates(monkeypatch):
    """Screen-space tray bounds must become a safe client-space position."""
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


def test_maximized_work_area_does_not_cover_monitor():
    """A maximized app that leaves the taskbar visible must keep Q-Tracker visible."""
    assert widget._rect_covers_monitor(
        (0, 0, 1920, 1032),
        (0, 0, 1920, 1080),
    ) is False


def test_borderless_fullscreen_covers_monitor():
    """A borderless app covering the complete monitor must hide Q-Tracker."""
    assert widget._rect_covers_monitor(
        (0, 0, 1920, 1080),
        (0, 0, 1920, 1080),
    ) is True


def test_fullscreen_tolerates_two_pixel_rounding_only():
    """DPI rounding may miss two pixels, but a larger gap is not fullscreen."""
    monitor = (0, 0, 1920, 1080)

    assert widget._rect_covers_monitor((1, 2, 1918, 1079), monitor) is True
    assert widget._rect_covers_monitor((0, 0, 1917, 1080), monitor) is False


def test_foreground_fullscreen_on_taskbar_monitor_is_detected(monkeypatch):
    """A same-monitor borderless foreground window must request hiding."""
    monkeypatch.setattr(
        widget,
        "u32",
        SimpleNamespace(
            GetForegroundWindow=lambda: 200,
            FindWindowW=lambda *_: 300,
            IsWindowVisible=lambda _h: 1,
            IsIconic=lambda _h: 0,
            GetClassNameW=_class_name_reader("GameWindow"),
            MonitorFromWindow=lambda _h, _flags: 10,
            GetMonitorInfoW=_monitor_info_reader({10: (0, 0, 1920, 1080)}),
            GetWindowRect=_rect_reader((0, 0, 1920, 1080)),
        ),
    )
    monkeypatch.setattr(
        widget,
        "dwmapi",
        SimpleNamespace(DwmGetWindowAttribute=lambda *_: -1),
    )

    assert widget._foreground_fullscreen_on_taskbar_monitor(100) is True


def test_foreground_fullscreen_on_other_monitor_is_ignored(monkeypatch):
    """Fullscreen on another monitor must not hide this taskbar's overlay."""
    monkeypatch.setattr(
        widget,
        "u32",
        SimpleNamespace(
            GetForegroundWindow=lambda: 200,
            FindWindowW=lambda *_: 300,
            IsWindowVisible=lambda _h: 1,
            IsIconic=lambda _h: 0,
            GetClassNameW=_class_name_reader("GameWindow"),
            MonitorFromWindow=lambda h, _flags: 10 if h == 200 else 20,
            GetMonitorInfoW=_monitor_info_reader(
                {10: (1920, 0, 3840, 1080), 20: (0, 0, 1920, 1080)}
            ),
        ),
    )

    assert widget._foreground_fullscreen_on_taskbar_monitor(100) is False


def test_missing_foreground_window_preserves_visibility_state(monkeypatch):
    """A transient missing foreground handle is unknown, not proof of fullscreen."""
    monkeypatch.setattr(
        widget,
        "u32",
        SimpleNamespace(GetForegroundWindow=lambda: 0),
    )

    assert widget._foreground_fullscreen_on_taskbar_monitor(100) is None


@pytest.mark.parametrize("class_name", ["Progman", "WorkerW"])
def test_desktop_shell_never_counts_as_fullscreen(monkeypatch, class_name):
    """Clicking the desktop must not be mistaken for a fullscreen application."""
    monkeypatch.setattr(
        widget,
        "u32",
        SimpleNamespace(
            GetForegroundWindow=lambda: 200,
            FindWindowW=lambda *_: 300,
            IsWindowVisible=lambda _h: 1,
            IsIconic=lambda _h: 0,
            GetClassNameW=_class_name_reader(class_name),
            MonitorFromWindow=lambda _h, _flags: 10,
            GetMonitorInfoW=_monitor_info_reader({10: (0, 0, 1920, 1080)}),
            GetWindowRect=_rect_reader((0, 0, 1920, 1080)),
        ),
    )
    monkeypatch.setattr(
        widget,
        "dwmapi",
        SimpleNamespace(DwmGetWindowAttribute=lambda *_: -1),
    )

    assert widget._foreground_fullscreen_on_taskbar_monitor(100) is False


@pytest.mark.parametrize(
    "process_name",
    ["explorer.exe", "SearchHost.exe", "StartMenuExperienceHost.exe", "ShellExperienceHost.exe"],
)
def test_windows_shell_process_never_counts_as_fullscreen(monkeypatch, process_name):
    """Start/Search shell surfaces must not hide the overlay as fullscreen apps."""
    monkeypatch.setattr(widget, "_window_process_name", lambda _h: process_name)
    monkeypatch.setattr(
        widget,
        "u32",
        SimpleNamespace(
            GetForegroundWindow=lambda: 200,
            FindWindowW=lambda *_: 300,
            GetClassNameW=_class_name_reader("Windows.UI.Core.CoreWindow"),
            IsWindowVisible=lambda _h: 1,
            IsIconic=lambda _h: 0,
        ),
    )

    assert widget._foreground_fullscreen_on_taskbar_monitor(100) is False


def test_foreground_event_repairs_z_order_without_waiting_for_timer(monkeypatch):
    """A foreground transition must repair taskbar overlap immediately."""
    runtime = _runtime(_view("test"))
    runtime.hwnd = 100
    calls = []
    monkeypatch.setattr(widget, "_runtime", runtime)
    monkeypatch.setattr(widget, "_ensure_overlay_above_taskbar", lambda hwnd: calls.append(hwnd))

    widget._on_win_event(
        0,
        widget.EVENT_SYSTEM_FOREGROUND,
        200,
        0,
        0,
        0,
        0,
    )

    assert calls == [100]


def test_win_event_posts_deferred_repair_after_shell_reorder(monkeypatch):
    """A queued repair must run after the shell finishes its native Z-order change."""
    runtime = _runtime(_view("test"))
    runtime.hwnd = 100
    calls = []
    monkeypatch.setattr(widget, "_runtime", runtime)
    monkeypatch.setattr(
        widget,
        "_ensure_overlay_above_taskbar",
        lambda hwnd: calls.append(("ensure", hwnd)),
    )
    monkeypatch.setattr(
        widget,
        "u32",
        SimpleNamespace(
            PostMessageW=lambda *args: calls.append(("post", args)) or 1,
        ),
    )

    widget._on_win_event(0, widget.EVENT_OBJECT_REORDER, 300, 0, 0, 0, 0)

    assert calls == [
        ("ensure", 100),
        ("post", (100, widget.WM_SHELL_ZORDER_REPAIR, 0, 0)),
    ]


def test_reorder_event_repairs_taskbar_overlap(monkeypatch):
    """The reorder event must repair the taskbar after its Z-order change."""
    runtime = _runtime(_view("test"))
    runtime.hwnd = 100
    calls = []
    monkeypatch.setattr(widget, "_runtime", runtime)
    monkeypatch.setattr(widget, "_ensure_overlay_above_taskbar", lambda hwnd: calls.append(hwnd))

    widget._on_win_event(
        0,
        widget.EVENT_OBJECT_REORDER,
        300,
        0,
        0,
        0,
        0,
    )

    assert calls == [100]


def test_install_shell_event_hook_registers_foreground_callback(monkeypatch):
    """The overlay must subscribe to foreground changes for zero-frame recovery."""
    hook_calls = []
    monkeypatch.setattr(widget, "_winevent_hook", None)
    monkeypatch.setattr(widget, "_reorder_winevent_hook", None)
    monkeypatch.setattr(widget, "_winevent_proc", None)
    monkeypatch.setattr(
        widget,
        "u32",
        SimpleNamespace(
            SetWinEventHook=lambda *args: hook_calls.append(args) or 55,
        ),
    )

    assert widget._install_shell_event_hook() is True
    assert len(hook_calls) == 2
    assert hook_calls[0][0:2] == (
        widget.EVENT_SYSTEM_FOREGROUND,
        widget.EVENT_SYSTEM_FOREGROUND,
    )
    assert hook_calls[0][2] is None
    assert hook_calls[0][3] is widget._winevent_proc
    assert hook_calls[0][4:] == (0, 0, widget.WINEVENT_OUTOFCONTEXT)
    assert hook_calls[1][0:2] == (
        widget.EVENT_OBJECT_REORDER,
        widget.EVENT_OBJECT_REORDER,
    )


def test_entering_fullscreen_hides_once(monkeypatch):
    """Repeated fullscreen timer ticks must issue only one hide transition."""
    runtime = _runtime(_view("test"))
    show_calls = []
    monkeypatch.setattr(widget, "_runtime", runtime)
    monkeypatch.setattr(
        widget,
        "_foreground_fullscreen_on_taskbar_monitor",
        lambda _h: True,
    )
    monkeypatch.setattr(
        widget,
        "u32",
        SimpleNamespace(
            FindWindowW=lambda *_: 300,
            IsWindowVisible=lambda _h: 1,
            ShowWindow=lambda *args: show_calls.append(args),
        ),
    )

    widget._sync_overlay_visibility(100)
    widget._sync_overlay_visibility(100)

    assert show_calls == [(100, widget.SW_HIDE)]
    assert runtime.overlay_hidden is True


def test_leaving_fullscreen_repositions_and_shows_without_activation(monkeypatch):
    """Returning from fullscreen must restore position without stealing focus."""
    runtime = _runtime(_view("test"))
    runtime.overlay_hidden = True
    calls = []
    monkeypatch.setattr(widget, "_runtime", runtime)
    monkeypatch.setattr(
        widget,
        "_foreground_fullscreen_on_taskbar_monitor",
        lambda _h: False,
    )
    monkeypatch.setattr(
        widget,
        "_reposition",
        lambda hwnd: calls.append(("position", hwnd)) or True,
    )
    monkeypatch.setattr(
        widget,
        "u32",
        SimpleNamespace(
            FindWindowW=lambda *_: 300,
            IsWindowVisible=lambda _h: 1,
            ShowWindow=lambda hwnd, mode: calls.append(("show", hwnd, mode))
        ),
    )

    widget._sync_overlay_visibility(100)

    assert calls == [
        ("position", 100),
        ("show", 100, widget.SW_SHOWNOACTIVATE),
    ]
    assert runtime.overlay_hidden is False


def test_uncertain_fullscreen_detection_keeps_last_visibility(monkeypatch):
    """A transient Win32 read failure must not flicker a hidden overlay visible."""
    runtime = _runtime(_view("test"))
    runtime.overlay_hidden = True
    show_calls = []
    monkeypatch.setattr(widget, "_runtime", runtime)
    monkeypatch.setattr(
        widget,
        "_foreground_fullscreen_on_taskbar_monitor",
        lambda _h: None,
    )
    monkeypatch.setattr(
        widget,
        "u32",
        SimpleNamespace(
            FindWindowW=lambda *_: 300,
            IsWindowVisible=lambda _h: 1,
            ShowWindow=lambda *args: show_calls.append(args),
        ),
    )

    widget._sync_overlay_visibility(100)

    assert show_calls == []
    assert runtime.overlay_hidden is True


@pytest.mark.parametrize(
    ("taskbar_handle", "taskbar_visible"),
    [(0, 0), (300, 0)],
)
def test_unavailable_taskbar_hides_overlay_once(
    monkeypatch,
    taskbar_handle,
    taskbar_visible,
):
    """The standalone overlay must hide while its taskbar sensor is unavailable."""
    runtime = _runtime(_view("test"))
    runtime.overlay_hidden = False
    show_calls = []
    monkeypatch.setattr(widget, "_runtime", runtime)
    monkeypatch.setattr(
        widget,
        "u32",
        SimpleNamespace(
            FindWindowW=lambda *_: taskbar_handle,
            IsWindowVisible=lambda _h: taskbar_visible,
            ShowWindow=lambda *args: show_calls.append(args),
        ),
    )

    widget._sync_overlay_visibility(100)
    widget._sync_overlay_visibility(100)

    assert show_calls == [(100, widget.SW_HIDE)]
    assert runtime.overlay_hidden is True


def test_visible_taskbar_restores_overlay_without_activation(monkeypatch):
    """A restored taskbar must reposition and show the overlay without taking focus."""
    runtime = _runtime(_view("test"))
    runtime.overlay_hidden = True
    calls = []
    monkeypatch.setattr(widget, "_runtime", runtime)
    monkeypatch.setattr(
        widget,
        "_foreground_fullscreen_on_taskbar_monitor",
        lambda _h: False,
    )
    monkeypatch.setattr(
        widget,
        "_reposition",
        lambda hwnd: calls.append(("position", hwnd)) or True,
    )
    monkeypatch.setattr(
        widget,
        "u32",
        SimpleNamespace(
            FindWindowW=lambda *_: 300,
            IsWindowVisible=lambda _h: 1,
            ShowWindow=lambda hwnd, mode: calls.append(("show", hwnd, mode)),
        ),
    )

    widget._sync_overlay_visibility(100)

    assert calls == [
        ("position", 100),
        ("show", 100, widget.SW_SHOWNOACTIVATE),
    ]
    assert runtime.overlay_hidden is False


def test_render_segments_preserve_compact_provider_text_order():
    """The alignment refactor must not change which quotas users see or their order."""
    view = TrackerView(
        providers=(
            _provider(
                "agy",
                "Antigravity",
                (_window("session", "5H", 91), _window("weekly", "W", 94)),
            ),
            _provider("codex", "Codex", (_window("weekly", "W", 46),)),
        ),
        compact_text="",
        token_totals={},
    )

    segments = widget._render_segments(
        view,
        {"agy": {"color": "#35C2FF"}, "codex": {"color": "#7FE36A"}},
    )

    assert [segment.text for segment in segments] == [
        "Antigravity",
        "91%",
        "5H",
        "·",
        "94%",
        "W",
        " | ",
        "Codex",
        "46%",
        "W",
    ]
    assert segments[1].text == "91%"
    assert segments[1].gap_after == 4
    assert segments[-1].gap_after == 0


def test_render_segments_include_waiting_text_without_providers():
    """The empty-provider state must enter the same measured alignment pipeline."""
    segments = widget._render_segments(_view(""), {})

    assert [segment.text for segment in segments] == ["Q-Tracker — waiting for data"]
    assert segments[0].gap_after == 0


def test_shell_timer_rechecks_geometry_without_moving_unchanged_window(monkeypatch):
    """Shell checks may recalculate geometry but must not move an unchanged child."""
    current = _view("same")
    reposition_calls = []
    set_position_calls = []
    visibility_syncs = []
    z_order_syncs = []
    monkeypatch.setattr(widget, "_runtime", _runtime(current, ticks=4))
    monkeypatch.setattr(widget, "build_tracker_view", lambda *_: current)
    monkeypatch.setattr(widget, "_reposition", lambda hwnd: reposition_calls.append(hwnd))
    monkeypatch.setattr(
        widget,
        "_sync_overlay_visibility",
        lambda hwnd: visibility_syncs.append(hwnd),
    )
    monkeypatch.setattr(
        widget,
        "_ensure_overlay_above_taskbar",
        lambda hwnd: z_order_syncs.append(hwnd),
    )
    monkeypatch.setattr(
        widget,
        "u32",
        SimpleNamespace(
            SetWindowPos=lambda *args: set_position_calls.append(args),
            DefWindowProcW=lambda *_: -1,
        ),
    )

    assert widget._wnd_proc(100, widget.WM_TIMER, widget.SHELL_SYNC_TIMER_ID, 0) == 0
    assert visibility_syncs == [100]
    assert z_order_syncs == [100]
    assert reposition_calls == [100]
    assert set_position_calls == []


def test_shell_timer_repositions_visible_child_after_taskbar_layout_change(monkeypatch):
    """The child must refresh geometry because display broadcasts can skip child windows."""
    current = _view("same")
    reposition_calls = []
    monkeypatch.setattr(widget, "_runtime", _runtime(current, ticks=4))
    monkeypatch.setattr(widget, "_sync_overlay_visibility", lambda _hwnd: None)
    monkeypatch.setattr(
        widget,
        "_reposition",
        lambda hwnd: reposition_calls.append(hwnd) or True,
    )
    monkeypatch.setattr(
        widget,
        "_ensure_overlay_above_taskbar",
        lambda _hwnd: True,
    )
    monkeypatch.setattr(widget, "u32", SimpleNamespace(DefWindowProcW=lambda *_: -1))

    assert widget._wnd_proc(100, widget.WM_TIMER, widget.SHELL_SYNC_TIMER_ID, 0) == 0
    assert reposition_calls == [100]


def test_shell_timer_repairs_presentation_without_loading_quota(monkeypatch):
    """Fast shell checks must not turn into high-frequency state loads."""
    current = _view("same")
    runtime = _runtime(current, ticks=4)
    calls = []
    runtime.store = SimpleNamespace(load=lambda: calls.append("load") or {})
    monkeypatch.setattr(widget, "_runtime", runtime)
    monkeypatch.setattr(
        widget,
        "_sync_overlay_visibility",
        lambda hwnd: calls.append(("visibility", hwnd)),
    )
    monkeypatch.setattr(
        widget,
        "_ensure_overlay_above_taskbar",
        lambda hwnd: calls.append(("z-order", hwnd)),
    )
    monkeypatch.setattr(
        widget,
        "_reposition",
        lambda hwnd: calls.append(("position", hwnd)) or True,
    )
    monkeypatch.setattr(
        widget,
        "build_tracker_view",
        lambda *_: calls.append("view") or current,
    )
    monkeypatch.setattr(widget, "u32", SimpleNamespace(DefWindowProcW=lambda *_: -1))

    assert widget._wnd_proc(100, widget.WM_TIMER, widget.SHELL_SYNC_TIMER_ID, 0) == 0
    assert calls == [("visibility", 100), ("position", 100), ("z-order", 100)]
    assert runtime.ticks == 4


def test_data_timer_refreshes_quota_without_shell_sync(monkeypatch):
    """The configurable data tick must not duplicate presentation checks."""
    current = _view("same")
    calls = []
    monkeypatch.setattr(widget, "_runtime", _runtime(current, ticks=4))
    monkeypatch.setattr(
        widget,
        "build_tracker_view",
        lambda *_: calls.append("view") or current,
    )
    monkeypatch.setattr(
        widget,
        "_sync_overlay_visibility",
        lambda _hwnd: calls.append("visibility"),
    )
    monkeypatch.setattr(
        widget,
        "_ensure_overlay_above_taskbar",
        lambda _hwnd: calls.append("z-order"),
    )
    monkeypatch.setattr(widget, "u32", SimpleNamespace(DefWindowProcW=lambda *_: -1))

    assert widget._wnd_proc(100, widget.WM_TIMER, widget.DATA_REFRESH_TIMER_ID, 0) == 0
    assert calls == ["view"]
    assert widget._runtime.ticks == 5


def test_close_stops_both_window_timers(monkeypatch):
    """Closing the overlay must not leave either timer registered."""
    calls = []
    monkeypatch.setattr(widget, "_runtime", _runtime(_view("same")))
    monkeypatch.setattr(
        widget,
        "u32",
        SimpleNamespace(
            KillTimer=lambda *args: calls.append(("kill", args)),
            DestroyWindow=lambda hwnd: calls.append(("destroy", hwnd)),
            DefWindowProcW=lambda *_: -1,
        ),
    )

    assert widget._wnd_proc(100, widget.WM_CLOSE, 0, 0) == 0
    assert calls == [
        ("kill", (100, widget.DATA_REFRESH_TIMER_ID)),
        ("kill", (100, widget.SHELL_SYNC_TIMER_ID)),
        ("destroy", 100),
    ]


def test_z_order_repair_raises_overlay_when_taskbar_is_above(monkeypatch):
    """A visible taskbar above the overlay must trigger one non-activating raise."""
    calls = []
    monkeypatch.setattr(
        widget,
        "u32",
        SimpleNamespace(
            FindWindowW=lambda *_: 300,
            IsWindowVisible=lambda _h: 1,
            GetWindow=lambda hwnd, _command: 300 if hwnd == 100 else 0,
            SetWindowPos=lambda *args: calls.append(args) or 1,
        ),
    )

    assert widget._ensure_overlay_above_taskbar(100) is True
    assert calls == [
        (
            100,
            widget.HWND_TOPMOST,
            0,
            0,
            0,
            0,
            widget.SWP_NOMOVE | widget.SWP_NOSIZE | widget.SWP_NOACTIVATE,
        )
    ]


def test_z_order_repair_skips_when_taskbar_is_not_above(monkeypatch):
    """A correctly ordered overlay must not cause periodic compositor churn."""
    calls = []
    monkeypatch.setattr(
        widget,
        "u32",
        SimpleNamespace(
            FindWindowW=lambda *_: 300,
            IsWindowVisible=lambda _h: 1,
            GetWindow=lambda *_: 0,
            SetWindowPos=lambda *args: calls.append(args),
        ),
    )

    assert widget._ensure_overlay_above_taskbar(100) is True
    assert calls == []


def test_child_z_order_repair_moves_widget_to_top_of_taskbar_band(monkeypatch):
    """A shell child reorder must be repaired within Shell_TrayWnd siblings."""
    calls = []
    monkeypatch.setattr(
        widget,
        "u32",
        SimpleNamespace(
            FindWindowW=lambda *_: 300,
            IsWindowVisible=lambda _h: 1,
            GetParent=lambda _h: 300,
            GetWindow=lambda _h, _command: 301,
            SetWindowPos=lambda *args: calls.append(args) or 1,
        ),
    )

    assert widget._ensure_overlay_above_taskbar(100) is True
    assert calls == [
        (
            100,
            widget.HWND_TOP,
            0,
            0,
            0,
            0,
            widget.SWP_NOMOVE | widget.SWP_NOSIZE | widget.SWP_NOACTIVATE,
        )
    ]


def test_changed_view_invalidates_without_background_erase(monkeypatch):
    """A quota update must not expose a transparent frame before repainting."""
    current = _view("old")
    changed = _view("new")
    invalidate_calls = []
    monkeypatch.setattr(widget, "_runtime", _runtime(current))
    monkeypatch.setattr(widget, "build_tracker_view", lambda *_: changed)
    monkeypatch.setattr(widget, "_sync_overlay_visibility", lambda _hwnd: None)
    monkeypatch.setattr(widget, "_ensure_overlay_above_taskbar", lambda _hwnd: True)
    monkeypatch.setattr(
        widget,
        "u32",
        SimpleNamespace(
            InvalidateRect=lambda *args: invalidate_calls.append(args),
            DefWindowProcW=lambda *_: -1,
        ),
    )

    assert widget._wnd_proc(100, widget.WM_TIMER, widget.DATA_REFRESH_TIMER_ID, 0) == 0
    assert widget._runtime.view is changed
    assert invalidate_calls == [(100, None, 0)]


def test_taskbar_child_is_created_inside_explorer_taskbar(monkeypatch):
    """Explorer must host the widget so Start cannot cover its taskbar surface."""
    captured = []
    monkeypatch.setattr(
        widget,
        "u32",
        SimpleNamespace(CreateWindowExW=lambda *args: captured.append(args) or 321),
    )

    result = widget._create_taskbar_child(11, "TrackerClass", 400, 99)

    assert result == 321
    assert captured[0][8] == 99
    assert captured[0][1] == "TrackerClass"
    assert captured[0][3] & widget.WS_CHILD
    assert captured[0][3] & widget.WS_CLIPCHILDREN
    assert captured[0][3] & widget.WS_CLIPSIBLINGS
    assert captured[0][0] & widget.WS_EX_NOACTIVATE
    assert captured[0][0] & widget.WS_EX_LAYERED
    assert captured[0][0] & widget.WS_EX_TOPMOST


def test_create_window_stops_when_taskbar_owner_is_missing(monkeypatch):
    """A missing Explorer taskbar must not recreate the unstable standalone popup."""
    create_calls = []
    monkeypatch.setattr(
        widget,
        "_runtime",
        SimpleNamespace(
            hwnd=None,
            settings=SimpleNamespace(display={"width": 400, "update_interval_ms": 1_000}),
        ),
    )
    monkeypatch.setattr(widget, "_background_brush", None)
    monkeypatch.setattr(widget, "_wndproc_ref", None)
    monkeypatch.setattr(widget, "_reposition", lambda *_: None)
    monkeypatch.setattr(widget, "k32", SimpleNamespace(GetModuleHandleW=lambda *_: 11))
    monkeypatch.setattr(widget, "g32", SimpleNamespace(CreateSolidBrush=lambda *_: 22))
    monkeypatch.setattr(
        widget,
        "u32",
        SimpleNamespace(
            FindWindowW=lambda *_: 0,
            LoadCursorW=lambda *_: 33,
            RegisterClassExW=lambda *_: 1,
            CreateWindowExW=lambda *args: create_calls.append(args) or 321,
            SetLayeredWindowAttributes=lambda *_: 1,
            SetTimer=lambda *_: 1,
            ShowWindow=lambda *_: 1,
            UpdateWindow=lambda *_: 1,
        ),
    )

    assert widget._create_window(max_retries=1, retry_delay=0) is False
    assert create_calls == []


def test_create_window_registers_data_and_shell_timers(monkeypatch):
    """Creation must schedule fast shell checks separately from data refreshes."""
    timer_calls = []
    monkeypatch.setattr(
        widget,
        "_runtime",
        SimpleNamespace(
            hwnd=None,
            settings=SimpleNamespace(display={"width": 400, "update_interval_ms": 1_000}),
        ),
    )
    monkeypatch.setattr(widget, "_background_brush", None)
    monkeypatch.setattr(widget, "_wndproc_ref", None)
    monkeypatch.setattr(widget, "_reposition", lambda *_: True)
    monkeypatch.setattr(widget, "k32", SimpleNamespace(GetModuleHandleW=lambda *_: 11))
    monkeypatch.setattr(widget, "g32", SimpleNamespace(CreateSolidBrush=lambda *_: 22))
    monkeypatch.setattr(
        widget,
        "u32",
        SimpleNamespace(
            FindWindowW=lambda *_: 99,
            GetWindowRect=lambda _h, rect: (
                setattr(rect._obj, "left", 0)
                or setattr(rect._obj, "top", 1032)
                or setattr(rect._obj, "right", 1920)
                or setattr(rect._obj, "bottom", 1080)
                or 1
            ),
            LoadCursorW=lambda *_: 33,
            RegisterClassExW=lambda *_: 1,
            CreateWindowExW=lambda *_: 321,
            SetLayeredWindowAttributes=lambda *_: 1,
            SetTimer=lambda *args: timer_calls.append(args) or args[1],
            ShowWindow=lambda *_: 1,
            UpdateWindow=lambda *_: 1,
        ),
    )

    assert widget._create_window(max_retries=1, retry_delay=0) is True
    assert timer_calls == [
        (321, widget.DATA_REFRESH_TIMER_ID, 1_000, None),
        (321, widget.SHELL_SYNC_TIMER_ID, 10, None),
    ]


def test_run_taskbar_sets_retry_timer_when_initial_window_creation_fails(monkeypatch):
    """When Explorer is not available on startup, run_taskbar must set a retry timer instead of exiting immediately."""
    timer_calls = []
    messages = [SimpleNamespace(message=123)]

    def fake_get_message(msg_ptr, *_args):
        if messages:
            msg = messages.pop(0)
            getattr(msg_ptr, "_obj", msg_ptr).message = msg.message
            return 1
        return 0

    monkeypatch.setattr(widget, "_create_window", lambda *args, **kwargs: False)
    monkeypatch.setattr(widget, "build_tracker_view", lambda *_: _view("test"))
    monkeypatch.setattr(
        widget,
        "u32",
        SimpleNamespace(
            SetTimer=lambda *args: timer_calls.append(("set", args)) or 999,
            KillTimer=lambda *args: timer_calls.append(("kill", args)),
            GetMessageW=fake_get_message,
            TranslateMessage=lambda *_: None,
            DispatchMessageW=lambda *_: None,
        ),
    )

    result = widget.run_taskbar(
        store=SimpleNamespace(load=lambda: {}),
        settings=SimpleNamespace(enabled_providers=()),
        on_open=lambda: None,
        on_refresh=lambda: None,
    )

    assert result == 0
    assert ("set", (None, 0, 2000, None)) in timer_calls
    assert ("kill", (None, 999)) in timer_calls


def test_reposition_ignores_zero_dimensions(monkeypatch):
    """Repositioning must not collapse the window if taskbar has not finished layout."""
    set_position_calls = []
    monkeypatch.setattr(widget, "_runtime", _runtime(_view("test")))
    monkeypatch.setattr(
        widget,
        "u32",
        SimpleNamespace(
            FindWindowW=lambda *_: 99,
            GetWindowRect=lambda _h, r: setattr(r._obj, "right", 0) or setattr(r._obj, "bottom", 0) or 1,
            SetWindowPos=lambda *args: set_position_calls.append(args),
        ),
    )

    widget._reposition(100)
    assert set_position_calls == []


def test_create_window_retries_when_taskbar_has_zero_geometry(monkeypatch):
    """When Explorer exists but has 0 width or 0 height, create_window must wait."""
    create_calls = []
    monkeypatch.setattr(
        widget,
        "_runtime",
        SimpleNamespace(
            hwnd=None,
            settings=SimpleNamespace(display={"width": 400, "update_interval_ms": 1_000}),
        ),
    )
    monkeypatch.setattr(widget, "_background_brush", None)
    monkeypatch.setattr(widget, "_wndproc_ref", None)
    monkeypatch.setattr(widget, "k32", SimpleNamespace(GetModuleHandleW=lambda *_: 11))
    monkeypatch.setattr(widget, "g32", SimpleNamespace(CreateSolidBrush=lambda *_: 22))
    monkeypatch.setattr(
        widget,
        "u32",
        SimpleNamespace(
            FindWindowW=lambda *_: 99,
            GetWindowRect=lambda _h, r: setattr(r._obj, "right", 0) or setattr(r._obj, "bottom", 0) or 1,
            LoadCursorW=lambda *_: 33,
            RegisterClassExW=lambda *_: 1,
            CreateWindowExW=lambda *args: create_calls.append(args) or 321,
            SetLayeredWindowAttributes=lambda *_: 1,
            SetTimer=lambda *_: 1,
            ShowWindow=lambda *_: 1,
            UpdateWindow=lambda *_: 1,
        ),
    )

    assert widget._create_window(max_retries=1, retry_delay=0) is False
    assert create_calls == []
