# -*- coding: utf-8 -*-
"""暖词主程序：把取词、词典、窗口、热键、托盘串起来。

线程分工：
  界面线程（主线程）  pywebview 的消息循环
  鼠标线程           每 30 毫秒看一次鼠标和取词键，交给 HoverController 决定取词 / 收起
  取词线程           一次只跑一个取词请求，新的请求覆盖还没开始的旧请求
  热键线程 / 托盘线程 / 词典加载线程
取词结果直接推给浮窗（不再让页面轮询），浮窗可以真正隐藏。
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
import threading
import time
from pathlib import Path

import requests

from . import autostart, translate, win32
from .capture.clipboard import copy_selection_text, set_clipboard_text
from .capture.geometry import place_card
from .config import HOTKEY_LABELS, Config, apply_settings, hotkey_text
from .dictionary import DictEngine, build_card, enrich_from_mdx
from .dictionary.resources import ref_path, rewrite_html
from .hotkeys import HotkeyThread, valid_hotkey
from .hover import HoverController, Inputs
from .log import LOG_FILE, log
from .paths import (BASE, DATA, DICT_DIR, EXPORT_DIR, FROZEN, HISTORY_PATH, INDEX_DIR, PORTABLE,
                    UI_DIR, VOCAB_PATH, VOICE_DIR, migrate_old_data)
from .server import ResourceServer
from .store import History, Vocab
from .textutil import clean_html
from .windows import WindowManager
from .wordlist import pick_new_words

ECDICT_URL = "https://raw.githubusercontent.com/skywind3000/ECDICT/master/ecdict.csv"
FLOAT_WIDTH = 400          # 浮窗宽度（页面像素）
TICK = 0.03                # 鼠标线程间隔
ENGLISH = re.compile(r"[A-Za-z]")


def card_brief(card: dict, limit: int = 80) -> str:
    """一行简要释义：v. 放弃；n. 放任"""
    parts = [f"{s['pos']} {s['text']}".strip() for s in card.get("senses", [])[:3]]
    return "；".join(parts)[:limit]


class App:
    def __init__(self):
        self.cfg = Config(log=log)
        self.vocab = Vocab(VOCAB_PATH, log)
        self.history = History(HISTORY_PATH, log)
        self.engine: DictEngine | None = None
        self.engine_ready = threading.Event()
        self.capture = None
        self.server = ResourceServer(UI_DIR, VOICE_DIR, lambda: self.engine, log)
        from .api import Api
        self.api = Api(self)
        self.wm = WindowManager(self.server, self.api, log)
        self.hover = HoverController(mode=self.cfg["capture_mode"],
                                     hover_delay=self.cfg["hover_delay_ms"] / 1000)
        self.highlight = None
        self.hotkeys: HotkeyThread | None = None
        self.tray = None
        self.quit = False
        self.ecdict_state = {"running": False, "pct": 0, "msg": ""}
        # 浮窗
        self._float_lock = threading.Lock()
        self.float_word = ""
        self.float_box: tuple | None = None
        # 取词请求：最新的覆盖旧的
        self._cap_lock = threading.Lock()
        self._cap_want: tuple | None = None
        self._cap_busy = False
        self._cap_seq = 0
        # 划词按钮
        self._drag_start: tuple | None = None
        self._last_click = (0.0, (0, 0))
        self._sel_shown_at = 0.0
        self._sel_pos = (0, 0)
        self._sel_owner = 0
        self._notes: dict[str, float] = {}
        from .sapi import SapiSpeaker
        self.sapi = SapiSpeaker(log)

    # ================= 启动 =================
    def run(self) -> None:
        import webview
        log("=" * 40)
        log(f"[启动] 暖词 2.0（数据目录 {DATA}{'，便携模式' if PORTABLE else ''}）")
        translate.configure(self.cfg["baidu_appid"], self.cfg["baidu_key"])
        threading.Thread(target=self._load_engine, daemon=True, name="engine").start()
        threading.Thread(target=self._start_capture, daemon=True, name="capture-start").start()
        self.hotkeys = HotkeyThread(lambda: dict(self.cfg["hotkeys"]), self.on_hotkey, log)
        threading.Thread(target=self._mouse_loop, daemon=True, name="mouse").start()
        threading.Thread(target=self._tray, daemon=True, name="tray").start()
        threading.Thread(target=win32.wait_event_loop, daemon=True,
                         args=(win32.QUIT_EVENT, lambda: os._exit(0), lambda: self.quit)).start()
        threading.Thread(target=win32.wait_event_loop, daemon=True,
                         args=(win32.SHOW_EVENT, lambda: self.open_lookup(""), lambda: self.quit)).start()
        threading.Thread(target=self._after_start, daemon=True).start()
        autostart.sync(self.cfg["autostart"], log)
        try:
            from .overlay import Highlight
            self.highlight = Highlight(log=log)
        except Exception as e:
            log(f"[高亮] 不可用 {e!r}")
        self.wm.create_eager()
        webview.start(debug=bool(self.cfg.get("debug")))
        while not self.quit:  # 界面线程退出后仍保持托盘运行，直到从托盘退出
            time.sleep(0.5)

    def _after_start(self) -> None:
        time.sleep(2.5)
        self.wm.kick("float")
        self.hotkeys.registered.wait(5)
        bad = [f"{HOTKEY_LABELS.get(a, a)}（{hotkey_text(self.cfg['hotkeys'].get(a, ''))}）"
               for a, ok in self.hotkeys.status.items() if not ok]
        if bad:
            self.notify("这些快捷键被别的软件占用了：" + "、".join(bad) + "。可以在设置里换一个")
        self.engine_ready.wait(60)
        if self.engine is not None and not self.engine.books:
            self.notify("还没有词典：在设置里一键下载 ECDICT，或添加自己的 .mdx 词典")
            self.open_settings()
        else:  # 查词窗提前建好，第一次按快捷键不用等
            self.wm.create("lookup")

    def _start_capture(self) -> None:
        from .capture.client import CaptureClient
        t0 = time.time()
        self.capture = CaptureClient()
        log(f"[取词] 服务就绪（{time.time() - t0:.1f} 秒）：{self.capture.status}")
        if self.capture.status and not self.capture.status.get("ocr"):
            self.notify("文字识别不可用，取词只能用在支持系统文字接口的程序里。详情见托盘「自检」")

    def _load_engine(self) -> None:
        t0 = time.time()
        books = [(d["path"], d.get("name")) for d in self.cfg.get("dictionaries", [])]
        try:
            engine = DictEngine(books, cache_dir=INDEX_DIR)
        except Exception as e:
            log(f"[词典] 加载失败 {e!r}")
            engine = DictEngine([])
        self.engine = engine
        self.engine_ready.set()
        names = "、".join(f"{b.name}（{len(b):,} 条）" for b in engine.books)
        log(f"[词典] 就绪（{time.time() - t0:.1f} 秒）：{names or '没有可用的词典'}")
        if engine.errors:
            for name, why in engine.errors:
                log(f"[词典] 加载失败 {name}: {why}")
            self.notify("有词典没加载上：" + "、".join(n for n, _ in engine.errors) + "。在设置里检查路径")
        for b in engine.mdx_books:  # 后台读好 mdd 索引，第一次显示原版排版不用等
            try:
                res = b.resources()
                for err in res.errors:
                    log(f"[资源] {b.name}: {err}")
            except Exception as e:
                log(f"[资源] {b.name} 加载失败 {e!r}")
        self.wm.call("settings", "render", self.settings_payload())

    def reload_engine(self) -> None:
        self.engine_ready.clear()
        threading.Thread(target=self._load_engine, daemon=True).start()

    # ================= 提示 =================
    def notify(self, msg: str, interval: float = 6.0) -> None:
        """托盘气泡（同一条 interval 秒内只弹一次）。操作没成功一定要让用户看得到。"""
        log(f"[提示] {msg}")
        now = time.time()
        if now - self._notes.get(msg, 0) < interval:
            return
        self._notes[msg] = now
        try:
            if self.tray is not None and getattr(self.tray, "HAS_NOTIFICATION", False):
                self.tray.notify(msg, "暖词")
        except Exception as e:
            log(f"[提示] 气泡失败 {e!r}")

    def prefs(self) -> dict:
        return {"font_scale": float(self.cfg.get("font_scale", 1.0) or 1.0),
                "accent": self.cfg.get("accent", "us"), "theme": self.cfg.get("theme", "system"),
                "hotkeys": {a: hotkey_text(s) for a, s in self.cfg["hotkeys"].items()},
                "modifier": self.cfg.get("capture_modifier", "shift").capitalize(),
                "capture_mode": self.cfg.get("capture_mode")}

    # ================= 查词 =================
    def entries_payload(self, entries) -> list[dict]:
        """mdx 词条 → 页面要的原版排版（资源走本机服务地址）。"""
        out = []
        for e in entries:
            book = self.engine.book(e.source) if self.engine else None
            bid = book.id if book else ""
            html, css = rewrite_html(e.html, lambda ref, b=bid: self.server.res_url(b, ref_path(ref)))
            out.append({"id": bid, "name": e.dict_name, "head": e.headword, "html": html, "css": css})
        return out

    def search(self, word: str, record: bool = False, mdx_limit: int = 8) -> dict:
        word = (word or "").strip()
        out = {"query": word, "card": None, "entries": [], "did_you_mean": [], "status": ""}
        if not word:
            return out
        if not self.engine_ready.wait(0.05) or self.engine is None:
            out["status"] = "词典还在加载，请稍等几秒"
            return out
        if not self.engine.books:
            out["status"] = "还没有词典：在设置里一键下载 ECDICT，或添加 .mdx 词典"
            return out
        card = build_card(self.engine, word)
        entries = self.engine.lookup_mdx(card["word"] if card["found"] else word)[:mdx_limit]
        card = enrich_from_mdx(card, entries)
        out["card"] = card
        out["entries"] = self.entries_payload(entries)
        out["saved"] = self.vocab.has(card["word"])
        if card["found"]:
            if record:
                self.history.add(card["word"])
        elif ENGLISH.search(word):
            out["did_you_mean"] = self.engine.fuzzy(word, limit=6)  # dictionery → dictionary
        return out

    # ================= 取词 =================
    def request_capture(self, x: int, y: int, manual: bool = False) -> None:
        with self._cap_lock:
            self._cap_seq += 1
            self._cap_want = (x, y, manual, self._cap_seq)
            if self._cap_busy:
                return
            self._cap_busy = True
        threading.Thread(target=self._capture_worker, daemon=True, name="capture").start()

    def _capture_worker(self) -> None:
        while True:
            with self._cap_lock:
                want = self._cap_want
                self._cap_want = None
                if want is None:
                    self._cap_busy = False
                    return
            try:
                self._capture_once(*want)
            except Exception as e:
                log(f"[取词] 出错 {e!r}")

    def _stale(self, seq: int) -> bool:
        with self._cap_lock:
            return self._cap_want is not None and self._cap_want[3] > seq

    def _capture_once(self, x: int, y: int, manual: bool, seq: int) -> None:
        if self.capture is None:
            if manual:
                self.notify("取词服务还在启动，请稍等几秒")
            return
        res = self.capture.word_at(x, y)
        word = (res.get("word") or "").strip()
        box = tuple(res.get("box") or (x - 20, y - 10, x + 20, y + 10))
        if self._stale(seq):
            return  # 鼠标已经去了别的词
        if not word or len(word) > 40:
            if manual:
                self.notify("鼠标下没认出文字：把鼠标移到单词正上方再试")
            return
        if self.cfg.get("capture_only_english", True) and not ENGLISH.search(word):
            return
        with self._float_lock:
            if self.hover.visible and word == self.float_word and box == self.float_box:
                return
        data = self.search(word, mdx_limit=6)
        card = data.get("card")
        note = ""
        if (card is None or not card["found"]) and len(word) >= 5 and word.isalpha() and self.engine:
            # 识别偶尔认错一个字母（l/I、rn/m）：只接受编辑距离为 1 的纠正
            for cand in self.engine.fuzzy(word, limit=3, max_dist=1):
                alt = self.search(cand, mdx_limit=6)
                if alt.get("card") and alt["card"]["found"]:
                    note, data = f"识别为「{word}」，按「{cand}」显示", alt
                    break
        if not (data.get("card") and data["card"]["found"]) and not manual:
            return
        if self._stale(seq):
            return
        data["status"] = note or data.get("status", "")
        log(f"[取词] {word} ← {res.get('via')} {res.get('ms')}ms")
        self.show_float(data, box)
        if data.get("card") and data["card"]["found"] and self.cfg.get("auto_speak"):
            self.wm.call("float", "speakHead", None)

    # ================= 浮窗 =================
    def show_float(self, data: dict, box: tuple) -> None:
        data = dict(data, pinned=self.hover.pinned, prefs=self.prefs())
        h_css = self.wm.call("float", "render", data)
        try:
            h_css = int(h_css)
        except (TypeError, ValueError):
            h_css = 260
        word = (data.get("card") or {}).get("word") or data.get("query", "")
        with self._float_lock:
            self.float_word = data.get("query") or word
            self.float_box = box
        self._place_float(h_css)
        if self.highlight is not None and self.cfg.get("highlight_word", True):
            self.highlight.show(box)

    def _place_float(self, h_css: int) -> None:
        box = self.float_box
        if not box:
            return
        cx, cy = (box[0] + box[2]) // 2, (box[1] + box[3]) // 2
        scale = win32.scale_at(cx, cy)
        work = win32.work_area_at(cx, cy)
        max_h = int((work[3] - work[1]) * 0.5)
        w = int(FLOAT_WIDTH * scale)
        h = max(int(96 * scale), min(int(h_css * scale), max_h))
        x, y = place_card(box, (w, h), work, gap=int(6 * scale), inset=int(14 * scale))
        if self.wm.show_native("float", x, y, w, h):
            self.hover.shown(box, (x, y, x + w, y + h))

    def resize_float(self, h_css: int) -> None:
        if self.hover.visible:
            self._place_float(int(h_css))

    def hide_float(self) -> None:
        self.wm.hide("float")
        self.hover.hidden()
        with self._float_lock:
            self.float_word = ""
            self.float_box = None
        if self.highlight is not None:
            self.highlight.hide()
        self.wm.call("float", "reset", None)

    # ================= 鼠标线程 =================
    def _mouse_loop(self) -> None:
        while not self.quit:
            time.sleep(TICK)
            try:
                self._mouse_tick()
            except Exception as e:
                log(f"[鼠标] 出错 {e!r}")
                time.sleep(1)

    def _mouse_tick(self) -> None:
        now = time.monotonic()
        pos = win32.cursor_pos()
        mod_name = self.cfg.get("capture_modifier", "shift")
        self.hover.mode = self.cfg.get("capture_mode", "modifier")
        self.hover.hover_delay = self.cfg.get("hover_delay_ms", 450) / 1000
        lbutton = win32.key_down(win32.VK_LBUTTON)
        in_own = win32.own_window_at(*pos)
        inputs = Inputs(now=now, pos=pos,
                        modifier=self.hover.mode == "modifier" and win32.only_modifier_down(mod_name),
                        lbutton=lbutton, in_own_window=in_own,
                        esc=self.hover.visible and win32.key_down(win32.VK_ESCAPE))
        for act in self.hover.tick(inputs):
            if act[0] == "capture" and self.engine is not None:
                self.request_capture(act[1], act[2])
            elif act[0] == "hide":
                self.hide_float()
        if self.cfg.get("selection_button", True):
            self._selection_tick(now, pos, lbutton, in_own)

    # ---------- 划词按钮 ----------
    def _selection_tick(self, now: float, pos, lbutton: bool, in_own: bool) -> None:
        if self._sel_shown_at:
            far = abs(pos[0] - self._sel_pos[0]) > 160 or abs(pos[1] - self._sel_pos[1]) > 120
            if (now - self._sel_shown_at > 4 and not in_own) or far or (lbutton and not in_own):
                self.hide_selection_button()
        if lbutton and self._drag_start is None and not in_own:
            self._drag_start = (pos, now)
        elif not lbutton and self._drag_start is not None:
            (sx, sy), t0 = self._drag_start
            self._drag_start = None
            if in_own:
                return
            dragged = abs(pos[0] - sx) > 12 and now - t0 > 0.12
            last_t, last_p = self._last_click
            double = now - last_t < 0.45 and abs(pos[0] - last_p[0]) < 5 and abs(pos[1] - last_p[1]) < 5
            self._last_click = (now, pos)
            if dragged or double:
                self.show_selection_button(pos)

    def show_selection_button(self, pos) -> None:
        w = self.wm.wins["selbtn"]
        if w.window is None:
            self.wm.create("selbtn")
            return  # 第一次只建窗口，下次划词就有了
        if not w.ready.is_set():
            return
        scale = win32.scale_at(*pos)
        size = int(36 * scale)
        self._sel_owner = win32.foreground()
        self._sel_pos = pos
        self._sel_shown_at = time.monotonic()
        self.wm.show_native("selbtn", pos[0] + int(10 * scale), pos[1] + int(14 * scale), size, size)

    def hide_selection_button(self) -> None:
        self._sel_shown_at = 0.0
        self.wm.hide("selbtn")

    def selection_button_clicked(self) -> None:
        self.hide_selection_button()
        win32.set_foreground(self._sel_owner)  # 焦点还给原来的程序，模拟复制才复制得到
        time.sleep(0.08)
        self.translate_selection(skip_uia=False)

    # ================= 热键与动作 =================
    def on_hotkey(self, action: str) -> None:
        log(f"[热键] {action}")
        if action == "lookup":
            self.open_lookup("")
        elif action == "translate":
            self.translate_selection()
        elif action == "capture":
            x, y = win32.cursor_pos()
            self.request_capture(x, y, manual=True)
        elif action == "add_vocab":
            self.add_current_to_vocab()
        elif action == "toggle_auto":
            self.toggle_capture()
        elif action == "quit":
            self.exit()

    def toggle_capture(self) -> None:
        mode = self.cfg.get("capture_mode")
        if mode == "off":
            self.cfg["capture_mode"] = self.cfg.get("_last_mode") or "modifier"
            self.notify("鼠标取词：已开启")
        else:
            self.cfg["_last_mode"] = mode
            self.cfg["capture_mode"] = "off"
            self.notify("鼠标取词：已关闭（快捷键取词仍可用）")
        self.cfg.save()
        self._refresh_tray()

    def add_current_to_vocab(self) -> None:
        word = self.float_word if self.hover.visible else ""
        if not word and self.capture is not None:
            word = (self.capture.word_at(*win32.cursor_pos()).get("word") or "").strip()
        if not word or not ENGLISH.search(word):
            self.notify("鼠标下没有英文单词，没收藏")
            return
        card = self.search(word).get("card") or {}
        word = card.get("word") or word
        if self.vocab.add(word, card_brief(card)):
            self.notify(f"已收进生词本：{word}")
            self.wm.call("float", "setSaved", True)
        else:
            self.notify(f"生词本里已经有「{word}」了")

    def open_lookup(self, word: str = "") -> None:
        if self.hover.visible:
            self.hide_float()
        threading.Thread(target=self._open_lookup, args=(word,), daemon=True).start()

    def _open_lookup(self, word: str) -> None:
        self.wm.show_window("lookup")
        self.wm.call("lookup", "open", {"word": word, "prefs": self.prefs()})

    def open_vocab(self) -> None:
        threading.Thread(target=lambda: (self.wm.show_window("vocab"),
                                         self.wm.call("vocab", "refresh", None)), daemon=True).start()

    def open_settings(self) -> None:
        def run():
            self.wm.show_window("settings")
            self.wm.call("settings", "render", self.settings_payload())
        threading.Thread(target=run, daemon=True).start()

    def translate_selection(self, skip_uia: bool = False) -> None:
        text = None
        if not skip_uia and self.capture is not None:
            try:
                text = self.capture.selection()
            except Exception as e:
                log(f"[翻译] 读选区失败 {e!r}")
        if not text:  # 选区接口读不到：模拟一次复制再读剪贴板（读完恢复原剪贴板）
            text = copy_selection_text()
        if not text:
            self.notify(f"没读到选中的文字：先选中一段文字，再按 {hotkey_text(self.cfg['hotkeys']['translate'])}")
            return
        self.show_translate(text.strip()[:3000])

    def show_translate(self, text: str) -> None:
        if self.hover.visible:
            self.hide_float()
        self.wm.call("translate", "render", {"original": text, "loading": True, "prefs": self.prefs()})
        self.wm.show_window("translate")
        translated, source = None, ""
        online = bool(self.cfg.get("online_translate", True))
        if online:
            translated = translate.translate(text)
            source = translate.last_source()
        words = []
        if self.engine is not None and self.engine.books:
            def brief(w):
                return card_brief(build_card(self.engine, w), 40) or ""
            words = pick_new_words(text, brief, rank=self.engine.rank, saved=self.vocab.words())
        self.wm.call("translate", "render", {
            "original": text, "loading": False, "translated": translated, "source": source,
            "online": online, "words": words, "prefs": self.prefs()})

    # ================= 设置 =================
    def settings_payload(self) -> dict:
        counts = {b.path: len(b) for b in (self.engine.books if self.engine else [])}
        dicts = []
        for d in self.cfg.get("dictionaries", []):
            path = str(d.get("path", ""))
            dicts.append({"path": path, "name": d.get("name") or Path(path).stem,
                          "count": counts.get(path, 0), "missing": not Path(path).exists(),
                          "kind": "ECDICT" if path.lower().endswith((".csv", ".db")) else "MDict"})
        status = self.hotkeys.status if self.hotkeys else {}
        cap = self.capture.status if self.capture else {}
        return {
            "hotkeys": {a: {"spec": s, "ok": status.get(a), "label": HOTKEY_LABELS.get(a, a)}
                        for a, s in self.cfg["hotkeys"].items()},
            "dictionaries": dicts,
            "has_ecdict": any(d["kind"] == "ECDICT" for d in dicts),
            "ecdict": dict(self.ecdict_state),
            "engine_ready": self.engine_ready.is_set(),
            "capture": cap,
            "data_dir": str(DATA),
            **{k: self.cfg.get(k) for k in (
                "capture_mode", "capture_modifier", "hover_delay_ms", "capture_only_english",
                "highlight_word", "selection_button", "online_translate", "baidu_appid", "baidu_key",
                "accent", "auto_speak", "font_scale", "theme", "hide_tray_icon")},
            "autostart": autostart.enabled(),
            "prefs": self.prefs(),
        }

    def save_settings(self, payload: dict) -> dict:
        changes, err = apply_settings(self.cfg, payload, valid_hotkey)
        if err:
            return {"ok": False, "error": err}
        self.cfg.save()
        if "translate" in changes:
            translate.configure(self.cfg["baidu_appid"], self.cfg["baidu_key"])
        if "autostart" in changes:
            autostart.set_enabled(self.cfg["autostart"], log)
        if "hotkeys" in changes and self.hotkeys:
            self.hotkeys.reload()
            self.hotkeys.registered.wait(2)
        if "dictionaries" in changes:
            self.reload_engine()
        self.wm.broadcast("setPrefs", self.prefs())
        self._refresh_tray()
        notes = []
        if "hotkeys" in changes:
            bad = [HOTKEY_LABELS[a] for a, ok in self.hotkeys.status.items() if not ok]
            notes.append("快捷键已更新" + (f"，但「{'、'.join(bad)}」被占用" if bad else ""))
        if "dictionaries" in changes:
            notes.append("词典正在后台重新加载")
        if "hide_tray_icon" in changes:
            notes.append("托盘图标的变化重启后生效")
        return {"ok": True, "message": "；".join(notes) or "已保存", "settings": self.settings_payload()}

    def download_ecdict(self) -> dict:
        if self.ecdict_state["running"]:
            return {"ok": False, "error": "已经在下载了"}
        threading.Thread(target=self._download_ecdict, daemon=True).start()
        return {"ok": True}

    def _ecdict_progress(self, pct: int, msg: str, done: bool = False, ok: bool = True) -> None:
        self.ecdict_state.update(pct=pct, msg=msg)
        self.wm.call("settings", "ecdictProgress", {"pct": pct, "msg": msg, "done": done, "ok": ok})

    def _download_ecdict(self) -> None:
        """下载开源词库 ECDICT（约 63MB，MIT 许可），完成后自动加进词典列表。"""
        self.ecdict_state["running"] = True
        target = DICT_DIR / "ecdict.csv"
        part = target.with_suffix(".csv.part")
        try:
            DICT_DIR.mkdir(parents=True, exist_ok=True)
            if not target.exists():
                self._ecdict_progress(0, "正在连接 GitHub…")
                with requests.get(ECDICT_URL, stream=True, timeout=(10, 30)) as r:
                    r.raise_for_status()
                    total = int(r.headers.get("Content-Length") or 0)
                    got, last = 0, 0.0
                    with open(part, "wb") as f:
                        for chunk in r.iter_content(256 * 1024):
                            f.write(chunk)
                            got += len(chunk)
                            if time.time() - last > 0.5:
                                last = time.time()
                                pct = min(99, int(got * 100 / total)) if total else 0
                                self._ecdict_progress(pct, f"已下载 {got / 1048576:.1f} MB"
                                                      + (f" / {total / 1048576:.0f} MB" if total else ""))
                part.replace(target)
            self._ecdict_progress(100, "下载完成，正在建立索引（第一次约半分钟）…")
            dicts = self.cfg.setdefault("dictionaries", [])
            if not any(Path(d.get("path", "")) == target for d in dicts):
                dicts.insert(0, {"path": str(target), "name": "ECDICT"})  # 放最前：简明释义靠它
                self.cfg.save()
            self.engine_ready.clear()
            self._load_engine()
            ok = self.engine is not None and self.engine.ecdict is not None
            msg = "ECDICT 已就绪" if ok else "ECDICT 加载失败，详见日志"
            self._ecdict_progress(100, msg, done=True, ok=ok)
            self.wm.call("settings", "render", self.settings_payload())
            self.notify(msg)
        except Exception as e:
            log(f"[ECDICT] 下载失败 {e!r}")
            try:
                part.unlink()
            except Exception:
                pass
            self._ecdict_progress(0, "下载失败：连不上 GitHub。可以手动下载 ecdict.csv 后用「添加词典」选它",
                                  done=True, ok=False)
        finally:
            self.ecdict_state["running"] = False

    # ================= 发音 =================
    def speak_offline(self, text: str, accent: str = "") -> bool:
        return self.sapi.speak(text, accent or self.cfg.get("accent", "us"))

    # ================= 生词本导出 =================
    def anki_back(self, word: str) -> str | None:
        if self.engine is None:
            return None
        card = build_card(self.engine, word)
        if card["found"] and card["senses"]:
            lines = [f"<b>{c['pos']}</b> {c['text']}" if c["pos"] else c["text"] for c in card["senses"]]
            phon = card["phonetic"].get("uk") or card["phonetic"].get("plain") or ""
            return (f"{phon}<br>" if phon else "") + "<br>".join(lines)
        entries = self.engine.lookup_mdx(word)[:1]
        return clean_html(entries[0].html)[:3000] if entries else None

    # ================= 自检 =================
    def self_check(self) -> str:
        lines = ["暖词自检报告", f"时间: {time.strftime('%Y-%m-%d %H:%M:%S')}", f"数据目录: {DATA}", ""]
        if self.engine is None:
            lines.append("词典: 还没加载完")
        else:
            lines.append(f"词典: {len(self.engine.books)} 本")
            for b in self.engine.books:
                lines.append(f"  · {b.name}: {len(b):,} 条")
            for name, why in self.engine.errors:
                lines.append(f"  × {name}: {why}")
            if self.engine.ecdict is None:
                lines.append("  提示：没有 ECDICT，浮窗的考纲标签、词形变化、简明释义会不完整")
        lines.append("")
        if self.capture is None:
            lines.append("取词服务: 还没启动")
        else:
            st = self.capture.refresh_status()
            lines.append(f"系统文字接口: {'可用' if st.get('uia') else '不可用'}")
            lines.append(f"文字识别: {st.get('ocr') or '不可用'} {st.get('ocr_language') or ''}")
            if st.get("ocr") and not str(st.get("ocr_language", "")).startswith("en"):
                lines.append("  提示：系统没装英语文字识别，英文识别会差一些。"
                             "设置 → 时间和语言 → 语言和区域 → 添加语言 → English (United States)")
            for n in st.get("notes", []):
                lines.append(f"  · {n}")
            x, y = win32.cursor_pos()
            t0 = time.time()
            r = self.capture.word_at(x, y)
            lines.append(f"取词测试（鼠标处）: {r.get('word')!r} via {r.get('via')} "
                         f"{(time.time() - t0) * 1000:.0f} 毫秒")
        lines.append("")
        for action, ok in (self.hotkeys.status if self.hotkeys else {}).items():
            spec = self.cfg["hotkeys"].get(action, "")
            lines.append(f"快捷键 {HOTKEY_LABELS.get(action, action)}（{hotkey_text(spec)}）: "
                         f"{'正常' if ok else '注册失败（被占用）'}")
        mode = {"modifier": f"按住 {self.cfg['capture_modifier'].capitalize()} 取词",
                "hover": "鼠标停住取词", "off": "关闭"}[self.cfg["capture_mode"]]
        lines.append(f"鼠标取词: {mode}")
        lines.append(f"开机自启: {'开' if autostart.enabled() else '关'}")
        t0 = time.time()
        tr = translate.translate("This is a self check.")
        lines.append(f"联网翻译: {'正常（' + translate.last_source() + '）' if tr else '不可用'} "
                     f"({time.time() - t0:.1f} 秒)")
        lines += ["", f"日志文件: {LOG_FILE}"]
        report = "\n".join(lines)
        try:
            path = DATA / "自检报告.txt"
            path.write_text(report, encoding="utf-8")
            os.startfile(str(path))
        except Exception:
            pass
        log("[自检]\n" + report)
        return report

    # ================= 托盘 =================
    def _refresh_tray(self) -> None:
        try:
            if self.tray is not None:
                self.tray.update_menu()
        except Exception:
            pass

    def _tray(self) -> None:
        if self.cfg.get("hide_tray_icon"):
            log(f"[托盘] 已隐藏托盘图标；退出请按 {hotkey_text(self.cfg['hotkeys']['quit'])}")
            return
        from .tray import build_tray
        self.tray = build_tray(self)
        self.tray.run()

    def restart(self) -> None:
        cmd = [sys.executable] if FROZEN else [sys.executable, str(BASE / "main.py")]
        try:
            subprocess.Popen(cmd + ["--force"], cwd=str(BASE), creationflags=0x00000008)
        except Exception as e:
            log(f"[重启] 拉起新进程失败 {e!r}")
        time.sleep(0.8)
        self.exit()

    def exit(self) -> None:
        log("[退出]")
        self.quit = True
        try:
            if self.capture is not None:
                self.capture.close()
            if self.tray is not None:
                self.tray.stop()
        except Exception:
            pass
        os._exit(0)

    def open_folder(self, which: str) -> bool:
        path = {"export": EXPORT_DIR, "data": DATA}.get(which, DATA)
        try:
            path.mkdir(parents=True, exist_ok=True)
            os.startfile(str(path))
            return True
        except Exception as e:
            log(f"[打开目录] 失败 {e!r}")
            return False

    def copy(self, text: str) -> None:
        set_clipboard_text(text or "")


def main() -> None:
    migrate_old_data(log)
    if "--force" in sys.argv or "--restart" in sys.argv:
        if win32.signal_event(win32.QUIT_EVENT):
            log("[重启] 已通知旧实例退出")
            time.sleep(2.0)
    elif not win32.single_instance():
        log("[启动] 已有实例在运行，唤出查词窗")
        win32.user32.AllowSetForegroundWindow(-1)
        win32.signal_event(win32.SHOW_EVENT)
        return
    App().run()

