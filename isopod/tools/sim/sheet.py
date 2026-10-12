#!/usr/bin/env python3
"""Lay a folder's screenshots out as thumbnails, each over its name: sheet.py <dir> <out.png>"""
import glob
import os
import sys

from PIL import Image, ImageDraw, ImageFont

FONT = "/usr/share/fonts/pretendard/Pretendard-Medium.otf"
W, H, LABEL, GAP, COLUMNS = 320, 240, 24, 12, 4


def main(src, out):
    files = sorted(glob.glob(os.path.join(src, "*.png")))
    rows = (len(files) + COLUMNS - 1) // COLUMNS
    sheet = Image.new("RGB", (COLUMNS * (W + GAP) + GAP, rows * (H + LABEL + GAP) + GAP), (236, 236, 240))
    draw = ImageDraw.Draw(sheet)
    font = ImageFont.truetype(FONT, 14) if os.path.exists(FONT) else ImageFont.load_default()
    for i, path in enumerate(files):
        x, y = GAP + i % COLUMNS * (W + GAP), GAP + i // COLUMNS * (H + LABEL + GAP)
        sheet.paste(Image.open(path).convert("RGB").resize((W, H), Image.LANCZOS), (x, y))
        draw.text((x + 2, y + H + 4), os.path.basename(path)[:-4], fill=(60, 60, 67), font=font)
    sheet.save(out)


if __name__ == "__main__":
    main(*sys.argv[1:3])
