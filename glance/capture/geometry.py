# -*- coding: utf-8 -*-
"""取词的几何计算（纯函数，不依赖 Windows，可单独测试）。

- 文字识别给出每个词的方框 → pick_word 找鼠标下的那个
- 只有整行方框时（RapidOCR）→ split_line 按字符宽度估出每个词的方框
- place_card 决定浮窗放在词的下方还是上方
"""
from __future__ import annotations

import re
from dataclasses import dataclass

LETTER = re.compile(r"[A-Za-z]")
CJK = re.compile(r"[一-鿿]")
WORD_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9'’\-]*")


@dataclass
class WordBox:
    text: str
    left: float
    top: float
    right: float
    bottom: float

    def contains(self, x: float, y: float, pad: float = 0) -> bool:
        return (self.left - pad <= x <= self.right + pad and
                self.top - pad <= y <= self.bottom + pad)

    def shifted(self, dx: float, dy: float, scale: float = 1.0) -> "WordBox":
        return WordBox(self.text, self.left / scale + dx, self.top / scale + dy,
                       self.right / scale + dx, self.bottom / scale + dy)

    def rect(self) -> list[int]:
        return [round(self.left), round(self.top), round(self.right), round(self.bottom)]


def clean_word(text: str) -> str:
    """去掉两头的标点：'car,' → 'car'，'(hello)' → 'hello'，保留 don't / well-known。"""
    text = (text or "").strip().replace("’", "'")
    m = WORD_RE.search(text)
    if not m:
        return text if CJK.search(text) else ""
    return m.group(0).strip("'-")


def pick_word(words: list[WordBox], x: float, y: float) -> WordBox | None:
    """鼠标下的词：先找方框包含鼠标的；落在词间空隙时取同一行里左右 6 像素内最近的。"""
    best, best_d = None, None
    for w in words:
        if not clean_word(w.text):
            continue
        h = max(1.0, w.bottom - w.top)
        if not (w.top - h * 0.25 <= y <= w.bottom + h * 0.25):
            continue
        if w.left <= x <= w.right:
            return w
        d = min(abs(x - w.left), abs(x - w.right))
        if d <= max(6.0, h * 0.3) and (best_d is None or d < best_d):
            best, best_d = w, d
    return best


def char_weight(ch: str) -> float:
    """字符宽度权重：中文/全角约等于两个英文字符。"""
    if ch.isspace():
        return 0.7
    if CJK.match(ch):
        return 2.0
    if ch in "ilIj.,;:'!|":
        return 0.55
    if ch in "mwMW":
        return 1.5
    if ch.isupper():
        return 1.2
    return 1.0


def split_line(text: str, left: float, top: float, right: float, bottom: float) -> list[WordBox]:
    """把一整行的识别结果按字符宽度拆成逐词的方框（估算）。"""
    weights = [char_weight(ch) for ch in text]
    total = sum(weights) or 1.0
    width = max(1.0, right - left)
    xs = [left]
    for w in weights:
        xs.append(xs[-1] + width * w / total)
    out = []
    for m in re.finditer(r"[A-Za-z0-9'’\-]+|[一-鿿]", text):
        out.append(WordBox(m.group(0), xs[m.start()], top, xs[m.end()], bottom))
    return out


def place_card(word: tuple[int, int, int, int], size: tuple[int, int],
               work: tuple[int, int, int, int], gap: int = 6, inset: int = 14) -> tuple[int, int]:
    """浮窗位置：词的正下方、左边和词对齐（减去卡片内边距）；下面放不下就放到词上方。

    坐标都是物理像素；word=(左,上,右,下)，size=(宽,高)，work=显示器工作区。
    """
    wl, wt, _wr, wb = word
    w, h = size
    al, at, ar, ab = work
    x = wl - inset
    y = wb + gap
    if y + h > ab and wt - gap - h >= at:
        y = wt - gap - h
    elif y + h > ab:
        y = max(at, ab - h)
    x = max(al, min(x, ar - w))
    return int(x), int(y)
