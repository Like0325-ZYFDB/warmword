# -*- coding: utf-8 -*-
"""剪贴板与模拟复制：读不到选区时，模拟一次 Ctrl+C 读出选中文字，读完把原剪贴板放回去。"""
from __future__ import annotations

import ctypes
import time

user32 = ctypes.windll.user32
CF_UNICODETEXT = 13


# ---------- 剪贴板 ----------
def _open_clipboard(tries: int = 5) -> bool:
    """剪贴板可能正被别的程序占着，稍等重试几次。"""
    for _ in range(tries):
        if user32.OpenClipboard(None):
            return True
        time.sleep(0.02)
    return False


def get_clipboard_text() -> str | None:
    kernel32 = ctypes.windll.kernel32
    kernel32.GlobalLock.restype = ctypes.c_void_p
    kernel32.GlobalLock.argtypes = [ctypes.c_void_p]
    kernel32.GlobalUnlock.argtypes = [ctypes.c_void_p]
    user32.GetClipboardData.restype = ctypes.c_void_p
    if not _open_clipboard():
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
    if not _open_clipboard():
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


VK_SHIFT, VK_CONTROL, VK_MENU, VK_LWIN, VK_RWIN, VK_C = 0x10, 0x11, 0x12, 0x5B, 0x5C, 0x43
VK_MASK = 0xE8          # 未分配的虚拟键：抬起 Alt/Win 前按一下，避免目标程序把它当成「打开菜单」
KEYEVENTF_KEYUP = 0x0002
MODIFIER_KEYS = (VK_SHIFT, VK_CONTROL, VK_MENU, VK_LWIN, VK_RWIN)


def _modifiers_down() -> list[int]:
    return [vk for vk in MODIFIER_KEYS if user32.GetAsyncKeyState(vk) & 0x8000]


def wait_modifiers_released(timeout: float = 0.8) -> bool:
    """等用户松开 Ctrl/Alt/Shift/Win（热键刚按下时手往往还没松）。"""
    end = time.time() + timeout
    while time.time() < end:
        if not _modifiers_down():
            return True
        time.sleep(0.02)
    return False


def copy_selection() -> None:
    """模拟 Ctrl+C（给剪贴板兜底读选区用）。

    一定要等热键里的 Ctrl+Alt 松开再发，否则目标程序收到的是 Ctrl+Alt+C，什么也复制不到。
    """
    if not wait_modifiers_released():
        held = _modifiers_down()
        if any(vk in held for vk in (VK_MENU, VK_LWIN, VK_RWIN)):
            user32.keybd_event(VK_MASK, 0, 0, 0)
            user32.keybd_event(VK_MASK, 0, KEYEVENTF_KEYUP, 0)
        for vk in held:
            user32.keybd_event(vk, 0, KEYEVENTF_KEYUP, 0)
    user32.keybd_event(VK_CONTROL, 0, 0, 0)
    user32.keybd_event(VK_C, 0, 0, 0)
    user32.keybd_event(VK_C, 0, KEYEVENTF_KEYUP, 0)
    user32.keybd_event(VK_CONTROL, 0, KEYEVENTF_KEYUP, 0)


def copy_selection_text(timeout: float = 0.6) -> str | None:
    """模拟复制读出选中的文字，读完把剪贴板里原来的文字放回去。

    用剪贴板序号判断「这次复制成功了没有」，选中内容和剪贴板原有内容相同也能识别。
    """
    user32.GetClipboardSequenceNumber.restype = ctypes.c_ulong
    old_text = get_clipboard_text()
    seq = user32.GetClipboardSequenceNumber()
    copy_selection()
    text = None
    end = time.time() + timeout
    while time.time() < end:
        time.sleep(0.03)
        if user32.GetClipboardSequenceNumber() != seq:
            time.sleep(0.03)  # 有的程序分几次写剪贴板，稍等再读
            text = get_clipboard_text()
            break
    if text is not None and old_text is not None and text != old_text:
        set_clipboard_text(old_text)
    text = (text or "").replace("\x00", "").strip()
    return text or None
