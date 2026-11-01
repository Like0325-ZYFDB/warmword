# -*- coding: utf-8 -*-
"""词典文件：MDict（.mdx）与 ECDICT（.csv / .db）。

两种词典对外接口一致：lookup / suggest / fuzzy / rank / __len__。
ECDICT 另外提供 row(word)：结构化的音标、释义、考纲标签、柯林斯星级、词形变化，
浮窗里的「简明」卡片主要靠它。
"""
from __future__ import annotations

import bisect
import csv
import hashlib
import os
import re
import sqlite3
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from .morph import edit_distance, inflection_candidates

_LINK_RE = re.compile(r"^\s*@@@LINK=(.+?)\s*$")


def book_id(path: str) -> str:
    """词典的短 id（资源地址里用）：路径的哈希，重新加载词典后不变。"""
    return hashlib.md5(str(path).encode("utf-8")).hexdigest()[:8]


@dataclass
class Entry:
    dict_name: str    # 词典显示名
    headword: str     # 词头
    html: str         # 释义原文（mdx 的 html；ECDICT 为空）
    source: str = ""  # 来自哪本词典（路径）
    kind: str = "mdx"  # mdx / ecdict
    data: dict = field(default_factory=dict)  # ECDICT 的结构化字段


class DictBook:
    """一本 mdx 词典，键表常驻内存，按需取释义。"""

    kind = "mdx"

    def __init__(self, path: str, name: str | None = None):
        from mdict_utils import reader
        self._reader = reader
        self.path = str(path)
        self.id = book_id(self.path)
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
        self._sorted = sorted(self._index)  # 前缀补全用二分查找
        self._res = None
        self._res_lock = threading.Lock()
        self.load_seconds = time.time() - t0

    def resources(self):
        """同目录的 mdd（发音、图片、样式），第一次用到时才加载。"""
        with self._res_lock:
            if self._res is None:
                from .resources import Resources
                self._res = Resources(self.path)
        return self._res

    def __len__(self):
        return len(self._keys)

    def _fold(self, w: str) -> str:
        return w if self.case_sensitive else w.casefold()

    def _record(self, i: int) -> str:
        offset, key = self._keys[i]
        length = self._keys[i + 1][0] - offset if i + 1 < len(self._keys) else -1
        out = self._reader.get_record(self.md, key, offset, length)
        return out if isinstance(out, str) else out.decode("utf-8", "ignore")

    def _resolve(self, i: int) -> tuple[str, str]:
        """取第 i 条记录；遇到 @@@LINK 跳转就跟进目标词条（最多跳 4 次，防循环）。"""
        key = self._keys[i][1].decode("utf-8", "ignore")
        try:
            html = self._record(i)
        except Exception:
            return key, ""
        seen: set[str] = set()
        for _ in range(4):
            m = _LINK_RE.match(html.replace("\x00", "").strip())
            if not m:
                return key, html
            target = m.group(1).strip()
            if target in seen:
                return key, ""
            seen.add(target)
            html = ""
            for j in self._index.get(self._fold(target), []):
                try:
                    rec = self._record(j)
                except Exception:
                    continue
                html = rec
                if not _LINK_RE.match(rec.replace("\x00", "").strip()):
                    break
            if not html:
                return target, ""
            key = target
        return key, ""

    def exact(self, word: str) -> list[Entry]:
        results: list[Entry] = []
        seen: set[tuple[str, str]] = set()
        for i in self._index.get(self._fold(word), []):
            head, html = self._resolve(i)
            if not html:
                continue
            token = (head, html[:300])
            if token in seen:
                continue
            seen.add(token)
            results.append(Entry(self.name, head, html, self.path))
        return results

    def lookup(self, word: str) -> list[Entry]:
        """精确查找（含词形回退），回退到第一个有结果的形态就停。"""
        for cand in inflection_candidates(word):
            hits = self.exact(cand)
            if hits:
                return hits
        return []

    def suggest(self, prefix: str, limit: int = 8) -> list[str]:
        fold = self._fold(prefix)
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
        """拼写纠错：首字母相同、编辑距离不超过 max_dist 的词条。"""
        w = self._fold(word)
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
            d = edit_distance(k, w, max_dist)
            if 0 < d <= max_dist:
                best.append((d, len(k), k))
        best.sort()
        return [k for _d, _l, k in best[:5]]

    def rank(self, word: str) -> int | None:
        return None  # mdx 词典没有词频信息


# ---------------- ECDICT 开源词库 ----------------
ECDICT_COLUMNS = ("word", "sw", "phonetic", "definition", "translation", "pos",
                  "collins", "oxford", "tag", "bnc", "frq", "exchange")
INT_COLUMNS = ("collins", "oxford", "bnc", "frq")
SCHEMA = "v2"  # 索引结构变了就改这里，旧索引会自动重建


def strip_word(word: str) -> str:
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
            f"{c} INTEGER" if c in INT_COLUMNS else f"{c} TEXT" for c in ECDICT_COLUMNS) + ")")
        sql = f"INSERT INTO stardict VALUES ({', '.join('?' * len(ECDICT_COLUMNS))})"
        with open(csv_path, encoding="utf-8", newline="") as f:
            rows = csv.reader(f)
            header = next(rows)
            col = {name: i for i, name in enumerate(header)}

            def get(row, name):
                i = col.get(name)
                return row[i] if i is not None and i < len(row) else ""

            batch = []
            for row in rows:
                w = get(row, "word").strip()
                if not w:
                    continue
                vals = []
                for c in ECDICT_COLUMNS:
                    if c == "word":
                        vals.append(w)
                    elif c == "sw":
                        vals.append(strip_word(w))
                    elif c in INT_COLUMNS:
                        vals.append(_to_int(get(row, c)))
                    else:
                        vals.append(get(row, c))
                batch.append(vals)
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


def parse_exchange(text: str) -> dict[str, str]:
    """ECDICT 的 exchange 字段：'p:went/d:gone/i:going/3:goes' → {'p': 'went', ...}"""
    out = {}
    for part in (text or "").split("/"):
        k, _, v = part.partition(":")
        if k and v:
            out[k.strip()] = v.strip()
    return out


class EcdictBook:
    """ECDICT 开源词库（skywind3000/ECDICT，MIT 许可）：支持 ecdict.csv 或 stardict.db。

    csv 首次加载时转成 sqlite 索引（放在 cache_dir），之后秒开、几乎不占内存。
    """

    kind = "ecdict"

    def __init__(self, path: str, name: str | None = None, cache_dir: str | Path | None = None):
        self.path = str(path)
        self.id = book_id(self.path)
        self.name = name or "ECDICT"
        t0 = time.time()
        p = Path(path)
        if p.suffix.lower() == ".csv":
            tag = hashlib.md5(str(p.resolve()).encode("utf-8")).hexdigest()[:8]
            db = Path(cache_dir or p.parent) / f"{p.stem}_{tag}_{SCHEMA}.db"
            if not db.exists() or db.stat().st_mtime < p.stat().st_mtime:
                csv_to_sqlite(p, db)
        else:
            db = p
        self._db = sqlite3.connect(str(db), check_same_thread=False)
        self._lock = threading.Lock()
        cols = {r[1] for r in self._q("PRAGMA table_info(stardict)")}
        self._cols = [c for c in ECDICT_COLUMNS if c in cols]  # 自带的 stardict.db 可能缺列
        self._count = self._q("SELECT COUNT(*) FROM stardict")[0][0]
        self.load_seconds = time.time() - t0

    def _q(self, sql: str, *args):
        with self._lock:
            return self._db.execute(sql, args).fetchall()

    def __len__(self):
        return self._count

    def rows(self, word: str) -> list[dict]:
        cols = ", ".join(self._cols)
        out = []
        for r in self._q(f"SELECT {cols} FROM stardict WHERE word = ? COLLATE NOCASE LIMIT 4", word):
            d = dict(zip(self._cols, r))
            for c in ECDICT_COLUMNS:
                d.setdefault(c, 0 if c in INT_COLUMNS else "")
            out.append(d)
        # 大小写完全一致的排前面（May 月份 / may 情态动词）
        out.sort(key=lambda d: d["word"] != word)
        return out

    def row(self, word: str) -> dict | None:
        """精确词条（不做词形回退）；只有词形信息、没有释义的也返回。"""
        rows = self.rows(word)
        return rows[0] if rows else None

    @staticmethod
    def has_meaning(d: dict | None) -> bool:
        return bool(d and ((d.get("translation") or "").strip() or (d.get("definition") or "").strip()))

    def lemma_of(self, d: dict) -> str | None:
        """变形词条（如 went）的原形：exchange 里的 0:原形。"""
        base = parse_exchange(d.get("exchange", "")).get("0")
        return base if base and base.lower() != d["word"].lower() else None

    def _entry(self, d: dict) -> Entry:
        return Entry(self.name, d["word"], "", self.path, "ecdict", d)

    def lookup(self, word: str) -> list[Entry]:
        for cand in inflection_candidates(word):
            rows = self.rows(cand)
            hits = [r for r in rows if self.has_meaning(r)]
            if not hits:
                for r in rows:  # 变形词条可能只有 exchange 字段，跟到原形去
                    base = self.lemma_of(r)
                    if base:
                        hits = [x for x in self.rows(base) if self.has_meaning(x)]
                        if hits:
                            break
            if hits:
                return [self._entry(r) for r in hits[:2]]
        return []

    def _prefix_rows(self, sw: str, sql_tail: str = "", *args, limit: int = 400):
        if not sw:
            return []
        hi = sw[:-1] + chr(ord(sw[-1]) + 1)
        return self._q("SELECT word FROM stardict WHERE sw >= ? AND sw < ? "
                       + sql_tail + f" LIMIT {int(limit)}", sw, hi, *args)

    def suggest(self, prefix: str, limit: int = 8) -> list[str]:
        sw = strip_word(prefix)
        if len(sw) < 2:
            return []
        words = {r[0].casefold() for r in self._prefix_rows(sw)}
        words = {w for w in words if w.startswith(prefix.casefold()[:1])}
        return sorted(words, key=lambda s: (len(s), s))[:limit]

    def fuzzy(self, word: str, max_dist: int = 2) -> list[str]:
        w = word.casefold()
        sw = strip_word(w)
        if len(w) < 3 or not sw:
            return []
        rows = self._prefix_rows(sw[0], "AND length(sw) BETWEEN ? AND ?",
                                 len(sw) - max_dist, len(sw) + max_dist, limit=8000)
        best = set()
        for (k,) in rows:
            k = k.casefold()
            d = edit_distance(k, w, max_dist)
            if 0 < d <= max_dist:
                best.add((d, len(k), k))
        return [k for _d, _l, k in sorted(best)[:5]]

    def rank(self, word: str) -> int | None:
        """词频排名（越小越常用）：取 COCA 与 BNC 排名里较小的那个。"""
        if "frq" not in self._cols:
            return None
        rows = self._q("SELECT frq, bnc FROM stardict WHERE word = ? COLLATE NOCASE LIMIT 1", word)
        if not rows:
            return None
        ranks = [r for r in rows[0] if r and r > 0]
        return min(ranks) if ranks else None


def open_book(path: str, name: str | None = None, cache_dir=None):
    if Path(path).suffix.lower() in (".csv", ".db"):
        return EcdictBook(path, name, cache_dir)
    return DictBook(path, name)
