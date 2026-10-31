"""ディザの見え方の物差し (Phase 13.1): 見る距離に合わせた色差・残って見える粒・ぼかしで消える粒・ちらつき・明るさごとの粒。

距離 k はスクリーンの高さ H の倍数 (D = k·H ブロック)。ぼかしは H×k と、1 倍の距離でスクリーンの高さが占める視角 θ1 だけで決まる:
マス/度 = H·k / θ1。θ1 の既定 24.7 度は、24 型 1080p を 60 cm で見て、ゲーム内の縦の視野を 60 度とした仮定で、実機のショットで校正する。

ぼかし V_k は S-CIELAB (Zhang & Wandell 1997) の形に沿う。線形光の反対色 3 成分それぞれにガウス和をかけて Lab へ戻す。
本来の式から変えた点が 1 つある。明るさの 7 度の負の山を外し、残り 2 本の重みを足して 1 にした。入れたままだと高い周波数のゲインが
1.117 になり、誤差と粒が元の画素の値を超える。代償として低い周波数の感度の落ち込みは表さない (低周波の色ずれは多めに数える)。
フィルタの数値と反対色の行列は、原論文や参照コードとまだ照合していない。照合できるまでは「コントラスト感度に似せたガウスの近似」として扱う。
この行列では白が反対色の 0 にならない (D65 で赤緑 −0.053、青黄 +0.037) ので、成分ごとにぼかしの幅が違う分、灰色のディザも a, b へ少し漏れる
(灰色の近い 2 色の 256×144、k=1 で L の粒 3.43 に ab の粒 0.88)。grain_ab の絶対値には、この分が明るさの粒に比例して入っている。

このモジュールは numpy と skimage (Lab と CIEDE2000) だけを使う。
"""
import math
from functools import lru_cache

import numpy as np
from skimage.color import deltaE_ciede2000, rgb2lab, xyz2lab

SCREEN_ANGLE = 24.7  # 1 倍の距離でスクリーンの高さが占める視角 (度)
DISTANCES = (1, 2, 4)
SIGMA_MEAN = 2.0  # 見ている絵から引く平均のぼかし M と、エネルギーを集める窓 G2 の σ (マス)。視覚実験の根拠はない
TAU = 2.0  # 静止とみなす、5 フレームの Lab の幅
MASK_DISTANCE = 2  # 静止の判定は全距離共通でこの距離の絵を使う
MIN_STATIC_FRAME = 0.01  # 静止がこれ未満のフレームは数えない
MIN_STATIC_CLIP = 0.05  # 平均の静止がこれ未満のクリップは n/a
L_BANDS = ((0, 25), (25, 50), (50, 75), (75, 101))
MIN_BAND = 0.01

# 成分ごとの (重み, 広がり s (度))。s は exp(-r²/s²) の s で、σ = s/√2
LOBES = (((0.898, 0.05), (0.102, 0.225)),  # 明るさ (7 度の負の山を外して正規化)
         ((0.617, 0.0685), (0.383, 0.826)),  # 赤緑
         ((0.568, 0.092), (0.432, 0.645)))  # 青黄
RGB2XYZ = np.array([[0.4124564, 0.3575761, 0.1804375], [0.2126729, 0.7151522, 0.0721750], [0.0193339, 0.1191920, 0.9503041]])
XYZ2OPP = np.array([[278.7336, 721.8031, -106.5520], [-448.7736, 289.8056, 77.1569], [85.9513, -589.9859, 501.1089]]) / 1000
OPP2XYZ = np.linalg.inv(XYZ2OPP)
RGB2OPP = XYZ2OPP @ RGB2XYZ


def cells_per_degree(height, distance, screen_angle=SCREEN_ANGLE):
    return height * distance / screen_angle


@lru_cache(maxsize=256)
def gaussian_kernel(sigma):
    """1 次元のガウス核。マスを箱とみなして積分するので、σ が 0.5 マス未満でも崩れず、σ→0 で恒等になる。"""
    if sigma <= 0:
        return np.ones(1)
    radius = max(1, math.ceil(4 * sigma + 0.5))
    cdf = np.array([math.erf((x - 0.5) / (sigma * math.sqrt(2))) for x in range(-radius, radius + 2)])
    kernel = np.diff(cdf)
    kernel = kernel + kernel[::-1]
    kernel /= kernel.sum()
    kernel.flags.writeable = False
    return kernel


def _convolve_axis(img, kernel, axis):
    radius = len(kernel) // 2
    if radius == 0:
        return img.astype(np.float64)
    pad = [(0, 0)] * img.ndim
    pad[axis] = (radius, radius)
    padded = np.pad(img, pad, mode="edge")  # 端は mode=nearest と同じ
    size = img.shape[axis]

    def window(start):
        index = [slice(None)] * img.ndim
        index[axis] = slice(start, start + size)
        return padded[tuple(index)]

    out = kernel[radius] * window(radius)
    for i in range(1, radius + 1):  # 核は左右対称
        out = out + kernel[radius + i] * (window(radius + i) + window(radius - i))
    return out


def gaussian_blur(img, sigma):
    """先頭の 2 軸 (高さ・幅) にかけるガウスぼかし。"""
    kernel = gaussian_kernel(sigma)
    return _convolve_axis(_convolve_axis(img, kernel, 0), kernel, 1)


def srgb_to_linear(rgb):
    x = np.asarray(rgb, np.float64) / 255
    return np.where(x <= 0.04045, x / 12.92, ((x + 0.055) / 1.055) ** 2.4)


def view_lab(rgb, cpd):
    """距離に合わせてぼかした絵 V_k。sRGB (uint8) → 線形光 → 反対色 → 成分ごとのガウス和 → XYZ → Lab。cpd はマス/度。"""
    opp = srgb_to_linear(rgb) @ RGB2OPP.T
    out = np.empty_like(opp)
    for c, lobes in enumerate(LOBES):
        out[..., c] = sum(weight * gaussian_blur(opp[..., c], spread * cpd / math.sqrt(2)) for weight, spread in lobes)
    return xyz2lab(out @ OPP2XYZ.T)


def _mean_blur(lab):
    return gaussian_blur(lab, SIGMA_MEAN)


def _excess(residual_shown, residual_source):
    """局所のエネルギーを引いて、足された粒の rms を推定する。独立な雑音ならエネルギーは足し算になるので、元の細部は打ち消される。"""
    energy_shown = gaussian_blur(residual_shown, SIGMA_MEAN)
    energy_source = gaussian_blur(residual_source, SIGMA_MEAN)
    return np.sqrt(np.maximum(energy_shown - energy_source, 0))


def _grain_visible_map(view_source, view_shown):
    return _visible_residual_excess(view_source, view_shown, slice(None))


def _visible_residual_excess(view_source, view_shown, channels):
    d_shown = view_shown - _mean_blur(view_shown)
    d_source = view_source - _mean_blur(view_source)
    return _excess((d_shown[..., channels] ** 2).sum(-1), (d_source[..., channels] ** 2).sum(-1))


def _grain_visible(view_source, view_shown):
    return {"all": float(_grain_visible_map(view_source, view_shown).mean()),
            "L": float(_visible_residual_excess(view_source, view_shown, slice(0, 1)).mean()),
            "ab": float(_visible_residual_excess(view_source, view_shown, slice(1, 3)).mean())}


def _grain_removed(lab_source, lab_shown, view_source, view_shown):
    return float(_excess(((lab_shown - view_shown) ** 2).sum(-1), ((lab_source - view_source) ** 2).sum(-1)).mean())


def _bands(grain_map, lab_source):
    """元の映像の M(L*) で帯に分ける。帯を元で決めるので、変更前後で同じマスを同じ帯として比べられる。"""
    lightness = gaussian_blur(lab_source[..., 0], SIGMA_MEAN)
    values, fractions = [], []
    for low, high in L_BANDS:
        mask = (lightness >= low) & (lightness < high)
        fraction = float(mask.mean())
        fractions.append(fraction)
        values.append(float(grain_map[mask].mean()) if fraction >= MIN_BAND else None)
    return {"bands": values, "fractions": fractions}


def view_error(source, shown, cpd):
    """見る距離の色差: 同じ V_k をかけた 2 枚の CIEDE2000 の全マス平均。"""
    return float(deltaE_ciede2000(view_lab(source, cpd), view_lab(shown, cpd)).mean())


def grain_visible(source, shown, cpd):
    """残って見える粒 (採用基準の本命)。見ている絵と、その平均の色との差のエネルギーを、元の分だけ引く。{"all", "L", "ab"} を返す。"""
    return _grain_visible(view_lab(source, cpd), view_lab(shown, cpd))


def grain_removed(source, shown, cpd):
    """ぼかしで消える粒 (補助)。ぼかす前後の差を測るので距離とともに増える。粒を高い周波数へ移す改善で上がるので、採用基準には使わない。"""
    return _grain_removed(rgb2lab(source), rgb2lab(shown), view_lab(source, cpd), view_lab(shown, cpd))


def grain_bands(source, shown, cpd):
    """明るさの帯ごとの grain_visible。{"bands": [4 つ、割合 1% 未満は None], "fractions": [4 つ]} を返す。"""
    return _bands(_grain_visible_map(view_lab(source, cpd), view_lab(shown, cpd)), rgb2lab(source))


def frame_metrics(source, shown, height, distances=DISTANCES, screen_angle=SCREEN_ANGLE):
    """1 フレームの色差と粒を、距離ごとに V_k を 1 回ずつだけ計算してまとめて出す。"""
    lab_source, lab_shown = rgb2lab(source), rgb2lab(shown)
    out = {}
    for k in distances:
        cpd = cells_per_degree(height, k, screen_angle)
        view_source, view_shown = view_lab(source, cpd), view_lab(shown, cpd)
        out[k] = {"view_error": float(deltaE_ciede2000(view_source, view_shown).mean()),
                  "grain_visible": _grain_visible(view_source, view_shown),
                  "grain_removed": _grain_removed(lab_source, lab_shown, view_source, view_shown),
                  "grain_band": _bands(_grain_visible_map(view_source, view_shown), lab_source)}
    return out


def _maximum_3x3(img):
    padded = np.pad(img, 1, mode="edge")
    rows = np.maximum(np.maximum(padded[:-2], padded[1:-1]), padded[2:])
    return np.maximum(np.maximum(rows[:, :-2], rows[:, 1:-1]), rows[:, 2:])


def static_mask(labs, tau=TAU):
    """元の映像の Lab (T×H×W×3) から、動かない場所 (T×H×W) を求める。

    t−2〜t+2 の成分ごとの幅のユークリッド長が τ 以下のマスを静止とし、動きの隣も 3×3 で外す。
    5 フレームの幅なので、1 フレームごとは閾値以下の遅いフェードも、場面の切り替わりの前後も静止にならない。
    """
    labs = np.asarray(labs)
    mask = np.empty(labs.shape[:3], bool)
    for t in range(len(labs)):
        window = labs[max(0, t - 2):t + 3]
        width = np.sqrt(((window.max(0) - window.min(0)) ** 2).sum(-1))
        mask[t] = _maximum_3x3(width) <= tau
    return mask


def _stream_views(frames, cpd):
    """frames の V_k をフレーム番号で引く。前のフレームと同じ絵は計算し直さない。"""
    cache = {}

    def get(t):
        if t not in cache:
            if t - 1 in cache and np.array_equal(frames[t], frames[t - 1]):
                cache[t] = cache[t - 1]
            else:
                cache[t] = view_lab(frames[t], cpd)
            for old in [i for i in cache if i < t - 1]:
                del cache[old]
        return cache[t]
    return get


def flicker(source, shown, height, distances=DISTANCES, screen_angle=SCREEN_ANGLE, tau=TAU, min_static=MIN_STATIC_CLIP):
    """動かない場所のちらつき。

    S_t は元の映像だけで決める (t と t−1 の両方で静止のマス)。flicker_rate は S_t の中で表示の RGB が変わったマスの割合、
    flicker_mag[k] は S_t の上で、距離 k の絵の 1 フレームの変化のエネルギーから元の分を引いた rms。
    静止がクリップ平均で min_static 未満なら n/a (None) を返す。0 にはしない。
    """
    count = min(len(source), len(shown))
    source, shown = source[:count], shown[:count]
    empty = {"flicker_rate": None, "flicker_mag": {k: None for k in distances}, "static_coverage": 0.0, "static_frames": 0}
    if count < 2:
        return empty
    mask_cpd = cells_per_degree(height, MASK_DISTANCE, screen_angle)
    mask_view = _stream_views(source, mask_cpd)
    static = static_mask(np.stack([mask_view(t).astype(np.float32) for t in range(count)]), tau)
    pairs = static[1:] & static[:-1]
    coverage = pairs.mean(axis=(1, 2))
    used = [t + 1 for t in range(count - 1) if coverage[t] >= MIN_STATIC_FRAME]
    average = float(coverage.mean())
    empty["static_coverage"] = average
    if average < min_static or not used:
        return empty
    rates = [float(np.any(shown[t] != shown[t - 1], -1)[pairs[t - 1]].mean()) for t in used]
    magnitudes = {}
    for k in distances:
        cpd = cells_per_degree(height, k, screen_angle)
        view_source, view_shown = _stream_views(source, cpd), _stream_views(shown, cpd)
        values = []
        for t in used:
            mask = pairs[t - 1]
            previous_source, previous_shown = view_source(t - 1), view_shown(t - 1)
            change_shown = ((view_shown(t) - previous_shown) ** 2).sum(-1)[mask].mean()
            change_source = ((view_source(t) - previous_source) ** 2).sum(-1)[mask].mean()
            values.append(math.sqrt(max(0.0, float(change_shown - change_source))))
        magnitudes[k] = float(np.mean(values))
    return {"flicker_rate": float(np.mean(rates)), "flicker_mag": magnitudes, "static_coverage": average, "static_frames": len(used)}
