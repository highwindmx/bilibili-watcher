#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
BiliLiveWatcher 一键打包脚本（PyInstaller --onedir + 无控制台）

用法（在项目根目录）：
  .venv/Scripts/python.exe tools/build_exe.py

流程：
  1. 调 PyInstaller 按 tools/bili_live_watcher.spec 打包
  2. 把项目根的 config.json 复制进 dist/BiliLiveWatcher/（首发配置）
  3. 清掉上次测试遗留的 state.json / watcher.log，保证 dist 干净

产物：dist/BiliLiveWatcher/BiliLiveWatcher.exe（双击即托盘常驻，无命令行窗口）
分发：整个 dist/BiliLiveWatcher/ 文件夹打包带走即可，无需 Python/uv/bat。
"""
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DIST = os.path.join(ROOT, "dist", "BiliLiveWatcher")
SPEC = os.path.join(HERE, "bili_live_watcher.spec")


def main() -> int:
    py = sys.executable
    print("[1/3] PyInstaller 打包中（约 1 分钟）...")
    r = subprocess.run(
        [py, "-m", "PyInstaller", SPEC, "--noconfirm",
         "--distpath", os.path.join(ROOT, "dist"),
         "--workpath", os.path.join(ROOT, "build")],
        cwd=ROOT,
    )
    if r.returncode != 0 or not os.path.exists(os.path.join(DIST, "BiliLiveWatcher.exe")):
        print("[ERROR] 打包失败，请检查上方 PyInstaller 输出")
        return 1

    print("[2/3] 复制 config.json 到 dist ...")
    shutil.copy2(os.path.join(ROOT, "config.json"),
                 os.path.join(DIST, "config.json"))

    print("[3/3] 清理测试遗留的 state.json / watcher.log ...")
    for name in ("state.json", "watcher.log"):
        p = os.path.join(DIST, name)
        if os.path.exists(p):
            os.remove(p)

    print("[OK] 完成：%s" % os.path.join(DIST, "BiliLiveWatcher.exe"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
