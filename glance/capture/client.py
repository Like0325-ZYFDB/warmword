# -*- coding: utf-8 -*-
"""取词服务的客户端：负责启动子进程、收发请求、崩了或卡住自动重启。"""
from __future__ import annotations

import json
import queue
import subprocess
import sys
import threading
import time

from ..paths import BASE, FROZEN

CREATE_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0


class CaptureClient:
    def __init__(self):
        self._lock = threading.Lock()
        self._queue: queue.Queue = queue.Queue()
        self._proc: subprocess.Popen | None = None
        self._seq = 0
        self._timeouts = 0
        self.status: dict = {}
        self._start()

    def _command(self) -> list[str]:
        if FROZEN:  # 打包后没有 .py 文件：同一个 exe 带参数启动取词服务
            return [sys.executable, "--capture-service"]
        return [sys.executable, "-m", "glance.capture.service"]

    def _start(self):
        try:
            self._proc = subprocess.Popen(
                self._command(),
                stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL, text=True, encoding="utf-8",
                bufsize=1, cwd=str(BASE), creationflags=CREATE_NO_WINDOW)
            threading.Thread(target=self._reader, args=(self._proc, self._queue),
                             daemon=True).start()
            try:  # 等就绪消息（带着各级取词是否可用）
                ready = self._queue.get(timeout=15)
                self.status = ready.get("status") or {}
            except queue.Empty:
                pass
        except Exception:
            self._proc = None

    @staticmethod
    def _reader(proc, q):
        try:
            for line in proc.stdout:
                line = line.strip()
                if not line:
                    continue
                try:
                    data = json.loads(line)
                except Exception:
                    continue
                q.put(data)
        except Exception:
            pass

    def _restart(self):
        try:
            if self._proc is not None:
                self._proc.kill()
        except Exception:
            pass
        self._proc = None
        self._queue = queue.Queue()
        self._start()

    def close(self):
        try:
            if self._proc is not None:
                self._proc.kill()
        except Exception:
            pass

    def _call(self, req: dict, timeout: float = 2.5):
        with self._lock:
            if self._proc is None or self._proc.poll() is not None:
                self._restart()
            if self._proc is None:
                return None
            self._seq += 1
            rid = self._seq
            try:
                self._proc.stdin.write(json.dumps(dict(req, id=rid), ensure_ascii=False) + "\n")
                self._proc.stdin.flush()
            except Exception:
                self._proc = None
                return None
            end = time.monotonic() + timeout
            while True:
                remaining = end - time.monotonic()
                try:
                    data = self._queue.get(timeout=max(0.01, remaining))
                except queue.Empty:
                    data = None
                if data is not None and data.get("id") == rid:
                    self._timeouts = 0
                    return data
                # 不是这次请求的回复：上一次超时请求迟到的结果，丢掉（以前会被当成这次的结果）
                if data is None or remaining <= 0:
                    self._timeouts += 1
                    if self._timeouts >= 2:  # 连续超时：服务多半卡住了，重启
                        self._timeouts = 0
                        self._restart()
                    return None

    def word_at(self, x: int, y: int) -> dict:
        """{"word": ..., "box": [l, t, r, b], "via": "uia" / "ocr", "ms": 耗时}；失败返回 {}。"""
        return self._call({"op": "word", "x": int(x), "y": int(y)}, timeout=3.0) or {}

    def selection(self) -> str | None:
        r = self._call({"op": "selection"}, timeout=2.0)
        return (r or {}).get("text")

    def refresh_status(self) -> dict:
        r = self._call({"op": "status"}, timeout=3.0)
        if r and r.get("status"):
            self.status = r["status"]
        return self.status
