# -*- coding: utf-8 -*-
"""整句翻译：多个来源同时请求，按优先级取第一个成功的结果。

来源（优先级从高到低）：
  - 百度翻译开放平台（需在设置里填 APP ID / 密钥，国内稳定，标准版每月有免费额度）
  - 谷歌免费端点（无需密钥；国内网络通常连不上）
  - MyMemory 免费接口（无需密钥，每日额度，作兜底）

几个来源并发请求，最慢也只等一个来源的超时；连不上的来源会被记下，
之后 10 分钟内不再等它（仍会顺带请求，恢复了自动用回来）。

translate(text) -> str | None   中英自动识别方向
"""
from __future__ import annotations

import hashlib
import random
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import requests

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
    )
}
TIMEOUT = (3, 8)          # (连接, 读取) 秒：连不上的来源 3 秒就放弃
DOWN_SECONDS = 600        # 失败的来源多久内不再等待

CJK_RE = re.compile(r"[一-鿿]")

_SETTINGS = {"baidu_appid": "", "baidu_key": ""}
_DOWN: dict[str, float] = {}
_LAST_SOURCE = {"name": ""}
_POOL = ThreadPoolExecutor(max_workers=6, thread_name_prefix="translate")
_LOCK = threading.Lock()


def configure(baidu_appid: str = "", baidu_key: str = "") -> None:
    _SETTINGS["baidu_appid"] = (baidu_appid or "").strip()
    _SETTINGS["baidu_key"] = (baidu_key or "").strip()


def last_source() -> str:
    """最近一次翻译用的来源名（给界面显示）。"""
    return _LAST_SOURCE["name"]


def _is_chinese(text: str) -> bool:
    return bool(CJK_RE.search(text))


def _baidu(text: str) -> str | None:
    appid, key = _SETTINGS["baidu_appid"], _SETTINGS["baidu_key"]
    if not appid or not key:
        return None
    q = text[:2000]
    salt = str(random.randint(10000, 99999))
    sign = hashlib.md5(f"{appid}{q}{salt}{key}".encode("utf-8")).hexdigest()
    r = requests.post(
        "https://fanyi-api.baidu.com/api/trans/vip/translate",
        data={"q": q, "from": "auto", "to": "en" if _is_chinese(q) else "zh",
              "appid": appid, "salt": salt, "sign": sign},
        timeout=TIMEOUT,
    )
    data = r.json()
    if "trans_result" not in data:
        raise RuntimeError(f"百度翻译返回错误 {data.get('error_code')}: {data.get('error_msg')}")
    return "\n".join(seg.get("dst", "") for seg in data["trans_result"])


def _google(text: str) -> str | None:
    """谷歌免费端点（无需密钥）。"""
    src, dst = ("zh-CN", "en") if _is_chinese(text) else ("auto", "zh-CN")
    r = requests.get(
        "https://translate.googleapis.com/translate_a/single",
        params={"client": "gtx", "sl": src, "tl": dst, "dt": "t", "q": text[:4500]},
        headers=HEADERS,
        timeout=TIMEOUT,
    )
    if r.status_code != 200:
        return None
    data = r.json()
    return "".join(seg[0] for seg in data[0] if seg and seg[0])


def _chunks(text: str, size: int = 450, max_chunks: int = 6) -> list[str]:
    """按句子切成不超过 size 字符的小段（MyMemory 单次最多 500 字符）。"""
    out, cur = [], ""
    for sent in re.split(r"(?<=[.!?。！？;；])\s*", text):
        while len(sent) > size:
            out.append(sent[:size])
            sent = sent[size:]
        if len(cur) + len(sent) + 1 > size and cur:
            out.append(cur)
            cur = ""
        cur = (cur + " " + sent).strip() if cur else sent
    if cur:
        out.append(cur)
    return out[:max_chunks]


def _mymemory(text: str) -> str | None:
    """MyMemory 免费接口（每日额度，作备用）。"""
    pair = "zh-CN|en" if _is_chinese(text) else "en|zh-CN"
    parts = []
    for chunk in _chunks(text):
        r = requests.get(
            "https://api.mymemory.translated.net/get",
            params={"q": chunk, "langpair": pair},
            headers=HEADERS,
            timeout=TIMEOUT,
        )
        if r.status_code != 200:
            return None
        parts.append(r.json()["responseData"]["translatedText"])
    sep = "" if not _is_chinese(text) else " "
    return sep.join(parts)


SOURCES = [("百度翻译", _baidu), ("谷歌翻译", _google), ("MyMemory", _mymemory)]


def _run(name: str, fn, text: str) -> str | None:
    try:
        out = fn(text)
    except Exception:
        out = None
    out = (out or "").strip()
    with _LOCK:
        if out and out != text:
            _DOWN.pop(name, None)
            return out
        if name != "百度翻译" or _SETTINGS["baidu_appid"]:
            _DOWN[name] = time.time()
    return None


def translate(text: str, sources=None, deadline: float = 10.0) -> str | None:
    """整段翻译，自动识别中英方向；全部失败返回 None。"""
    text = (text or "").strip()
    if not text:
        return None
    sources = sources or SOURCES
    if not (_SETTINGS["baidu_appid"] and _SETTINGS["baidu_key"]):
        sources = [s for s in sources if s[0] != "百度翻译"]
    futures = [(name, _POOL.submit(_run, name, fn, text)) for name, fn in sources]
    end = time.time() + deadline
    now = time.time()
    with _LOCK:
        down = {n for n, t in _DOWN.items() if now - t < DOWN_SECONDS}
    # 第一轮：按优先级等「最近能用」的来源；第二轮：再看最近失败过的来源有没有恰好成功
    for wanted_down in (False, True):
        for name, fut in futures:
            if (name in down) != wanted_down:
                continue
            try:
                out = fut.result(timeout=max(0.05, end - time.time()))
            except Exception:
                out = None
            if out:
                _LAST_SOURCE["name"] = name
                return out
    _LAST_SOURCE["name"] = ""
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
        print(f"{s}\n  -> {out}  [{last_source()}] ({time.time()-t0:.1f}s)")
