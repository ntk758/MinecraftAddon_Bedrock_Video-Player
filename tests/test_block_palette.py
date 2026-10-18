"""ブロックの色の表 (mvcodec/block_colors.py) と、それを使うパレットのテスト。"""
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import convert  # noqa: E402
from mvcodec.block_colors import BLOCK_COLORS  # noqa: E402
from mvcodec.color import ALL_BLOCKS, AUTO_PALETTE_CANDIDATES, BLOCK_RGB, PALETTES  # noqa: E402
from mvcodec.encode import MAX_PALETTE_COLORS  # noqa: E402

# 置くと困るブロック: 重力で落ちる・時間で変化する・まわりに作用する (チートを使うアドオンなので、鉱石などの資源のブロックは使う)
# (砂岩・ソウルサンドは落ちない。氷は溶けるが packed_ice・blue_ice は溶けない。銅はろう引きしたものだけ酸化しない)
FORBIDDEN = ("concrete_powder", "gravel", "redstone", "magma", "tnt", "leaves", "grass", "dirt_with", "mycelium", "nylium",
             "farmland", "coral_block", "snow_layer", "powder_snow", "sponge", "observer", "piston", "dispenser", "dropper", "note", "infested",
             "budding", "anvil", "dragon_egg", "scaffolding", "pointed_dripstone", "sculk_sensor", "sculk_catalyst",
             "respawn_anchor", "creaking")


def is_forbidden(name):
    if name.startswith("dead_") or name == "redstone_lamp":
        return False
    if "sand" in name and not ("sandstone" in name or name == "soul_sand"):
        return True
    if name == "ice" or name == "dirt":
        return True
    if "copper" in name and not name.startswith(("waxed_", "raw_")):
        return True
    return any(word in name for word in FORBIDDEN)


def test_every_block_palette_fits_volumes_and_is_safe():
    ids = [item["block"] for item in BLOCK_COLORS]
    assert len(ids) == len(set(ids))
    # 立体は空気に 1 つ使うので、1 つのパレットは 127 色まで (全ブロックからは色が離れた 127 色を選ぶ)
    assert len(PALETTES["all"]) == MAX_PALETTE_COLORS - 1
    assert len(PALETTES["all"]) < len(AUTO_PALETTE_CANDIDATES) < len(BLOCK_COLORS)
    for block_id in ids:
        assert block_id.startswith("minecraft:")
        assert not is_forbidden(block_id.split(":", 1)[1]), block_id
    for item in BLOCK_COLORS:
        assert all(0 <= c <= 255 for c in item["rgb"]) and all(0 <= c <= 255 for c in item["side"])
    # 資源のブロックも使う
    assert {"minecraft:diamond_block", "minecraft:gold_block"} & set(ids)
    # 雪のブロックは光るブロックの明かりで溶けるので、光るブロックと同じパレットには入れない (自動パレットには入る)
    for name, palette in PALETTES.items():
        groups = {item.get("group") for item in palette}
        assert not ({"light"} <= groups and any(item["block"] == "minecraft:snow" for item in palette)), name
    assert any(item["block"] == "minecraft:snow" for item in AUTO_PALETTE_CANDIDATES)


def test_volumes_choose_blocks_by_their_side_color():
    """原木のように上面と側面の見た目が違うブロックは、立体では側面 (樹皮) の色で選ぶ。"""
    from mvcodec.color import side_palette

    logs = [item for item in AUTO_PALETTE_CANDIDATES if item["name"].endswith("_log") and item["rgb"] != item["side"]]
    assert logs
    sides = side_palette(logs)
    assert [item["rgb"] for item in sides] == [item["side"] for item in logs]
    assert [item["block"] for item in sides] == [item["block"] for item in logs]


def test_old_palettes_use_measured_colors():
    """以前の目安の値 (白いコンクリートが 255, 255, 255 など) ではなく、テクスチャの平均色を使う。"""
    white = next(item for item in ALL_BLOCKS if item["block"] == "minecraft:white_concrete")
    assert white["rgb"] != (255, 255, 255)
    assert max(white["rgb"]) < 230
    for item in ALL_BLOCKS:
        assert BLOCK_RGB[item["block"]] == tuple(item["rgb"])
    # 自動パレットの候補には光るブロックを入れない (全ブロックには入る)
    assert all(item["group"] != "light" for item in AUTO_PALETTE_CANDIDATES)
    from mvcodec.color import OPAQUE_BLOCKS

    assert {item["block"] for item in AUTO_PALETTE_CANDIDATES} < {item["block"] for item in OPAQUE_BLOCKS}


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


def test_glass_palette_is_only_for_holograms():
    """ステンドグラス (半透明) は専用のパレットだけにあり、全ブロック・自動パレットには入らない。"""
    glass = PALETTES["glass"]
    assert len(glass) == 16
    assert all(item["block"].endswith("_stained_glass") for item in glass)
    assert all(0 < item["alpha"] < 1 for item in BLOCK_COLORS if item["group"] == "glass")
    opaque = {item["block"] for item in PALETTES["all"]} | {item["block"] for item in AUTO_PALETTE_CANDIDATES}
    assert not opaque & {item["block"] for item in glass}
    assert all(BLOCK_RGB[item["block"]] == item["rgb"] for item in glass)


def test_glass_hologram_places_stained_glass(tmp_path):
    """ステンドグラスのパレットで立体を作り、モックの Minecraft で再生するとステンドグラスと空気だけが置かれる。"""
    import shutil

    from test_codec_v4 import make_scene_frames
    from test_main_js import run_addon

    if shutil.which("node") is None:
        pytest.skip("node が必要")
    make_scene_frames(tmp_path / "frames")
    out = tmp_path / "glass.js"
    assert convert.main(["--frames-dir", str(tmp_path / "frames"), "--output", str(out), "--width", "24",
                         "--height", "16", "--fps", "10", "--device", "cpu", "--palette", "glass", "--mode", "relief",
                         "--depth", "6", "--duration", "1"]) == 0
    result = run_addon(tmp_path / "addon", out)
    placed = {block for block in result["finalBoard"] if block}
    assert placed and all(block.endswith("_stained_glass") or block == "minecraft:air" for block in placed), placed


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


def test_auto_color_count_picks_the_fewest_colors_that_keep_the_error():
    """--palette-colors auto: 色の誤差が一番多い色数の AUTO_COLOR_TOLERANCE 倍以内に収まる、一番少ない色数を選ぶ。"""
    from mvcodec.auto_palette import AUTO_COLOR_COUNTS, _select_color_count, generate_auto_palette

    candidates = AUTO_PALETTE_CANDIDATES
    # 3 色だけの映像: 24 色で 64 色と同じ誤差になる
    few = np.array([candidates[i]["rgb"] for i in (0, 5, 9)] * 400, dtype=np.float32)
    calls = []

    def select(count):
        calls.append(count)
        return candidates[:count] if count == AUTO_COLOR_COUNTS[-1] else [candidates[i] for i in (0, 5, 9)] + candidates[20:20 + count - 3]

    assert len(_select_color_count(few, select)) == AUTO_COLOR_COUNTS[0]
    assert calls[0] == AUTO_COLOR_COUNTS[-1]
    # 色の多い映像で、少ない色数では誤差が大きく増える: 一番多い色数のまま
    rng = np.random.default_rng(0)
    many = rng.integers(0, 256, (3000, 3)).astype(np.float32)
    largest = AUTO_COLOR_COUNTS[-1]
    chosen = _select_color_count(many, lambda count: candidates[:count] if count == largest else candidates[:2])
    assert len(chosen) == largest
    # 実際の選び方 (PyTorch が無ければ色の数え上げ) でも、候補の色数のどれかのパレットになる
    frames = [rng.integers(0, 256, (24, 32, 3), dtype=np.uint8) for _ in range(3)]
    palette = generate_auto_palette(frames, candidates, max_colors="auto", sample_stride=1)
    assert len(palette) in AUTO_COLOR_COUNTS


def test_auto_palette_is_not_shared_by_scenes_that_start_black():
    """黒い画面から始まる別々の場面には、それぞれのパレットを作る (以前は最初のコマだけで同じ場面とみなし、
    最初の場面のパレットを使い回していた)。"""
    import convert

    built = []

    def fake_palette(samples, candidates, **kwargs):
        built.append(len(samples))
        return candidates[:24]

    black = np.zeros((16, 16, 3), np.uint8)
    red = np.zeros((16, 16, 3), np.uint8); red[..., 0] = 200
    blue = np.zeros((16, 16, 3), np.uint8); blue[..., 2] = 200
    import mvcodec.auto_palette as ap
    original = ap.generate_auto_palette
    ap.generate_auto_palette = fake_palette
    try:
        build = convert.make_auto_palette_builder(None)
        build([black, red, red], black)
        build([black, blue, blue], black)
        build([black, red, red], black)  # 同じ見た目の場面は使い回す
    finally:
        ap.generate_auto_palette = original
    assert len(built) == 2
