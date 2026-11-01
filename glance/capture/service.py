# -*- coding: utf-8 -*-
"""取词服务（独立小进程）：标准输入收请求，标准输出回结果，一行一个 JSON。

取词分三级，前一级拿不到再用下一级：
  1. 系统文字接口（UI Automation）—— 程序支持时最准，还能拿到词的精确位置
  2. Windows 自带文字识别 —— 截鼠标周围一小块屏幕，逐词给方框
  3. RapidOCR —— 系统识别不可用时的兜底（第一次用到才加载模型）

单独开进程：文字接口偶尔会卡在某个程序上，卡住了主程序照样能用，客户端超时后重启服务即可。
请求：{"id": 1, "op": "word", "x": 100, "y": 200}
回复：{"id": 1, "word": "abandon", "box": [l, t, r, b], "via": "uia"}
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes
import json
import os
import sys
import threading
import time


def _ensure_stdio():
    """打包成窗口程序后 sys.stdin/stdout 可能是 None，从继承的管道句柄重新打开。"""
    import msvcrt
    k32 = ctypes.windll.kernel32
    k32.GetStdHandle.restype = ctypes.c_void_p
    for attr, num, mode in (("stdin", -10, "r"), ("stdout", -11, "w"), ("stderr", -12, "w")):
        stream = getattr(sys, attr)
        if stream is not None:
            stream.reconfigure(encoding="utf-8")
            continue
        try:
            fd = msvcrt.open_osfhandle(k32.GetStdHandle(num), os.O_RDONLY if mode == "r" else 0)
            setattr(sys, attr, open(fd, mode, encoding="utf-8", buffering=1 if mode == "w" else -1))
        except Exception:
            if attr == "stderr":
                sys.stderr = open(os.devnull, "w")


def _err(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def dpi_at(x: int, y: int) -> int:
    try:
        user32 = ctypes.windll.user32
        user32.MonitorFromPoint.restype = ctypes.c_void_p
        hmon = user32.MonitorFromPoint(ctypes.wintypes.POINT(x, y), 2)
        dx, dy = ctypes.c_uint(), ctypes.c_uint()
        ctypes.windll.shcore.GetDpiForMonitor(ctypes.c_void_p(hmon), 0, ctypes.byref(dx), ctypes.byref(dy))
        return int(dx.value) or 96
    except Exception:
        return 96


class Capturer:
    def __init__(self):
        from .geometry import clean_word, pick_word
        self._pick, self._clean = pick_word, clean_word
        self.uia = None
        self.ocr = None
        self.notes: list[str] = []
        try:
            from .uia import TextCapture
            self.uia = TextCapture()
        except Exception as e:
            self.notes.append(f"系统文字接口不可用：{e!r}")
        try:
            from .winocr import WinOcr
            self.ocr = WinOcr()
        except Exception as e:
            self.notes.append(f"系统文字识别不可用：{e!r}")
        self._rapid_lock = threading.Lock()
        if self.ocr is None:
            threading.Thread(target=self._load_rapid, daemon=True).start()

    def _load_rapid(self):
        with self._rapid_lock:
            if self.ocr is not None:
                return
            try:
                from .rapid import RapidOcr
                self.ocr = RapidOcr()
                _err("[warmup] rapidocr ok")
            except Exception as e:
                self.notes.append(f"RapidOCR 不可用：{e!r}")

    def status(self) -> dict:
        return {"uia": self.uia is not None,
                "ocr": getattr(self.ocr, "name", ""),
                "ocr_language": getattr(self.ocr, "language", ""),
                "notes": self.notes}

    def word_at(self, x: int, y: int) -> dict:
        t0 = time.perf_counter()
        if self.uia is not None:
            box = self.uia.word_at(x, y)
            if box is not None:
                return {"word": box.text, "box": box.rect(), "via": "uia",
                        "ms": int((time.perf_counter() - t0) * 1000)}
        if self.ocr is None:
            with self._rapid_lock:  # 兜底模型还在加载：等它
                pass
        if self.ocr is None:
            return {"word": None, "error": "没有可用的文字识别"}
        from PIL import Image, ImageGrab
        scale = dpi_at(x, y) / 96.0
        sx, sy = int(240 * scale), int(30 * scale)
        left, top = x - sx, y - sy
        # 副屏在主屏左边/上边时坐标本来就是负的：按虚拟桌面截图，不要夹到 0
        img = ImageGrab.grab(bbox=(left, top, x + sx, y + sy), all_screens=True)
        up = 2 if scale < 1.5 else 1  # 字小的屏幕放大一倍再识别，准确率明显更高
        if up > 1:
            img = img.resize((img.width * up, img.height * up), Image.LANCZOS)
        words = [w.shifted(left, top, up) for w in self.ocr.words(img)]
        hit = self._pick(words, x, y)
        ms = int((time.perf_counter() - t0) * 1000)
        if hit is None:
            return {"word": None, "via": "ocr", "ms": ms}
        return {"word": self._clean(hit.text), "box": hit.rect(), "via": "ocr", "ms": ms}

    def selection(self) -> str | None:
        return self.uia.selection() if self.uia is not None else None


def main():
    _ensure_stdio()
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)  # 与主程序一致：坐标用物理像素
    except Exception:
        pass
    cap = Capturer()
    _err(f"[warmup] {cap.status()}")
    print(json.dumps({"ready": True, "status": cap.status()}, ensure_ascii=False), flush=True)
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        rid = None
        try:
            req = json.loads(line)
            rid = req.get("id")
            op = req.get("op", "word")
            if op == "word":
                out = cap.word_at(int(req["x"]), int(req["y"]))
            elif op == "selection":
                out = {"text": cap.selection()}
            elif op == "status":
                out = {"status": cap.status()}
            else:
                out = {"error": "unknown op"}
        except Exception as e:
            out = {"error": repr(e)}
        out["id"] = rid
        print(json.dumps(out, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
