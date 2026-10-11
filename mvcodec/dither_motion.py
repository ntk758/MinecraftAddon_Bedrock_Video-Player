"""ディザ模様を映像の全体の動き (パン・スクロール) に合わせてずらす (Phase 8.2)。

Blue Noise や Ordered のディザ模様は画面に固定されているので、カメラがパンすると、映像の中身は動いても模様は動かず、
ほぼ全画素のパレット番号が毎フレーム変わる。データが大きくなり (動き補償も効かない)、模様がちらついて見える。

ここでは、元の映像の連続するフレームから全体の動き (整数画素のずれ) を推定し、その累積だけ模様をずらす。
パンでは「前のフレームをずらしたもの」がそのまま次のフレームになるので、動き補償で予測できるようになる。
模様は上下左右がつながる (周期的な) ものを使うので、どれだけずらしても継ぎ目は出ない。

ずれの向きは mvcodec/motion.py と同じ: 今のフレーム[y][x] ≈ 前のフレーム[y + dy][x + dx]。
模様は pattern[(y + oy) % H][(x + ox) % W] で引き、ずれ (dx, dy) のたびに (ox, oy) に足す。
"""
import functools

import numpy as np

# 全体の動きを探す縮小画像の大きさの目安 (長辺) と、その上での探索範囲
SEARCH_SIZE = 96
SEARCH_RANGE = 8
# ずらした方が、ずらさないより平均の差がこの割合以下に小さくなるときだけ「動いた」とみなす (静止画・ノイズでぶれない)
ACCEPT_RATIO = 0.8


def _luma(rgb):
    rgb = np.asarray(rgb, dtype=np.float32)
    return rgb[..., 0] * 0.299 + rgb[..., 1] * 0.587 + rgb[..., 2] * 0.114


def _shift_cost(prev, cur, dx, dy, margin):
    """cur[y][x] と prev[y + dy][x + dx] の平均の差 (端から margin 画素は除く)。"""
    h, w = cur.shape
    c = cur[margin:h - margin, margin:w - margin]
    p = prev[margin + dy:h - margin + dy, margin + dx:w - margin + dx]
    return float(np.abs(c - p).mean())


def estimate_global_shift(prev_rgb, cur_rgb):
    """2 枚のフレームの全体のずれ (dx, dy) を整数画素で返す。はっきりした動きが無ければ (0, 0)。"""
    prev, cur = _luma(prev_rgb), _luma(cur_rgb)
    h, w = cur.shape
    factor = max(1, round(max(w, h) / SEARCH_SIZE))
    # 1. 縮小画像で粗く探す
    small_prev, small_cur = prev[::factor, ::factor], cur[::factor, ::factor]
    sh, sw = small_cur.shape
    r = min(SEARCH_RANGE, max(1, min(sh, sw) // 4))
    coarse, best = (0, 0), None
    for dy in range(-r, r + 1):
        for dx in range(-r, r + 1):
            cost = _shift_cost(small_prev, small_cur, dx, dy, r)
            if best is None or cost < best:
                coarse, best = (dx, dy), cost
    # 2. 元の大きさで、粗いずれの周り ±factor を細かく探す
    margin = min((r + 1) * factor, min(h, w) // 3)
    if margin < 1:
        return 0, 0
    zero = _shift_cost(prev, cur, 0, 0, margin)
    found, best = (0, 0), zero
    for dy in range(coarse[1] * factor - factor, coarse[1] * factor + factor + 1):
        for dx in range(coarse[0] * factor - factor, coarse[0] * factor + factor + 1):
            if max(abs(dx), abs(dy)) > margin:
                continue
            cost = _shift_cost(prev, cur, dx, dy, margin)
            if cost < best:
                found, best = (dx, dy), cost
    if found != (0, 0) and best > zero * ACCEPT_RATIO:
        return 0, 0
    return found


class DitherAligner:
    """フレームを順に渡すと、そのフレームでディザ模様をずらす量 (ox, oy) を返す。"""

    def __init__(self, enabled=True):
        self.enabled = enabled
        self.prev = None
        self.offset = (0, 0)

    def update(self, rgb):
        if not self.enabled:
            return 0, 0
        rgb = np.asarray(rgb)
        if self.prev is not None and self.prev.shape == rgb.shape:
            dx, dy = estimate_global_shift(self.prev, rgb)
            self.offset = (self.offset[0] + dx, self.offset[1] + dy)
        self.prev = rgb
        return self.offset


@functools.lru_cache(maxsize=8)
def _periodic_blue_noise(width, height):
    """上下左右がつながる高周波ノイズ (0〜1)。ガウスぼかしを周期的に (FFT で) かけて差し引く。"""
    rng = np.random.default_rng(42)
    noise = rng.integers(0, 256, (height, width)).astype(np.float64)
    sigma = 1.5
    fy = np.fft.fftfreq(height)[:, None]
    fx = np.fft.fftfreq(width)[None, :]
    gauss = np.exp(-2 * (np.pi * sigma) ** 2 * (fx ** 2 + fy ** 2))
    blur = np.real(np.fft.ifft2(np.fft.fft2(noise) * gauss))
    hp = noise - blur
    lo, hi = hp.min(), hp.max()
    hp = (hp - lo) / (hi - lo) if hi > lo else np.zeros_like(hp)
    hp = hp.astype(np.float32)
    hp.setflags(write=False)
    return hp


BAYER_4 = (np.array([[0, 8, 2, 10], [12, 4, 14, 6], [3, 11, 1, 9], [15, 7, 13, 5]], dtype=np.float32) / 16.0) - 0.5


def dither_pattern(method, width, height, offset=(0, 0)):
    """ディザのずらし量 (H, W)、値はおよそ -0.5〜0.5。offset だけ模様をずらす。対応しない方式なら None。"""
    ox, oy = offset
    ys = (np.arange(height) + oy)
    xs = (np.arange(width) + ox)
    if method == "blue_noise":
        base = _periodic_blue_noise(width, height) - 0.5
        return base[np.ix_(ys % height, xs % width)]
    if method == "ordered":
        return BAYER_4[np.ix_(ys % 4, xs % 4)]
    return None
