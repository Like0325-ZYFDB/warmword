# -*- coding: utf-8 -*-
"""词典原版排版：mdd 资源（样式、图片、发音）与 HTML 改写。"""
import base64

from mdict_utils import writer

from dict_engine import DictEngine
from dict_render import rewrite_html

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
         '<style>body .x > b { color: red }</style>'
         '<span>a &lt; b &amp; c</span>')


def _make_dict(tmp_path):
    res = tmp_path / "res"
    (res / "sound" / "uk").mkdir(parents=True)
    (res / "img").mkdir()
    (res / "img" / "pic.png").write_bytes(PNG)
    (res / "icon.png").write_bytes(PNG)
    (res / "sound" / "uk" / "hello.mp3").write_bytes(MP3)
    (res / "oald.css").write_text('@import "extra.css";\nbody, .hw { background: url(icon.png); }\n'
                                  'html.dark .x { color: #fff }\n@media (max-width: 9px) { body { margin: 0 } }',
                                  encoding="utf-8")
    (res / "extra.css").write_text(".extra { color: blue }", encoding="utf-8")
    dic = tmp_path / "dict"
    dic.mkdir()
    writer.pack(str(dic / "oald.mdd"), writer.pack_mdd_file(str(res)), is_mdd=True)
    src = tmp_path / "src.txt"
    src.write_text(f"hello\n{ENTRY}\n</>\n", encoding="utf-8")
    writer.pack(str(dic / "oald.mdx"), writer.pack_mdx_txt(str(src)), title="t")
    return dic / "oald.mdx"


def test_mdd_resources_and_rewrite(tmp_path):
    mdx = _make_dict(tmp_path)
    eng = DictEngine([(str(mdx), "牛津")])
    entry = eng.lookup("hello")[0]
    assert entry.source == str(mdx)
    res = eng.book(entry.source).resources()
    assert res.has_mdd and not res.errors
    assert res.get("sound://sound/uk/hello.mp3") == MP3
    assert res.get("/SOUND/UK/HELLO.MP3") == MP3            # 大小写、斜杠方向都不敏感

    html, css_refs, has_sound = rewrite_html(entry.html, res)
    assert css_refs == ["oald.css"] and has_sound
    assert "<script" not in html and "alert" not in html and "onclick" not in html
    assert 'data-sound="sound/uk/hello.mp3"' in html
    assert 'src="data:image/png;base64,' in html
    assert 'data-entry="world"' in html and 'href="#top"' in html
    assert 'data-href="https://example.com"' in html and "javascript" not in html
    assert "body .x > b" not in html and ".ww-root .x > b" in html   # <style> 里的 body 也改写
    assert "a &lt; b &amp; c" in html

    css = res.css("oald.css")
    assert ".extra { color: blue }" in css                      # @import 展开
    assert 'url("data:image/png;base64,' in css                 # 图片内嵌
    assert ".ww-root, .hw" in css and ".ww-root.dark .x" in css
    assert "@media (max-width: 9px) { .ww-root { margin: 0 } }" in css


def test_path_traversal_blocked(tmp_path):
    mdx = _make_dict(tmp_path)
    (tmp_path / "secret.txt").write_text("secret", encoding="utf-8")
    res = DictEngine([(str(mdx), "牛津")]).books[0].resources()
    assert res.get("../secret.txt") is None


def test_no_mdd():
    html, refs, has_sound = rewrite_html('<a href="sound://x.mp3">▶</a><img src="a.png">', None)
    assert 'data-sound="x.mp3"' in html and has_sound and "<img>" in html
