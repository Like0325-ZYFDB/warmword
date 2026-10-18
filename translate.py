# -*- coding: utf-8 -*-
"""整句翻译：免费接口双源自动降级（谷歌免费端点 → MyMemory），无需密钥。

translate(text) -> str | None   中英自动识别方向
"""
from __future__ import annotations

import re
import time

import requests

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
    )
}

CJK_RE = re.compile(r"[\u4e00-\u9fff]")


def _is_chinese(text: str) -> bool:
    return bool(CJK_RE.search(text))


def _google(text: str) -> str | None:
    """谷歌免费端点（无需密钥）。"""
    src, dst = ("zh-CN", "en") if _is_chinese(text) else ("auto", "zh-CN")
    try:
        r = requests.get(
            "https://translate.googleapis.com/translate_a/single",
            params={"client": "gtx", "sl": src, "tl": dst, "dt": "t", "q": text[:4500]},
            headers=HEADERS,
            timeout=10,
        )
        if r.status_code != 200:
            return None
        data = r.json()
        return "".join(seg[0] for seg in data[0] if seg and seg[0])
    except Exception:
        return None


def _mymemory(text: str) -> str | None:
    """MyMemory 免费接口（每日额度，作备用）。"""
    pair = "zh-CN|en" if _is_chinese(text) else "en|zh-CN"
    try:
        r = requests.get(
            "https://api.mymemory.translated.net/get",
            params={"q": text[:4500], "langpair": pair},
            headers=HEADERS,
            timeout=10,
        )
        if r.status_code != 200:
            return None
        return r.json()["responseData"]["translatedText"]
    except Exception:
        return None


def translate(text: str) -> str | None:
    """整段翻译，自动识别中英方向；全部失败返回 None。"""
    text = (text or "").strip()
    if not text:
        return None
    for fn in (_google, _mymemory):
        out = fn(text)
        if out and out.strip() and out.strip() != text:
            return out.strip()
    return None


if __name__ == "__main__":
    import sys

    sys.stdout.reconfigure(encoding="utf-8")
    for s in [
        "The quick brown fox jumps over the lazy dog.",
        "Learning a language takes time and patience.",
        "今天天气很好，我们去公园散步吧。",
    ]:
        t0 = time.time()
        out = translate(s)
        print(f"{s}\n  -> {out}  ({time.time()-t0:.1f}s)")
