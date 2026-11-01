# -*- coding: utf-8 -*-
"""单词卡片：浮窗和查词窗顶部的「简明」内容。

顺序与取舍参考欧路词典的取词窗：词头 → 英/美音标 → 考纲标签与柯林斯星级 →
按词性分行的简明中文释义 → 词形变化；变形词（went、abandoned）标出原形。
有 ECDICT 时用它的结构化字段；没有时从 mdx 释义里尽量提取。
"""
from __future__ import annotations

import re

from ..textutil import plain_text
from .books import EcdictBook, parse_exchange
from .morph import inflection_candidates

EXCHANGE_LABELS = {"p": "过去式", "d": "过去分词", "i": "现在分词", "3": "三单",
                   "r": "比较级", "t": "最高级", "s": "复数"}
FORM_ORDER = ("s", "3", "p", "d", "i", "r", "t")
TAG_LABELS = {"zk": "中考", "gk": "高考", "cet4": "CET4", "cet6": "CET6", "ky": "考研",
              "ielts": "雅思", "toefl": "托福", "gre": "GRE"}
TAG_ORDER = ("zk", "gk", "cet4", "cet6", "ky", "ielts", "toefl", "gre")

CJK = re.compile(r"[一-鿿]")
# 行首词性：n. / vt. / adj. / [计] / noun / verb ...
POS_HEAD = re.compile(
    r"^\s*((?:(?:[a-z]{1,5}\.)\s*(?:[&/,]\s*)?)+|\[[^\]]{1,8}\]|"
    r"(?:noun|verb|adjective|adverb|preposition|conjunction|pronoun|determiner|"
    r"interjection|exclamation|phrasal verb|modal verb|abbreviation)\b)\s*", re.I)
_PHON = r"([/\[][^/\[\]\n]{1,40}[/\]])"
UK_PHON = re.compile(r"(?:BrE|英式?|UK|Br\.?)\s*[:：]?\s*" + _PHON)
US_PHON = re.compile(r"(?:NAmE|AmE|美式?|US|Am\.?)\s*[:：]?\s*" + _PHON)
ANY_PHON = re.compile(r"(?<![A-Za-z])" + _PHON)


def split_sense(line: str) -> dict:
    line = re.sub(r"\s+", " ", line).strip()
    m = POS_HEAD.match(line)
    if m and m.end() < len(line):
        return {"pos": m.group(1).strip(), "text": line[m.end():].strip()}
    return {"pos": "", "text": line}


def ecdict_senses(d: dict, limit: int = 8) -> list[dict]:
    text = (d.get("translation") or "").replace("\\n", "\n")
    out = [split_sense(x) for x in text.split("\n") if x.strip()]
    if not out:  # 只有英文释义的词条
        text = (d.get("definition") or "").replace("\\n", "\n")
        out = [split_sense(x) for x in text.split("\n") if x.strip()]
    return out[:limit]


def ecdict_forms(d: dict) -> list[dict]:
    ex = parse_exchange(d.get("exchange", ""))
    out, seen = [], set()
    for k in FORM_ORDER:
        w = ex.get(k)
        if w and (k, w) not in seen and w.lower() != (d.get("word") or "").lower():
            seen.add((k, w))
            out.append({"label": EXCHANGE_LABELS[k], "word": w})
    return out


def ecdict_tags(d: dict) -> list[str]:
    have = set((d.get("tag") or "").split())
    return [TAG_LABELS[t] for t in TAG_ORDER if t in have]


def relation_text(d: dict) -> str:
    """变形词条和原形的关系：exchange 里的 1:pd → 过去式和过去分词"""
    kinds = parse_exchange(d.get("exchange", "")).get("1", "")
    names = [EXCHANGE_LABELS[k] for k in kinds if k in EXCHANGE_LABELS]
    return "、".join(dict.fromkeys(names)) or "变形"


def mdx_phonetics(html: str) -> dict:
    text = plain_text(html)[:1500]
    out = {}
    m = UK_PHON.search(text)
    if m:
        out["uk"] = m.group(1)
    m = US_PHON.search(text)
    if m:
        out["us"] = m.group(1)
    if not out:
        m = ANY_PHON.search(text)
        if m:
            out["plain"] = m.group(1)
    return out


def mdx_senses(html: str, headword: str = "", limit: int = 5) -> list[dict]:
    """没有 ECDICT 时从 mdx 释义里挑出带中文的短行当简明释义。"""
    out = []
    for line in plain_text(html).split("\n"):
        line = re.sub(r"^\s*(\d+[.)]?|[①-⑳]|[a-z]\))\s*", "", line).strip()
        if not CJK.search(line) or len(line) > 90:
            continue
        if headword and line.lower() == headword.lower():
            continue
        out.append(split_sense(line))
        if len(out) >= limit:
            break
    return out


def _wrap(p: str) -> str:
    p = p.strip()
    if p and p[0] not in "/[":
        p = f"/{p}/"
    return p


def build_card(engine, query: str) -> dict:
    """查一个词，返回浮窗 / 查词窗用的卡片数据（找不到时 found=False）。"""
    q = (query or "").strip()
    card = {"query": q, "word": q, "found": False, "phonetic": {}, "tags": [], "collins": 0,
            "oxford": False, "senses": [], "en": [], "forms": [], "lemma": None, "rank": None}
    if not q or engine is None:
        return card
    ec: EcdictBook | None = engine.ecdict

    row = None
    if ec is not None:
        exact = ec.row(q)
        if exact is not None and not ec.has_meaning(exact):
            base = ec.lemma_of(exact)  # went → go：直接显示原形，标出关系
            if base:
                card["lemma"] = {"word": base, "relation": relation_text(exact), "from": exact["word"]}
                exact = ec.row(base)
        elif exact is not None:
            base = ec.lemma_of(exact)  # abandoned 自己有释义：照常显示，另外提示原形
            if base:
                card["lemma"] = {"word": base, "relation": relation_text(exact), "from": exact["word"]}
        if exact is None or not ec.has_meaning(exact):
            for cand in inflection_candidates(q)[1:]:  # 词库里没有这个变形：按规则回退
                r = ec.row(cand)
                if ec.has_meaning(r):
                    exact = r
                    card["lemma"] = {"word": r["word"], "relation": "变形", "from": q}
                    break
        if ec.has_meaning(exact):
            row = exact

    if row is not None:
        en = [split_sense(x) for x in (row.get("definition") or "").replace("\\n", "\n").split("\n")
              if x.strip()][:6] if (row.get("translation") or "").strip() else []
        card.update(found=True, word=row["word"], senses=ecdict_senses(row), forms=ecdict_forms(row), en=en,
                    tags=ecdict_tags(row), collins=int(row.get("collins") or 0),
                    oxford=bool(row.get("oxford")))
        if row.get("phonetic"):
            card["phonetic"] = {"plain": _wrap(row["phonetic"])}
        card["rank"] = ec.rank(row["word"])
        if card["lemma"] and card["lemma"]["word"].lower() == row["word"].lower() \
                and card["lemma"]["from"].lower() == row["word"].lower():
            card["lemma"] = None
    return card


def enrich_from_mdx(card: dict, entries: list) -> dict:
    """用 mdx 词条补全卡片：mdx 里分开标了英美音标就用它；没有 ECDICT 时从释义里提取简明释义。"""
    if not entries:
        return card
    first = entries[0]
    phon = mdx_phonetics(first.html)
    if "uk" in phon or "us" in phon:
        card["phonetic"] = {k: _wrap(v) for k, v in phon.items()}
    elif not card["phonetic"] and phon.get("plain"):
        card["phonetic"] = {"plain": _wrap(phon["plain"])}
    if not card["found"]:
        card["word"] = first.headword or card["word"]
        card["senses"] = mdx_senses(first.html, first.headword)
        card["found"] = True
    return card
