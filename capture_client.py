# -*- coding: utf-8 -*-
"""取词服务的客户端：负责启动子进程、收发请求、崩了自动重启。"""
from __future__ import annotations

import json
import queue
import subprocess
import sys
import threading
from pathlib import Path

BASE = Path(__file__).resolve().parent


class CaptureClient:
    def __init__(self):
        self._lock = threading.Lock()
        self._queue: queue.Queue = queue.Queue()
        self._proc: subprocess.Popen | None = None
        self._start()

    def _start(self):
        try:
            self._proc = subprocess.Popen(
                [sys.executable, str(BASE / "capture_service.py")],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL, text=True, encoding="utf-8",
                bufsize=1, cwd=str(BASE))
            threading.Thread(target=self._reader, daemon=True).start()
            try:  # 等就绪消息
                self._queue.get(timeout=8)
            except queue.Empty:
                pass
        except Exception:
            self._proc = None

    def _reader(self):
        proc = self._proc
        try:
            for line in proc.stdout:
                line = line.strip()
                if not line:
                    continue
                try:
                    data = json.loads(line)
                except Exception:
                    continue
                self._queue.put(data)
        except Exception:
            pass

    def _call(self, req: dict, timeout: float = 2.5):
        with self._lock:
            if self._proc is None or self._proc.poll() is not None:
                self._queue = queue.Queue()
                self._start()
            if self._proc is None:
                return None
            try:
                self._proc.stdin.write(json.dumps(req, ensure_ascii=False) + "\n")
                self._proc.stdin.flush()
            except Exception:
                self._proc = None
                return None
            try:
                return self._queue.get(timeout=timeout)
            except queue.Empty:
                return None

    def word_at(self, x: int, y: int) -> str | None:
        r = self._call({"op": "word", "x": int(x), "y": int(y)}, timeout=8.0)
        return (r or {}).get("word")

    def selection(self) -> str | None:
        r = self._call({"op": "selection"}, timeout=3.0)
        return (r or {}).get("text")

    def probe_at(self, x: int, y: int) -> dict:
        r = self._call({"op": "probe", "x": int(x), "y": int(y)}, timeout=3.0)
        return (r or {}).get("probe") or {"error": "no reply"}
