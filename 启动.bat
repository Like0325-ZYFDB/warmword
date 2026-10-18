@echo off
chcp 65001 >nul
title 暖词 · 取词助手
cd /d %~dp0

if not exist venv\Scripts\pythonw.exe (
    echo 首次运行：正在创建运行环境并安装依赖（需要联网，约 1-3 分钟）...
    where python >nul 2>nul
    if errorlevel 1 (
        echo.
        echo 没有找到 Python。请先安装 Python 3.10 或更高版本：
        echo    https://www.python.org/downloads/
        echo 安装时记得勾选 "Add Python to PATH"。
        echo.
        pause
        exit /b 1
    )
    python -m venv venv
    if errorlevel 1 (
        echo 创建虚拟环境失败。
        pause
        exit /b 1
    )
    venv\Scripts\python.exe -m pip install --upgrade pip -i https://pypi.tuna.tsinghua.edu.cn/simple
    venv\Scripts\python.exe -m pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
    if errorlevel 1 (
        echo.
        echo 依赖安装失败，请检查网络后重试。
        pause
        exit /b 1
    )
    echo 环境准备完成。
)

if not exist config.json (
    if exist config.example.json copy config.example.json config.json >nul
)

start "" venv\Scripts\pythonw.exe app.py
exit
