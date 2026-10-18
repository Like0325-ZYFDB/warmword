# -*- coding: utf-8 -*-
"""取词层：鼠标下取词 / 取整句 / 读选中内容（界面文字接口 + 剪贴板兜底）。

不依赖任何界面库，只用 comtypes 调系统文字接口。
"""
from __future__ import annotations

import ctypes
import re
import time
from ctypes.wintypes import POINT

from comtypes.client import CreateObject, GetModule

GetModule("UIAutomationCore.dll")
import comtypes.gen.UIAutomationClient as UIA  # noqa: E402

user32 = ctypes.windll.user32
CF_UNICODETEXT = 13

WORD = 2       # UIA 文字单位：词（TextUnit_Word）
LINE = 3       # UIA 文字单位：行
PARAGRAPH = 4  # UIA 文字单位：段


class TextCapture:
    """对屏幕任意位置读文字（每个线程各用一个实例）。"""

    def __init__(self):
        # 非主线程使用 COM 前必须初始化；用多线程模式，避免没有消息循环时的跨进程调用卡死
        try:
            import comtypes
            comtypes.CoInitializeEx(comtypes.COINIT_MULTITHREADED)
        except Exception:
            pass
        self._uia = CreateObject(UIA.CUIAutomation, interface=UIA.IUIAutomation)

    def probe_at(self, x: int, y: int) -> dict:
        """逐步诊断：每一步的结果和异常都记下来。"""
        out = {}
        try:
            elem = self._uia.ElementFromPoint(POINT(x, y))
            out["element"] = repr(elem)
            if elem is None:
                out["error"] = "ElementFromPoint 返回空"
                return out
            out["name"] = elem.CurrentName
            out["type"] = elem.CurrentControlType
            try:
                pattern = elem.GetCurrentPattern(UIA.UIA_TextPatternId)
                out["pattern"] = repr(pattern)
                if pattern is not None:
                    tp = pattern.QueryInterface(UIA.IUIAutomationTextPattern)
                    rng = tp.RangeFromPoint(POINT(x, y))
                    out["range"] = repr(rng)
                    if rng is not None:
                        rng.ExpandToEnclosingUnit(WORD)
                        out["text"] = (rng.GetText(-1) or "")
            except Exception as e:
                out["pattern_error"] = repr(e)
        except Exception as e:
            out["error"] = repr(e)
        return out

    # ---------- 鼠标下取词 ----------
    def _range_at(self, x: int, y: int):
        try:
            elem = self._uia.ElementFromPoint(POINT(x, y))
            if elem is None:
                return None
            pattern = elem.GetCurrentPattern(UIA.UIA_TextPatternId)
            if pattern is None:
                return None
            tp = pattern.QueryInterface(UIA.IUIAutomationTextPattern)
            return tp.RangeFromPoint(POINT(x, y))
        except Exception:
            return None

    def text_at(self, x: int, y: int, unit: int = WORD) -> str | None:
        """取坐标处的文字：unit=词/句/段。失败返回 None。"""
        rng = self._range_at(x, y)
        if rng is None:
            return None
        try:
            rng.ExpandToEnclosingUnit(unit)
            text = (rng.GetText(-1) or "").replace("\x00", "").strip()
            return text or None
        except Exception:
            return None

    def word_at(self, x: int, y: int) -> str | None:
        return self.text_at(x, y, WORD)

    def sentence_at(self, x: int, y: int) -> str | None:
        """取坐标处整句：先取词，再看整段里按标点切出的那句。"""
        word = self.text_at(x, y, WORD)
        para = self.text_at(x, y, PARAGRAPH)
        if not para:
            return word
        para = para.replace("\n", " ").strip()
        if len(para) > 600:
            return word or self.text_at(x, y, LINE)
        # 按中英文句末标点切句，取包含当前词的那句
        parts = re.split(r"(?<=[。！？!?.;；])\s*", para)
        for part in parts:
            part = part.strip()
            if word and word in part and len(part) >= 2:
                return part
        return para or word

    # ---------- 选中内容 ----------
    def selection(self) -> str | None:
        """读当前程序里选中的文字（界面选区接口）。"""
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
            rng = sel.GetElement(0)
            text = (rng.GetText(-1) or "").replace("\x00", "").strip()
            return text or None
        except Exception:
            return None

    def selection_or_clipboard(self) -> str | None:
        """选区为空时，模拟一次复制并读剪贴板兜底。"""
        text = self.selection()
        if text:
            return text
        before = get_clipboard_text()
        copy_selection()
        time.sleep(0.15)
        after = get_clipboard_text()
        if after and after != before:
            return after.strip() or None
        return None


# ---------- 剪贴板 ----------
def get_clipboard_text() -> str | None:
    kernel32 = ctypes.windll.kernel32
    kernel32.GlobalLock.restype = ctypes.c_void_p
    kernel32.GlobalLock.argtypes = [ctypes.c_void_p]
    kernel32.GlobalUnlock.argtypes = [ctypes.c_void_p]
    user32.GetClipboardData.restype = ctypes.c_void_p
    if not user32.OpenClipboard(None):
        return None
    try:
        handle = user32.GetClipboardData(CF_UNICODETEXT)
        if not handle:
            return None
        ptr = kernel32.GlobalLock(handle)
        if not ptr:
            return None
        try:
            return ctypes.wstring_at(ptr)
        finally:
            kernel32.GlobalUnlock(handle)
    finally:
        user32.CloseClipboard()


def set_clipboard_text(text: str) -> bool:
    kernel32 = ctypes.windll.kernel32
    kernel32.GlobalAlloc.restype = ctypes.c_void_p
    kernel32.GlobalAlloc.argtypes = [ctypes.c_uint, ctypes.c_size_t]
    kernel32.GlobalLock.restype = ctypes.c_void_p
    kernel32.GlobalLock.argtypes = [ctypes.c_void_p]
    kernel32.GlobalUnlock.argtypes = [ctypes.c_void_p]
    kernel32.GlobalFree.argtypes = [ctypes.c_void_p]
    user32.SetClipboardData.argtypes = [ctypes.c_uint, ctypes.c_void_p]
    user32.SetClipboardData.restype = ctypes.c_void_p
    if not user32.OpenClipboard(None):
        return False
    try:
        user32.EmptyClipboard()
        data = (text + "\x00").encode("utf-16-le")
        handle = kernel32.GlobalAlloc(0x0042, len(data))  # GMEM_MOVEABLE|GMEM_ZEROINIT
        if not handle:
            return False
        ptr = kernel32.GlobalLock(handle)
        if not ptr:
            kernel32.GlobalFree(handle)
            return False
        ctypes.memmove(ptr, data, len(data))
        kernel32.GlobalUnlock(handle)
        if not user32.SetClipboardData(CF_UNICODETEXT, handle):
            kernel32.GlobalFree(handle)
            return False
        return True
    finally:
        user32.CloseClipboard()


def copy_selection() -> None:
    """模拟 Ctrl+C（给剪贴板兜底读选区用）。"""
    VK_CONTROL, VK_C = 0x11, 0x43
    user32.keybd_event(VK_CONTROL, 0, 0, 0)
    user32.keybd_event(VK_C, 0, 0, 0)
    user32.keybd_event(VK_C, 0, 2, 0)
    user32.keybd_event(VK_CONTROL, 0, 2, 0)


if __name__ == "__main__":
    import sys

    sys.stdout.reconfigure(encoding="utf-8")
    cap = TextCapture()
    pos = POINT(0, 0)
    user32.GetCursorPos(ctypes.byref(pos))
    print(f"鼠标位置 ({pos.x}, {pos.y}) 下的词:", cap.word_at(pos.x, pos.y))
    print("该处整句:", cap.sentence_at(pos.x, pos.y))
