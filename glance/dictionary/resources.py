# -*- coding: utf-8 -*-
"""词典原版排版：读取 .mdd 资源（发音、图片、样式），把词条 HTML 改写成能在界面里安全显示的样子。

- 同目录的 xxx.mdd / xxx.1.mdd … 以及散放在词典目录里的 css、图片都算资源
- 资源不再内嵌成 base64：页面通过本机资源服务按地址取（见 server.py），首次渲染不卡
- sound://… 改成 data-sound，点了播放；entry://… 改成 data-entry，点了跳到那个词
- 去掉脚本、事件属性、iframe 等，词典 HTML 只当内容显示
"""
from __future__ import annotations

import mimetypes
import re
import threading
import urllib.parse
from html import escape
from html.parser import HTMLParser
from pathlib import Path

MIME = {
    ".mp3": "audio/mpeg", ".wav": "audio/wav", ".ogg": "audio/ogg", ".oga": "audio/ogg",
    ".opus": "audio/ogg", ".m4a": "audio/mp4", ".aac": "audio/aac", ".spx": "audio/ogg",
    ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif",
    ".svg": "image/svg+xml", ".webp": "image/webp", ".bmp": "image/bmp", ".ico": "image/x-icon",
    ".woff": "font/woff", ".woff2": "font/woff2", ".ttf": "font/ttf", ".otf": "font/otf",
    ".css": "text/css; charset=utf-8", ".js": "text/javascript",
}


def norm_key(ref: str) -> str:
    """资源路径统一成 mdd 里的写法：反斜杠开头、小写。"""
    ref = ref.replace("/", "\\").lstrip(".")
    if not ref.startswith("\\"):
        ref = "\\" + ref
    return ref.lower()


def clean_ref(ref: str) -> str:
    """去掉 sound:// file:// 之类的前缀和 ?#，解码 %xx。"""
    ref = (ref or "").strip()
    ref = re.sub(r"^(sound|file|mdd|mdict)://", "", ref, flags=re.I)
    ref = re.split(r"[?#]", ref, maxsplit=1)[0]
    return urllib.parse.unquote(ref)


def mime_of(ref: str) -> str:
    ext = Path(ref).suffix.lower()
    return MIME.get(ext) or mimetypes.guess_type(ref)[0] or "application/octet-stream"


def ref_path(ref: str) -> str:
    """资源引用 → 地址里的路径部分（正斜杠、逐段转义）。"""
    ref = clean_ref(ref).replace("\\", "/").lstrip("/")
    return "/".join(urllib.parse.quote(p) for p in ref.split("/") if p not in ("", ".", ".."))


class Resources:
    """一本 mdx 词典的附属资源。"""

    def __init__(self, mdx_path: str):
        self.dir = Path(mdx_path).resolve().parent
        self.stem = Path(mdx_path).stem
        self._mdds: list[tuple[object, list, dict[str, int]]] = []
        self._lock = threading.Lock()
        self.errors: list[str] = []
        for p in self._mdd_paths():
            try:
                from mdict_utils.base.readmdict import MDD
                md = MDD(str(p))
                keys = md._key_list
                index = {norm_key(k.decode("utf-8", "ignore")): i for i, (_o, k) in enumerate(keys)}
                self._mdds.append((md, keys, index))
            except Exception as e:
                self.errors.append(f"{p.name}: {e!r}")

    def _mdd_paths(self) -> list[Path]:
        out = []
        first = self.dir / f"{self.stem}.mdd"
        if first.exists():
            out.append(first)
        for i in range(1, 30):
            p = self.dir / f"{self.stem}.{i}.mdd"
            if not p.exists():
                break
            out.append(p)
        return out

    @property
    def has_mdd(self) -> bool:
        return bool(self._mdds)

    def get(self, ref: str) -> bytes | None:
        """取资源内容：先找词典目录里的散文件，再找 mdd。"""
        ref = clean_ref(ref)
        if not ref or re.match(r"^(https?:|data:)", ref, re.I):
            return None
        # 1. 散放的文件（只允许在词典目录里，防止 ../ 跳出去）
        try:
            p = (self.dir / ref.replace("\\", "/").lstrip("/")).resolve()
            if p.is_file() and self.dir in p.parents:
                return p.read_bytes()
        except Exception:
            pass
        # 2. mdd
        key = norm_key(ref)
        with self._lock:
            for md, keys, index in self._mdds:
                i = index.get(key)
                if i is None:
                    continue
                try:
                    from mdict_utils import reader
                    offset, raw_key = keys[i]
                    length = keys[i + 1][0] - offset if i + 1 < len(keys) else -1
                    return reader.get_record(md, raw_key, offset, length)
                except Exception as e:
                    self.errors.append(f"{ref}: {e!r}")
        return None


# 只在选择器位置把 html/body 换成词条根节点（一次性全局替换，几十 KB 的样式表也是毫秒级）
_ROOT_RE = re.compile(r"(?<![\w.#-])(html|body)(?![\w-])(?=[^{}]*\{)", re.I)
_URL_RE = re.compile(r"""url\(\s*(['"]?)([^'")]+)\1\s*\)""", re.I)


def rewrite_css(text: str, url_for=None) -> str:
    """样式表改写：html/body 选择器 → .ww-root；url_for 给出时把相对 url() 换成资源地址。

    独立的 .css 文件由资源服务按原路径提供，里面的相对 url() 浏览器自己会解析，不用改。
    """
    text = _ROOT_RE.sub(".ww-root", text)
    if url_for is not None:
        def url(m):
            ref = m.group(2).strip()
            if ref.startswith(("data:", "http:", "https:", "#")):
                return m.group(0)
            return f'url("{url_for(ref)}")'
        text = _URL_RE.sub(url, text)
    return text


# ---------------- HTML 改写 ----------------
DROP_WITH_CONTENT = {"script", "iframe", "object", "embed", "noscript", "title", "head", "template"}
DROP_TAG = {"base", "meta", "link", "html", "body", "form", "input", "button", "frame", "frameset"}
VOID = {"area", "br", "col", "hr", "img", "source", "track", "wbr"}


class _Rewriter(HTMLParser):
    def __init__(self, url_for):
        super().__init__(convert_charrefs=False)
        self.url_for = url_for
        self.out: list[str] = []
        self.css_refs: list[str] = []
        self.skip = 0
        self.in_style = False

    def _attrs(self, tag: str, attrs) -> str:
        keep = []
        for name, value in attrs:
            name = name.lower()
            value = value or ""
            if name.startswith("on") or name in ("srcdoc", "formaction"):
                continue
            low = value.strip().lower()
            if low.startswith(("javascript:", "vbscript:")):
                continue
            if tag == "a" and name == "href":
                if low.startswith("sound://"):
                    keep += [("data-sound", clean_ref(value)), ("href", "#")]
                elif low.startswith(("entry://", "bword://", "d:")):
                    target = urllib.parse.unquote(re.sub(r"^\w+://", "", value).strip())
                    if target.startswith("#"):  # 同一词条内的锚点
                        keep.append(("href", target))
                    else:
                        keep += [("data-entry", target.split("#")[0]), ("href", "#")]
                elif low.startswith(("http://", "https://")):
                    keep += [("data-href", value), ("href", "#")]
                else:
                    keep.append(("href", value if value.startswith("#") else "#"))
                continue
            if name in ("src", "data-src") and tag in ("img", "image", "source", "audio"):
                if tag in ("source", "audio") and not low.startswith(("http", "data:")):
                    keep.append(("data-sound", clean_ref(value)))
                    continue
                if low.startswith(("http:", "https:", "data:")):
                    keep.append(("src", value))
                else:
                    keep += [("src", self.url_for(value)), ("loading", "lazy")]
                continue
            if name == "style" and "url(" in low:
                value = rewrite_css(value, self.url_for)
            keep.append((name, value))
        return "".join(f' {n}="{escape(v, quote=True)}"' for n, v in keep)

    def handle_starttag(self, tag, attrs):
        t = tag.lower()
        if t in DROP_WITH_CONTENT:
            self.skip += 1
            return
        if self.skip:
            return
        if t == "link":
            d = dict((k.lower(), v or "") for k, v in attrs)
            if "stylesheet" in d.get("rel", "").lower() and d.get("href"):
                self.css_refs.append(d["href"])
            return
        if t in DROP_TAG:
            return
        if t in ("audio", "source"):  # <audio src> 换成一个播放按钮
            a = self._attrs(t, attrs)
            if "data-sound" in a:
                self.out.append(f'<a href="#" class="ww-audio"{a}></a>')
            return
        if t == "style":
            self.in_style = True
        self.out.append(f"<{t}{self._attrs(t, attrs)}>")

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        t = tag.lower()
        if t in DROP_WITH_CONTENT and self.skip:
            self.skip -= 1

    def handle_endtag(self, tag):
        t = tag.lower()
        if t in DROP_WITH_CONTENT:
            self.skip = max(0, self.skip - 1)
            return
        if t == "style":
            self.in_style = False
        if self.skip or t in DROP_TAG or t in VOID or t == "audio":
            return
        self.out.append(f"</{t}>")

    def handle_data(self, data):
        if self.skip:
            return
        data = data.replace("\x00", "")
        if self.in_style:
            self.out.append(rewrite_css(data, self.url_for))
        else:
            self.out.append(escape(data, quote=False))

    def handle_entityref(self, name):
        if not self.skip:
            self.out.append(f"&{name};")

    def handle_charref(self, name):
        if not self.skip:
            self.out.append(f"&#{name};")

    def handle_comment(self, data):
        pass


def rewrite_html(html: str, url_for) -> tuple[str, list[str]]:
    """返回 (改写后的 HTML, 引用的样式表地址列表)。url_for(ref) -> 资源地址。"""
    p = _Rewriter(url_for)
    try:
        p.feed(html or "")
        p.close()
    except Exception:
        return escape(re.sub(r"<[^>]+>", " ", html or "")), []
    return "".join(p.out), [url_for(r) for r in p.css_refs]
