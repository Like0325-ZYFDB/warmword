# -*- coding: utf-8 -*-
"""多词典合并查询。

    engine = DictEngine([(r'D:\\词典\\oaldpe.mdx', '牛津高阶'), (r'...\\ecdict.csv', 'ECDICT')])
    engine.lookup('tests')    # -> [Entry, ...]（按词典顺序）
    engine.suggest('dict')    # -> ['dictionary', 'diction', ...]
"""
from __future__ import annotations

from pathlib import Path

from .books import Entry, EcdictBook, open_book
from .morph import edit_distance


class DictEngine:
    def __init__(self, books: list[tuple[str, str | None]], cache_dir=None):
        """books: [(路径, 显示名), ...]；.mdx 按 MDict 读，.csv / .db 按 ECDICT 读。"""
        self.books: list = []
        self.errors: list[tuple[str, str]] = []  # [(显示名, 原因)]
        for path, name in books:
            label = name or Path(path).stem
            if not Path(path).exists():
                self.errors.append((label, "找不到文件"))
                continue
            try:
                self.books.append(open_book(path, name, cache_dir))
            except Exception as e:
                self.errors.append((label, repr(e)))

    @property
    def ecdict(self) -> EcdictBook | None:
        for b in self.books:
            if isinstance(b, EcdictBook):
                return b
        return None

    @property
    def mdx_books(self) -> list:
        return [b for b in self.books if b.kind == "mdx"]

    def lookup(self, word: str) -> list[Entry]:
        out: list[Entry] = []
        for book in self.books:
            out.extend(book.lookup(word))
        return out

    def lookup_mdx(self, word: str) -> list[Entry]:
        out: list[Entry] = []
        for book in self.mdx_books:
            out.extend(book.lookup(word))
        return out

    def suggest(self, prefix: str, limit: int = 8) -> list[str]:
        seen: list[str] = []
        for book in self.books:
            for w in book.suggest(prefix, limit):
                if w not in seen:
                    seen.append(w)
                if len(seen) >= limit:
                    return seen
        return seen

    def fuzzy(self, word: str, limit: int = 5, max_dist: int = 2) -> list[str]:
        """拼写纠错候选（跨词典汇总，距离近的在前）。"""
        scored: dict[str, int] = {}
        for book in self.books:
            for w in book.fuzzy(word, max_dist):
                scored.setdefault(w, edit_distance(w, word.casefold(), max_dist))
        return sorted(scored, key=lambda w: (scored[w], len(w), w))[:limit]

    def rank(self, word: str) -> int | None:
        ranks = [r for r in (b.rank(word) for b in self.books) if r]
        return min(ranks) if ranks else None

    def book(self, path: str):
        for b in self.books:
            if b.path == path:
                return b
        return None

    def book_by_id(self, bid: str):
        for b in self.books:
            if b.id == bid:
                return b
        return None
