# -*- coding: utf-8 -*-
"""只在 Windows 上跑：系统文字识别真的能认出屏幕上的词、取词服务能启动、高亮窗口能建。"""
import sys

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="只在 Windows 上跑")


def _text_image(text, size=22):
    from PIL import Image, ImageDraw, ImageFont
    font = None
    for fp in (r"C:\Windows\Fonts\segoeui.ttf", r"C:\Windows\Fonts\arial.ttf"):
        try:
            font = ImageFont.truetype(fp, size)
            break
        except OSError:
            continue
    img = Image.new("RGB", (560, 60), "white")
    d = ImageDraw.Draw(img)
    d.text((10, 14), text, font=font, fill="black")
    return img, d, font


def test_winocr_gives_word_boxes():
    from glance.capture.geometry import pick_word
    from glance.capture.winocr import WinOcr
    try:
        ocr = WinOcr()
    except RuntimeError as e:
        pytest.skip(f"这台机器没有文字识别语言：{e}")
    text = "They had to abandon the car and walk"
    img, d, font = _text_image(text)
    img = img.resize((img.width * 2, img.height * 2))
    words = [w.shifted(0, 0, 2) for w in ocr.words(img)]
    print("识别结果:", [(w.text, w.rect()) for w in words], "语言:", ocr.language)
    assert any(w.text.lower() == "abandon" for w in words)
    left = 10 + d.textlength("They had to ", font=font)
    width = d.textlength("abandon", font=font)
    hit = pick_word(words, left + width / 2, 30)
    assert hit is not None and hit.text.lower() == "abandon"


def test_capture_service_starts():
    from glance.capture.client import CaptureClient
    c = CaptureClient()
    try:
        print("取词服务状态:", c.status)
        assert c.status, "取词服务没有发出就绪消息"
        r = c.word_at(5, 5)
        assert isinstance(r, dict)
    finally:
        c.close()


def test_highlight_window():
    from glance.overlay import Highlight
    h = Highlight(log=print)
    assert h.hwnd
    h.show((100, 100, 160, 120))
    h.hide()


def test_hotkey_parse():
    from glance.hotkeys import MOD_ALT, MOD_CONTROL, parse_hotkey, valid_hotkey
    mods, vk = parse_hotkey("ctrl+alt+d")
    assert mods & MOD_CONTROL and mods & MOD_ALT and vk == 0x44
    assert valid_hotkey("ctrl+alt+f5") and not valid_hotkey("shift+d") and not valid_hotkey("ctrl+alt")
