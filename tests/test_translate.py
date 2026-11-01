# -*- coding: utf-8 -*-
"""翻译调度测试：用假来源，不联网。"""
import time

from warmword import translate as tr


def _slow_fail(text):
    time.sleep(0.6)
    raise TimeoutError


def _ok(text):
    return "好的结果"


def _setup():
    tr._DOWN.clear()
    tr.configure("", "")


def test_priority_and_down_memory():
    _setup()
    srcs = [("谷歌翻译", _slow_fail), ("MyMemory", _ok)]
    t0 = time.time()
    assert tr.translate("hello", srcs) == "好的结果"
    first = time.time() - t0
    assert first >= 0.5                       # 第一次要等高优先级的来源失败
    assert "谷歌翻译" in tr._DOWN
    t0 = time.time()
    assert tr.translate("hello", srcs) == "好的结果"
    assert time.time() - t0 < 0.3             # 之后不再等已知连不上的来源
    assert tr.last_source() == "MyMemory"


def test_all_fail_returns_none():
    _setup()
    assert tr.translate("hello", [("MyMemory", lambda t: t)]) is None   # 原样返回算失败


def test_baidu_skipped_without_key():
    _setup()
    called = []
    srcs = [("百度翻译", lambda t: called.append(1) or "x"), ("MyMemory", _ok)]
    assert tr.translate("hello", srcs) == "好的结果" and not called


def test_chunks():
    text = "This is one sentence. " * 60
    parts = tr._chunks(text)
    assert all(len(p) <= 450 for p in parts) and len(parts) >= 2
