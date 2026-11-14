"""色が一定の面の塗り方 (--flat-areas) のテスト。

自作の合成画像と、mvcodec/block_colors.py の表だけを使う。
"""
import sys
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import convert  # noqa: E402
from mvcodec.color import (AUTO_PALETTE_CANDIDATES, FLAT_AREA_SETTINGS, FLAT_MIX_ERROR_FACTOR, PALETTES,  # noqa: E402
                           create_palette_image, flat_mask, knoll_dither_indices, nearest_oklab_indices, palette_tex,
                           process_single_frame_img)

SKY = (56, 139, 228)  # テスト映像の空。一番近い模様の無いブロック (空色のコンクリート) より明るく、今は青氷が混ざる


def block(name, palette=AUTO_PALETTE_CANDIDATES):
    return next(item for item in palette if item["block"] == f"minecraft:{name}")


def solid(color, w=48, h=32):
    return np.tile(np.array(color, np.uint8), (h, w, 1))


def quantize(img, palette, dither, flat_areas, perceptual=True):
    pal_rgb = np.array([item["rgb"] for item in palette], np.float64)
    return process_single_frame_img(Image.fromarray(img), create_palette_image(palette), img.shape[1], img.shape[0],
                                    len(palette), dither, perceptual, pal_rgb, pal_tex=palette_tex(palette),
                                    flat_areas=flat_areas)


# --- 平らな面の判定 ---

def test_flat_mask_finds_a_plain_area_but_not_a_gentle_gradient():
    assert flat_mask(solid(SKY)).all()
    x = np.linspace(0, 1, 96)[None, :, None]
    gradient = np.clip(np.array([60, 120, 200]) + x * np.array([60, 40, 30]), 0, 255).astype(np.uint8)
    gradient = np.repeat(gradient, 40, axis=0)
    # 隣の画素の差は小さく局所的には平らだが、たどると色が変わっていくので平らではない
    assert not flat_mask(gradient).any()


def test_flat_mask_reaches_the_edge_of_a_plain_area():
    rng = np.random.default_rng(0)
    img = rng.integers(0, 256, (40, 60, 3), dtype=np.uint8)
    img[8:32, 10:50] = SKY
    flat = flat_mask(img)
    assert flat[8:32, 10:50].all()  # ふち (窓の半分の幅) まで平ら
    assert not flat[:6].any() and not flat[34:].any()


# --- 減色 (第 2 段階) ---

def test_smooth_paints_a_plain_area_with_one_block_where_off_mixes_two():
    img = solid(SKY)
    off = quantize(img, AUTO_PALETTE_CANDIDATES, "blue_noise", "off")
    smooth = quantize(img, AUTO_PALETTE_CANDIDATES, "blue_noise", "smooth")
    assert len(np.unique(off)) >= 2
    assert np.unique(smooth).tolist() == [AUTO_PALETTE_CANDIDATES.index(block("light_blue_concrete"))]


def test_smooth_avoids_a_strongly_patterned_block_on_a_plain_area():
    palette = [block("crafting_table"), block("orange_terracotta"), block("hardened_clay")]
    img = solid((139, 84, 39))  # 平均の色は作業台が一番近い
    for dither in ("none", "blue_noise"):
        assert (quantize(img, palette, dither, "off") == 0).mean() > 0.5
        assert 0 not in np.unique(quantize(img, palette, dither, "smooth"))


def test_balance_mixes_only_blocks_close_to_the_nearest_one():
    palette = AUTO_PALETTE_CANDIDATES
    radius = FLAT_AREA_SETTINGS["balance"]["radius"]
    from mvcodec.block_rdo import rgb_to_oklab

    lab = rgb_to_oklab(np.array([item["rgb"] for item in palette], np.uint8))  # OkLab × 100
    for color in (SKY, (74, 174, 56), (200, 40, 160)):
        used = np.unique(quantize(solid(color), palette, "blue_noise", "balance"))
        target = rgb_to_oklab(np.array([color], np.uint8))[0]
        nearest = np.linalg.norm(lab - target, axis=1).min()
        limit = max(radius, FLAT_MIX_ERROR_FACTOR * nearest)
        assert np.linalg.norm(lab[used][:, None] - lab[used][None], axis=-1).max() <= 2 * limit


def test_smooth_still_dithers_detailed_areas():
    """平らでない所 (なめらかなグラデーション) は今までどおり混ぜて色を表す。"""
    x = np.linspace(0, 1, 96)[None, :, None]
    gradient = np.repeat(np.clip(np.array([30, 60, 200]) + x * np.array([200, 150, 0]), 0, 255).astype(np.uint8), 40, 0)
    palette = PALETTES["concrete"]
    off = quantize(gradient, palette, "blue_noise", "off")
    smooth = quantize(gradient, palette, "blue_noise", "smooth")
    assert len(np.unique(smooth)) >= len(np.unique(off)) - 1


@pytest.mark.parametrize("dither", ["none", "blue_noise", "ordered", "floyd", "atkinson"])
def test_off_gives_the_same_indices_as_before(dither):
    """--flat-areas off (と効かないディザ) は、引数を足す前と同じ結果。"""
    rng = np.random.default_rng(1)
    img = np.asarray(Image.fromarray(rng.integers(0, 256, (12, 16, 3), dtype=np.uint8)).resize((48, 36)))
    img = img.copy()
    img[:12, :20] = SKY
    palette = PALETTES["full"]
    pal_rgb = np.array([item["rgb"] for item in palette], np.float64)
    before = process_single_frame_img(Image.fromarray(img), create_palette_image(palette), 48, 36, len(palette), dither,
                                      True, pal_rgb)
    assert np.array_equal(quantize(img, palette, dither, "off"), before)
    if dither not in ("none", "blue_noise"):
        assert np.array_equal(quantize(img, palette, dither, "smooth"), before)


def test_extended_functions_keep_their_old_results_without_settings():
    rng = np.random.default_rng(2)
    img = rng.integers(0, 256, (20, 30, 3), dtype=np.uint8)
    pal_rgb = np.array([item["rgb"] for item in PALETTES["all"]], np.float64)
    noise = rng.random((20, 30))
    tex = palette_tex(PALETTES["all"])
    assert np.array_equal(knoll_dither_indices(img, pal_rgb, noise, pal_tex=tex), knoll_dither_indices(img, pal_rgb, noise))
    assert np.array_equal(nearest_oklab_indices(img, pal_rgb, tex), nearest_oklab_indices(img, pal_rgb))


def test_cli_off_writes_the_same_file_as_no_option(tmp_path):
    frames = tmp_path / "frames"
    frames.mkdir()
    for t in range(6):
        img = solid(SKY, 64, 36).copy()
        img[10:26, 8 + t * 4:24 + t * 4] = (252, 209, 62)
        Image.fromarray(img).save(frames / f"f{t:03d}.png")
    outputs = {}
    for name, extra in (("default", []), ("off", ["--flat-areas", "off"]), ("smooth", ["--flat-areas", "smooth"])):
        out = tmp_path / f"{name}.js"
        code = convert.main(["--frames-dir", str(frames), "--output", str(out), "--width", "64", "--height", "36",
                             "--fps", "10", "--palette", "auto", "--dither-method", "blue_noise", "--device", "cpu",
                             "--threads", "1", "--lang", "en", *extra])
        assert code == 0
        outputs[name] = out.read_bytes()
    assert outputs["off"] == outputs["default"]
    assert outputs["smooth"] != outputs["off"]


def test_cli_rejects_an_unknown_flat_areas_value():
    with pytest.raises(SystemExit):
        convert.parse_args(["--frames-dir", "x", "--flat-areas", "maybe"])


# --- 前のフレームのブロックを残す処理との組み合わせ ---

def test_hysteresis_does_not_keep_old_blocks_on_a_plain_area():
    """平らな面 (fresh) では、前のフレームの模様の強いブロックが RGB で十分近くても残さない。"""
    palette = np.array([(134, 83, 48), (152, 94, 68)], np.float64)  # 作業台, テラコッタ
    source = solid((140, 86, 52), 4, 2)
    new = np.ones((2, 4), np.int32)
    previous = np.zeros((2, 4), np.int32)
    assert (convert.keep_previous_blocks(new, previous, source, palette, 24) == 0).all()
    fresh = np.zeros((2, 4), bool)
    fresh[:, :2] = True
    kept = convert.keep_previous_blocks(new, previous, source, palette, 24, fresh)
    assert kept[:, :2].tolist() == [[1, 1], [1, 1]] and (kept[:, 2:] == 0).all()


def test_block_rdo_keeps_the_quantized_blocks_on_a_plain_area():
    from mvcodec.block_rdo import BlockRDO

    palette = np.array([(134, 83, 48), (152, 94, 68), (20, 20, 20)], np.float64)
    source = solid((140, 86, 52), 16, 16)
    new = np.ones((16, 16), np.int32)
    reference = np.zeros((16, 16), np.int32)
    rdo = BlockRDO(64)
    assert (rdo(new, source, palette, reference) == 0).all()  # 表示中のまま (置かない方が安い)
    fresh = np.ones((16, 16), bool)
    assert (BlockRDO(64)(new, source, palette, reference, fresh=fresh) == 1).all()


def test_flat_mask_ignores_small_plain_patches():
    rng = np.random.default_rng(5)
    img = rng.integers(0, 256, (40, 60, 3), dtype=np.uint8)
    img[5:12, 5:12] = SKY  # 7 × 7: 9 × 9 の正方形が入らない
    img[15:35, 25:55] = SKY
    flat = flat_mask(img)
    assert not flat[5:12, 5:12].any()
    assert flat[15:35, 25:55].all()


# --- 自動パレット ---

def test_auto_palette_adds_low_pattern_blocks_for_plain_areas():
    from mvcodec.auto_palette import _flat_blocks_for_color, generate_auto_palette

    dirt = (139, 84, 39)  # 平均の色は作業台 (模様が強い) が一番近い
    picks = [AUTO_PALETTE_CANDIDATES[i]["block"] for i in _flat_blocks_for_color(dirt, AUTO_PALETTE_CANDIDATES,
                                                                                 FLAT_AREA_SETTINGS["smooth"])]
    assert "minecraft:crafting_table" not in picks
    assert palette_tex([block(name.split(":")[1]) for name in picks])[0] < 2
    frames = [solid(dirt), solid(SKY)]
    off = generate_auto_palette(frames, AUTO_PALETTE_CANDIDATES, max_colors=8, sample_stride=1)
    assert generate_auto_palette(frames, AUTO_PALETTE_CANDIDATES, max_colors=8, sample_stride=1, flat_areas="off") == off
    smooth = generate_auto_palette(frames, AUTO_PALETTE_CANDIDATES, max_colors=8, sample_stride=1, flat_areas="smooth")
    assert min(palette_tex(smooth)) < 1 and smooth != off
