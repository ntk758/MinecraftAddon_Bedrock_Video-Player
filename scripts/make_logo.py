"""CurseForge 用ロゴ (正面から見たピクセルアートの画面 + ホログラムの粒子)。32x32 のマスを 1 マス 32 px で描く。"""
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

OUT = Path(__file__).resolve().parent.parent / "docs" / "images"
N, CELL = 32, 32
SIZE = N * CELL

C = {
    "frame": (46, 50, 60), "frame_side": (28, 30, 37), "frame_top": (66, 72, 86),
    "sky1": (110, 200, 245), "sky2": (64, 160, 225), "sun": (255, 214, 72),
    "grass1": (106, 190, 70), "grass2": (70, 150, 52), "dirt": (134, 94, 60),
    "play": (255, 255, 255), "play_shadow": (30, 60, 90),
    "leg": (36, 40, 48), "holo": (120, 235, 255), "holo2": (200, 250, 255),
}
grid = {}


def put(x, y, color):
    if 0 <= x < N and 0 <= y < N:
        grid[(x, y)] = color


# 画面の厚み (右と下にずらした濃い色) → 枠 → 映像
X0, Y0, X1, Y1 = 3, 8, 27, 24
for x in range(X0 + 1, X1 + 2):
    for y in range(Y0 + 1, Y1 + 2):
        put(x, y, C["frame_side"])
for x in range(X0, X1 + 1):
    for y in range(Y0, Y1 + 1):
        put(x, y, C["frame"])
for x in range(X0, X1 + 1):
    put(x, Y0, C["frame_top"])
# 映像: 空・太陽・丘・土
for x in range(X0 + 1, X1):
    for y in range(Y0 + 1, Y1):
        put(x, y, C["sky1"] if y < Y0 + 4 else C["sky2"])
for x, y in [(22, 10), (23, 10), (22, 11), (23, 11)]:
    put(x, y, C["sun"])
for x in range(X0 + 1, X1):
    top = int(round(18.5 + 1.6 * np.sin((x - 2) / 2.4)))
    for y in range(top, Y1):
        put(x, y, C["grass1"] if y == top else C["grass2"])
for x in range(X0 + 1, X1):
    put(x, Y1 - 1, C["dirt"])
# 再生ボタン (行ごとの幅 1,2,3,4,5,5,4,3,2,1) と右下の影
px, py = 13, 11
for i in range(10):
    w = min(i, 9 - i) + 1
    for k in range(w):
        put(px + k + 1, py + i + 1, C["play_shadow"])
for i in range(10):
    w = min(i, 9 - i) + 1
    for k in range(w):
        put(px + k, py + i, C["play"])
# 脚
for x, y in [(8, 25), (8, 26), (9, 26), (22, 25), (22, 26), (21, 26)]:
    put(x, y, C["leg"])
# ホログラムの粒子 (画面から立ちのぼるブロック)
holo = [(6, 5, 1), (10, 3, 1), (15, 4, 2), (20, 2, 1), (24, 5, 1), (12, 1, 1), (27, 3, 1)]

# --- 背景 ---
yy, xx = np.mgrid[0:SIZE, 0:SIZE].astype(np.float32) / SIZE
bg = np.array([20, 26, 56], np.float32) * (1 - yy[..., None]) + np.array([58, 24, 92], np.float32) * yy[..., None]
glow = np.exp(-(((xx - 0.5) ** 2) / 0.10 + ((yy - 0.5) ** 2) / 0.08))[..., None]
bg = bg + glow * np.array([30, 90, 130], np.float32)
canvas = Image.fromarray(np.clip(bg, 0, 255).astype(np.uint8)).convert("RGBA")

# 画面の後ろの発光
halo = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
ImageDraw.Draw(halo).rectangle([X0 * CELL, Y0 * CELL, (X1 + 1) * CELL, (Y1 + 1) * CELL], fill=(110, 230, 255, 170))
halo = halo.filter(ImageFilter.GaussianBlur(40))
canvas = Image.alpha_composite(canvas, halo)


def block(draw, x, y, color, size=1):
    """1 マスを、左上が明るく右下が暗い縁の付いたブロックとして描く。"""
    x0, y0 = x * CELL, y * CELL
    x1, y1 = x0 + size * CELL - 1, y0 + size * CELL - 1
    edge = max(3, size * CELL // 10)
    light = tuple(min(255, int(c * 1.12 + 8)) for c in color)
    dark = tuple(int(c * 0.80) for c in color)
    draw.rectangle([x0, y0, x1, y1], fill=color)
    draw.rectangle([x0, y0, x1, y0 + edge - 1], fill=light)
    draw.rectangle([x0, y0, x0 + edge - 1, y1], fill=light)
    draw.rectangle([x0, y1 - edge + 1, x1, y1], fill=dark)
    draw.rectangle([x1 - edge + 1, y0, x1, y1], fill=dark)


art = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
d = ImageDraw.Draw(art)
for (x, y), color in grid.items():
    block(d, x, y, color)

# 粒子は発光させる
particles = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
pd = ImageDraw.Draw(particles)
for x, y, s in holo:
    block(pd, x, y, C["holo"] if s == 1 else C["holo2"], s)
glow_p = particles.filter(ImageFilter.GaussianBlur(14))
canvas = Image.alpha_composite(canvas, glow_p)
canvas = Image.alpha_composite(canvas, glow_p)

# 影
shadow = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
shadow.paste((0, 0, 0, 140), (16, 24), art.getchannel("A"))
shadow = shadow.filter(ImageFilter.GaussianBlur(14))
canvas = Image.alpha_composite(canvas, shadow)
canvas = Image.alpha_composite(canvas, art)
canvas = Image.alpha_composite(canvas, particles)

final = canvas.convert("RGB")
final.save(OUT / "logo.png")
final.resize((400, 400), Image.Resampling.LANCZOS).save(OUT / "logo_400.png")
print("ok")
