#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
回归测试：Low 完整性级别进程下 pystray 的 ChangeWindowMessageFilterEx 不再崩溃。

背景（2026-11-08 用户实测崩溃）：
  PermissionError: [WinError 5] 拒绝访问。
  pystray/_win32.py:_create_window -> ChangeWindowMessageFilterEx
  微软文档：进程 IL <= SECURITY_MANDATORY_LOW_RID 时该 API 返回 ERROR_ACCESS_DENIED。
  DSH workspace 的 trust mark 把 Low IL 继承给了 .venv/Scripts/pythonw.exe。

本测试用一个会抛 WinError 5 的假实现驱动真实代码路径，不依赖真实 GUI/IL。
"""
import importlib.util
import os
import sys
import types

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

passed = failed = 0


def check(name, cond, extra=""):
    global passed, failed
    if cond:
        passed += 1
        print("  PASS  %s" % name)
    else:
        failed += 1
        print("  FAIL  %s  %s" % (name, extra))


def load_tray_app():
    spec = importlib.util.spec_from_file_location(
        "tray_app_under_test", os.path.join(ROOT, "tray_app.py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


print("\n[1] install_low_il_compat 存在且可调用")
m = load_tray_app()
check("函数存在", callable(getattr(m, "install_low_il_compat", None)))

print("\n[2] 注入后：ChangeWindowMessageFilterEx 遇 WinError 5 被吞掉")
fake_win32 = types.ModuleType("pystray._util.win32_fake")
calls = []


def raising(hwnd, message, action, p):
    calls.append((hwnd, message, action, p))
    e = OSError("拒绝访问。")
    e.winerror = 5
    raise e


fake_win32.ChangeWindowMessageFilterEx = raising
# install_low_il_compat 接受可注入的 win32 模块，便于离线测试
m.install_low_il_compat(win32_module=fake_win32)
try:
    r = fake_win32.ChangeWindowMessageFilterEx(123, 456, 1, None)
    check("WinError 5 不抛异常", True)
    check("返回 True（pystray errcheck 视为成功）", r is True, r)
    check("原实现仍被调用（没有屏蔽掉 API）", len(calls) == 1, calls)
except OSError as e:
    check("WinError 5 不抛异常", False, "raised %s" % e)

print("\n[3] 非 WinError 5 的错误仍应抛出（不能吞掉真正的错误）")
calls.clear()
fake2 = types.ModuleType("pystray._util.win32_fake2")


def raising_other(hwnd, message, action, p):
    e = OSError("参数错误")
    e.winerror = 87
    raise e


fake2.ChangeWindowMessageFilterEx = raising_other
m.install_low_il_compat(win32_module=fake2)
try:
    fake2.ChangeWindowMessageFilterEx(1, 2, 3, None)
    check("WinError 87 应原样抛出", False, "swallowed!")
except OSError as e:
    check("WinError 87 应原样抛出", getattr(e, "winerror", None) == 87,
          getattr(e, "winerror", None))

print("\n[4] 幂等：重复调用不会套多层包装")
fake3 = types.ModuleType("pystray._util.win32_fake3")
n = {"c": 0}


def counting(hwnd, message, action, p):
    n["c"] += 1
    e = OSError("拒绝访问。")
    e.winerror = 5
    raise e


fake3.ChangeWindowMessageFilterEx = counting
m.install_low_il_compat(win32_module=fake3)
m.install_low_il_compat(win32_module=fake3)
fake3.ChangeWindowMessageFilterEx(1, 2, 3, None)
check("底层只被调 1 次（无重复包装）", n["c"] == 1, n)

print("\n[5] 真实 pystray 模块上可安全安装（不抛）")
try:
    from pystray._util import win32 as real_win32
    m.install_low_il_compat(win32_module=real_win32)
    check("在真实 pystray win32 模块上安装成功", True)
except Exception as e:
    check("在真实 pystray win32 模块上安装成功", False, "%s: %s" % (type(e).__name__, e))

print("\n===== 结果：%d PASS / %d FAIL =====" % (passed, failed))
sys.exit(0 if failed == 0 else 1)
