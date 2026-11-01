# -*- coding: utf-8 -*-
"""词典引擎测试：词形回退、mdx 查询、ECDICT（csv）查询。"""
import csv

import pytest
from mdict_utils import writer

from glance.dictionary import DictEngine, inflection_candidates


@pytest.mark.parametrize("word, base", [
    ("happily", "happy"), ("easily", "easy"), ("simply", "simple"), ("quickly", "quick"),
    ("bigger", "big"), ("biggest", "big"), ("later", "late"), ("nicest", "nice"),
    ("stopped", "stop"), ("running", "run"), ("hoped", "hope"), ("hoping", "hope"),
    ("used", "use"), ("makes", "make"), ("boxes", "box"), ("studies", "study"),
    ("wolves", "wolf"), ("doesn't", "do"), ("can't", "can"), ("John's", "john"),
    ("visited", "visit"), ("singing", "sing"), ("played", "play"),
])
def test_inflection_reaches_base(word, base):
    assert base in inflection_candidates(word)


@pytest.mark.parametrize("word, base, wrong", [
    ("hoped", "hope", "hop"),     # 没有双写辅音：先试补 e
    ("used", "use", "us"),
    ("makes", "make", "mak"),
    ("singing", "sing", "singe"),
])
def test_inflection_prefers_right_base(word, base, wrong):
    c = inflection_candidates(word)
    assert wrong not in c or c.index(base) < c.index(wrong)


def _make_mdx(tmp_path, entries):
    src = tmp_path / "src.txt"
    with open(src, "w", encoding="utf-8", newline="\n") as f:
        for k, v in entries.items():
            f.write(f"{k}\n{v}\n</>\n")
    target = tmp_path / "test.mdx"
    writer.pack(str(target), writer.pack_mdx_txt(str(src)), title="t")
    return target


def test_mdx_lookup_suggest_fuzzy(tmp_path):
    mdx = _make_mdx(tmp_path, {
        "dictionary": "<b>dictionary</b> n. 词典",
        "diction": "n. 措辞",
        "happy": "adj. 快乐的",
        "hope": "v. 希望",
        "hop": "v. 单脚跳",
        "Colour": "@@@LINK=color",
        "color": "n. 颜色",
    })
    eng = DictEngine([(str(mdx), "测试")])
    assert not eng.errors
    assert eng.lookup("happily")[0].headword == "happy"
    assert eng.lookup("hoped")[0].headword == "hope"
    assert "颜色" in eng.lookup("colour")[0].html          # @@@LINK 跳转
    assert eng.suggest("dict")[:2] == ["diction", "dictionary"]
    assert eng.fuzzy("dictionery", max_dist=1) == ["dictionary"]
    assert eng.fuzzy("xyzzy") == []


def test_missing_dictionary_is_reported(tmp_path):
    eng = DictEngine([(str(tmp_path / "nope.mdx"), "没有的词典")])
    assert eng.books == []
    assert eng.errors == [("没有的词典", "找不到文件")]


def test_ecdict_csv(tmp_path):
    path = tmp_path / "ecdict.csv"
    header = ["word", "phonetic", "definition", "translation", "pos", "collins", "oxford",
              "tag", "bnc", "frq", "exchange", "detail", "audio"]
    rows = [
        ["perceive", "pə'si:v", "v. become aware of", "v. 察觉\\n[网络] 感知", "", "", "",
         "cet4 cet6 ky", "3000", "2500", "d:perceived/i:perceiving", "", ""],
        ["perceived", "", "", "", "", "", "", "", "0", "0", "0:perceive/1:d", "", ""],
        ["the", "ðə", "", "art. 这；那", "", "", "", "zk", "1", "1", "", "", ""],
        ["perch", "pɜ:tʃ", "", "n. 栖木", "", "", "", "", "9000", "9000", "", "", ""],
    ]
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)
    eng = DictEngine([(str(path), "ECDICT")], cache_dir=tmp_path / "cache")
    assert not eng.errors and len(eng.books[0]) == 4
    hit = eng.lookup("perceived")                        # 变形词条只有 exchange，跟到原形
    assert hit and hit[0].headword == "perceive"
    assert hit[0].kind == "ecdict" and "察觉" in hit[0].data["translation"]
    assert "cet4" in hit[0].data["tag"]
    assert eng.lookup("perceives")[0].headword == "perceive"
    assert eng.suggest("per") == ["perch", "perceive", "perceived"]
    assert eng.fuzzy("percieve", max_dist=2)[0] == "perceive"
    assert eng.rank("the") == 1 and eng.rank("nope") is None
    # 第二次打开直接用缓存的 sqlite
    again = DictEngine([(str(path), "ECDICT")], cache_dir=tmp_path / "cache")
    assert again.lookup("perch")
