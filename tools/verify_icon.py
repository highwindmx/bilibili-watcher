# -*- coding: utf-8 -*-
"""Structural (non-visual) validation of generated icon frames."""
import os, struct, sys
from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)  # 产物在项目根目录
ICO = os.path.join(ROOT, "app.ico")

with open(ICO, "rb") as f:
    data = f.read()
reserved, typ, count = struct.unpack_from("<HHH", data, 0)
print("ICONDIR:", reserved, typ, count)
all_ok = True
for i in range(count):
    w, h, _, _, planes, bpp, ln, off = struct.unpack_from("<BBBBHHII", data, 6 + 16 * i)
    w = 256 if w == 0 else w
    h = 256 if h == 0 else h
    blob = data[off:off + ln]
    assert blob[:8] == b"\x89PNG\r\n\x1a\n", "frame %d not PNG" % i
    import io
    im = Image.open(io.BytesIO(blob)).convert("RGBA")
    px = im.load()
    W, H = im.size
    # corner must be transparent (rounded rect), center must be pink-ish
    corner = px[0, 0]
    # 不取正中心（"Bo" 笔画可能正好覆盖），取四个粉色区域采样点
    probes = [(int(W*0.25), int(H*0.30)), (int(W*0.75), int(H*0.30)),
              (int(W*0.25), int(H*0.75)), (int(W*0.75), int(H*0.75))]
    mids = [px[x, y] for x, y in probes]
    corner_transparent = corner[3] < 20
    center_pink = sum(1 for m in mids if m[0] > 200 and m[1] < 190 and m[3] > 200) >= 3
    mid = mids[0]
    # count white-ish text pixels (R,G,B all high, alpha high)
    white = 0
    for y in range(H):
        for x in range(W):
            r, g, b, a = px[x, y]
            if a > 200 and r > 230 and g > 230 and b > 230:
                white += 1
    ratio = white / (W * H)
    # text must not touch the border (no clipping)
    border_clean = all(px[x, 0][3] < 20 and px[x, H-1][3] < 20 and
                       px[0, y][3] < 20 and px[W-1, y][3] < 20
                       for x in range(W) for y in range(H))
    ok = corner_transparent and center_pink and 0.02 < ratio < 0.35 and border_clean
    all_ok &= ok
    print("  %3dx%-3d corner_alpha=%3d center=(%3d,%3d,%3d,%3d) white_ratio=%.3f border_clean=%s -> %s"
          % (w, h, corner[3], *mid, ratio, border_clean, "OK" if ok else "FAIL"))

# ASCII art of the 64px frame: '#'=pink, '.'=white text, ' '=transparent
im = Image.open(os.path.join(ROOT, "tray.png")).convert("RGBA")
s = im.size[0]
step = 2
px = im.load()
print("\nASCII preview of tray.png (64px, sampled every 2px; #=pink .=white space=transparent):")
for y in range(0, s, step):
    line = ""
    for x in range(0, s, step):
        r, g, b, a = px[x, y]
        if a < 60:
            line += " "
        elif r > 230 and g > 230 and b > 230:
            line += "."
        else:
            line += "#"
    print("  " + line)
print("\nSTRUCTURAL_VALIDATION:", "PASS" if all_ok else "FAIL")
sys.exit(0 if all_ok else 1)
