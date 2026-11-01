# -*- coding: utf-8 -*-
"""生成应用图标 assets/lantern.ico（不依赖外部素材，纯 PIL 绘制）。"""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "assets" / "lantern.ico"
S = 1024


def gradient(size: int, c1: tuple, c2: tuple) -> Image.Image:
    img = Image.new("RGB", (size, size), c1)
    d = ImageDraw.Draw(img)
    for y in range(size):
        t = y / max(1, size - 1)
        col = tuple(int(c1[i] + (c2[i] - c1[i]) * t) for i in range(3))
        d.line([(0, y), (size, y)], fill=col)
    return img


def rounded_mask(size: int, radius: int) -> Image.Image:
    m = Image.new("L", (size, size), 0)
    ImageDraw.Draw(m).rounded_rectangle([0, 0, size - 1, size - 1], radius=radius, fill=255)
    return m


def build() -> Image.Image:
    bg = gradient(S, (246, 178, 107), (194, 106, 29))

    # 顶部高光，让渐变不那么平
    glow = Image.new("L", (S, S), 0)
    ImageDraw.Draw(glow).ellipse([-S * 0.25, -S * 0.55, S * 1.15, S * 0.55], fill=90)
    white = Image.new("RGB", (S, S), (255, 255, 255))
    bg = Image.composite(white, bg, glow.point(lambda v: v // 2))

    canvas = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    canvas.paste(bg, (0, 0), rounded_mask(S, int(S * 0.22)))
    d = ImageDraw.Draw(canvas)

    cream = (255, 249, 240, 255)
    warm = (255, 236, 210, 255)

    # 提手
    d.arc([S * 0.34, S * 0.16, S * 0.66, S * 0.44], start=185, end=355,
          fill=cream, width=int(S * 0.035))

    # 顶盖 + 底座
    d.rounded_rectangle([S * 0.33, S * 0.30, S * 0.67, S * 0.375], radius=S * 0.022, fill=warm)
    d.rounded_rectangle([S * 0.355, S * 0.755, S * 0.645, S * 0.825], radius=S * 0.022, fill=warm)

    # 灯身
    d.rounded_rectangle([S * 0.30, S * 0.365, S * 0.70, S * 0.765], radius=S * 0.06, fill=cream)

    # 灯身里的两个竖向装饰条 + 中间光点
    d.rounded_rectangle([S * 0.345, S * 0.43, S * 0.375, S * 0.70], radius=S * 0.015,
                        fill=(240, 170, 100, 255))
    d.rounded_rectangle([S * 0.625, S * 0.43, S * 0.655, S * 0.70], radius=S * 0.015,
                        fill=(240, 170, 100, 255))
    d.ellipse([S * 0.455, S * 0.50, S * 0.545, S * 0.59], fill=(226, 138, 58, 255))
    return canvas


def main() -> None:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    big = build()
    sizes = [256, 128, 96, 64, 48, 40, 32, 24, 20, 16]
    big.save(OUT, format="ICO",
             sizes=[(s, s) for s in sizes])
    big.resize((256, 256), Image.LANCZOS).save(OUT.with_suffix(".png"))
    print(f"图标已生成：{OUT}")


if __name__ == "__main__":
    main()
