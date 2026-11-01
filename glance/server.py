# -*- coding: utf-8 -*-
"""本机资源服务（只监听 127.0.0.1，地址里带随机口令）：

  /<口令>/ui/<文件>          界面页面、样式、脚本
  /<口令>/r/<词典id>/<路径>   词典 mdd 里的图片、样式表、字体、录音（按需读取，不再整段内嵌）
  /<口令>/v?t=文字&a=us       在线发音（有道，本地缓存）；拿不到返回 404，页面改用系统语音

页面全部从这里加载，词典样式表里的相对路径浏览器自己就能解析。
"""
from __future__ import annotations

import secrets
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .dictionary.resources import mime_of, rewrite_css
from .speech import online_voice


class ResourceServer:
    def __init__(self, ui_dir: Path, voice_dir: Path, get_engine, log=print):
        self.ui_dir = Path(ui_dir).resolve()
        self.voice_dir = Path(voice_dir)
        self.get_engine = get_engine
        self.log = log
        self.token = secrets.token_urlsafe(12)
        server = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                try:
                    server._handle(self)
                except (BrokenPipeError, ConnectionResetError):
                    pass
                except Exception as e:
                    server.log(f"[资源服务] {self.path[:120]} 出错 {e!r}")
                    try:
                        self.send_error(500)
                    except Exception:
                        pass

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.httpd.daemon_threads = True
        self.port = self.httpd.server_address[1]
        self.base = f"http://127.0.0.1:{self.port}/{self.token}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True, name="resources").start()

    def page(self, name: str) -> str:
        return f"{self.base}/ui/{name}.html"

    def res_url(self, bid: str, path: str) -> str:
        return f"{self.base}/r/{bid}/{path}"

    # ---------- 处理请求 ----------
    @staticmethod
    def _send(h, body: bytes, ctype: str, cache: bool = True) -> None:
        h.send_response(200)
        h.send_header("Content-Type", ctype)
        h.send_header("Content-Length", str(len(body)))
        h.send_header("Cache-Control", "max-age=86400" if cache else "no-store")
        h.end_headers()
        h.wfile.write(body)

    def _handle(self, h) -> None:
        url = urllib.parse.urlsplit(h.path)
        parts = url.path.lstrip("/").split("/", 2)
        if len(parts) < 2 or parts[0] != self.token:
            h.send_error(404)
            return
        kind, rest = parts[1], (parts[2] if len(parts) > 2 else "")
        if kind == "ui":
            p = (self.ui_dir / urllib.parse.unquote(rest)).resolve()
            if self.ui_dir not in p.parents or not p.is_file():
                h.send_error(404)
                return
            self._send(h, p.read_bytes(), mime_of(p.name) if p.suffix != ".html"
                       else "text/html; charset=utf-8", cache=False)
        elif kind == "r":
            bid, _, ref = rest.partition("/")
            engine = self.get_engine()
            book = engine.book_by_id(bid) if engine is not None else None
            if book is None or not hasattr(book, "resources"):
                h.send_error(404)
                return
            ref = urllib.parse.unquote(ref)
            data = book.resources().get(ref)
            if data is None:
                h.send_error(404)
                return
            if ref.lower().endswith(".css"):
                data = rewrite_css(data.decode("utf-8", "ignore")).encode("utf-8")
            self._send(h, data, mime_of(ref))
        elif kind == "v":
            q = urllib.parse.parse_qs(url.query)
            text = (q.get("t") or [""])[0]
            accent = (q.get("a") or ["us"])[0]
            data = online_voice(text, accent, self.voice_dir, self.log)
            if data is None:
                h.send_error(404)
                return
            self._send(h, data, "audio/mpeg")
        else:
            h.send_error(404)

    def close(self) -> None:
        try:
            self.httpd.shutdown()
        except Exception:
            pass
