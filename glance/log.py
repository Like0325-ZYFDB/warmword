# -*- coding: utf-8 -*-
"""运行日志：控制台 + 数据目录下的 logs/运行日志.txt（超过 512KB 轮换一次）。"""
from __future__ import annotations

import threading
import time

from .paths import LOG_DIR

LOG_FILE = LOG_DIR / "运行日志.txt"
_LOCK = threading.Lock()


def log(msg: str) -> None:
    line = f"[{time.strftime('%m-%d %H:%M:%S')}] {msg}"
    try:
        print(line, flush=True)
    except Exception:
        pass
    with _LOCK:
        try:
            LOG_DIR.mkdir(parents=True, exist_ok=True)
            if LOG_FILE.exists() and LOG_FILE.stat().st_size > 512 * 1024:
                LOG_FILE.replace(LOG_FILE.with_suffix(".old.txt"))
            with open(LOG_FILE, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except Exception:
            pass
