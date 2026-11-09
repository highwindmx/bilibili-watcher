#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
B 站博主开播提醒（零第三方依赖，Python 3.8+）

原理：定时轮询 B 站公开的「按 UID 批量查直播状态」接口，
当某博主状态从「未开播/轮播」变为「直播中」时，按该博主配置的渠道触发一次通知。
用 live_time（本场开播时间戳）去重，避免轮播/接口抖动造成重复提醒。

接口：
  状态  GET https://api.live.bilibili.com/room/v1/Room/get_status_info_by_uids?uids[]=xxx
        live_status: 0 未开播 / 1 直播中 / 2 轮播中
  流地址 GET https://api.live.bilibili.com/room/v1/Room/playUrl?cid=<room>&platform=web&otype=json
        （备用：xlive/web-room/v2/index/getRoomPlayInfo）
  注意：直播流直链强制校验 Referer，且带 expires，必须点击时现取。

渠道（可在 config.json 里按博主单独配置 channels）：
  desktop / sound / native_toast / open_browser / email / all / none

用法：
  python watcher.py                           # 按 config.json 常驻监控
  python watcher.py --once                    # 只查一次（自检 / 调试，不通知）
  python watcher.py --test-notify             # 立即发一条测试通知
  python watcher.py --play-room 1496449       # 手动打开播放弹窗（听音频/看画面，播放器独立窗口）
  python watcher.py --config other.json
"""
from __future__ import annotations

import argparse
import ctypes
import json
import os
import queue
import shutil
import smtplib
import ssl
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
import webbrowser
from datetime import datetime
from email.header import Header
from email.mime.text import MIMEText
from email.utils import formataddr

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------
API_STATUS = "https://api.live.bilibili.com/room/v1/Room/get_status_info_by_uids"
API_PLAYURL = "https://api.live.bilibili.com/room/v1/Room/playUrl"
API_ROOMPLAYINFO = "https://api.live.bilibili.com/xlive/web-room/v2/index/getRoomPlayInfo"
APP_NAME = "BiliLiveWatcher"
# 可写数据文件（config/state/log）的基目录：
#   开发运行 = 本文件所在目录；PyInstaller 打包后 = exe 所在目录（onedir 可写）。
# 只读资源（app.ico / tray.png）不在此处，由 tray_app / settings_gui 用
# RES_DIR（sys._MEIPASS）定位，打包时随 datas 收进 _internal。
if getattr(sys, "frozen", False):
    APP_DIR = os.path.dirname(os.path.abspath(sys.executable))
else:
    APP_DIR = os.path.dirname(os.path.abspath(__file__))
STATE_FILE = os.path.join(APP_DIR, "state.json")
LOG_FILE = os.path.join(APP_DIR, "watcher.log")
DEFAULT_CONFIG = os.path.join(APP_DIR, "config.json")

REFERER = "https://live.bilibili.com/"
USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
              "AppleWebKit/537.36 (KHTML, like Gecko) "
              "Chrome/124.0 Safari/537.36")
HEADERS = {"User-Agent": USER_AGENT, "Referer": REFERER}

ALL_CHANNELS = {"desktop", "sound", "native_toast", "open_browser", "email"}
LIVE_OFF, LIVE_ON, LIVE_ROUND = 0, 1, 2


def log(msg: str, *, also_print: bool = True) -> None:
    line = "[%s] %s" % (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), msg)
    if also_print:
        try:
            print(line, flush=True)
        except UnicodeEncodeError:  # 控制台为 GBK 且无 UTF-8 支持时
            print(line.encode("utf-8", "replace").decode("ascii", "replace"), flush=True)
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass


def room_url(room_id) -> str:
    return "https://live.bilibili.com/%s" % room_id if room_id else "https://live.bilibili.com/"


# ---------------------------------------------------------------------------
# 配置：streamers + 每博主 channels（兼容旧 uids + 全局开关）
# ---------------------------------------------------------------------------
def normalize_channels(raw) -> set | None:
    """把 channels 配置归一化成集合；返回 None 表示「用全局默认渠道」。"""
    if raw is None:
        return None
    if isinstance(raw, str):
        raw = [raw]
    out = set()
    for item in raw:
        c = str(item).strip().lower()
        if c in ("all", "*"):
            out |= ALL_CHANNELS
        elif c in ("none", "", "-"):
            pass
        elif c in ALL_CHANNELS:
            out.add(c)
        else:
            log("忽略未知通知渠道 %r（可用：%s）" % (item, ", ".join(sorted(ALL_CHANNELS))))
    return out


def load_config(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        cfg = json.load(f)
    cfg.setdefault("uids", [])
    cfg.setdefault("streamers", [])
    cfg.setdefault("interval", 180)
    cfg.setdefault("desktop", True)
    cfg.setdefault("sound", True)
    cfg.setdefault("native_toast", False)
    cfg.setdefault("open_browser", False)
    cfg.setdefault("notify_on_start", True)
    cfg.setdefault("notify_on_end", False)
    cfg.setdefault("debounce_polls", 1)
    cfg.setdefault("http_timeout", 10)
    cfg.setdefault("retries", 2)
    cfg.setdefault("email", {"enabled": False})
    cfg.setdefault("player", {})

    # 全局开关 = 默认渠道（旧配置 / --test-notify / 未写 channels 的博主都用它）
    defaults = set()
    if cfg.get("desktop", True):
        defaults.add("desktop")
    if cfg.get("sound", True):
        defaults.add("sound")
    if cfg.get("native_toast", False):
        defaults.add("native_toast")
    if cfg.get("open_browser", False):
        defaults.add("open_browser")
    if (cfg.get("email") or {}).get("enabled", False):
        defaults.add("email")
    cfg["default_channels"] = defaults

    mapping: dict[int, dict] = {}
    uids: list[int] = []
    if cfg["streamers"]:
        for s in cfg["streamers"]:
            uid = int(s["uid"])
            if uid in mapping:
                log("配置中 UID %s 重复，已合并" % uid)
            ch = normalize_channels(s.get("channels"))
            mapping[uid] = {
                "name": s.get("name") or "",
                "channels": defaults if ch is None else ch,
            }
            uids.append(uid)
    else:
        uids = [int(u) for u in cfg["uids"]]
        for uid in uids:
            mapping[uid] = {"name": "", "channels": set(defaults)}

    if not uids:
        raise ValueError("config.json 里 streamers / uids 不能为空，至少填一个 B 站 UID")
    cfg["_streamers"] = mapping
    cfg["uids"] = uids
    cfg["interval"] = max(30, int(cfg["interval"]))
    return cfg


def channels_for(uid: int, cfg: dict) -> set:
    entry = (cfg.get("_streamers") or {}).get(uid)
    if entry is None:
        return set(cfg.get("default_channels") or {"desktop", "sound"})
    return set(entry["channels"])


# ---------------------------------------------------------------------------
# 接口层
# ---------------------------------------------------------------------------
def build_url(uids: list[int]) -> str:
    # 形如 ?uids[]=1&uids[]=2 —— 与浏览器/官方文档一致
    q = "&".join("uids%%5B%%5D=%d" % u for u in uids)
    return "%s?%s" % (API_STATUS, q)


def http_get_json(url: str, cfg: dict) -> dict:
    ctx = ssl.create_default_context()
    headers = dict(HEADERS)
    # 可选 Cookie（config.json 的 "cookie" 字段）：轮播房间的流地址
    # 需要登录态才下发，直播中房间无 Cookie 也可用
    cookie = str((cfg or {}).get("cookie") or "").strip()
    if cookie:
        headers["Cookie"] = cookie
    req = urllib.request.Request(url, headers=headers)
    last_err = None
    retries = int(cfg.get("retries", 2))
    for attempt in range(1, retries + 2):
        try:
            with urllib.request.urlopen(req, timeout=cfg.get("http_timeout", 10), context=ctx) as resp:
                return json.load(resp)
        except Exception as e:  # noqa: BLE001 - 网络层统一重试
            last_err = e
            if attempt <= retries:
                time.sleep(2 * attempt)
    raise RuntimeError("请求失败（已重试）: %s: %s" % (type(last_err).__name__, last_err))


def fetch_status(uids: list[int], cfg: dict) -> dict[int, dict]:
    """返回 {uid: row}；网络/接口异常时抛出，由调用方做退避。"""
    payload = http_get_json(build_url(uids), cfg)
    if payload.get("code") != 0:
        raise RuntimeError("接口返回 code=%s msg=%s" % (payload.get("code"), payload.get("message")))
    data = payload.get("data") or {}
    return {int(k): v for k, v in data.items()}


def parse_playurl_payload(payload: dict) -> str:
    """从 Room/playUrl 返回值里提取直链（纯函数，便于测试）。"""
    if payload.get("code") != 0:
        raise ValueError("playUrl code=%s" % payload.get("code"))
    durl = ((payload.get("data") or {}).get("durl") or [])
    if not durl or not durl[0].get("url"):
        raise ValueError("playUrl 未返回 durl")
    return durl[0]["url"]


def parse_roomplayinfo_payload(payload: dict) -> str:
    """从 getRoomPlayInfo 返回值里拼出 FLV 直链（纯函数，便于测试）。"""
    if payload.get("code") != 0:
        raise ValueError("getRoomPlayInfo code=%s" % payload.get("code"))
    streams = (((payload.get("data") or {}).get("playurl_info") or {})
               .get("playurl") or {}).get("stream") or []
    for prefer_proto in ("http_stream", "http_hls"):
        for st in streams:
            if st.get("protocol_name") != prefer_proto:
                continue
            for fmt in st.get("format") or []:
                for codec in fmt.get("codec") or []:
                    for info in codec.get("url_info") or []:
                        host, base, extra = (info.get("host"), codec.get("base_url"), info.get("extra"))
                        if host and base and extra:
                            return host.rstrip("/") + base + extra
    raise ValueError("getRoomPlayInfo 未找到可用流地址")


def fetch_stream_url(room_id: int, cfg: dict) -> str:
    """点击「播放」时实时取直链（带 Referer 校验的短时效地址，不能缓存）。

    顺序：v2 getRoomPlayInfo（直播中房间可用，无需登录）→ 老 playUrl 兜底。
    注意：轮播中（live_status=2）的房间两者都可能被服务端拒绝——实测
    未登录时 v2 返回空 stream、playUrl 返回的 CDN 地址 404（2026-11 实测），
    拿轮播流需要登录态 Cookie（config.json 的 "cookie" 字段）。
    """
    errors = []
    try:
        p = http_get_json(
            "%s?room_id=%s&protocol=0,1&format=0,1,2&codec=0,1&qn=10000"
            "&platform=web&ptype=8&dolby=5&panorama=1" % (API_ROOMPLAYINFO, room_id), cfg)
        return parse_roomplayinfo_payload(p)
    except Exception as e:  # noqa: BLE001
        errors.append("getRoomPlayInfo: %s" % e)
    try:
        p = http_get_json("%s?cid=%s&qn=0&platform=web&otype=json" % (API_PLAYURL, room_id), cfg)
        return parse_playurl_payload(p)
    except Exception as e:  # noqa: BLE001
        errors.append("playUrl: %s" % e)
    raise RuntimeError(
        "获取直播流失败 -> %s。若该房间正在轮播：B 站对未登录请求不下发轮播流，"
        "可在 config.json 填 \"cookie\"（浏览器登录 Cookie）后重试"
        % " | ".join(errors))


# ---------------------------------------------------------------------------
# 播放器：自动探测 + 命令构造（Referer 防盗链）
# ---------------------------------------------------------------------------
COMMON_PATHS = {
    "vlc": [
        r"C:\Program Files\VideoLAN\VLC\vlc.exe",
        r"C:\Program Files (x86)\VideoLAN\VLC\vlc.exe",
        r"C:\Program Files\VideoLAN\VLC\vlc.exe",
    ],
    "mpv": [
        r"C:\Program Files\mpv\mpv.exe",
        r"C:\Program Files (x86)\mpv\mpv.exe",
        os.path.expandvars(r"%LOCALAPPDATA%\Programs\mpv\mpv.exe"),
        os.path.expandvars(r"%APPDATA%\mpv\mpv.exe"),
    ],
    "ffplay": [
        r"C:\Program Files\ffmpeg\bin\ffplay.exe",
        r"C:\ffmpeg\bin\ffplay.exe",
        os.path.expandvars(r"%LOCALAPPDATA%\Programs\ffmpeg\bin\ffplay.exe"),
    ],
}


def find_player(cfg: dict, which=None, exists=None):
    """返回 (kind, exe_path) 或 None。which/exists 可注入以便离线测试。"""
    which = which or shutil.which
    exists = exists or os.path.exists
    pc = cfg.get("player") or {}
    prefer = pc.get("prefer") or ["vlc", "mpv", "ffplay"]
    for kind in prefer:
        explicit = pc.get(kind + "_path")
        candidates = ([explicit] if explicit else [])
        candidates += [which(kind), which(kind + ".exe")]
        candidates += COMMON_PATHS.get(kind, [])
        for c in candidates:
            if c and exists(c):
                return (kind, c)
    return None


def build_player_cmd(kind: str, exe: str, url: str, audio_only: bool, cfg: dict = None) -> list:
    """构造播放命令（纯函数）。所有播放器都必须带 Referer + UA，否则 B 站 CDN 返回 403。"""
    if kind == "vlc":
        # 不用 --play-and-exit：直播流断开/打开失败时 VLC 窗口会停留并显示
        # 错误，而不是静默闪退（用户曾因 404 流 + play-and-exit 以为「闪退」）
        cmd = [exe, "--no-one-instance", "--no-video-title-show"]
        if audio_only:
            # 只禁用视频画面，但【保留 VLC 自己的 GUI 窗口】：
            # 用户可在 VLC 窗口里暂停/继续/调音量/关闭，Python 弹窗不复制这些控制。
            # （早期版本的 --intf dummy 是完全无窗口，已按「播放与弹窗解离」移除）
            cmd.append("--no-video")
        cmd += ["--http-referrer=" + REFERER, "--http-user-agent=" + USER_AGENT,
                url, ":http-referrer=" + REFERER, ":http-user-agent=" + USER_AGENT]
        return cmd
    if kind == "mpv":
        cmd = [exe, "--no-terminal", "--force-window=no", "--cache=yes",
               "--referrer=" + REFERER, "--user-agent=" + USER_AGENT]
        if audio_only:
            cmd.append("--no-video")
        cmd.append(url)
        return cmd
    if kind == "ffplay":
        hdr = "Referer: %s\r\nUser-Agent: %s\r\n" % (REFERER, USER_AGENT)
        cmd = [exe, "-autoexit", "-headers", hdr]
        if audio_only:
            cmd.append("-nodisp")
        cmd.append(url)
        return cmd
    raise ValueError("未知播放器类型: %r" % kind)


class PlaybackController:
    """
    全局播放登记（与弹窗解离）。

    设计变更（用户要求「VLC 播放与弹窗解离」）：
      - 播放器保留自己的 GUI 窗口，暂停/继续/音量/关闭都由播放器窗口负责；
        通知弹窗【不再】持有播放 token、不再在关窗时 terminate 进程。
      - 本类只保留两个职责：① 登记当前播放进程；② 开新播放时顶替旧播放
        （防止多个直播间音频叠加）。
      - stop() 仅供「退出程序」或「显式停止」调用，弹窗不会主动调它。
    """

    def __init__(self):
        self.lock = threading.Lock()
        self.proc = None
        self.kind = None
        self.mode = None
        self.room_id = None
        self._status = "idle"  # idle / loading / playing
        self._token = 0

    def status(self) -> str:
        with self.lock:
            if self._status == "loading":
                return "loading"
            if self.proc is not None and self.proc.poll() is None:
                return "playing"
            return "idle"

    def is_active(self) -> bool:
        return self.status() == "playing"

    def begin_token(self) -> int:
        """预占下一个播放 token，供弹窗精确判断「关窗时该停哪一路」。"""
        with self.lock:
            return self._token + 1

    @staticmethod
    def _terminate(proc) -> None:
        if proc is None or proc.poll() is not None:
            return
        try:
            proc.terminate()
            proc.wait(timeout=2)
        except Exception:  # noqa: BLE001
            try:
                proc.kill()
            except Exception:  # noqa: BLE001
                pass

    def start(self, room_id: int, mode: str, cfg: dict, on_state=None, on_error=None) -> None:
        emit = on_state or (lambda m: log(m))
        fail = on_error or (lambda m: log(m))
        with self.lock:
            self._token += 1
            token = self._token
            old = self.proc
            self.proc = None
            self._status = "loading"
            self.room_id = room_id
            self.mode = mode
        self._terminate(old)

        def work():
            try:
                url = fetch_stream_url(room_id, cfg)
            except Exception as e:  # noqa: BLE001
                with self.lock:
                    if token == self._token:
                        self._status = "idle"
                fail("获取直播流失败：%s（已改为打开网页）" % e)
                try:
                    webbrowser.open(room_url(room_id))
                except Exception:  # noqa: BLE001
                    pass
                return

            player = find_player(cfg)
            if not player:
                with self.lock:
                    if token == self._token:
                        self._status = "idle"
                emit("未找到本地播放器（VLC/mpv/ffplay），已打开直播间网页")
                try:
                    webbrowser.open(room_url(room_id))
                except Exception:  # noqa: BLE001
                    pass
                return

            kind, exe = player
            cmd = build_player_cmd(kind, exe, url, audio_only=(mode == "audio"), cfg=cfg)
            try:
                # 不加 CREATE_NO_WINDOW：VLC 必须显示自己的 GUI 窗口，
                # 用户靠那个窗口暂停/继续/关闭。stdout/stderr 仍丢弃避免弹控制台。
                proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL,
                                        stderr=subprocess.DEVNULL)
            except Exception as e:  # noqa: BLE001
                with self.lock:
                    if token == self._token:
                        self._status = "idle"
                fail("启动播放器失败：%s（已改为打开网页）" % e)
                try:
                    webbrowser.open(room_url(room_id))
                except Exception:  # noqa: BLE001
                    pass
                return

            with self.lock:
                if token != self._token:  # 已被更新的播放请求取代
                    self._terminate(proc)
                    return
                self.proc, self.kind, self.mode = proc, kind, mode
                self._status = "idle"  # status() 依据 proc.poll() 判断 playing
            emit("已启动%s（%s）；请在播放器窗口里暂停/继续/关闭" % (
                "音频播放" if mode == "audio" else "视频播放", kind.upper()))

        threading.Thread(target=work, daemon=True).start()

    def stop(self, token: int = None) -> None:
        """停止播放；若传 token，则只停该 token 对应的那一路（避免误停别的弹窗）。"""
        with self.lock:
            if token is not None and token != self._token:
                return
            self._token += 1
            self._status = "idle"
            proc = self.proc
            self.proc = None
            self.room_id = None
        self._terminate(proc)


PLAYBACK = PlaybackController()


def play_room(room_id: int, mode: str, cfg: dict, on_state=None, on_error=None) -> None:
    """
    统一的「播放某房间」入口（与弹窗解离）。

    所有 UI（开播弹窗 / 托盘 / 探测汇总窗口）都调这里，不再各自持有 PLAYBACK 状态。
    播放器独立窗口自行负责暂停/继续/关闭；本函数只负责取流 + 启动 + 顶替旧播放。
    """
    PLAYBACK.start(room_id, mode, cfg, on_state=on_state, on_error=on_error)


# ---------------------------------------------------------------------------
# 状态持久化与判定
# ---------------------------------------------------------------------------
def load_state() -> dict:
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {"first_run": True, "users": {}}


def save_state(state: dict) -> None:
    tmp = STATE_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)
    os.replace(tmp, STATE_FILE)


def fmt_time(ts) -> str:
    try:
        return datetime.fromtimestamp(int(ts)).strftime("%Y-%m-%d %H:%M:%S")
    except (ValueError, TypeError, OSError):
        return "未知"


def evaluate(uid: int, row: dict, prev: dict | None, first_run: bool,
             confirmed_on: bool, cfg: dict) -> tuple[str | None, dict]:
    """纯判定函数（无副作用）：返回 (action, new_prev)。"""
    status = int(row.get("live_status", LIVE_OFF))
    live_time = row.get("live_time", 0)
    title = row.get("title", "")
    room_id = row.get("room_id", 0)
    uname = row.get("uname", "UID %s" % uid)
    was_on = bool(prev and prev.get("status") == LIVE_ON)
    action = None

    if first_run:
        if status == LIVE_ON and confirmed_on and cfg.get("notify_on_start", True):
            action = "start"
    else:
        if status == LIVE_ON and not was_on and confirmed_on:
            if int(live_time or 0) != int((prev or {}).get("live_time", 0)):
                action = "start"
        elif status in (LIVE_OFF, LIVE_ROUND) and was_on:
            if cfg.get("notify_on_end", False):
                action = "end"

    return action, {
        "status": status, "live_time": live_time, "title": title,
        "room_id": room_id, "uname": uname,
    }


# ---------------------------------------------------------------------------
# 通知层
# ---------------------------------------------------------------------------
def play_sound() -> None:
    try:
        import winsound
        for _ in range(2):
            winsound.MessageBeep(winsound.MB_ICONEXCLAMATION)
            time.sleep(0.15)
    except Exception as e:  # noqa: BLE001
        log("提示音失败: %s" % e)


def _tk_popup(title: str, body: str, url: str, room_id: int, cfg: dict,
              duration_ms: int | None = 20000) -> None:
    """
    置顶开播通知弹窗（与播放解离）。

    点击「听音频 / 看画面」只负责启动播放器；播放器用自己的 GUI 窗口播放，
    暂停/继续/音量/关闭都在那个窗口里操作。**本弹窗可以随时关掉，音频继续**，
    不再持有播放 token、不再在关窗时 stop 任何进程。
    在调用方线程运行（desktop_popup 会放进 daemon 线程）。
    """
    import tkinter as tk

    root = tk.Tk()
    root.title(title)
    root.attributes("-topmost", True)
    root.resizable(False, False)
    try:  # 让任务栏也高亮
        ctypes.windll.user32.FlashWindow(
            ctypes.windll.kernel32.GetConsoleWindow(), True)
    except Exception:  # noqa: BLE001
        pass

    w, h = 480, 268
    sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
    root.geometry("%dx%d+%d+%d" % (w, h, sw - w - 24, sh - h - 64))

    pad = {"padx": 16, "pady": 5}
    tk.Label(root, text="🔴  B 站开播提醒", font=("Microsoft YaHei UI", 14, "bold"),
             fg="#FB7299").pack(anchor="w", **pad)
    tk.Label(root, text=title, font=("Microsoft YaHei UI", 11, "bold"),
             wraplength=w - 32, justify="left").pack(anchor="w", **pad)
    tk.Label(root, text=body, font=("Microsoft YaHei UI", 9), fg="#555555",
             wraplength=w - 32, justify="left").pack(anchor="w", **pad)

    status_var = tk.StringVar(value="")
    q: queue.Queue = queue.Queue()
    auto = {"id": None}

    def cancel_auto():
        if auto["id"] is not None:
            try:
                root.after_cancel(auto["id"])
            except Exception:  # noqa: BLE001
                pass
            auto["id"] = None

    def begin(mode: str):
        cancel_auto()
        root.attributes("-topmost", False)
        status_var.set("正在获取直播流…")
        # 统一入口：取流 + 启动播放器；不做任何进程绑定，弹窗关闭不影响播放
        play_room(room_id, mode, cfg,
                  on_state=lambda m: q.put(m),
                  on_error=lambda m: q.put("❌ " + m))

    row1 = tk.Frame(root)
    row1.pack(fill="x", pady=(8, 2))
    tk.Button(row1, text="▶ 听音频", width=12,
              command=lambda: begin("audio")).pack(side="left", padx=(16, 4))
    tk.Button(row1, text="▶ 看画面", width=12,
              command=lambda: begin("video")).pack(side="left", padx=4)
    tk.Button(row1, text="打开网页", width=12,
              command=lambda: webbrowser.open(url)).pack(side="left", padx=4)

    row2 = tk.Frame(root)
    row2.pack(fill="x", pady=(2, 4))
    tk.Label(row2, textvariable=status_var, font=("Microsoft YaHei UI", 9),
             fg="#0078D7", wraplength=330, justify="left").pack(side="left", padx=16)

    def close():
        # 解离：关弹窗不停止任何播放进程。播放器窗口自己负责暂停/关闭。
        root.destroy()

    root.protocol("WM_DELETE_WINDOW", close)
    tk.Button(root, text="关闭（不影响播放）", width=18,
              command=close).pack(anchor="e", padx=16, pady=(0, 10))

    def pump():
        try:
            while True:
                status_var.set(q.get_nowait())
        except queue.Empty:
            pass
        root.after(400, pump)

    root.after(400, pump)
    if duration_ms:
        auto["id"] = root.after(duration_ms, close)
    root.mainloop()


def desktop_popup(title: str, body: str, url: str, room_id: int = 0,
                  cfg: dict = None, *, blocking: bool = False) -> None:
    args = (title, body, url, room_id, cfg or {})
    if blocking:
        _tk_popup(*args, duration_ms=None)
    else:
        threading.Thread(target=_tk_popup, args=args, daemon=True).start()


def native_toast(title: str, body: str, url: str) -> None:
    """可选：Windows 原生 Toast（PowerShell 5.1 + WinRT）。失败静默降级，不影响其它通道。"""
    ps1 = r'''
param([string]$T, [string]$B, [string]$U)
$ErrorActionPreference = 'Stop'
try {
    $AUMID = 'BiliLiveWatcher.App'
    $k = 'HKCU:\Software\Classes\AppUserModelId\' + $AUMID
    if (-not (Test-Path $k)) { New-Item -Path $k -Force | Out-Null }
    Set-ItemProperty -Path $k -Name DisplayName -Value 'Bili Live Watcher' -Force
    [void][Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime]
    [void][Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime]
    $tpl = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent(
        [Windows.UI.Notifications.ToastTemplateType]::ToastText02)
    $nodes = $tpl.GetElementsByTagName('text')
    $nodes.Item(0).AppendChild($tpl.CreateTextNode($T)) | Out-Null
    $nodes.Item(1).AppendChild($tpl.CreateTextNode($B)) | Out-Null
    $toast = [Windows.UI.Notifications.ToastNotification]::new($tpl)
    $n = [Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier($AUMID)
    $n.Show($toast)
    'OK'
} catch {
    Write-Error $_.Exception.Message
    exit 1
}
'''
    fd, path = tempfile.mkstemp(suffix=".ps1", prefix="bili_toast_")
    try:
        with os.fdopen(fd, "w", encoding="utf-8-sig", newline="\r\n") as f:
            f.write(ps1)
        exe = os.path.join(os.environ.get("WINDIR", r"C:\Windows"),
                           "System32", "WindowsPowerShell", "v1.0", "powershell.exe")
        if os.path.exists(exe):
            r = subprocess.run(
                [exe, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", path,
                 "-T", title, "-B", body, "-U", url],
                capture_output=True, text=True, timeout=15)
            if r.returncode != 0:
                log("原生 Toast 失败（将忽略）: %s" % (r.stderr.strip() or r.stdout.strip()))
    except Exception as e:  # noqa: BLE001
        log("原生 Toast 异常（将忽略）: %s" % e)
    finally:
        try:
            os.remove(path)
        except OSError:
            pass


def send_email(subject: str, body: str, url: str, cfg: dict) -> None:
    e = cfg.get("email") or {}
    if not e.get("enabled"):
        return
    host, port, user, pwd = e.get("smtp_host"), int(e.get("smtp_port", 465)), e.get("username"), e.get("password")
    to = e.get("to") or user
    if not (host and user and pwd and to):
        log("邮件配置不完整，跳过发送（需 smtp_host/username/password/to）")
        return
    msg = MIMEText("%s\n\n直播间：%s\n\n%s" % (body, url, datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
                   "plain", "utf-8")
    msg["Subject"] = Header(subject, "utf-8")
    msg["From"] = formataddr((str(Header(APP_NAME, "utf-8")), user))
    msg["To"] = ", ".join(to if isinstance(to, list) else [to])
    try:
        if e.get("use_ssl", True):
            server = smtplib.SMTP_SSL(host, port, timeout=15, context=ssl.create_default_context())
        else:
            server = smtplib.SMTP(host, port, timeout=15)
            if e.get("use_starttls", False):
                server.starttls(context=ssl.create_default_context())
        with server:
            server.login(user, pwd)
            server.sendmail(user, to if isinstance(to, list) else [to], msg.as_string())
        log("邮件已发送 -> %s" % to)
    except Exception as ex:  # noqa: BLE001
        log("邮件发送失败: %s: %s" % (type(ex).__name__, ex))


def display_name(uid: int, row: dict, cfg: dict) -> str:
    """昵称兜底：接口 uname → 配置里 name → UID。B 站偶尔返回空 uname。"""
    entry = (cfg.get("_streamers") or {}).get(uid) or {}
    return (row.get("uname") or entry.get("name") or ("UID %s" % uid))


def notify(action: str, row: dict, uid: int, cfg: dict, *, quiet: bool = False) -> None:
    """按该博主配置的 channels 分发通知。签名保持不变，渠道在内部解析。"""
    uname = display_name(uid, row, cfg)
    title = row.get("title") or "（无标题）"
    room = row.get("room_id") or 0
    url = room_url(room)
    live_time = fmt_time(row.get("live_time"))

    if action == "start":
        subject = "🔴 %s 开播了" % uname
        body = "%s\n分区/标题：%s\n开播时间：%s\n房间号：%s" % (subject, title, live_time, room)
    else:
        subject = "⚪ %s 下播了" % uname
        body = "%s\n标题：%s\n本场开播：%s" % (subject, title, live_time)

    channels = channels_for(uid, cfg)
    log("触发通知 [%s] %s | 渠道=%s | %s" % (
        action, subject, ",".join(sorted(channels)) or "(无)", url))
    if quiet or not channels:
        return

    if "desktop" in channels:
        desktop_popup(subject, body, url, room, cfg)
    if "sound" in channels:
        threading.Thread(target=play_sound, daemon=True).start()
    if "native_toast" in channels:
        threading.Thread(target=native_toast, args=(subject, body, url), daemon=True).start()
    if "open_browser" in channels and action == "start":
        try:
            webbrowser.open(url)
        except Exception:  # noqa: BLE001
            pass
    if "email" in channels:
        send_email(subject, body, url, cfg)


# ---------------------------------------------------------------------------
# 主循环
# ---------------------------------------------------------------------------
def poll_once(cfg: dict, state: dict, debounce: dict, *, quiet: bool = False) -> dict:
    uids = cfg["uids"]
    rows = fetch_status(uids, cfg)
    need = int(cfg.get("debounce_polls", 1))
    first_run = bool(state.get("first_run", True))
    users = state.setdefault("users", {})
    new_users = {}

    for uid in uids:
        row = rows.get(uid)
        if not row:
            log("UID %s 未在接口返回中（可能 UID 无效/无直播间）" % uid)
            if str(uid) in users:
                new_users[str(uid)] = users[str(uid)]
            continue
        prev = users.get(str(uid))
        raw_status = int(row.get("live_status", LIVE_OFF))
        confirmed = status_confirmed(row, debounce, uid, need)
        if raw_status == LIVE_ON and not confirmed:
            # 未达确认阈值：保持旧状态，不推进状态机，否则会提前消费「开播跳变」
            new_users[str(uid)] = prev or {
                "status": LIVE_OFF, "live_time": 0, "title": "",
                "room_id": row.get("room_id", 0),
                "uname": row.get("uname", "UID %s" % uid),
            }
            continue
        action, new_prev = evaluate(uid, row, prev, first_run, confirmed, cfg)
        if action:
            notify(action, row, uid, cfg, quiet=quiet)
        new_users[str(uid)] = new_prev

    state["users"] = new_users
    state["first_run"] = False
    return state


def status_confirmed(row: dict, debounce: dict, uid: int, need: int) -> bool:
    """连续 need 次看到 live_status==1 才认定开播（过滤抖动/轮播）。"""
    if int(row.get("live_status", LIVE_OFF)) == LIVE_ON:
        debounce[uid] = debounce.get(uid, 0) + 1
        return debounce[uid] >= need
    debounce[uid] = 0
    return True  # 下播无需确认，立即生效


def main() -> None:
    ap = argparse.ArgumentParser(description="B 站博主开播提醒")
    ap.add_argument("--config", default=DEFAULT_CONFIG, help="配置文件路径")
    ap.add_argument("--once", action="store_true", help="只查询一次并打印状态（不通知）")
    ap.add_argument("--test-notify", action="store_true", help="立即发一条测试通知")
    ap.add_argument("--play-room", type=int, metavar="ROOM",
                    help="手动打开播放弹窗（听音频/看画面，播放器独立窗口），不改配置也能播任意直播间")
    args = ap.parse_args()

    cfg = load_config(args.config)

    if args.play_room:
        log("手动播放弹窗：房间 %s" % args.play_room)
        desktop_popup("▶ 手动播放：房间 %s" % args.play_room,
                      "点「听音频」直连直播音频流；点「看画面」打开视频。\n"
                      "播放器会用独立窗口打开，暂停/继续/关闭都在那个窗口操作；本弹窗可随时关掉，不影响播放。",
                      room_url(args.play_room), args.play_room, cfg, blocking=True)
        return

    if args.test_notify:
        log("发送测试通知（渠道=%s）" % ",".join(sorted(cfg["default_channels"])))
        notify("start",
               {"uname": "测试博主", "title": "这是一条测试通知",
                "room_id": 1496449, "live_time": int(time.time())},
               0, cfg)
        return

    log("启动监控：%d 个博主，间隔 %s 秒；日志 %s；配置 %s" % (
        len(cfg["uids"]), cfg["interval"], LOG_FILE, os.path.abspath(args.config)))

    if args.once:
        try:
            rows = fetch_status(cfg["uids"], cfg)
        except Exception as e:  # noqa: BLE001
            log("查询失败: %s" % e)
            sys.exit(1)
        for uid in cfg["uids"]:
            row = rows.get(uid)
            ch = ",".join(sorted(channels_for(uid, cfg))) or "(无)"
            if row:
                log("UID %s | %s | live_status=%s | title=%s | room=%s | live_time=%s | channels=%s" % (
                    uid, display_name(uid, row, cfg), row.get("live_status"), row.get("title"),
                    row.get("room_id"), fmt_time(row.get("live_time")), ch))
            else:
                log("UID %s | 无返回（UID 可能无效） | channels=%s" % (uid, ch))
        return

    state = load_state()
    debounce: dict = {}
    backoff = 0
    while True:
        try:
            state = poll_once(cfg, state, debounce)
            save_state(state)
            backoff = 0
        except KeyboardInterrupt:
            log("已手动停止")
            break
        except Exception as e:  # noqa: BLE001
            backoff = min(backoff + 1, 6)
            wait = min(cfg["interval"] * backoff, 900)
            log("本轮出错：%s: %s ；%s 秒后重试" % (type(e).__name__, e, wait))
        time.sleep(cfg["interval"])


if __name__ == "__main__":
    main()
