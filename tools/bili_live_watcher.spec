# -*- mode: python ; coding: utf-8 -*-
"""
BiliLiveWatcher 打包配置（PyInstaller --onedir + 无控制台窗口）

用法（在项目根目录）：
  .venv/Scripts/python.exe -m PyInstaller tools/bili_live_watcher.spec --noconfirm

产物：dist/BiliLiveWatcher/BiliLiveWatcher.exe
  - 双击即启动托盘程序，全程无命令行窗口（console=False）
  - 整个 dist/BiliLiveWatcher/ 文件夹是分发单元，config.json/state.json/
    watcher.log 运行时读写在该文件夹内（exe 旁）
  - app.ico / tray.png 收进 _internal/（只读资源，代码经 sys._MEIPASS 定位）

注意：config.json 不打进包（可写文件不能进 _internal），首次分发后需把
项目根目录的 config.json 复制到 dist/BiliLiveWatcher/ 旁（build_exe.py 已做）。
"""
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(SPEC)))

a = Analysis(
    [os.path.join(ROOT, "tray_app.py")],
    pathex=[ROOT],
    binaries=[],
    datas=[
        (os.path.join(ROOT, "app.ico"), "."),
        (os.path.join(ROOT, "tray.png"), "."),
    ],
    hiddenimports=[
        # pystray 的 Windows 后端在 __init__ 里按平台动态选择，显式声明防漏
        "pystray._win32",
        "pystray._util.win32",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="BiliLiveWatcher",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,          # 关键：无命令行窗口
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=os.path.join(ROOT, "app.ico"),
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="BiliLiveWatcher",
)
