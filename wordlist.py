# -*- coding: utf-8 -*-
"""整句翻译里挑「生词」：去掉人人都认识的常用词，生词本里收藏过的词排在前面。"""
from __future__ import annotations

import re

# 最常用的英语词（功能词 + 高频基础词），整句翻译时不列为生词
COMMON_WORDS = set("""
a about above across after again against ago all almost along already also although always am among an and
another any anyone anything are around as ask asked at away back bad be became because become been before began
being below best better between big both but by call called came can cannot come could day did different do does
doing done don't down during each early end enough even ever every everyone everything far few find first for
found from full gave get gets getting give given go goes going gone good got great had has have having he her here
hers herself high him himself his home how however i i'm if in into is isn't it it's its itself just keep kind knew
know known last later least left less let like little long look looked lot made make makes making man many may me
mean men might mine more most much must my myself near need never new next no none nor not nothing now of off
often old on once one only or other others our ours out over own part people place put rather really right said
same saw say says see seem seemed seen several she should show side since small so some someone something
sometimes soon still such sure take taken tell than that that's the their theirs them themselves then there these
they thing things think this those though thought three through time to today together too took toward two under
until up upon us use used using very want was way we well went were what when where whether which while who whole
whom whose why will with within without word words work would year years yes yet you your yours yourself
""".split())

WORD_RE = re.compile(r"[A-Za-z][A-Za-z'’-]*[A-Za-z]")


def is_common(word: str, rank: int | None = None, common_rank: int = 3000) -> bool:
    """常用词判断：在常用词表里，或词频排名靠前（有 ECDICT 词频时）。"""
    w = word.lower().replace("’", "'")
    if w in COMMON_WORDS or len(w) <= 2:
        return True
    return rank is not None and 0 < rank <= common_rank


def pick_new_words(text: str, lookup, rank=None, saved: set[str] | None = None,
                   limit: int = 15) -> list[dict]:
    """从句子里挑生词。

    lookup(word) -> 简短释义（查不到返回空串）
    rank(word)   -> 词频排名（越小越常用，未知返回 None），可不传
    saved        -> 生词本里已有的词（小写），这些词即使常用也列出来并排在前面
    """
    saved = saved or set()
    seen: set[str] = set()
    picked: list[dict] = []
    for raw in WORD_RE.findall(text or ""):
        w = raw.lower().replace("’", "'")
        if w in seen:
            continue
        seen.add(w)
        in_vocab = w in saved
        if not in_vocab and is_common(w, rank(w) if rank else None):
            continue
        brief = lookup(w)
        if not brief:
            continue
        picked.append({"word": w, "brief": brief, "saved": in_vocab})
    picked.sort(key=lambda i: not i["saved"])  # 稳定排序：收藏过的在前，其余保持原文顺序
    return picked[:limit]
