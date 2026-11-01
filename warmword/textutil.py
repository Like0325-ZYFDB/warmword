# -*- coding: utf-8 -*-
"""词典 HTML → 干净文字：生词本简要释义、Anki 导出、没有 ECDICT 时的简明释义都用它。"""
from __future__ import annotations

import html as html_mod
import re
from html.parser import HTMLParser

_TAG_RE = re.compile(r"<[^>]+>")


class _Cleaner(HTMLParser):
    """把词典原始 HTML 压成干净的富文本：块级转行、保留粗斜体、去掉脚本/图片/样式。"""

    BLOCK = {
        "div", "p", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6",
        "table", "section", "article", "header", "footer", "blockquote",
        "dt", "dd", "ul", "ol", "center", "html", "body",
    }
    KEEP = {"b": "b", "strong": "b", "i": "i", "em": "i", "u": "u",
            "sub": "sub", "sup": "sup"}
    DROP = {"script", "style", "img", "link", "input", "iframe", "audio",
            "video", "svg", "meta", "head", "table"}  # table 多是导航/排版干扰，整块丢弃
    DROP_SKIP = {"script", "style", "svg", "iframe", "audio", "video", "head", "table"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.skip_depth = 0

    def handle_starttag(self, tag, attrs):
        t = tag.lower()
        if t in self.DROP:
            if t in self.DROP_SKIP:
                self.skip_depth += 1
            return
        if self.skip_depth:
            return
        if t == "br":
            self.parts.append("<br>")
        elif t in self.KEEP:
            self.parts.append(f"<{self.KEEP[t]}>")
        elif t in ("span", "font", "small"):
            # 被丢弃的内联标签之间补空格，避免相邻词性标签粘成 "verbnoun"
            if self.parts and not self.parts[-1].endswith(("<br>", " ")):
                self.parts.append(" ")
        elif t in self.BLOCK:
            self.parts.append("<br>")

    def handle_endtag(self, tag):
        t = tag.lower()
        if t in self.DROP:
            if t in self.DROP_SKIP and self.skip_depth:
                self.skip_depth -= 1
            return
        if self.skip_depth:
            return
        if t in self.KEEP:
            self.parts.append(f"</{self.KEEP[t]}>")

    def handle_data(self, data):
        if self.skip_depth:
            return
        data = data.replace("\x00", "")
        data = re.sub(r"[ \t\r\f\v]+", " ", data)
        if data.strip():
            self.parts.append(html_mod.escape(data, quote=False))
        elif data and self.parts:
            last = self.parts[-1]
            if not last.endswith(("<br>", " ", "&nbsp;")) and last.strip():
                self.parts.append(" ")


POS_RE = re.compile(
    r"^(noun|verb|adjective|adverb|preposition|conjunction|pronoun|determiner|"
    r"interjection|numeral|article|auxiliary|abbr\.?|phrasal verb|idiom)\b", re.I)
NUM_RE = re.compile(r"^(\d+[a-z]?|\(\d+\))$")


def enrich_html(text: str) -> str:
    """给纯文本释义加语义标记：中文释义、音标、词性、编号单独着色，提升可读性。"""
    if not text:
        return text
    segs = text.split("<br>")
    out = []
    cn_re = re.compile(r"([\u4e00-\u9fff][\u4e00-\u9fff，。、；：！？（）《》〈〉…—·“”‘’、\s]*)")
    phon_re = re.compile(r"(?<![A-Za-z>])(/[^/<>]{2,42}/|\[[^\[\]<>]{2,30}\])")
    for seg in segs:
        seg = phon_re.sub(r'<span class="phon">\1</span>', seg)
        seg = cn_re.sub(r'<span class="cn">\1</span>', seg)
        # 中文和英文之间补空格，避免「language词典」粘在一起
        seg = re.sub(r'(<span class="cn">[^<]{0,200}</span>)', r' \1 ', seg)
        seg = re.sub(r" {2,}", " ", seg).strip()
        # 整句英文例句（大写开头、句号结尾、至少 4 个词）：标出来，页面会在后面加朗读按钮
        eng = re.split(r"[\u4e00-\u9fff]", _TAG_RE.sub("", seg), maxsplit=1)[0].strip()
        if (re.match(r"^[A-Z\"'‘“]", eng) and len(re.findall(r"[A-Za-z]{2,}", eng)) >= 4
                and re.search(r"[.?!][\"'’”]?$", eng)):
            seg = f'<span class="ex">{seg}</span>'
        # 行首词性、纯编号做成小标签，层次更分明
        m = POS_RE.match(seg)
        if m:
            seg = f'<span class="pos">{m.group(1)}</span>' + seg[m.end():]
        elif NUM_RE.match(seg):
            seg = f'<span class="num">{NUM_RE.match(seg).group(1)}</span>'
        out.append(seg)
    return "<br>".join(out)


def clean_html(raw: str) -> str:
    try:
        p = _Cleaner()
        p.feed(raw or "")
        text = "".join(p.parts)
    except Exception:
        text = re.sub(r"<[^>]+>", " ", (raw or "").replace("\x00", ""))
    text = re.sub(r"(<br>\s*){2,}", "<br>", text)
    text = re.sub(r"^(<br>\s*)+", "", text)
    text = re.sub(r"(\s*<br>)+$", "", text)
    # 相邻内联标签之间补空格，避免 "verb noun" 粘成 "verbnoun"
    text = re.sub(r"</(b|i|u|sub|sup)>\s*<(b|i|u|sub|sup)>", r"</\1> <\2>", text)
    text = re.sub(r"</(b|i|u|sub|sup)>(?=\S)", r"</\1> ", text)
    return enrich_html(text.strip())


def plain_text(raw: str) -> str:
    t = clean_html(raw).replace("<br>", "\n")
    t = _TAG_RE.sub("", t)
    return re.sub(r"[ \t]+", " ", t).strip()


def brief_text(raw: str, headword: str = "", limit: int = 140) -> str:
    """一行简要释义：去掉开头重复的词头，换行压成空格。"""
    text = plain_text(raw).replace("\n", " ")
    head = (headword or "").strip()
    if head and text.lower().startswith(head.lower()):
        text = text[len(head):].lstrip(" ·:：")
    return re.sub(r"\s{2,}", " ", text)[:limit]
