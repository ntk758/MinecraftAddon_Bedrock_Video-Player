import numpy as np
from PIL import Image, ImageFilter

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



ALL_BLOCKS = CONCRETE_PALETTE + TERRACOTTA_PALETTE + FROGLIGHT_PALETTE

PALETTES = {
    "concrete": CONCRETE_PALETTE,
    "expanded": CONCRETE_PALETTE + TERRACOTTA_PALETTE,
    "full": CONCRETE_PALETTE + TERRACOTTA_PALETTE + FROGLIGHT_PALETTE,
    "all_55": ALL_BLOCKS,
    "ultra_110": ALL_BLOCKS,
}

def create_palette_image(palette):
    pal_img = Image.new("P", (1, 1))
    pal_data = []
    for item in palette:
        pal_data.extend(item['rgb'])
    if len(pal_data) < 768:
        pal_data.extend(pal_data[:min(768 - len(pal_data), len(palette) * 3)])
    pal_img.putpalette(pal_data)
    return pal_img

def generate_blue_noise_approx_numpy(width, height):
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

def rgb_to_oklab_torch(rgb_tensor):
    import torch
    rgb = rgb_tensor / 255.0
    mask = rgb > 0.04045
    linear = torch.where(mask, torch.pow((rgb + 0.055) / 1.055, 2.4), rgb / 12.92)
    
    m1 = torch.tensor([
        [0.4122214708, 0.5363325363, 0.0514459929],
        [0.2119034982, 0.6806995451, 0.1073969566],
        [0.0883024619, 0.2817188376, 0.6299787005]
    ], dtype=rgb_tensor.dtype, device=rgb_tensor.device)
    lms = torch.matmul(linear, m1.T)
    
    lms_ = torch.sign(lms) * torch.pow(torch.abs(lms), 1.0/3.0)
    
    m2 = torch.tensor([
        [ 0.2104542553,  0.7936177850, -0.0040720468],
        [ 1.9779984951, -2.4285922050,  0.4505937099],
        [ 0.0259040371,  0.7827717662, -0.8086757660]
    ], dtype=rgb_tensor.dtype, device=rgb_tensor.device)
    return torch.matmul(lms_, m2.T)
