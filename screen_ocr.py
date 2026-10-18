# -*- coding: utf-8 -*-
"""屏幕文字识别取词：截取鼠标周围的小块屏幕，识别文字，定位鼠标下的词。

对浏览器、游戏、图片、PDF 等一切屏幕内容有效（不依赖应用是否暴露文字接口）。
"""
from __future__ import annotations

import re
import threading

import numpy as np
from PIL import ImageGrab

_ENGINE = None
_ENGINE_LOCK = threading.Lock()

WORD_CHARS = re.compile(r"[A-Za-z0-9'’\-]")
CJK = re.compile(r"[\u4e00-\u9fff]")
NON_SPACE = re.compile(r"[^\s]+")


def _engine():
    global _ENGINE
    with _ENGINE_LOCK:
        if _ENGINE is None:
            from rapidocr_onnxruntime import RapidOCR
            _ENGINE = RapidOCR()
    return _ENGINE


def char_weight(ch: str) -> float:
    """字符宽度权重：中文/全角约等于两个英文字符。"""
    if ch.isspace():
        return 0.7
    if CJK.match(ch):
        return 2.0
    if ch.isupper():
        return 1.15
    return 1.0


def _pick_word_in_line(text: str, xs: tuple[float, float], px: float) -> str | None:
    """在一行识别文字里定位鼠标下的词（按字符宽度加权，比纯比例准）。"""
    text = text.strip()
    if not text:
        return None
    x1, x2 = xs
    width = max(1.0, x2 - x1)
    rel = px - x1
    if rel < -6 or rel > width + 6:
        return None
    rel = max(0.0, min(width, rel))

    weights = [char_weight(ch) for ch in text]
    total = sum(weights) or 1.0
    acc = 0.0
    hit = len(text) - 1
    for i, w in enumerate(weights):
        cw = width * w / total
        if rel < acc + cw:
            hit = i
            break
        acc += cw

    # 中文直接返回该字
    if CJK.match(text[hit]):
        return text[hit]

    # 落在空格/标点上：往左右找最近的字母
    if not WORD_CHARS.match(text[hit]):
        idx = None
        for step in range(1, 6):
            for j in (hit - step, hit + step):
                if 0 <= j < len(text) and WORD_CHARS.match(text[j]):
                    idx = j
                    break
            if idx is not None:
                break
        if idx is None:
            return None
        hit = idx

    left = hit
    while left > 0 and WORD_CHARS.match(text[left - 1]):
        left -= 1
    right = hit
    while right < len(text) - 1 and WORD_CHARS.match(text[right + 1]):
        right += 1
    return text[left:right + 1].strip("'-’") or None


def _match_row(rows, mx: float, my: float, box_top: int, box_left: int):
    """找出鼠标所在的行；没有包含它的行时取纵向最近的一行。"""
    candidates = []
    for item in rows:
        pts, text, score = item[0], item[1], item[2]
        ys = [p[1] for p in pts]
        xs = [p[0] for p in pts]
        top, bottom = min(ys), max(ys)
        left, right = min(xs), max(xs)
        ry = my - box_top
        rx = mx - box_left
        if top - 4 <= ry <= bottom + 4:
            dy = 0.0
        else:
            dy = min(abs(ry - top), abs(ry - bottom))
        dx = 0.0 if left - 10 <= rx <= right + 10 else min(abs(rx - left), abs(rx - right))
        candidates.append((dy, dx, (left, right), text, float(score or 0.0)))
    if not candidates:
        return None
    candidates.sort(key=lambda c: (c[0], c[1], -c[4]))
    dy, dx, xs, text, score = candidates[0]
    if dy > 22 or dx > 260:  # 太远就不算这个位置有文字
        return None
    return text, xs


def word_at(x: int, y: int, span_left: int = 300, span_right: int = 300,
            span_y: int = 45) -> str | None:
    """识别鼠标坐标下的英文词或中文字。"""
    try:
        box = (max(0, x - span_left), max(0, y - span_y),
               x + span_right, y + span_y)
        img = ImageGrab.grab(bbox=box, all_screens=True)
        arr = np.array(img)[:, :, ::-1]  # RGB -> BGR
        result, _ = _engine()(arr)
    except Exception:
        return None
    if not result:
        return None
    row = _match_row(result, x, y, box[1], box[0])
    if not row:
        return None
    text, xs = row
    word = _pick_word_in_line(text, (min(xs), max(xs)), x - box[0])
    if word:
        return word
    # 行内没定位到（比如鼠标落在行尾外侧），退化用整行里最像词的部分
    m = re.search(r"[A-Za-z]{2,}", text)
    return m.group(0) if m else None


def sentence_at(x: int, y: int) -> str | None:
    """识别鼠标所在整行文字（给整句翻译用）。"""
    try:
        box = (max(0, x - 700), max(0, y - 30), x + 700, y + 30)
        img = ImageGrab.grab(bbox=box, all_screens=True)
        arr = np.array(img)[:, :, ::-1]
        result, _ = _engine()(arr)
    except Exception:
        return None
    if not result:
        return None
    row = _match_row(result, x, y, box[1], box[0])
    return row[0].strip() if row else None
