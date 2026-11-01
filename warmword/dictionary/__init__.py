# -*- coding: utf-8 -*-
"""词典层：mdx / ECDICT 读取、合并查询、单词卡片、原版排版资源。"""
from .books import Entry, EcdictBook, DictBook, open_book  # noqa: F401
from .card import build_card, enrich_from_mdx  # noqa: F401
from .engine import DictEngine  # noqa: F401
from .morph import inflection_candidates  # noqa: F401
