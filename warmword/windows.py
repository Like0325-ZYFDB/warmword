# -*- coding: utf-8 -*-
"""界面窗口管理（pywebview + WebView2）。

- 取词浮窗启动时就建好（隐藏待命），保证第一次取词就快；其余窗口第一次用到才建
- 页面加载完会调 api.ready(名字)，把在此之前积攒的推送一次取走，不会丢内容
- 浮窗完全由系统接口控制显示 / 隐藏 / 位置：不抢焦点、不进任务栏、截图时看不见
- 窗口点关闭只是收起，程序在托盘继续运行
"""
from __future__ import annotations

import json
import threading
import time

from . import win32

SPECS = {
    # 名字: (标题, 宽, 高, 无边框, 可获得焦点, 启动时就建)
    "float": ("暖词 · 取词", 400, 320, True, False, True),
    "lookup": ("暖词", 780, 640, False, True, False),
    "translate": ("暖词 · 翻译", 640, 520, False, True, False),
    "vocab": ("暖词 · 生词本", 720, 640, False, True, False),
    "settings": ("暖词 · 设置", 760, 700, False, True, False),
    "selbtn": ("暖词 · 划词", 36, 36, True, False, False),
}
NATIVE = ("float", "selbtn")  # 这两个窗口只用系统接口显示，不经过界面库（界面库的显示会抢焦点、进任务栏）


class Win:
    def __init__(self, name: str):
        self.name = name
        self.window = None
        self.hwnd = 0
        self.ready = threading.Event()
        self.pending: dict[str, object] = {}   # 页面没准备好时积攒的推送：函数名 → 数据（只留最新）
        self.lock = threading.Lock()
        self.styled = False


class WindowManager:
    def __init__(self, server, api, log=print):
        self.server = server
        self.api = api
        self.log = log
        self.wins = {name: Win(name) for name in SPECS}
        self._create_lock = threading.Lock()
        self._focus_before: int = 0

    # ---------- 创建 ----------
    def create(self, name: str):
        import webview
        w = self.wins[name]
        with self._create_lock:
            if w.window is not None:
                return w
            title, width, height, frameless, focus, _eager = SPECS[name]
            kw = dict(title=title, url=self.server.page(name), js_api=self.api,
                      width=width, height=height, hidden=True, frameless=frameless,
                      on_top=True, background_color="#FFFFFF", text_select=True)
            if not focus:
                kw["focus"] = False
            if frameless:
                kw.update(resizable=False, easy_drag=False)
            else:
                kw.update(min_size=(480, 360))
            try:
                w.window = webview.create_window(**kw)
            except TypeError:  # 老版本界面库不认识某些参数
                kw.pop("focus", None)
                w.window = webview.create_window(**kw)
            # 事件回调写成不带参数的函数：带参数的话界面库会把窗口对象传进来
            w.window.events.closing += self._bind(self._on_closing, name)
            w.window.events.loaded += self._bind(self._grab_hwnd, name)
        return w

    @staticmethod
    def _bind(fn, name):
        def handler():
            return fn(name)
        return handler

    def create_eager(self) -> None:
        for name, spec in SPECS.items():
            if spec[5]:
                self.create(name)

    def _on_closing(self, name: str):
        threading.Thread(target=self.hide, args=(name,), daemon=True).start()
        return False  # 不让界面库真正销毁窗口

    def _grab_hwnd(self, name: str) -> None:
        w = self.wins[name]
        if w.hwnd:
            return
        hwnd = 0
        try:
            hwnd = int(w.window.native.Handle.ToInt64())
        except Exception:
            try:
                import ctypes
                hwnd = int(ctypes.windll.user32.FindWindowW(None, SPECS[name][0]) or 0)
            except Exception:
                hwnd = 0
        w.hwnd = hwnd
        if hwnd and not w.styled:
            w.styled = True
            if name in NATIVE:
                win32.make_tool_window(hwnd, no_activate=True)
                win32.exclude_from_capture(hwnd)
            win32.round_corners(hwnd)

    def kick(self, name: str) -> None:
        """隐藏状态下页面迟迟没加载（有的系统上网页控件要显示过一次才初始化）：
        在屏幕外不抢焦点地显示一下，加载完再收起。"""
        w = self.wins[name]
        if w.ready.is_set() or w.window is None:
            return
        self.log(f"[窗口] {name} 页面还没加载，在屏幕外显示一下")
        if not w.hwnd:
            self._grab_hwnd(name)
        if w.hwnd:
            win32.show_at(w.hwnd, -32000, -32000)
        else:
            try:
                w.window.show()
                w.window.move(-32000, -32000)
            except Exception as e:
                self.log(f"[窗口] {name} 显示失败 {e!r}")
        if w.ready.wait(10):
            self._grab_hwnd(name)
            if w.hwnd:
                win32.hide(w.hwnd)

    def ensure(self, name: str, timeout: float = 8.0) -> Win:
        """窗口建好、页面加载完（第一次打开某个窗口时要等它加载）。"""
        w = self.create(name)
        w.ready.wait(timeout)
        if not w.hwnd:
            self._grab_hwnd(name)
        return w

    # ---------- 页面通信 ----------
    def mark_ready(self, name: str) -> dict:
        """页面加载完调用：返回积攒的推送，页面按顺序执行。"""
        w = self.wins.get(name)
        if w is None:
            return {}
        with w.lock:
            pending = list(w.pending.items())
            w.pending.clear()
            w.ready.set()
        if not w.hwnd:
            threading.Thread(target=self._grab_hwnd, args=(name,), daemon=True).start()
        return {"calls": pending}

    def call(self, name: str, func: str, data=None):
        """调页面里的 window.WW[func](data)，返回页面的返回值；页面没准备好就先存着。"""
        w = self.wins[name]
        with w.lock:
            if not w.ready.is_set() or w.window is None:
                w.pending[func] = data
                return None
        try:
            return w.window.evaluate_js(
                f"window.WW && WW.{func} ? WW.{func}({json.dumps(data, ensure_ascii=False)}) : null")
        except Exception as e:
            self.log(f"[窗口] {name}.{func} 失败 {e!r}")
            return None

    def broadcast(self, func: str, data=None) -> None:
        for name, w in self.wins.items():
            if w.window is not None:
                self.call(name, func, data)

    # ---------- 显示 / 收起 ----------
    def visible(self, name: str) -> bool:
        return win32.is_visible(self.wins[name].hwnd)

    def rect(self, name: str):
        return win32.window_rect(self.wins[name].hwnd)

    def show_native(self, name: str, x: int, y: int, w: int, h: int) -> bool:
        win = self.wins[name]
        if not win.hwnd:
            self._grab_hwnd(name)
        if not win.hwnd:
            return False
        win32.show_at(win.hwnd, x, y, w, h)
        return True

    def show_window(self, name: str, center: bool = True) -> Win:
        """普通窗口：在鼠标所在的显示器上居中显示并切到前台。"""
        win = self.ensure(name)
        fg = win32.foreground()
        if fg and win32.window_pid(fg) != win32.os.getpid():
            self._focus_before = fg
        try:
            win.window.show()
            win.window.restore()
        except Exception as e:
            self.log(f"[窗口] 显示 {name} 失败 {e!r}")
        if center and win.hwnd:
            cx, cy = win32.cursor_pos()
            left, top, right, bottom = win32.work_area_at(cx, cy)
            r = win32.window_rect(win.hwnd)
            if r:
                ww, wh = r[2] - r[0], r[3] - r[1]
                x = left + max(0, (right - left - ww) // 2)
                y = top + max(0, int((bottom - top - wh) * 0.2))
                win32.show_at(win.hwnd, x, y, activate=True)
        if win.hwnd:
            win32.activate(win.hwnd)
        return win

    def hide(self, name: str) -> None:
        win = self.wins[name]
        if name in NATIVE:
            if win.hwnd:
                win32.hide(win.hwnd)
            return
        was_active = bool(win.hwnd) and win32.foreground() == win.hwnd
        try:
            if win.window is not None:
                win.window.hide()
        except Exception:
            if win.hwnd:
                win32.hide(win.hwnd)
        if was_active and self._focus_before:
            time.sleep(0.05)
            win32.set_foreground(self._focus_before)  # 焦点还给原来的程序
