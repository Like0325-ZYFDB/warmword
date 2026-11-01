# -*- coding: utf-8 -*-
"""暖词 · Windows 取词翻译助手 —— 程序入口。

运行：venv\\Scripts\\pythonw.exe main.py（或双击 启动.bat）
打包后的 exe 带 --capture-service 参数时充当取词服务子进程。
"""
import sys

if __name__ == "__main__":
    from warmword import win32

    win32.dpi_awareness()  # 必须在导入界面库之前
    if "--capture-service" in sys.argv:
        from warmword.capture import service
        service.main()
        raise SystemExit(0)
    from warmword.app import main
    main()
