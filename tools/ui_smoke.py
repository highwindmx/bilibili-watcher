#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
UI 布局冒烟：真实实例化设置窗与探测窗，验证关键控件是否映射在可视区内。

用法：.venv/Scripts/python.exe tools/ui_smoke.py
会短暂（约 2 秒）在屏幕上弹出两个窗口并自动关闭，输出几何检查结果 + 截图
到 tools/_ui_smoke_*.png。窗口置顶显示，勿动鼠标即可。
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import tkinter as tk

import settings_gui
import watcher


def geom(w):
    return {"mapped": bool(w.winfo_ismapped()), "x": w.winfo_x(), "y": w.winfo_y(),
            "w": w.winfo_width(), "h": w.winfo_height()}


def visible_in(win, w):
    """控件是否已映射且完整落在窗口客户区内（不被 pack 裁剪）。"""
    g = geom(w)
    wh = win.winfo_height()
    ww = win.winfo_width()
    ok = g["mapped"] and g["y"] >= 0 and (g["y"] + g["h"]) <= wh \
        and g["x"] >= 0 and (g["x"] + g["w"]) <= ww
    return ok, g, (ww, wh)


def screenshot(win, path):
    try:
        from PIL import ImageGrab
        x = win.winfo_rootx()
        y = win.winfo_rooty()
        ImageGrab.grab(bbox=(x, y, x + win.winfo_width(), y + win.winfo_height())).save(path)
        return True
    except Exception as e:  # noqa: BLE001
        print("  截图失败：%s" % e)
        return False


def main():
    for scaling in (1.0, 1.5):
        print("=== tk scaling %.2f（模拟 %d%% DPI）===" % (scaling, scaling * 100))
        run_once(scaling)
    print("[DONE]")


def run_once(scaling: float) -> None:
    tag = "%03d" % round(scaling * 100)
    icon = os.path.join(ROOT, "app.ico")
    cfg_path = os.path.join(ROOT, "config.json")

    probe = tk.Tk()
    probe.tk.call("tk", "scaling", scaling)
    probe.withdraw()
    probe.destroy()

    print("[1] 设置窗口")
    root = tk.Tk()
    root.tk.call("tk", "scaling", scaling)
    win = settings_gui.SettingsWindow(root, cfg_path, None, icon)
    root.attributes("-topmost", True)
    root.update_idletasks()
    root.update()
    n_bad = 0
    for name in ("btn_add", "btn_update", "btn_delete", "tree", "ed_uid"):
        ok, g, winsize = visible_in(root, getattr(win, name))
        if not ok:
            n_bad += 1
        print("  %-11s %s  geom=%s  win=%s" % (name, "OK " if ok else "OUT", g, winsize))
    screenshot(root, os.path.join(HERE, "_ui_smoke_settings_dpi%s.png" % tag))
    root.destroy()

    print("[2] 探测窗口（假数据：1 直播 + 1 轮播 + 1 未开播）")
    cfg = watcher.load_config(cfg_path)
    uid0 = cfg["uids"][0]
    rows = {uid0: {"uname": "测试轮播", "live_status": 2, "live_time": 1700000000,
                   "room_id": 555, "title": "轮播测试"},
            1: {"uname": "测试直播", "live_status": 1, "live_time": 1700000000,
                "room_id": 666, "title": "直播测试"}}
    table = settings_gui.probe_rows_to_table(cfg, rows)
    root2 = tk.Tk()
    root2.tk.call("tk", "scaling", scaling)
    win2 = settings_gui.ProbeWindow(root2, cfg, table, "", icon, refresh_cb=lambda: (rows, None, cfg))
    root2.attributes("-topmost", True)
    root2.update_idletasks()
    root2.update()
    for name in ("btn_refresh", "tree", "play_bar"):
        w = getattr(win2, name)
        ok, g, winsize = visible_in(root2, w)
        if not ok:
            n_bad += 1
        print("  %-11s %s  geom=%s  win=%s" % (name, "OK " if ok else "OUT", g, winsize))
    live_n = len(win2._live)
    print("  播放下拉可选（含轮播）：%d 个 -> %s" % (live_n, [win2._live_label(x) for x in win2._live]))
    screenshot(root2, os.path.join(HERE, "_ui_smoke_probe_dpi%s.png" % tag))
    root2.destroy()
    if n_bad:
        print("  !! %d 个控件溢出" % n_bad)


if __name__ == "__main__":
    main()
