# -*- coding: utf-8 -*-
"""开机自启（当前用户的 Run 注册表项）。"""
from __future__ import annotations

import sys
from pathlib import Path

from .paths import BASE, FROZEN

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
NAME = "暖词取词助手"


def command() -> str:
    """自启要执行的命令：用当前正在运行的解释器 / exe。"""
    if FROZEN:
        return f'"{sys.executable}"'
    exe = Path(sys.executable)
    pythonw = exe.with_name("pythonw.exe")
    if pythonw.exists():
        exe = pythonw
    return f'"{exe}" "{BASE / "main.py"}"'


def current() -> str | None:
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
            return winreg.QueryValueEx(k, NAME)[0]
    except OSError:
        return None


def enabled() -> bool:
    return current() is not None


def set_enabled(on: bool, log=print) -> bool:
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
            if on:
                winreg.SetValueEx(k, NAME, 0, winreg.REG_SZ, command())
            else:
                try:
                    winreg.DeleteValue(k, NAME)
                except OSError:
                    pass
        return True
    except Exception as e:
        log(f"[自启] 设置失败: {e!r}")
        return False


def sync(want: bool, log=print) -> None:
    """按配置开关；注册表里的路径过期了（换过目录）就改成当前路径。"""
    if want and current() != command():
        set_enabled(True, log)
    elif not want and enabled():
        set_enabled(False, log)
