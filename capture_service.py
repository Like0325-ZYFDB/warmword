# -*- coding: utf-8 -*-
"""取词服务（独立小进程）：标准输入收坐标，返回鼠标下的词或选中内容。

主程序通过管道调用。之所以单独开进程：系统的文字接口只在进程主线程可用，
而主程序的主线程被界面库占着。
"""
import ctypes
import json
import sys

sys.stdout.reconfigure(encoding="utf-8")
sys.stdin.reconfigure(encoding="utf-8")

ctypes.windll.shcore.SetProcessDpiAwareness(2)  # 与主程序一致：坐标用物理像素

from text_capture import TextCapture  # noqa: E402
import screen_ocr  # noqa: E402


def main():
    cap = TextCapture()
    print(json.dumps({"ok": True, "ready": True}), flush=True)
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
            op = req.get("op", "word")
            if op == "word":
                word = None
                if req.get("mode") == "uia":
                    word = cap.word_at(int(req["x"]), int(req["y"]))
                if not word:
                    word = screen_ocr.word_at(int(req["x"]), int(req["y"]))
                print(json.dumps({"word": word}, ensure_ascii=False), flush=True)
            elif op == "sentence":
                text = cap.sentence_at(int(req["x"]), int(req["y"]))
                if not text:
                    text = screen_ocr.sentence_at(int(req["x"]), int(req["y"]))
                print(json.dumps({"text": text}, ensure_ascii=False), flush=True)
            elif op == "selection":
                text = cap.selection()
                print(json.dumps({"text": text}, ensure_ascii=False), flush=True)
            elif op == "probe":
                info = cap.probe_at(int(req["x"]), int(req["y"]))
                print(json.dumps({"probe": info}, ensure_ascii=False), flush=True)
            else:
                print(json.dumps({"error": "unknown op"}), flush=True)
        except Exception as e:
            print(json.dumps({"error": repr(e)}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
