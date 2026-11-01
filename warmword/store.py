# -*- coding: utf-8 -*-
"""生词本与查词历史（JSON 文件），以及导出 CSV / Anki。"""
from __future__ import annotations

import csv
import html as html_mod
import json
import threading
import time
from pathlib import Path


class JsonList:
    def __init__(self, path: Path, log=print):
        self.path = Path(path)
        self._log = log
        self._lock = threading.Lock()

    def load(self) -> list:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            return data if isinstance(data, list) else []
        except Exception:
            return []

    def save(self, items: list) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(items, ensure_ascii=False, indent=1), encoding="utf-8")
            tmp.replace(self.path)
        except Exception as e:
            self._log(f"[保存] {self.path.name} 失败 {e!r}")


class Vocab(JsonList):
    """生词本：[{word, brief, added_at}]，最新的在前。"""

    def words(self) -> set[str]:
        return {i.get("word", "").lower() for i in self.load()}

    def has(self, word: str) -> bool:
        return (word or "").strip().lower() in self.words()

    def add(self, word: str, brief: str = "") -> bool:
        word = (word or "").strip()
        if not word:
            return False
        with self._lock:
            items = self.load()
            if any(i.get("word", "").lower() == word.lower() for i in items):
                return False
            items.insert(0, {"word": word, "brief": brief,
                             "added_at": time.strftime("%Y-%m-%d %H:%M")})
            self.save(items)
        self._log(f"[生词本] 已加入 {word}（共 {len(items)} 个）")
        return True

    def remove(self, word: str) -> bool:
        with self._lock:
            items = self.load()
            left = [i for i in items if i.get("word", "").lower() != (word or "").lower()]
            if len(left) == len(items):
                return False
            self.save(left)
        return True

    def toggle(self, word: str, brief: str = "") -> bool:
        """收藏 / 取消收藏，返回操作后是否在生词本里。"""
        if self.has(word):
            self.remove(word)
            return False
        self.add(word, brief)
        return True

    def clear(self) -> int:
        with self._lock:
            n = len(self.load())
            self.save([])
        return n

    def export_csv(self, out_dir: Path) -> str:
        items = self.load()
        if not items:
            return ""
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / f"生词本_{time.strftime('%Y%m%d_%H%M')}.csv"
        with open(path, "w", encoding="utf-8-sig", newline="") as f:
            w = csv.writer(f)
            w.writerow(["单词", "释义", "加入时间"])
            for i in items:
                w.writerow([i.get("word", ""), i.get("brief", ""), i.get("added_at", "")])
        return str(path)

    def export_anki(self, out_dir: Path, back_html=None) -> str:
        """Anki 可直接导入的文本：正面单词，背面释义（HTML），制表符分隔。

        back_html(word) -> str | None：能查到词典时给出更完整的背面。
        """
        items = self.load()
        if not items:
            return ""
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / f"生词本_Anki_{time.strftime('%Y%m%d_%H%M')}.txt"
        lines = ["#separator:tab", "#html:true", "#tags column:3"]
        for i in items:
            word = i.get("word", "")
            back = (back_html(word) if back_html else None) or html_mod.escape(i.get("brief", ""))
            back = back.replace("\t", " ").replace("\n", "<br>")
            lines.append(f"{word}\t{back}\t暖词")
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return str(path)


class History(JsonList):
    """查词历史：最近的在前，去重，最多 60 个。"""

    def add(self, word: str) -> None:
        word = (word or "").strip()
        if not word or len(word) > 60:
            return
        with self._lock:
            items = [w for w in self.load() if isinstance(w, str) and w.lower() != word.lower()]
            items.insert(0, word)
            self.save(items[:60])

    def recent(self, n: int = 30) -> list[str]:
        return [w for w in self.load() if isinstance(w, str)][:n]

    def clear(self) -> None:
        self.save([])
