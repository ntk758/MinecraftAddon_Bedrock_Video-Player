"""ブロックの色の表 (mvcodec/block_colors.py) と、それを使うパレットのテスト。"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import convert  # noqa: E402
from mvcodec.block_colors import BLOCK_COLORS  # noqa: E402
from mvcodec.color import ALL_BLOCKS, AUTO_PALETTE_CANDIDATES, BLOCK_RGB, PALETTES  # noqa: E402
from mvcodec.encode import MAX_PALETTE_COLORS  # noqa: E402

# 置くと困るブロック: 重力で落ちる・時間で変化する・まわりに作用する
# (氷は溶けるが、packed_ice・blue_ice は溶けない。銅はろう引きしたものだけ酸化しない)
FORBIDDEN = ("sand", "gravel", "concrete_powder", "redstone_block", "magma", "tnt", "leaves", "grass", "dirt_with",
             "mycelium", "farmland", "coral_block", "gold", "diamond", "emerald", "netherite", "iron_block", "raw_iron",
             "lapis", "coal", "sculk")


def test_every_block_palette_fits_volumes_and_is_safe():
    ids = [item["block"] for item in BLOCK_COLORS]
    assert len(ids) == len(set(ids))
    # 立体は空気に 1 つ使うので、パレットは 127 色まで
    assert 39 < len(PALETTES["all"]) <= MAX_PALETTE_COLORS - 1
    for block_id in ids:
        assert block_id.startswith("minecraft:")
        name = block_id.split(":", 1)[1]
        assert not any(word in name for word in FORBIDDEN) or name.startswith("dead_"), name
        assert name != "ice", name
        assert not ("copper" in name and "raw_copper" not in name and not name.startswith("waxed_")), name
    for item in BLOCK_COLORS:
        assert all(0 <= c <= 255 for c in item["rgb"])


def test_old_palettes_use_measured_colors():
    """以前の目安の値 (白いコンクリートが 255, 255, 255 など) ではなく、テクスチャの平均色を使う。"""
    white = next(item for item in ALL_BLOCKS if item["block"] == "minecraft:white_concrete")
    assert white["rgb"] != (255, 255, 255)
    assert max(white["rgb"]) < 230
    for item in ALL_BLOCKS:
        assert BLOCK_RGB[item["block"]] == tuple(item["rgb"])
    # 自動パレットの候補には光るブロックを入れない (全ブロックには入る)
    assert all(item["group"] != "light" for item in AUTO_PALETTE_CANDIDATES)
    assert {item["block"] for item in AUTO_PALETTE_CANDIDATES} < {item["block"] for item in PALETTES["all"]}


@pytest.mark.parametrize("mode", ["screen", "relief"])
def test_convert_with_every_block_palette(tmp_path, mode):
    from mvcodec.decode import iter_decoded_frames, load_frame_data
    from test_codec_v4 import make_scene_frames

    make_scene_frames(tmp_path / "frames")
    out = tmp_path / "all.js"
    assert convert.main(["--frames-dir", str(tmp_path / "frames"), "--output", str(out), "--width", "32",
                         "--height", "24", "--fps", "10", "--device", "cpu", "--palette", "all", "--mode", mode,
                         "--depth", "8", "--duration", "2"]) == 0
    data = load_frame_data(out)
    assert len(data["level_blocks"]) == len(PALETTES["all"])
    assert sum(1 for _ in iter_decoded_frames(data)) == data["frame_count"]


def test_gui_labels_show_the_current_color_count():
    import json

    every, auto = str(len(PALETTES["all"])), str(len(AUTO_PALETTE_CANDIDATES))
    for path in (ROOT / "locales").glob("*.json"):
        data = json.loads(path.read_text(encoding="utf-8"))
        assert every in data["palette.all"] and auto in data["palette.auto"], path.name


def test_auto_palette_without_torch_picks_colors_from_the_video(monkeypatch):
    """PyTorch が無くても、先頭の固定の色ではなく、映像に多い色のブロックを選ぶ。"""
    import numpy as np

    from mvcodec.auto_palette import generate_auto_palette

    monkeypatch.setitem(sys.modules, "torch", None)
    frame = np.zeros((16, 16, 3), np.uint8)
    frame[:8] = (130, 165, 245)   # 水色 (packed_ice に近い)
    frame[8:] = (40, 120, 120)    # 青緑 (warped_wart_block などに近い)
    picked = generate_auto_palette(iter([frame]), AUTO_PALETTE_CANDIDATES, max_colors=8, sample_stride=1)
    assert len(picked) == 8
    top = [item["rgb"] for item in picked[:2]]
    assert any(abs(r - 130) + abs(g - 165) + abs(b - 245) < 60 for r, g, b in top), top
    assert any(abs(r - 40) + abs(g - 120) + abs(b - 120) < 60 for r, g, b in top), top
