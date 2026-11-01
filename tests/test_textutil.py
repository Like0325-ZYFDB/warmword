# -*- coding: utf-8 -*-
"""清晰排版：编号并进释义、中文释义标出来、例句单独成行、习语做小标题。"""
from pathlib import Path

from glance.textutil import brief_text, clear_html

SAMPLE = (Path(__file__).parent / "data" / "oxford_like.html").read_text(encoding="utf-8")


def test_clear_html_structure():
    out = clear_html(SAMPLE, "abandon")
    lines = out.split("</div>")
    assert lines[0].startswith('<div class="ln meta">abandon verb')
    assert lines[1].startswith('<div class="ln def"><span class="num">1</span> ~ sb to leave sb')
    assert '<span class="cn">抛弃，遗弃（尤指不负责任）</span>' in lines[1]
    assert lines[2].startswith('<div class="ln ex">The baby had been abandoned')
    assert '<span class="cn">（不得已而）舍弃，丢弃，离开</span>' in out
    assert '<div class="ln sub">abandon ship</div>' in out
    assert "<span class=\"ex\">" not in out


def test_clear_html_generic_dictionary_without_classes():
    html = "<b>hello</b><br>1<br>int. 你好<br>Hello, how are you today? 你好，今天怎么样？"
    out = clear_html(html, "hello")
    assert '<span class="num">1</span>' in out and '<div class="ln ex">Hello, how are you today?' in out


def test_brief_text_unaffected_by_marks():
    assert "" not in brief_text(SAMPLE, "abandon")
