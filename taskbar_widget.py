"""Small native Windows taskbar overlay backed only by persisted tracker state.

Provider I/O is owned by :mod:`usage_service`; this window merely repaints when
the state fingerprint changes.  Keeping those concerns separate avoids the old
20 Hz top-most loop and overlapping network requests.
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wintypes
from dataclasses import dataclass
from typing import Any, Callable, Optional

from settings import Settings
from state_store import AtomicStateStore
from ui_models import (
    TrackerView,
    build_tracker_view,
    context_detail_lines,
    countdown_suffix,
    taskbar_overlay_width,
    taskbar_windows,
)


HANDLE = ctypes.c_void_p
HWND = ctypes.c_void_p
HDC = ctypes.c_void_p
HFONT = ctypes.c_void_p
ATOM = ctypes.c_ushort
BOOL = ctypes.c_int
UINT = ctypes.c_uint
INT = ctypes.c_int
DWORD = ctypes.c_uint
LONG = ctypes.c_long
WPARAM = ctypes.c_size_t
LPARAM = ctypes.c_ssize_t
LRESULT = ctypes.c_ssize_t
WNDPROC = ctypes.WINFUNCTYPE(LRESULT, HWND, UINT, WPARAM, LPARAM)
WNDENUMPROC = ctypes.WINFUNCTYPE(BOOL, HWND, LPARAM)
WINEVENTPROC = ctypes.WINFUNCTYPE(
    None,
    HANDLE,
    UINT,
    HWND,
    LONG,
    LONG,
    DWORD,
    DWORD,
)

WS_VISIBLE = 0x10000000
WS_CHILD = 0x40000000
WS_CLIPCHILDREN = 0x02000000
WS_CLIPSIBLINGS = 0x04000000
WS_SYSMENU = 0x00080000
WS_POPUP = 0x80000000
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_LAYERED = 0x00080000
WS_EX_TOPMOST = 0x00000008
WS_EX_NOACTIVATE = 0x08000000

WM_DESTROY = 0x0002
WM_PAINT = 0x000F
WM_CLOSE = 0x0010
WM_ERASEBKGND = 0x0014
WM_SETTINGCHANGE = 0x001A
WM_DISPLAYCHANGE = 0x007E
WM_TIMER = 0x0113
WM_RBUTTONDOWN = 0x0204
WM_LBUTTONDBLCLK = 0x0203
WM_DPICHANGED = 0x02E0
WM_SHELL_ZORDER_REPAIR = 0x8001
EVENT_SYSTEM_FOREGROUND = 0x0003
EVENT_OBJECT_REORDER = 0x8004
WINEVENT_OUTOFCONTEXT = 0x0000

CS_HREDRAW = 0x0002
CS_VREDRAW = 0x0001
CS_DBLCLKS = 0x0008
IDC_ARROW = 32512
BK_TRANSPARENT = 1
DT_SINGLELINE = 0x0020
DT_VCENTER = 0x0004
DT_LEFT = 0x0000
DT_NOCLIP = 0x0100
SWP_NOACTIVATE = 0x0010
SWP_NOZORDER = 0x0004
SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SW_HIDE = 0
SW_SHOWNOACTIVATE = 4
HWND_TOP = 0
LWA_COLORKEY = 0x00000001
TPM_RETURNCMD = 0x0100
MF_STRING = 0x0000
MF_SEPARATOR = 0x0800
MF_GRAYED = 0x0001
FW_BOLD = 700
DEFAULT_CHARSET = 1
ANTIALIASED_QUALITY = 4
COLORKEY_RGB = 0x00010101
TASKBAR_NOTIFICATION_CLASS = "TrayNotifyWnd"
TASKBAR_NOTIFICATION_GAP = 12
TASKBAR_LEFT_MARGIN = 12
TASKBAR_PRIMARY_GAP = 20
TASKBAR_PRIMARY_CLASSES = frozenset({"Start", "MSTaskListWClass", "MSTaskSwWClass", "ReBarWindow32"})
FULLSCREEN_TOLERANCE_PX = 2
MAX_AUTO_WIDTH = 720
MIN_AUTO_WIDTH = 240
DATA_REFRESH_TIMER_ID = 1
SHELL_SYNC_TIMER_ID = 2
SHELL_SYNC_INTERVAL_MS = 10
SHELL_RECONNECT_INTERVAL_MS = 2_000
GW_HWNDPREV = 3
HWND_TOPMOST = -1
u32 = ctypes.windll.user32
g32 = ctypes.windll.gdi32
k32 = ctypes.windll.kernel32
dwmapi = ctypes.windll.dwmapi

k32.GetModuleHandleW.argtypes = [ctypes.c_wchar_p]
k32.GetModuleHandleW.restype = HANDLE
k32.OpenProcess.argtypes = [DWORD, BOOL, DWORD]
k32.OpenProcess.restype = HANDLE
k32.QueryFullProcessImageNameW.argtypes = [HANDLE, DWORD, ctypes.POINTER(ctypes.c_wchar), ctypes.POINTER(DWORD)]
k32.QueryFullProcessImageNameW.restype = BOOL
k32.CloseHandle.argtypes = [HANDLE]
k32.CloseHandle.restype = BOOL
u32.FindWindowW.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p]
u32.FindWindowW.restype = HANDLE
u32.GetWindowRect.argtypes = [HANDLE, ctypes.POINTER(wintypes.RECT)]
u32.GetWindowRect.restype = BOOL
if hasattr(u32, "EnumChildWindows"):
    u32.EnumChildWindows.argtypes = [HWND, WNDENUMPROC, LPARAM]
    u32.EnumChildWindows.restype = BOOL
u32.GetClassNameW.argtypes = [HANDLE, ctypes.POINTER(ctypes.c_wchar), INT]
u32.GetClassNameW.restype = INT
u32.GetWindowThreadProcessId.argtypes = [HANDLE, ctypes.POINTER(DWORD)]
u32.GetWindowThreadProcessId.restype = DWORD
u32.GetWindow.argtypes = [HANDLE, UINT]
u32.GetWindow.restype = HANDLE
u32.GetParent.argtypes = [HANDLE]
u32.GetParent.restype = HANDLE
u32.IsWindow.argtypes = [HANDLE]
u32.IsWindow.restype = BOOL
u32.GetClientRect.argtypes = [HANDLE, ctypes.POINTER(wintypes.RECT)]
u32.GetClientRect.restype = BOOL
if hasattr(u32, "ScreenToClient"):
    u32.ScreenToClient.argtypes = [HWND, ctypes.POINTER(wintypes.POINT)]
    u32.ScreenToClient.restype = BOOL
u32.RegisterClassExW.argtypes = [ctypes.c_void_p]
u32.RegisterClassExW.restype = ATOM
u32.CreateWindowExW.argtypes = [DWORD, ctypes.c_wchar_p, ctypes.c_wchar_p, DWORD, INT, INT, INT, INT, HANDLE, HANDLE, HANDLE, ctypes.c_void_p]
u32.CreateWindowExW.restype = HANDLE
u32.ShowWindow.argtypes = [HANDLE, INT]
u32.ShowWindow.restype = BOOL
u32.UpdateWindow.argtypes = [HANDLE]
u32.UpdateWindow.restype = BOOL
u32.DestroyWindow.argtypes = [HANDLE]
u32.DestroyWindow.restype = BOOL
u32.PostQuitMessage.argtypes = [INT]
u32.PostQuitMessage.restype = None
u32.DefWindowProcW.argtypes = [HANDLE, UINT, WPARAM, LPARAM]
u32.DefWindowProcW.restype = LRESULT
u32.SetTimer.argtypes = [HANDLE, ctypes.c_size_t, UINT, ctypes.c_void_p]
u32.SetTimer.restype = ctypes.c_size_t
u32.KillTimer.argtypes = [HANDLE, ctypes.c_size_t]
u32.KillTimer.restype = BOOL
u32.InvalidateRect.argtypes = [HANDLE, ctypes.c_void_p, BOOL]
u32.InvalidateRect.restype = BOOL
u32.BeginPaint.argtypes = [HANDLE, ctypes.c_void_p]
u32.BeginPaint.restype = HDC
u32.EndPaint.argtypes = [HANDLE, ctypes.c_void_p]
u32.EndPaint.restype = BOOL
u32.FillRect.argtypes = [HDC, ctypes.POINTER(wintypes.RECT), HANDLE]
u32.FillRect.restype = INT
u32.DrawTextW.argtypes = [HDC, ctypes.c_wchar_p, INT, ctypes.POINTER(wintypes.RECT), UINT]
u32.DrawTextW.restype = INT
u32.SetLayeredWindowAttributes.argtypes = [HANDLE, ctypes.c_uint, ctypes.c_byte, DWORD]
u32.SetLayeredWindowAttributes.restype = BOOL
u32.SetWindowPos.argtypes = [HANDLE, HANDLE, INT, INT, INT, INT, UINT]
u32.SetWindowPos.restype = BOOL
u32.SetWinEventHook.argtypes = [UINT, UINT, HANDLE, WINEVENTPROC, DWORD, DWORD, UINT]
u32.SetWinEventHook.restype = HANDLE
u32.UnhookWinEvent.argtypes = [HANDLE]
u32.UnhookWinEvent.restype = BOOL
u32.LoadCursorW.argtypes = [HANDLE, ctypes.c_void_p]
u32.LoadCursorW.restype = HANDLE
u32.GetMessageW.argtypes = [ctypes.c_void_p, HANDLE, UINT, UINT]
u32.GetMessageW.restype = INT
u32.TranslateMessage.argtypes = [ctypes.c_void_p]
u32.TranslateMessage.restype = BOOL
u32.DispatchMessageW.argtypes = [ctypes.c_void_p]
u32.DispatchMessageW.restype = LRESULT
u32.PostMessageW.argtypes = [HANDLE, UINT, WPARAM, LPARAM]
u32.PostMessageW.restype = BOOL
u32.CreatePopupMenu.argtypes = []
u32.CreatePopupMenu.restype = HANDLE
u32.AppendMenuW.argtypes = [HANDLE, UINT, ctypes.c_size_t, ctypes.c_wchar_p]
u32.AppendMenuW.restype = BOOL
u32.TrackPopupMenu.argtypes = [HANDLE, UINT, INT, INT, INT, HANDLE, ctypes.c_void_p]
u32.TrackPopupMenu.restype = INT
u32.DestroyMenu.argtypes = [HANDLE]
u32.DestroyMenu.restype = BOOL
u32.GetCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]
u32.GetCursorPos.restype = BOOL
u32.SetForegroundWindow.argtypes = [HANDLE]
u32.SetForegroundWindow.restype = BOOL
u32.RegisterWindowMessageW.argtypes = [ctypes.c_wchar_p]
u32.RegisterWindowMessageW.restype = UINT
WM_TASKBARCREATED = u32.RegisterWindowMessageW("TaskbarCreated")
MSGFLT_ALLOW = 1
if hasattr(u32, "ChangeWindowMessageFilterEx"):
    u32.ChangeWindowMessageFilterEx.argtypes = [HANDLE, UINT, DWORD, ctypes.c_void_p]
    u32.ChangeWindowMessageFilterEx.restype = BOOL


g32.CreateSolidBrush.argtypes = [ctypes.c_uint]
g32.CreateSolidBrush.restype = HANDLE
g32.CreateFontW.argtypes = [INT, INT, INT, INT, INT, DWORD, DWORD, DWORD, DWORD, DWORD, DWORD, DWORD, DWORD, ctypes.c_wchar_p]
g32.CreateFontW.restype = HANDLE
g32.SelectObject.argtypes = [HDC, HANDLE]
g32.SelectObject.restype = HANDLE
g32.DeleteObject.argtypes = [HANDLE]
g32.DeleteObject.restype = BOOL
g32.SetBkMode.argtypes = [HDC, INT]
g32.SetBkMode.restype = INT
g32.SetTextColor.argtypes = [HDC, ctypes.c_uint]
g32.SetTextColor.restype = ctypes.c_uint
g32.GetTextExtentPoint32W.argtypes = [HDC, ctypes.c_wchar_p, INT, ctypes.POINTER(wintypes.SIZE)]
g32.GetTextExtentPoint32W.restype = BOOL
g32.CreateCompatibleDC.argtypes = [HDC]
g32.CreateCompatibleDC.restype = HDC
u32.GetDC.argtypes = [HWND]
u32.GetDC.restype = HDC
u32.ReleaseDC.argtypes = [HWND, HDC]
u32.ReleaseDC.restype = INT
g32.CreateCompatibleBitmap.argtypes = [HDC, INT, INT]
g32.CreateCompatibleBitmap.restype = HANDLE
g32.BitBlt.argtypes = [HDC, INT, INT, INT, INT, HDC, INT, INT, DWORD]
g32.BitBlt.restype = BOOL
g32.DeleteDC.argtypes = [HDC]
g32.DeleteDC.restype = BOOL


class WNDCLASSEX(ctypes.Structure):
    _fields_ = [
        ("cbSize", UINT), ("style", UINT), ("lpfnWndProc", WNDPROC),
        ("cbClsExtra", INT), ("cbWndExtra", INT), ("hInstance", HANDLE),
        ("hIcon", HANDLE), ("hCursor", HANDLE), ("hbrBackground", HANDLE),
        ("lpszMenuName", ctypes.c_wchar_p), ("lpszClassName", ctypes.c_wchar_p),
        ("hIconSm", HANDLE),
    ]


class PAINTSTRUCT(ctypes.Structure):
    _fields_ = [
        ("hdc", HDC), ("fErase", BOOL), ("rcPaint", wintypes.RECT),
        ("fRestore", BOOL), ("fIncUpdate", BOOL), ("rgbReserved", ctypes.c_byte * 32),
    ]


class MONITORINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", DWORD),
        ("rcMonitor", wintypes.RECT),
        ("rcWork", wintypes.RECT),
        ("dwFlags", DWORD),
    ]


u32.GetForegroundWindow.argtypes = []
u32.GetForegroundWindow.restype = HWND
u32.MonitorFromWindow.argtypes = [HWND, DWORD]
u32.MonitorFromWindow.restype = HANDLE
u32.GetMonitorInfoW.argtypes = [HANDLE, ctypes.POINTER(MONITORINFO)]
u32.GetMonitorInfoW.restype = BOOL
u32.IsWindowVisible.argtypes = [HWND]
u32.IsWindowVisible.restype = BOOL
u32.IsIconic.argtypes = [HWND]
u32.IsIconic.restype = BOOL
dwmapi.DwmGetWindowAttribute.argtypes = [HWND, DWORD, ctypes.c_void_p, DWORD]
dwmapi.DwmGetWindowAttribute.restype = ctypes.c_long


def rgb(value: Any) -> int:
    """Convert a CSS hex color or RGB tuple into a Win32 COLORREF."""
    if isinstance(value, str) and len(value.lstrip("#")) == 6:
        value = value.lstrip("#")
        red, green, blue = int(value[:2], 16), int(value[2:4], 16), int(value[4:], 16)
    elif isinstance(value, (tuple, list)) and len(value) >= 3:
        red, green, blue = (int(value[index]) for index in range(3))
    else:
        red, green, blue = 255, 255, 255
    return red | (green << 8) | (blue << 16)


class FontCache:
    def __init__(self) -> None:
        self.handle: Optional[HANDLE] = None
        self.key: Optional[tuple[str, int]] = None

    def get(self, name: str, size: int) -> HANDLE:
        key = (name, size)
        if self.handle is None or self.key != key:
            self.cleanup()
            self.handle = g32.CreateFontW(size, 0, 0, 0, FW_BOLD, 0, 0, 0, DEFAULT_CHARSET, 0, 0, ANTIALIASED_QUALITY, 0, name)
            self.key = key
        return self.handle

    def cleanup(self) -> None:
        if self.handle:
            g32.DeleteObject(self.handle)
            self.handle = None
            self.key = None


@dataclass
class _Runtime:
    store: AtomicStateStore
    settings: Settings
    on_open: Callable[[], Any]
    on_refresh: Callable[[], Any]
    view: TrackerView
    hwnd: Optional[HWND] = None
    ticks: int = 0
    last_position: Optional[tuple[int, int, int, int]] = None
    overlay_hidden: bool = False
    compact_countdowns: bool = False
    shutting_down: bool = False


@dataclass(frozen=True, slots=True)
class RenderSegment:
    text: str
    color: int
    gap_after: int = 0


_runtime: Optional[_Runtime] = None
_wndproc_ref: Optional[WNDPROC] = None
_winevent_proc: Optional[WINEVENTPROC] = None
_winevent_hook: Optional[HANDLE] = None
_reorder_winevent_hook: Optional[HANDLE] = None
_background_brush: Optional[HANDLE] = None
_font_cache = FontCache()


ERROR_CLASS_ALREADY_EXISTS = 1410
DWMWA_EXTENDED_FRAME_BOUNDS = 9
MONITOR_DEFAULTTONULL = 0
DESKTOP_SHELL_CLASSES = frozenset({"Progman", "WorkerW"})
SHELL_PROCESS_NAMES = frozenset(
    {
        "explorer.exe",
        "searchhost.exe",
        "searchapp.exe",
        "startmenuexperiencehost.exe",
        "shellexperiencehost.exe",
    }
)


def request_close() -> None:
    """Ask the UI thread to close; safe to call from the tray thread."""
    if _runtime is not None and _runtime.hwnd:
        u32.PostMessageW(_runtime.hwnd, WM_CLOSE, 0, 0)
    else:
        u32.PostQuitMessage(0)



def _left_aligned_start(client_width: int, content_width: int) -> int:
    """Keep taskbar text visually aligned to the left edge of its overlay."""
    if client_width <= 0:
        return 0
    return min(TASKBAR_LEFT_MARGIN, max(0, client_width - max(0, content_width)))


def _render_segments(
    view: TrackerView,
    provider_styles: dict[str, dict[str, object]],
    include_countdown: bool = True,
    taskbar_window_map: Optional[dict[str, tuple[str, ...]]] = None,
) -> tuple[RenderSegment, ...]:
    window_map = taskbar_window_map or {}
    if not view.providers:
        return (RenderSegment("Q-Tracker — waiting for data", rgb((170, 178, 195))),)

    segments: list[RenderSegment] = []
    for provider_index, provider in enumerate(view.providers):
        has_next_provider = provider_index < len(view.providers) - 1
        style = provider_styles.get(provider.provider_id, {})
        segments.append(
            RenderSegment(
                provider.display_name,
                rgb(style.get("color", "#6CB6FF")),
                6,
            )
        )
        if provider.indicator:
            indicator_color = "#FFB454" if provider.status == "stale" else "#FF6B6B"
            segments.append(RenderSegment(provider.indicator, rgb(indicator_color), 5))

        windows = taskbar_windows(provider, preferred_ids=window_map.get(provider.provider_id))
        if not windows:
            segments.append(
                RenderSegment(
                    "—",
                    rgb((170, 178, 195)),
                    5 if has_next_provider else 0,
                )
            )
        for window_index, window in enumerate(windows):
            has_next_window = window_index < len(windows) - 1
            quota_color = (
                "#FF6B6B"
                if window.severity == "critical"
                else "#FFB454"
                if window.severity == "warning"
                else "#D4D9E5"
            )
            segments.append(
                RenderSegment(f"{window.remaining_percent:.0f}%", rgb(quota_color), 4)
            )
            tail_gap = 5 if has_next_window else 10 if has_next_provider else 0
            countdown = countdown_suffix(window.reset_in) if include_countdown else ""
            segments.append(
                RenderSegment(
                    window.short_label,
                    rgb((125, 132, 150)),
                    3 if countdown else tail_gap,
                )
            )
            if countdown:
                segments.append(
                    RenderSegment(countdown, rgb((110, 118, 138)), tail_gap)
                )
            if has_next_window:
                segments.append(RenderSegment("·", rgb((125, 132, 150)), 5))
        if has_next_provider:
            segments.append(RenderSegment(" | ", rgb((95, 103, 123)), 8))
    return tuple(segments)


def _taskbar_overlay_position(
    taskbar_bounds: tuple[int, int, int, int],
    configured_width: int,
    notification_bounds: Optional[tuple[int, int, int, int]] = None,
    desired_width: Optional[int] = None,
    primary_bounds: Optional[tuple[int, int, int, int]] = None,
) -> Optional[tuple[int, int, int, int]]:
    left, top, right, bottom = taskbar_bounds
    taskbar_width = right - left
    taskbar_height = bottom - top
    if taskbar_width <= 0 or taskbar_height <= 0:
        return None
    width = taskbar_overlay_width(configured_width, taskbar_width)
    if desired_width is not None and desired_width > 0:
        ceiling = min(MAX_AUTO_WIDTH, max(taskbar_width - 300, MIN_AUTO_WIDTH))
        width = max(MIN_AUTO_WIDTH, min(int(desired_width), ceiling))
    if taskbar_width >= taskbar_height:
        x = left + TASKBAR_LEFT_MARGIN
        safe_right = right - TASKBAR_LEFT_MARGIN
        notification_gap = max(TASKBAR_NOTIFICATION_GAP, taskbar_height)
        if notification_bounds is not None:
            notification_left, notification_top, notification_right, notification_bottom = notification_bounds
            overlaps_taskbar = notification_top < bottom and notification_bottom > top
            inside_taskbar = (
                x + notification_gap <= notification_left < right
                and notification_right <= right
                and notification_left < notification_right
            )
            if overlaps_taskbar and inside_taskbar:
                safe_right = notification_left - notification_gap

        if primary_bounds is not None:
            primary_left, primary_top, primary_right, primary_bottom = primary_bounds
            overlaps_taskbar = primary_top < bottom and primary_bottom > top
            inside_taskbar = (
                left <= primary_left < right
                and left < primary_right <= right
                and primary_left < primary_right
            )
            if overlaps_taskbar and inside_taskbar:
                left_region_right = primary_left - TASKBAR_PRIMARY_GAP
                left_region_width = left_region_right - x
                if left_region_width >= MIN_AUTO_WIDTH:
                    safe_right = min(safe_right, left_region_right)
                else:
                    # Left-aligned Windows taskbars leave no useful room before
                    # Start. Sit after the primary buttons in that layout.
                    x = primary_right + TASKBAR_PRIMARY_GAP

        safe_right = min(right - TASKBAR_LEFT_MARGIN, safe_right)
        available_width = max(1, safe_right - x)
        width = min(width, available_width)
        return x, top, width, taskbar_height
    width = taskbar_width
    height = min(180, max(60, taskbar_height - 150))
    return left, bottom - height - 100, width, height


def _taskbar_notification_client_bounds(
    taskbar: HWND,
    screen_bounds: tuple[int, int, int, int],
) -> Optional[tuple[int, int, int, int]]:
    """Convert a notification-area rectangle from screen to taskbar space."""
    screen_to_client = getattr(u32, "ScreenToClient", None)
    if screen_to_client is None:
        return None
    top_left = wintypes.POINT(screen_bounds[0], screen_bounds[1])
    bottom_right = wintypes.POINT(screen_bounds[2], screen_bounds[3])
    try:
        if not screen_to_client(taskbar, ctypes.byref(top_left)):
            return None
        if not screen_to_client(taskbar, ctypes.byref(bottom_right)):
            return None
    except (AttributeError, OSError, TypeError):
        return None
    if bottom_right.x <= top_left.x or bottom_right.y <= top_left.y:
        return None
    return top_left.x, top_left.y, bottom_right.x, bottom_right.y


def _taskbar_primary_controls_bounds(
    taskbar: HWND,
) -> Optional[tuple[int, int, int, int]]:
    """Return the visible Start/task-button band in screen coordinates."""
    enum_children = getattr(u32, "EnumChildWindows", None)
    get_window_rect = getattr(u32, "GetWindowRect", None)
    is_window_visible = getattr(u32, "IsWindowVisible", None)
    if enum_children is None or get_window_rect is None:
        return None

    candidates: list[tuple[int, int, int, int]] = []

    def collect(hwnd: HWND, _data: LPARAM) -> bool:
        try:
            if _window_class_name(hwnd) not in TASKBAR_PRIMARY_CLASSES:
                return True
            if is_window_visible is not None and not is_window_visible(hwnd):
                return True
            bounds = wintypes.RECT()
            if not get_window_rect(hwnd, ctypes.byref(bounds)):
                return True
            if bounds.right <= bounds.left or bounds.bottom <= bounds.top:
                return True
            candidates.append((bounds.left, bounds.top, bounds.right, bounds.bottom))
        except (AttributeError, OSError, TypeError):
            return True
        return True

    callback = WNDENUMPROC(collect)
    try:
        enum_children(taskbar, callback, 0)
    except (AttributeError, OSError, TypeError):
        return None
    if not candidates:
        return None
    return (
        min(bounds[0] for bounds in candidates),
        min(bounds[1] for bounds in candidates),
        max(bounds[2] for bounds in candidates),
        max(bounds[3] for bounds in candidates),
    )


def _taskbar_child_position(
    taskbar: HWND,
    configured_width: int,
    desired_width: Optional[int] = None,
) -> Optional[tuple[int, int, int, int]]:
    """Return geometry in ``Shell_TrayWnd`` client coordinates.

    A child window must be positioned in its parent's client space.  Using
    the current taskbar client size also keeps the 230 px right anchor stable
    when the display resolution or taskbar DPI changes. The notification-area
    boundary is converted into the same client coordinate space before use.
    """
    get_client_rect = getattr(u32, "GetClientRect", None)
    if get_client_rect is None:
        return None
    client = wintypes.RECT()
    try:
        if not get_client_rect(taskbar, ctypes.byref(client)):
            return None
    except (AttributeError, OSError):
        return None
    taskbar_width = client.right - client.left
    taskbar_height = client.bottom - client.top
    if taskbar_width <= 0 or taskbar_height <= 0:
        return None
    notification_bounds = None
    primary_bounds = None
    if taskbar_width >= taskbar_height:
        screen_bounds = _taskbar_notification_bounds(taskbar)
        if screen_bounds is not None:
            notification_bounds = _taskbar_notification_client_bounds(taskbar, screen_bounds)
        screen_bounds = _taskbar_primary_controls_bounds(taskbar)
        if screen_bounds is not None:
            primary_bounds = _taskbar_notification_client_bounds(taskbar, screen_bounds)
    return _taskbar_overlay_position(
        (client.left, client.top, client.right, client.bottom),
        configured_width,
        notification_bounds,
        desired_width,
        primary_bounds,
    )


def _rect_covers_monitor(
    window_bounds: tuple[int, int, int, int],
    monitor_bounds: tuple[int, int, int, int],
    tolerance: int = FULLSCREEN_TOLERANCE_PX,
) -> bool:
    window_left, window_top, window_right, window_bottom = window_bounds
    monitor_left, monitor_top, monitor_right, monitor_bottom = monitor_bounds
    return (
        window_left <= monitor_left + tolerance
        and window_top <= monitor_top + tolerance
        and window_right >= monitor_right - tolerance
        and window_bottom >= monitor_bottom - tolerance
    )


def _window_bounds(hwnd: HWND) -> Optional[tuple[int, int, int, int]]:
    bounds = wintypes.RECT()
    try:
        result = dwmapi.DwmGetWindowAttribute(
            hwnd,
            DWMWA_EXTENDED_FRAME_BOUNDS,
            ctypes.byref(bounds),
            ctypes.sizeof(bounds),
        )
    except (AttributeError, OSError):
        result = -1
    if result == 0 and bounds.right > bounds.left and bounds.bottom > bounds.top:
        return bounds.left, bounds.top, bounds.right, bounds.bottom
    if not u32.GetWindowRect(hwnd, ctypes.byref(bounds)):
        return None
    if bounds.right <= bounds.left or bounds.bottom <= bounds.top:
        return None
    return bounds.left, bounds.top, bounds.right, bounds.bottom


def _monitor_for_window(
    hwnd: HWND,
) -> Optional[tuple[HANDLE, tuple[int, int, int, int]]]:
    monitor = u32.MonitorFromWindow(hwnd, MONITOR_DEFAULTTONULL)
    if not monitor:
        return None
    info = MONITORINFO()
    info.cbSize = ctypes.sizeof(MONITORINFO)
    if not u32.GetMonitorInfoW(monitor, ctypes.byref(info)):
        return None
    bounds = info.rcMonitor
    if bounds.right <= bounds.left or bounds.bottom <= bounds.top:
        return None
    return monitor, (bounds.left, bounds.top, bounds.right, bounds.bottom)


def _window_class_name(hwnd: HWND) -> str:
    class_name = ctypes.create_unicode_buffer(256)
    if not u32.GetClassNameW(hwnd, class_name, len(class_name)):
        return ""
    return class_name.value


def _taskbar_notification_bounds(
    taskbar: HWND,
) -> Optional[tuple[int, int, int, int]]:
    """Return the visible Explorer notification-area rectangle in screen space."""
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
            candidates.append((bounds.left, bounds.top, bounds.right, bounds.bottom))
        except (AttributeError, OSError, TypeError):
            return True
        return True

    callback = WNDENUMPROC(collect)
    try:
        enum_children(taskbar, callback, 0)
    except (AttributeError, OSError, TypeError):
        return None
    return max(candidates, key=lambda bounds: bounds[0], default=None)


def _window_process_name(hwnd: HWND) -> str:
    get_process_id = getattr(u32, "GetWindowThreadProcessId", None)
    if get_process_id is None:
        return ""
    process_id = DWORD()
    if not get_process_id(hwnd, ctypes.byref(process_id)):
        return ""
    open_process = getattr(k32, "OpenProcess", None)
    query_name = getattr(k32, "QueryFullProcessImageNameW", None)
    close_handle = getattr(k32, "CloseHandle", None)
    if open_process is None or query_name is None or close_handle is None:
        return ""
    process = open_process(0x1000, False, process_id.value)
    if not process:
        return ""
    try:
        path = ctypes.create_unicode_buffer(32768)
        path_length = DWORD(len(path))
        if not query_name(process, 0, path, ctypes.byref(path_length)):
            return ""
        return path.value.rsplit("\\", 1)[-1].casefold()
    finally:
        close_handle(process)


def _is_windows_shell_window(hwnd: HWND) -> bool:
    return (
        _window_class_name(hwnd) in DESKTOP_SHELL_CLASSES
        or _window_process_name(hwnd).casefold() in SHELL_PROCESS_NAMES
    )


def _is_desktop_shell_window(hwnd: HWND) -> bool:
    return _is_windows_shell_window(hwnd)


def _on_win_event(
    _hook: HANDLE,
    event: int,
    _hwnd: HWND,
    _id_object: int,
    _id_child: int,
    _event_thread: int,
    _event_time: int,
) -> None:
    if event not in (EVENT_SYSTEM_FOREGROUND, EVENT_OBJECT_REORDER):
        return
    try:
        if _runtime is not None and _runtime.hwnd and not _runtime.overlay_hidden:
            _ensure_overlay_above_taskbar(_runtime.hwnd)
            post_message = getattr(u32, "PostMessageW", None)
            if post_message is not None:
                post_message(_runtime.hwnd, WM_SHELL_ZORDER_REPAIR, 0, 0)
    except Exception:
        # WinEvent callbacks must never escape into the native callback boundary.
        return


def _install_shell_event_hook() -> bool:
    global _winevent_hook, _reorder_winevent_hook, _winevent_proc
    if _winevent_hook and _reorder_winevent_hook:
        return True
    set_hook = getattr(u32, "SetWinEventHook", None)
    if set_hook is None:
        return False
    _winevent_proc = WINEVENTPROC(_on_win_event)
    foreground_hook = set_hook(
        EVENT_SYSTEM_FOREGROUND,
        EVENT_SYSTEM_FOREGROUND,
        None,
        _winevent_proc,
        0,
        0,
        WINEVENT_OUTOFCONTEXT,
    )
    if not foreground_hook:
        _winevent_proc = None
        return False
    reorder_hook = set_hook(
        EVENT_OBJECT_REORDER,
        EVENT_OBJECT_REORDER,
        None,
        _winevent_proc,
        0,
        0,
        WINEVENT_OUTOFCONTEXT,
    )
    if not reorder_hook:
        u32.UnhookWinEvent(foreground_hook)
        _winevent_proc = None
        return False
    _winevent_hook = foreground_hook
    _reorder_winevent_hook = reorder_hook
    return True


def _uninstall_shell_event_hook() -> None:
    global _winevent_hook, _reorder_winevent_hook, _winevent_proc
    if _winevent_hook:
        u32.UnhookWinEvent(_winevent_hook)
    if _reorder_winevent_hook:
        u32.UnhookWinEvent(_reorder_winevent_hook)
    _winevent_hook = None
    _reorder_winevent_hook = None
    _winevent_proc = None


def _foreground_fullscreen_on_taskbar_monitor(
    overlay_hwnd: HWND,
) -> Optional[bool]:
    foreground = u32.GetForegroundWindow()
    if not foreground:
        return None
    taskbar = u32.FindWindowW("Shell_TrayWnd", None)
    if not taskbar:
        return None
    if foreground == overlay_hwnd or foreground == taskbar:
        return False
    if _is_desktop_shell_window(foreground):
        return False
    if not u32.IsWindowVisible(foreground) or u32.IsIconic(foreground):
        return False
    foreground_monitor = _monitor_for_window(foreground)
    taskbar_monitor = _monitor_for_window(taskbar)
    if foreground_monitor is None or taskbar_monitor is None:
        return None
    if foreground_monitor[0] != taskbar_monitor[0]:
        return False
    foreground_bounds = _window_bounds(foreground)
    if foreground_bounds is None:
        return None
    return _rect_covers_monitor(foreground_bounds, foreground_monitor[1])


def _taskbar_visible() -> bool:
    taskbar = u32.FindWindowW("Shell_TrayWnd", None)
    return bool(taskbar and u32.IsWindowVisible(taskbar))


def _ensure_overlay_above_taskbar(hwnd: HWND) -> bool:
    if not hwnd:
        return False
    taskbar = u32.FindWindowW("Shell_TrayWnd", None)
    if not taskbar or not u32.IsWindowVisible(taskbar):
        return False
    get_parent = getattr(u32, "GetParent", None)
    if get_parent is not None and get_parent(hwnd) == taskbar:
        if not u32.GetWindow(hwnd, GW_HWNDPREV):
            return True
        return bool(
            u32.SetWindowPos(
                hwnd,
                HWND_TOP,
                0,
                0,
                0,
                0,
                SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE,
            )
        )
    window_above = u32.GetWindow(hwnd, GW_HWNDPREV)
    while window_above:
        if window_above == taskbar:
            return bool(
                u32.SetWindowPos(
                    hwnd,
                    HWND_TOPMOST,
                    0,
                    0,
                    0,
                    0,
                    SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE,
                )
            )
        window_above = u32.GetWindow(window_above, GW_HWNDPREV)
    return True


def _sync_overlay_visibility(hwnd: HWND) -> None:
    if _runtime is None or not hwnd:
        return
    if not _taskbar_visible():
        should_hide = True
    else:
        fullscreen = _foreground_fullscreen_on_taskbar_monitor(hwnd)
        if fullscreen is None:
            return
        should_hide = fullscreen
    if should_hide and not _runtime.overlay_hidden:
        u32.ShowWindow(hwnd, SW_HIDE)
        _runtime.overlay_hidden = True
    elif not should_hide and _runtime.overlay_hidden:
        _reposition(hwnd)
        u32.ShowWindow(hwnd, SW_SHOWNOACTIVATE)
        _runtime.overlay_hidden = False


def _reposition(hwnd: HWND) -> bool:
    if _runtime is None or not hwnd:
        return False
    find_window = getattr(u32, "FindWindowW", None)
    if find_window is None:
        return False
    taskbar = find_window("Shell_TrayWnd", None)
    if not taskbar:
        return False
    display = getattr(_runtime.settings, "display", {}) or {}
    configured_width = int(display.get("width", 460))
    full_needed = _content_width(include_countdown=True) + _CONTENT_MARGINS_PX
    position = _taskbar_child_position(taskbar, configured_width, desired_width=full_needed)
    if position is None:
        bounds = wintypes.RECT()
        get_window_rect = getattr(u32, "GetWindowRect", None)
        if get_window_rect is None or not get_window_rect(taskbar, ctypes.byref(bounds)):
            return False
        taskbar_bounds = (bounds.left, bounds.top, bounds.right, bounds.bottom)
        screen_position = _taskbar_overlay_position(
            taskbar_bounds, configured_width, desired_width=full_needed
        )
        if screen_position is None:
            return False
        x, y, width, height = screen_position
        position = (x - bounds.left, y - bounds.top, width, height)
    _runtime.compact_countdowns = position[2] + 4 < full_needed
    if _runtime.last_position == position:
        return True
    x, y, width, height = position
    if not u32.SetWindowPos(
        hwnd,
        None,
        x,
        y,
        width,
        height,
        SWP_NOZORDER | SWP_NOACTIVATE,
    ):
        return False
    _runtime.last_position = position
    return True



def _measure_text(hdc: HDC, text: str) -> int:
    size = wintypes.SIZE()
    g32.GetTextExtentPoint32W(hdc, text, len(text), ctypes.byref(size))
    return size.cx


_CONTENT_MARGINS_PX = 24


def _taskbar_window_map() -> dict[str, tuple[str, ...]]:
    """Read validated per-provider taskbar window preferences from settings."""
    if _runtime is None:
        return {}
    display = getattr(_runtime.settings, "display", {}) or {}
    mapping = display.get("taskbar_windows")
    return mapping if isinstance(mapping, dict) else {}


def _content_width(include_countdown: bool = True) -> int:
    """Measure the rendered segment width with the active font (0 when no view)."""
    if _runtime is None:
        return 0
    hdc = u32.GetDC(None)
    if not hdc:
        return 0
    try:
        display = getattr(_runtime.settings, "display", {}) or {}
        font = _font_cache.get(
            str(display.get("font_name", "Segoe UI Variable Display")),
            int(display.get("font_size", 18)),
        )
        old_font = g32.SelectObject(hdc, font)
        try:
            segments = _render_segments(
                _runtime.view,
                getattr(_runtime.settings, "provider_styles", {}),
                include_countdown=include_countdown,
                taskbar_window_map=_taskbar_window_map(),
            )
            return sum(
                _measure_text(hdc, segment.text) + segment.gap_after
                for segment in segments
            )
        finally:
            g32.SelectObject(hdc, old_font)
    finally:
        u32.ReleaseDC(None, hdc)


def _draw_text(hdc: HDC, text: str, x: int, height: int) -> None:
    rectangle = wintypes.RECT(x, 0, x + 600, height)
    u32.DrawTextW(hdc, text, -1, ctypes.byref(rectangle), DT_SINGLELINE | DT_VCENTER | DT_LEFT | DT_NOCLIP)


def _paint(hwnd: HWND) -> None:
    if _runtime is None:
        return
    paint = PAINTSTRUCT()
    hdc = u32.BeginPaint(hwnd, ctypes.byref(paint))
    if not hdc:
        return
    rectangle = wintypes.RECT()
    u32.GetClientRect(hwnd, ctypes.byref(rectangle))
    width, height = max(1, rectangle.right), max(1, rectangle.bottom)
    memory_dc = g32.CreateCompatibleDC(hdc)
    bitmap = g32.CreateCompatibleBitmap(hdc, width, height)
    old_bitmap = g32.SelectObject(memory_dc, bitmap)
    brush = g32.CreateSolidBrush(COLORKEY_RGB)
    u32.FillRect(memory_dc, ctypes.byref(rectangle), brush)
    g32.DeleteObject(brush)
    g32.SetBkMode(memory_dc, BK_TRANSPARENT)
    font = _font_cache.get(
        str(_runtime.settings.display.get("font_name", "Segoe UI Variable Display")),
        int(_runtime.settings.display.get("font_size", 18)),
    )
    old_font = g32.SelectObject(memory_dc, font)

    segments = _render_segments(
        _runtime.view,
        _runtime.settings.provider_styles,
        include_countdown=not _runtime.compact_countdowns,
        taskbar_window_map=_taskbar_window_map(),
    )
    measured = tuple(
        (segment, _measure_text(memory_dc, segment.text))
        for segment in segments
    )
    content_width = sum(
        text_width + segment.gap_after
        for segment, text_width in measured
    )
    x = _left_aligned_start(width, content_width)
    for segment, text_width in measured:
        g32.SetTextColor(memory_dc, segment.color)
        _draw_text(memory_dc, segment.text, x, height)
        x += text_width + segment.gap_after

    g32.SelectObject(memory_dc, old_font)
    g32.BitBlt(hdc, 0, 0, width, height, memory_dc, 0, 0, 0x00CC0020)
    g32.SelectObject(memory_dc, old_bitmap)
    g32.DeleteObject(bitmap)
    g32.DeleteDC(memory_dc)
    u32.EndPaint(hwnd, ctypes.byref(paint))


def _show_menu(hwnd: HWND) -> None:
    if _runtime is None:
        return
    menu = u32.CreatePopupMenu()
    try:
        u32.AppendMenuW(menu, MF_STRING, 1, "Open dashboard")
        u32.AppendMenuW(menu, MF_STRING, 2, "Refresh now")
        u32.AppendMenuW(menu, MF_SEPARATOR, 0, None)
        for detail in context_detail_lines(_runtime.view):
            u32.AppendMenuW(menu, MF_STRING | MF_GRAYED, 0, detail[:160])
        u32.AppendMenuW(menu, MF_SEPARATOR, 0, None)
        u32.AppendMenuW(menu, MF_STRING, 9, "Exit")
        point = wintypes.POINT()
        u32.GetCursorPos(ctypes.byref(point))
        u32.SetForegroundWindow(hwnd)
        command = u32.TrackPopupMenu(menu, TPM_RETURNCMD, point.x, point.y, 0, hwnd, None)
    finally:
        u32.DestroyMenu(menu)
    if command == 1:
        _runtime.on_open()
    elif command == 2:
        _runtime.on_refresh()
    elif command == 9:
        u32.PostMessageW(hwnd, WM_CLOSE, 0, 0)


def _wnd_proc(hwnd: HWND, message: int, wparam: int, lparam: int) -> int:
    if _runtime is None:
        return u32.DefWindowProcW(hwnd, message, wparam, lparam)
    try:
        if message == WM_SHELL_ZORDER_REPAIR:
            if not _runtime.overlay_hidden:
                _ensure_overlay_above_taskbar(hwnd)
            return 0
        if message == WM_TIMER:
            if wparam == SHELL_SYNC_TIMER_ID:
                _sync_overlay_visibility(hwnd)
                if not _runtime.overlay_hidden:
                    # Explorer can keep this taskbar child alive without
                    # forwarding WM_DISPLAYCHANGE/WM_DPICHANGED to it.  A
                    # no-op reposition check keeps the anchor current after
                    # a resolution or taskbar-layout change; SetWindowPos is
                    # only issued when the client geometry actually differs.
                    _reposition(hwnd)
                    _ensure_overlay_above_taskbar(hwnd)
                return 0
            if wparam == DATA_REFRESH_TIMER_ID:
                _runtime.ticks += 1
                view = build_tracker_view(
                    _runtime.store.load(),
                    _runtime.settings.enabled_providers,
                )
                if view.fingerprint != _runtime.view.fingerprint:
                    _runtime.view = view
                    _reposition(hwnd)
                    u32.InvalidateRect(hwnd, None, 0)
                return 0
            return u32.DefWindowProcW(hwnd, message, wparam, lparam)
        if message == WM_PAINT:
            _paint(hwnd)
            return 0
        if message == WM_ERASEBKGND:
            return 1
        if message in (WM_DISPLAYCHANGE, WM_SETTINGCHANGE, WM_DPICHANGED) or (WM_TASKBARCREATED and message == WM_TASKBARCREATED):
            _reposition(hwnd)
            return 0
        if message == WM_LBUTTONDBLCLK:
            _runtime.on_open()
            return 0
        if message == WM_RBUTTONDOWN:
            _show_menu(hwnd)
            return 0
        if message == WM_CLOSE:
            _runtime.shutting_down = True
            _uninstall_shell_event_hook()
            u32.KillTimer(hwnd, DATA_REFRESH_TIMER_ID)
            u32.KillTimer(hwnd, SHELL_SYNC_TIMER_ID)
            u32.DestroyWindow(hwnd)
            return 0
        if message == WM_DESTROY:
            _uninstall_shell_event_hook()
            _font_cache.cleanup()
            if _runtime.hwnd == hwnd:
                _runtime.hwnd = None
            if _runtime.shutting_down:
                u32.PostQuitMessage(0)
            return 0
    except Exception:
        # UI callback failures must not tear down Explorer's taskbar overlay.
        return u32.DefWindowProcW(hwnd, message, wparam, lparam)
    return u32.DefWindowProcW(hwnd, message, wparam, lparam)


def _create_taskbar_child(
    instance: HANDLE,
    class_name: str,
    width: int,
    taskbar: HWND,
) -> HANDLE:
    return u32.CreateWindowExW(
        WS_EX_NOACTIVATE | WS_EX_LAYERED | WS_EX_TOPMOST,
        class_name,
        "Q-Tracker",
        WS_CHILD | WS_VISIBLE | WS_CLIPCHILDREN | WS_CLIPSIBLINGS | WS_SYSMENU,
        0,
        0,
        width,
        48,
        taskbar,
        None,
        instance,
        None,
    )


def _create_window(max_retries: int = 30, retry_delay: float = 0.5) -> bool:
    global _wndproc_ref, _background_brush
    if _runtime is None:
        return False
    if _runtime.hwnd:
        return True
    import time

    taskbar = None
    bounds = wintypes.RECT()
    get_rect = getattr(u32, "GetWindowRect", None)
    for attempt in range(max_retries):
        taskbar = u32.FindWindowW("Shell_TrayWnd", None)
        if taskbar:
            if get_rect and get_rect(taskbar, ctypes.byref(bounds)):
                if (bounds.right - bounds.left) > 0 and (bounds.bottom - bounds.top) > 0:
                    break
                taskbar = None
            else:
                break
        if attempt < max_retries - 1 and retry_delay > 0:
            time.sleep(retry_delay)

    if not taskbar:
        return False
    instance = k32.GetModuleHandleW(None)
    class_name = "QTrackerTaskbarV1"
    _wndproc_ref = WNDPROC(_wnd_proc)
    if _background_brush is None:
        _background_brush = g32.CreateSolidBrush(COLORKEY_RGB)
    window_class = WNDCLASSEX()
    window_class.cbSize = ctypes.sizeof(WNDCLASSEX)
    window_class.style = CS_HREDRAW | CS_VREDRAW | CS_DBLCLKS
    window_class.lpfnWndProc = _wndproc_ref
    window_class.hInstance = instance
    window_class.hCursor = u32.LoadCursorW(None, IDC_ARROW)
    window_class.hbrBackground = _background_brush
    window_class.lpszClassName = class_name
    if not u32.RegisterClassExW(ctypes.byref(window_class)):
        get_err = getattr(k32, "GetLastError", ctypes.get_last_error)
        if get_err() != ERROR_CLASS_ALREADY_EXISTS:
            return False
    _runtime.hwnd = _create_taskbar_child(
        instance,
        class_name,
        int(_runtime.settings.display.get("width", 460)),
        taskbar,
    )
    if not _runtime.hwnd:
        return False
    if hasattr(u32, "ChangeWindowMessageFilterEx") and WM_TASKBARCREATED:
        try:
            u32.ChangeWindowMessageFilterEx(_runtime.hwnd, WM_TASKBARCREATED, MSGFLT_ALLOW, None)
        except (AttributeError, OSError):
            pass
    u32.SetLayeredWindowAttributes(_runtime.hwnd, COLORKEY_RGB, 0, LWA_COLORKEY)
    _reposition(_runtime.hwnd)
    u32.SetTimer(
        _runtime.hwnd,
        DATA_REFRESH_TIMER_ID,
        int(_runtime.settings.display.get("update_interval_ms", 1_000)),
        None,
    )
    u32.SetTimer(
        _runtime.hwnd,
        SHELL_SYNC_TIMER_ID,
        SHELL_SYNC_INTERVAL_MS,
        None,
    )
    _install_shell_event_hook()
    u32.ShowWindow(_runtime.hwnd, 5)
    u32.UpdateWindow(_runtime.hwnd)
    return True


def _ensure_taskbar_window() -> bool:
    """Recreate the overlay when Explorer replaces its taskbar window."""
    if _runtime is None:
        return False
    taskbar = u32.FindWindowW("Shell_TrayWnd", None)
    hwnd = _runtime.hwnd
    if hwnd and u32.IsWindow(hwnd) and taskbar and u32.GetParent(hwnd) == taskbar:
        return True
    if hwnd:
        _runtime.hwnd = None
        if u32.IsWindow(hwnd):
            u32.DestroyWindow(hwnd)
    return _create_window(max_retries=1, retry_delay=0)



def run_taskbar(
    store: AtomicStateStore,
    settings: Settings,
    on_open: Callable[[], Any],
    on_refresh: Callable[[], Any],
) -> int:
    """Run the native taskbar message loop on the calling thread."""
    global _runtime, _background_brush
    _runtime = _Runtime(
        store=store,
        settings=settings,
        on_open=on_open,
        on_refresh=on_refresh,
        view=build_tracker_view(store.load(), settings.enabled_providers),
    )
    reconnect_timer = None
    try:
        _create_window(max_retries=10, retry_delay=0.2)
        # A thread timer is not owned by the taskbar child, so it survives when
        # Explorer destroys and replaces Shell_TrayWnd during login or restart.
        reconnect_timer = u32.SetTimer(None, 0, SHELL_RECONNECT_INTERVAL_MS, None)

        message = wintypes.MSG()
        while True:
            result = u32.GetMessageW(ctypes.byref(message), None, 0, 0)
            if result <= 0:
                break
            if (
                reconnect_timer
                and message.message == WM_TIMER
                and message.wParam == reconnect_timer
            ):
                _ensure_taskbar_window()

            u32.TranslateMessage(ctypes.byref(message))
            u32.DispatchMessageW(ctypes.byref(message))
        return 0
    finally:
        if reconnect_timer:
            u32.KillTimer(None, reconnect_timer)
            reconnect_timer = None
        _font_cache.cleanup()
        if _background_brush:
            g32.DeleteObject(_background_brush)
            _background_brush = None
        _runtime = None


if __name__ == "__main__":
    from app import main

    raise SystemExit(main([]))
