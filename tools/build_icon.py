#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
生成应用图标 app.ico（多尺寸）与托盘用 tray.png。

设计：圆角矩形，B 站粉 (#FB7299) 底，白色粗体 ASCII "Bo"。
遵循 win-app-multi-size-icon 规范：
  - 9 个尺寸逐帧手调（字号/圆角/内边距），不做 256->16 的 LANCZOS 缩放
  - 纯 struct 手动打包 ICO（Vista+ 支持 PNG 编码帧）
  - 16/20/24 单独 hand-tune，避免笔画粘连

运行（在项目根目录，用带 Pillow 的解释器，本机为 uv venv）：
  .venv\\Scripts\\python.exe tools\\build_icon.py
  .venv\\Scripts\\python.exe tools\\build_icon.py --preview   # 额外出 _icon_preview/_sheet.png

构建期依赖：Pillow。运行期（tray_app.py）不需要重新生成，直接用 app.ico / tray.png。
"""
from __future__ import annotations

import argparse
import os
import struct
import sys

try:
    from PIL import Image, ImageDraw, ImageFont
except ImportError:
    sys.exit("需要 Pillow：请在项目根目录运行 .venv\\Scripts\\python.exe tools\\build_icon.py（先跑 启动.bat）")

# 本脚本位于 tools/，产物写到项目根目录
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
ICO_PATH = os.path.join(ROOT, "app.ico")
TRAY_PNG = os.path.join(ROOT, "tray.png")
PREVIEW_DIR = os.path.join(ROOT, "_icon_preview")

PINK = (251, 114, 153, 255)      # B 站粉 #FB7299
WHITE = (255, 255, 255, 255)
TEXT = "Bo"

FONT_CANDIDATES = [
    r"C:\Windows\Fonts\seguibl.ttf",   # Segoe UI Black（最粗，小尺寸最清晰）
    r"C:\Windows\Fonts\arialbd.ttf",
    r"C:\Windows\Fonts\segoeuib.ttf",
    r"C:\Windows\Fonts\calibrib.ttf",
    r"C:\Windows\Fonts\msyhbd.ttc",
]

# 逐尺寸手调：pad=内边距占比, radius=圆角半径占比, font=字号占比, dy=文字垂直微调占比
PER_SIZE_TUNE = {
    # 16/20 内边距特意留大（0.10/0.09）：否则 4x 超采样后圆角边缘贴到画布边
    # 会出现半透明描边像素，Explorer 256 视图下看着像有白边/毛刺。
    16:  dict(pad=0.10, radius=0.28, font=0.46, dy=0.00),
    20:  dict(pad=0.09, radius=0.26, font=0.50, dy=0.00),
    24:  dict(pad=0.08, radius=0.24, font=0.54, dy=0.00),
    32:  dict(pad=0.10, radius=0.22, font=0.56, dy=0.00),
    40:  dict(pad=0.10, radius=0.22, font=0.58, dy=0.00),
    48:  dict(pad=0.10, radius=0.22, font=0.59, dy=0.00),
    64:  dict(pad=0.10, radius=0.22, font=0.60, dy=0.00),
    128: dict(pad=0.10, radius=0.22, font=0.60, dy=0.00),
    256: dict(pad=0.10, radius=0.22, font=0.60, dy=0.00),
}
SIZES = list(PER_SIZE_TUNE.keys())


def pick_font() -> str | None:
    for p in FONT_CANDIDATES:
        if os.path.exists(p):
            return p
    return None


def render(size: int) -> Image.Image:
    """在 4x 超采样画布上绘制，再 LANCZOS 缩到目标尺寸，保证圆角/文字边缘平滑。"""
    t = PER_SIZE_TUNE[size]
    ss = 4  # supersample
    s = size * ss
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    pad = int(s * t["pad"])
    box = (pad, pad, s - pad - 1, s - pad - 1)
    radius = int(s * t["radius"])
    d.rounded_rectangle(box, radius=radius, fill=PINK)

    font_path = pick_font()
    font_px = int(s * t["font"])
    try:
        font = ImageFont.truetype(font_path, font_px) if font_path else ImageFont.load_default()
    except OSError:
        font = ImageFont.load_default()

    # 用 anchor='mm' 精确居中；dy 做像素级微调（"Bo" 有下伸部视觉偏上）
    cx, cy = s / 2, s / 2 + int(s * t["dy"])
    try:
        d.text((cx, cy), TEXT, font=font, fill=WHITE, anchor="mm")
    except TypeError:  # 极老 Pillow 无 anchor
        bbox = d.textbbox((0, 0), TEXT, font=font)
        tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
        d.text((cx - tw / 2, cy - th / 2 - bbox[1]), TEXT, font=font, fill=WHITE)

    return img.resize((size, size), Image.LANCZOS)


def pack_ico(frames: list[tuple[int, bytes]], path: str) -> None:
    """手动打包 ICONDIR + ICONDIRENTRY*n + PNG 帧。"""
    import io
    n = len(frames)
    out = bytearray()
    out += struct.pack("<HHH", 0, 1, n)  # reserved, type=1(icon), count
    offset = 6 + 16 * n
    for size, _ in frames:
        w = 0 if size >= 256 else size
        h = 0 if size >= 256 else size
        # bWidth bHeight bColorCount bReserved wPlanes wBitCount dwBytesInRes dwImageOffset
        # 占位长度后面回填
        out += struct.pack("<BBBBHHII", w, h, 0, 0, 1, 32, 0, offset)
    # 逐帧写 PNG 并回填长度/偏移
    entry_off = 6
    for size, _ in frames:
        buf = io.BytesIO()
        frames_by_size[size].save(buf, format="PNG")
        blob = buf.getvalue()
        struct.pack_into("<I", out, entry_off + 8, len(blob))
        struct.pack_into("<I", out, entry_off + 12, offset)
        out += blob
        offset += len(blob)
        entry_off += 16
    with open(path, "wb") as f:
        f.write(out)


frames_by_size: dict[int, Image.Image] = {}


def main() -> None:
    ap = argparse.ArgumentParser(description="生成 B 站开播提醒应用图标")
    ap.add_argument("--preview", action="store_true", help="额外输出各尺寸拼图")
    args = ap.parse_args()

    for s in SIZES:
        frames_by_size[s] = render(s)

    # 占位 frames 列表（真正的 PNG 在 pack_ico 里按 size 从 frames_by_size 取）
    frames = [(s, b"") for s in SIZES]
    pack_ico(frames, ICO_PATH)

    # 托盘用 64x64 PNG（pystray 直接吃 PIL Image，也存一份便于查看/复用）
    frames_by_size[64].save(TRAY_PNG, format="PNG")

    # 验证：解析 ICONDIR，确认 9 帧且每帧是 PNG
    with open(ICO_PATH, "rb") as f:
        data = f.read()
    reserved, typ, count = struct.unpack_from("<HHH", data, 0)
    ok = (reserved == 0 and typ == 1 and count == len(SIZES))
    sizes_found = []
    for i in range(count):
        w, h, _, _, planes, bpp, ln, off = struct.unpack_from("<BBBBHHII", data, 6 + 16 * i)
        w = 256 if w == 0 else w
        sizes_found.append(w)
        if data[off:off + 8] != b"\x89PNG\r\n\x1a\n":
            ok = False
    print("app.ico: %d bytes, %d frames %s" % (len(data), count, sizes_found))
    print("PNG-signature-every-frame: %s" % ("OK" if ok else "FAIL"))
    print("tray.png: %dx%d" % (frames_by_size[64].width, frames_by_size[64].height))
    if not ok:
        sys.exit(1)

    if args.preview:
        os.makedirs(PREVIEW_DIR, exist_ok=True)
        cell = 280
        cols = 3
        rows = (len(SIZES) + cols - 1) // cols
        sheet = Image.new("RGBA", (cols * cell, rows * cell), (245, 245, 245, 255))
        sd = ImageDraw.Draw(sheet)
        for i, s in enumerate(SIZES):
            im = frames_by_size[s]
            x = (i % cols) * cell + (cell - s) // 2
            y = (i // cols) * cell + 30
            sheet.alpha_composite(im, (x, y))
            sd.text(((i % cols) * cell + 10, (i // cols) * cell + 6),
                    "%d x %d" % (s, s), fill=(30, 30, 30, 255))
        p = os.path.join(PREVIEW_DIR, "_sheet.png")
        sheet.save(p)
        print("preview:", p)


if __name__ == "__main__":
    main()
