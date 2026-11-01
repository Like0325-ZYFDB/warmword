# -*- coding: utf-8 -*-
"""系统文字接口（UI Automation）取词：Word、记事本、WPF / UWP 程序里能直接拿到准确的词和它的位置。

拿到的词一定要核对方框：有些程序 RangeFromPoint 会退回到文档开头，
方框不包含鼠标时当作取词失败，交给文字识别。
Chromium 系（Chrome / Edge / Electron）不走这里：查询会让浏览器开启整套无障碍树，拖慢浏览器。
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes
from ctypes.wintypes import POINT

from comtypes.client import CreateObject, GetModule

from .geometry import WordBox, clean_word

GetModule("UIAutomationCore.dll")
import comtypes.gen.UIAutomationClient as UIA  # noqa: E402

user32 = ctypes.windll.user32
WORD = 2       # TextUnit_Word

SKIP_CLASSES = ("Chrome_WidgetWin", "Chrome_RenderWidgetHostHWND", "MozillaWindowClass",
                "Intermediate D3D Window")


def window_class_at(x: int, y: int) -> str:
    try:
        user32.WindowFromPoint.restype = ctypes.c_void_p
        hwnd = user32.WindowFromPoint(POINT(x, y))
        buf = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(ctypes.c_void_p(hwnd), buf, 256)
        return buf.value
    except Exception:
        return ""


class TextCapture:
    """对屏幕任意位置读文字（每个线程各用一个实例）。"""

    def __init__(self):
        try:  # 非主线程用 COM 要先初始化；多线程模式，避免没有消息循环时跨进程调用卡死
            import comtypes
            comtypes.CoInitializeEx(comtypes.COINIT_MULTITHREADED)
        except Exception:
            pass
        self._uia = CreateObject(UIA.CUIAutomation, interface=UIA.IUIAutomation)

    def _range_at(self, x: int, y: int):
        elem = self._uia.ElementFromPoint(POINT(x, y))
        if elem is None:
            return None
        pattern = elem.GetCurrentPattern(UIA.UIA_TextPatternId)
        if pattern is None:
            return None
        tp = pattern.QueryInterface(UIA.IUIAutomationTextPattern)
        return tp.RangeFromPoint(POINT(x, y))

    def word_at(self, x: int, y: int) -> WordBox | None:
        cls = window_class_at(x, y)
        if cls.startswith(SKIP_CLASSES):
            return None
        try:
            rng = self._range_at(x, y)
            if rng is None:
                return None
            rng.ExpandToEnclosingUnit(WORD)
            word = clean_word((rng.GetText(64) or "").replace("\x00", ""))
            if not word:
                return None
            rects = rng.GetBoundingRectangles() or ()
        except Exception:
            return None
        # 每 4 个数一个方框：左、上、宽、高（屏幕物理像素）
        for i in range(0, len(rects) - 3, 4):
            left, top, w, h = rects[i:i + 4]
            box = WordBox(word, left, top, left + w, top + h)
            if w > 0 and h > 0 and box.contains(x, y, pad=2):
                return box
        return None

    def selection(self) -> str | None:
        """读当前程序里选中的文字（不碰剪贴板）。"""
        try:
            elem = self._uia.GetFocusedElement()
            if elem is None:
                return None
            pattern = elem.GetCurrentPattern(UIA.UIA_TextPatternId)
            if pattern is None:
                return None
            tp = pattern.QueryInterface(UIA.IUIAutomationTextPattern)
            sel = tp.GetSelection()
            if not sel or sel.Length == 0:
                return None
            text = (sel.GetElement(0).GetText(-1) or "").replace("\x00", "").strip()
            return text or None
        except Exception:
            return None
