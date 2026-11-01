# -*- coding: utf-8 -*-
"""取词客户端：上一次超时的迟到回复不能被当成这一次的结果。"""
import sys
import textwrap

from glance.capture import client as capture_client

FAKE_SERVICE = textwrap.dedent('''
    import json, sys, time
    print(json.dumps({"ready": True}), flush=True)
    for line in sys.stdin:
        req = json.loads(line)
        if req["x"] == 1:
            time.sleep(1.0)          # 第一次请求很慢，客户端会先超时
        print(json.dumps({"word": "word%d" % req["x"], "id": req["id"]}), flush=True)
''')


def test_late_reply_is_dropped(tmp_path, monkeypatch):
    script = tmp_path / "fake_service.py"
    script.write_text(FAKE_SERVICE, encoding="utf-8")
    monkeypatch.setattr(capture_client.CaptureClient, "_command",
                        lambda self: [sys.executable, str(script)])
    c = capture_client.CaptureClient()
    try:
        assert c._call({"op": "word", "x": 1, "y": 0}, timeout=0.3) is None
        # 以前这里会拿到迟到的 word1
        assert c._call({"op": "word", "x": 2, "y": 0}, timeout=3)["word"] == "word2"
    finally:
        c.close()
