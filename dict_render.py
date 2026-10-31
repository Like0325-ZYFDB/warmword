# -*- coding: utf-8 -*-
"""词典原版排版：读取 .mdd 资源（发音、图片、样式），把词条 HTML 改写成能在界面里安全显示的样子。

和 GoldenDict 的做法一样：
- 同目录的 xxx.mdd / xxx.1.mdd … 以及散放在词典目录里的 css、图片都算资源
- <link rel=stylesheet> 收集成样式表（url() 里的图片、字体内嵌成 data URI）
- sound://… 改成 data-sound，点了由程序播放；entry://… 改成 data-entry，点了跳到那个词
- 去掉脚本、事件属性、iframe 等，词典 HTML 只当内容显示
"""
from __future__ import annotations

import base64
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
    ".css": "text/css",
}
MAX_INLINE = 1_500_000  # 单个图片/字体超过这个大小就不内嵌


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


class Resources:
    """一本 mdx 词典的附属资源。"""

    def __init__(self, mdx_path: str):
        self.dir = Path(mdx_path).resolve().parent
        self.stem = Path(mdx_path).stem
        self._mdds: list[tuple[object, list, dict[str, int]]] = []
        self._data_uri: dict[str, str | None] = {}
        self._css: dict[str, str] = {}
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

    def get(self, ref: str, base: str = "") -> bytes | None:
        """取资源内容：先找词典目录里的散文件，再找 mdd。base 是引用它的 css 的路径（处理相对路径）。"""
        ref = clean_ref(ref)
        if not ref or re.match(r"^(https?:|data:)", ref, re.I):
            return None
        if base and not ref.startswith(("/", "\\")):
            ref = str(Path(clean_ref(base)).parent / ref)
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

    def data_uri(self, ref: str, base: str = "") -> str | None:
        k = f"{base}|{ref}"
        if k not in self._data_uri:
            data = self.get(ref, base)
            if data is None or len(data) > MAX_INLINE:
                self._data_uri[k] = None
            else:
                self._data_uri[k] = f"data:{mime_of(clean_ref(ref))};base64,{base64.b64encode(data).decode()}"
        return self._data_uri[k]

    def css(self, ref: str, depth: int = 0) -> str:
        """取一个样式表，url() 内嵌、@import 展开、body/html 选择器改成词条根节点。"""
        if ref in self._css:
            return self._css[ref]
        data = self.get(ref)
        text = data.decode("utf-8", "ignore") if data else ""
        text = rewrite_css(text, self, base=clean_ref(ref), depth=depth)
        self._css[ref] = text
        return text


_URL_RE = re.compile(r"""url\(\s*(['"]?)([^'")]+)\1\s*\)""", re.I)
_IMPORT_RE = re.compile(r"""@import\s+(?:url\()?\s*['"]?([^'")\s;]+)['"]?\s*\)?[^;]*;""", re.I)
_SELECTOR_RE = re.compile(r"([^{}]+)\{")
# 只在选择器位置把 html/body 换成词条根节点：一次性全局替换，比逐段回调快两个数量级
_ROOT_RE = re.compile(r"(?<![\w.#-])(html|body)(?![\w-])(?=[^{}]*\{)", re.I)


def rewrite_css(text: str, res: Resources | None, base: str = "", depth: int = 0) -> str:
    if res is not None and depth < 3:
        text = _IMPORT_RE.sub(lambda m: res.css(m.group(1), depth + 1)
                              if not m.group(1).startswith("http") else "", text)

    # 先改选择器（此时文本还是原始 CSS，几十 KB 量级），再内联 url()。
    # 顺序反过来的话，内联出几 MB 的 base64 后还要跑一遍选择器正则，首次渲染会卡几分钟。
    text = _ROOT_RE.sub(".ww-root", text)

    if res is not None and depth < 3:
        def url(m):
            ref = m.group(2).strip()
            if ref.startswith(("data:", "http:", "https:", "#")):
                return m.group(0)
            uri = res.data_uri(ref, base)
            return f'url("{uri}")' if uri else "url()"
        text = _URL_RE.sub(url, text)
    return text


# ---------------- HTML 改写 ----------------
DROP_WITH_CONTENT = {"script", "iframe", "object", "embed", "noscript", "title", "head", "template"}
DROP_TAG = {"base", "meta", "link", "html", "body", "form", "input", "button", "frame", "frameset"}
VOID = {"area", "br", "col", "hr", "img", "source", "track", "wbr"}


class _Rewriter(HTMLParser):
    def __init__(self, res: Resources | None):
        super().__init__(convert_charrefs=False)
        self.res = res
        self.out: list[str] = []
        self.css_refs: list[str] = []
        self.skip = 0
        self.in_style = False
        self.has_sound = False

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
                    keep.append(("data-sound", clean_ref(value)))
                    keep.append(("href", "#"))
                    self.has_sound = True
                elif low.startswith(("entry://", "bword://", "d:")):
                    target = urllib.parse.unquote(re.sub(r"^\w+://", "", value).strip())
                    if target.startswith("#"):  # 同一词条内的锚点
                        keep.append(("href", target))
                    else:
                        keep.append(("data-entry", target.split("#")[0]))
                        keep.append(("href", "#"))
                elif low.startswith(("http://", "https://")):
                    keep.append(("data-href", value))
                    keep.append(("href", "#"))
                else:
                    keep.append(("href", value if value.startswith("#") else "#"))
                continue
            if name in ("src", "data-src") and tag in ("img", "image", "source", "audio"):
                if tag in ("source", "audio") and not low.startswith(("http", "data:")):
                    keep.append(("data-sound", clean_ref(value)))
                    self.has_sound = True
                    continue
                if low.startswith(("http:", "https:", "data:")):
                    keep.append(("src", value))
                elif self.res is not None:
                    uri = self.res.data_uri(value)
                    if uri:
                        keep.append(("src", uri))
                continue
            if name == "style" and self.res is not None and "url(" in low:
                value = rewrite_css(value, self.res)
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
            self.out.append(rewrite_css(data, self.res))
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


def rewrite_html(html: str, res: Resources | None) -> tuple[str, list[str], bool]:
    """返回 (改写后的 HTML, 引用的样式表列表, 是否含发音链接)。"""
    p = _Rewriter(res)
    try:
        p.feed(html or "")
        p.close()
    except Exception:
        return escape(re.sub(r"<[^>]+>", " ", html or "")), [], False
    return "".join(p.out), p.css_refs, p.has_sound
