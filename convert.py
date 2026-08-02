import os
import glob
import json
import argparse
import base64
from concurrent.futures import ThreadPoolExecutor
from PIL import Image
import numpy as np

WIDTH = 64
HEIGHT = 64
FRAMES_DIR = "frames"
OUTPUT_DIR = "BP/scripts"
OUTPUT_FILE = f"{OUTPUT_DIR}/frames_data.js"

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

TERRACOTTA_PALETTE = [
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

def create_pillow_palette_image(palette):
    pal_img = Image.new("P", (1, 1))
    pal_data = []
    for item in palette:
        r, g, b = item["rgb"]
        pal_data.extend([int(r), int(g), int(b)])
    while len(pal_data) < 768:
        pal_data.extend(pal_data[:min(768 - len(pal_data), len(palette) * 3)])
    pal_img.putpalette(pal_data)
    return pal_img

def process_single_frame(path, pal_img, width, height, num_colors, dither):
    img = Image.open(path).convert("RGB")
    if img.size != (width, height):
        img = img.resize((width, height), Image.Resampling.LANCZOS)
    dither_mode = Image.Dither.FLOYDSTEINBERG if dither else Image.Dither.NONE
    q = img.quantize(palette=pal_img, dither=dither_mode)
    return np.array(q, dtype=np.int32) % num_colors

def parse_args():
    parser = argparse.ArgumentParser(
        description="PNG連番をMinecraft Bedrock用のフレーム差分データへ超高速変換します。"
    )
    parser.add_argument("--frames-dir", default=FRAMES_DIR, help="入力PNG連番のフォルダ")
    parser.add_argument("--output", default=OUTPUT_FILE, help="出力するframes_data.jsのパス")
    parser.add_argument("--width", type=int, default=WIDTH, help="出力幅（ブロック数）")
    parser.add_argument("--height", type=int, default=HEIGHT, help="出力高さ（ブロック数）")
    parser.add_argument("--palette", choices=PALETTES.keys(), default="concrete", help="使用するブロック色パレット")
    parser.add_argument("--dither", action="store_true", help="ディザリングを有効化")
    parser.add_argument("--threads", type=int, default=os.cpu_count() or 4, help="使用スレッド数")
    return parser.parse_args()

def encode_varint(val, byte_arr):
    while val >= 0x80:
        byte_arr.append((val & 0x7f) | 0x80)
        val >>= 7
    byte_arr.append(val & 0x7f)

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
    num_colors = len(palette)
    level_blocks = [{"block": item["block"], "states": {}} for item in palette]
    os.makedirs(os.path.dirname(os.path.abspath(output_file)), exist_ok=True)

    files = sorted(glob.glob(os.path.join(frames_dir, "*.png")))
    print("Frames:", len(files))
    if not files:
        print(f"警告: {frames_dir}/ にPNGが見つかりません。ffmpegでフレームを書き出してください。")
        return

    pal_img = create_pillow_palette_image(palette)

    # ThreadPoolExecutor による並列フレーム処理
    def task(file_path):
        return process_single_frame(file_path, pal_img, WIDTH, HEIGHT, num_colors, args.dither)

    max_workers = min(args.threads, len(files))
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        processed_frames = list(executor.map(task, files))

    old = np.full((HEIGHT, WIDTH), -1, dtype=int)
    frame_diffs = []

    for current in processed_frames:
        diff_mask = (current != old)
        changed_indices = np.flatnonzero(diff_mask)
        if len(changed_indices) > 0:
            changed_colors = current.flat[changed_indices]
            byte_arr = bytearray()
            prev_idx = 0
            for idx, color in zip(changed_indices, changed_colors):
                delta = int(idx) - prev_idx
                val = (delta << 6) | (int(color) & 0x3f)
                encode_varint(val, byte_arr)
                prev_idx = int(idx)
            packed_b64 = base64.b64encode(byte_arr).decode("ascii")
        else:
            packed_b64 = ""
        frame_diffs.append(packed_b64)
        old = current

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

if __name__ == "__main__":
    main()
