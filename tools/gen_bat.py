#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
重新生成 启动.bat / 调试.bat（严格 GBK + CRLF，纯 ASCII 内容）。

按 win-script-encoding 规范：不用 Write 工具直接写 .bat，用 Python 显式控制字节。
修改 bat 内容请改本脚本，再在项目根目录运行：python tools\\gen_bat.py

规则：
  - 内容纯 ASCII（中文说明放 README），彻底规避 GBK/UTF-8 乱码
  - 行尾 CRLF
  - 用 %~dp0 定位脚本自身目录，绝不依赖 %USERPROFILE%
  - 无 BOM
"""
import os

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)  # bat 写到项目根目录

# %~dp0 = 脚本自身目录（不依赖 %USERPROFILE%）
HEAD = r'''@echo off
setlocal
cd /d "%~dp0"

set "UV_CACHE_DIR=%~dp0.uv-cache"
set "VENV=%~dp0.venv"
set "PYW=%VENV%\Scripts\pythonw.exe"
set "PY=%VENV%\Scripts\python.exe"

where uv >nul 2>nul
if errorlevel 1 (
  echo [ERROR] uv not found in PATH. Install: https://docs.astral.sh/uv/
  echo Or add your uv.exe folder ^(e.g. .local\bin^) to PATH.
  pause
  exit /b 1
)

if not exist "%PYW%" (
  echo [SETUP] Creating venv with uv ...
  uv venv --python 3.14 "%VENV%"
  if errorlevel 1 goto :fail
)

"%PY%" -c "import pystray, PIL" >nul 2>nul
if errorlevel 1 (
  echo [SETUP] Installing pystray + pillow ...
  uv pip install --python "%PY%" pystray pillow
  if errorlevel 1 goto :fail
)

if not exist "app.ico" (
  echo [SETUP] Generating icon ...
  "%PY%" tools\build_icon.py
  if errorlevel 1 goto :fail
)
'''

SILENT = HEAD + r'''
echo [OK] Starting Bili Live Watcher ^(tray, no console^) ...
start "" "%PYW%" tray_app.py
exit /b 0

:fail
echo [ERROR] Setup failed. See messages above.
pause
exit /b 1
'''

DEBUG = HEAD + r'''
echo [OK] Starting Bili Live Watcher ^(console debug mode^) ...
echo Press Ctrl+C to stop.
"%PY%" tray_app.py --no-tray
echo [STOPPED]
pause
exit /b 0

:fail
echo [ERROR] Setup failed. See messages above.
pause
exit /b 1
'''


def main() -> None:
    for name, content in (("启动.bat", SILENT), ("调试.bat", DEBUG)):
        path = os.path.join(ROOT, name)
        data = content.replace("\n", "\r\n").encode("gbk")
        with open(path, "wb") as f:
            f.write(data)
        with open(path, "rb") as f:
            b = f.read()
        stray_lf = b.replace(b"\r\n", b"").count(b"\n")
        print("%s: %d bytes | stray_LF=%d | CRLF=%s | BOM=%s | ASCII-only=%s" % (
            name, len(b), stray_lf, b"\r\n" in b,
            b.startswith(b"\xef\xbb\xbf"), all(c < 128 for c in b)))
        assert stray_lf == 0 and all(c < 128 for c in b), "encoding check failed"


if __name__ == "__main__":
    main()
