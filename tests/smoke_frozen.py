# -*- coding: utf-8 -*-
"""打包后的冒烟测试：用 --capture-service 启动 exe，发一个取词请求，检查回复和模型加载。

用法：python tests/smoke_frozen.py dist/WarmWord/WarmWord.exe
"""
import json
import subprocess
import sys

sys.stdout.reconfigure(encoding="utf-8")  # Windows 控制台默认 cp1252，打印中文会报错

exe = sys.argv[1]
req = json.dumps({"op": "word", "x": 100, "y": 100, "id": 7}) + "\n"
p = subprocess.run([exe, "--capture-service"], input=req, capture_output=True,
                   text=True, encoding="utf-8", timeout=180)
print("stdout:", p.stdout)
print("stderr:", p.stderr)
lines = [json.loads(x) for x in p.stdout.splitlines() if x.strip().startswith("{")]
assert lines and lines[0].get("ready"), "取词服务没有发出就绪消息"
assert any(x.get("id") == 7 for x in lines), "取词请求没有带 id 的回复"
assert "[warmup] ok" in p.stderr, "识别模型没加载成功（打包可能漏了 rapidocr 的模型文件）"
print("冒烟测试通过")
