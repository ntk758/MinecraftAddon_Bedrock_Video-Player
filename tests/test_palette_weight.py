"""AI で人物の色を優先するパレット (convert.py --palette-weight-mask): マスクの中の色を重く数える。"""
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import convert  # noqa: E402


def test_weighted_sample_repeats_the_masked_pixels():
    frame = np.zeros((4, 4, 3), np.uint8)
    frame[0, 0] = (255, 0, 0)
    mask = np.zeros((4, 4, 3), np.uint8)
    mask[0, 0] = 255
    sample = convert.weighted_sample(frame, mask, 4)
    assert sample.shape == (16 + 3, 1, 3)
    assert (sample[:, 0] == (255, 0, 0)).all(axis=1).sum() == 4


def test_weighted_sample_ignores_a_nearly_empty_mask():
    frame = np.zeros((100, 100, 3), np.uint8)
    mask = np.zeros((100, 100), np.uint8)
    mask[0, :10] = 255  # 0.1%
    assert convert.weighted_sample(frame, mask, 4) is frame
    assert convert.weighted_sample(frame, None, 4) is frame


def test_gop_palettes_see_the_masked_pixels_weighted():
    """GOP ごとのパレットを選ぶ画素のうち、マスクの中の画素が weight 倍に数えられる (マスクのコマと色のコマが揃う)。"""
    rng = np.random.default_rng(0)
    frames, masks = [], []
    for i in range(6):
        a = rng.integers(0, 256, (24, 32, 3), dtype=np.uint8)
        a[4:8, 4:12] = (224, 172, 140)
        m = np.zeros((24, 32, 3), np.uint8)
        m[4:8, 4:12] = 255
        frames.append(a)
        masks.append(m)
    seen = []

    def builder(samples, first_frame):
        pixels = np.concatenate([np.asarray(x).reshape(-1, 3) for x in samples])
        seen.append(float((pixels == (224, 172, 140)).all(axis=1).mean()))
        return []

    convert.analyze_gops(iter(frames), builder)
    convert.analyze_gops(iter(frames), builder, iter(masks), 8)
    plain, weighted = seen
    assert plain == pytest.approx(32 / 768, rel=0.05)
    assert weighted == pytest.approx(32 * 8 / (768 + 32 * 7), rel=0.05)
