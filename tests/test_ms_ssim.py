"""MS-SSIM (benchmark/metrics/ms_ssim.py) のテスト (Issue #1)。"""
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "benchmark"))

skimage_metrics = pytest.importorskip("skimage.metrics")
from metrics.ms_ssim import calculate_ms_ssim, scales_for  # noqa: E402


def textured(h, w, seed=0):
    rng = np.random.default_rng(seed)
    yy, xx = np.mgrid[0:h, 0:w]
    base = 128 + 60 * np.sin(xx / 5.0) * np.cos(yy / 7.0) + rng.normal(0, 10, (h, w))
    return np.clip(np.stack([base, base * 0.8, 255 - base], axis=-1), 0, 255).astype(np.uint8)


def test_identical_images_score_one():
    img = textured(128, 128)
    assert calculate_ms_ssim(img, img) == pytest.approx(1.0)


def test_single_scale_matches_scikit_image_ssim():
    """1 段だけなら、明るさについての普通の SSIM (ガウス窓) と同じ値になる。"""
    a = textured(96, 96, 1).astype(np.float64)
    b = np.clip(a + np.random.default_rng(2).normal(0, 20, a.shape), 0, 255)
    gray = lambda img: img[..., 0] * 0.299 + img[..., 1] * 0.587 + img[..., 2] * 0.114  # noqa: E731
    expected = skimage_metrics.structural_similarity(gray(a), gray(b), data_range=255, gaussian_weights=True,
                                                     sigma=1.5, use_sample_covariance=False)
    assert calculate_ms_ssim(a, b, scales=1) == pytest.approx(expected, abs=1e-6)


def test_more_noise_scores_lower():
    img = textured(128, 128).astype(np.float64)
    rng = np.random.default_rng(3)
    scores = [calculate_ms_ssim(img, np.clip(img + rng.normal(0, s, img.shape), 0, 255)) for s in (5, 20, 60)]
    assert 1.0 > scores[0] > scores[1] > scores[2] > 0.0


def test_small_images_use_fewer_scales():
    assert scales_for((256, 256)) == 5
    assert scales_for((64, 64)) == 3
    assert scales_for((16, 16)) == 1
    img = textured(64, 64)
    assert 0.0 < calculate_ms_ssim(img, np.roll(img, 1, axis=1)) < 1.0
    assert np.isnan(calculate_ms_ssim(img[:8, :8], img[:8, :8]))
