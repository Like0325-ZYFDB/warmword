# -*- coding: utf-8 -*-
"""打包后的冒烟测试：用 --capture-service 启动 exe，检查就绪消息、取词回复和各级取词是否可用。

用法：python tests/smoke_frozen.py dist/WarmWord/WarmWord.exe
"""
import json
import subprocess
import sys

sys.stdout.reconfigure(encoding="utf-8")  # Windows 控制台默认 cp1252，打印中文会报错

exe = sys.argv[1]
reqs = "".join(json.dumps(r) + "\n" for r in (
    {"op": "word", "x": 100, "y": 100, "id": 7},
    {"op": "status", "id": 8},
))
p = subprocess.run([exe, "--capture-service"], input=reqs, capture_output=True,
                   text=True, encoding="utf-8", timeout=180)
print("stdout:", p.stdout)
print("stderr:", p.stderr)
lines = [json.loads(x) for x in p.stdout.splitlines() if x.strip().startswith("{")]
assert lines and lines[0].get("ready"), "取词服务没有发出就绪消息"
assert any(x.get("id") == 7 for x in lines), "取词请求没有带 id 的回复"
status = next(x for x in lines if x.get("id") == 8)["status"]
assert status["uia"], "打包版里系统文字接口不可用（可能漏了 comtypes 生成的接口）"
assert status["ocr"], f"打包版里没有可用的文字识别：{status.get('notes')}"
print("冒烟测试通过：", status)
