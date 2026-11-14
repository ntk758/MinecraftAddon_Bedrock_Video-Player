"""ブロックのテクスチャのばらつき (mvcodec/block_colors.py の tex / tex_side) と、色ムラの物差し (benchmark/metrics/mura.py) のテスト。

自作の合成画像と、mvcodec/block_colors.py の表だけを使う。
"""
import importlib.util
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT), str(ROOT / "benchmark")]

from metrics.mura import blurred_color_error, flat_region, mura  # noqa: E402
from mvcodec.block_colors import BLOCK_COLORS  # noqa: E402
from mvcodec.color import AUTO_PALETTE_CANDIDATES, BLOCK_TEX, OPAQUE_BLOCKS, PALETTES, palette_tex, side_palette  # noqa: E402

SKY = (56, 139, 228)  # テスト映像の空


def block(name, palette=AUTO_PALETTE_CANDIDATES):
    return next(item for item in palette if item["block"] == f"minecraft:{name}")


def solid(color, w=48, h=32):
    return np.tile(np.array(color, np.uint8), (h, w, 1))


def test_every_block_has_texture_spread_for_both_faces():
    for item in BLOCK_COLORS:
        assert 0 <= item["tex"] < 40 and 0 <= item["tex_side"] < 40, item["block"]
    tex = {item["block"].split(":")[1]: item for item in BLOCK_COLORS}
    assert tex["white_concrete"]["tex"] < 1 and tex["black_concrete"]["tex"] < 1
    assert 2 < tex["white_wool"]["tex"] < 8
    assert tex["crafting_table"]["tex"] > 10
    # 原木は上面 (年輪) と側面 (樹皮) で模様が違う
    assert tex["oak_log"]["tex"] != tex["oak_log"]["tex_side"]


def test_texture_spread_is_zero_for_a_plain_texture_and_grows_with_contrast():
    spec = importlib.util.spec_from_file_location("generate_block_colors", ROOT / "scripts" / "generate_block_colors.py")
    gen = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gen)
    plain = np.tile(np.array([120.0, 80.0, 40.0, 255.0]), (256, 1))
    assert gen.texture_spread(plain, (120, 80, 40)) == 0.0
    checker = plain.copy()
    checker[::2, :3] = (60, 40, 20)
    soft = plain.copy()
    soft[::2, :3] = (110, 73, 36)
    def mean(img):
        return gen.linear_to_srgb(gen.srgb_to_linear(img[:, :3]).mean(axis=0))

    strong = gen.texture_spread(checker, mean(checker))
    weak = gen.texture_spread(soft, mean(soft))
    assert strong > weak > 0


def test_palettes_carry_the_texture_spread_of_the_face_they_show():
    assert np.allclose(palette_tex(PALETTES["concrete"]), [BLOCK_TEX[item["block"]] for item in PALETTES["concrete"]])
    log = block("oak_log", OPAQUE_BLOCKS)
    assert palette_tex([log])[0] == pytest.approx(log["tex"])
    assert palette_tex(side_palette([log]))[0] == pytest.approx(log["tex_side"])
    # 手書きのパレット (tex の無い項目) は表から引く
    assert palette_tex(side_palette(PALETTES["concrete"]))[0] == pytest.approx(BLOCK_TEX["minecraft:white_concrete"])


# --- 色ムラの物差し ---

def test_mura_of_one_plain_block_is_its_texture_spread():
    source = solid(SKY)
    shown = solid(block("light_blue_concrete")["rgb"])
    result = mura(source, shown, np.full(source.shape[:2], 0.3))
    assert result["flat_share"] > 0.5
    assert result["mura"] == pytest.approx(0.3, abs=0.01)
    assert result["mura_grain"] == pytest.approx(0.0, abs=1e-6)


def test_mura_grows_with_mixed_blocks_and_patterned_blocks():
    source = solid(SKY)
    plain = mura(source, solid(block("light_blue_concrete")["rgb"]), np.full(source.shape[:2], 0.3))["mura"]
    mixed = solid(block("light_blue_concrete")["rgb"]).copy()
    mixed[::3, ::2] = block("blue_ice")["rgb"]
    assert mura(source, mixed, np.full(source.shape[:2], 0.3))["mura"] > 5 * plain
    assert mura(source, solid(block("light_blue_concrete")["rgb"]), np.full(source.shape[:2], 9.5))["mura"] > 9


def test_mura_is_nan_without_a_plain_area_and_blur_error_is_zero_for_the_same_image():
    rng = np.random.default_rng(4)
    noisy = rng.integers(0, 256, (30, 40, 3), dtype=np.uint8)
    assert not flat_region(noisy).any()
    assert np.isnan(mura(noisy, noisy, np.zeros((30, 40)))["mura"])
    assert blurred_color_error(noisy, noisy) == pytest.approx(0.0, abs=1e-9)
