# -*- coding: utf-8 -*-
from warmword.wordlist import pick_new_words


def test_common_words_are_skipped_and_saved_words_first():
    text = "The committee will scrutinize the proposal, and it is ubiquitous."
    lookup = {"committee": "n. 委员会", "scrutinize": "v. 仔细检查",
              "proposal": "n. 提议", "ubiquitous": "adj. 无处不在的", "the": "art."}.get
    words = pick_new_words(text, lambda w: lookup(w, ""), saved={"ubiquitous"})
    assert [w["word"] for w in words] == ["ubiquitous", "committee", "scrutinize", "proposal"]
    assert words[0]["saved"] is True


def test_rank_filters_frequent_words():
    words = pick_new_words("government ephemeral", lambda w: "释义",
                           rank={"government": 300, "ephemeral": 20000}.get)
    assert [w["word"] for w in words] == ["ephemeral"]
