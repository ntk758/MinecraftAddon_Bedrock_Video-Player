"""フレーム間の安定化 (Phase 8.2) のテスト: 条件付き更新、ディザ模様のずらし、全体の動きの推定。"""
import sys
from pathlib import Path

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import convert  # noqa: E402
from mvcodec.decode import iter_decoded_frames, load_frame_data  # noqa: E402
from mvcodec.dither_motion import DitherAligner, dither_pattern, estimate_global_shift  # noqa: E402


def test_keep_previous_blocks_only_when_almost_as_good():
    palette = np.array([[0, 0, 0], [100, 100, 100], [110, 110, 110]], np.float32)
    source = np.array([[[108, 108, 108], [10, 10, 10]]], np.float32)
    new = np.array([[2, 0]])
    previous = np.array([[1, 1]])
    # 1 画素目: 前の色 (100) は新しい色 (110) より少し悪いだけなので前のまま。2 画素目: 前の色は大きく外れるので更新
    kept = convert.keep_previous_blocks(new, previous, source, palette, threshold=24)
    assert kept.tolist() == [[1, 0]]
    assert convert.keep_previous_blocks(new, previous, source, palette, threshold=0).tolist() == [[2, 0]]
    assert convert.keep_previous_blocks(new, None, source, palette, threshold=24).tolist() == [[2, 0]]


def make_gradient_pan(folder, frames=16, w=48, h=24):
    """なめらかなグラデーションがゆっくり横に流れる映像 (ディザの境目でパレット番号がちらつきやすい)。"""
    folder.mkdir()
    xx = np.arange(w + frames)[None, :, None]
    world = np.clip(np.concatenate([xx * 3, 80 + xx * 2, 200 - xx], axis=2), 0, 255).repeat(h, axis=0)
    for t in range(frames):
        Image.fromarray(world[:, t:t + w].astype(np.uint8)).save(folder / f"f{t:03d}.png")
    return w, h


def changed_per_frame(path):
    previous, changed = None, []
    for board, _ in iter_decoded_frames(load_frame_data(path)):
        if previous is not None:
            changed.append(int((board != previous).sum()))
        previous = board
    return sum(changed)


def test_temporal_hysteresis_reduces_changed_blocks(tmp_path):
    w, h = make_gradient_pan(tmp_path / "frames")
    common = ["--frames-dir", str(tmp_path / "frames"), "--width", str(w), "--height", str(h), "--fps", "10",
              "--device", "cpu", "--palette", "full", "--dither-method", "blue_noise", "--no-adaptive-fps"]
    assert convert.main([*common, "--output", str(tmp_path / "off.js"), "--temporal-hysteresis", "0"]) == 0
    assert convert.main([*common, "--output", str(tmp_path / "on.js")]) == 0
    assert changed_per_frame(tmp_path / "on.js") < changed_per_frame(tmp_path / "off.js") * 0.8


def test_dither_pattern_is_periodic_and_shifts():
    base = dither_pattern("blue_noise", 16, 8)
    assert base.shape == (8, 16) and -0.5 <= base.min() and base.max() <= 0.5
    assert np.allclose(dither_pattern("blue_noise", 16, 8, (3, 2)), np.roll(base, (-2, -3), axis=(0, 1)))
    assert np.allclose(dither_pattern("ordered", 9, 6, (4, 4)), dither_pattern("ordered", 9, 6))
    assert dither_pattern("none", 4, 4) is None


def test_global_shift_follows_a_pan_and_ignores_still_frames():
    rng = np.random.default_rng(2)
    world = (rng.random((60, 140, 3)) * 255).astype(np.uint8)
    prev, cur = world[10:50, 10:110], world[11:51, 13:113]   # 中身が左上へ動く: 今[y][x] = 前[y+1][x+3]
    assert estimate_global_shift(prev, cur) == (3, 1)
    assert estimate_global_shift(prev, prev) == (0, 0)
    aligner = DitherAligner()
    aligner.update(prev)
    assert aligner.update(cur) == (3, 1)
    assert DitherAligner(enabled=False).update(cur) == (0, 0)


def test_auto_palette_color_space_and_size_limit():
    """自動パレットは色数の上限を守り、OkLab でも同じ入力から同じパレットを返す (Issue #3)。"""
    import numpy as np
    import pytest

    pytest.importorskip("torch")
    from mvcodec.auto_palette import generate_auto_palette
    from mvcodec.color import ALL_BLOCKS

    rng = np.random.default_rng(0)
    frames = [rng.integers(0, 256, size=(24, 32, 3), dtype=np.uint8) for _ in range(3)]
    for space in ("rgb", "oklab"):
        first = generate_auto_palette(iter(frames), ALL_BLOCKS, max_colors=12, sample_stride=1, color_space=space)
        again = generate_auto_palette(iter(frames), ALL_BLOCKS, max_colors=12, sample_stride=1, color_space=space)
        assert len(first) == 12 and first == again
    # 赤一色の映像なら、どちらの色空間でも赤いブロックが選ばれる
    red = [np.full((8, 8, 3), (150, 35, 35), np.uint8)]
    for space in ("rgb", "oklab"):
        picked = generate_auto_palette(iter(red), ALL_BLOCKS, max_colors=2, sample_stride=1, color_space=space)
        r, g, b = picked[0]["rgb"]
        assert r > g + 60 and r > b + 60, picked[0]
