#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
设置 GUI + 直播状态探测汇总窗口（tkinter）

由 tray_app.py 在独立线程里调用：
  open_settings()      —— 设置窗口（博主表格 / 全局 / 邮件 / 播放器）
  open_probe_window()  —— 「🔍 探测直播状态」汇总窗口

探测汇总窗口：一次列出所有博主（UID / 昵称 / 状态 / 房间 / 标题 / 开播时间 / 渠道），
直播中与轮播中的行都可播（下拉选人 + 听音频 / 看画面按钮）；「立即检查」按钮实时
重查并原位刷新（refresh_cb 由托盘注入）；未开播行灰显。播放复用 watcher.play_room()，
播放器独立窗口，关汇总窗不影响播放。

设计要点：
  - 配置 <-> 表单、rows <-> 表格 的转换抽成模块级纯函数，便于离线测试
  - 保存写回 config.json（UTF-8 / indent=2），再回调 on_saved() 通知托盘热重载
  - bring_to_front() 供托盘从别的线程安全地把已打开的窗口提到前面
"""
from __future__ import annotations

import json
import os
import sys
import threading
import time
import traceback
import tkinter as tk
from tkinter import ttk, messagebox

import watcher

HERE = os.path.dirname(os.path.abspath(__file__))
# 打包后：只读资源（app.ico）在 _MEIPASS，可写 config.json 在 exe 目录（与 tray_app 一致）
if getattr(sys, "frozen", False):
    _RES_DIR = getattr(sys, "_MEIPASS", HERE)
    _APP_DIR = os.path.dirname(os.path.abspath(sys.executable))
else:
    _RES_DIR = HERE
    _APP_DIR = HERE
DEFAULT_CONFIG = os.path.join(_APP_DIR, "config.json")

CHANNEL_KEYS = ["desktop", "sound", "email", "native_toast", "open_browser"]
CHANNEL_LABELS = {
    "desktop": "桌面弹窗",
    "sound": "提示音",
    "email": "邮件",
    "native_toast": "原生 Toast",
    "open_browser": "打开网页",
}

# 全局参数：(key, 标签, 类型, 默认, 提示)
GLOBAL_FIELDS = [
    ("interval", "轮询间隔(秒)", "int", 180, "建议 120~300，最低 30"),
    ("debounce_polls", "开播确认次数", "int", 1, "低频轮询用 1；误报改 2"),
    ("retries", "请求重试次数", "int", 2, ""),
    ("http_timeout", "请求超时(秒)", "int", 10, ""),
    ("notify_on_start", "启动时已在直播也提醒", "bool", True, ""),
    ("notify_on_end", "下播也提醒", "bool", False, ""),
    ("cookie", "B 站 Cookie(可选)", "str", "", "轮播房间拉流需登录态；从浏览器复制整串 Cookie"),
]

EMAIL_FIELDS = [
    ("enabled", "启用邮件通知", "bool", False, ""),
    ("smtp_host", "SMTP 服务器", "str", "smtp.qq.com", "QQ: smtp.qq.com"),
    ("smtp_port", "端口", "int", 465, "SSL 通常 465"),
    ("use_ssl", "使用 SSL", "bool", True, ""),
    ("use_starttls", "使用 STARTTLS", "bool", False, "Outlook 587 用这个"),
    ("username", "用户名/发件邮箱", "str", "", ""),
    ("password", "授权码(非登录密码)", "password", "", "QQ 邮箱需生成授权码"),
    ("to", "收件人(逗号分隔)", "str", "", "可填多个"),
]

# 模块级引用：托盘从其它线程调用 bring_to_front() 时用
_root: tk.Tk | None = None
_open_lock = threading.Lock()


# ===========================================================================
# 纯函数：配置 <-> 表单数据 转换与校验（可离线测试）
# ===========================================================================
def parse_to_list(value) -> list:
    """把 'a,b' / ['a','b'] / 字符串转成去重后的列表。"""
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        items = [str(v).strip() for v in value]
    else:
        items = [p.strip() for p in str(value).replace("\n", ",").split(",")]
    out, seen = [], set()
    for it in items:
        if it and it not in seen:
            seen.add(it)
            out.append(it)
    return out


def channel_set_to_sorted(channels) -> list:
    """渠道集合 -> 按 CHANNEL_KEYS 顺序排列的列表（序列化稳定）。"""
    s = set(channels or [])
    return [k for k in CHANNEL_KEYS if k in s]


def split_config(raw: dict) -> tuple:
    """
    config.json -> (streamers, globals, email, player)
    streamers: [{'uid':int,'name':str,'channels':[str]}]（按 CHANNEL_KEYS 排序）
    """
    glob = {}
    for key, _label, _typ, default, _hint in GLOBAL_FIELDS:
        glob[key] = raw.get(key, default)
    # 顶层全局开关（旧配置用；新配置以 streamers 为准，但保留以便兼容）
    for k in ("desktop", "sound", "native_toast", "open_browser"):
        glob[k] = bool(raw.get(k, False))

    email = {}
    for key, _label, _typ, default, _hint in EMAIL_FIELDS:
        email[key] = (raw.get("email") or {}).get(key, default)
    if "to" in email:
        email["to"] = parse_to_list(email["to"])

    player = dict(raw.get("player") or {})
    player.setdefault("prefer", ["vlc", "mpv", "ffplay"])
    player.setdefault("vlc_path", None)
    player.setdefault("mpv_path", None)
    player.setdefault("ffplay_path", None)

    streamers = []
    src = raw.get("streamers") or []
    if src:
        for s in src:
            ch = watcher.normalize_channels(s.get("channels"))
            if ch is None:
                ch = set(watcher.ALL_CHANNELS)  # 表单里显式展示为全选
            streamers.append({
                "uid": int(s["uid"]),
                "name": s.get("name") or "",
                "channels": channel_set_to_sorted(ch),
            })
    else:
        # 旧配置 uids + 全局开关 -> 推导默认渠道
        base = set()
        for k in ("desktop", "sound", "native_toast", "open_browser"):
            if bool(raw.get(k, False)):
                base.add(k)
        if (raw.get("email") or {}).get("enabled", False):
            base.add("email")
        for u in raw.get("uids") or []:
            streamers.append({"uid": int(u), "name": "", "channels": channel_set_to_sorted(base)})
    return streamers, glob, email, player


def merge_config(raw: dict, streamers: list, glob: dict, email: dict, player: dict) -> dict:
    """表单数据 -> 完整 config.json dict（保留 raw 里的未知键）。"""
    out = dict(raw)
    out["streamers"] = [
        {"uid": int(s["uid"]), "name": s.get("name", ""),
         "channels": channel_set_to_sorted(s.get("channels"))}
        for s in streamers
    ]
    out.pop("uids", None)  # 新格式以 streamers 为准
    for key, _l, _t, _d, _h in GLOBAL_FIELDS:
        out[key] = glob[key]
    # 顶层开关是「默认渠道」（供 --test-notify / 未写 channels 的博主使用）。
    # 用所有 streamers 渠道的并集推导，保证 GUI 保存后 --test-notify 仍有渠道可用。
    union = set()
    for s in out["streamers"]:
        union |= set(s["channels"])
    for k in ("desktop", "sound", "native_toast", "open_browser"):
        out[k] = k in union
    out["email"] = dict(email)
    out["player"] = dict(player)
    return out


def validate(streamers: list, glob: dict, email: dict) -> list:
    """返回错误信息列表；空列表表示通过。"""
    errs = []
    if not streamers:
        errs.append("至少要有 1 个博主")
    uids = []
    for s in streamers:
        uid = s.get("uid")
        if not isinstance(uid, int) or isinstance(uid, bool) or uid <= 0:
            errs.append("博主 UID 必须是正整数：%r" % uid)
        elif uid in uids:
            errs.append("UID %s 重复" % uid)
        else:
            uids.append(uid)
        ch = set(s.get("channels") or [])
        unknown = ch - set(CHANNEL_KEYS)
        if unknown:
            errs.append("UID %s 有未知渠道：%s" % (uid, ", ".join(sorted(unknown))))
    try:
        if int(glob.get("interval", 0)) < 30:
            errs.append("轮询间隔不能小于 30 秒")
    except (TypeError, ValueError):
        errs.append("轮询间隔必须是整数")
    try:
        if int(glob.get("debounce_polls", 0)) < 1:
            errs.append("开播确认次数至少为 1")
    except (TypeError, ValueError):
        errs.append("开播确认次数必须是整数")
    if email.get("enabled"):
        if not email.get("smtp_host"):
            errs.append("启用邮件时必须填 SMTP 服务器")
        if not email.get("username"):
            errs.append("启用邮件时必须填用户名")
        if not parse_to_list(email.get("to")):
            errs.append("启用邮件时必须填收件人")
        try:
            int(email.get("smtp_port", 0))
        except (TypeError, ValueError):
            errs.append("SMTP 端口必须是整数")
    return errs


def save_config(path: str, cfg: dict) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


# ===========================================================================
# tkinter 界面
# ===========================================================================
class SettingsWindow:
    def __init__(self, root: tk.Tk, config_path: str, on_saved, icon_path: str):
        self.root = root
        self.config_path = config_path
        self.on_saved = on_saved
        self.icon_path = icon_path
        self.streamers: list = []
        self.vars: dict = {}          # 全局/邮件/播放器 的 tk 变量
        self.ch_vars: dict = {}       # 博主编辑区的渠道复选变量
        self._build()

    # ---- 构建 ----
    def _build(self) -> None:
        r = self.root
        r.title("B 站开播提醒 · 设置")
        r.geometry("880x720")
        r.minsize(760, 620)
        try:
            if self.icon_path and os.path.exists(self.icon_path):
                r.iconbitmap(self.icon_path)
        except Exception:  # noqa: BLE001
            pass

        # 居中
        r.update_idletasks()
        sw, sh = r.winfo_screenwidth(), r.winfo_screenheight()
        r.geometry("+%d+%d" % (max(0, (sw - 880) // 2), max(0, (sh - 720) // 2 - 30)))

        outer = ttk.Frame(r, padding=10)
        outer.pack(fill="both", expand=True)

        # ---------- 博主区 ----------
        grp = ttk.LabelFrame(outer, text=" 监控的博主（每个可单独设置提醒方式） ", padding=8)
        grp.pack(fill="both", expand=False)

        # 固定件（按钮行 / 提示行）先 pack(side=bottom)，表格伸缩件最后 pack：
        # 保证任何窗口尺寸、DPI 缩放下按钮行都不会被表格挤出可视区。
        # 第一行只放按钮——窄窗 / 高 DPI 下长提示文字会把后面的按钮挤出可视区。
        bar = ttk.Frame(grp)
        bar.pack(side="bottom", fill="x", pady=(6, 0))
        self.btn_add = ttk.Button(bar, text="＋ 添加为新条目", command=self.on_add)
        self.btn_add.pack(side="left")
        self.btn_update = ttk.Button(bar, text="✎ 保存修改到选中行", command=self.on_update,
                                     state="disabled")
        self.btn_update.pack(side="left", padx=4)
        self.btn_delete = ttk.Button(bar, text="🗑 删除选中…", command=self.on_delete,
                                     state="disabled")
        self.btn_delete.pack(side="left")
        ttk.Label(grp, text="双击行载入编辑；未选中时提交＝新增；UID 框回车＝提交",
                  foreground="#888").pack(side="bottom", fill="x", pady=(2, 0))

        cols = ("uid", "name", "channels")
        self.tree = ttk.Treeview(grp, columns=cols, show="headings", height=6,
                                 selectmode="browse")
        for c, txt, w in zip(cols, ("UID", "备注名", "提醒渠道"), (110, 180, 380)):
            self.tree.heading(c, text=txt)
            self.tree.column(c, width=w, anchor="w")
        self.tree.pack(side="left", fill="both", expand=True)
        sb = ttk.Scrollbar(grp, orient="vertical", command=self.tree.yview)
        sb.pack(side="right", fill="y")
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.bind("<<TreeviewSelect>>", self.on_select)
        self.tree.bind("<Double-1>", lambda e: self.on_select())

        # 博主编辑表单
        edit = ttk.LabelFrame(outer, text=" 编辑博主 ", padding=8)
        edit.pack(fill="x", pady=(8, 0))
        row = ttk.Frame(edit)
        row.pack(fill="x")
        ttk.Label(row, text="UID：").pack(side="left")
        self.ed_uid = ttk.Entry(row, width=14)
        self.ed_uid.pack(side="left", padx=(0, 12))
        self.ed_uid.bind("<Return>", lambda e: self._submit_form())
        ttk.Label(row, text="备注名：").pack(side="left")
        self.ed_name = ttk.Entry(row, width=24)
        self.ed_name.pack(side="left", padx=(0, 12))
        ttk.Label(row, text="提醒渠道：").pack(side="left")
        for k in CHANNEL_KEYS:
            v = tk.BooleanVar(value=(k in ("desktop", "sound")))
            self.ch_vars[k] = v
            ttk.Checkbutton(row, text=CHANNEL_LABELS[k], variable=v).pack(side="left", padx=(0, 6))
        ttk.Button(row, text="全选", width=6, command=lambda: self.set_all_channels(True)).pack(side="left", padx=(8, 2))
        ttk.Button(row, text="清空", width=6, command=lambda: self.set_all_channels(False)).pack(side="left")

        # ---------- 参数区 ----------
        nb = ttk.Notebook(outer)
        nb.pack(fill="both", expand=True, pady=(8, 0))
        self.vars = {}
        frm_g = ttk.Frame(nb, padding=10)
        frm_e = ttk.Frame(nb, padding=10)
        frm_p = ttk.Frame(nb, padding=10)
        nb.add(frm_g, text="全局")
        nb.add(frm_e, text="邮件")
        nb.add(frm_p, text="播放器")

        self._build_fields(frm_g, GLOBAL_FIELDS, self.vars)
        self._build_fields(frm_e, EMAIL_FIELDS, self.vars, prefix="email.")
        self._build_player(frm_p)

        # ---------- 底部按钮 ----------
        bottom = ttk.Frame(outer)
        bottom.pack(fill="x", pady=(10, 0))
        ttk.Label(bottom, text="保存后立即生效，无需重启托盘", foreground="#888").pack(side="left")
        ttk.Button(bottom, text="取消", command=self.on_cancel).pack(side="right", padx=4)
        ttk.Button(bottom, text="保存并应用", command=self.on_save).pack(side="right")
        r.protocol("WM_DELETE_WINDOW", self.on_cancel)

        self.load()

    def _build_fields(self, parent, fields, store, prefix="") -> None:
        for i, (key, label, typ, default, hint) in enumerate(fields):
            row = ttk.Frame(parent)
            row.grid(row=i, column=0, sticky="ew", pady=3, padx=2)
            parent.columnconfigure(0, weight=1)
            ttk.Label(row, text=label, width=20, anchor="w").pack(side="left")
            if typ == "bool":
                v = tk.BooleanVar(value=bool(default))
                ttk.Checkbutton(row, variable=v).pack(side="left")
            else:
                v = tk.StringVar(value=str(default) if default is not None else "")
                show = "*" if typ == "password" else None
                ttk.Entry(row, textvariable=v, width=34, show=show).pack(side="left")
            store[prefix + key] = (v, typ, default)
            if hint:
                ttk.Label(row, text=hint, foreground="#999").pack(side="left", padx=8)

    def _build_player(self, parent) -> None:
        row = ttk.Frame(parent)
        row.pack(fill="x", pady=4)
        ttk.Label(row, text="优先顺序", width=20, anchor="w").pack(side="left")
        self.vars["player.prefer"] = (tk.StringVar(value="vlc, mpv, ffplay"), "str", "")
        ttk.Entry(row, textvariable=self.vars["player.prefer"][0], width=34).pack(side="left")
        ttk.Label(row, text="（逗号分隔，自动探测）", foreground="#999").pack(side="left", padx=8)
        for key, label in (("vlc_path", "VLC 路径"), ("mpv_path", "mpv 路径"),
                           ("ffplay_path", "ffplay 路径")):
            r2 = ttk.Frame(parent)
            r2.pack(fill="x", pady=3)
            ttk.Label(r2, text=label, width=20, anchor="w").pack(side="left")
            self.vars["player." + key] = (tk.StringVar(value=""), "str", None)
            ttk.Entry(r2, textvariable=self.vars["player." + key][0], width=52).pack(side="left")
            ttk.Label(r2, text="留空 = 自动探测", foreground="#999").pack(side="left", padx=8)

    # ---- 数据 ----
    def load(self) -> None:
        try:
            with open(self.config_path, "r", encoding="utf-8") as f:
                raw = json.load(f)
        except (OSError, ValueError) as e:
            messagebox.showerror("读取失败", "无法读取 config.json：\n%s" % e, parent=self.root)
            raw = {}
        self._raw = raw
        self.streamers, glob, email, player = split_config(raw)
        self._refresh_tree()
        self.set_all_channels(False)
        self.ed_uid.delete(0, "end")
        self.ed_name.delete(0, "end")

        for key, _l, _t, default, _h in GLOBAL_FIELDS:
            self._set_var(self.vars[key], glob.get(key, default))
        for k in ("desktop", "sound", "native_toast", "open_browser"):
            # 顶层开关仅作兼容保留，界面不直接编辑；用默认全 false，不影响 streamers
            pass
        for key, _l, _t, default, _h in EMAIL_FIELDS:
            val = email.get(key, default)
            if key == "to":
                val = ", ".join(parse_to_list(val))
            self._set_var(self.vars["email." + key], val)
        self.vars["player.prefer"][0].set(", ".join(player.get("prefer") or []))
        for key in ("vlc_path", "mpv_path", "ffplay_path"):
            self.vars["player." + key][0].set(player.get(key) or "")

    @staticmethod
    def _set_var(entry, value) -> None:
        v, typ, default = entry
        if typ == "bool":
            v.set(bool(value))
        else:
            v.set("" if value is None else str(value))

    def _refresh_tree(self) -> None:
        for i in self.tree.get_children():
            self.tree.delete(i)
        for s in self.streamers:
            ch = ", ".join(channel_set_to_sorted(s["channels"])) or "(无)"
            self.tree.insert("", "end", values=(s["uid"], s.get("name", ""), ch))

    def set_all_channels(self, value: bool) -> None:
        for v in self.ch_vars.values():
            v.set(value)

    def on_select(self, event=None) -> None:
        sel = self.tree.selection()
        if not sel:
            self._sync_buttons()
            return
        idx = self.tree.index(sel[0])
        s = self.streamers[idx]
        self.ed_uid.delete(0, "end")
        self.ed_uid.insert(0, str(s["uid"]))
        self.ed_name.delete(0, "end")
        self.ed_name.insert(0, s.get("name", ""))
        ch = set(s["channels"])
        for k, v in self.ch_vars.items():
            v.set(k in ch)
        self._sync_buttons()

    def _sync_buttons(self) -> None:
        """按「是否有选中行」切换 保存修改/删除 按钮的可用态。"""
        has_sel = bool(self.tree.selection())
        self.btn_update.state(["!disabled"] if has_sel else ["disabled"])
        self.btn_delete.state(["!disabled"] if has_sel else ["disabled"])

    def _clear_form(self) -> None:
        self.ed_uid.delete(0, "end")
        self.ed_name.delete(0, "end")
        self.ch_vars["desktop"].set(True)
        self.ch_vars["sound"].set(True)
        for k in ("email", "native_toast", "open_browser"):
            if k in self.ch_vars:
                self.ch_vars[k].set(False)

    def _submit_form(self) -> None:
        """UID 框回车：有选中行＝保存修改，无选中＝新增。"""
        if self.tree.selection():
            self.on_update()
        else:
            self.on_add()

    def on_add(self) -> None:
        """把编辑表单追加为新条目（未选中行时的提交路径）。"""
        s = self._form_streamer()
        if s is None:
            return
        if any(x["uid"] == s["uid"] for x in self.streamers):
            messagebox.showerror("重复", "UID %s 已存在。\n若要修改它，请先在列表中选中该行再编辑。"
                                 % s["uid"], parent=self.root)
            return
        self.streamers.append(s)
        self._refresh_tree()
        children = self.tree.get_children()
        if children:
            self.tree.selection_set(children[-1])
            self.tree.see(children[-1])
        self._clear_form()
        watcher.log("设置：添加博主 %s (%s)" % (s["uid"], s.get("name", "")))

    def _form_streamer(self):
        raw_uid = self.ed_uid.get().strip()
        try:
            uid = int(raw_uid)
            if uid <= 0:
                raise ValueError
        except ValueError:
            messagebox.showerror("输入错误", "UID 必须是正整数（从 space.bilibili.com/<数字> 取）",
                                 parent=self.root)
            return None
        ch = [k for k, v in self.ch_vars.items() if v.get()]
        return {"uid": uid, "name": self.ed_name.get().strip(), "channels": ch}

    def on_update(self) -> None:
        """把编辑表单写回当前选中行（仅选中行时可用）。"""
        sel = self.tree.selection()
        if not sel:  # 按钮已禁用，防御性兜底
            return
        s = self._form_streamer()
        if s is None:
            return
        idx = self.tree.index(sel[0])
        # 允许改 UID：排除自身后查重
        others = [x["uid"] for i, x in enumerate(self.streamers) if i != idx]
        if s["uid"] in others:
            messagebox.showerror("重复", "UID %s 已存在于其他行" % s["uid"], parent=self.root)
            return
        old = self.streamers[idx]
        self.streamers[idx] = s
        self._refresh_tree()
        children = self.tree.get_children()
        if len(children) > idx:
            self.tree.selection_set(children[idx])
            self.tree.see(children[idx])
        watcher.log("设置：更新博主 %s -> %s (%s)"
                    % (old.get("uid"), s["uid"], s.get("name", "")))

    def on_delete(self) -> None:
        sel = self.tree.selection()
        if not sel:
            return
        idx = self.tree.index(sel[0])
        s = self.streamers[idx]
        label = s.get("name") or ("UID %s" % s["uid"])
        if not messagebox.askyesno(
                "删除博主",
                "确定删除「%s」（UID %s）？\n\n删除仅作用于本窗口，点「保存并应用」后才写入配置。",
                parent=self.root):
            return
        self.streamers.pop(idx)
        self._refresh_tree()
        self._clear_form()
        self.tree.selection_remove(*self.tree.selection())
        self._sync_buttons()
        watcher.log("设置：删除博主 %s (%s)" % (s["uid"], s.get("name", "")))

    def _collect(self):
        glob, email, player = {}, {}, {}
        for key, _l, typ, default, _h in GLOBAL_FIELDS:
            v = self.vars[key][0].get()
            glob[key] = self._coerce(v, typ, default)
        for k in ("desktop", "sound", "native_toast", "open_browser"):
            glob[k] = False  # 新格式以 streamers 为准
        for key, _l, typ, default, _h in EMAIL_FIELDS:
            v = self.vars["email." + key][0].get()
            email[key] = self._coerce(v, typ, default)
        email["to"] = parse_to_list(email.get("to"))
        player["prefer"] = [x.strip().lower() for x in
                            self.vars["player.prefer"][0].get().replace("\n", ",").split(",")
                            if x.strip()]
        for key in ("vlc_path", "mpv_path", "ffplay_path"):
            player[key] = self.vars["player." + key][0].get().strip() or None
        return glob, email, player

    @staticmethod
    def _coerce(v, typ, default):
        if typ == "bool":
            return bool(v)
        if typ == "int":
            try:
                return int(v)
            except (TypeError, ValueError):
                return default
        return v

    def on_save(self) -> None:
        glob, email, player = self._collect()
        errs = validate(self.streamers, glob, email)
        if errs:
            messagebox.showerror("配置有误", "\n".join("· " + e for e in errs), parent=self.root)
            return
        cfg = merge_config(self._raw, self.streamers, glob, email, player)
        try:
            save_config(self.config_path, cfg)
        except OSError as e:
            messagebox.showerror("保存失败", str(e), parent=self.root)
            return
        watcher.log("设置已保存：%d 个博主，间隔 %s 秒" % (len(self.streamers), glob["interval"]))
        if self.on_saved:
            try:
                self.on_saved()
            except Exception:  # noqa: BLE001
                watcher.log("热重载回调异常：\n" + traceback.format_exc())
        messagebox.showinfo("已保存", "配置已保存并立即生效。", parent=self.root)
        self.root.destroy()

    def on_cancel(self) -> None:
        self.root.destroy()


# ===========================================================================
# 对外入口
# ===========================================================================
def bring_to_front() -> None:
    """从其它线程安全地把已打开的设置窗口提到前面。"""
    with _open_lock:
        r = _root
    if r is None:
        return
    try:
        r.after(0, lambda: (r.deiconify(), r.lift(), r.attributes("-topmost", True),
                            r.after(200, lambda: r.attributes("-topmost", False)),
                            r.focus_force()))
    except Exception:  # noqa: BLE001
        pass


def open_settings(config_path: str = DEFAULT_CONFIG, on_saved=None, icon_path: str = None) -> None:
    """阻塞：创建窗口并跑 mainloop，直到用户关闭。应在独立线程里调用。"""
    global _root
    root = tk.Tk()
    with _open_lock:
        _root = root
    try:
        SettingsWindow(root, os.path.abspath(config_path), on_saved,
                       icon_path or os.path.join(_RES_DIR, "app.ico"))
        root.mainloop()
    finally:
        with _open_lock:
            _root = None


# ===========================================================================
# 「🔍 探测直播状态」汇总窗口
# ===========================================================================
STATUS_TEXT = {0: "未开播", 1: "🔴 直播中", 2: "🔄 轮播中"}
# 可播放状态：直播中 + 轮播中（轮播房间 getRoomPlayInfo 同样能取到流）
PLAYABLE_STATUS = (1, 2)


def probe_rows_to_table(cfg: dict, rows: dict) -> list:
    """
    纯函数：把 (cfg, {uid: row}) 转成汇总窗口的表格行。

    每行：uid / 昵称 / 状态码 / 状态文案 / 房间号 / 标题 / 开播时间 / 渠道文本 / 是否可播
    可播 = 直播中或轮播中且有房间号；接口未返回的 UID 列为「无返回」。
    """
    out = []
    for uid in cfg.get("uids", []):
        row = (rows or {}).get(uid) or {}
        if not row:
            out.append({
                "uid": uid, "name": watcher.display_name(uid, {}, cfg),
                "status": -1, "status_text": "无返回（UID 可能无效）",
                "room_id": "", "title": "", "live_time": "",
                "channels": ", ".join(sorted(watcher.channels_for(uid, cfg))) or "(无)",
                "online": False,
            })
            continue
        st = int(row.get("live_status", 0))
        lt = row.get("live_time", 0)
        out.append({
            "uid": uid,
            "name": watcher.display_name(uid, row, cfg),
            "status": st,
            "status_text": STATUS_TEXT.get(st, "未知(%s)" % st),
            "room_id": row.get("room_id", ""),
            "title": row.get("title", ""),
            "live_time": watcher.fmt_time(lt) if int(lt or 0) > 0 else "—",
            "channels": ", ".join(sorted(watcher.channels_for(uid, cfg))) or "(无)",
            "online": st in PLAYABLE_STATUS and bool(row.get("room_id")),
        })
    return out


class ProbeWindow:
    """
    直播状态汇总窗口（整合原托盘的「立即检查」「听直播」）：

      - 直播中 / 轮播中行都可播放（下拉 + 听音频 / 看画面），走 watcher.play_room()
      - refresh_cb 不为 None 时显示「🔄 立即检查」按钮：后台线程实时探测，
        完成后原位刷新表格与播放区（不关窗重建）
    """

    def __init__(self, root: tk.Tk, cfg: dict, table: list, error: str, icon_path: str,
                 refresh_cb=None):
        self.root = root
        self.cfg = cfg
        self.table = table
        self.error = error or ""
        self.refresh_cb = refresh_cb      # () -> (rows, error, cfg) 在工作线程执行
        self._live: list = []
        self._build(icon_path)
        self._render()

    def _build(self, icon_path: str) -> None:
        r = self.root
        r.title("🔍 直播状态探测")
        r.geometry("980x580")
        r.minsize(820, 440)
        try:
            if icon_path and os.path.exists(icon_path):
                r.iconbitmap(icon_path)
        except Exception:  # noqa: BLE001
            pass
        r.update_idletasks()
        sw, sh = r.winfo_screenwidth(), r.winfo_screenheight()
        r.geometry("+%d+%d" % (max(0, (sw - 980) // 2), max(0, (sh - 580) // 2 - 40)))

        outer = ttk.Frame(r, padding=10)
        outer.pack(fill="both", expand=True)

        # 底部固定件先 pack(side=bottom)（自下而上：关闭 → 状态栏 → 播放区），
        # 表格伸缩件最后 pack 吃剩余空间——否则高 DPI / 窄窗下表格会把
        # 播放区、状态栏整个挤出窗口外（tkinter pack 经典坑）。
        ttk.Button(outer, text="关闭", command=r.destroy).pack(
            side="bottom", anchor="e", pady=(6, 0))
        self.status_var = tk.StringVar(value="")
        ttk.Label(outer, textvariable=self.status_var, foreground="#0078D7",
                  wraplength=940).pack(side="bottom", anchor="w", pady=(6, 0))
        self.play_bar = ttk.LabelFrame(
            outer, text=" 播放（直播中 / 轮播中均可；播放器独立窗口，关本窗口不影响播放） ",
            padding=8)
        self.play_bar.pack(side="bottom", fill="x", pady=(8, 0))

        # 顶栏：统计文本 + 立即检查
        top = ttk.Frame(outer)
        top.pack(side="top", fill="x", pady=(0, 8))
        self.head_var = tk.StringVar(value="")
        ttk.Label(top, textvariable=self.head_var,
                  font=("Microsoft YaHei UI", 11, "bold")).pack(side="left")
        self.btn_refresh = ttk.Button(top, text="🔄 立即检查", command=self.on_refresh)
        if self.refresh_cb is not None:
            self.btn_refresh.pack(side="right")

        # 状态表格（伸缩区，最后 pack）
        cols = ("uid", "name", "status", "room", "title", "time", "channels", "act")
        self.tree = ttk.Treeview(outer, columns=cols, show="headings", height=10)
        heads = (("uid", "UID", 80), ("name", "昵称", 120), ("status", "状态", 90),
                 ("room", "房间号", 80), ("title", "标题", 180), ("time", "开播时间", 120),
                 ("channels", "提醒渠道", 130), ("act", "操作", 120))
        for c, txt, wd in heads:
            self.tree.heading(c, text=txt)
            self.tree.column(c, width=wd, anchor="w", stretch=False)
        self.tree.pack(side="left", fill="both", expand=True)
        sb = ttk.Scrollbar(outer, orient="vertical", command=self.tree.yview)
        sb.pack(side="right", fill="y")
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.tag_configure("on", foreground="#C2185B")     # 直播中：品红
        self.tree.tag_configure("round", foreground="#E65100")  # 轮播中：橙
        self.tree.tag_configure("off", foreground="#888888")

        r.protocol("WM_DELETE_WINDOW", r.destroy)

    # ---- 数据渲染：表格 + 播放区 + 顶栏统计（可重复调用刷新） ----
    def _render(self) -> None:
        n_on = sum(1 for x in self.table if x["status"] == 1)
        n_round = sum(1 for x in self.table if x["status"] == 2)
        head = "共 %d 个博主 · %d 个直播中 · %d 个轮播中 · %s" % (
            len(self.table), n_on, n_round, time.strftime("%H:%M:%S"))
        if self.error:
            head += "    （实时查询失败，显示缓存状态：%s）" % self.error[:60]
        self.head_var.set(head)

        for i in self.tree.get_children():
            self.tree.delete(i)
        for x in self.table:
            if x["status"] == 1:
                tag = "on"
            elif x["status"] == 2:
                tag = "round"
            else:
                tag = "off"
            act = "▶ 用下方播放区" if x["online"] else "—（未开播）"
            self.tree.insert("", "end", values=(
                x["uid"], x["name"], x["status_text"], x["room_id"],
                x["title"], x["live_time"], x["channels"], act), tags=(tag,))

        for w in self.play_bar.winfo_children():
            w.destroy()
        self._live = [x for x in self.table if x["online"]]
        if self._live:
            names = [self._live_label(x) for x in self._live]
            ttk.Label(self.play_bar, text="选择直播间：").pack(side="left")
            self.sel = tk.StringVar(value=names[0])
            ttk.Combobox(self.play_bar, textvariable=self.sel, values=names,
                         state="readonly", width=38).pack(side="left", padx=(0, 12))
            ttk.Button(self.play_bar, text="▶ 听音频",
                       command=lambda: self._play("audio")).pack(side="left", padx=3)
            ttk.Button(self.play_bar, text="▶ 看画面",
                       command=lambda: self._play("video")).pack(side="left", padx=3)
        else:
            ttk.Label(self.play_bar, text="当前没有直播中 / 轮播中的博主。",
                      foreground="#888").pack(side="left")

    @staticmethod
    def _live_label(x: dict) -> str:
        base = "%s · 房间 %s" % (x["name"], x["room_id"])
        return base + " · 轮播" if x["status"] == 2 else base

    # ---- 立即检查：后台线程探测，完成后原位刷新 ----
    def on_refresh(self) -> None:
        if self.refresh_cb is None:
            return
        self.btn_refresh.state(["disabled"])
        self.status_var.set("正在实时探测所有博主状态…（窗口可继续操作）")
        self._refreshing = True

        def work():
            rows, err, cfg = None, None, self.cfg
            try:
                rows, err, cfg = self.refresh_cb()
            except Exception as e:  # noqa: BLE001
                err = "%s: %s" % (type(e).__name__, e)
                watcher.log("探测刷新异常：%s" % err)

            def apply():
                self.btn_refresh.state(["!disabled"])
                if rows is not None:
                    self.cfg = cfg
                    self.table = probe_rows_to_table(cfg, rows)
                    self.error = err or ""
                    self._render()
                    self.status_var.set("已刷新 · %s" % time.strftime("%H:%M:%S"))
                else:
                    self.status_var.set("❌ 探测失败：%s（保留当前列表）" % err)
            self.root.after(0, apply)

        threading.Thread(target=work, daemon=True).start()

    def _play(self, mode: str) -> None:
        names = [self._live_label(x) for x in self._live]
        try:
            idx = names.index(self.sel.get())
        except (ValueError, AttributeError):
            idx = 0
        item = self._live[idx]
        room = item["room_id"]
        self.status_var.set("正在为「%s」获取直播流…" % item["name"])
        # 统一入口，播放与窗口解离；通过 root.after 轮询队列把消息送回主线程
        import queue as _queue
        q: _queue.Queue = _queue.Queue()

        def pump():
            try:
                while True:
                    self.status_var.set(q.get_nowait())
            except _queue.Empty:
                pass
            self.root.after(300, pump)

        self.root.after(300, pump)
        watcher.play_room(room, mode, self.cfg,
                          on_state=lambda m: q.put(m),
                          on_error=lambda m: q.put("❌ " + m))


_probe_root: tk.Tk | None = None


def bring_probe_to_front() -> None:
    if _probe_root is None:
        return
    try:
        _probe_root.after(0, lambda: (_probe_root.deiconify(), _probe_root.lift(),
                                      _probe_root.focus_force()))
    except Exception:  # noqa: BLE001
        pass


def open_probe_window(cfg: dict, rows: dict, error: str = None, icon_path: str = None,
                      refresh_cb=None) -> None:
    """
    阻塞：打开汇总窗口并跑 mainloop；应在独立线程里调用。

    refresh_cb：探测窗「立即检查」按钮的回调，工作线程中执行，
    返回 (rows, error, cfg)；None 表示不显示刷新按钮。
    """
    global _probe_root
    if _probe_root is not None:
        bring_probe_to_front()
        return
    root = tk.Tk()
    _probe_root = root
    try:
        table = probe_rows_to_table(cfg, rows)
        ProbeWindow(root, cfg, table, error or "",
                    icon_path or os.path.join(_RES_DIR, "app.ico"),
                    refresh_cb=refresh_cb)
        root.mainloop()
    finally:
        _probe_root = None


if __name__ == "__main__":
    open_settings()
