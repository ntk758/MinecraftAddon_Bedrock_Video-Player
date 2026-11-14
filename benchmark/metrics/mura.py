"""色ムラの物差し: 元の映像で色が一定の面に、ブロックの画面がどれだけまだらに見えるか (--flat-areas の評価。docs/research/flat_areas.md)。

ブロックの画面を近くで見たときのまだらさは、2 つの和になる:
  1. 隣どうしのブロックの平均の色の違い (ディザの粒): ブロックの色から、まわり 5 × 5 ブロックの平均を引いた残り
  2. 1 個のブロックの中の模様: テクスチャのばらつき tex (mvcodec/block_colors.py。各画素の色の、平均の色からの距離の二乗平均の平方根)
ムラは、元の映像で色が一定の面の画素について、1² + 2² の平均の平方根 (OkLab × 100)。テクスチャの絵を並べなくても計算できるよう、
2 は表の tex を使う (ブロックのテクスチャを並べた画像から 5 ブロック幅のぼかしを引いた残りの二乗平均の平方根と、ほぼ同じ値になる)。
1 色の面にコンクリート 1 種類を並べると 0.3、模様の強いブロック (作業台、16) や明るさの違う 2 色の粒が混ざると大きくなる。

遠目の色差 (blurred_color_error) は、ブロックの色と元の色をそれぞれ 4 × 4 ブロックの箱でぼかして比べた平均の距離 (OkLab × 100)。
ディザで混ぜた色は遠目には平均の色に見えるので、混ぜ方の良し悪しはこちらで見る。

このモジュールは numpy だけを使う (mvcodec の OkLab の変換を使う)。
"""
import numpy as np

from mvcodec.block_rdo import rgb_to_oklab

# 元の映像で「色が一定の面」とみなす: まわり FLAT_WINDOW × FLAT_WINDOW の色の標準偏差が FLAT_STD 未満の画素を、
# さらに FLAT_ERODE × FLAT_ERODE で削った (面の内側だけ残す) もの
FLAT_WINDOW = 5
FLAT_STD = 0.1
FLAT_ERODE = 5
GRAIN_WINDOW = 5
BLUR_WINDOW = 4


def lab100(rgb):
    """(H, W, 3) の sRGB (0〜255) を OkLab × 100 にする。"""
    rgb = np.asarray(rgb)
    # rgb_to_oklab は OkLab × 100 を返す
    return rgb_to_oklab(np.clip(rgb, 0, 255).astype(np.uint8).reshape(-1, 3)).reshape(*rgb.shape[:2], 3).astype(np.float64)


def box_mean(img, window):
    """(H, W, C) の window × window の平均 (端は端の画素を繰り返す。window が偶数なら右下に 1 つ寄る)。"""
    before = (window - 1) // 2
    padded = np.pad(img, ((before, window - 1 - before), (before, window - 1 - before), (0, 0)), mode="edge")
    integral = np.zeros((padded.shape[0] + 1, padded.shape[1] + 1, padded.shape[2]), np.float64)
    integral[1:, 1:] = padded.cumsum(0).cumsum(1)
    h, w = img.shape[:2]
    total = (integral[window:window + h, window:window + w] - integral[:h, window:window + w]
             - integral[window:window + h, :w] + integral[:h, :w])
    return total / (window * window)


def flat_region(source_rgb, window=FLAT_WINDOW, threshold=FLAT_STD, erode=FLAT_ERODE):
    """元の映像 (H, W, 3) で色が一定の面の内側の画素 (True)。"""
    lab = lab100(source_rgb)
    variance = (box_mean(lab * lab, window) - box_mean(lab, window) ** 2).sum(-1)
    flat = np.sqrt(np.maximum(variance, 0)) < threshold
    if erode > 1:
        pad = erode // 2
        padded = np.pad(flat, pad, mode="edge")
        flat = np.lib.stride_tricks.sliding_window_view(padded, (erode, erode)).all(axis=(-1, -2))
    return flat


def mura(source_rgb, shown_rgb, shown_tex, flat=None):
    """色ムラ (OkLab × 100)。shown_rgb は各ブロックの平均の色 (H, W, 3)、shown_tex は各ブロックのテクスチャのばらつき (H, W)。
    flat (元の映像の色が一定の面) が空なら nan。戻り値は {"mura", "mura_grain", "mura_texture", "flat_share"}:
    mura_grain は 1 だけ、mura_texture は 2 だけの値。"""
    flat = flat_region(source_rgb) if flat is None else np.asarray(flat, bool)
    if not flat.any():
        return {"mura": float("nan"), "mura_grain": float("nan"), "mura_texture": float("nan"), "flat_share": 0.0}
    lab = lab100(shown_rgb)
    grain = ((lab - box_mean(lab, GRAIN_WINDOW)) ** 2).sum(-1)[flat]
    texture = np.asarray(shown_tex, np.float64)[flat] ** 2
    return {"mura": float(np.sqrt((grain + texture).mean())), "mura_grain": float(np.sqrt(grain.mean())),
            "mura_texture": float(np.sqrt(texture.mean())), "flat_share": float(flat.mean())}


def blurred_color_error(source_rgb, shown_rgb, window=BLUR_WINDOW, mask=None):
    """遠目の色差: 両方を window × window ブロックの箱でぼかして比べた距離の平均 (OkLab × 100)。mask があればその画素だけ。"""
    diff = np.sqrt(((box_mean(lab100(source_rgb), window) - box_mean(lab100(shown_rgb), window)) ** 2).sum(-1))
    return float(diff[np.asarray(mask, bool)].mean() if mask is not None else diff.mean())
