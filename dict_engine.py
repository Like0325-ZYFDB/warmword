# -*- coding: utf-8 -*-
"""词典查询引擎：常驻加载 mdx 词典，支持大小写折叠、词形回退、前缀建议。

用法：
    engine = DictEngine([r'D:\\词典\\oaldpe.mdx', ...])
    entries = engine.lookup('tests')   # -> [Entry(dict_name, headword, html), ...]
    words = engine.suggest('dict')     # -> ['dictionary', 'diction', ...]
"""
from __future__ import annotations

import bisect
import csv
import hashlib
import html as html_mod
import os
import re
import sqlite3
import threading
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
    "does": "do", "did": "do", "done": "do", "doing": "do",
    "has": "have", "having": "have", "dying": "die", "tying": "tie",
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


VOWELS = set("aeiou")
CONTRACTIONS = {"can't": "can", "won't": "will", "shan't": "shall", "ain't": "be"}


def _cvc(stem: str) -> bool:
    """词干以「辅音+单元音+辅音」结尾（hop、rat、us）：去掉词尾后多半要补回 e（hoped→hope）。"""
    if len(stem) < 2:
        return False
    c, v = stem[-1], stem[-2]
    if c in VOWELS or c in "wxy" or v not in VOWELS:
        return False
    return len(stem) == 2 or stem[-3] not in VOWELS


def inflection_candidates(word: str) -> list[str]:
    """把屈折形式回退成可能的原形，越靠前优先级越高。"""
    w = word.strip().replace("\u2019", "'")
    if not w:
        return []
    if not any(ch.isascii() and ch.isalpha() for ch in w):
        return [w.casefold()]  # 中文等非英文查询不做屈折回退
    wl = w.casefold()
    cands = [wl]

    def add(x: str):
        if len(x) >= 2 and x not in cands:
            cands.append(x)

    def add_stem(stem: str):
        # hoped→hope（补 e）、wanted→want、stopped→stop（双写辅音）
        if _cvc(stem):
            add(stem + "e")
            add(stem)
        else:
            add(stem)
            add(stem + "e")
        if len(stem) >= 3 and stem[-1] == stem[-2] and stem[-1] not in VOWELS:
            add(stem[:-1])

    if "'" in wl:  # doesn't→does→do，it's→it，John's→john
        if wl in CONTRACTIONS:
            add(CONTRACTIONS[wl])
        elif wl.endswith("n't"):
            add(wl[:-3])
        else:
            add(wl.split("'")[0])
        wl = cands[-1]
    if wl in IRREGULAR:
        add(IRREGULAR[wl])

    if wl.endswith("ies") and len(wl) > 4:
        add(wl[:-3] + "y")
    if wl.endswith("ves") and len(wl) > 4:
        add(wl[:-3] + "f")
        add(wl[:-3] + "fe")
    if wl.endswith("s") and not wl.endswith("ss") and len(wl) > 2:
        add(wl[:-1])               # makes→make，uses→use
        if wl.endswith("es") and len(wl) > 3:
            add(wl[:-2])           # boxes→box，goes→go
    if wl.endswith("ied") and len(wl) > 4:
        add(wl[:-3] + "y")
    if wl.endswith("ed") and len(wl) > 3:
        add_stem(wl[:-2])
    if wl.endswith("ing") and len(wl) > 4:
        add_stem(wl[:-3])
    if wl.endswith("ier") and len(wl) > 4:
        add(wl[:-3] + "y")
    if wl.endswith("er") and len(wl) > 3:
        add_stem(wl[:-2])          # bigger→big，later→late
    if wl.endswith("iest") and len(wl) > 5:
        add(wl[:-4] + "y")
    if wl.endswith("est") and len(wl) > 4:
        add_stem(wl[:-3])
    if wl.endswith("ly") and len(wl) > 3:
        if wl.endswith("ily") and len(wl) > 4:
            add(wl[:-3] + "y")     # happily→happy
        add(wl[:-2])               # quickly→quick
        add(wl[:-1] + "e")         # simply→simple，gently→gentle
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
        self._sorted = sorted(self._index)  # 前缀补全用二分查找，不用每次扫全表
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
        """前缀建议：同前缀里短词优先。"""
        fold = prefix if self.case_sensitive else prefix.casefold()
        if len(fold) < 2:
            return []
        out = []
        i = bisect.bisect_left(self._sorted, fold)
        while i < len(self._sorted) and len(out) < 200:
            k = self._sorted[i]
            if not k.startswith(fold):
                break
            out.append(k)
            i += 1
        out.sort(key=lambda s: (len(s), s))
        return out[:limit]

    def fuzzy(self, word: str, max_dist: int = 2) -> list[str]:
        """拼写纠错：按编辑距离找最接近的词条（首字母相同、长度差不超过 max_dist）。"""
        w = word.casefold() if not self.case_sensitive else word
        if len(w) < 3:
            return []
        best = []
        scanned = 0
        lo = bisect.bisect_left(self._sorted, w[0])
        for k in self._sorted[lo:]:
            if not k.startswith(w[0]):
                break
            if abs(len(k) - len(w)) > max_dist:
                continue
            scanned += 1
            if scanned > 20000:
                break
            d = _edit_distance(k, w, max_dist)
            if 0 < d <= max_dist:
                best.append((d, len(k), k))
        best.sort()
        return [k for _d, _l, k in best[:5]]

    def rank(self, word: str) -> int | None:
        return None  # mdx 词典没有词频信息


# ---------------- ECDICT 开源词库 ----------------
ECDICT_TAGS = {"zk": "中考", "gk": "高考", "cet4": "四级", "cet6": "六级", "ky": "考研",
               "toefl": "托福", "ielts": "雅思", "gre": "GRE"}
ECDICT_COLUMNS = ("word", "sw", "phonetic", "definition", "translation", "pos",
                  "tag", "bnc", "frq", "exchange")


def _strip_word(word: str) -> str:
    """与 ECDICT 的 stripword 一致：只留小写字母数字，用于前缀索引。"""
    return "".join(ch for ch in word.lower() if ch.isalnum())


def _to_int(v) -> int:
    try:
        return int(v or 0)
    except (TypeError, ValueError):
        return 0


def csv_to_sqlite(csv_path: Path, db_path: Path) -> None:
    """把 ecdict.csv 转成带索引的 sqlite（只做一次，约 10~30 秒）。"""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = db_path.with_name(db_path.name + ".tmp")
    if tmp.exists():
        tmp.unlink()
    csv.field_size_limit(1 << 24)
    con = sqlite3.connect(str(tmp))
    try:
        con.execute("CREATE TABLE stardict (" + ", ".join(
            f"{c} INTEGER" if c in ("bnc", "frq") else f"{c} TEXT" for c in ECDICT_COLUMNS) + ")")
        sql = f"INSERT INTO stardict VALUES ({', '.join('?' * len(ECDICT_COLUMNS))})"
        with open(csv_path, encoding="utf-8", newline="") as f:
            reader = csv.reader(f)
            header = next(reader)
            col = {name: i for i, name in enumerate(header)}

            def get(row, name):
                i = col.get(name)
                return row[i] if i is not None and i < len(row) else ""

            batch = []
            for row in reader:
                w = get(row, "word").strip()
                if not w:
                    continue
                batch.append((w, _strip_word(w), get(row, "phonetic"), get(row, "definition"),
                              get(row, "translation"), get(row, "pos"), get(row, "tag"),
                              _to_int(get(row, "bnc")), _to_int(get(row, "frq")),
                              get(row, "exchange")))
                if len(batch) >= 5000:
                    con.executemany(sql, batch)
                    batch.clear()
            if batch:
                con.executemany(sql, batch)
        con.execute("CREATE INDEX sd_word ON stardict (word COLLATE NOCASE)")
        con.execute("CREATE INDEX sd_sw ON stardict (sw)")
        con.commit()
    finally:
        con.close()
    os.replace(tmp, db_path)


def _ecdict_html(word, phonetic, definition, translation, tag) -> str:
    esc = lambda t: html_mod.escape(t or "", quote=False)  # noqa: E731
    parts = []
    if phonetic:
        parts.append(f"<div>/{esc(phonetic)}/</div>")
    for line in (translation or "").replace("\\n", "\n").split("\n"):
        if line.strip():
            parts.append(f"<div>{esc(line.strip())}</div>")
    for line in (definition or "").replace("\\n", "\n").split("\n"):
        if line.strip():
            parts.append(f"<div><i>{esc(line.strip())}</i></div>")
    tags = [ECDICT_TAGS[t] for t in (tag or "").split() if t in ECDICT_TAGS]
    if tags:
        parts.append(f"<div><small>考纲：{' '.join(tags)}</small></div>")
    return "".join(parts)


class EcdictBook:
    """ECDICT 开源词库（skywind3000/ECDICT，MIT 许可）：支持 ecdict.csv 或 stardict.db。

    csv 首次加载时转成 sqlite 索引（放在 cache_dir），之后秒开、几乎不占内存。
    """

    def __init__(self, path: str, name: str | None = None, cache_dir: str | Path | None = None):
        self.path = str(path)
        self.name = name or "ECDICT"
        self.case_sensitive = False
        t0 = time.time()
        p = Path(path)
        if p.suffix.lower() == ".csv":
            tag = hashlib.md5(str(p.resolve()).encode("utf-8")).hexdigest()[:8]
            db = Path(cache_dir or p.parent) / f"{p.stem}_{tag}.db"
            if not db.exists() or db.stat().st_mtime < p.stat().st_mtime:
                csv_to_sqlite(p, db)
        else:
            db = p
        self._db = sqlite3.connect(str(db), check_same_thread=False)
        self._lock = threading.Lock()
        self._count = self._q("SELECT COUNT(*) FROM stardict")[0][0]
        self.load_seconds = time.time() - t0

    def _q(self, sql: str, *args):
        with self._lock:
            return self._db.execute(sql, args).fetchall()

    def __len__(self):
        return self._count

    def _rows(self, word: str):
        return self._q("SELECT word, phonetic, definition, translation, tag, exchange "
                       "FROM stardict WHERE word = ? COLLATE NOCASE LIMIT 3", word)

    def lookup(self, word: str) -> list[Entry]:
        for cand in inflection_candidates(word):
            rows = self._rows(cand)
            hits = [r for r in rows if (r[3] or r[2])]
            if not hits:
                # 变形词条可能只有 exchange 字段（0:原形），跟到原形去
                for r in rows:
                    m = re.search(r"(?:^|/)0:([^/]+)", r[5] or "")
                    if m:
                        hits = [x for x in self._rows(m.group(1)) if (x[3] or x[2])]
                        if hits:
                            break
            if hits:
                return [Entry(self.name, r[0], _ecdict_html(r[0], r[1], r[2], r[3], r[4]))
                        for r in hits[:2]]
        return []

    def _prefix_rows(self, sw: str, sql_tail: str = "", *args, limit: int = 400):
        if not sw:
            return []
        hi = sw[:-1] + chr(ord(sw[-1]) + 1)
        return self._q("SELECT word FROM stardict WHERE sw >= ? AND sw < ? "
                       + sql_tail + f" LIMIT {int(limit)}", sw, hi, *args)

    def suggest(self, prefix: str, limit: int = 8) -> list[str]:
        sw = _strip_word(prefix)
        if len(sw) < 2:
            return []
        words = {r[0].casefold() for r in self._prefix_rows(sw)}
        return sorted(words, key=lambda s: (len(s), s))[:limit]

    def fuzzy(self, word: str, max_dist: int = 2) -> list[str]:
        w = word.casefold()
        sw = _strip_word(w)
        if len(w) < 3 or not sw:
            return []
        rows = self._prefix_rows(sw[0], "AND length(sw) BETWEEN ? AND ?",
                                 len(sw) - max_dist, len(sw) + max_dist, limit=8000)
        best = set()
        for (k,) in rows:
            k = k.casefold()
            d = _edit_distance(k, w, max_dist)
            if 0 < d <= max_dist:
                best.add((d, len(k), k))
        return [k for _d, _l, k in sorted(best)[:5]]

    def rank(self, word: str) -> int | None:
        """词频排名（越小越常用）：取 COCA 与 BNC 排名里较小的那个。"""
        rows = self._q("SELECT frq, bnc FROM stardict WHERE word = ? COLLATE NOCASE LIMIT 1", word)
        if not rows:
            return None
        ranks = [r for r in rows[0] if r and r > 0]
        return min(ranks) if ranks else None


def open_book(path: str, name: str | None = None, cache_dir=None):
    if Path(path).suffix.lower() in (".csv", ".db"):
        return EcdictBook(path, name, cache_dir)
    return DictBook(path, name)


class DictEngine:
    """多词典合并查询。"""

    def __init__(self, books: list[tuple[str, str]], cache_dir=None):
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

    def fuzzy(self, word: str, limit: int = 5, max_dist: int = 2) -> list[str]:
        """拼写纠错候选（跨词典汇总，距离近的在前）。"""
        scored: dict[str, int] = {}
        for book in self.books:
            for w in book.fuzzy(word, max_dist):
                scored.setdefault(w, _edit_distance(w, word.casefold(), max_dist))
        return sorted(scored, key=lambda w: (scored[w], len(w), w))[:limit]

    def rank(self, word: str) -> int | None:
        ranks = [r for r in (b.rank(word) for b in self.books) if r]
        return min(ranks) if ranks else None


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
