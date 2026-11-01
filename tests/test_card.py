# -*- coding: utf-8 -*-
"""单词卡片：ECDICT 结构化字段、变形词标原形、没有 ECDICT 时从 mdx 提取。"""
import csv

import pytest
from mdict_utils import writer

from warmword.dictionary import DictEngine, build_card, enrich_from_mdx
from warmword.dictionary.card import mdx_phonetics, split_sense

HEADER = ["word", "phonetic", "definition", "translation", "pos", "collins", "oxford",
          "tag", "bnc", "frq", "exchange", "detail", "audio"]
ROWS = [
    ["abandon", "ә'bændәn", "v. leave behind", "v. 放弃, 抛弃\\nn. 放任, 纵情", "", "4", "1",
     "cet4 ky gk", "3000", "2500", "p:abandoned/d:abandoned/i:abandoning/3:abandons"],
    ["abandoned", "ә'bændәnd", "", "a. 被抛弃的, 放纵的", "", "2", "", "cet6", "0", "0",
     "0:abandon/1:pd"],
    ["went", "went", "", "", "", "", "", "", "", "", "0:go/1:p"],
    ["go", "gәu", "", "vi. 去, 走\\nn. 尝试", "", "5", "1", "zk gk", "50", "40",
     "p:went/d:gone/i:going/3:goes"],
    ["happy", "'hæpi", "", "a. 快乐的, 幸福的", "", "5", "1", "zk", "500", "400",
     "r:happier/t:happiest"],
]


@pytest.fixture()
def engine(tmp_path):
    p = tmp_path / "ecdict.csv"
    with open(p, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(HEADER)
        for r in ROWS:
            w.writerow(r + [""] * (len(HEADER) - len(r)))
    return DictEngine([(str(p), "ECDICT")], cache_dir=tmp_path / "idx")


def test_card_basic(engine):
    c = build_card(engine, "abandon")
    assert c["found"] and c["word"] == "abandon"
    assert c["senses"][0] == {"pos": "v.", "text": "放弃, 抛弃"}
    assert c["senses"][1]["pos"] == "n."
    assert c["tags"] == ["高考", "CET4", "考研"]
    assert c["collins"] == 4 and c["oxford"] is True
    assert {"label": "过去式", "word": "abandoned"} in c["forms"]
    assert c["phonetic"] == {"plain": "/ә'bændәn/"}
    assert c["lemma"] is None


def test_card_inflected_without_meaning_shows_lemma(engine):
    c = build_card(engine, "went")
    assert c["word"] == "go"
    assert c["lemma"] == {"word": "go", "relation": "过去式", "from": "went"}


def test_card_inflected_with_own_meaning_keeps_word(engine):
    c = build_card(engine, "abandoned")
    assert c["word"] == "abandoned"
    assert c["lemma"]["word"] == "abandon"
    assert c["lemma"]["relation"] == "过去式、过去分词"


def test_card_rule_fallback(engine):
    c = build_card(engine, "happily")  # 词库里没有 happily：按规则回退到 happy
    assert c["word"] == "happy"
    assert c["lemma"]["from"] == "happily"


def test_card_not_found(engine):
    assert build_card(engine, "xyzzyq")["found"] is False


def test_split_sense():
    assert split_sense("vt. & vi. 打开") == {"pos": "vt. & vi.", "text": "打开"}
    assert split_sense("[计] 放弃") == {"pos": "[计]", "text": "放弃"}
    assert split_sense("没有词性") == {"pos": "", "text": "没有词性"}


def test_mdx_phonetics():
    html = "<span>BrE</span> <span class=phon>/əˈbændən/</span> NAmE /əˈbændən/"
    assert mdx_phonetics(html) == {"uk": "/əˈbændən/", "us": "/əˈbændən/"}


def test_enrich_from_mdx_without_ecdict(tmp_path):
    src = tmp_path / "src.txt"
    src.write_text("hello\n<b>hello</b> /həˈləʊ/<br>1. int. 你好<br>2. n. 招呼<br>\n</>\n",
                   encoding="utf-8")
    target = tmp_path / "t.mdx"
    writer.pack(str(target), writer.pack_mdx_txt(str(src)), title="t")
    eng = DictEngine([(str(target), "测试")])
    card = enrich_from_mdx(build_card(eng, "hello"), eng.lookup_mdx("hello"))
    assert card["found"]
    assert card["phonetic"] == {"plain": "/həˈləʊ/"}
    assert card["senses"][0] == {"pos": "int.", "text": "你好"}
