# -*- coding: utf-8 -*-
"""发音：单词和例句的在线真人/合成发音（有道，本地缓存），拿不到时用 Windows 自带语音。

音频由页面自己播放（本机资源服务提供 mp3），不再经过系统 MCI。
"""
from __future__ import annotations

import hashlib
import re
from pathlib import Path

import requests

HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")
}


def normalize(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "")).strip()[:300]


def _trim(folder: Path, keep: int = 400) -> None:
    try:
        files = sorted(folder.glob("*.mp3"), key=lambda p: p.stat().st_mtime)
        for old in files[:-keep]:
            old.unlink()
    except Exception:
        pass


def online_voice(text: str, accent: str, cache_dir: Path, log=print) -> bytes | None:
    """有道发音（英=1 / 美=2；单词和句子都行）。失败返回 None。"""
    text = normalize(text)
    if not text:
        return None
    voice_type = "1" if accent == "uk" else "2"
    path = Path(cache_dir) / (hashlib.md5(f"{text}|{voice_type}".encode("utf-8")).hexdigest() + ".mp3")
    if path.exists():
        return path.read_bytes()
    try:
        r = requests.get("https://dict.youdao.com/dictvoice",
                         params={"audio": text, "type": voice_type},
                         headers=HEADERS, timeout=(3, 8))
        if r.status_code != 200 or len(r.content) < 800:
            log(f"[发音] 没拿到音频 {text[:30]} code={r.status_code} size={len(r.content)}")
            return None
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(r.content)
        _trim(path.parent)
        return r.content
    except Exception as e:
        log(f"[发音] 下载失败 {text[:30]}: {e!r}")
        return None
