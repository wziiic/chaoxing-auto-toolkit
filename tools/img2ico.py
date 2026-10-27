# -*- coding: utf-8 -*-
"""
把任意图片转成 Windows 多尺寸图标 .ico，并可把连到边缘的纯色背景去掉。

用法:
    python img2ico.py <输入图片> <输出.ico> [--keep-bg] [--preview 预览.png]

默认会把"从四角能泛洪到的浅色背景"变透明（适合方形 logo 贴到桌面）。
"""
import argparse
import sys

import numpy as np
from PIL import Image, ImageDraw

SIZES = [(256, 256), (128, 128), (64, 64), (48, 48), (32, 32), (16, 16)]


def strip_border_bg(im: Image.Image, thresh: int = 60) -> Image.Image:
    """把与边缘连通、且与角落颜色接近的区域设为透明。"""
    im = im.convert("RGBA")
    w, h = im.size

    # 用 floodfill 在 RGB 副本上标记背景
    tmp = im.convert("RGB")
    marker = (255, 0, 255)
    for pt in [(0, 0), (w - 1, 0), (0, h - 1), (w - 1, h - 1)]:
        try:
            ImageDraw.floodfill(tmp, pt, marker, thresh=thresh)
        except Exception:
            pass

    bg = np.all(np.array(tmp) == np.array(marker), axis=-1)
    arr = np.array(im)
    arr[bg, 3] = 0                      # 背景透明
    return Image.fromarray(arr, "RGBA")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("src")
    ap.add_argument("dst")
    ap.add_argument("--keep-bg", action="store_true", help="不去背景")
    ap.add_argument("--preview", default="", help="额外导出一张 PNG 预览")
    ap.add_argument("--thresh", type=int, default=60)
    args = ap.parse_args()

    im = Image.open(args.src)
    print(f"读入: {im.format} {im.size} {im.mode}")
    im = im.convert("RGBA")

    # 强制正方形（居中裁切），避免图标被拉伸
    w, h = im.size
    side = min(w, h)
    left, top = (w - side) // 2, (h - side) // 2
    im = im.crop((left, top, left + side, top + side))

    if not args.keep_bg:
        im = strip_border_bg(im, args.thresh)
        print("已去除边缘背景")

    im.save(args.dst, format="ICO", sizes=SIZES)
    print(f"已生成: {args.dst}  尺寸: {', '.join(f'{s[0]}' for s in SIZES)}")

    if args.preview:
        im.resize((256, 256), Image.LANCZOS).save(args.preview)
        print(f"预览: {args.preview}")


if __name__ == "__main__":
    sys.exit(main())
