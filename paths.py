# -*- coding: utf-8 -*-
"""目录约定：程序目录（只读资源）与用户数据目录（配置、生词本、缓存、日志）。

- 源码运行：程序目录 = 本文件所在目录
- 打包运行：程序目录 = exe 所在目录，界面资源在 PyInstaller 的解包目录
- 用户数据默认放 %APPDATA%\\WarmWord，程序目录放了 portable.txt 时改为放程序目录（便携版）
"""
from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

FROZEN = bool(getattr(sys, "frozen", False))
BASE = Path(sys.executable).resolve().parent if FROZEN else Path(__file__).resolve().parent
RES = Path(getattr(sys, "_MEIPASS", BASE))
PORTABLE = (BASE / "portable.txt").exists()
DATA = BASE if PORTABLE else Path(os.environ.get("APPDATA") or Path.home()) / "WarmWord"

# 旧版本放在程序目录里的用户数据，首次运行新版时复制到数据目录（原文件保留不动）
_OLD_FILES = ("config.json", "生词本.json")


def migrate_old_data(log=print) -> None:
    try:
        DATA.mkdir(parents=True, exist_ok=True)
    except Exception as e:
        log(f"[数据目录] 创建失败 {DATA}: {e!r}")
        return
    if DATA == BASE:
        return
    for name in _OLD_FILES:
        old, new = BASE / name, DATA / name
        if old.exists() and not new.exists():
            try:
                shutil.copy2(old, new)
                log(f"[数据目录] 已把旧的 {name} 复制到 {new}")
            except Exception as e:
                log(f"[数据目录] 复制 {name} 失败: {e!r}")
