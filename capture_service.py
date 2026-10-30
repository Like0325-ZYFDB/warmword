# -*- coding: utf-8 -*-
"""取词服务（独立小进程）：标准输入收坐标，返回鼠标下的词或选中内容。

主程序通过管道调用。之所以单独开进程：系统的文字接口只在进程主线程可用，
而主程序的主线程被界面库占着。每个请求带 id，回复原样带回，客户端据此丢弃迟到的旧回复。
"""
import ctypes
import json
import os
import sys
import threading


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


def _warmup(screen_ocr):
    """提前加载识别模型，第一次取词不用多等几秒。"""
    try:
        screen_ocr._engine()
        print("[warmup] ok", file=sys.stderr, flush=True)
    except Exception as e:
        print(f"[warmup] failed {e!r}", file=sys.stderr, flush=True)


def main():
    _ensure_stdio()
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)  # 与主程序一致：坐标用物理像素
    except Exception:
        pass
    from text_capture import TextCapture
    import screen_ocr

    warm = threading.Thread(target=_warmup, args=(screen_ocr,), daemon=True)
    warm.start()
    cap = TextCapture()
    print(json.dumps({"ok": True, "ready": True}), flush=True)
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
                word = None
                if req.get("mode") == "uia":
                    word = cap.word_at(int(req["x"]), int(req["y"]))
                if not word:
                    word = screen_ocr.word_at(int(req["x"]), int(req["y"]))
                out = {"word": word}
            elif op == "sentence":
                text = cap.sentence_at(int(req["x"]), int(req["y"]))
                if not text:
                    text = screen_ocr.sentence_at(int(req["x"]), int(req["y"]))
                out = {"text": text}
            elif op == "selection":
                out = {"text": cap.selection()}
            elif op == "probe":
                out = {"probe": cap.probe_at(int(req["x"]), int(req["y"]))}
            else:
                out = {"error": "unknown op"}
        except Exception as e:
            out = {"error": repr(e)}
        out["id"] = rid
        print(json.dumps(out, ensure_ascii=False), flush=True)
    warm.join(timeout=60)


if __name__ == "__main__":
    main()
