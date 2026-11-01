# -*- coding: utf-8 -*-
"""页面能调用的接口（pywebview 的 js_api）：页面里写 pywebview.api.方法名(...)。

每次调用都在界面库的工作线程里执行，耗时的事放到后台线程，别卡住页面。
"""
from __future__ import annotations

import threading
from pathlib import Path

from .log import log
from .paths import EXPORT_DIR


def _bg(fn, *args):
    threading.Thread(target=fn, args=args, daemon=True).start()


class Api:
    def __init__(self, app):
        self._app = app

    # ---------- 页面生命周期 ----------
    def ready(self, name: str) -> dict:
        out = self._app.wm.mark_ready(name)
        out["prefs"] = self._app.prefs()
        return out

    def close(self, name: str) -> None:
        if name == "float":
            _bg(self._app.hide_float)
        else:
            _bg(self._app.wm.hide, name)

    # ---------- 查词 ----------
    def search(self, word: str, record: bool = False) -> dict:
        return self._app.search(word, record=bool(record))

    def suggest(self, prefix: str) -> list[str]:
        eng = self._app.engine
        return eng.suggest((prefix or "").strip(), 8) if eng is not None else []

    def recent(self) -> list[str]:
        return self._app.history.recent(30)

    def clear_history(self) -> bool:
        self._app.history.clear()
        return True

    def open_lookup(self, word: str = "") -> None:
        self._app.open_lookup(word)

    # ---------- 浮窗 ----------
    def float_pin(self, pinned: bool) -> bool:
        self._app.hover.pinned = bool(pinned)
        return self._app.hover.pinned

    def float_resize(self, height: int) -> None:
        _bg(self._app.resize_float, int(height or 0))

    def float_word(self, word: str) -> None:
        """浮窗里点了交叉引用 / 词形 / 原形：就地换成那个词。"""
        def run():
            data = self._app.search(word, mdx_limit=6)
            data = dict(data, pinned=self._app.hover.pinned, prefs=self._app.prefs())
            h = self._app.wm.call("float", "render", data)
            if h:
                self._app.resize_float(int(h))
        _bg(run)

    # ---------- 划词按钮 ----------
    def selection_clicked(self) -> None:
        _bg(self._app.selection_button_clicked)

    # ---------- 发音 ----------
    def speak_offline(self, text: str, accent: str = "") -> bool:
        return self._app.speak_offline(text, accent)

    # ---------- 生词本 ----------
    def vocab_toggle(self, word: str, brief: str = "") -> bool:
        return self._app.vocab.toggle(word, brief or "")

    def vocab_has(self, word: str) -> bool:
        return self._app.vocab.has(word)

    def vocab_list(self) -> list[dict]:
        return self._app.vocab.load()

    def vocab_remove(self, word: str) -> bool:
        return self._app.vocab.remove(word)

    def vocab_clear(self) -> int:
        return self._app.vocab.clear()

    def export_csv(self) -> str:
        return self._app.vocab.export_csv(EXPORT_DIR)

    def export_anki(self) -> str:
        return self._app.vocab.export_anki(EXPORT_DIR, self._app.anki_back)

    def open_folder(self, which: str = "export") -> bool:
        return self._app.open_folder(which)

    def open_vocab(self) -> None:
        self._app.open_vocab()

    def open_settings(self) -> None:
        self._app.open_settings()

    # ---------- 翻译 ----------
    def translate_text(self, text: str) -> None:
        _bg(self._app.show_translate, (text or "").strip()[:3000])

    def copy(self, text: str) -> None:
        self._app.copy(text)

    # ---------- 设置 ----------
    def get_settings(self) -> dict:
        return self._app.settings_payload()

    def save_settings(self, payload: dict) -> dict:
        try:
            return self._app.save_settings(payload or {})
        except Exception as e:
            log(f"[设置] 保存失败 {e!r}")
            return {"ok": False, "error": f"保存失败：{e}"}

    def set_entry_style(self, style: str) -> dict:
        self._app.save_settings({"entry_style": style})
        return self._app.prefs()

    def set_font_scale(self, scale: float) -> dict:
        self._app.save_settings({"font_scale": scale})
        return self._app.prefs()

    def pick_dictionary(self):
        """打开文件对话框选一本词典（.mdx，或 ECDICT 的 .csv / .db）。"""
        import webview
        win = self._app.wm.wins["settings"].window
        if win is None:
            return {"error": "设置窗口还没准备好"}
        try:
            files = win.create_file_dialog(
                webview.OPEN_DIALOG, allow_multiple=False,
                file_types=("词典文件 (*.mdx;*.csv;*.db)", "所有文件 (*.*)"))
        except Exception as e:
            return {"error": f"选择文件失败：{e}"}
        if not files:
            return None
        path = Path(files[0] if isinstance(files, (list, tuple)) else files)
        if path.suffix.lower() not in (".mdx", ".csv", ".db"):
            return {"error": "请选择 .mdx 词典，或 ECDICT 的 ecdict.csv / stardict.db"}
        ec = path.suffix.lower() in (".csv", ".db")
        return {"path": str(path), "name": "ECDICT" if ec else path.stem,
                "kind": "ECDICT" if ec else "MDict", "count": 0, "missing": False}

    def download_ecdict(self) -> dict:
        return self._app.download_ecdict()

    def self_check(self) -> None:
        _bg(self._app.self_check)
