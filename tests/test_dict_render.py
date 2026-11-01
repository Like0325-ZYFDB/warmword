# -*- coding: utf-8 -*-
"""词典原版排版：mdd 资源（样式、图片、发音）与 HTML 改写。"""
import base64

from mdict_utils import writer

from warmword.dictionary import DictEngine
from warmword.dictionary.resources import ref_path, rewrite_css, rewrite_html

PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==")
MP3 = b"ID3" + b"\x00" * 2000

ENTRY = ('<link rel="stylesheet" type="text/css" href="oald.css">'
         '<script>alert(1)</script>'
         '<span class="hw" onclick="steal()">hello</span>'
         '<a class="sound" href="sound://sound/uk/hello.mp3"><img src="img/pic.png"></a>'
         '<span class="x">Hello, how are you today?</span>'
         '<a href="entry://world#sense1">world</a>'
         '<a href="entry://#top">top</a>'
         '<a href="javascript:alert(1)">bad</a>'
         '<a href="https://example.com">web</a>'
         '<style>body .x > b { color: red; background: url(bg.png) }</style>'
         '<span>a &lt; b &amp; c</span>')


def url_for(ref):
    return "/r/book/" + ref_path(ref)


def _make_dict(tmp_path):
    res = tmp_path / "res"
    (res / "sound" / "uk").mkdir(parents=True)
    (res / "img").mkdir()
    (res / "img" / "pic.png").write_bytes(PNG)
    (res / "sound" / "uk" / "hello.mp3").write_bytes(MP3)
    dic = tmp_path / "dict"
    dic.mkdir()
    writer.pack(str(dic / "oald.mdd"), writer.pack_mdd_file(str(res)), is_mdd=True)
    src = tmp_path / "src.txt"
    src.write_text(f"hello\n{ENTRY}\n</>\n", encoding="utf-8")
    writer.pack(str(dic / "oald.mdx"), writer.pack_mdx_txt(str(src)), title="t")
    return dic / "oald.mdx"


def test_mdd_resources(tmp_path):
    mdx = _make_dict(tmp_path)
    eng = DictEngine([(str(mdx), "牛津")])
    entry = eng.lookup("hello")[0]
    res = eng.book(entry.source).resources()
    assert res.has_mdd and not res.errors
    assert res.get("sound://sound/uk/hello.mp3") == MP3
    assert res.get("/SOUND/UK/HELLO.MP3") == MP3            # 大小写、斜杠方向都不敏感
    assert res.get("img/pic.png") == PNG


def test_rewrite_html():
    html, css = rewrite_html(ENTRY, url_for)
    assert css == ["/r/book/oald.css"]
    assert "<script" not in html and "alert" not in html and "onclick" not in html
    assert 'data-sound="sound/uk/hello.mp3"' in html
    assert 'src="/r/book/img/pic.png"' in html and "base64" not in html
    assert 'data-entry="world"' in html and 'href="#top"' in html
    assert 'data-href="https://example.com"' in html and "javascript" not in html
    assert ".ww-root .x > b" in html and 'url("/r/book/bg.png")' in html
    assert "a &lt; b &amp; c" in html


def test_rewrite_css_selectors():
    css = rewrite_css("body, .hw { margin: 0 }\nhtml.dark .x { color: #fff }\n"
                      "@media (max-width: 9px) { body { margin: 0 } }\n.body-text { color: red }")
    assert ".ww-root, .hw" in css and ".ww-root.dark .x" in css
    assert "@media (max-width: 9px) { .ww-root { margin: 0 } }" in css
    assert ".body-text" in css


def test_ref_path():
    assert ref_path("sound://\\Sound\\UK\\a b.mp3") == "Sound/UK/a%20b.mp3"
    assert ref_path("../../etc/passwd") == "etc/passwd"


def test_path_traversal_blocked(tmp_path):
    mdx = _make_dict(tmp_path)
    (tmp_path / "secret.txt").write_text("secret", encoding="utf-8")
    res = DictEngine([(str(mdx), "牛津")]).books[0].resources()
    assert res.get("../secret.txt") is None
