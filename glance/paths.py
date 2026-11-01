# -*- coding: utf-8 -*-
"""目录约定：程序目录（只读资源）与用户数据目录（配置、生词本、缓存、日志）。

- 源码运行：程序目录 = 仓库根目录（main.py 所在处）
- 打包运行：程序目录 = exe 所在目录，界面资源在 PyInstaller 的解包目录
- 用户数据默认放 %APPDATA%\\Glance，程序目录放了 portable.txt 时改为放程序目录（便携版）
"""
from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path

FROZEN = bool(getattr(sys, "frozen", False))
BASE = Path(sys.executable).resolve().parent if FROZEN else Path(__file__).resolve().parent.parent
RES = Path(getattr(sys, "_MEIPASS", BASE))
UI_DIR = RES / "ui"
PORTABLE = (BASE / "portable.txt").exists()
APPDATA = Path(os.environ.get("APPDATA") or Path.home())
DATA = BASE if PORTABLE else APPDATA / "Glance"
OLD_DATA = APPDATA / "WarmWord"          # 改名前（暖词）的数据目录

CONFIG_PATH = DATA / "config.json"
VOCAB_PATH = DATA / "生词本.json"
HISTORY_PATH = DATA / "查词历史.json"
DICT_DIR = DATA / "词库"                 # 下载的 ECDICT 放这里
INDEX_DIR = DATA / "cache" / "index"     # ECDICT csv 转出来的 sqlite 索引
VOICE_DIR = DATA / "cache" / "voice"     # 在线发音缓存
EXPORT_DIR = DATA / "导出"
LOG_DIR = DATA / "logs"

# 旧版本放在程序目录里的用户数据，首次运行新版时复制到数据目录（原文件保留不动）
_OLD_FILES = ("config.json", "生词本.json")


def migrate_renamed_data(old: Path, new: Path, log=print) -> bool:
    """改名前的数据目录整个复制过来（原目录保留不动），配置里指向旧目录的词典路径改成新目录。"""
    if not old.is_dir() or new.exists():
        return False
    try:
        shutil.copytree(old, new)
    except Exception as e:
        log(f"[数据目录] 从 {old} 复制失败: {e!r}")
        return False
    cfg = new / "config.json"
    try:
        data = json.loads(cfg.read_text(encoding="utf-8"))
        for d in data.get("dictionaries", []):
            path = str(d.get("path", ""))
            if path.lower().startswith(str(old).lower()):
                d["path"] = str(new) + path[len(str(old)):]
        cfg.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass
    log(f"[数据目录] 已把 {old} 复制到 {new}")
    return True


def migrate_old_data(log=print) -> None:
    if not PORTABLE:
        migrate_renamed_data(OLD_DATA, DATA, log)
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
