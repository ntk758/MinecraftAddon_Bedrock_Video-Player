"""マルチスケール SSIM (MS-SSIM, Wang, Simoncelli & Bovik 2003)。

明るさ (BT.601 の Y) について、11×11・σ=1.5 のガウス窓で SSIM の各成分を求め、2×2 の平均で縮小しながら
最大 5 段のスケールで重み付き積を取る:
    MS-SSIM = (l_M · cs_M)^w_M × Π_{j<M} cs_j^w_j
重みは論文の値 (0.0448, 0.2856, 0.3001, 0.2363, 0.1333)。小さな画像 (64×64 など) では、一番粗いスケールでも
窓 (11 画素) が収まる段数までに減らし、使う段の重みを合計 1 になるよう割り直す。
窓の外にはみ出す縁の画素は平均から除く (scikit-image の structural_similarity と同じ扱い)。
cs が負になる画素の多い画像では積が定義できないので、各スケールの値は 0 以上に切り詰める。
"""
import numpy as np

try:
    from scipy.ndimage import gaussian_filter
except ImportError:  # pragma: no cover - scipy は scikit-image と一緒に入る
    gaussian_filter = None

WEIGHTS = np.array([0.0448, 0.2856, 0.3001, 0.2363, 0.1333])
SIGMA = 1.5
WINDOW = 11
K1, K2 = 0.01, 0.03
DATA_RANGE = 255.0


def _luminance(img):
    img = np.asarray(img, dtype=np.float64)
    if img.ndim == 3:
        img = img[..., 0] * 0.299 + img[..., 1] * 0.587 + img[..., 2] * 0.114
    return img


def _filter(x):
    # truncate=3.5 で半径 5 (11×11 の窓) にそろえる
    return gaussian_filter(x, SIGMA, mode="reflect", truncate=(WINDOW // 2) / SIGMA)


def _ssim_components(x, y):
    """(l の平均 × cs の平均ではなく) 画素ごとの l·cs の平均と cs の平均を返す。"""
    c1 = (K1 * DATA_RANGE) ** 2
    c2 = (K2 * DATA_RANGE) ** 2
    mu_x, mu_y = _filter(x), _filter(y)
    sxx = _filter(x * x) - mu_x * mu_x
    syy = _filter(y * y) - mu_y * mu_y
    sxy = _filter(x * y) - mu_x * mu_y
    luminance = (2 * mu_x * mu_y + c1) / (mu_x * mu_x + mu_y * mu_y + c1)
    contrast_structure = (2 * sxy + c2) / (sxx + syy + c2)
    pad = WINDOW // 2
    crop = (slice(pad, -pad), slice(pad, -pad))
    return float((luminance * contrast_structure)[crop].mean()), float(contrast_structure[crop].mean())


def _downsample(x):
    h, w = x.shape[0] // 2 * 2, x.shape[1] // 2 * 2
    x = x[:h, :w]
    return (x[0::2, 0::2] + x[1::2, 0::2] + x[0::2, 1::2] + x[1::2, 1::2]) / 4.0


def scales_for(shape, max_scales=len(WEIGHTS)):
    """一番粗いスケールでも窓より 1 画素以上大きいまま使える段数 (最低 1)。"""
    side = min(shape[:2])
    scales = 1
    while scales < max_scales and side // (2 ** scales) > WINDOW:
        scales += 1
    return scales


def calculate_ms_ssim(img1, img2, scales=None):
    """2 枚の画像 ((H, W, 3) または (H, W)、0〜255) の MS-SSIM (0〜1、同じ画像なら 1)。

    画像が窓 (11×11) より小さいときと、scipy が無いときは nan。
    """
    if gaussian_filter is None:
        return float("nan")
    x, y = _luminance(img1), _luminance(img2)
    if x.shape != y.shape:
        raise ValueError(f"画像の大きさが違います: {x.shape} と {y.shape}")
    if min(x.shape) <= WINDOW:
        return float("nan")
    scales = scales or scales_for(x.shape)
    weights = WEIGHTS[:scales] / WEIGHTS[:scales].sum()
    value = 1.0
    for j in range(scales):
        ssim, cs = _ssim_components(x, y)
        if j == scales - 1:
            value *= max(ssim, 0.0) ** weights[j]
        else:
            value *= max(cs, 0.0) ** weights[j]
            x, y = _downsample(x), _downsample(y)
    return float(value)
