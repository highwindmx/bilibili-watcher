#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""watcher.py 离线自测：mock 掉网络与通知，只验证状态机/去重/配置逻辑。

运行：python test_watcher.py
预期：全部 PASS，退出码 0。
"""
import importlib.util
import os
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)  # 被测模块在项目根目录
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
spec = importlib.util.spec_from_file_location("watcher", os.path.join(ROOT, "watcher.py"))
w = importlib.util.module_from_spec(spec)
spec.loader.exec_module(w)
_ORIG_NOTIFY = w.notify  # 保存真实实现（后面 [4]~[10] 会临时替换它）

passed = 0
failed = 0


def check(name, cond, extra=""):
    global passed, failed
    if cond:
        passed += 1
        print("  PASS  %s" % name)
    else:
        failed += 1
        print("  FAIL  %s  %s" % (name, extra))


def row(uid, status, live_time=0, title="T", room=100, uname="U"):
    return {"uid": uid, "live_status": status, "live_time": live_time,
            "title": title, "room_id": room, "uname": uname}


def base_cfg(**kw):
    c = {"uids": [1], "debounce_polls": 1, "notify_on_start": True,
         "notify_on_end": False, "desktop": False, "sound": False,
         "native_toast": False, "open_browser": False,
         "email": {"enabled": False}, "http_timeout": 10, "retries": 0,
         "default_channels": set(), "_streamers": {}}
    c.update(kw)
    return c


def run_seq(seq, cfg=None, first_run=True):
    """喂入一串 rows，返回每轮产生的 action 列表。"""
    cfg = cfg or base_cfg()
    events = []
    w.fetch_status = lambda uids, c: seq  # dict: uid -> row
    w.notify = lambda action, r, uid, c, quiet=False: events.append((action, uid))
    state = {"first_run": first_run, "users": {}}
    debounce = {}
    w.poll_once(cfg, state, debounce, quiet=True)
    return events, state


print("\n[1] build_url 批量参数")
u = w.build_url([48330353, 2])
check("包含两个 uids[] 参数", u.count("uids%5B%5D=") == 2, u)
check("参数值正确", "48330353" in u and "=2" in u, u)

print("\n[2] 首次启动已在直播 -> 提醒一次（notify_on_start=True）")
ev, st = run_seq({1: row(1, 1, live_time=1000)})
check("产生 1 条 start", ev == [("start", 1)], ev)
check("状态记为直播中", st["users"]["1"]["status"] == 1, st["users"])

print("\n[3] 首次启动已在直播但 notify_on_start=False -> 不提醒")
ev, _ = run_seq({1: row(1, 1, live_time=1000)}, base_cfg(notify_on_start=False))
check("无通知", ev == [], ev)

print("\n[4] 未开播 -> 开播 -> 持续开播：只在跳变时提醒一次")
events = []
w.notify = lambda action, r, uid, c, quiet=False: events.append((action, uid, r.get("live_time")))
cfg = base_cfg()
seqs = [
    {1: row(1, 0)},
    {1: row(1, 1, live_time=2000)},
    {1: row(1, 1, live_time=2000)},
    {1: row(1, 1, live_time=2000)},
]
state = {"first_run": False, "users": {}}
debounce = {}
for seq in seqs:
    w.fetch_status = lambda uids, c, s=seq: s
    state = w.poll_once(cfg, state, debounce, quiet=True)
check("仅 1 条 start 且 live_time=2000", events == [("start", 1, 2000)], events)

print("\n[5] 同一 live_time 重启脚本不重复提醒（状态持久化去重）")
events = []
state = {"first_run": True, "users": {"1": {"status": 1, "live_time": 2000}}}
debounce = {}
w.fetch_status = lambda uids, c: {1: row(1, 1, live_time=2000)}
state = w.poll_once(base_cfg(notify_on_start=False), state, debounce, quiet=True)
check("无重复通知", events == [], events)

print("\n[6] 下播后同一 UID 再次开播（新 live_time）-> 再次提醒")
events = []
w.notify = lambda action, r, uid, c, quiet=False: events.append((action, uid, r.get("live_time")))
cfg = base_cfg(notify_on_end=False)
seqs = [
    {1: row(1, 0)},
    {1: row(1, 1, live_time=3000)},
    {1: row(1, 0)},
    {1: row(1, 1, live_time=4000)},
]
state = {"first_run": False, "users": {}}
debounce = {}
for seq in seqs:
    w.fetch_status = lambda uids, c, s=seq: s
    state = w.poll_once(cfg, state, debounce, quiet=True)
check("两条 start，live_time 各不同", events == [("start", 1, 3000), ("start", 1, 4000)], events)

print("\n[7] 轮播中(2) 不算开播；开播 -> 轮播 不算下播通知")
events = []
w.notify = lambda action, r, uid, c, quiet=False: events.append(action)
cfg = base_cfg()
seqs = [{1: row(1, 2)}, {1: row(1, 2)}, {1: row(1, 1, live_time=5000)},
        {1: row(1, 2)}, {1: row(1, 0)}]
state = {"first_run": False, "users": {}}
debounce = {}
for seq in seqs:
    w.fetch_status = lambda uids, c, s=seq: s
    state = w.poll_once(cfg, state, debounce, quiet=True)
check("仅轮播->开播触发 1 次", events == ["start"], events)

print("\n[8] debounce_polls=2：单次命中不提醒，连续两次才提醒")
events = []
w.notify = lambda action, r, uid, c, quiet=False: events.append(action)
cfg = base_cfg(debounce_polls=2)
seqs = [{1: row(1, 1, live_time=6000)}, {1: row(1, 1, live_time=6000)}]
state = {"first_run": False, "users": {}}
debounce = {}
for seq in seqs:
    w.fetch_status = lambda uids, c, s=seq: s
    state = w.poll_once(cfg, state, debounce, quiet=True)
check("恰好 1 条 start", events == ["start"], events)

print("\n[9] 多博主：一次请求，各自独立判定")
events = []
w.notify = lambda action, r, uid, c, quiet=False: events.append((action, uid))
cfg = base_cfg(uids=[1, 2])
state = {"first_run": False, "users": {}}
debounce = {}
w.fetch_status = lambda uids, c: {1: row(1, 0), 2: row(2, 1, live_time=7000, uname="B")}
state = w.poll_once(cfg, state, debounce, quiet=True)
check("仅 UID 2 开播提醒", events == [("start", 2)], events)
check("两个 UID 状态都写入", set(state["users"].keys()) == {"1", "2"}, state["users"])

print("\n[10] 接口未返回某 UID（无效 UID）：保持旧状态，不误判下播")
events = []
w.notify = lambda action, r, uid, c, quiet=False: events.append(action)
cfg = base_cfg(uids=[1, 999])
state = {"first_run": False, "users": {"1": {"status": 1, "live_time": 8000}}}
debounce = {}
w.fetch_status = lambda uids, c: {1: row(1, 1, live_time=8000)}  # 999 缺失
state = w.poll_once(cfg, state, debounce, quiet=True)
check("无通知", events == [], events)
check("UID 1 旧状态保留", state["users"].get("1", {}).get("live_time") == 8000, state["users"])

print("\n[11] load_config 校验与默认值")
with tempfile.NamedTemporaryFile("w", suffix=".json", encoding="utf-8", delete=False) as f:
    import json
    json.dump({"uids": ["48330353", 2], "email": {"enabled": False}}, f)
    p = f.name
try:
    c = w.load_config(p)
    check("UID 字符串转 int", c["uids"] == [48330353, 2], c["uids"])
    check("默认间隔 180", c["interval"] == 180)
    check("默认去抖 1", c["debounce_polls"] == 1)
    try:
        w.load_config(p.replace(".json", "_empty.json"))
        check("空配置应报错", False)
    except (OSError, ValueError):
        check("空配置应报错", True)
finally:
    os.remove(p)

print("\n[12] fmt_time 容错")
check("0 时间戳不崩", isinstance(w.fmt_time(0), str))
check("非法值返回未知", w.fmt_time("bad") == "未知", w.fmt_time("bad"))

# ---------------------------------------------------------------------------
# 新增功能测试：每博主 channels / 流地址 / 播放器 / 播放与弹窗解离 / 探测表格
# ---------------------------------------------------------------------------
import json

def write_cfg(obj):
    f = tempfile.NamedTemporaryFile("w", suffix=".json", encoding="utf-8", delete=False)
    json.dump(obj, f, ensure_ascii=False)
    f.close()
    return f.name

print("\n[13] streamers + channels：A=桌面+声音+邮件，B=仅邮件")
p = write_cfg({
    "desktop": True, "sound": True, "email": {"enabled": True},
    "streamers": [
        {"uid": 48330353, "name": "A", "channels": ["desktop", "sound", "email"]},
        {"uid": 2, "name": "B", "channels": ["email"]},
    ],
})
try:
    c = w.load_config(p)
    check("生成 2 个 UID", c["uids"] == [48330353, 2], c["uids"])
    check("A 渠道 = desktop/sound/email",
          w.channels_for(48330353, c) == {"desktop", "sound", "email"}, w.channels_for(48330353, c))
    check("B 渠道 = 仅 email", w.channels_for(2, c) == {"email"}, w.channels_for(2, c))
    check("全局默认渠道 = desktop/sound/email",
          c["default_channels"] == {"desktop", "sound", "email"}, c["default_channels"])
finally:
    os.remove(p)

print("\n[14] channels 特殊值：all / none / 未知项忽略 / 未写则用默认")
check("all 展开为全部渠道", w.normalize_channels(["all"]) == w.ALL_CHANNELS)
check("none 为空集合", w.normalize_channels("none") == set())
check("未知项被忽略", w.normalize_channels(["desktop", "telegram", "sound"]) == {"desktop", "sound"})
check("None 表示用默认", w.normalize_channels(None) is None)
p = write_cfg({"desktop": True, "sound": True, "email": {"enabled": False},
               "streamers": [{"uid": 1, "channels": None}, {"uid": 2}]})
try:
    c = w.load_config(p)
    check("未写 channels 用全局默认", w.channels_for(1, c) == {"desktop", "sound"})
    check("缺 channels 键也用默认", w.channels_for(2, c) == {"desktop", "sound"})
finally:
    os.remove(p)

print("\n[15] 旧配置兼容：只有 uids + 全局开关")
p = write_cfg({"uids": [48330353], "desktop": True, "sound": False,
               "email": {"enabled": True}})
try:
    c = w.load_config(p)
    check("uids 生效", c["uids"] == [48330353])
    check("渠道来自全局开关", w.channels_for(48330353, c) == {"desktop", "email"},
          w.channels_for(48330353, c))
finally:
    os.remove(p)

print("\n[16] notify 按渠道分发（mock 各通道函数）")
# 前面 [4]~[10] 把 w.notify 换成了记录 lambda，这里恢复真实实现再测分发
w.notify = _ORIG_NOTIFY
calls = []
w.desktop_popup = lambda *a, **k: calls.append("desktop")
w.play_sound = lambda: calls.append("sound")
w.native_toast = lambda *a, **k: calls.append("toast")
w.send_email = lambda *a, **k: calls.append("email")
_open = w.webbrowser.open
w.webbrowser.open = lambda u: calls.append("browser")
c = base_cfg(uids=[1], default_channels={"desktop", "sound", "email"},
             _streamers={1: {"name": "A", "channels": {"desktop", "email"}}},
             email={"enabled": True})
w.notify("start", row(1, 1, live_time=9000, room=1496449), 1, c)
# sound/native_toast 是异步线程，等其收敛；desktop/email 是同步调用
time.sleep(0.2)
check("只触发 desktop+email（无 sound）", sorted(calls) == ["desktop", "email"], calls)
calls.clear()
c["_streamers"][1]["channels"] = set()
w.notify("start", row(1, 1, live_time=9000, room=1496449), 1, c)
check("channels 为空则不发任何通知", calls == [], calls)
w.webbrowser.open = _open

print("\n[17] 流地址解析：playUrl / getRoomPlayInfo（纯函数）")
pu = {"code": 0, "data": {"durl": [{"url": "https://cdn.example/a.flv?expires=1"}]}}
check("playUrl 提取首条直链",
      w.parse_playurl_payload(pu) == "https://cdn.example/a.flv?expires=1")
try:
    w.parse_playurl_payload({"code": -400, "data": {}})
    check("playUrl 非 0 code 应抛错", False)
except ValueError:
    check("playUrl 非 0 code 应抛错", True)
rpi = {"code": 0, "data": {"playurl_info": {"playurl": {"stream": [
    {"protocol_name": "http_hls", "format": [{"codec": [{"base_url": "/h.m3u8?",
        "url_info": [{"host": "https://hls.example", "extra": "x=1"}]}]}]},
    {"protocol_name": "http_stream", "format": [{"codec": [{"base_url": "/a.flv?",
        "url_info": [{"host": "https://cdn.example", "extra": "expires=2"}]}]}]},
]}}}}
check("getRoomPlayInfo 优先 http_stream 并正确拼接",
      w.parse_roomplayinfo_payload(rpi) == "https://cdn.example/a.flv?expires=2",
      w.parse_roomplayinfo_payload(rpi))

print("\n[18] fetch_stream_url：v2 优先，失败回退 playUrl，全失败抛错并提示 cookie")
w.http_get_json = lambda url, c: (rpi if "getRoomPlayInfo" in url else pu)
check("v2 可用时优先取 v2",
      w.fetch_stream_url(1496449, base_cfg()) == "https://cdn.example/a.flv?expires=2")
def failing_v2(url, c):
    if "getRoomPlayInfo" in url:
        raise RuntimeError("mock v2 空 stream")
    return pu
w.http_get_json = failing_v2
check("v2 失败回退 playUrl",
      w.fetch_stream_url(1496449, base_cfg()) == "https://cdn.example/a.flv?expires=1")
w.http_get_json = lambda url, c: {"code": -400}
try:
    w.fetch_stream_url(1496449, base_cfg())
    check("两接口全失败应抛错", False)
except RuntimeError as e:
    check("两接口全失败应抛错且提示 cookie", "cookie" in str(e), str(e)[:80])

print("\n[19] find_player：显式路径 / PATH / 常见安装路径")
cfg = base_cfg(player={"vlc_path": r"C:\My\vlc.exe", "prefer": ["vlc", "mpv", "ffplay"]})
check("显式路径优先命中", w.find_player(cfg, which=lambda n: None,
                                     exists=lambda p: p == r"C:\My\vlc.exe") == ("vlc", r"C:\My\vlc.exe"))
cfg2 = base_cfg(player={"prefer": ["mpv", "vlc"]})
check("PATH 命中 mpv", w.find_player(cfg2, which=lambda n: r"C:\bin\mpv.exe" if n == "mpv" else None,
                                   exists=lambda p: p == r"C:\bin\mpv.exe") == ("mpv", r"C:\bin\mpv.exe"))
check("全找不到返回 None", w.find_player(base_cfg(player={"prefer": ["vlc", "mpv", "ffplay"]}),
                                      which=lambda n: None, exists=lambda p: False) is None)

print("\n[20] build_player_cmd：Referer/UA 与纯音频参数（防盗链关键）")
u = "https://cdn.example/a.flv?expires=1"
vlc_a = w.build_player_cmd("vlc", "vlc.exe", u, True)
check("VLC 音频含 --no-video", "--no-video" in vlc_a, vlc_a)
check("VLC 带 http-referrer 全局选项", any(x.startswith("--http-referrer=") for x in vlc_a), vlc_a)
check("VLC 带 per-input :http-referrer", any(x.startswith(":http-referrer=") for x in vlc_a), vlc_a)
check("VLC 带 UA", any(x.startswith("--http-user-agent=") for x in vlc_a))
check("VLC URL 在参数中", u in vlc_a)
vlc_v = w.build_player_cmd("vlc", "vlc.exe", u, False)
check("VLC 视频不含 --no-video", "--no-video" not in vlc_v)
mpv_a = w.build_player_cmd("mpv", "mpv.exe", u, True)
check("mpv 音频含 --no-video", "--no-video" in mpv_a)
check("mpv 带 --referrer", any(x.startswith("--referrer=") for x in mpv_a))
ff_a = w.build_player_cmd("ffplay", "ffplay.exe", u, True)
check("ffplay 音频含 -nodisp", "-nodisp" in ff_a)
check("ffplay headers 含 Referer 与 UA",
      any(isinstance(x, str) and "Referer:" in x and "User-Agent:" in x for x in ff_a), ff_a)

print("\n[21] PlaybackController：token 精确停止 / 状态机")
pbc = w.PlaybackController()
t = pbc.begin_token()
check("begin_token = 下一个 token", t == 1, t)
pbc.stop(token=999)  # 不匹配当前 token，不应停
check("不匹配 token 的 stop 是 no-op", pbc._token == 0, pbc._token)
pbc.stop(token=0)    # 匹配，推进 token
check("匹配 token 的 stop 推进 token", pbc._token == 1, pbc._token)
check("无进程时 status=idle", pbc.status() == "idle")

class FakeProc:
    def __init__(self): self.terminated = False; self.killed = False
    def poll(self): return None
    def terminate(self): self.terminated = True
    def wait(self, timeout=None): pass
    def kill(self): self.killed = True
fp = FakeProc()
pbc2 = w.PlaybackController()
pbc2._token = 5
pbc2.proc = fp
pbc2.stop(token=5)
check("stop 终止子进程", fp.terminated, fp.terminated)
check("stop 清空 proc", pbc2.proc is None)

print("\n[22] --once 真实接口回归（渠道列解析）")
p = write_cfg({"streamers": [{"uid": 48330353, "name": "双尾彗星", "channels": ["desktop", "email"]}]})
try:
    c = w.load_config(p)
    check("真实配置可加载", 48330353 in c["uids"])
    check("真实配置渠道", w.channels_for(48330353, c) == {"desktop", "email"})
finally:
    os.remove(p)

print("\n[23] 昵称兜底：接口 uname 为空时用配置 name")
c = base_cfg(_streamers={48330353: {"name": "双尾彗星", "channels": set()}})
check("uname 存在时优先用接口值", w.display_name(48330353, {"uname": "接口昵称"}, c) == "接口昵称")
check("uname 为空时用配置 name", w.display_name(48330353, {"uname": ""}, c) == "双尾彗星")
check("都没有时回退 UID", w.display_name(999, {}, c) == "UID 999")

print("\n[24] 播放与弹窗解离：VLC 保留自己的 GUI 窗口")
u = "https://cdn.example/a.flv?expires=1"
vlc_a = w.build_player_cmd("vlc", "vlc.exe", u, True)
check("音频模式仍禁用视频画面", "--no-video" in vlc_a, vlc_a)
check("音频模式不再用 --intf dummy（必须有 VLC 窗口）", "--intf" not in vlc_a and "dummy" not in vlc_a, vlc_a)
check("仍强制 Referer 防盗链", any(x.startswith("--http-referrer=") for x in vlc_a))
vlc_v = w.build_player_cmd("vlc", "vlc.exe", u, False)
check("视频模式无 --no-video", "--no-video" not in vlc_v)
check("play_room 统一入口存在", callable(getattr(w, "play_room", None)))
# play_room 只是转调 PLAYBACK.start，不应额外绑定任何 token
called = []
_orig_start = w.PLAYBACK.start
w.PLAYBACK.start = lambda *a, **k: called.append((a, k))
w.play_room(1496449, "audio", base_cfg(), on_state=lambda m: None)
check("play_room 转调 PLAYBACK.start", len(called) == 1 and called[0][0][0] == 1496449, called)
w.PLAYBACK.start = _orig_start

print("\n[25] 探测汇总表格纯函数（多博主 / 无效 UID）")
import importlib.util as _ilu
_spec = _ilu.spec_from_file_location("settings_gui", os.path.join(ROOT, "settings_gui.py"))
sg = _ilu.module_from_spec(_spec)
_spec.loader.exec_module(sg)
cfg = base_cfg(uids=[1, 2, 999],
               _streamers={1: {"name": "A", "channels": {"desktop"}},
                           2: {"name": "B", "channels": {"email"}},
                           999: {"name": "C", "channels": set()}})
rows = {
    1: {"uname": "主播A", "live_status": 1, "live_time": 1000, "room_id": 1496449, "title": "标题A"},
    2: {"uname": "主播B", "live_status": 0, "live_time": 0, "room_id": 1024, "title": "标题B"},
}
t = sg.probe_rows_to_table(cfg, rows)
check("3 行（含未返回的 UID）", len(t) == 3, t)
check("在线行 online=True 且房间号正确", t[0]["online"] is True and t[0]["room_id"] == 1496449, t[0])
check("离线行 online=False", t[1]["online"] is False, t[1])
check("无返回行标为无效 UID", t[2]["status"] == -1 and "无返回" in t[2]["status_text"], t[2])
check("渠道文本按博主区分", t[0]["channels"] == "desktop" and t[1]["channels"] == "email", (t[0], t[1]))
check("在线行开播时间格式化", t[0]["live_time"] != "—" and t[1]["live_time"] == "—", (t[0], t[1]))

print("\n[26] 轮播中可播放（online 判定含 live_status==2）")
rows_r = {
    1: {"uname": "主播A", "live_status": 2, "live_time": 1000, "room_id": 555, "title": "轮播中"},
    2: {"uname": "主播B", "live_status": 0, "live_time": 0, "room_id": 1024, "title": "标题B"},
}
t2 = sg.probe_rows_to_table(cfg, rows_r)
check("轮播行 online=True（可播）", t2[0]["online"] is True and t2[0]["status"] == 2, t2[0])
check("轮播行状态文案含「轮播」", "轮播" in t2[0]["status_text"], t2[0])
check("离线行仍不可播", t2[1]["online"] is False, t2[1])

print("\n===== 结果：%d PASS / %d FAIL =====" % (passed, failed))
sys.exit(0 if failed == 0 else 1)
