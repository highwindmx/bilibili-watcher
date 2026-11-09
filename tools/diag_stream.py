#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
流播放诊断：取一个真实流地址，分别用 Python 请求与 VLC（dummy 界面 + 详细日志）验证，
定位「VLC 一闪而过」的原因（403 防盗链 / TLS 证书 / 失效 URL / 参数问题）。
用法：.venv/Scripts/python.exe tools/diag_stream.py [room_id]
"""
import os
import subprocess
import sys
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import watcher

VLC_CANDIDATES = [
    r"C:\Program Files\VideoLAN\VLC\vlc.exe",
    r"C:\Program Files (x86)\VideoLAN\VLC\vlc.exe",
]


def http_probe(url: str, tag: str) -> None:
    req = urllib.request.Request(url, headers={
        "Referer": watcher.REFERER, "User-Agent": watcher.USER_AGENT,
        "Range": "bytes=0-1023"})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            print("  [%s] Python 请求 OK: HTTP %s, %s" % (
                tag, r.status, r.headers.get("Content-Type")))
    except Exception as e:  # noqa: BLE001
        print("  [%s] Python 请求失败: %s: %s" % (tag, type(e).__name__, e))


def vlc_probe(url: str, tag: str) -> None:
    vlc = next((c for c in VLC_CANDIDATES if os.path.exists(c)), None)
    if not vlc:
        print("  [%s] 未找到 VLC，跳过" % tag)
        return
    cmd = [vlc, "-vv", "--intf", "dummy", "--play-and-exit",
           "--http-referrer=" + watcher.REFERER,
           "--http-user-agent=" + watcher.USER_AGENT, url]
    try:
        p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                             text=True, errors="replace")
    except Exception as e:  # noqa: BLE001
        print("  [%s] VLC 启动失败: %s" % (tag, e))
        return
    time.sleep(10)
    p.terminate()
    out, _ = p.communicate(timeout=5)
    keys = ("error", "fail", "403", "gnutls", "certificate", "tls", "refused",
            "timeout", "invalid", "404", "denied", "https")
    hits = [ln for ln in (out or "").splitlines()
            if any(k in ln.lower() for k in keys)]
    print("  [%s] VLC 退出码=%s，日志关键行 %d 条：" % (tag, p.returncode, len(hits)))
    for ln in hits[:12]:
        print("    " + ln.strip()[:180])


def main():
    cfg = watcher.load_config(os.path.join(ROOT, "config.json"))
    room = int(sys.argv[1]) if len(sys.argv) > 1 else None
    if not room:
        uids = cfg["uids"]
        rows = watcher.fetch_status(uids, cfg)
        for u in uids:
            r = rows.get(u) or {}
            print("uid=%s status=%s room=%s name=%s" % (
                u, r.get("live_status"), r.get("room_id"), r.get("uname")))
        room = next((int(rows[u]["room_id"]) for u in uids
                     if (rows.get(u) or {}).get("room_id")), 0)
    if not room:
        print("没有可用房间，无法诊断")
        return
    print("== 诊断房间 %s ==" % room)

    print("\n[1] playUrl 路径（现用首选）")
    try:
        p = watcher.http_get_json(
            "%s?cid=%s&qn=0&platform=web&otype=json" % (watcher.API_PLAYURL, room), cfg)
        u1 = watcher.parse_playurl_payload(p)
        print("  playUrl 成功 -> %s" % u1[:110])
        http_probe(u1, "playUrl")
        vlc_probe(u1, "playUrl")
    except Exception as e:  # noqa: BLE001
        print("  playUrl 失败: %s" % e)

    print("\n[2] getRoomPlayInfo 路径（现用 fallback）")
    try:
        p = watcher.http_get_json(
            "%s?room_id=%s&protocol=0,1&format=0,1,2&codec=0,1,2&qn=0&platform=web&ptype=8"
            % (watcher.API_ROOMPLAYINFO, room), cfg)
        u2 = watcher.parse_roomplayinfo_payload(p)
        print("  getRoomPlayInfo 成功 -> %s" % u2[:110])
        http_probe(u2, "getRoomPlayInfo")
        vlc_probe(u2, "getRoomPlayInfo")
    except Exception as e:  # noqa: BLE001
        print("  getRoomPlayInfo 失败: %s" % e)


if __name__ == "__main__":
    main()
