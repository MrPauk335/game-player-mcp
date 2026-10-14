"""Win32 layer: window discovery, geometry, focus and synthetic input.

Everything here is built on ``ctypes`` against user32/kernel32/dwmapi, so the
application needs no third-party dependency for the parts that actually touch
the game.
"""

from __future__ import annotations

import ctypes
import os
import threading
import time
from ctypes import wintypes
from dataclasses import dataclass, field

# --------------------------------------------------------------------------- #
# DLL handles
# --------------------------------------------------------------------------- #

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
try:
    dwmapi = ctypes.WinDLL("dwmapi")
except OSError:  # pragma: no cover - very old systems
    dwmapi = None

ULONG_PTR = ctypes.c_ulonglong if ctypes.sizeof(ctypes.c_void_p) == 8 else ctypes.c_ulong

# --------------------------------------------------------------------------- #
# Constants
# --------------------------------------------------------------------------- #

GWL_STYLE = -16
GWL_EXSTYLE = -20

WS_EX_TOOLWINDOW = 0x00000080
WS_EX_APPWINDOW = 0x00040000
WS_EX_NOACTIVATE = 0x08000000

SW_RESTORE = 9
SW_SHOW = 5
SW_MINIMIZE = 6

DWMWA_EXTENDED_FRAME_BOUNDS = 9
DWMWA_CLOAKED = 14

PROCESS_QUERY_LIMITED_INFORMATION = 0x1000

INPUT_MOUSE = 0
INPUT_KEYBOARD = 1

KEYEVENTF_EXTENDEDKEY = 0x0001
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_UNICODE = 0x0004
KEYEVENTF_SCANCODE = 0x0008

MOUSEEVENTF_MOVE = 0x0001
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
MOUSEEVENTF_RIGHTDOWN = 0x0008
MOUSEEVENTF_RIGHTUP = 0x0010
MOUSEEVENTF_MIDDLEDOWN = 0x0020
MOUSEEVENTF_MIDDLEUP = 0x0040
MOUSEEVENTF_WHEEL = 0x0800
MOUSEEVENTF_ABSOLUTE = 0x8000
MOUSEEVENTF_VIRTUALDESK = 0x4000

MAPVK_VK_TO_VSC = 0

SM_XVIRTUALSCREEN = 76
SM_YVIRTUALSCREEN = 77
SM_CXVIRTUALSCREEN = 78
SM_CYVIRTUALSCREEN = 79

VK_F12 = 0x7B


# --------------------------------------------------------------------------- #
# Structures
# --------------------------------------------------------------------------- #


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [
        ("uMsg", wintypes.DWORD),
        ("wParamL", wintypes.WORD),
        ("wParamH", wintypes.WORD),
    ]


class _INPUTUNION(ctypes.Union):
    _fields_ = [("ki", KEYBDINPUT), ("mi", MOUSEINPUT), ("hi", HARDWAREINPUT)]


class WINDOWPLACEMENT(ctypes.Structure):
    """Carries ``rcNormalPosition`` — the size a minimized window restores to."""

    _fields_ = [
        ("length", wintypes.UINT),
        ("flags", wintypes.UINT),
        ("showCmd", wintypes.UINT),
        ("ptMinPosition", wintypes.POINT),
        ("ptMaxPosition", wintypes.POINT),
        ("rcNormalPosition", wintypes.RECT),
    ]


class INPUT(ctypes.Structure):
    _anonymous_ = ("u",)
    _fields_ = [("type", wintypes.DWORD), ("u", _INPUTUNION)]


WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

# --------------------------------------------------------------------------- #
# Prototypes
# --------------------------------------------------------------------------- #

user32.EnumWindows.argtypes = [WNDENUMPROC, wintypes.LPARAM]
user32.EnumWindows.restype = wintypes.BOOL
user32.IsWindowVisible.argtypes = [wintypes.HWND]
user32.IsWindowVisible.restype = wintypes.BOOL
user32.IsWindow.argtypes = [wintypes.HWND]
user32.IsWindow.restype = wintypes.BOOL
user32.IsIconic.argtypes = [wintypes.HWND]
user32.IsIconic.restype = wintypes.BOOL
user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
user32.GetWindowTextLengthW.restype = ctypes.c_int
user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.GetWindowTextW.restype = ctypes.c_int
user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
user32.GetWindowThreadProcessId.restype = wintypes.DWORD
user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
user32.GetWindowRect.restype = wintypes.BOOL
user32.GetClientRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
user32.GetClientRect.restype = wintypes.BOOL
user32.ClientToScreen.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.POINT)]
user32.ClientToScreen.restype = wintypes.BOOL
user32.SetForegroundWindow.argtypes = [wintypes.HWND]
user32.SetForegroundWindow.restype = wintypes.BOOL
user32.GetForegroundWindow.argtypes = []
user32.GetForegroundWindow.restype = wintypes.HWND
user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
user32.ShowWindow.restype = wintypes.BOOL
# Without these, a 64-bit lParam (packed mouse coordinates) is truncated to a
# signed int and every posted click lands in the wrong place.
user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT,
                                wintypes.WPARAM, wintypes.LPARAM]
user32.PostMessageW.restype = wintypes.BOOL
user32.MapVirtualKeyW.argtypes = [wintypes.UINT, wintypes.UINT]
user32.MapVirtualKeyW.restype = wintypes.UINT
user32.BringWindowToTop.argtypes = [wintypes.HWND]
user32.BringWindowToTop.restype = wintypes.BOOL
user32.SetActiveWindow.argtypes = [wintypes.HWND]
user32.SetActiveWindow.restype = wintypes.HWND
user32.SetFocus.argtypes = [wintypes.HWND]
user32.SetFocus.restype = wintypes.HWND
user32.AttachThreadInput.argtypes = [wintypes.DWORD, wintypes.DWORD, wintypes.BOOL]
user32.AttachThreadInput.restype = wintypes.BOOL
user32.SendInput.argtypes = [wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int]
user32.SendInput.restype = wintypes.UINT
user32.MapVirtualKeyW.argtypes = [wintypes.UINT, wintypes.UINT]
user32.MapVirtualKeyW.restype = wintypes.UINT
user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
user32.GetAsyncKeyState.restype = ctypes.c_short
user32.GetSystemMetrics.argtypes = [ctypes.c_int]
user32.GetSystemMetrics.restype = ctypes.c_int
user32.SetWindowPos.argtypes = [
    wintypes.HWND,
    wintypes.HWND,
    ctypes.c_int,
    ctypes.c_int,
    ctypes.c_int,
    ctypes.c_int,
    ctypes.c_uint,
]
user32.SetWindowPos.restype = wintypes.BOOL
user32.VkKeyScanW.argtypes = [wintypes.WCHAR]
user32.VkKeyScanW.restype = ctypes.c_short
user32.GetWindowPlacement.argtypes = [wintypes.HWND, ctypes.POINTER(WINDOWPLACEMENT)]
user32.GetWindowPlacement.restype = wintypes.BOOL

kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
kernel32.OpenProcess.restype = wintypes.HANDLE
kernel32.QueryFullProcessImageNameW.argtypes = [
    wintypes.HANDLE,
    wintypes.DWORD,
    wintypes.LPWSTR,
    ctypes.POINTER(wintypes.DWORD),
]
kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
kernel32.CloseHandle.restype = wintypes.BOOL
kernel32.GetCurrentThreadId.argtypes = []
kernel32.GetCurrentThreadId.restype = wintypes.DWORD

HWND_TOPMOST = -1
HWND_NOTOPMOST = -2
SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SWP_NOACTIVATE = 0x0010


def _get_window_long(hwnd: int, index: int) -> int:
    if ctypes.sizeof(ctypes.c_void_p) == 8:
        fn = user32.GetWindowLongPtrW
        fn.argtypes = [wintypes.HWND, ctypes.c_int]
        fn.restype = ctypes.c_ssize_t
        return int(fn(hwnd, index))
    fn = user32.GetWindowLongW
    fn.argtypes = [wintypes.HWND, ctypes.c_int]
    fn.restype = ctypes.c_long
    return int(fn(hwnd, index))


# --------------------------------------------------------------------------- #
# DPI
# --------------------------------------------------------------------------- #

_dpi_ready = False


def enable_dpi_awareness() -> str:
    """Make the process DPI aware so screen coordinates match physical pixels.

    Without this, window rectangles and ``ImageGrab`` disagree on scaled
    displays and every click lands in the wrong place.
    """
    global _dpi_ready
    if _dpi_ready:
        return "already"
    mode = "unknown"
    try:
        # DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2
        if user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4)):
            mode = "per-monitor-v2"
    except Exception:
        pass
    if mode == "unknown":
        try:
            ctypes.WinDLL("shcore").SetProcessDpiAwareness(2)
            mode = "per-monitor"
        except Exception:
            try:
                user32.SetProcessDPIAware()
                mode = "system"
            except Exception:
                mode = "none"
    _dpi_ready = True
    return mode


def dpi_scale_for(hwnd: int = 0) -> float:
    """Effective scale between logical and physical pixels for a window.

    1.0 means "coordinates already match the screen". Anything above 1.0 means
    Windows is virtualising our coordinates and every screen rectangle we read
    is too small by this factor.
    """
    try:
        if not _dpi_ready:
            enable_dpi_awareness()
        dpi = user32.GetDpiForWindow(hwnd) if hwnd else user32.GetDpiForSystem()
        if dpi:
            return float(dpi) / 96.0
    except Exception:
        pass
    return 1.0


# DPI awareness must be set before the process creates any window, and only the
# first call has an effect. Doing it at import time means no entry point can
# forget: ``live_run.py`` originally did, and the consequence was silent — the
# client rectangle came back virtualised (1536x800 instead of 1920x1000), the
# capture cropped the bottom-right of the game, and the model never saw its own
# health, hunger or hotbar while still describing the scene confidently.
enable_dpi_awareness()


# --------------------------------------------------------------------------- #
# Window model
# --------------------------------------------------------------------------- #

#: Processes that are never games and only add noise to the picker.
SYSTEM_PROCESSES = {
    "explorer.exe",
    "searchhost.exe",
    "startmenuexperiencehost.exe",
    "shellexperiencehost.exe",
    "applicationframehost.exe",
    "textinputhost.exe",
    "dwm.exe",
    "systemsettings.exe",
    "lockapp.exe",
    "widgets.exe",
    "widgetservice.exe",
    "sihost.exe",
    "ctfmon.exe",
    "taskmgr.exe",
    "consent.exe",
    "useroobebroker.exe",
    "runtimebroker.exe",
    "python.exe",
    "pythonw.exe",
}

_GAME_HINTS = (
    "steam", "epic", "gog", "ubisoft", "battle.net", "riot", "origin", "ea app",
    "xbox", "playnite", "unity", "unreal", "shipping", "win64", "game",
    # Java games and the launchers that run them
    "minecraft", "prism", "modrinth", "curseforge", "multimc", "atlauncher",
    "lunarclient", "badlion", "javaw",
)


@dataclass
class WindowInfo:
    hwnd: int
    title: str
    pid: int
    process: str
    exe_path: str
    left: int
    top: int
    right: int
    bottom: int
    client_left: int
    client_top: int
    client_right: int
    client_bottom: int
    minimized: bool
    is_own: bool = False
    #: Bounds a minimized window restores to. ``GetClientRect`` reports 0x0 for
    #: minimized windows (and puts them at -25600,-25600), so without this a
    #: running game looks like a zero-sized window and gets filtered out.
    normal_left: int = 0
    normal_top: int = 0
    normal_right: int = 0
    normal_bottom: int = 0

    @property
    def width(self) -> int:
        return max(0, self.right - self.left)

    @property
    def height(self) -> int:
        return max(0, self.bottom - self.top)

    @property
    def client_width(self) -> int:
        return max(0, self.client_right - self.client_left)

    @property
    def client_height(self) -> int:
        return max(0, self.client_bottom - self.client_top)

    @property
    def normal_width(self) -> int:
        return max(0, self.normal_right - self.normal_left)

    @property
    def normal_height(self) -> int:
        return max(0, self.normal_bottom - self.normal_top)

    @property
    def display_size(self) -> tuple[int, int]:
        """Size to show the user: the restored size when minimized."""
        if self.minimized and self.normal_width >= 8:
            return self.normal_width, self.normal_height
        return self.client_width, self.client_height

    @property
    def usable_size(self) -> tuple[int, int]:
        """Best estimate of the real window size for the "is this a game" test."""
        if self.minimized:
            return self.display_size
        if self.client_width >= 8 and self.client_height >= 8:
            return self.client_width, self.client_height
        return self.width, self.height

    @property
    def looks_like_game(self) -> bool:
        if self.process.lower() in SYSTEM_PROCESSES:
            return False
        width, height = self.usable_size
        if width < 320 or height < 240:
            return False
        haystack = f"{self.process} {self.exe_path} {self.title}".lower()
        if any(h in haystack for h in _GAME_HINTS):
            return True
        # Large borderless windows are usually games or media players.
        return width >= 800 and height >= 600

    def describe(self) -> str:
        width, height = self.display_size
        flag = " [свёрнуто]" if self.minimized else ""
        return (
            f"{self.title or '(без заголовка)'} — {self.process} "
            f"[{width}x{height}]{flag}"
        )

    def geometry_key(self) -> tuple:
        return (self.left, self.top, self.right, self.bottom,
                self.client_left, self.client_top,
                self.client_right, self.client_bottom)


def _window_title(hwnd: int) -> str:
    length = user32.GetWindowTextLengthW(hwnd)
    if length <= 0:
        return ""
    buf = ctypes.create_unicode_buffer(length + 1)
    user32.GetWindowTextW(hwnd, buf, length + 1)
    return buf.value


def get_window(hwnd: int) -> WindowInfo | None:
    """Re-read a window's geometry. Call it right before acting: windows move."""
    hwnd = int(hwnd or 0)
    if not hwnd or not user32.IsWindow(hwnd):
        return None
    left, top, right, bottom = _real_bounds(hwnd)
    cl, ct, cr, cb_ = _client_bounds(hwnd)
    nl, nt, nr, nb = _normal_bounds(hwnd)
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    process, exe = _process_info(pid.value)
    return WindowInfo(
        hwnd=hwnd,
        title=_window_title(hwnd),
        pid=pid.value,
        process=process,
        exe_path=exe,
        left=left, top=top, right=right, bottom=bottom,
        client_left=cl, client_top=ct, client_right=cr, client_bottom=cb_,
        minimized=bool(user32.IsIconic(hwnd)),
        is_own=pid.value == os.getpid(),
        normal_left=nl, normal_top=nt, normal_right=nr, normal_bottom=nb,
    )


def _process_info(pid: int) -> tuple[str, str]:
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return "", ""
    try:
        size = wintypes.DWORD(32768)
        buf = ctypes.create_unicode_buffer(size.value)
        if kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
            path = buf.value
            return os.path.basename(path), path
        return "", ""
    finally:
        kernel32.CloseHandle(handle)


def _is_cloaked(hwnd: int) -> bool:
    """UWP/Store apps keep invisible "cloaked" windows around forever."""
    if dwmapi is None:
        return False
    value = wintypes.DWORD(0)
    try:
        res = dwmapi.DwmGetWindowAttribute(
            wintypes.HWND(hwnd),
            wintypes.DWORD(DWMWA_CLOAKED),
            ctypes.byref(value),
            ctypes.sizeof(value),
        )
    except Exception:
        return False
    return res == 0 and value.value != 0


def _real_bounds(hwnd: int) -> tuple[int, int, int, int]:
    """Window bounds excluding the invisible resize border DWM adds."""
    rect = wintypes.RECT()
    if dwmapi is not None:
        try:
            res = dwmapi.DwmGetWindowAttribute(
                wintypes.HWND(hwnd),
                wintypes.DWORD(DWMWA_EXTENDED_FRAME_BOUNDS),
                ctypes.byref(rect),
                ctypes.sizeof(rect),
            )
            if res == 0 and rect.right > rect.left:
                return rect.left, rect.top, rect.right, rect.bottom
        except Exception:
            pass
    if user32.GetWindowRect(hwnd, ctypes.byref(rect)):
        return rect.left, rect.top, rect.right, rect.bottom
    return 0, 0, 0, 0


def _client_bounds(hwnd: int) -> tuple[int, int, int, int]:
    rect = wintypes.RECT()
    if not user32.GetClientRect(hwnd, ctypes.byref(rect)):
        return 0, 0, 0, 0
    origin = wintypes.POINT(0, 0)
    if not user32.ClientToScreen(hwnd, ctypes.byref(origin)):
        return 0, 0, 0, 0
    return origin.x, origin.y, origin.x + rect.right, origin.y + rect.bottom


def _normal_bounds(hwnd: int) -> tuple[int, int, int, int]:
    """Where the window would be if restored from minimized.

    A minimized window reports a 0x0 client area and sits at -25600,-25600, so
    this is the only way to know how big it really is.
    """
    placement = WINDOWPLACEMENT()
    placement.length = ctypes.sizeof(WINDOWPLACEMENT)
    if not user32.GetWindowPlacement(hwnd, ctypes.byref(placement)):
        return 0, 0, 0, 0
    rect = placement.rcNormalPosition
    return rect.left, rect.top, rect.right, rect.bottom


def _own_hwnds() -> set[int]:
    """Our own top-level windows, so we never offer to "play" ourselves."""
    found: set[int] = set()
    own_pid = os.getpid()

    @WNDENUMPROC
    def cb(hwnd, _lparam):
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value == own_pid:
            found.add(int(hwnd))
        return True

    user32.EnumWindows(cb, 0)
    return found


def list_windows(include_own: bool = False, min_size: int = 0) -> list[WindowInfo]:
    """Every visible, non-cloaked top-level window, newest geometry each call."""
    results: list[WindowInfo] = []
    own = set() if include_own else _own_hwnds()

    @WNDENUMPROC
    def cb(hwnd, _lparam):
        h = int(hwnd)
        if not user32.IsWindowVisible(hwnd) or _is_cloaked(h):
            return True
        style = _get_window_long(h, GWL_STYLE)
        exstyle = _get_window_long(h, GWL_EXSTYLE)
        if exstyle & WS_EX_TOOLWINDOW and not exstyle & WS_EX_APPWINDOW:
            return True
        if exstyle & WS_EX_NOACTIVATE:
            return True
        title = _window_title(h)
        if not title:
            return True
        minimized = bool(user32.IsIconic(hwnd))
        left, top, right, bottom = _real_bounds(h)
        nl, nt, nr, nb = _normal_bounds(h) if minimized else (0, 0, 0, 0)
        # A minimized window collapses to a ~199x34 frame parked at -32000, so
        # judging it by that size would hide every minimized game.
        restored_ok = minimized and (nr - nl) >= min_size and (nb - nt) >= min_size
        if not restored_ok and (right - left < min_size or bottom - top < min_size):
            return True
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        process, exe = _process_info(pid.value)
        cl, ct, cr, cb_ = _client_bounds(h)
        results.append(
            WindowInfo(
                hwnd=h,
                title=title,
                pid=pid.value,
                process=process,
                exe_path=exe,
                left=left,
                top=top,
                right=right,
                bottom=bottom,
                client_left=cl,
                client_top=ct,
                client_right=cr,
                client_bottom=cb_,
                minimized=minimized,
                is_own=h in own,
                normal_left=nl,
                normal_top=nt,
                normal_right=nr,
                normal_bottom=nb,
            )
        )
        return True

    user32.EnumWindows(cb, 0)
    results.sort(key=lambda w: w.title.lower())
    return results


# --------------------------------------------------------------------------- #
# Focus
# --------------------------------------------------------------------------- #


def foreground_window() -> int:
    return int(user32.GetForegroundWindow())


def is_foreground(hwnd: int) -> bool:
    return foreground_window() == int(hwnd)


def focus_window(hwnd: int, timeout: float = 1.5) -> bool:
    """Best-effort foreground activation.

    Windows refuses ``SetForegroundWindow`` for background processes, so we
    attach to the target's input queue (the classic workaround) and verify the
    result instead of trusting the return value.

    One situation cannot be beaten from here: while a shell overlay (the Alt+Tab
    switcher, the task view) owns the foreground, no application can take focus.
    Callers should report :func:`foreground_blocker` when this returns False.
    """
    hwnd = int(hwnd)
    if is_foreground(hwnd):
        return True
    if user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, SW_RESTORE)
    else:
        user32.ShowWindow(hwnd, SW_SHOW)

    target_tid = user32.GetWindowThreadProcessId(hwnd, None)
    fg = foreground_window()
    fg_tid = user32.GetWindowThreadProcessId(fg, None) if fg else 0
    cur_tid = kernel32.GetCurrentThreadId()

    attached: list[int] = []
    for tid in {target_tid, fg_tid}:
        if tid and tid != cur_tid:
            if user32.AttachThreadInput(cur_tid, tid, True):
                attached.append(tid)
    try:
        user32.BringWindowToTop(hwnd)
        user32.SetForegroundWindow(hwnd)
        user32.SetActiveWindow(hwnd)
        user32.SetFocus(hwnd)
        # Topmost flip reliably raises a window above an exclusive game.
        user32.SetWindowPos(hwnd, HWND_TOPMOST, 0, 0, 0, 0, SWP_NOSIZE | SWP_NOMOVE)
        user32.SetWindowPos(hwnd, HWND_NOTOPMOST, 0, 0, 0, 0, SWP_NOSIZE | SWP_NOMOVE)
    finally:
        for tid in attached:
            user32.AttachThreadInput(cur_tid, tid, False)

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if is_foreground(hwnd):
            return True
        time.sleep(0.05)
    return is_foreground(hwnd)


#: Shell processes that own overlays which block every other window from
#: taking focus while they are open.
SHELL_OVERLAY_PROCESSES = {
    "shellexperiencehost.exe", "startmenuexperiencehost.exe",
    "searchhost.exe", "textinputhost.exe", "lockapp.exe",
}


def foreground_blocker() -> str:
    """Describe whatever currently holds the foreground, for error messages."""
    fg = foreground_window()
    if not fg:
        return "неизвестное окно"
    info = get_window(fg)
    if info is None:
        return f"окно {fg}"
    title = (info.title or "(без заголовка)")[:48]
    process = info.process or "неизвестный процесс"
    return f"«{title}» ({process})"


def is_shell_overlay(window: "WindowInfo | None") -> bool:
    return bool(window and window.process.lower() in SHELL_OVERLAY_PROCESSES)


# --------------------------------------------------------------------------- #
# Input primitives
# --------------------------------------------------------------------------- #


def _send(*inputs: INPUT) -> int:
    n = len(inputs)
    array = (INPUT * n)(*inputs)
    return int(user32.SendInput(n, array, ctypes.sizeof(INPUT)))


def _key_input(vk: int, scan: int, flags: int) -> INPUT:
    item = INPUT()
    item.type = INPUT_KEYBOARD
    item.ki = KEYBDINPUT(wVk=vk, wScan=scan, dwFlags=flags, time=0, dwExtraInfo=0)
    return item


def _mouse_input(dx: int, dy: int, data: int, flags: int) -> INPUT:
    item = INPUT()
    item.type = INPUT_MOUSE
    item.mi = MOUSEINPUT(dx=dx, dy=dy, mouseData=data, dwFlags=flags, time=0, dwExtraInfo=0)
    return item


def virtual_screen() -> tuple[int, int, int, int]:
    return (
        user32.GetSystemMetrics(SM_XVIRTUALSCREEN),
        user32.GetSystemMetrics(SM_YVIRTUALSCREEN),
        user32.GetSystemMetrics(SM_CXVIRTUALSCREEN),
        user32.GetSystemMetrics(SM_CYVIRTUALSCREEN),
    )


def move_mouse_screen(x: int, y: int) -> None:
    """Absolute move to physical screen coordinates (works across monitors)."""
    vx, vy, vw, vh = virtual_screen()
    vw = max(vw - 1, 1)
    vh = max(vh - 1, 1)
    nx = int(round((x - vx) * 65535 / vw))
    ny = int(round((y - vy) * 65535 / vh))
    _send(_mouse_input(nx, ny, 0, MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_VIRTUALDESK))


def move_mouse_relative(dx: int, dy: int) -> None:
    _send(_mouse_input(int(dx), int(dy), 0, MOUSEEVENTF_MOVE))


_BUTTON_FLAGS = {
    "left": (MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP),
    "right": (MOUSEEVENTF_RIGHTDOWN, MOUSEEVENTF_RIGHTUP),
    "middle": (MOUSEEVENTF_MIDDLEDOWN, MOUSEEVENTF_MIDDLEUP),
}


def mouse_button(button: str, down: bool) -> None:
    flags = _BUTTON_FLAGS.get(button.lower())
    if not flags:
        raise ValueError(f"неизвестная кнопка мыши: {button}")
    _send(_mouse_input(0, 0, 0, flags[0] if down else flags[1]))


def click(button: str = "left") -> None:
    down, up = _BUTTON_FLAGS[button.lower()]
    _send(_mouse_input(0, 0, 0, down), _mouse_input(0, 0, 0, up))


def double_click(button: str = "left") -> None:
    down, up = _BUTTON_FLAGS[button.lower()]
    _send(
        _mouse_input(0, 0, 0, down),
        _mouse_input(0, 0, 0, up),
        _mouse_input(0, 0, 0, down),
        _mouse_input(0, 0, 0, up),
    )


def scroll(amount: int) -> None:
    """``amount`` follows the Windows convention: positive = up."""
    _send(_mouse_input(0, 0, int(amount) * 120, MOUSEEVENTF_WHEEL))


# --------------------------------------------------------------------------- #
# Background input: messages instead of SendInput
# --------------------------------------------------------------------------- #
#
# SendInput delivers to whichever window is in the foreground, so an agent using
# it owns the machine: the user cannot touch anything else. Posting the same
# messages straight to the game's window removes that coupling — measured on
# Minecraft 1.21.11 with the window deliberately unfocused, a posted "w" moved
# the player from z=-52.700 to z=-52.300, and a posted click opened a dialog.
#
# Two of the game's own settings decide whether this is enough:
#   pauseOnLostFocus:false  — otherwise the pause screen reopens every tick and
#                             swallows movement keys
#   rawMouseInput:false     — otherwise the camera reads raw mouse motion, which
#                             cannot be synthesised, and the view never turns
# Both live in the instance's options.txt.

WM_MOUSEMOVE = 0x0200
WM_LBUTTONDOWN, WM_LBUTTONUP = 0x0201, 0x0202
WM_RBUTTONDOWN, WM_RBUTTONUP = 0x0204, 0x0205
WM_MBUTTONDOWN, WM_MBUTTONUP = 0x0207, 0x0208
WM_MOUSEWHEEL = 0x020A
WM_KEYDOWN, WM_KEYUP = 0x0100, 0x0101
MK_LBUTTON, MK_RBUTTON, MK_MBUTTON = 0x0001, 0x0002, 0x0010

_POST_BUTTONS = {
    "left": (WM_LBUTTONDOWN, WM_LBUTTONUP, MK_LBUTTON),
    "right": (WM_RBUTTONDOWN, WM_RBUTTONUP, MK_RBUTTON),
    "middle": (WM_MBUTTONDOWN, WM_MBUTTONUP, MK_MBUTTON),
}


def _post(hwnd: int, message: int, wparam: int, lparam: int) -> bool:
    return bool(user32.PostMessageW(hwnd, message, wparam, lparam))


def _client_lparam(x: int, y: int) -> int:
    """Mouse coordinates packed the way WM_* expects: client area, 16 bits each."""
    return ((int(y) & 0xFFFF) << 16) | (int(x) & 0xFFFF)


def post_mouse_move(hwnd: int, x: int, y: int) -> bool:
    """Move the cursor inside the window's client area, without focusing it."""
    return _post(hwnd, WM_MOUSEMOVE, 0, _client_lparam(x, y))


def post_mouse_button(hwnd: int, button: str, down: bool,
                      x: int | None = None, y: int | None = None) -> bool:
    flags = _POST_BUTTONS.get(button.lower())
    if not flags:
        raise ValueError(f"неизвестная кнопка мыши: {button}")
    down_msg, up_msg, mask = flags
    point = _client_lparam(x or 0, y or 0)
    return _post(hwnd, down_msg if down else up_msg, mask if down else 0, point)


def post_click(hwnd: int, button: str = "left",
               x: int | None = None, y: int | None = None) -> None:
    """Hover, press and release at a client position. Clicks need all three.

    Measured: Minecraft only highlights a menu button on WM_MOUSEMOVE and only
    acts on the button message, so a bare WM_LBUTTONDOWN with no preceding move
    can land on nothing.
    """
    if x is not None and y is not None:
        post_mouse_move(hwnd, x, y)
    post_mouse_button(hwnd, button, True, x, y)
    post_mouse_button(hwnd, button, False, x, y)


def post_scroll(hwnd: int, amount: int, x: int = 0, y: int = 0) -> None:
    delta = (int(amount) * 120) & 0xFFFF
    _post(hwnd, WM_MOUSEWHEEL, delta << 16, _client_lparam(x, y))


def post_key(hwnd: int, name: str, down: bool) -> bool:
    """Send a key to the window without focusing it."""
    vk, scan = resolve_key(name)
    base = 1 | (scan << 16)
    if down:
        return _post(hwnd, WM_KEYDOWN, vk, base)
    return _post(hwnd, WM_KEYUP, vk, base | (1 << 30) | (1 << 31))


def post_key_hold(hwnd: int, name: str, seconds: float) -> None:
    post_key(hwnd, name, True)
    time.sleep(max(0.0, float(seconds)))
    post_key(hwnd, name, False)


def post_key_press(hwnd: int, name: str, delay: float = 0.03) -> None:
    post_key_hold(hwnd, name, delay)


def post_type_text(hwnd: int, text: str) -> None:
    """Type text as WM_CHAR messages, one per character.

    WM_KEYDOWN alone is not enough for a text field: the character arrives in
    WM_CHAR, so a game reading text needs both.
    """
    for char in text:
        _post(hwnd, 0x0102, ord(char), 1)
        time.sleep(0.01)


# --------------------------------------------------------------------------- #
# Key names
# --------------------------------------------------------------------------- #

VK_NAMES: dict[str, int] = {
    "esc": 0x1B, "escape": 0x1B,
    "enter": 0x0D, "return": 0x0D, "numpad_enter": 0x0D,
    "space": 0x20, "tab": 0x09,
    "shift": 0x10, "lshift": 0xA0, "rshift": 0xA1,
    "ctrl": 0x11, "control": 0x11, "lctrl": 0xA2, "rctrl": 0xA3,
    "alt": 0x12, "lalt": 0xA4, "ralt": 0xA5,
    "backspace": 0x08, "delete": 0x2E, "del": 0x2E,
    "insert": 0x2D, "home": 0x24, "end": 0x23,
    "pageup": 0x21, "pagedown": 0x22,
    "up": 0x26, "down": 0x28, "left": 0x25, "right": 0x27,
    "capslock": 0x14, "numlock": 0x90, "scrolllock": 0x91,
    "printscreen": 0x2C, "pause": 0x13,
    "minus": 0xBD, "equals": 0xBB, "comma": 0xBC, "period": 0xBE,
    "slash": 0xBF, "backslash": 0xDC, "semicolon": 0xBA, "quote": 0xDE,
    "grave": 0xC0, "lbracket": 0xDB, "rbracket": 0xDD,
    "numpad0": 0x60, "numpad1": 0x61, "numpad2": 0x62, "numpad3": 0x63,
    "numpad4": 0x64, "numpad5": 0x65, "numpad6": 0x66, "numpad7": 0x67,
    "numpad8": 0x68, "numpad9": 0x69,
    "multiply": 0x6A, "add": 0x6B, "subtract": 0x6D,
    "decimal": 0x6E, "divide": 0x6F,
    "win": 0x5B, "lwin": 0x5B, "rwin": 0x5C,
    "menu": 0x5D, "apps": 0x5D,
}
for _i in range(1, 25):
    VK_NAMES[f"f{_i}"] = 0x6F + _i
for _c in "abcdefghijklmnopqrstuvwxyz":
    VK_NAMES[_c] = ord(_c.upper())
for _d in "0123456789":
    VK_NAMES[_d] = ord(_d)

#: Keys the agent is never allowed to press on its own.
FORBIDDEN_KEYS = {"win", "lwin", "rwin", "sleep", "power"}
#: Key combos the agent must not produce.
FORBIDDEN_COMBOS = [{"alt", "f4"}, {"ctrl", "alt", "delete"}]


def resolve_key(name: str) -> tuple[int, int]:
    """Return ``(virtual_key, scan_code)`` for a key name."""
    key = (name or "").strip().lower()
    if key in ("", "none"):
        raise ValueError("пустое имя клавиши")
    if key not in VK_NAMES:
        raise ValueError(f"неизвестная клавиша: {name}")
    vk = VK_NAMES[key]
    scan = user32.MapVirtualKeyW(vk, MAPVK_VK_TO_VSC)
    return vk, int(scan)


def key_down(name: str, mode: str = "scancode") -> None:
    vk, scan = resolve_key(name)
    if mode == "scancode":
        _send(_key_input(0, scan, KEYEVENTF_SCANCODE))
    else:
        _send(_key_input(vk, scan, 0))


def key_up(name: str, mode: str = "scancode") -> None:
    vk, scan = resolve_key(name)
    if mode == "scancode":
        _send(_key_input(0, scan, KEYEVENTF_SCANCODE | KEYEVENTF_KEYUP))
    else:
        _send(_key_input(vk, scan, KEYEVENTF_KEYUP))


def key_press(name: str, mode: str = "scancode", delay: float = 0.03) -> None:
    key_down(name, mode)
    if delay:
        time.sleep(delay)
    key_up(name, mode)


def type_unicode(text: str) -> None:
    """Type literal text through ``KEYEVENTF_UNICODE`` (menus, chat boxes)."""
    for ch in text:
        code = ord(ch)
        _send(_key_input(0, code, KEYEVENTF_UNICODE))
        _send(_key_input(0, code, KEYEVENTF_UNICODE | KEYEVENTF_KEYUP))


def type_scancodes(text: str) -> None:
    """Type text as physical key presses (honours the active layout)."""
    for ch in text:
        res = user32.VkKeyScanW(ch)
        if res == -1:
            type_unicode(ch)
            continue
        vk = res & 0xFF
        shift = bool((res >> 8) & 1)
        if shift:
            key_down("shift")
        scan = int(user32.MapVirtualKeyW(vk, MAPVK_VK_TO_VSC))
        _send(_key_input(0, scan, KEYEVENTF_SCANCODE))
        _send(_key_input(0, scan, KEYEVENTF_SCANCODE | KEYEVENTF_KEYUP))
        if shift:
            key_up("shift")


# --------------------------------------------------------------------------- #
# Panic hotkey
# --------------------------------------------------------------------------- #


class PanicKeyWatcher:
    """Polls a virtual key globally so the user can abort at any moment.

    ``GetAsyncKeyState`` reads the physical keyboard state regardless of which
    window has focus, which is exactly what we need while the agent owns the
    foreground.
    """

    def __init__(self, vk: int = VK_F12, callback=None, poll: float = 0.03):
        self.vk = vk
        self.callback = callback
        self.poll = poll
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.armed = threading.Event()

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self.armed.set()
        self._thread = threading.Thread(target=self._run, name="panic-key", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self.armed.clear()
        thread = self._thread
        if thread and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=1.0)
        self._thread = None

    def _run(self) -> None:
        was_down = False
        while not self._stop.is_set():
            down = bool(user32.GetAsyncKeyState(self.vk) & 0x8000)
            if down and not was_down and self.callback:
                try:
                    self.callback()
                except Exception:
                    pass
            was_down = down
            time.sleep(self.poll)


# --------------------------------------------------------------------------- #
# Self-test helper
# --------------------------------------------------------------------------- #


def self_test() -> dict:
    mode = enable_dpi_awareness()
    windows = list_windows(include_own=False)
    return {
        "dpi_awareness": mode,
        "virtual_screen": virtual_screen(),
        "foreground": foreground_window(),
        "windows": len(windows),
        "games": sum(1 for w in windows if w.looks_like_game),
    }
