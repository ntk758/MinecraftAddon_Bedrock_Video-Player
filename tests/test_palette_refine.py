"""自動パレットの入れ替え (refine_palette): 色数・重複・再現性と、混ぜたときの誤差が増えないこと。"""
import numpy as np

from mvcodec.auto_palette import REFINE_TRIES, _mix_error, refine_palette
from mvcodec.color import AUTO_PALETTE_CANDIDATES, _linear_to_oklab_np, _srgb_to_linear_np


def pixels_of(seed=0, count=4000):
    """暗いオリーブ・鮮やかな青・肌色のかたまり。"""
    rng = np.random.default_rng(seed)
    centers = np.array([[70, 75, 25], [20, 60, 240], [225, 170, 140]], np.float32)
    picks = rng.integers(0, len(centers), count)
    return np.clip(centers[picks] + rng.normal(0, 8, (count, 3)), 0, 255).astype(np.float32)


def error_of(pixels, blocks):
    goal = _srgb_to_linear_np(pixels)
    rgb = np.array([b["rgb"] for b in blocks], np.float32)
    return _mix_error(goal, _linear_to_oklab_np(goal), np.ones(len(goal), np.float32), rgb)[0]


def test_lowers_the_mix_error_of_a_poor_palette_and_keeps_the_count():
    pixels = pixels_of()
    start = AUTO_PALETTE_CANDIDATES[:12]
    refined = refine_palette(pixels, start, AUTO_PALETTE_CANDIDATES)
    assert len(refined) == len(start)
    assert len({tuple(b["rgb"]) for b in refined}) == len(refined)
    assert all(b in AUTO_PALETTE_CANDIDATES for b in refined)
    assert error_of(pixels, refined) < error_of(pixels, start) * 0.9


def test_same_input_gives_the_same_palette():
    pixels = pixels_of(seed=3)
    start = AUTO_PALETTE_CANDIDATES[:24]
    assert refine_palette(pixels, start, AUTO_PALETTE_CANDIDATES) == refine_palette(pixels, start, AUTO_PALETTE_CANDIDATES)


def test_full_or_tiny_palettes_are_left_alone():
    pixels = pixels_of()
    assert refine_palette(pixels, AUTO_PALETTE_CANDIDATES, AUTO_PALETTE_CANDIDATES) == AUTO_PALETTE_CANDIDATES
    one = AUTO_PALETTE_CANDIDATES[:1]
    assert refine_palette(pixels, one, AUTO_PALETTE_CANDIDATES) == one


def test_search_is_bounded():
    assert REFINE_TRIES <= 100
