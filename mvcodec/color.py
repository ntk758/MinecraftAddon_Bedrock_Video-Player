import functools
import numpy as np
from PIL import Image, ImageFilter

from mvcodec.block_colors import BLOCK_COLORS

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



# 上の色は古い版で使っていた目安の値 (Java 版の地図の色に近く、実際の見た目と違う: 白いコンクリートが (255, 255, 255) など)。
# 実際のテクスチャの平均色 (mvcodec/block_colors.py、Mojang の bedrock-samples から生成) で置き換える
MEASURED_RGB = {item["block"]: tuple(item["rgb"]) for item in BLOCK_COLORS}
for _item in CONCRETE_PALETTE + TERRACOTTA_PALETTE + FROGLIGHT_PALETTE + WOOL_PALETTE:
    _item["rgb"] = MEASURED_RGB.get(_item["block"], _item["rgb"])

ALL_BLOCKS = CONCRETE_PALETTE + TERRACOTTA_PALETTE + FROGLIGHT_PALETTE

def _palette_item(item):
    return {"name": item["block"].split(":", 1)[1], "block": item["block"], "rgb": tuple(item["rgb"]),
            "side": tuple(item.get("side", item["rgb"])), "group": item["group"]}


# 1 つのパレットに入れられる色の数 (パレット番号は 7bit、立体は 1 つを空気に使う)
MAX_PALETTE_BLOCKS = 127


def _most_distinct(items, count):
    """items から、上面と側面の色 (OkLab) が互いに一番離れるように count 個を選ぶ (先頭から優先。順番は items のまま)。"""
    if len(items) <= count:
        return list(items)
    from mvcodec.block_rdo import rgb_to_oklab

    lab = np.concatenate([rgb_to_oklab(np.array([item["rgb"] for item in items], dtype=np.uint8)),
                          rgb_to_oklab(np.array([item["side"] for item in items], dtype=np.uint8))], axis=1)
    chosen = [0]
    distance = np.linalg.norm(lab - lab[0], axis=1)
    while len(chosen) < count:
        index = int(distance.argmax())
        chosen.append(index)
        distance = np.minimum(distance, np.linalg.norm(lab - lab[index], axis=1))
    return [items[i] for i in sorted(chosen)]


def side_palette(palette):
    """立体用: 各ブロックの色を側面の色にしたパレット (立体は横から見るので、原木などは樹皮の色で選ぶ)。"""
    return [dict(item, rgb=tuple(item.get("side", item["rgb"]))) for item in palette]


# 重力で落ちず・時間で変化せず・まわりに作用しない不透明な立方体のブロックすべて (上面も側面も色がほぼ同じものは 1 つに絞り済み)。
# 雪のブロックは光るブロックの明かりで溶けるので、光るブロックと一緒に使うパレット (全ブロック) には入れない
OPAQUE_BLOCKS = [_palette_item(item) for item in BLOCK_COLORS if item["group"] != "glass"]
EVERY_BLOCK_PALETTE = [item for item in OPAQUE_BLOCKS if item["group"] != "snow"]
# 半透明のステンドグラス (3D ホログラム向け。奥のブロックや景色が透けるので、平面スクリーンでは色が背景で変わる)。
# 色はテクスチャの不透明な部分の色 (透けて見える色は後ろの物で変わる)
GLASS_PALETTE = [_palette_item(item) for item in BLOCK_COLORS if item["group"] == "glass"]

PALETTES = {
    "concrete": CONCRETE_PALETTE,
    "expanded": CONCRETE_PALETTE + TERRACOTTA_PALETTE,
    "full": ALL_BLOCKS,
    # 全ブロックは 127 色を超えるので、色が互いに一番離れる 127 色を選ぶ
    "all": _most_distinct(EVERY_BLOCK_PALETTE, MAX_PALETTE_BLOCKS),
    "glass": GLASS_PALETTE,
}

# 自動パレットが色を選ぶ候補 (全ブロックから光るものを除いたもの)。光るブロックは入れない (置き替えるたびに明るさの計算が起き、
# 夜はスクリーンの明るさがまだらになる。負荷はまだ実機で測っていない)。「全ブロック」パレットと 39 色以下のパレットには入っている
AUTO_PALETTE_CANDIDATES = [item for item in OPAQUE_BLOCKS if item["group"] != "light"]

# ブロックID -> RGB (デコーダ・評価用)
BLOCK_RGB = {item["block"]: tuple(item["rgb"]) for item in ALL_BLOCKS + WOOL_PALETTE + OPAQUE_BLOCKS + GLASS_PALETTE}
# ブロックID -> 側面の RGB (立体のプレビュー用。原木などは上面と違う)
BLOCK_SIDE_RGB = {**BLOCK_RGB, **{item["block"]: tuple(item.get("side", item["rgb"])) for item in BLOCK_COLORS}}

def create_palette_image(palette):
    pal_img = Image.new("P", (1, 1))
    pal_data = []
    for item in palette:
        pal_data.extend(item['rgb'])
    if len(pal_data) < 768:
        pal_data.extend(pal_data[:min(768 - len(pal_data), len(palette) * 3)])
    pal_img.putpalette(pal_data)
    return pal_img

@functools.lru_cache(maxsize=8)
def _blue_noise_cached(width, height):
    # 全体共有の乱数 (np.random.seed) は複数スレッドから同時に使うと結果が毎回変わるため、専用の乱数を使う
    rng = np.random.default_rng(42)
    noise = rng.integers(0, 256, (height, width), dtype=np.uint8)
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
    hp.setflags(write=False)
    return hp


def generate_blue_noise_approx_numpy(width, height):
    """ディザ用の高周波ノイズ (0〜1)。同じ大きさなら常に同じ模様を返す (スレッドから同時に呼んでも安全)。"""
    return _blue_noise_cached(width, height).copy()

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

def process_single_frame_img(img, pal_img, width, height, num_colors, dither_method, apply_perceptual=True, palette_rgb=None,
                             dither_offset=(0, 0)):
    """1 フレームをパレット番号 (H, W) にする。dither_offset は Blue Noise / Ordered の模様をずらす量
    (mvcodec/dither_motion.py: 映像の全体の動きに合わせて模様を動かし、パンでのちらつきとデータ量を抑える)。"""
    from mvcodec.dither_motion import dither_pattern

    if img.size != (width, height):
        img = img.resize((width, height), Image.Resampling.LANCZOS)
        
    if dither_method == 'blue_noise' and palette_rgb is not None:
        noise = dither_pattern("blue_noise", width, height, dither_offset) + 0.5
        mask = get_edge_mask_cpu(img)[..., 0] if apply_perceptual else None
        return knoll_dither_indices(np.asarray(img), palette_rgb, noise, mask) % num_colors
    if dither_method == 'blue_noise':
        bn = dither_pattern("blue_noise", width, height, dither_offset) * 64.0
        bn_tiled = np.stack([bn]*3, axis=-1)
        if apply_perceptual:
            mask = get_edge_mask_cpu(img)
            bn_tiled *= mask
        img_arr = np.array(img, dtype=np.float32) + bn_tiled
        img = Image.fromarray(np.clip(img_arr, 0, 255).astype(np.uint8))
        if palette_rgb is not None:
            return nearest_oklab_indices(np.asarray(img), palette_rgb)
        q = img.quantize(palette=pal_img, dither=Image.Dither.NONE)
        return np.array(q, dtype=np.int32) % num_colors
    elif dither_method == 'ordered':
        bayer_tiled = np.expand_dims(dither_pattern("ordered", width, height, dither_offset) * 32.0, axis=-1)
        if apply_perceptual:
            bayer_tiled = bayer_tiled * get_edge_mask_cpu(img)
        img_arr = np.array(img, dtype=np.float32) + bayer_tiled
        img = Image.fromarray(np.clip(img_arr, 0, 255).astype(np.uint8))
        if palette_rgb is not None:
            return nearest_oklab_indices(np.asarray(img), palette_rgb)
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
        if palette_rgb is not None:
            return nearest_oklab_indices(np.asarray(img), palette_rgb)
        q = img.quantize(palette=pal_img, dither=Image.Dither.NONE)
        return np.array(q, dtype=np.int32) % num_colors


# 減色 (CPU / GPU) で、OkLab の色合い (a, b) の差に掛ける重み。1 だと暗い緑の木の葉・芝生が、明るさの近い茶色や
# 青緑のブロックになっていた。実写 16 本でぼかした色差 −7%、評価用 8 本で −2%、データ −8〜12% (GPU)。
# CPU は以前 Pillow の RGB の距離で選んでいて、同じ OkLab にしたことで実写のぼかした色差が −11%
# (docs/research/phase7x.md の 18 章)
CHROMA_WEIGHT = 2.0
# パターンディザ (knoll_dither_indices) の候補の数と、誤差を足す割合。1.0 より 0.5 の方が、実写 16 本・ジャンル 24 本で
# MS-SSIM が高く (粒が細かい)、ぼかした色差は同じ (docs/research/phase7x.md の 20 章)
KNOLL_STEPS = 8
KNOLL_GAIN = 0.5


def nearest_oklab_indices(rgb, palette_rgb):
    """各画素に一番近いパレットの色の番号 (OkLab、色合いの差を CHROMA_WEIGHT 倍に重く見る)。"""
    from mvcodec.block_rdo import rgb_to_oklab

    scale = np.array([1.0, CHROMA_WEIGHT, CHROMA_WEIGHT], np.float32)
    lab = rgb_to_oklab(np.asarray(rgb, dtype=np.uint8)).reshape(-1, 3) * scale
    pal = rgb_to_oklab(np.clip(np.asarray(palette_rgb), 0, 255).astype(np.uint8)) * scale
    # |x - p|^2 = |x|^2 - 2 x.p + |p|^2 (|x|^2 は比較に要らない)
    d = (pal ** 2).sum(1)[None] - 2.0 * lab @ pal.T
    return d.argmin(1).astype(np.int32).reshape(np.asarray(rgb).shape[:2])

def _srgb_to_linear_np(rgb):
    c = np.asarray(rgb, dtype=np.float32) / 255.0
    return np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)


def _linear_to_oklab_np(lin):
    from mvcodec.block_rdo import _M1, _M2

    return np.cbrt(np.maximum(lin, 0) @ _M1.T.astype(np.float32)) @ (_M2.T * 100.0).astype(np.float32)


def knoll_dither_indices(rgb, palette_rgb, noise01, edge_mask=None, steps=KNOLL_STEPS, error_gain=KNOLL_GAIN):
    """パターンディザ (Knoll): 各画素で、誤差を足しながら一番近い色を steps 回選んで候補を作り、明るさの順に並べて、
    ノイズ (0〜1) で 1 つを選ぶ。候補の平均 (線形の明るさ) が元の色に近づくので、明るさだけでなく色合いも、2 色に限らず
    混ざる (肌の桃色が白と暖色のブロックの混ざりになる)。元の色にちょうど合うブロックがある所では粒が出ず、暗い所で
    明るさの揺らぎが大きすぎることもない (以前の Blue Noise は明るさに一定の揺らぎを足していた)。
    edge_mask (輪郭で小さい) が 0.5 未満の画素は 1 つ目の候補 (一番近い色) にする。"""
    from mvcodec.block_rdo import rgb_to_oklab

    shape = np.asarray(rgb).shape[:2]
    scale = np.array([1.0, CHROMA_WEIGHT, CHROMA_WEIGHT], np.float32)
    src = np.asarray(rgb, dtype=np.uint8).reshape(-1, 3)
    pal_rgb = np.clip(np.asarray(palette_rgb, dtype=np.float32), 0, 255)
    pal_lab = rgb_to_oklab(pal_rgb.astype(np.uint8)) * scale
    pal_lin = _srgb_to_linear_np(pal_rgb)
    pal_sq = (pal_lab ** 2).sum(1)
    goal = _srgb_to_linear_np(src)
    err = np.zeros_like(goal)
    cands = np.empty((steps, len(src)), np.int32)
    for i in range(steps):
        attempt = np.clip(goal + err * error_gain, 0, 1)
        lab = _linear_to_oklab_np(attempt) * scale
        c = (pal_sq[None] - 2.0 * lab @ pal_lab.T).argmin(1)
        cands[i] = c
        err += goal - pal_lin[c]
    first = cands[0].copy()
    order = np.argsort(pal_lab[:, 0][cands], axis=0, kind="stable")
    cands = np.take_along_axis(cands, order, 0)
    n = np.asarray(noise01, dtype=np.float32).reshape(-1)
    pick = np.clip((n * steps).astype(np.int32), 0, steps - 1)
    out = cands[pick, np.arange(len(src))]
    if edge_mask is not None:
        out = np.where(np.asarray(edge_mask, dtype=np.float32).reshape(-1) < 0.5, first, out)
    return out.astype(np.int32).reshape(shape)


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


def linear_to_oklab_torch(linear):
    """線形 RGB (0〜1) の tensor を OkLab にする (rgb_to_oklab_torch の後半と同じ)。"""
    import torch

    m1 = torch.tensor([[0.4122214708, 0.5363325363, 0.0514459929], [0.2119034982, 0.6806995451, 0.1073969566],
                       [0.0883024619, 0.2817188376, 0.6299787005]], dtype=linear.dtype, device=linear.device)
    m2 = torch.tensor([[0.2104542553, 0.7936177850, -0.0040720468], [1.9779984951, -2.4285922050, 0.4505937099],
                       [0.0259040371, 0.7827717662, -0.8086757660]], dtype=linear.dtype, device=linear.device)
    lms = torch.matmul(linear, m1.T)
    return torch.matmul(torch.sign(lms) * torch.pow(torch.abs(lms), 1.0 / 3.0), m2.T)


def knoll_dither_torch(img_rgb, pal_rgb, noise01, edge_mask=None, chroma_weight=CHROMA_WEIGHT, steps=KNOLL_STEPS,
                       error_gain=KNOLL_GAIN):
    """knoll_dither_indices の GPU (PyTorch) 版。img_rgb は (H, W, 3) の 0〜255、pal_rgb は (K, 3)、noise01 と edge_mask は
    (H, W)。戻り値は (H, W) のパレット番号 (long)。"""
    import torch

    h, w = img_rgb.shape[:2]
    scale = torch.tensor([1.0, chroma_weight, chroma_weight], dtype=torch.float32, device=img_rgb.device)

    def to_linear(rgb):
        c = rgb / 255.0
        return torch.where(c > 0.04045, torch.pow((c + 0.055) / 1.055, 2.4), c / 12.92)

    goal = to_linear(img_rgb.reshape(-1, 3))
    pal_lin = to_linear(pal_rgb)
    pal_lab = linear_to_oklab_torch(pal_lin) * scale
    # 1 回ごとの演算の数を減らす (DirectML は小さな演算ごとの手間が大きい): 線形 RGB は 0 以上なので LMS も 0 以上で、
    # 立方根は pow だけでよい。OkLab への 2 つ目の行列に色合いの重みを掛けておき、距離は |p|^2 - 2 x.p で比べる
    m1t = torch.tensor([[0.4122214708, 0.5363325363, 0.0514459929], [0.2119034982, 0.6806995451, 0.1073969566],
                        [0.0883024619, 0.2817188376, 0.6299787005]], dtype=torch.float32, device=img_rgb.device).T
    m2s = torch.tensor([[0.2104542553, 0.7936177850, -0.0040720468], [1.9779984951, -2.4285922050, 0.4505937099],
                        [0.0259040371, 0.7827717662, -0.8086757660]], dtype=torch.float32, device=img_rgb.device).T * scale
    pal_t2 = (pal_lab * -2.0).T.contiguous()
    pal_sq = (pal_lab ** 2).sum(1)
    err = torch.zeros_like(goal)
    cands = []
    for _ in range(steps):
        attempt = torch.clamp(goal + err * error_gain, 0, 1)
        lab = torch.pow(attempt @ m1t, 1.0 / 3.0) @ m2s
        c = torch.argmin(torch.addmm(pal_sq, lab, pal_t2), dim=1)
        cands.append(c)
        err = err + (goal - pal_lin[c])
    cands = torch.stack(cands)
    first = cands[0]
    # 候補を明るさの順に並べて pick 番目を取る。並べ替え (argsort) は DirectML で遅いので、各候補の順位を比べて数える
    lum = pal_lab[:, 0][cands]
    idx = torch.arange(steps, device=img_rgb.device).view(steps, 1, 1)
    before = (lum.unsqueeze(1) < lum.unsqueeze(0)) | ((lum.unsqueeze(1) == lum.unsqueeze(0)) & (idx.transpose(0, 1) < idx))
    rank = before.sum(0)  # rank[i]: 候補 i より前に並ぶ候補の数
    pick = torch.clamp((noise01.reshape(1, -1) * steps).long(), 0, steps - 1)
    slot = torch.argmax((rank == pick).int(), dim=0)
    out = torch.gather(cands, 0, slot.unsqueeze(0))[0]
    if edge_mask is not None:
        out = torch.where(edge_mask.reshape(-1) < 0.5, first, out)
    return out.view(h, w)

