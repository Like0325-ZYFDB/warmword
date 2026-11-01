# -*- coding: utf-8 -*-
"""词形：屈折形式回退到原形（happily→happy、bigger→big），以及拼写纠错用的编辑距离。"""
from __future__ import annotations

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


def edit_distance(a: str, b: str, limit: int = 2) -> int:
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
