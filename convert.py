"""
動画 → Minecraft統合版 差分データ生成スクリプト (v2: カラーパレット対応版)

v1(11段階グレースケール)からの変更点:
- 発光ブロックの明度だけで階調を作る方式をやめ、
  Minecraft公式のマップカラー定義(Java Edition Wiki "Map item format"の
  Concreteエントリ、Spigot MaterialMapColor.java 由来)にある
  16色のconcreteブロックのRGB値を基準パレットとして採用。
- 各ピクセルの色を、パレット中で最も色距離が近いconcreteブロックに
  マッピングする(最近傍色マッチング)。
- これにより白黒Bad Apple以外の、任意のカラー動画にも対応できる。

前提: frames/ フォルダにffmpegで書き出したPNG連番を置くこと
  ffmpeg -i yourvideo.mp4 -vf "fps=20,scale=64:64:flags=lanczos" frames/output_%04d.png
"""

from PIL import Image
import numpy as np
import os
import glob
import json
import argparse

WIDTH = 64
HEIGHT = 64
FRAMES_DIR = "frames"
OUTPUT_DIR = "BP/scripts"
OUTPUT_FILE = f"{OUTPUT_DIR}/frames_data.js"

# Minecraft公式マップカラー定義(Java Edition Wiki "Map item format"より、
# Concreteブロックの基準RGB値。第3シェード=素の色を採用)。
# ブロックIDはBedrock公式 "Default Minecraft Block Listings"
# (learn.microsoft.com)で実在確認済みの命名パターン(<色名>_concrete)。
CONCRETE_PALETTE = [
    {"name": "white",      "block": "minecraft:white_concrete",      "rgb": (255, 255, 255)},
    {"name": "orange",     "block": "minecraft:orange_concrete",     "rgb": (216, 127, 51)},
    {"name": "magenta",    "block": "minecraft:magenta_concrete",    "rgb": (178, 76, 216)},
    {"name": "light_blue", "block": "minecraft:light_blue_concrete", "rgb": (102, 153, 216)},
    {"name": "yellow",     "block": "minecraft:yellow_concrete",     "rgb": (229, 229, 51)},
    {"name": "lime",       "block": "minecraft:lime_concrete",       "rgb": (127, 204, 25)},
    {"name": "pink",       "block": "minecraft:pink_concrete",       "rgb": (242, 127, 165)},
    {"name": "gray",       "block": "minecraft:gray_concrete",       "rgb": (76, 76, 76)},
    {"name": "light_gray", "block": "minecraft:light_gray_concrete", "rgb": (153, 153, 153)},
    {"name": "cyan",       "block": "minecraft:cyan_concrete",       "rgb": (76, 127, 153)},
    {"name": "purple",     "block": "minecraft:purple_concrete",     "rgb": (127, 63, 178)},
    {"name": "blue",       "block": "minecraft:blue_concrete",       "rgb": (51, 76, 178)},
    {"name": "brown",      "block": "minecraft:brown_concrete",      "rgb": (102, 76, 51)},
    {"name": "green",      "block": "minecraft:green_concrete",      "rgb": (102, 127, 51)},
    {"name": "red",        "block": "minecraft:red_concrete",        "rgb": (153, 51, 51)},
    {"name": "black",      "block": "minecraft:black_concrete",      "rgb": (25, 25, 25)},
]

# テラコッタを加えると、中間色・低彩度色をより近く表現できる。
TERRACOTTA_PALETTE = [
    # 統合版では無色テラコッタのブロックIDはminecraft:terracottaではなくhardened_clay。
    {"name": "terracotta", "block": "minecraft:hardened_clay", "rgb": (152, 94, 67)},
    {"name": "white_terracotta", "block": "minecraft:white_terracotta", "rgb": (209, 178, 161)},
    {"name": "orange_terracotta", "block": "minecraft:orange_terracotta", "rgb": (161, 83, 37)},
    {"name": "magenta_terracotta", "block": "minecraft:magenta_terracotta", "rgb": (149, 88, 108)},
    {"name": "light_blue_terracotta", "block": "minecraft:light_blue_terracotta", "rgb": (113, 108, 137)},
    {"name": "yellow_terracotta", "block": "minecraft:yellow_terracotta", "rgb": (186, 133, 35)},
    {"name": "lime_terracotta", "block": "minecraft:lime_terracotta", "rgb": (103, 117, 53)},
    {"name": "pink_terracotta", "block": "minecraft:pink_terracotta", "rgb": (160, 77, 78)},
    {"name": "gray_terracotta", "block": "minecraft:gray_terracotta", "rgb": (57, 42, 35)},
    {"name": "light_gray_terracotta", "block": "minecraft:light_gray_terracotta", "rgb": (135, 107, 98)},
    {"name": "cyan_terracotta", "block": "minecraft:cyan_terracotta", "rgb": (86, 91, 91)},
    {"name": "purple_terracotta", "block": "minecraft:purple_terracotta", "rgb": (118, 70, 86)},
    {"name": "blue_terracotta", "block": "minecraft:blue_terracotta", "rgb": (74, 59, 91)},
    {"name": "brown_terracotta", "block": "minecraft:brown_terracotta", "rgb": (77, 51, 35)},
    {"name": "green_terracotta", "block": "minecraft:green_terracotta", "rgb": (76, 83, 42)},
    {"name": "red_terracotta", "block": "minecraft:red_terracotta", "rgb": (143, 61, 47)},
    {"name": "black_terracotta", "block": "minecraft:black_terracotta", "rgb": (37, 22, 16)},
]

PALETTES = {
    "concrete": CONCRETE_PALETTE,
    "expanded": CONCRETE_PALETTE + TERRACOTTA_PALETTE,
}


def convert_image(path, palette, dither):
    """画像を読み込み、各ピクセルを最近傍のパレット色indexに変換したHxW配列を返す"""
    img = Image.open(path).convert("RGB")
    img = img.resize((WIDTH, HEIGHT), Image.Resampling.LANCZOS)
    pixels = np.array(img, dtype=np.float64)  # (H, W, 3)

    palette_rgb = np.array([item["rgb"] for item in palette], dtype=np.float64)
    if not dither:
        diff = pixels[:, :, np.newaxis, :] - palette_rgb[np.newaxis, np.newaxis, :, :]
        return np.argmin(np.sum(diff ** 2, axis=3), axis=2)

    # Floyd-Steinbergディザリング: 色数が限られていても中間色を見た目上滑らかにする。
    working = pixels.copy()
    result = np.empty((HEIGHT, WIDTH), dtype=int)
    for y in range(HEIGHT):
        for x in range(WIDTH):
            distances = np.sum((palette_rgb - working[y, x]) ** 2, axis=1)
            index = int(np.argmin(distances))
            result[y, x] = index
            error = working[y, x] - palette_rgb[index]
            if x + 1 < WIDTH:
                working[y, x + 1] += error * 7 / 16
            if y + 1 < HEIGHT:
                if x > 0:
                    working[y + 1, x - 1] += error * 3 / 16
                working[y + 1, x] += error * 5 / 16
                if x + 1 < WIDTH:
                    working[y + 1, x + 1] += error / 16
    return result


def parse_args():
    parser = argparse.ArgumentParser(
        description="PNG連番をMinecraft Bedrock用のフレーム差分データへ変換します。"
    )
    parser.add_argument("--frames-dir", default=FRAMES_DIR, help="入力PNG連番のフォルダ")
    parser.add_argument("--output", default=OUTPUT_FILE, help="出力するframes_data.jsのパス")
    parser.add_argument("--width", type=int, default=WIDTH, help="出力幅（ブロック数）")
    parser.add_argument("--height", type=int, default=HEIGHT, help="出力高さ（ブロック数）")
    parser.add_argument("--palette", choices=PALETTES.keys(), default="concrete", help="使用するブロック色パレット")
    parser.add_argument("--dither", action="store_true", help="Floyd-Steinbergディザリングを有効化")
    return parser.parse_args()


def main():
    global WIDTH, HEIGHT
    args = parse_args()
    if args.width <= 0 or args.height <= 0:
        raise ValueError("幅と高さは1以上にしてください")

    WIDTH = args.width
    HEIGHT = args.height
    frames_dir = args.frames_dir
    output_file = args.output
    palette = PALETTES[args.palette]
    level_blocks = [{"block": item["block"], "states": {}} for item in palette]
    os.makedirs(os.path.dirname(os.path.abspath(output_file)), exist_ok=True)

    files = sorted(glob.glob(os.path.join(frames_dir, "*.png")))
    print("Frames:", len(files))
    if not files:
        print(f"警告: {frames_dir}/ にPNGが見つかりません。ffmpegでフレームを書き出してください。")
        return

    old = np.full((HEIGHT, WIDTH), -1, dtype=int)
    frame_diffs = []

    for number, file in enumerate(files):
        current = convert_image(file, palette, args.dither)
        changes = []
        ys, xs = np.where(current != old)
        for y, x in zip(ys, xs):
            changes.append([int(x), int(y), int(current[y][x])])
        frame_diffs.append(changes)
        old = current
        if number % 50 == 0:
            print(f"Frame {number}/{len(files)} - changes: {len(changes)}")

    data = {
        "width": WIDTH,
        "height": HEIGHT,
        "level_blocks": level_blocks,
        "frame_count": len(frame_diffs),
        "frames": frame_diffs,
    }

    json_text = json.dumps(data, separators=(",", ":"))
    with open(output_file, "w", encoding="utf-8") as f:
        f.write(f"export const FRAME_DATA = {json_text};\n")

    size_mb = os.path.getsize(output_file) / 1024 / 1024
    print(f"Done! {output_file} ({size_mb:.2f} MB, {len(frame_diffs)} frames)")
    if size_mb > 3:
        print("警告: frames_data.js が大きすぎる可能性があります。フレーム数や解像度を落とすことを検討してください。")


if __name__ == "__main__":
    main()
