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

FROGLIGHT_PALETTE = [
    {"name": "sea_lantern",          "block": "minecraft:sea_lantern",          "rgb": (172, 217, 203)},
    {"name": "glowstone",            "block": "minecraft:glowstone",            "rgb": (175, 133, 74)},
    {"name": "shroomlight",          "block": "minecraft:shroomlight",          "rgb": (246, 172, 119)},
    {"name": "pearlescent_froglight","block": "minecraft:pearlescent_froglight","rgb": (240, 215, 220)},
    {"name": "verdant_froglight",    "block": "minecraft:verdant_froglight",    "rgb": (210, 230, 200)},
    {"name": "ochre_froglight",      "block": "minecraft:ochre_froglight",      "rgb": (245, 225, 160)},
]

WOOL_PALETTE = [
    {"name": "white_wool",      "block": "minecraft:white_wool",      "rgb": (233, 236, 236)},
    {"name": "orange_wool",     "block": "minecraft:orange_wool",     "rgb": (240, 118, 19)},
    {"name": "magenta_wool",    "block": "minecraft:magenta_wool",    "rgb": (189, 68, 179)},
    {"name": "light_blue_wool", "block": "minecraft:light_blue_wool", "rgb": (58, 175, 217)},
    {"name": "yellow_wool",     "block": "minecraft:yellow_wool",     "rgb": (248, 197, 39)},
    {"name": "lime_wool",       "block": "minecraft:lime_wool",       "rgb": (112, 185, 25)},
    {"name": "pink_wool",       "block": "minecraft:pink_wool",       "rgb": (237, 141, 172)},
    {"name": "gray_wool",       "block": "minecraft:gray_wool",       "rgb": (62, 68, 71)},
    {"name": "light_gray_wool", "block": "minecraft:light_gray_wool", "rgb": (142, 142, 134)},
    {"name": "cyan_wool",       "block": "minecraft:cyan_wool",       "rgb": (21, 137, 145)},
    {"name": "purple_wool",     "block": "minecraft:purple_wool",     "rgb": (121, 42, 172)},
    {"name": "blue_wool",       "block": "minecraft:blue_wool",       "rgb": (53, 57, 157)},
    {"name": "brown_wool",      "block": "minecraft:brown_wool",      "rgb": (114, 71, 40)},
    {"name": "green_wool",      "block": "minecraft:green_wool",      "rgb": (84, 109, 27)},
    {"name": "red_wool",        "block": "minecraft:red_wool",        "rgb": (160, 39, 34)},
    {"name": "black_wool",      "block": "minecraft:black_wool",      "rgb": (20, 21, 25)},
]

SPECIAL_BLOCKS_PALETTE = [
    {"name": "gold_block",      "block": "minecraft:gold_block",      "rgb": (246, 208, 61)},
    {"name": "iron_block",      "block": "minecraft:iron_block",      "rgb": (220, 220, 220)},
    {"name": "diamond_block",   "block": "minecraft:diamond_block",   "rgb": (98, 237, 228)},
    {"name": "emerald_block",   "block": "minecraft:emerald_block",   "rgb": (43, 201, 93)},
    {"name": "lapis_block",     "block": "minecraft:lapis_block",     "rgb": (30, 67, 140)},
    {"name": "quartz_block",    "block": "minecraft:quartz_block",    "rgb": (235, 229, 222)},
    {"name": "obsidian",        "block": "minecraft:obsidian",        "rgb": (15, 11, 24)},
    {"name": "coal_block",      "block": "minecraft:coal_block",      "rgb": (18, 18, 18)},
]

CONCRETE_POWDER_PALETTE = [
    {"name": "white_concrete_powder",      "block": "minecraft:white_concrete_powder",      "rgb": (226, 227, 227)},
    {"name": "orange_concrete_powder",     "block": "minecraft:orange_concrete_powder",     "rgb": (227, 131, 31)},
    {"name": "magenta_concrete_powder",    "block": "minecraft:magenta_concrete_powder",    "rgb": (193, 84, 185)},
    {"name": "light_blue_concrete_powder", "block": "minecraft:light_blue_concrete_powder", "rgb": (74, 181, 213)},
    {"name": "yellow_concrete_powder",     "block": "minecraft:yellow_concrete_powder",     "rgb": (233, 199, 55)},
    {"name": "lime_concrete_powder",       "block": "minecraft:lime_concrete_powder",       "rgb": (126, 189, 41)},
    {"name": "pink_concrete_powder",       "block": "minecraft:pink_concrete_powder",       "rgb": (229, 153, 181)},
    {"name": "gray_concrete_powder",       "block": "minecraft:gray_concrete_powder",       "rgb": (76, 81, 84)},
    {"name": "light_gray_concrete_powder", "block": "minecraft:light_gray_concrete_powder", "rgb": (154, 154, 148)},
    {"name": "cyan_concrete_powder",       "block": "minecraft:cyan_concrete_powder",       "rgb": (36, 147, 156)},
    {"name": "purple_concrete_powder",     "block": "minecraft:purple_concrete_powder",     "rgb": (132, 56, 178)},
    {"name": "blue_concrete_powder",       "block": "minecraft:blue_concrete_powder",       "rgb": (70, 73, 166)},
    {"name": "brown_concrete_powder",      "block": "minecraft:brown_concrete_powder",      "rgb": (126, 85, 54)},
    {"name": "green_concrete_powder",      "block": "minecraft:green_concrete_powder",      "rgb": (97, 119, 44)},
    {"name": "red_concrete_powder",        "block": "minecraft:red_concrete_powder",        "rgb": (168, 54, 50)},
    {"name": "black_concrete_powder",      "block": "minecraft:black_concrete_powder",      "rgb": (26, 27, 32)},
]

WOOD_PLANKS_PALETTE = [
    {"name": "oak_planks",      "block": "minecraft:oak_planks",      "rgb": (162, 130, 78)},
    {"name": "spruce_planks",   "block": "minecraft:spruce_planks",   "rgb": (114, 84, 48)},
    {"name": "birch_planks",    "block": "minecraft:birch_planks",    "rgb": (192, 175, 121)},
    {"name": "jungle_planks",   "block": "minecraft:jungle_planks",   "rgb": (160, 115, 80)},
    {"name": "acacia_planks",   "block": "minecraft:acacia_planks",   "rgb": (168, 90, 50)},
    {"name": "dark_oak_planks", "block": "minecraft:dark_oak_planks", "rgb": (66, 43, 20)},
    {"name": "mangrove_planks", "block": "minecraft:mangrove_planks", "rgb": (118, 51, 51)},
    {"name": "cherry_planks",   "block": "minecraft:cherry_planks",   "rgb": (226, 178, 174)},
    {"name": "bamboo_planks",   "block": "minecraft:bamboo_planks",   "rgb": (196, 172, 70)},
    {"name": "crimson_planks",  "block": "minecraft:crimson_planks",  "rgb": (106, 50, 70)},
    {"name": "warped_planks",   "block": "minecraft:warped_planks",   "rgb": (43, 104, 99)},
]

STONE_MINERALS_PALETTE = [
    {"name": "stone",            "block": "minecraft:stone",            "rgb": (125, 125, 125)},
    {"name": "granite",          "block": "minecraft:granite",          "rgb": (149, 103, 85)},
    {"name": "diorite",          "block": "minecraft:diorite",          "rgb": (188, 188, 188)},
    {"name": "andesite",         "block": "minecraft:andesite",         "rgb": (134, 134, 134)},
    {"name": "deepslate",        "block": "minecraft:deepslate",        "rgb": (81, 81, 84)},
    {"name": "tuff",             "block": "minecraft:tuff",             "rgb": (108, 109, 102)},
    {"name": "calcite",          "block": "minecraft:calcite",          "rgb": (224, 223, 218)},
    {"name": "dripstone_block",  "block": "minecraft:dripstone_block",  "rgb": (134, 107, 92)},
    {"name": "basalt",           "block": "minecraft:basalt",           "rgb": (80, 80, 85)},
    {"name": "blackstone",       "block": "minecraft:blackstone",       "rgb": (42, 38, 45)},
    {"name": "netherrack",       "block": "minecraft:netherrack",       "rgb": (111, 54, 54)},
    {"name": "end_stone",        "block": "minecraft:end_stone",        "rgb": (220, 223, 158)},
    {"name": "purpur_block",     "block": "minecraft:purpur_block",     "rgb": (169, 125, 169)},
    {"name": "prismarine",       "block": "minecraft:prismarine",       "rgb": (99, 156, 151)},
    {"name": "prismarine_bricks","block": "minecraft:prismarine_bricks","rgb": (99, 171, 158)},
    {"name": "dark_prismarine",  "block": "minecraft:dark_prismarine",  "rgb": (51, 91, 75)},
    {"name": "clay",             "block": "minecraft:clay",             "rgb": (160, 166, 179)},
    {"name": "mud",              "block": "minecraft:mud",              "rgb": (60, 57, 62)},
    {"name": "snow_block",       "block": "minecraft:snow_block",       "rgb": (240, 249, 249)},
    {"name": "packed_ice",       "block": "minecraft:packed_ice",       "rgb": (160, 196, 245)},
    {"name": "blue_ice",         "block": "minecraft:blue_ice",         "rgb": (116, 167, 253)},
    {"name": "sponge",           "block": "minecraft:sponge",           "rgb": (195, 195, 80)},
]

ALL_BLOCKS = CONCRETE_PALETTE + TERRACOTTA_PALETTE + FROGLIGHT_PALETTE + WOOL_PALETTE + SPECIAL_BLOCKS_PALETTE
ULTRA_PALETTE_110 = ALL_BLOCKS + CONCRETE_POWDER_PALETTE + WOOD_PLANKS_PALETTE + STONE_MINERALS_PALETTE

PALETTES = {
    "concrete": CONCRETE_PALETTE,
    "expanded": CONCRETE_PALETTE + TERRACOTTA_PALETTE,
    "full": CONCRETE_PALETTE + TERRACOTTA_PALETTE + FROGLIGHT_PALETTE,
    "all_55": ALL_BLOCKS,
    "ultra_110": ULTRA_PALETTE_110,
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

def apply_ordered_dither_bias(img, palette_rgb):
    bayer_matrix = np.array([
        [ 0,  8,  2, 10],
        [12,  4, 14,  6],
        [ 3, 11,  1,  9],
        [15,  7, 13,  5]
    ], dtype=np.float32)
    bayer_matrix = (bayer_matrix / 16.0) - 0.5
    spread = 32.0
    bayer_matrix *= spread

    img_arr = np.array(img, dtype=np.float32)
    h, w, c = img_arr.shape
    
    bayer_tiled = np.tile(bayer_matrix, (h // 4 + 1, w // 4 + 1))[:h, :w]
    bayer_tiled = np.expand_dims(bayer_tiled, axis=-1)
    
    img_arr += bayer_tiled
    img_arr = np.clip(img_arr, 0, 255).astype(np.uint8)
    return Image.fromarray(img_arr)

def apply_custom_dither(img, palette_rgb, dither_method):
    h, w = img.height, img.width
    working = np.array(img, dtype=np.float32)
    out_indices = np.empty((h, w), dtype=np.int32)

    if dither_method == "atkinson":
        matrix = [(1, 0, 1/8), (2, 0, 1/8), (-1, 1, 1/8), (0, 1, 1/8), (1, 1, 1/8), (0, 2, 1/8)]
    elif dither_method == "burkes":
        matrix = [(1, 0, 8/32), (2, 0, 4/32), (-2, 1, 2/32), (-1, 1, 4/32), (0, 1, 8/32), (1, 1, 4/32), (2, 1, 2/32)]
    elif dither_method == "sierra":
        matrix = [(1, 0, 2/4), (-1, 1, 1/4), (0, 1, 1/4)]
    else:
        matrix = []

    for y in range(h):
        for x in range(w):
            pix = working[y, x]
            dists = np.sum((palette_rgb - pix) ** 2, axis=1)
            idx = int(np.argmin(dists))
            out_indices[y, x] = idx
            err = pix - palette_rgb[idx]

            for dx, dy, weight in matrix:
                nx, ny = x + dx, y + dy
                if 0 <= nx < w and 0 <= ny < h:
                    working[ny, nx] += err * weight

    return out_indices

def process_single_frame_img(img, pal_img, width, height, num_colors, dither_method, apply_perceptual=True, palette_rgb=None):
    if img.size != (width, height):
        img = img.resize((width, height), Image.Resampling.LANCZOS)
        
    if dither_method == 'blue_noise':
        bn = generate_blue_noise_approx_numpy(width, height)
        bn = (bn - 0.5) * 64.0
        bn_tiled = np.stack([bn]*3, axis=-1)
        if apply_perceptual:
            mask = get_edge_mask_cpu(img)
            bn_tiled *= mask
        img_arr = np.array(img, dtype=np.float32) + bn_tiled
        img = Image.fromarray(np.clip(img_arr, 0, 255).astype(np.uint8))
        q = img.quantize(palette=pal_img, dither=Image.Dither.NONE)
        return np.array(q, dtype=np.int32) % num_colors
    elif dither_method == 'ordered':
        if apply_perceptual:
            bayer_matrix = np.array([[0, 8, 2, 10], [12, 4, 14, 6], [3, 11, 1, 9], [15, 7, 13, 5]], dtype=np.float32)
            bayer_matrix = (bayer_matrix / 16.0 - 0.5) * 32.0
            bayer_tiled = np.tile(bayer_matrix, (height // 4 + 1, width // 4 + 1))[:height, :width]
            bayer_tiled = np.expand_dims(bayer_tiled, axis=-1)
            mask = get_edge_mask_cpu(img)
            img_arr = np.array(img, dtype=np.float32) + (bayer_tiled * mask)
            img = Image.fromarray(np.clip(img_arr, 0, 255).astype(np.uint8))
        else:
            img = apply_ordered_dither_bias(img, palette_rgb)
        q = img.quantize(palette=pal_img, dither=Image.Dither.NONE)
        return np.array(q, dtype=np.int32) % num_colors
    elif dither_method in ('atkinson', 'burkes', 'sierra'):
        if palette_rgb is None:
            palette_rgb = np.array(pal_img.getpalette()[:num_colors*3], dtype=np.float32).reshape(-1, 3)
        return apply_custom_dither(img, palette_rgb, dither_method) % num_colors
    elif dither_method == 'floyd':
        q = img.quantize(palette=pal_img, dither=Image.Dither.FLOYDSTEINBERG)
        return np.array(q, dtype=np.int32) % num_colors
    else:
        q = img.quantize(palette=pal_img, dither=Image.Dither.NONE)
        return np.array(q, dtype=np.int32) % num_colors

try:
    import torch
    HAS_TORCH_CUDA = torch.cuda.is_available()
except ImportError:
    HAS_TORCH_CUDA = False

def rgb_to_oklab_torch(rgb_tensor):
    # rgb_tensor: (..., 3) in range [0, 255]
    rgb = rgb_tensor / 255.0
    # sRGB -> linear sRGB
    mask = rgb > 0.04045
    linear = torch.where(mask, torch.pow((rgb + 0.055) / 1.055, 2.4), rgb / 12.92)
    
    # linear sRGB -> LMS
    m1 = torch.tensor([
        [0.4122214708, 0.5363325363, 0.0514459929],
        [0.2119034982, 0.6806995451, 0.1073969566],
        [0.0883024619, 0.2817188376, 0.6299787005]
    ], dtype=rgb_tensor.dtype, device=rgb_tensor.device)
    lms = torch.matmul(linear, m1.T)
    
    # 非線形変換 (cbrt)
    lms_ = torch.sign(lms) * torch.pow(torch.abs(lms), 1.0/3.0)
    
    # LMS -> OKLab
    m2 = torch.tensor([
        [ 0.2104542553,  0.7936177850, -0.0040720468],
        [ 1.9779984951, -2.4285922050,  0.4505937099],
        [ 0.0259040371,  0.7827717662, -0.8086757660]
    ], dtype=rgb_tensor.dtype, device=rgb_tensor.device)
    return torch.matmul(lms_, m2.T)

def generate_blue_noise_approx_numpy(width, height):
    from PIL import ImageFilter
    np.random.seed(42)
    noise = np.random.randint(0, 256, (height, width), dtype=np.uint8)
    img = Image.fromarray(noise)
    blur = img.filter(ImageFilter.GaussianBlur(radius=1.5))
    noise_f = noise.astype(np.float32)
    blur_f = np.array(blur).astype(np.float32)
    hp = noise_f - blur_f
    hp_min, hp_max = hp.min(), hp.max()
    if hp_max > hp_min:
        hp = (hp - hp_min) / (hp_max - hp_min)
    else:
        hp = np.zeros_like(hp)
    return hp

def get_edge_mask_cpu(img_pil):
    from PIL import ImageFilter
    edges = img_pil.convert("L").filter(ImageFilter.FIND_EDGES)
    edges = edges.filter(ImageFilter.GaussianBlur(radius=1.0))
    edges_f = np.array(edges).astype(np.float32) / 255.0
    max_val = edges_f.max()
    if max_val > 0:
        edges_f /= max_val
    mask = np.clip(1.0 - edges_f, 0.0, 1.0)
    return np.expand_dims(mask, axis=-1)

def get_edge_mask_gpu(img_tensor_h_w_3):
    import torch
    import torch.nn.functional as F
    img = img_tensor_h_w_3.permute(2, 0, 1).unsqueeze(0) / 255.0
    gray = 0.299 * img[:, 0:1, :, :] + 0.587 * img[:, 1:2, :, :] + 0.114 * img[:, 2:3, :, :]
    
    sobel_x = torch.tensor([[-1., 0., 1.], [-2., 0., 2.], [-1., 0., 1.]], dtype=torch.float32, device=img.device).view(1, 1, 3, 3)
    sobel_y = torch.tensor([[-1., -2., -1.], [0., 0., 0.], [1., 2., 1.]], dtype=torch.float32, device=img.device).view(1, 1, 3, 3)
    
    grad_x = F.conv2d(gray, sobel_x, padding=1)
    grad_y = F.conv2d(gray, sobel_y, padding=1)
    
    mag = torch.sqrt(grad_x**2 + grad_y**2)
    mag = torch.clamp(mag / 2.0, 0.0, 1.0)
    mask = 1.0 - mag
    return mask[0].permute(1, 2, 0)

def process_frames_gpu(frames_iter, palette_rgb, width, height, dither_method="none", apply_perceptual=True):
    import torch
    num_colors = len(palette_rgb)
    pal_tensor = torch.tensor(palette_rgb, dtype=torch.float32, device="cuda")
    processed_frames = []

    dither_tensor = None
    if dither_method == "ordered":
        bayer = np.array([
            [0, 8, 2, 10],
            [12, 4, 14, 6],
            [3, 11, 1, 9],
            [15, 7, 13, 5]
        ], dtype=np.float32)
        bayer = (bayer / 16.0) - 0.5
        bayer *= 32.0
        bayer_tiled = np.tile(bayer, (height // 4 + 1, width // 4 + 1))[:height, :width]
        bayer_tiled = np.stack([bayer_tiled]*3, axis=-1)
        dither_tensor = torch.tensor(bayer_tiled, dtype=torch.float32, device="cuda")
    elif dither_method == "blue_noise":
        bn = generate_blue_noise_approx_numpy(width, height)
        bn = (bn - 0.5) * 64.0 # Spread
        bn_tiled = np.stack([bn]*3, axis=-1)
        dither_tensor = torch.tensor(bn_tiled, dtype=torch.float32, device="cuda")

    for i, img_arr in enumerate(frames_iter):
        img_tensor = torch.tensor(img_arr, dtype=torch.float32, device="cuda")
        
        if dither_tensor is not None:
            if apply_perceptual:
                mask = get_edge_mask_gpu(img_tensor)
                bias = dither_tensor * mask
            else:
                bias = dither_tensor
            img_tensor += bias
            img_tensor = torch.clamp(img_tensor, 0, 255)

        dists = torch.cdist(img_tensor.view(-1, 3), pal_tensor)
        idx_tensor = torch.argmin(dists, dim=1).view(height, width)
        processed_frames.append(idx_tensor.cpu().numpy().astype(np.int32) % num_colors)

        # OOM回避: メモリ明示解放と64フレームごとのキャッシュクリア
        del img_tensor, dists, idx_tensor
        if i % 64 == 63:
            torch.cuda.empty_cache()

    return processed_frames

def parse_args():
    parser = argparse.ArgumentParser(
        description="PNG連番をMinecraft Bedrock用のフレーム差分データへ超高速変換します。"
    )
    parser.add_argument("--output", default=OUTPUT_FILE, help="出力するframes_data.jsのパス")
    parser.add_argument("--width", type=int, default=WIDTH, help="出力幅（ブロック数）")
    parser.add_argument("--height", type=int, default=HEIGHT, help="出力高さ（ブロック数）")
    parser.add_argument("--input-video", default=None, help="入力動画ファイル（FFmpeg直接読み込み）")
    parser.add_argument("--fps", type=float, default=20.0, help="変換フレームレート")
    parser.add_argument("--duration", type=float, default=None, help="処理時間（秒）")
    parser.add_argument("--palette", choices=PALETTES.keys(), default="concrete", help="使用するブロック色パレット")
    parser.add_argument("--dither", action="store_true", help="ディザリングを有効化")
    parser.add_argument("--dither-method", default="none", choices=["none", "floyd", "ordered", "blue_noise", "atkinson", "burkes", "sierra"], help="ディザリング手法")
    parser.add_argument("--perceptual", action="store_true", default=True, help="知覚最適化(エッジ減衰)を有効にする (デフォルトTrue)")
    parser.add_argument("--no-perceptual", dest="perceptual", action="store_false", help="知覚最適化を無効にする")
    parser.add_argument("--video-id", default=None, help="動画ID（マルチ動画パック用）")
    parser.add_argument("--threads", type=int, default=os.cpu_count() or 4, help="使用スレッド数")
    parser.add_argument("--keyframe-interval", type=int, default=30, help="キーフレーム(Iフレーム)間隔（フレーム数、0で無効）")
    parser.add_argument("--gpu", action="store_true", help="PyTorch/CUDA による GPU 加速量子化を使用")
    parser.add_argument("--adaptive-fps", action="store_true", default=True, help="適応的フレームレート(静止シーンスキップ)を有効化")
    parser.add_argument("--scene-threshold", type=float, default=0.015, help="静止シーン判定しきい値(比率)")
    return parser.parse_args()

def encode_varint(val, byte_arr):
    while val >= 0x80:
        byte_arr.append((val & 0x7f) | 0x80)
        val >>= 7
    byte_arr.append(val & 0x7f)

def bytearray_to_utf16_str(byte_arr):
    # バイト配列を 15ビットごとに区切り、0x1000 を足して UTF-16 文字列にする
    if not byte_arr:
        return ""
    bits = "".join(f"{b:08b}" for b in byte_arr)
    
    rem = len(bits) % 15
    pad_len = (15 - rem) if rem != 0 else 0
    if pad_len > 0:
        bits += "0" * pad_len
        
    chars = [chr(0x1000 + pad_len)]
    for i in range(0, len(bits), 15):
        val_15 = int(bits[i:i+15], 2)
        chars.append(chr(0x1000 + val_15))
        
    return "".join(chars)

def extract_rle_chunks(changed_indices, changed_colors, width):
    chunks = []
    if len(changed_indices) == 0:
        return chunks
    
    start_idx = int(changed_indices[0])
    current_color = int(changed_colors[0])
    current_y = start_idx // width
    length = 1
    
    for i in range(1, len(changed_indices)):
        idx = int(changed_indices[i])
        color = int(changed_colors[i])
        y = idx // width
        
        # 連続条件:
        # 1. 完全に隣接している (idx == start_idx + length)
        # 2. 同じ行に属している (y == current_y)
        # 3. 色が同じ
        # 4. 長さが 64 未満 (6bit制約のため、length-1が0~63に収まる)
        if idx == start_idx + length and y == current_y and color == current_color and length < 64:
            length += 1
        else:
            chunks.append((start_idx, length, current_color))
            start_idx = idx
            current_color = color
            current_y = y
            length = 1
            
    chunks.append((start_idx, length, current_color))
    return chunks

def stream_frames_from_video(video_path, width, height, fps, duration=None):
    import subprocess
    # hwaccel を外す。ハードウェアデコーダを経由すると、rawvideo 出力時に
    # NV12 や YUV フォーマットが強制され、色が破損（白が緑になる等）する現象を防ぐため。
    cmd = ["ffmpeg", "-y", "-i", video_path]
    if duration is not None:
        cmd.extend(["-t", str(duration)])
    cmd.extend([
        "-vf", f"fps={fps:g},scale={width}:{height}:flags=lanczos,format=rgb24",
        "-f", "rawvideo", "-pix_fmt", "rgb24", "-"
    ])
    
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    frame_size = width * height * 3
    
    while True:
        raw = proc.stdout.read(frame_size)
        if len(raw) != frame_size:
            break
        yield np.frombuffer(raw, dtype=np.uint8).reshape((height, width, 3))

def main():
    global WIDTH, HEIGHT
    args = parse_args()
    if args.width <= 0 or args.height <= 0:
        raise ValueError("幅と高さは1以上にしてください")

    WIDTH = args.width
    HEIGHT = args.height
    output_file = args.output
    
    if args.video_id:
        output_dir = os.path.dirname(os.path.abspath(output_file))
        output_file = os.path.join(output_dir, f"frames_{args.video_id}.js")

    palette = PALETTES[args.palette]
    num_colors = len(palette)
    level_blocks = [{"block": item["block"], "states": {}} for item in palette]
    os.makedirs(os.path.dirname(os.path.abspath(output_file)), exist_ok=True)

    if args.input_video:
        print(f"Video: {args.input_video} (FPS: {args.fps})")
        frames_iter = stream_frames_from_video(args.input_video, WIDTH, HEIGHT, args.fps, args.duration)
        # We need the total count roughly to know if empty, but we can't get it easily.
        # We'll just pass the generator to the processing functions.
    else:
        # Fallback for PNG sequences if still used
        frames_dir = getattr(args, 'frames_dir', FRAMES_DIR)
        files = sorted(glob.glob(os.path.join(frames_dir, "*.png")))
        print("Frames:", len(files))
        if not files:
            print(f"警告: {frames_dir}/ にPNGが見つかりません。")
            return
        
        def image_generator(file_list, w, h):
            for f in file_list:
                img = Image.open(f).convert("RGB")
                if img.size != (w, h):
                    img = img.resize((w, h), Image.Resampling.LANCZOS)
                yield np.array(img, dtype=np.uint8)
        
        frames_iter = image_generator(files, WIDTH, HEIGHT)

    pal_img = create_pillow_palette_image(palette)

    dither_method = args.dither_method
    if args.dither and dither_method == 'none':
        dither_method = 'floyd'

    use_gpu = args.gpu or HAS_TORCH_CUDA
    palette_rgb_arr = np.array([item['rgb'] for item in palette], dtype=np.float64)

    if use_gpu and HAS_TORCH_CUDA and dither_method in ('none', 'ordered', 'blue_noise'):
        print(f"GPU (PyTorch / CUDA) 加速量子化を使用して超高速変換中... (Dither: {dither_method}, Perceptual: {args.perceptual})")
        processed_frames = process_frames_gpu(frames_iter, palette_rgb_arr, WIDTH, HEIGHT, dither_method, apply_perceptual=args.perceptual)
    else:
        if use_gpu:
            if not HAS_TORCH_CUDA:
                print("注意: GPU (PyTorch/CUDA) が利用できません。CPUスレッドプール処理にフォールバックします。")
            elif dither_method not in ('none', 'ordered', 'blue_noise'):
                print(f"注意: 誤差拡散型ディザリング ({dither_method}) が選択されたため、並列計算できず CPU 処理にフォールバックします。")
                print("      → GPUで超高速変換を行いたい場合は、ディザリング手法を 'ordered', 'blue_noise' または 'none' に変更してください。")
        
        palette_rgb_for_dither = palette_rgb_arr if dither_method in ('ordered', 'atkinson', 'burkes', 'sierra') else None
        
        frames_list = list(frames_iter)
        
        def task(img_arr):
            img = Image.fromarray(img_arr)
            return process_single_frame_img(img, pal_img, WIDTH, HEIGHT, num_colors, dither_method, args.perceptual, palette_rgb_for_dither)

        max_workers = min(args.threads, len(frames_list) if frames_list else 1)
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            processed_frames = list(executor.map(task, frames_list))

    old = np.full((HEIGHT, WIDTH), -1, dtype=int)
    frame_diffs = []
    skipped_frames = 0
    keyframe_count = 0

    total_pixels = WIDTH * HEIGHT
    k_interval = args.keyframe_interval

    for i, current in enumerate(processed_frames):
        is_keyframe = (k_interval > 0 and i % k_interval == 0)

        if is_keyframe:
            keyframe_count += 1
            changed_indices = np.arange(total_pixels)
            changed_colors = current.flat[changed_indices]
            prefix = "K:"
        else:
            diff_mask = (current != old)
            changed_indices = np.flatnonzero(diff_mask)
            prefix = ""

            # Adaptive FPS: 変化率が指定閾値未満で、最初のフレームでない場合はスキップ
            change_ratio = len(changed_indices) / total_pixels
            if args.adaptive_fps and i > 0 and change_ratio < args.scene_threshold:
                frame_diffs.append("")
                skipped_frames += 1
                continue

            changed_colors = current.flat[changed_indices]

        if len(changed_indices) > 0 or is_keyframe:
            byte_arr = bytearray()
            chunks = extract_rle_chunks(changed_indices, changed_colors, WIDTH)
            
            prev_idx = 0
            for start_idx, length, color in chunks:
                delta = start_idx - prev_idx
                # 新 VarInt構造: [delta(可変長)] + [length-1(6bit)] + [color(7bit)]
                val = (delta << 13) | ((length - 1) << 7) | (color & 0x7f)
                encode_varint(val, byte_arr)
                prev_idx = start_idx
                
            packed_b64 = prefix + bytearray_to_utf16_str(byte_arr)
            old = current.copy()
        else:
            packed_b64 = prefix
            
        frame_diffs.append(packed_b64)

    # len(files) への依存を排除する
    total_frames = len(processed_frames)
    print(f"Keyframes (Iフレーム): {keyframe_count} 個, スキップされたフレーム数: {skipped_frames} / {total_frames}")

    data = {
        "width": WIDTH,
        "height": HEIGHT,
        "keyframe_interval": k_interval,
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
