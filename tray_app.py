#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
B 站开播提醒 —— 托盘主程序（uv + pystray + Pillow）

职责：
  1. 系统托盘常驻（粉底白字 "Bo" 图标，app.ico / tray.png）
  2. 后台 daemon 线程跑 watcher 轮询；配置改了可热重载，不用重启
  3. 右键菜单：设置 / 探测直播状态（内含「立即检查」与「听音频/看画面」
     播放，直播中/轮播中均可播）/ 暂停·恢复 / 打开日志 / 退出
  4. 设置窗口在独立线程里用 tkinter（settings_gui.py）

线程模型（Windows 下 pystray 的最稳组合）：
  主线程  -> pystray icon.run()（阻塞，Windows 允许非主线程但主线程最稳）
  线程 A  -> watcher 轮询循环（daemon）
  线程 B  -> 每次点「设置/探测」时新建，跑 tkinter mainloop（用完即销毁）
  通知弹窗/播放 -> watcher 内部各自 daemon 线程

依赖（由 启动.bat 用 uv 装进 .venv）：pystray, pillow
  .venv\\Scripts\\pythonw.exe tray_app.py
"""
from __future__ import annotations

import argparse
import os
import sys
import threading
import time
import traceback
import webbrowser

import pystray
from PIL import Image

import watcher

HERE = os.path.dirname(os.path.abspath(__file__))
# PyInstaller --onedir 打包后：只读资源（app.ico/tray.png）在 sys._MEIPASS
# （即 _internal/），可写数据（config/state/log）放 exe 旁的目录。
# 开发运行时两者都等于脚本目录，行为与原来一致。
if getattr(sys, "frozen", False):
    RES_DIR = getattr(sys, "_MEIPASS", HERE)
    APP_DIR = os.path.dirname(os.path.abspath(sys.executable))
else:
    RES_DIR = HERE
    APP_DIR = HERE
ICON_ICO = os.path.join(RES_DIR, "app.ico")
TRAY_PNG = os.path.join(RES_DIR, "tray.png")
CONFIG_PATH = os.path.join(APP_DIR, "config.json")


# ---------------------------------------------------------------------------
# 低完整性级别（Low IL）兼容补丁
# ---------------------------------------------------------------------------
# 用户实测崩溃（2026-11-08）：
#   PermissionError: [WinError 5] 拒绝访问。
#   pystray/_win32.py:_create_window -> ChangeWindowMessageFilterEx
#
# 根因：微软文档明确规定「进程 IL <= SECURITY_MANDATORY_LOW_RID 时，
#   ChangeWindowMessageFilterEx 返回 ERROR_ACCESS_DENIED」。
#   DSH workspace 的 trust mark 让 .venv/Scripts/pythonw.exe 继承了
#   "Mandatory Label\Low Mandatory Level"，于是 pystray 的 errcheck=_err
#   把这个失败当异常抛出，整个托盘程序崩溃。
#
# 该调用仅用于「提权运行时也能收到 WM_TASKBARCREATED（资源管理器重启后
#   自动重建图标）」，对托盘核心功能并非必需。低 IL 下安全地跳过即可。
# 参考：https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-changewindowmessagefilterex
_LOW_IL_COMPAT_FLAG = "_bili_low_il_compat_installed"


def install_low_il_compat(win32_module=None) -> None:
    """
    包装 pystray win32 后端的 ChangeWindowMessageFilterEx：
    仅当失败为 ERROR_ACCESS_DENIED(5) 时视为成功（返回 True），其它错误原样抛出。

    幂等（重复调用不会叠加包装）；win32_module 可注入，便于离线测试。
    """
    if win32_module is None:
        from pystray._util import win32 as win32_module
    fn = getattr(win32_module, "ChangeWindowMessageFilterEx", None)
    if fn is None or getattr(fn, _LOW_IL_COMPAT_FLAG, False):
        return

    def tolerant(hwnd, message, action, p):
        try:
            return fn(hwnd, message, action, p)
        except OSError as e:
            if getattr(e, "winerror", None) == 5:  # ERROR_ACCESS_DENIED (Low IL)
                return True
            raise

    setattr(tolerant, _LOW_IL_COMPAT_FLAG, True)
    win32_module.ChangeWindowMessageFilterEx = tolerant


def _config_abs() -> str:
    return os.path.abspath(CONFIG_PATH)


class TrayApp:
    def __init__(self, config_path: str = None):
        self.config_path = os.path.abspath(config_path or CONFIG_PATH)
        self.lock = threading.RLock()
        self.cfg = watcher.load_config(self.config_path)
        self.state = watcher.load_state()
        self.debounce: dict = {}
        self.paused = False
        self.reloader = threading.Event()   # 设置窗口保存后置位，唤醒轮询
        self.stopper = threading.Event()
        self.last_check = None
        self.last_error = None
        self.live_count = 0
        self._settings_thread = None
        self.icon = None

    # ---- 图标 ----
    def load_icon_image(self) -> Image.Image:
        if os.path.exists(TRAY_PNG):
            return Image.open(TRAY_PNG).convert("RGBA")
        # 兜底：代码画一个 64x64 粉底白字（没有 tray.png 也不会崩）
        from PIL import ImageDraw, ImageFont
        im = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
        d = ImageDraw.Draw(im)
        d.rounded_rectangle((3, 3, 60, 60), radius=14, fill=(251, 114, 153, 255))
        try:
            f = ImageFont.truetype(r"C:\Windows\Fonts\seguibl.ttf", 38)
        except OSError:
            f = ImageFont.load_default()
        try:
            d.text((32, 32), "Bo", font=f, fill=(255, 255, 255, 255), anchor="mm")
        except TypeError:
            d.text((10, 12), "Bo", font=f, fill=(255, 255, 255, 255))
        return im

    # ---- 配置热重载 ----
    def reload_config(self) -> None:
        with self.lock:
            try:
                new_cfg = watcher.load_config(self.config_path)
            except Exception as e:  # noqa: BLE001
                watcher.log("配置重载失败（保持旧配置）: %s" % e)
                return
            self.cfg = new_cfg
            # 新增/删除博主时，重置去抖计数但保留已知状态（避免重复开播提醒）
            self.debounce = {k: v for k, v in self.debounce.items()
                             if k in new_cfg["uids"]}
            watcher.log("配置已热重载：%d 个博主" % len(new_cfg["uids"]))
        self.reloader.set()
        self._refresh_menu()

    # ---- 轮询线程 ----
    def poll_loop(self) -> None:
        backoff = 0
        while not self.stopper.is_set():
            if self.paused:
                self.reloader.wait(timeout=2)
                self.reloader.clear()
                continue
            try:
                with self.lock:
                    cfg = self.cfg
                    interval = int(cfg.get("interval", 180))
                self.state = watcher.poll_once(cfg, self.state, self.debounce)
                watcher.save_state(self.state)
                with self.lock:
                    self.last_check = time.strftime("%H:%M:%S")
                    self.last_error = None
                    self.live_count = sum(
                        1 for u in cfg["uids"]
                        if int((self.state.get("users", {}).get(str(u)) or {}).get("status", 0)) == 1
                    )
                backoff = 0
                self._refresh_menu()
            except KeyboardInterrupt:
                break
            except Exception as e:  # noqa: BLE001
                backoff = min(backoff + 1, 6)
                with self.lock:
                    self.last_error = "%s: %s" % (type(e).__name__, e)
                watcher.log("轮询出错：%s" % self.last_error)
                self._refresh_menu()
            # 可被「立即检查 / 热重载 / 退出」打断的等待
            self.reloader.wait(timeout=max(5, interval))
            self.reloader.clear()

    def check_now(self) -> None:
        self.reloader.set()

    # ---- 菜单 ----
    def _refresh_menu(self) -> None:
        if self.icon is not None:
            try:
                self.icon.update_menu()
            except Exception:  # noqa: BLE001
                pass

    def _status_text(self, item=None) -> str:
        with self.lock:
            if self.paused:
                return "状态：已暂停"
            if self.last_error:
                return "状态：出错（%s）" % self.last_error[:28]
            if self.last_check:
                return "状态：%d 人直播中 · %s 已检查" % (self.live_count, self.last_check)
            return "状态：启动中…"

    def _toggle_pause_text(self, item=None) -> str:
        return "▶ 恢复监控" if self.paused else "⏸ 暂停监控"

    def _pause_checked(self, item=None) -> bool:
        return self.paused

    def on_settings(self, icon=None, item=None) -> None:
        # 每次新开一个线程跑 tkinter；若上一个还活着就只把窗口提到前面
        if self._settings_thread and self._settings_thread.is_alive():
            try:
                import settings_gui
                settings_gui.bring_to_front()
            except Exception:  # noqa: BLE001
                pass
            return

        def runner():
            try:
                import settings_gui
                settings_gui.open_settings(
                    config_path=self.config_path,
                    on_saved=self.reload_config,
                    icon_path=ICON_ICO,
                )
            except Exception:  # noqa: BLE001
                watcher.log("设置窗口异常：\n" + traceback.format_exc())
            finally:
                self._settings_thread = None

        self._settings_thread = threading.Thread(target=runner, daemon=True)
        self._settings_thread.start()

    def on_toggle_pause(self, icon=None, item=None) -> None:
        self.paused = not self.paused
        watcher.log("监控已%s" % ("暂停" if self.paused else "恢复"))
        self.reloader.set()
        self._refresh_menu()

    def on_probe(self, icon=None, item=None) -> None:
        """
        「🔍 探测直播状态」：立刻查一次所有博主，弹汇总窗口列出状态，
        直播中/轮播中行可直接点「听音频 / 看画面」；窗口内「立即检查」
        按钮实时重查并原位刷新，同时顺带唤醒后台轮询。
        """
        def fetcher():
            """探测窗「立即检查」回调（工作线程）：实时探测 + 唤醒轮询。"""
            self.check_now()
            with self.lock:
                cfg = self.cfg
            try:
                return watcher.fetch_status(cfg["uids"], cfg), None, cfg
            except Exception as e:  # noqa: BLE001
                err = "%s: %s" % (type(e).__name__, e)
                watcher.log("探测刷新失败：%s" % err)
                return None, err, cfg

        def runner():
            try:
                import settings_gui
                with self.lock:
                    cfg = self.cfg
                # 实时查一次（不写 state、不触发通知），失败时回退到轮询缓存
                rows = None
                err = None
                try:
                    rows = watcher.fetch_status(cfg["uids"], cfg)
                except Exception as e:  # noqa: BLE001
                    err = "%s: %s" % (type(e).__name__, e)
                    watcher.log("探测请求失败（改用缓存状态）：%s" % err)
                    with self.lock:
                        users = dict(self.state.get("users", {}))
                    rows = {int(k): v for k, v in users.items()}
                settings_gui.open_probe_window(
                    cfg=cfg, rows=rows, error=err,
                    icon_path=ICON_ICO,
                    refresh_cb=fetcher,
                )
            except Exception:  # noqa: BLE001
                watcher.log("探测窗口异常：\n" + traceback.format_exc())

        threading.Thread(target=runner, daemon=True).start()

    def on_open_config(self, icon=None, item=None) -> None:
        """用系统默认程序打开 config.json（纯文本直接编辑的兜底方式）。"""
        try:
            os.startfile(self.config_path)  # noqa: S606 - 用户主动点击
        except Exception as e:  # noqa: BLE001
            watcher.log("打开配置失败：%s" % e)

    def on_open_log(self, icon=None, item=None) -> None:
        p = watcher.LOG_FILE  # 打包后为绝对路径（exe 目录），开发时同理
        if not os.path.exists(p):
            watcher.log("（暂无日志）")
        try:
            os.startfile(p)  # noqa: S606
        except Exception as e:  # noqa: BLE001
            watcher.log("打开日志失败：%s" % e)

    def on_about(self, icon=None, item=None) -> None:
        try:
            webbrowser.open("https://space.bilibili.com/48330353")
        except Exception:  # noqa: BLE001
            pass

    def on_quit(self, icon=None, item=None) -> None:
        watcher.log("退出托盘程序")
        self.stopper.set()
        self.reloader.set()
        # 解离：退出托盘不终止播放器进程。VLC 自己的窗口由用户自行关闭。
        if self.icon is not None:
            try:
                self.icon.stop()
            except Exception:  # noqa: BLE001
                pass

    def build_menu(self) -> pystray.Menu:
        # 「立即检查」「听直播」已整合进「探测直播状态」窗口
        return pystray.Menu(
            pystray.MenuItem(self._status_text, lambda i, it: None, enabled=False),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("⚙ 设置…", self.on_settings, default=True),
            pystray.MenuItem("🔍 探测直播状态…", self.on_probe),
            pystray.MenuItem(self._toggle_pause_text, self.on_toggle_pause,
                             checked=self._pause_checked),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("打开 config.json", self.on_open_config),
            pystray.MenuItem("打开日志", self.on_open_log),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("退出", self.on_quit),
        )

    def run(self) -> None:
        threading.Thread(target=self.poll_loop, daemon=True).start()
        watcher.log("托盘启动：%d 个博主，配置 %s" % (len(self.cfg["uids"]), self.config_path))
        self.icon = pystray.Icon(
            "BiliLiveWatcher",
            icon=self.load_icon_image(),
            title="B 站开播提醒",
            menu=self.build_menu(),
        )
        # 低完整性级别（workspace trust mark 继承）下 pystray 会因
        # ChangeWindowMessageFilterEx 的 WinError 5 崩溃，启动前装兼容补丁。
        install_low_il_compat()
        # Windows: run() 可安全地从主线程调用；设置窗口走独立 tkinter 线程
        self.icon.run()


def main() -> None:
    ap = argparse.ArgumentParser(description="B 站开播提醒（托盘）")
    ap.add_argument("--config", default=CONFIG_PATH, help="配置文件路径")
    ap.add_argument("--no-tray", action="store_true",
                    help="不显示托盘，只跑轮询（调试用，Ctrl+C 退出）")
    args = ap.parse_args()

    app = TrayApp(args.config)
    if args.no_tray:
        try:
            app.poll_loop()
        except KeyboardInterrupt:
            watcher.log("已停止（--no-tray）")
        return
    app.run()


if __name__ == "__main__":
    main()
