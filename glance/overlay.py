# -*- coding: utf-8 -*-
"""取词高亮：在取到的那个词上盖一层半透明色块，一眼看出取的是哪个词。

原生的分层窗口（不用网页窗口，几乎不占资源）：鼠标点击穿透、不抢焦点、不进任务栏、
截图时看不见（不干扰下一次文字识别）。窗口在自己的线程里跑消息循环。
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import threading

from . import win32

user32 = ctypes.windll.user32
gdi32 = ctypes.windll.gdi32
kernel32 = ctypes.windll.kernel32

WNDPROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, ctypes.c_void_p, ctypes.c_uint,
                             ctypes.c_size_t, ctypes.c_ssize_t)
user32.DefWindowProcW.restype = ctypes.c_ssize_t
user32.DefWindowProcW.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_size_t, ctypes.c_ssize_t]
user32.CreateWindowExW.restype = ctypes.c_void_p
user32.CreateWindowExW.argtypes = [ctypes.c_ulong, ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_ulong,
                                   ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                   ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p]
user32.SetLayeredWindowAttributes.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_ubyte,
                                              ctypes.c_ulong]
kernel32.GetModuleHandleW.restype = ctypes.c_void_p
gdi32.CreateSolidBrush.restype = ctypes.c_void_p


class WNDCLASSW(ctypes.Structure):
    _fields_ = [("style", ctypes.c_uint), ("lpfnWndProc", WNDPROC), ("cbClsExtra", ctypes.c_int),
                ("cbWndExtra", ctypes.c_int), ("hInstance", ctypes.c_void_p),
                ("hIcon", ctypes.c_void_p), ("hCursor", ctypes.c_void_p),
                ("hbrBackground", ctypes.c_void_p), ("lpszMenuName", ctypes.c_wchar_p),
                ("lpszClassName", ctypes.c_wchar_p)]


WS_POPUP = 0x80000000
WS_EX = 0x00080000 | 0x00000020 | 0x00000008 | 0x00000080 | 0x08000000
# LAYERED | TRANSPARENT（点击穿透）| TOPMOST | TOOLWINDOW | NOACTIVATE
CLASS_NAME = "GlanceHighlight"


def _rgb(hex_color: str) -> int:
    h = hex_color.lstrip("#")
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return r | (g << 8) | (b << 16)  # COLORREF 是 0x00BBGGRR


class Highlight:
    def __init__(self, color: str = "#3b82f6", alpha: int = 70, log=print):
        self._log = log
        self._color = color
        self._alpha = alpha
        self.hwnd = 0
        self._ready = threading.Event()
        self._proc = WNDPROC(lambda h, m, w, l: user32.DefWindowProcW(h, m, w, l))
        threading.Thread(target=self._run, daemon=True, name="highlight").start()
        self._ready.wait(3)

    def _run(self):
        try:
            hinst = kernel32.GetModuleHandleW(None)
            wc = WNDCLASSW()
            wc.lpfnWndProc = self._proc
            wc.hInstance = hinst
            wc.hbrBackground = gdi32.CreateSolidBrush(_rgb(self._color))
            wc.lpszClassName = CLASS_NAME
            user32.RegisterClassW(ctypes.byref(wc))
            hwnd = user32.CreateWindowExW(WS_EX, CLASS_NAME, "", WS_POPUP, 0, 0, 1, 1,
                                          None, None, hinst, None)
            if not hwnd:
                raise OSError(ctypes.get_last_error() or "CreateWindowExW 失败")
            user32.SetLayeredWindowAttributes(ctypes.c_void_p(hwnd), 0, self._alpha, 0x2)  # LWA_ALPHA
            win32.exclude_from_capture(hwnd)
            self.hwnd = hwnd
        except Exception as e:
            self._log(f"[高亮] 创建失败 {e!r}")
            self._ready.set()
            return
        self._ready.set()
        msg = wt.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))

    def show(self, box) -> None:
        if not self.hwnd or not box:
            return
        l, t, r, b = box
        pad = 2
        user32.SetWindowPos(ctypes.c_void_p(self.hwnd), win32.HWND_TOPMOST, l - pad, t - 1,
                            r - l + pad * 2, b - t + 2,
                            win32.SWP_NOACTIVATE | win32.SWP_SHOWWINDOW | win32.SWP_ASYNCWINDOWPOS)

    def hide(self) -> None:
        if self.hwnd:
            win32.hide(self.hwnd)
