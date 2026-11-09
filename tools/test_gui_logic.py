#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""settings_gui.py 纯函数层离线自测（不需要 tkinter / 显示器）。"""
import importlib.util
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)  # 被测模块在项目根目录
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


def load(name):
    spec = importlib.util.spec_from_file_location(name, os.path.join(ROOT, name + ".py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


w = load("watcher")
g = load("settings_gui")

passed = failed = 0


def check(name, cond, extra=""):
    global passed, failed
    if cond:
        passed += 1
        print("  PASS  %s" % name)
    else:
        failed += 1
        print("  FAIL  %s  %s" % (name, extra))


print("\n[1] parse_to_list")
check("逗号字符串", g.parse_to_list("a@b.com, c@d.com") == ["a@b.com", "c@d.com"])
check("列表", g.parse_to_list(["a", "b", "a"]) == ["a", "b"])
check("换行分隔", g.parse_to_list("a\nb") == ["a", "b"])
check("空值", g.parse_to_list(None) == [])

print("\n[2] channel_set_to_sorted 顺序稳定")
check("无序集合按固定顺序输出",
      g.channel_set_to_sorted({"email", "desktop", "sound"}) == ["desktop", "sound", "email"])
check("未知渠道被丢弃", g.channel_set_to_sorted({"desktop", "bogus"}) == ["desktop"])

print("\n[3] split_config：新格式 streamers")
raw = {
    "interval": 120, "desktop": True, "sound": True,
    "email": {"enabled": True, "to": ["a@x.com", "b@x.com"],
              "smtp_host": "smtp.qq.com", "username": "me@qq.com", "smtp_port": 465},
    "player": {"vlc_path": r"D:\my\vlc.exe"},
    "streamers": [
        {"uid": 48330353, "name": "A", "channels": ["desktop", "sound", "email"]},
        {"uid": 2, "name": "B", "channels": ["email"]},
    ],
}
st, gl, em, pl = g.split_config(raw)
check("2 个 streamer", len(st) == 2)
check("A 渠道有序", st[0]["channels"] == ["desktop", "sound", "email"], st[0])
check("B 仅 email", st[1]["channels"] == ["email"], st[1])
check("全局 interval", gl["interval"] == 120)
check("email.to 归一为列表", em["to"] == ["a@x.com", "b@x.com"], em)
check("player 显式路径保留", pl["vlc_path"] == r"D:\my\vlc.exe")
check("player 默认 prefer", pl["prefer"] == ["vlc", "mpv", "ffplay"])

print("\n[4] split_config：旧格式 uids + 全局开关 -> 推导默认渠道")
raw2 = {"uids": [48330353, 2], "desktop": True, "sound": False,
        "email": {"enabled": True}, "native_toast": False, "open_browser": False}
st2, _, _, _ = g.split_config(raw2)
check("生成 2 行", len(st2) == 2, st2)
check("默认渠道 = desktop+email", st2[0]["channels"] == ["desktop", "email"], st2[0])

print("\n[5] merge_config 往返")
st[0]["channels"] = ["email", "desktop"]  # 故意乱序
out = g.merge_config(raw, st, gl, em, pl)
check("streamers 写回", len(out["streamers"]) == 2)
check("渠道序列化有序", out["streamers"][0]["channels"] == ["desktop", "email"], out["streamers"][0])
check("uids 键被移除（新格式优先）", "uids" not in out)
check("email 写回", out["email"]["smtp_host"] == "smtp.qq.com")
# 注意：[5] 故意把 A 的渠道改成 ['email','desktop']（去掉了 sound），
# 所以并集里没有 sound；这里只断言并集中确实存在的/不存在的项。
check("顶层开关由 streamers 渠道并集推导（--test-notify 仍可用）",
      out["desktop"] is True and out["sound"] is False and out["native_toast"] is False,
      {k: out.get(k) for k in ("desktop", "sound", "native_toast", "open_browser")})
check("email.enabled 顶层由 email 段保留", out["email"]["enabled"] is True)
# 往返一致性：merge 会把渠道规范化为有序，期望值也应是规范化后的
expected_st = [{"uid": s["uid"], "name": s["name"],
                "channels": g.channel_set_to_sorted(s["channels"])} for s in st]
st3, gl3, em3, pl3 = g.split_config(out)
check("split(merge(x)) 的 streamers 一致（渠道已规范化）", st3 == expected_st, (st3, expected_st))
check("split(merge(x)) 的 interval 一致", gl3["interval"] == gl["interval"])

print("\n[6] validate 校验")
check("正常配置无错误", g.validate(st, gl, em) == [], g.validate(st, gl, em))
check("空博主列表报错", any("至少" in e for e in g.validate([], gl, em)))
bad = [{"uid": 0, "name": "", "channels": []}]
check("UID<=0 报错", any("正整数" in e for e in g.validate(bad, gl, em)))
dup = [{"uid": 1, "name": "", "channels": []}, {"uid": 1, "name": "", "channels": []}]
check("重复 UID 报错", any("重复" in e for e in g.validate(dup, gl, em)))
unk = [{"uid": 1, "name": "", "channels": ["telegram"]}]
check("未知渠道报错", any("未知渠道" in e for e in g.validate(unk, gl, em)))
gl_bad = dict(gl); gl_bad["interval"] = 10
check("间隔<30 报错", any("30" in e for e in g.validate(st, gl_bad, em)))
em_bad = dict(em); em_bad["enabled"] = True; em_bad["to"] = []
check("启用邮件但无收件人报错", any("收件人" in e for e in g.validate(st, gl, em_bad)))

print("\n[7] save_config 原子写入 + 内容正确")
import tempfile
fd, p = tempfile.mkstemp(suffix=".json")
os.close(fd)
try:
    g.save_config(p, out)
    with open(p, "r", encoding="utf-8") as f:
        back = json.load(f)
    check("写回 JSON 可解析", back["streamers"] == out["streamers"])
    check("不残留 .tmp", not os.path.exists(p + ".tmp"))
finally:
    os.remove(p)

print("\n[8] watcher.load_config 能吃下 GUI 保存的配置（端到端兼容）")
fd, p = tempfile.mkstemp(suffix=".json")
os.close(fd)
try:
    g.save_config(p, out)
    cfg = w.load_config(p)
    check("UID 列表正确", cfg["uids"] == [48330353, 2], cfg["uids"])
    check("A 渠道正确", w.channels_for(48330353, cfg) == {"desktop", "email"},
          w.channels_for(48330353, cfg))
    check("B 渠道正确", w.channels_for(2, cfg) == {"email"}, w.channels_for(2, cfg))
finally:
    os.remove(p)

print("\n===== 结果：%d PASS / %d FAIL =====" % (passed, failed))
sys.exit(0 if failed == 0 else 1)
