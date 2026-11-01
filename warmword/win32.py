# -*- coding: utf-8 -*-
"""Windows 系统接口的薄封装（ctypes）：鼠标键盘状态、显示器、窗口样式与位置、焦点、单实例。

坐标一律是物理像素（进程按「每显示器 DPI 感知」运行）。
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import os

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32
dwmapi = ctypes.windll.dwmapi

user32.WindowFromPoint.restype = ctypes.c_void_p
user32.WindowFromPoint.argtypes = [wt.POINT]
user32.GetAncestor.restype = ctypes.c_void_p
user32.GetAncestor.argtypes = [ctypes.c_void_p, ctypes.c_uint]
user32.GetForegroundWindow.restype = ctypes.c_void_p
user32.GetWindowThreadProcessId.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulong)]
user32.GetWindowLongPtrW.restype = ctypes.c_ssize_t
user32.GetWindowLongPtrW.argtypes = [ctypes.c_void_p, ctypes.c_int]
user32.SetWindowLongPtrW.restype = ctypes.c_ssize_t
user32.SetWindowLongPtrW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_ssize_t]
user32.SetWindowPos.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_int, ctypes.c_int,
                                ctypes.c_int, ctypes.c_int, ctypes.c_uint]
user32.ShowWindow.argtypes = [ctypes.c_void_p, ctypes.c_int]
user32.ShowWindowAsync.argtypes = [ctypes.c_void_p, ctypes.c_int]
user32.IsWindowVisible.argtypes = [ctypes.c_void_p]
user32.GetWindowRect.argtypes = [ctypes.c_void_p, ctypes.POINTER(wt.RECT)]
user32.SetForegroundWindow.argtypes = [ctypes.c_void_p]
user32.BringWindowToTop.argtypes = [ctypes.c_void_p]
user32.SetFocus.argtypes = [ctypes.c_void_p]
user32.MonitorFromPoint.restype = ctypes.c_void_p
user32.MonitorFromPoint.argtypes = [wt.POINT, ctypes.c_ulong]
user32.SetWindowDisplayAffinity.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
dwmapi.DwmSetWindowAttribute.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_void_p, ctypes.c_uint]

VK_LBUTTON, VK_SHIFT, VK_CONTROL, VK_MENU, VK_ESCAPE = 0x01, 0x10, 0x11, 0x12, 0x1B
VK_LWIN, VK_RWIN = 0x5B, 0x5C
MODIFIER_VK = {"shift": VK_SHIFT, "ctrl": VK_CONTROL, "alt": VK_MENU}

GWL_EXSTYLE, GWLP_HWNDPARENT = -20, -8
WS_EX_TOOLWINDOW, WS_EX_APPWINDOW, WS_EX_NOACTIVATE = 0x80, 0x40000, 0x08000000
HWND_TOPMOST = ctypes.c_void_p(-1)
SWP_NOSIZE, SWP_NOMOVE, SWP_NOZORDER, SWP_NOACTIVATE = 0x1, 0x2, 0x4, 0x10
SWP_FRAMECHANGED, SWP_SHOWWINDOW, SWP_ASYNCWINDOWPOS = 0x20, 0x40, 0x4000
SW_HIDE, SW_SHOWNOACTIVATE = 0, 4
WDA_EXCLUDEFROMCAPTURE = 0x11


# ---------------- 鼠标 / 键盘 ----------------
def cursor_pos() -> tuple[int, int]:
    p = wt.POINT()
    user32.GetCursorPos(ctypes.byref(p))
    return p.x, p.y


def key_down(vk: int) -> bool:
    return bool(user32.GetAsyncKeyState(vk) & 0x8000)


def only_modifier_down(name: str) -> bool:
    """只按着这一个修饰键（按 Ctrl+Alt+T 之类的快捷键时不算）。"""
    vk = MODIFIER_VK.get(name)
    if vk is None or not key_down(vk):
        return False
    others = [k for k in (VK_SHIFT, VK_CONTROL, VK_MENU, VK_LWIN, VK_RWIN) if k != vk]
    return not any(key_down(k) for k in others)


# ---------------- 显示器 ----------------
class MONITORINFO(ctypes.Structure):
    _fields_ = [("cbSize", ctypes.c_ulong), ("rcMonitor", wt.RECT),
                ("rcWork", wt.RECT), ("dwFlags", ctypes.c_ulong)]


def work_area_at(x: int, y: int) -> tuple[int, int, int, int]:
    """坐标所在显示器的工作区（不含任务栏）。"""
    try:
        hmon = user32.MonitorFromPoint(wt.POINT(int(x), int(y)), 2)  # NEAREST
        mi = MONITORINFO()
        mi.cbSize = ctypes.sizeof(MONITORINFO)
        if hmon and user32.GetMonitorInfoW(ctypes.c_void_p(hmon), ctypes.byref(mi)):
            r = mi.rcWork
            return r.left, r.top, r.right, r.bottom
    except Exception:
        pass
    return 0, 0, user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)


def scale_at(x: int, y: int) -> float:
    """坐标所在显示器的缩放比（1.0 = 100%）。"""
    try:
        hmon = user32.MonitorFromPoint(wt.POINT(int(x), int(y)), 2)
        dx, dy = ctypes.c_uint(), ctypes.c_uint()
        ctypes.windll.shcore.GetDpiForMonitor(ctypes.c_void_p(hmon), 0, ctypes.byref(dx), ctypes.byref(dy))
        return (dx.value or 96) / 96.0
    except Exception:
        return 1.0


# ---------------- 窗口 ----------------
def window_rect(hwnd) -> tuple[int, int, int, int] | None:
    r = wt.RECT()
    if hwnd and user32.GetWindowRect(ctypes.c_void_p(hwnd), ctypes.byref(r)) and r.right > r.left:
        return r.left, r.top, r.right, r.bottom
    return None


def is_visible(hwnd) -> bool:
    return bool(hwnd) and bool(user32.IsWindowVisible(ctypes.c_void_p(hwnd)))


def window_pid(hwnd) -> int:
    pid = ctypes.c_ulong()
    user32.GetWindowThreadProcessId(ctypes.c_void_p(hwnd), ctypes.byref(pid))
    return pid.value


def own_window_at(x: int, y: int) -> bool:
    hwnd = user32.WindowFromPoint(wt.POINT(x, y))
    return bool(hwnd) and window_pid(hwnd) == os.getpid()


def make_tool_window(hwnd, no_activate: bool = False, owner=None) -> None:
    """不进任务栏、不进 Alt+Tab；浮窗另外不抢焦点。要在窗口第一次显示之前调用。"""
    h = ctypes.c_void_p(hwnd)
    if owner:
        user32.SetWindowLongPtrW(h, GWLP_HWNDPARENT, int(owner))
    ex = user32.GetWindowLongPtrW(h, GWL_EXSTYLE)
    ex = (ex | WS_EX_TOOLWINDOW) & ~WS_EX_APPWINDOW
    ex = ex | WS_EX_NOACTIVATE if no_activate else ex & ~WS_EX_NOACTIVATE
    user32.SetWindowLongPtrW(h, GWL_EXSTYLE, ex)
    user32.SetWindowPos(h, None, 0, 0, 0, 0,
                        SWP_NOSIZE | SWP_NOMOVE | SWP_NOZORDER | SWP_NOACTIVATE | SWP_FRAMECHANGED)


def round_corners(hwnd) -> None:
    """Windows 11 原生圆角和阴影（Win10 上无效果，忽略）。"""
    try:
        val = ctypes.c_int(2)  # DWMWCP_ROUND
        dwmapi.DwmSetWindowAttribute(ctypes.c_void_p(hwnd), 33, ctypes.byref(val), 4)
    except Exception:
        pass


def exclude_from_capture(hwnd) -> None:
    """截图时看不见这个窗口：浮窗和高亮框不会混进下一次文字识别（Win10 2004 起支持）。"""
    try:
        user32.SetWindowDisplayAffinity(ctypes.c_void_p(hwnd), WDA_EXCLUDEFROMCAPTURE)
    except Exception:
        pass


def show_at(hwnd, x: int, y: int, w: int | None = None, h: int | None = None,
            activate: bool = False) -> None:
    """放到 (x, y) 并显示，置顶；activate=False 时不抢焦点。"""
    flags = SWP_SHOWWINDOW | (0 if activate else SWP_NOACTIVATE)
    if w is None or h is None:
        flags |= SWP_NOSIZE
        w = h = 0
    user32.SetWindowPos(ctypes.c_void_p(hwnd), HWND_TOPMOST, int(x), int(y), int(w), int(h), flags)


def resize(hwnd, w: int, h: int) -> None:
    user32.SetWindowPos(ctypes.c_void_p(hwnd), None, 0, 0, int(w), int(h),
                        SWP_NOMOVE | SWP_NOZORDER | SWP_NOACTIVATE)


def hide(hwnd) -> None:
    user32.ShowWindowAsync(ctypes.c_void_p(hwnd), SW_HIDE)


# ---------------- 焦点 ----------------
def foreground() -> int:
    return int(user32.GetForegroundWindow() or 0)


def activate(hwnd) -> None:
    """把窗口真正切到前台并拿到键盘焦点（查词窗弹出来就能直接打字）。"""
    h = ctypes.c_void_p(hwnd)
    fg = user32.GetForegroundWindow()
    me = kernel32.GetCurrentThreadId()
    fg_tid = user32.GetWindowThreadProcessId(fg, None) if fg else 0
    attached = bool(fg_tid and fg_tid != me and user32.AttachThreadInput(me, fg_tid, True))
    try:
        user32.BringWindowToTop(h)
        user32.SetForegroundWindow(h)
        user32.SetFocus(h)
    finally:
        if attached:
            user32.AttachThreadInput(me, fg_tid, False)


def set_foreground(hwnd) -> None:
    if hwnd:
        try:
            user32.SetForegroundWindow(ctypes.c_void_p(hwnd))
        except Exception:
            pass


# ---------------- 单实例 ----------------
QUIT_EVENT = "WarmWordHelper_QuitEvent_v1"
SHOW_EVENT = "WarmWordHelper_ShowLookup_v1"
kernel32.CreateEventW.restype = ctypes.c_void_p
kernel32.OpenEventW.restype = ctypes.c_void_p
kernel32.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_ulong]


def single_instance() -> bool:
    kernel32.CreateMutexW(None, False, "WarmWordHelper_SingleInstance")
    return kernel32.GetLastError() != 183  # ERROR_ALREADY_EXISTS


def signal_event(name: str) -> bool:
    h = kernel32.OpenEventW(0x0002, False, name)  # EVENT_MODIFY_STATE
    if not h:
        return False
    kernel32.SetEvent(ctypes.c_void_p(h))
    kernel32.CloseHandle(ctypes.c_void_p(h))
    return True


def wait_event_loop(name: str, on_signal, should_stop) -> None:
    """等别的实例发来的信号（再次启动 → 唤出查词窗；重启 → 旧实例退出）。"""
    h = kernel32.CreateEventW(None, False, False, name)
    while not should_stop():
        if kernel32.WaitForSingleObject(ctypes.c_void_p(h), 1000) == 0:
            on_signal()


def dpi_awareness() -> None:
    """必须在导入界面库之前调用，否则会被界面库的默认设置抢先，鼠标坐标与截图坐标对不上。"""
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)  # 每显示器感知
    except Exception:
        try:
            user32.SetProcessDPIAware()
        except Exception:
            pass
