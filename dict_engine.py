# -*- coding: utf-8 -*-
"""词典查询引擎：常驻加载 mdx 词典，支持大小写折叠、词形回退、前缀建议。

用法：
    engine = DictEngine([r'D:\\词典\\oaldpe.mdx', ...])
    entries = engine.lookup('tests')   # -> [Entry(dict_name, headword, html), ...]
    words = engine.suggest('dict')     # -> ['dictionary', 'diction', ...]
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass
from pathlib import Path

from mdict_utils import reader

# 常见不规则变化 → 原形
IRREGULAR = {
    "was": "be", "were": "be", "been": "be", "am": "be", "is": "be", "are": "be",
    "went": "go", "gone": "go", "goes": "go",
    "got": "get", "gotten": "get",
    "ran": "run",
    "ate": "eat", "eaten": "eat",
    "took": "take", "taken": "take",
    "came": "come",
    "said": "say", "told": "tell",
    "made": "make", "had": "have",
    "children": "child", "men": "man", "women": "woman", "people": "person",
    "better": "good", "best": "good", "worse": "bad", "worst": "bad",
    "more": "many", "most": "many", "less": "little", "least": "little",
    "studies": "study", "studied": "study", "studying": "study",
    "bought": "buy", "sold": "sell", "thought": "think", "taught": "teach",
    "caught": "catch", "fought": "fight", "brought": "bring",
    "wrote": "write", "written": "write",
    "spoke": "speak", "spoken": "speak",
    "drove": "drive", "driven": "drive",
    "gave": "give", "given": "give",
    "felt": "feel", "kept": "keep", "left": "leave", "lost": "lose",
    "found": "find", "held": "hold", "heard": "hear", "knew": "know",
    "known": "know", "saw": "see", "seen": "see", "sat": "sit", "stood": "stand",
    "understood": "understand", "won": "win", "wore": "wear", "worn": "wear",
    "paid": "pay", "met": "meet", "put": "put", "cut": "cut", "read": "read",
    "meant": "mean", "spent": "spend", "sent": "send", "built": "build",
    "grew": "grow", "grown": "grow", "flew": "fly", "flown": "fly",
    "threw": "throw", "thrown": "throw", "began": "begin", "begun": "begin",
    "chose": "choose", "chosen": "choose", "drew": "draw", "drawn": "draw",
    "fell": "fall", "fallen": "fall", "forgot": "forget", "forgotten": "forget",
    "hid": "hide", "hidden": "hide", "rose": "rise", "risen": "rise",
    "sang": "sing", "sung": "sing", "sank": "sink", "sunk": "sink",
    "swam": "swim", "swum": "swim", "woke": "wake", "woken": "wake",
    "lying": "lie", "lay": "lie", "lain": "lie",
    "mice": "mouse", "geese": "goose", "teeth": "tooth", "feet": "foot",
    "criteria": "criterion", "phenomena": "phenomenon", "analyses": "analysis",
    "theses": "thesis", "crises": "crisis", "data": "datum",
}

_TAG_RE = re.compile(r"<[^>]+>")
_LINK_RE = re.compile(r"^\s*@@@LINK=(.+?)\s*$")


def _edit_distance(a: str, b: str, limit: int = 2) -> int:
    """编辑距离（超过 limit 提前返回）。"""
    if abs(len(a) - len(b)) > limit:
        return limit + 1
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


@dataclass
class Entry:
    dict_name: str   # 词典显示名
    headword: str    # 词头
    html: str        # 释义原文（html）

    @property
    def plain(self) -> str:
        text = self.html.replace("<br>", "\n").replace("<br/>", "\n")
        text = _TAG_RE.sub(" ", text)
        return re.sub(r"[ \t\r\f\v]+", " ", text).strip()


def inflection_candidates(word: str) -> list[str]:
    """把屈折形式回退成可能的原形，越靠前优先级越高。"""
    w = word.strip()
    if not w:
        return []
    if not any(ch.isascii() and ch.isalpha() for ch in w):
        return [w.casefold()]  # 中文等非英文查询不做屈折回退
    wl = w.casefold()
    cands = [wl]
    if wl in IRREGULAR:
        cands.append(IRREGULAR[wl])

    def add(x: str):
        if len(x) >= 2 and x not in cands:
            cands.append(x)

    if wl.endswith("ies") and len(wl) > 4:
        add(wl[:-3] + "y")
    if wl.endswith("ves") and len(wl) > 4:
        add(wl[:-3] + "f")
        add(wl[:-3] + "fe")
    if wl.endswith("es") and len(wl) > 3:
        add(wl[:-2])
        add(wl[:-1])
    if wl.endswith("s") and not wl.endswith("ss") and len(wl) > 2:
        add(wl[:-1])
    if wl.endswith("ied") and len(wl) > 4:
        add(wl[:-3] + "y")
    if wl.endswith("ed") and len(wl) > 3:
        add(wl[:-2])
        add(wl[:-1])
        if len(wl) > 4 and wl[-3] == wl[-4]:
            add(wl[:-3])
    if wl.endswith("ing") and len(wl) > 4:
        add(wl[:-3])
        add(wl[:-3] + "e")
        if len(wl) > 5 and wl[-4] == wl[-5]:
            add(wl[:-4])
    if wl.endswith("ier") and len(wl) > 4:
        add(wl[:-3] + "y")
    if wl.endswith("er") and len(wl) > 3:
        add(wl[:-2])
        add(wl[:-1])
    if wl.endswith("iest") and len(wl) > 5:
        add(wl[:-4] + "y")
    if wl.endswith("est") and len(wl) > 4:
        add(wl[:-3])
        add(wl[:-2])
    if wl.endswith("ly") and len(wl) > 3:
        add(wl[:-2])
        add(wl[:-4] + "y") if len(wl) > 5 else None
    if "'" in wl:  # don't -> do not, it's -> it is 的词头常见是 do/it
        base = wl.split("'")[0]
        add(base)
    return cands


class DictBook:
    """一本词典，常驻内存，按需取释义。"""

    def __init__(self, path: str, name: str | None = None):
        self.path = str(path)
        self.name = name or Path(path).stem
        t0 = time.time()
        self.md = reader.MDX(self.path)
        try:
            meta = reader.meta(self.path)
            self.case_sensitive = str(meta.get("keycasesensitive", "No")).lower() == "yes"
        except Exception:
            self.case_sensitive = False
        self._keys = self.md._key_list  # [(offset, key_bytes), ...]
        self._index: dict[str, list[int]] = {}
        for i, (_offset, key) in enumerate(self._keys):
            k = key.decode("utf-8", "ignore")
            fold = k if self.case_sensitive else k.casefold()
            self._index.setdefault(fold, []).append(i)
        self.load_seconds = time.time() - t0

    def __len__(self):
        return len(self._keys)

    def _record(self, i: int) -> str:
        offset, key = self._keys[i]
        if i + 1 < len(self._keys):
            length = self._keys[i + 1][0] - offset
        else:
            length = -1
        out = reader.get_record(self.md, key, offset, length)
        return out if isinstance(out, str) else out.decode("utf-8", "ignore")

    def _resolve(self, i: int, depth: int = 0) -> tuple[str, str]:
        """取第 i 条记录；遇到 @@@LINK 跳转就跟进目标词条（防循环）。"""
        key = self._keys[i][1].decode("utf-8", "ignore")
        try:
            html = self._record(i)
        except Exception:
            return key, ""
        seen_targets = set()
        while depth <= 3:
            m = _LINK_RE.match(html.replace("\x00", "").strip())
            if not m:
                return key, html
            target = m.group(1).strip()
            if target in seen_targets:
                return key, ""
            seen_targets.add(target)
            fold = target if self.case_sensitive else target.casefold()
            idxs = self._index.get(fold, [])
            if not idxs:
                return key, ""
            # 目标的第一条非链接记录
            html = ""
            for j in idxs:
                try:
                    html = self._record(j)
                except Exception:
                    continue
                if not _LINK_RE.match(html.replace("\x00", "").strip()):
                    key = target
                    break
                html = ""
            key = key if not html else target
            if not html:
                return target, ""
            depth += 1
        return key, html

    def lookup(self, word: str) -> list[Entry]:
        """精确（含词形回退）查找，按优先级返回全部命中词条。"""
        results: list[Entry] = []
        seen: set[tuple[str, str]] = set()
        for cand in inflection_candidates(word):
            fold = cand if self.case_sensitive else cand.casefold()
            for i in self._index.get(fold, []):
                head, html = self._resolve(i)
                if not html:
                    continue
                token = (head, html[:300])
                if token in seen:
                    continue
                seen.add(token)
                results.append(Entry(self.name, head, html))
            if results:
                break  # 回退到第一个有结果的形态就停
        return results

    def suggest(self, prefix: str, limit: int = 8) -> list[str]:
        """前缀建议。"""
        fold = prefix if self.case_sensitive else prefix.casefold()
        if len(fold) < 2:
            return []
        out = []
        for k in self._index:
            if k.startswith(fold):
                out.append(k)
                if len(out) >= limit * 3:
                    break
        out.sort(key=lambda s: (len(s), s))
        return out[:limit]

    def fuzzy(self, word: str, limit: int = 2) -> list[str]:
        """拼写纠错：按编辑距离找最接近的词条（首字母相同、长度差不超过 2）。"""
        w = word.casefold() if not self.case_sensitive else word
        if len(w) < 3:
            return []
        best = []
        scanned = 0
        for k in self._index:
            if not k or k[0] != w[0] or abs(len(k) - len(w)) > limit:
                continue
            scanned += 1
            if scanned > 20000:
                break
            d = _edit_distance(k, w, limit)
            if d <= limit and d > 0:
                best.append((d, len(k), k))
        best.sort()
        return [k for _d, _l, k in best[:5]]


class DictEngine:
    """多词典合并查询。"""

    def __init__(self, books: list[tuple[str, str]]):
        """books: [(路径, 显示名), ...]"""
        self.books: list[DictBook] = []
        for path, name in books:
            try:
                self.books.append(DictBook(path, name))
            except Exception as e:
                print(f"[词典加载失败] {name}: {e!r}")

    def lookup(self, word: str) -> list[Entry]:
        out: list[Entry] = []
        for book in self.books:
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

    def fuzzy(self, word: str, limit: int = 5) -> list[str]:
        """拼写纠错候选（跨词典汇总）。"""
        out: list[str] = []
        for book in self.books:
            for w in book.fuzzy(word, 2):
                if w not in out:
                    out.append(w)
                if len(out) >= limit:
                    return out
        return out


if __name__ == "__main__":
    import sys

    sys.stdout.reconfigure(encoding="utf-8")
    # 示例：把 d 换成你自己的词典所在目录
    d = r"C:\词典"
    engine = DictEngine([
        (d + r"\oaldZhEn.mdx", "牛津汉英"),
        (d + r"\oaldpe.mdx", "牛津高阶"),
        (d + r"\麦克米伦高阶英汉双解.mdx", "麦克米伦英汉双解"),
    ])
    for book in engine.books:
        print(f"已加载 {book.name}: {len(book)} 条，耗时 {book.load_seconds:.1f}s")
    for w in ["tests", "running", "apples", "Hello", "studied", "geese", "苹果", "xyzzy"]:
        t0 = time.time()
        entries = engine.lookup(w)
        names = [e.headword + "@" + e.dict_name for e in entries]
        print(f"{w!r} -> {names}  ({(time.time()-t0)*1000:.0f}ms)")
        if entries:
            print("   ", entries[0].plain[:120].replace("\n", " "))
    print("前缀建议 dict ->", engine.suggest("dict"))
