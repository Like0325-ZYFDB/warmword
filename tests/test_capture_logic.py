# -*- coding: utf-8 -*-
"""取词的纯逻辑：选词、拆行、浮窗位置、取词节奏。"""
from warmword.capture.geometry import WordBox, clean_word, pick_word, place_card, split_line
from warmword.hover import HoverController, Inputs

LINE = [WordBox("They", 0, 0, 40, 20), WordBox("had", 46, 0, 76, 20),
        WordBox("to", 82, 0, 98, 20), WordBox("abandon", 104, 0, 180, 20),
        WordBox("the", 186, 0, 214, 20), WordBox("car,", 220, 0, 256, 20),
        WordBox("next", 0, 30, 40, 50)]


def test_pick_word_inside_and_gap():
    assert pick_word(LINE, 150, 10).text == "abandon"
    assert pick_word(LINE, 182, 10).text == "abandon"      # 词间空隙：取最近的
    assert pick_word(LINE, 20, 40).text == "next"
    assert pick_word(LINE, 400, 10) is None                 # 行尾外面很远
    assert pick_word(LINE, 150, 90) is None


def test_clean_word():
    assert clean_word("car,") == "car"
    assert clean_word("(hello)") == "hello"
    assert clean_word("don’t") == "don't"
    assert clean_word("well-known.") == "well-known"
    assert clean_word("“") == ""
    assert clean_word("词") == "词"


def test_split_line_finds_middle_word():
    words = split_line("They had to abandon the car", 0, 0, 270, 20)
    assert [w.text for w in words] == ["They", "had", "to", "abandon", "the", "car"]
    assert pick_word(words, 150, 10).text == "abandon"


def test_place_card_below_and_flip_above():
    work = (0, 0, 1920, 1040)
    assert place_card((500, 300, 580, 320), (400, 300), work) == (486, 326)
    x, y = place_card((500, 900, 580, 920), (400, 300), work)
    assert y == 900 - 6 - 300                                 # 下面放不下：放到上方
    x, _ = place_card((1800, 300, 1880, 320), (400, 300), work)
    assert x == 1920 - 400                                    # 右边贴边


def _tick(c, t, pos, **kw):
    return c.tick(Inputs(now=t, pos=pos, **kw))


def test_modifier_mode_fires_on_press_and_settles():
    c = HoverController(mode="modifier")
    assert _tick(c, 0.0, (100, 100)) == []
    assert _tick(c, 1.0, (100, 100)) == []                    # 没按取词键：悬停不取
    assert _tick(c, 1.1, (100, 100), modifier=True) == [("capture", 100, 100)]
    assert _tick(c, 1.2, (101, 100), modifier=True) == []     # 抖动不重复取
    assert _tick(c, 1.3, (200, 100), modifier=True) == []     # 刚移动，还没停稳
    assert _tick(c, 1.4, (200, 100), modifier=True) == [("capture", 200, 100)]


def test_hover_mode_waits_for_delay_once():
    c = HoverController(mode="hover", hover_delay=0.4)
    assert _tick(c, 0.0, (10, 10)) == []
    assert _tick(c, 0.3, (10, 10)) == []
    assert _tick(c, 0.5, (10, 10)) == [("capture", 10, 10)]
    assert _tick(c, 1.5, (10, 10)) == []                      # 同一处只取一次


def test_no_capture_while_dragging_or_on_current_word():
    c = HoverController(mode="hover", hover_delay=0.1)
    _tick(c, 0.0, (10, 10), lbutton=True)
    assert _tick(c, 0.5, (10, 10), lbutton=True) == []
    c.shown((0, 0, 50, 20), (0, 26, 300, 200))
    assert _tick(c, 1.0, (20, 10)) == []


def test_float_stays_while_moving_to_it_then_hides():
    c = HoverController(mode="modifier")
    c.shown((100, 100, 180, 120), (86, 126, 486, 400))
    assert _tick(c, 0.0, (140, 110)) == []
    assert _tick(c, 0.1, (140, 123)) == []                    # 词和浮窗之间
    assert _tick(c, 0.2, (300, 300)) == []                    # 浮窗上
    assert _tick(c, 1.0, (300, 300)) == []
    assert _tick(c, 1.1, (900, 900)) == []                    # 离开：先等一会
    assert _tick(c, 1.6, (900, 900)) == [("hide",)]


def test_pinned_float_only_closes_on_esc():
    c = HoverController(mode="modifier")
    c.shown((100, 100, 180, 120), (86, 126, 486, 400))
    c.pinned = True
    assert _tick(c, 0.0, (900, 900)) == []
    assert _tick(c, 5.0, (900, 900)) == []
    assert _tick(c, 5.1, (900, 900), esc=True) == [("hide",)]
