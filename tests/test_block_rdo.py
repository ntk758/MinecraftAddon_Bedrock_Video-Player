"""ブロックモードのレート歪み最適化 (Phase 8.5、mvcodec/block_rdo.py) のテスト。"""
import sys
from pathlib import Path

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import convert  # noqa: E402
from mvcodec.block_rdo import BlockRDO  # noqa: E402
from mvcodec.decode import iter_decoded_frames, load_frame_data  # noqa: E402

PALETTE = np.array([[0, 0, 0], [255, 255, 255], [128, 128, 128], [200, 40, 40]], np.float32)


def dithered_gray(h=16, w=16):
    """元は一様な灰色 (128) なのに、減色で黒と白の市松模様になったフレーム (ディザの粒)。"""
    source = np.full((h, w, 3), 128, np.uint8)
    checker = (np.add.outer(np.arange(h), np.arange(w)) % 2).astype(np.int32)  # 0 = 黒, 1 = 白
    return source, checker


def test_zero_strength_returns_the_input():
    source, indices = dithered_gray()
    assert np.array_equal(BlockRDO(0)(indices, source, PALETTE), indices)


def test_dither_noise_on_a_flat_area_becomes_one_block_color():
    source, indices = dithered_gray()
    out = BlockRDO(16)(indices, source, PALETTE)
    # 灰色のブロックがあるので、8×8 ごとに 1 色 (灰色) で塗るのが、色の誤差も置く費用も小さい
    assert np.all(out == 2)


def test_unchanged_source_keeps_the_displayed_blocks():
    source, indices = dithered_gray()
    reference = np.full_like(indices, 2)
    # 前のフレームで灰色に塗った場所に、また同じ元の色が来た: 置き直さない
    rdo = BlockRDO(16)
    out = rdo(1 - indices, source, PALETTE, reference)
    assert np.array_equal(out, reference)
    assert rdo.stats["skip"] + rdo.stats["exact"] == 4


def test_never_places_more_than_the_quantized_frame():
    rng = np.random.default_rng(3)
    h, w = 24, 40
    source = rng.integers(0, 256, (h, w, 3), dtype=np.uint8)
    indices = ((PALETTE[None, None] - source[:, :, None].astype(np.float32)) ** 2).sum(-1).argmin(-1).astype(np.int32)
    reference = rng.integers(-1, 4, (h, w)).astype(np.int32)
    rdo = BlockRDO(64, hysteresis=0)
    out = rdo(indices, source, PALETTE, reference)
    assert out.shape == indices.shape and out.min() >= 0 and out.max() < len(PALETTE)
    # 8×8 ごとに、見積もった置く時間は減色の結果以下 (置く費用を増やす候補は選ばない)。
    # マスの数は増えることがある (散らばった 50 マスより、1 個の箱にできる 54 マスの方が速い)
    out_place, _ = rdo._rate(out, reference, reference)
    exact_place, _ = rdo._rate(indices, reference, reference)
    assert np.all(out_place <= exact_place + 1e-6)
    assert rdo.stats["exact"] < 15
    # 前の盤面に未設置 (-1) のマスがあるブロックは、そのマスを未設置のまま残さない
    assert out.min() >= 0


def test_motion_candidate_copies_a_shifted_block():
    h, w = 16, 32
    stripes = np.tile(np.array([3, 3, 1, 1, 1, 0, 0, 0], np.int32), (h, w // 8))
    reference = stripes.copy()
    shifted = np.roll(stripes, 1, axis=1)
    source = PALETTE[shifted].astype(np.uint8)
    rdo = BlockRDO(16)
    out = rdo(shifted, source, PALETTE, reference)
    # 動き補償の予測が元の映像と一致するブロックはそのまま使う (端の列は範囲外なので予測と違うことがある)
    assert np.array_equal(out[:, 8:], shifted[:, 8:])


def test_auto_strength_stays_within_the_error_budget():
    rng = np.random.default_rng(7)
    h, w = 32, 48
    source = (rng.random((h, w, 3)) * 255).astype(np.uint8)
    indices = ((PALETTE[None, None] - source[:, :, None].astype(np.float32)) ** 2).sum(-1).argmin(-1).astype(np.int32)
    reference = rng.integers(0, 4, (h, w)).astype(np.int32)
    # 予算が十分大きければ一番強い強さと同じ、0 なら色の誤差を増やさない
    loose = BlockRDO("auto", budget=1e9)
    assert np.array_equal(loose(indices, source, PALETTE, reference), BlockRDO(64)(indices, source, PALETTE, reference))
    assert loose.strength_counts == {64: 1}
    strict = BlockRDO("auto", budget=0)
    strict(indices, source, PALETTE, reference)
    assert strict.added_errors[-1] <= 0 or list(strict.strength_counts) == [1]


def write_noisy_pan(folder, frames=8, w=32, h=16):
    folder.mkdir()
    rng = np.random.default_rng(0)
    world = (rng.random((h, w + frames, 1)) * 60 + np.linspace(60, 200, w + frames)[None, :, None]).repeat(3, 2)
    for t in range(frames):
        Image.fromarray(world[:, t:t + w].astype(np.uint8)).save(folder / f"f{t:03d}.png")
    return w, h


def test_convert_with_block_rdo_decodes_to_the_optimized_frames(tmp_path):
    w, h = write_noisy_pan(tmp_path / "frames")
    common = ["--frames-dir", str(tmp_path / "frames"), "--width", str(w), "--height", str(h), "--fps", "10",
              "--device", "cpu", "--palette", "full", "--dither-method", "blue_noise"]
    assert convert.main([*common, "--output", str(tmp_path / "off.js"), "--block-rdo", "0"]) == 0
    assert convert.main([*common, "--output", str(tmp_path / "on.js"), "--block-rdo", "64"]) == 0
    off = load_frame_data(tmp_path / "off.js")
    on = load_frame_data(tmp_path / "on.js")
    assert on["format"] == off["format"] and on["frame_count"] == off["frame_count"] == 8
    boards = [board for board, _ in iter_decoded_frames(on)]
    assert len(boards) == 8 and all(b.min() >= 0 for b in boards)
    assert sum(map(len, on["chunks"])) < sum(map(len, off["chunks"]))


def test_heightfield_colour_plane_is_optimized_without_touching_the_shape(tmp_path):
    """2.5D の立体では色の平面だけを最適化し、形 (値の平面) はそのまま。空の列の色は誤差に数えない。"""
    w, h = write_noisy_pan(tmp_path / "frames")
    common = ["--frames-dir", str(tmp_path / "frames"), "--width", str(w), "--height", str(h), "--fps", "10",
              "--device", "cpu", "--palette", "full", "--dither-method", "blue_noise", "--mode", "relief", "--depth", "6"]
    assert convert.main([*common, "--output", str(tmp_path / "off.js"), "--block-rdo", "0"]) == 0
    assert convert.main([*common, "--output", str(tmp_path / "on.js"), "--block-rdo", "64"]) == 0
    off, on = load_frame_data(tmp_path / "off.js"), load_frame_data(tmp_path / "on.js")
    assert on["layout"] == "heightfield"
    for (a, _), (b, _) in zip(iter_decoded_frames(off), iter_decoded_frames(on)):
        # 埋まっているマスの位置 (形) は同じ
        assert np.array_equal(a != off["air_level"], b != on["air_level"])
    assert sum(map(len, on["chunks"])) < sum(map(len, off["chunks"]))
    # 空の列 (厚み 0) は見えないので、色の誤差に数えない
    rdo = BlockRDO(16)
    rdo(np.zeros((4, 8), np.int32), np.full((4, 8, 3), 128, np.uint8), PALETTE, None, place_weight=np.zeros((4, 8)))
    assert rdo._distortion(np.full((4, 8), 3), rdo._palette_lab(PALETTE), np.zeros((4, 8, 3), np.float32),
                           np.zeros((4, 8, 3), np.float32)).sum() == 0


def test_thin_text_on_a_static_page_is_not_painted_over():
    """動きのない白いページの、細い灰色の文字 (縮小でにじんだ文字) の入ったブロックを、前の盤面 (文字の無い白) で済ませない。
    強さの予算はフレーム全体の平均なので、以前は少しのブロックの大きな崩れが平均に埋もれて、文字が消えていた。"""
    palette = np.array([[250, 250, 250], [200, 200, 200]], np.float32)
    h, w = 64, 128
    source = np.full((h, w, 3), 250, np.uint8)
    source[20, 8:40] = 200  # 1 行の細い文字の画
    indices = np.where(source[..., 0] == 200, 1, 0).astype(np.int32)
    reference = np.zeros((h, w), np.int32)  # 表示中は真っ白 (文字が消えた状態)
    for strength in (64, "auto"):
        out = BlockRDO(strength)(indices, source, palette, reference)
        assert np.array_equal(out, indices), strength


def test_block_cap_zero_restores_the_previous_behaviour():
    """--rdo-block-cap 0 は上限なし (v6.12.0 まで): 細い文字のブロックの一部を前の盤面で済ませてしまう。"""
    palette = np.array([[250, 250, 250], [200, 200, 200]], np.float32)
    source = np.full((64, 128, 3), 250, np.uint8)
    source[20, 8:40] = 200
    indices = np.where(source[..., 0] == 200, 1, 0).astype(np.int32)
    reference = np.zeros((64, 128), np.int32)
    out = BlockRDO(64, block_cap=0)(indices, source, palette, reference)
    assert (out == 1).sum() < (indices == 1).sum()  # 文字の画の一部が消える
