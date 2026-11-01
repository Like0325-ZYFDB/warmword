# -*- coding: utf-8 -*-
"""本机资源服务：口令校验、界面文件、词典资源（样式表改写）、不能跳出目录。"""
import urllib.error
import urllib.request

import pytest

from warmword.server import ResourceServer
from test_dict_render import PNG, _make_dict
from warmword.dictionary import DictEngine


def _get(url):
    with urllib.request.urlopen(url, timeout=5) as r:
        return r.status, r.headers.get("Content-Type"), r.read()


@pytest.fixture()
def server(tmp_path):
    ui = tmp_path / "ui"
    ui.mkdir()
    (ui / "float.html").write_text("<p>浮窗</p>", encoding="utf-8")
    (tmp_path / "secret.txt").write_text("secret", encoding="utf-8")
    mdx = _make_dict(tmp_path)
    res_dir = mdx.parent
    (res_dir / "style.css").write_text("body .x { background: url(img/pic.png) }", encoding="utf-8")
    eng = DictEngine([(str(mdx), "牛津")])
    s = ResourceServer(ui, tmp_path / "voice", lambda: eng, log=lambda m: None)
    yield s, eng
    s.close()


def test_ui_and_token(server):
    s, _ = server
    status, ctype, body = _get(s.page("float"))
    assert status == 200 and "text/html" in ctype and "浮窗" in body.decode()
    with pytest.raises(urllib.error.HTTPError):
        _get(f"http://127.0.0.1:{s.port}/wrongtoken/ui/float.html")
    with pytest.raises(urllib.error.HTTPError):
        _get(f"{s.base}/ui/..%2Fsecret.txt")


def test_dictionary_resources(server):
    s, eng = server
    bid = eng.books[0].id
    status, ctype, body = _get(s.res_url(bid, "img/pic.png"))
    assert body == PNG and ctype == "image/png"
    _, ctype, body = _get(s.res_url(bid, "style.css"))
    assert "text/css" in ctype and b".ww-root .x" in body and b"url(img/pic.png)" in body
    with pytest.raises(urllib.error.HTTPError):
        _get(s.res_url("nobook", "img/pic.png"))
