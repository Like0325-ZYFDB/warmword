# -*- coding: utf-8 -*-
"""离线朗读：Windows 自带的语音（SAPI）。在线发音拿不到时兜底，例句、单词都能读。

SAPI 对象要在同一个线程里创建和使用，所以开一个专门的朗读线程，用队列收任务。
"""
from __future__ import annotations

import queue
import threading

SVSF_ASYNC = 1
SVSF_PURGE = 2  # 打断正在读的内容


class SapiSpeaker:
    def __init__(self, log=print):
        self._log = log
        self._q: queue.Queue = queue.Queue()
        self._thread: threading.Thread | None = None
        self.available: bool | None = None  # None = 还没试过

    def _ensure(self):
        if self._thread is None:
            self._thread = threading.Thread(target=self._run, daemon=True, name="sapi")
            self._thread.start()

    def speak(self, text: str, accent: str = "us") -> bool:
        if self.available is False or not (text or "").strip():
            return False
        self._ensure()
        done = queue.Queue()
        self._q.put((text.strip()[:600], accent, done))
        try:
            return done.get(timeout=5)
        except queue.Empty:
            return False

    def _pick_voice(self, sp, accent: str):
        """优先选对应口音的英语语音（409=美式，809=英式），没有就任意英语语音。"""
        want = "809" if accent == "uk" else "409"
        best = None
        try:
            voices = sp.GetVoices()
            for i in range(voices.Count):
                v = voices.Item(i)
                lang = (v.GetAttribute("Language") or "").lower()
                langs = [x.strip() for x in lang.split(";")]
                if want in langs:
                    return v
                if best is None and any(x in ("409", "809", "c09", "1009") for x in langs):
                    best = v
        except Exception as e:
            self._log(f"[朗读] 枚举语音失败 {e!r}")
        return best

    def _run(self):
        try:
            import comtypes
            from comtypes.client import CreateObject
            comtypes.CoInitialize()
            sp = CreateObject("SAPI.SpVoice")
            self.available = True
        except Exception as e:
            self._log(f"[朗读] 系统语音不可用 {e!r}")
            self.available = False
            while True:
                _t, _a, done = self._q.get()
                done.put(False)
        voices: dict[str, object] = {}
        while True:
            text, accent, done = self._q.get()
            try:
                if accent not in voices:
                    voices[accent] = self._pick_voice(sp, accent)
                if voices[accent] is None:
                    self._log("[朗读] 系统里没有英语语音（设置 → 时间和语言 → 语音 里可以添加）")
                    done.put(False)
                    continue
                sp.Voice = voices[accent]
                sp.Speak(text, SVSF_ASYNC | SVSF_PURGE)
                done.put(True)
            except Exception as e:
                self._log(f"[朗读] 失败 {e!r}")
                done.put(False)
