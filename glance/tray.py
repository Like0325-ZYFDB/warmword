# -*- coding: utf-8 -*-
"""托盘图标与菜单。"""
from __future__ import annotations

import os
import threading

from . import autostart
from .config import hotkey_text

ACCENT = (37, 99, 235, 255)


def make_icon():
    """托盘图标：蓝底圆角方块上一个白色「瞥」字（16px 下也看得清）。"""
    from PIL import Image, ImageDraw, ImageFont
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([2, 2, 62, 62], radius=14, fill=ACCENT)
    for fp in (r"C:\Windows\Fonts\msyhbd.ttc", r"C:\Windows\Fonts\msyh.ttc", r"C:\Windows\Fonts\simhei.ttf"):
        if os.path.exists(fp):
            try:
                d.text((32, 31), "瞥", font=ImageFont.truetype(fp, 38), fill=(255, 255, 255, 255), anchor="mm")
                break
            except Exception:
                continue
    return img


def build_tray(app):
    import pystray
    from pystray import MenuItem as Item

    def hk(action):
        return hotkey_text(app.cfg["hotkeys"].get(action, ""))

    def label(text, action):
        return lambda item: f"{text}\t{hk(action)}"

    mode_names = {"modifier": lambda: f"按住 {app.cfg['capture_modifier'].capitalize()} 取词",
                  "hover": lambda: "鼠标停住取词", "off": lambda: "关闭"}

    def set_mode(mode):
        def run(icon, item):
            app.cfg["capture_mode"] = mode
            app.cfg.save()
            icon.update_menu()
        return run

    def on_autostart(icon, item):
        want = not autostart.enabled()
        autostart.set_enabled(want)
        app.cfg["autostart"] = want
        app.cfg.save()
        icon.update_menu()

    mode_menu = pystray.Menu(*[
        Item(lambda item, m=m: mode_names[m](), set_mode(m), radio=True,
             checked=lambda item, m=m: app.cfg.get("capture_mode") == m)
        for m in ("modifier", "hover", "off")])

    menu = pystray.Menu(
        Item(label("查词", "lookup"), lambda i, t: app.open_lookup(""), default=True),
        Item(label("翻译选中内容", "translate"),
             lambda i, t: threading.Thread(target=app.translate_selection, daemon=True).start()),
        Item("生词本", lambda i, t: app.open_vocab()),
        pystray.Menu.SEPARATOR,
        Item("鼠标取词", mode_menu),
        Item("设置…", lambda i, t: app.open_settings()),
        pystray.Menu.SEPARATOR,
        Item("开机自启", on_autostart, checked=lambda item: autostart.enabled()),
        Item("自检（生成报告）", lambda i, t: threading.Thread(target=app.self_check, daemon=True).start()),
        Item("重启", lambda i, t: app.restart()),
        Item(label("退出", "quit"), lambda i, t: app.exit()),
    )
    return pystray.Icon("glance", make_icon(), "一瞥 · 取词翻译", menu)
